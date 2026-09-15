"""Módulo de Cotizaciones por Cliente.

Permite crear propuestas de precio para un cliente (registrado u ocasional),
listarlas, verlas, imprimirlas y convertirlas en venta.

IMPORTANTE: crear/guardar una cotización NO descuenta stock, NO genera
movimientos de inventario, NO afecta cartera de créditos ni la caja. Solo
cuando el usuario convierte la cotización a venta se usa el flujo normal del
POS (`/api/ventas`).
"""
import hmac
import hashlib
from datetime import datetime
from flask import Blueprint, jsonify, render_template, request, session
from ..config import ESTADOS_COTIZACION, SECRET_KEY
from ..db import get_db
from ..security import login_required
from ..services.auditoria import registrar_auditoria
bp = Blueprint('cotizaciones', __name__)


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _token_cotizacion(id_cotizacion, consecutivo):
    # Token HMAC que autoriza ver/aprobar una cotizacion sin iniciar sesion.
    # El enlace se comparte por WhatsApp; el token evita que cualquiera pueda
    # leer o cambiar cotizaciones con solo adivinar el id.
    mensaje = f'{id_cotizacion}:{consecutivo}'.encode('utf-8')
    return hmac.new(SECRET_KEY.encode('utf-8'), mensaje, hashlib.sha256).hexdigest()[:32]


def _token_valido(id_cotizacion, consecutivo, token):
    esperado = _token_cotizacion(id_cotizacion, consecutivo)
    return hmac.compare_digest(esperado, str(token or ''))


def _numero(valor, defecto=0.0):
    try:
        return float(valor)
    except (ValueError, TypeError):
        return defecto


def _generar_consecutivo(conn):
    """Genera un consecutivo único tipo COT-AAAAMMDD-####."""
    base = datetime.now().strftime('%Y%m%d')
    prefijo = f'COT-{base}-'
    row = conn.execute(
        "SELECT consecutivo_cotizacion FROM cotizaciones "
        "WHERE consecutivo_cotizacion LIKE ? ORDER BY id DESC LIMIT 1",
        (prefijo + '%',),
    ).fetchone()
    siguiente = 1
    if row and row[0]:
        try:
            siguiente = int(str(row[0]).rsplit('-', 1)[-1]) + 1
        except (ValueError, IndexError):
            siguiente = 1
    return f'{prefijo}{siguiente:04d}'


@bp.route('/api/cotizaciones', methods=['GET', 'POST'])
@login_required
def handle_cotizaciones():
    if request.method == 'GET':
        return _listar_cotizaciones()
    return _crear_cotizacion()


def _listar_cotizaciones():
    """Lista cotizaciones con filtros opcionales por cliente, fecha y consecutivo."""
    conn = get_db()
    q = (request.args.get('q') or '').strip()
    desde = (request.args.get('desde') or '').strip()
    hasta = (request.args.get('hasta') or '').strip()
    estado = (request.args.get('estado') or '').strip()

    sql = ("SELECT c.id, c.consecutivo_cotizacion, c.nombre_cliente, c.cedula_nit, "
           "c.telefono, c.fecha, c.total, c.estado, c.vigencia, c.observaciones, "
           "COALESCE(COUNT(d.id), 0) AS n_items "
           "FROM cotizaciones c LEFT JOIN detalle_cotizaciones d ON d.id_cotizacion = c.id")
    where, params = [], []
    if q:
        where.append("(c.nombre_cliente LIKE ? OR c.cedula_nit LIKE ? "
                     "OR c.consecutivo_cotizacion LIKE ?)")
        patron = f'%{q}%'
        params.extend([patron, patron, patron])
    if desde:
        where.append("date(c.fecha) >= date(?)")
        params.append(desde)
    if hasta:
        where.append("date(c.fecha) <= date(?)")
        params.append(hasta)
    if estado:
        where.append("c.estado = ?")
        params.append(estado)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY c.id ORDER BY c.id DESC LIMIT 500"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "consecutivo": r[1], "cliente": r[2], "cedula_nit": r[3] or "",
        "telefono": r[4] or "", "fecha": r[5], "total": r[6], "estado": r[7],
        "vigencia": r[8] or "", "observaciones": r[9] or "", "items": r[10],
    } for r in rows])


def _crear_cotizacion():
    """Registra una cotización nueva. NO afecta inventario, caja ni cartera."""
    data = request.json or {}
    id_cliente = data.get('id_cliente')
    nombre_libre = str(data.get('nombre_cliente') or '').strip()
    items = data.get('items') or []

    if not items:
        return jsonify({"error": "La cotización debe tener al menos un ítem"}), 400
    if not id_cliente and not nombre_libre:
        return jsonify({"error": "Seleccione un cliente o escriba el nombre del cliente ocasional"}), 400

    conn = get_db()
    # Cliente registrado u ocasional.
    nombre = nombre_libre
    cedula = telefono = direccion = ''
    if id_cliente:
        cli = conn.execute(
            "SELECT nombre, cedula_nit, telefono, direccion FROM clientes WHERE id = ?",
            (id_cliente,),
        ).fetchone()
        if not cli:
            conn.close()
            return jsonify({"error": "Cliente no encontrado"}), 404
        nombre = cli[0]
        cedula = cli[1] or ''
        telefono = cli[2] or ''
        direccion = cli[3] or ''
    # Se permiten datos extra enviados desde el formulario (cliente ocasional).
    cedula = str(data.get('cedula_nit') or cedula or '').strip()
    telefono = str(data.get('telefono') or telefono or '').strip()
    direccion = str(data.get('direccion') or direccion or '').strip()

    # Construir detalle y calcular subtotales/total.
    detalles, total = [], 0.0
    for item in items:
        cantidad = _numero(item.get('cantidad'), 1) or 1
        precio = _numero(item.get('precio_unitario', item.get('precio')), 0)
        if cantidad <= 0:
            continue
        descripcion = str(item.get('descripcion') or item.get('nombre') or '').strip()
        id_producto = item.get('id_producto')
        if id_producto and not descripcion:
            prod = conn.execute("SELECT nombre FROM productos WHERE id = ?", (id_producto,)).fetchone()
            descripcion = prod[0] if prod else 'Producto'
        if not descripcion:
            continue
        subtotal = round(cantidad * precio, 2)
        total += subtotal
        detalles.append({
            'id_producto': id_producto, 'descripcion': descripcion,
            'cantidad': cantidad, 'precio_unitario': precio, 'subtotal': subtotal,
        })

    if not detalles:
        conn.close()
        return jsonify({"error": "Ningún ítem de la cotización es válido"}), 400

    vigencia = str(data.get('vigencia') or 'Válido por 15 días').strip()
    observaciones = str(data.get('observaciones') or '').strip()
    consecutivo = _generar_consecutivo(conn)
    ahora = _ahora()

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO cotizaciones (consecutivo_cotizacion, id_cliente, nombre_cliente,
                                  cedula_nit, telefono, direccion, fecha, vigencia,
                                  observaciones, total, estado, usuario)
        VALUES (:consec, :cli, :nombre, :cedula, :tel, :dir, :fecha, :vig,
                :obs, :total, 'Pendiente', :usuario)
        """,
        {'consec': consecutivo, 'cli': id_cliente, 'nombre': nombre, 'cedula': cedula,
         'tel': telefono, 'dir': direccion, 'fecha': ahora, 'vig': vigencia,
         'obs': observaciones, 'total': round(total, 2), 'usuario': session.get('usuario', 'sistema')},
    )
    id_cotizacion = cursor.lastrowid

    for d in detalles:
        cursor.execute(
            """
            INSERT INTO detalle_cotizaciones (id_cotizacion, id_producto, descripcion,
                                              cantidad, precio_unitario, subtotal)
            VALUES (:cot, :prod, :desc, :cant, :precio, :sub)
            """,
            {'cot': id_cotizacion, 'prod': d['id_producto'], 'desc': d['descripcion'],
             'cant': d['cantidad'], 'precio': d['precio_unitario'], 'sub': d['subtotal']},
        )

    registrar_auditoria(conn, 'crear', 'cotizacion', id_cotizacion,
                        f'Cotización {consecutivo} por {round(total, 2)}')
    conn.commit()
    conn.close()
    return jsonify({
        "mensaje": f"Cotización {consecutivo} creada correctamente",
        "id": id_cotizacion, "consecutivo": consecutivo, "total": round(total, 2),
    }), 201


@bp.route('/api/cotizaciones/<int:id_cotizacion>', methods=['GET', 'DELETE'])
@login_required
def cotizacion_por_id(id_cotizacion):
    conn = get_db()

    if request.method == 'DELETE':
        if session.get('rol') != 'admin':
            conn.close()
            return jsonify({"error": "Solo el administrador puede eliminar cotizaciones"}), 403
        existe = conn.execute("SELECT consecutivo_cotizacion FROM cotizaciones WHERE id = ?",
                              (id_cotizacion,)).fetchone()
        if not existe:
            conn.close()
            return jsonify({"error": "Cotización no encontrada"}), 404
        conn.execute("DELETE FROM detalle_cotizaciones WHERE id_cotizacion = ?", (id_cotizacion,))
        conn.execute("DELETE FROM cotizaciones WHERE id = ?", (id_cotizacion,))
        registrar_auditoria(conn, 'eliminar', 'cotizacion', id_cotizacion,
                            f'Cotización {existe[0]} eliminada')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Cotización eliminada"})

    cot = conn.execute(
        """
        SELECT id, consecutivo_cotizacion, nombre_cliente, cedula_nit, telefono, direccion,
               fecha, vigencia, observaciones, total, estado, id_cliente, usuario
        FROM cotizaciones WHERE id = ?
        """, (id_cotizacion,)
    ).fetchone()
    if not cot:
        conn.close()
        return jsonify({"error": "Cotización no encontrada"}), 404

    items = conn.execute(
        "SELECT id, id_producto, descripcion, cantidad, precio_unitario, subtotal "
        "FROM detalle_cotizaciones WHERE id_cotizacion = ? ORDER BY id", (id_cotizacion,)
    ).fetchall()

    negocio = conn.execute(
        "SELECT nombre, nit, telefono, direccion FROM configuracion WHERE id = 1"
    ).fetchone()
    conn.close()

    return jsonify({
        "id": cot[0], "consecutivo": cot[1], "cliente": cot[2],
        "cedula_nit": cot[3] or "", "telefono": cot[4] or "", "direccion": cot[5] or "",
        "fecha": cot[6], "vigencia": cot[7] or "", "observaciones": cot[8] or "",
        "total": cot[9], "estado": cot[10], "id_cliente": cot[11], "usuario": cot[12] or "",
        "negocio": {
            "nombre": (negocio[0] if negocio else "Ferretería y Fábrica de Flejes"),
            "nit": (negocio[1] if negocio else "") or "",
            "telefono": (negocio[2] if negocio else "") or "",
            "direccion": (negocio[3] if negocio else "") or "",
        },
        "items": [{
            "id": i[0], "id_producto": i[1], "descripcion": i[2],
            "cantidad": i[3], "precio_unitario": i[4], "subtotal": i[5],
        } for i in items],
    })


@bp.route('/api/cotizaciones/<int:id_cotizacion>/estado', methods=['PUT'])
@login_required
def cambiar_estado_cotizacion(id_cotizacion):
    data = request.json or {}
    nuevo = str(data.get('estado') or '').strip()
    if nuevo not in ESTADOS_COTIZACION:
        return jsonify({"error": f"Estado inválido. Válidos: {', '.join(ESTADOS_COTIZACION)}"}), 400

    conn = get_db()
    existe = conn.execute("SELECT consecutivo_cotizacion FROM cotizaciones WHERE id = ?",
                          (id_cotizacion,)).fetchone()
    if not existe:
        conn.close()
        return jsonify({"error": "Cotización no encontrada"}), 404
    conn.execute("UPDATE cotizaciones SET estado = ? WHERE id = ?", (nuevo, id_cotizacion))
    registrar_auditoria(conn, 'estado_cotizacion', 'cotizacion', id_cotizacion, f'Estado -> {nuevo}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Cotización marcada como '{nuevo}'"})


@bp.route('/api/cotizaciones/<int:id_cotizacion>/convertir', methods=['POST'])
@login_required
def convertir_a_venta(id_cotizacion):
    """Devuelve los datos para precargar el carrito del POS.

    No crea la venta aquí: el POS es quien la registra (así el stock y la caja
    se afectan SOLO cuando el usuario confirma la facturación).
    """
    conn = get_db()
    cot = conn.execute(
        "SELECT id, consecutivo_cotizacion, id_cliente, nombre_cliente FROM cotizaciones WHERE id = ?",
        (id_cotizacion,),
    ).fetchone()
    if not cot:
        conn.close()
        return jsonify({"error": "Cotización no encontrada"}), 404

    items = conn.execute(
        "SELECT id_producto, descripcion, cantidad, precio_unitario FROM detalle_cotizaciones "
        "WHERE id_cotizacion = ? ORDER BY id", (id_cotizacion,)
    ).fetchall()
    conn.close()

    return jsonify({
        "id_cotizacion": cot[0],
        "consecutivo": cot[1],
        "id_cliente": cot[2],
        "cliente": cot[3],
        "items": [{
            "id_producto": i[0], "descripcion": i[1],
            "cantidad": i[2], "precio_unitario": i[3],
        } for i in items if i[0]],
    })


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)


def _cotizacion_completa(conn, id_cotizacion):
    # Devuelve el dict completo de una cotizacion (cabecera + items + negocio).
    cot = conn.execute(
        'SELECT id, consecutivo_cotizacion, nombre_cliente, cedula_nit, telefono, direccion, '
        'fecha, vigencia, observaciones, total, estado, id_cliente, usuario '
        'FROM cotizaciones WHERE id = ?', (id_cotizacion,)
    ).fetchone()
    if not cot:
        return None
    items = conn.execute(
        "SELECT id, id_producto, descripcion, cantidad, precio_unitario, subtotal "
        "FROM detalle_cotizaciones WHERE id_cotizacion = ? ORDER BY id", (id_cotizacion,)
    ).fetchall()
    negocio = conn.execute(
        "SELECT nombre, nit, telefono, direccion FROM configuracion WHERE id = 1"
    ).fetchone()
    return {
        "id": cot[0], "consecutivo": cot[1], "cliente": cot[2],
        "cedula_nit": cot[3] or "", "telefono": cot[4] or "", "direccion": cot[5] or "",
        "fecha": cot[6], "vigencia": cot[7] or "", "observaciones": cot[8] or "",
        "total": cot[9], "estado": cot[10], "id_cliente": cot[11], "usuario": cot[12] or "",
        "negocio": {
            "nombre": (negocio[0] if negocio else "Ferreteria y Fabrica de Flejes"),
            "nit": (negocio[1] if negocio else "") or "",
            "telefono": (negocio[2] if negocio else "") or "",
            "direccion": (negocio[3] if negocio else "") or "",
        },
        "items": [{
            "id": i[0], "id_producto": i[1], "descripcion": i[2],
            "cantidad": i[3], "precio_unitario": i[4], "subtotal": i[5],
        } for i in items],
    }

@bp.route('/cotizacion/<int:id_cotizacion>', methods=['GET'])
def vista_publica_cotizacion(id_cotizacion):
    # Vista publica (sin login) que el cliente abre desde el enlace de WhatsApp.
    conn = get_db()
    cot = _cotizacion_completa(conn, id_cotizacion)
    conn.close()
    if not cot:
        return render_template('cotizacion_publica.html', error='Cotizacion no encontrada'), 404
    return render_template('cotizacion_publica.html', cot=cot, token=request.args.get('t', ''))

@bp.route('/api/cotizaciones/<int:id_cotizacion>/enlace', methods=['GET'])
@login_required
def generar_enlace(id_cotizacion):
    # Genera la URL publica de aprobacion para compartir por WhatsApp.
    conn = get_db()
    row = conn.execute("SELECT consecutivo_cotizacion FROM cotizaciones WHERE id = ?",
                       (id_cotizacion,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Cotizacion no encontrada"}), 404
    token = _token_cotizacion(id_cotizacion, row[0])
    ruta = f'/cotizacion/{id_cotizacion}?t={token}'
    return jsonify({
        "ruta": ruta,
        "ruta_absoluta": request.host_url.rstrip('/') + ruta,
        "token": token,
    })

@bp.route('/api/cotizacion/<int:id_cotizacion>/aprobar', methods=['POST'])
def aprobar_cotizacion(id_cotizacion):
    # El cliente aprueba la cotizacion desde el enlace publico (con token).
    data = request.json or {}
    token = data.get('token')
    conn = get_db()
    cot = conn.execute("SELECT consecutivo_cotizacion, estado FROM cotizaciones WHERE id = ?",
                       (id_cotizacion,)).fetchone()
    if not cot:
        conn.close()
        return jsonify({"error": "Cotizacion no encontrada"}), 404
    if not _token_valido(id_cotizacion, cot[0], token):
        conn.close()
        return jsonify({"error": "Enlace no valido o expirado"}), 403
    if cot[1] == 'Anulada':
        conn.close()
        return jsonify({"error": "Esta cotizacion fue anulada"}), 400
    conn.execute("UPDATE cotizaciones SET estado = 'Aprobada' WHERE id = ?", (id_cotizacion,))
    registrar_auditoria(conn, 'aprobar', 'cotizacion', id_cotizacion,
                        f'Cotizacion {cot[0]} aprobada por el cliente desde el enlace publico')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Cotizacion aprobada. Gracias por su confirmacion.", "estado": "Aprobada"})
