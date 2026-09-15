"""Rutas de ventas, créditos y abonos.

Incluye el registro de la venta (POS), su anulación, el detalle de factura,
los despachos "para llevar" y la gestión de créditos/abonos por cliente.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..db import get_db
from ..security import admin_required, login_required
from ..services.auditoria import registrar_auditoria
from ..services.ordenes_fleje import registrar_orden_fleje_desde_venta

bp = Blueprint('ventas', __name__)


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── Ventas ────────────────────
@bp.route('/api/ventas', methods=['GET', 'POST'])
@login_required
def handle_ventas():
    if request.method == 'GET':
        return _listar_ventas()
    return _registrar_venta()


def _listar_ventas():
    conn = get_db()
    fecha = request.args.get('fecha')
    query = """
        SELECT v.id, v.fecha_dia, v.hora, c.nombre, v.total_venta, v.tipo_pago,
               GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') as detalles,
               c.cedula_nit, c.telefono, v.id_cliente,
               COALESCE(v.direccion_cliente, c.direccion, ''), v.anulada, v.motivo_anulacion,
               v.tipo_entrega, v.numero_pedido
        FROM ventas v
        JOIN clientes c ON v.id_cliente = c.id
        LEFT JOIN detalle_ventas dv ON v.id = dv.id_venta
        LEFT JOIN productos p ON dv.id_producto = p.id
    """
    params = []
    if fecha:
        query += " WHERE v.fecha_dia = ?"
        params.append(fecha)
    query += " GROUP BY v.id ORDER BY v.id DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "fecha_dia": r[1], "hora": r[2], "cliente": r[3],
        "total_venta": r[4], "tipo_pago": r[5], "productos_detalle": r[6],
        "cedula_nit": r[7], "telefono": r[8], "id_cliente": r[9], "direccion": r[10] or "",
        "anulada": bool(r[11]), "motivo_anulacion": r[12] or "",
        "tipo_entrega": r[13] or "entrega_inmediata", "numero_pedido": r[14],
    } for r in rows])


def _es_producto_fleje(nombre, categoria, item):
    """Determina si un ítem vendido corresponde a un fleje a figurar."""
    return bool(
        (categoria or '').lower().find('fleje') >= 0
        or (nombre or '').lower().find('fleje') >= 0
        or item.get('calibre') or item.get('ancho_cm') or item.get('largo_cm')
    )


def _construir_detalles(cursor, conn, items):
    """Valida los ítems y calcula el total. Devuelve (total, detalles) o un error."""
    total_venta = 0
    detalles = []
    for item in items:
        item_id = item['id_producto']
        prod = cursor.execute(
            "SELECT nombre, categoria, precio_venta FROM productos WHERE id = ?", (item_id,)
        ).fetchone()
        if not prod:
            return None, (jsonify({"error": f"Producto ID {item_id} no encontrado"}), 400)

        nombre_producto, categoria_producto, precio_venta = prod
        precio_venta = float(precio_venta or 0)
        cantidad = int(item['cantidad'])
        if cantidad <= 0:
            return None, (jsonify({"error": "La cantidad debe ser mayor que cero"}), 400)

        # VENTAS PERMISIVAS: no se valida stock ni precio de costo.
        subtotal = cantidad * precio_venta
        total_venta += subtotal
        detalles.append({
            'id_producto': item_id, 'cantidad': cantidad, 'precio': precio_venta,
            'nombre': nombre_producto, 'categoria': categoria_producto, 'item': item,
        })
    return (total_venta, detalles), None


def _registrar_venta():
    data = request.json
    id_cliente = data['id_cliente']
    tipo_pago = data['tipo_pago']
    items = data['items']
    direccion_ingresada = str(data.get('direccion', '')).strip()

    conn = get_db()
    cursor = conn.cursor()

    cliente = cursor.execute("SELECT direccion FROM clientes WHERE id = ?", (id_cliente,)).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 400

    direccion_cliente = direccion_ingresada or (cliente[0] or '')
    if direccion_ingresada:
        cursor.execute("UPDATE clientes SET direccion = ? WHERE id = ?", (direccion_ingresada, id_cliente))

    if not items:
        conn.close()
        return jsonify({"error": "La venta debe contener al menos un producto"}), 400

    resultado, error = _construir_detalles(cursor, conn, items)
    if error:
        conn.close()
        return error
    total_venta, detalles = resultado

    now = datetime.now()
    saldo_pendiente = total_venta if tipo_pago == 'credito' else 0

    tipo_entrega = data.get('tipo_entrega', 'entrega_inmediata')
    if tipo_entrega not in ('entrega_inmediata', 'para_llevar'):
        tipo_entrega = 'entrega_inmediata'

    numero_pedido = None
    if tipo_entrega == 'para_llevar':
        numero_pedido = cursor.execute(
            "SELECT COALESCE(MAX(numero_pedido), 0) + 1 FROM ventas WHERE numero_pedido IS NOT NULL"
        ).fetchone()[0]

    try:
        cursor.execute(
            """
            INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente,
                                tipo_pago, direccion_cliente, tipo_entrega, numero_pedido)
            VALUES (:cliente, :dia, :hora, :total, :saldo, :pago, :direccion, :entrega, :pedido)
            """,
            {'cliente': id_cliente, 'dia': now.strftime('%Y-%m-%d'), 'hora': now.strftime('%H:%M:%S'),
             'total': total_venta, 'saldo': saldo_pendiente, 'pago': tipo_pago,
             'direccion': direccion_cliente, 'entrega': tipo_entrega, 'pedido': numero_pedido},
        )
        id_venta = cursor.lastrowid

        for d in detalles:
            cursor.execute(
                "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal) "
                "VALUES (:venta, :producto, :cantidad, :precio, :subtotal)",
                {'venta': id_venta, 'producto': d['id_producto'], 'cantidad': d['cantidad'],
                 'precio': d['precio'], 'subtotal': d['cantidad'] * d['precio']},
            )
            cursor.execute(
                "UPDATE productos SET stock_actual = COALESCE(stock_actual, 0) - ? WHERE id = ?",
                (d['cantidad'], d['id_producto']),
            )
            cursor.execute(
                "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
                "VALUES (:id, 'salida', :cantidad, :motivo, :usuario, :fecha)",
                {'id': d['id_producto'], 'cantidad': d['cantidad'],
                 'motivo': f'Venta #{id_venta}', 'usuario': session['usuario'], 'fecha': _ahora()},
            )

            if _es_producto_fleje(d['nombre'], d['categoria'], d['item']):
                registrar_orden_fleje_desde_venta(conn, id_venta, id_cliente, d['item'], d['nombre'])

        etiqueta = f'Para llevar — Pedido #{numero_pedido}' if numero_pedido else 'Entrega en mostrador'
        registrar_auditoria(conn, 'crear', 'venta', id_venta, f'Venta de {total_venta:.2f} — {etiqueta}')
        conn.commit()
        conn.close()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500

    msg = f"Venta #{id_venta} registrada con éxito"
    if numero_pedido:
        msg += f" — Pedido #{numero_pedido} creado para despacho"
    return jsonify({"mensaje": msg, "id_venta": id_venta,
                    "tipo_entrega": tipo_entrega, "numero_pedido": numero_pedido}), 201


@bp.route('/api/ventas/despachos', methods=['GET'])
@login_required
def get_despachos():
    """Devuelve las ventas 'para_llevar' ordenadas por numero_pedido."""
    conn = get_db()
    estado_filtro = request.args.get('estado', '')
    query = """
        SELECT v.id, v.numero_pedido, v.fecha_dia, v.hora,
               c.nombre, c.telefono, c.cedula_nit,
               COALESCE(v.direccion_cliente, c.direccion, '') AS direccion,
               v.total_venta, v.tipo_pago, v.anulada,
               GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') AS productos,
               COALESCE(v.estado_despacho, 'pendiente_preparar')
        FROM ventas v
        JOIN clientes c ON c.id = v.id_cliente
        LEFT JOIN detalle_ventas dv ON dv.id_venta = v.id
        LEFT JOIN productos p ON p.id = dv.id_producto
        WHERE v.tipo_entrega = 'para_llevar' AND v.anulada = 0
    """
    params = []
    if estado_filtro:
        query += " AND COALESCE(v.estado_despacho, 'pendiente_preparar') = ?"
        params.append(estado_filtro)
    query += " GROUP BY v.id ORDER BY v.numero_pedido ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([{
        "id_venta": r[0], "numero_pedido": r[1], "fecha_dia": r[2], "hora": r[3],
        "cliente": r[4], "telefono": r[5], "cedula_nit": r[6], "direccion": r[7] or "",
        "total_venta": r[8], "tipo_pago": r[9], "anulada": bool(r[10]),
        "productos": r[11] or "", "estado_despacho": r[12],
    } for r in rows])


@bp.route('/api/ventas/<int:id_venta>/estado_despacho', methods=['PUT'])
@login_required
def cambiar_estado_despacho(id_venta):
    """Cambia el estado_despacho de una venta para_llevar."""
    from ..config import ESTADOS_DESPACHO
    data = request.json or {}
    nuevo = (data.get('estado_despacho') or '').strip()
    if nuevo not in ESTADOS_DESPACHO:
        return jsonify({"error": f"Estado inválido. Válidos: {', '.join(ESTADOS_DESPACHO)}"}), 400

    conn = get_db()
    venta = conn.execute("SELECT id, tipo_entrega FROM ventas WHERE id = ?", (id_venta,)).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[1] != 'para_llevar':
        conn.close()
        return jsonify({"error": "Esta venta no es de tipo para_llevar"}), 400

    conn.execute("UPDATE ventas SET estado_despacho = ? WHERE id = ?", (nuevo, id_venta))
    registrar_auditoria(conn, 'estado_despacho', 'venta', id_venta,
                        f'Estado despacho -> {nuevo} por {session["usuario"]}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Estado actualizado a '{nuevo}'"})


@bp.route('/api/ventas/<int:id_venta>', methods=['GET'])
@login_required
def get_factura_detalle(id_venta):
    conn = get_db()
    venta = conn.execute(
        """
        SELECT v.id, v.fecha_dia, v.hora, c.nombre, c.cedula_nit, c.telefono,
               v.total_venta, v.tipo_pago, v.id_cliente,
               COALESCE(v.direccion_cliente, c.direccion, '')
        FROM ventas v JOIN clientes c ON v.id_cliente = c.id WHERE v.id = ?
        """, (id_venta,)
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404

    detalles = conn.execute(
        """
        SELECT p.nombre, p.dimensiones, dv.cantidad, dv.precio_unitario, dv.subtotal
        FROM detalle_ventas dv JOIN productos p ON dv.id_producto = p.id
        WHERE dv.id_venta = ?
        """, (id_venta,)
    ).fetchall()
    conn.close()

    return jsonify({
        "id": venta[0], "fecha_dia": venta[1], "hora": venta[2], "cliente": venta[3],
        "cedula_nit": venta[4], "telefono": venta[5], "total_venta": venta[6],
        "tipo_pago": venta[7], "id_cliente": venta[8], "direccion": venta[9] or "",
        "items": [{
            "nombre": d[0] + (f" ({d[1]})" if d[1] else ""), "cantidad": d[2],
            "precio_unitario": d[3], "subtotal": d[4],
        } for d in detalles],
    })


@bp.route('/api/ventas/<int:id_venta>/anular', methods=['PUT'])
@admin_required
def anular_venta(id_venta):
    data = request.json or {}
    motivo = str(data.get('motivo', '')).strip()
    if not motivo:
        return jsonify({"error": "Debe indicar el motivo de la anulación"}), 400

    conn = get_db()
    venta = conn.execute(
        "SELECT total_venta, saldo_pendiente, anulada FROM ventas WHERE id = ?", (id_venta,)
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[2]:
        conn.close()
        return jsonify({"error": "La venta ya está anulada"}), 400
    if venta[1] > 0 and venta[1] != venta[0]:
        conn.close()
        return jsonify({"error": "No se puede anular una venta de crédito que ya tiene abonos"}), 400

    detalles = conn.execute(
        "SELECT id_producto, cantidad FROM detalle_ventas WHERE id_venta = ?", (id_venta,)
    ).fetchall()
    for id_producto, cantidad in detalles:
        conn.execute("UPDATE productos SET stock_actual = stock_actual + ? WHERE id = ?",
                     (cantidad, id_producto))
        conn.execute(
            "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
            "VALUES (:id, 'entrada', :cantidad, :motivo, :usuario, :fecha)",
            {'id': id_producto, 'cantidad': cantidad, 'motivo': f'Anulación venta #{id_venta}',
             'usuario': session['usuario'], 'fecha': _ahora()},
        )
    conn.execute("UPDATE ventas SET anulada = 1, motivo_anulacion = ?, saldo_pendiente = 0 WHERE id = ?",
                 (motivo, id_venta))
    registrar_auditoria(conn, 'anular', 'venta', id_venta, motivo)
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Venta anulada y stock restaurado"})


@bp.route('/api/ventas/<int:id_venta>/cliente', methods=['PUT'])
@login_required
def editar_cliente_factura(id_venta):
    data = request.json
    nombre = data.get('nombre')
    if not nombre:
        return jsonify({"error": "El nombre es obligatorio"}), 400

    conn = get_db()
    res = conn.execute("SELECT id_cliente FROM ventas WHERE id = ?", (id_venta,)).fetchone()
    if not res:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    id_cliente = res[0]

    conn.execute(
        "UPDATE clientes SET nombre = ?, cedula_nit = ?, telefono = ?, direccion = ? WHERE id = ?",
        (nombre, data.get('cedula_nit'), data.get('telefono'), data.get('direccion', ''), id_cliente),
    )
    conn.execute("UPDATE ventas SET direccion_cliente = ? WHERE id = ?",
                 (data.get('direccion', ''), id_venta))
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Datos del cliente y dirección actualizados correctamente"}), 200


# ── Créditos y abonos ─────────────────────────
@bp.route('/api/creditos', methods=['GET'])
@login_required
def get_creditos():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT c.id, c.nombre, c.telefono, SUM(v.saldo_pendiente) as deuda_total, c.direccion,
               c.cedula_nit
        FROM clientes c
        LEFT JOIN ventas v ON c.id = v.id_cliente AND v.saldo_pendiente > 0
        GROUP BY c.id
        """
    ).fetchall()
    conn.close()
    return jsonify([{"id_cliente": r[0], "nombre": r[1], "telefono": r[2],
                     "deuda_total": r[3], "direccion": r[4] or "",
                     "cedula_nit": r[5] or ""} for r in rows])


@bp.route('/api/abonos', methods=['POST'])
@login_required
def registrar_abono():
    data = request.json
    id_cliente = data['id_cliente']
    monto_abono = float(data['monto'])
    if monto_abono <= 0:
        return jsonify({"error": "El monto del abono debe ser mayor a cero"}), 400

    conn = get_db()
    cursor = conn.cursor()
    ventas_pendientes = cursor.execute(
        "SELECT id, saldo_pendiente FROM ventas WHERE id_cliente = ? AND saldo_pendiente > 0 ORDER BY id ASC",
        (id_cliente,),
    ).fetchall()
    if not ventas_pendientes:
        conn.close()
        return jsonify({"error": "El cliente no tiene saldo pendiente por pagar"}), 400

    deuda_total = sum(saldo for _, saldo in ventas_pendientes)
    if monto_abono > deuda_total:
        conn.close()
        return jsonify({"error": f"El abono no puede superar la deuda total de {deuda_total:.2f}"}), 400

    monto_restante = monto_abono
    for venta_id, saldo in ventas_pendientes:
        if monto_restante <= 0:
            break
        if monto_restante >= saldo:
            monto_restante -= saldo
            cursor.execute("UPDATE ventas SET saldo_pendiente = 0 WHERE id = ?", (venta_id,))
        else:
            cursor.execute("UPDATE ventas SET saldo_pendiente = saldo_pendiente - ? WHERE id = ?",
                           (monto_restante, venta_id))
            monto_restante = 0

    fecha_hoy = _ahora()
    cursor.execute("INSERT INTO abonos (id_cliente, monto, fecha) VALUES (?, ?, ?)",
                   (id_cliente, monto_abono, fecha_hoy))
    id_abono = cursor.lastrowid
    registrar_auditoria(conn, 'registrar', 'abono', id_abono, f'Abono de {monto_abono:.2f}')

    cliente = cursor.execute(
        "SELECT nombre, cedula_nit, telefono, direccion FROM clientes WHERE id = ?", (id_cliente,)
    ).fetchone()
    saldo_despues = deuda_total - monto_abono
    conn.commit()
    conn.close()

    return jsonify({
        "mensaje": "Abono registrado con éxito",
        "abono": {
            "id": id_abono, "fecha": fecha_hoy, "cliente": cliente[0],
            "cedula_nit": cliente[1] or "", "telefono": cliente[2] or "",
            "direccion": cliente[3] or "", "deuda_anterior": deuda_total,
            "monto": monto_abono, "saldo_pendiente": saldo_despues,
        },
    }), 201


@bp.route('/api/abonos/<int:id_cliente>', methods=['GET'])
@login_required
def historial_abonos(id_cliente):
    conn = get_db()
    cursor = conn.cursor()
    cliente = cursor.execute(
        "SELECT nombre, cedula_nit, telefono, direccion FROM clientes WHERE id = ?", (id_cliente,)
    ).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404

    deuda_original = cursor.execute(
        "SELECT COALESCE(SUM(total_venta), 0) FROM ventas WHERE id_cliente = ? AND tipo_pago = 'credito'",
        (id_cliente,),
    ).fetchone()[0] or 0
    abonos = cursor.execute(
        "SELECT id, monto, fecha FROM abonos WHERE id_cliente = ? ORDER BY id ASC", (id_cliente,)
    ).fetchall()
    conn.close()

    historial = []
    total_abonado = 0
    for id_abono, monto, fecha in abonos:
        deuda_antes = max(deuda_original - total_abonado, 0)
        total_abonado += monto
        historial.append({
            "id": id_abono, "fecha": fecha, "cliente": cliente[0],
            "cedula_nit": cliente[1] or "", "telefono": cliente[2] or "",
            "direccion": cliente[3] or "", "deuda_anterior": deuda_antes,
            "monto": monto, "saldo_pendiente": max(deuda_original - total_abonado, 0),
        })

    return jsonify({
        "cliente": {"id": id_cliente, "nombre": cliente[0], "cedula_nit": cliente[1] or "",
                    "telefono": cliente[2] or "", "direccion": cliente[3] or ""},
        "abonos": historial,
    })


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
