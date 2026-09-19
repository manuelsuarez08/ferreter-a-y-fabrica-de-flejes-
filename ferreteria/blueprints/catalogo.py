"""Rutas de catálogo e inventario: clientes, productos y movimientos.

Agrupa el CRUD de clientes y productos junto al control de stock (entradas,
salidas, reconteos y búsqueda por código de barras).
"""
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..db import get_db
from ..security import login_required
from ..services.auditoria import registrar_auditoria

bp = Blueprint('catalogo', __name__)


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── Clientes ──────────────────
@bp.route('/api/clientes', methods=['GET', 'POST'])
@login_required
def handle_clientes():
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'GET':
        rows = cursor.execute(
            "SELECT id, nombre, cedula_nit, telefono, direccion, "
            "COALESCE(email, ''), COALESCE(tipo_documento, 'CC') FROM clientes ORDER BY id DESC"
        ).fetchall()
        conn.close()
        return jsonify([{"id": r[0], "nombre": r[1], "cedula_nit": r[2],
                         "telefono": r[3], "direccion": r[4] or "",
                         "email": r[5] or "", "tipo_documento": r[6] or "CC"} for r in rows])

    data = request.json
    if not data or not str(data.get('nombre', '')).strip():
        conn.close()
        return jsonify({"error": "El nombre del cliente es obligatorio"}), 400

    try:
        cursor.execute(
            "INSERT INTO clientes (nombre, cedula_nit, telefono, direccion, email, tipo_documento) "
            "VALUES (:nombre, :nit, :telefono, :direccion, :email, :tipo_documento)",
            {'nombre': data['nombre'].strip(), 'nit': str(data.get('cedula_nit', '')).strip() or None,
             'telefono': data.get('telefono', '').strip(), 'direccion': data.get('direccion', '').strip(),
             'email': str(data.get('email', '')).strip(),
             'tipo_documento': (str(data.get('tipo_documento', '')).strip() or 'CC')},
        )
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Cliente creado con éxito"}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "La cédula o NIT ya está registrado"}), 400


@bp.route('/api/clientes/<int:id_cliente>', methods=['PUT'])
@login_required
def actualizar_cliente(id_cliente):
    data = request.json or {}
    nombre = str(data.get('nombre', '')).strip()
    if not nombre:
        return jsonify({"error": "El nombre del cliente es obligatorio"}), 400

    conn = get_db()
    try:
        conn.execute(
            "UPDATE clientes SET nombre = ?, cedula_nit = ?, telefono = ?, direccion = ?, "
            "email = ?, tipo_documento = ? WHERE id = ?",
            (nombre, str(data.get('cedula_nit', '')).strip() or None,
             str(data.get('telefono', '')).strip(), str(data.get('direccion', '')).strip(),
             str(data.get('email', '')).strip(),
             (str(data.get('tipo_documento', '')).strip() or 'CC'), id_cliente),
        )
        if conn.total_changes == 0:
            conn.close()
            return jsonify({"error": "Cliente no encontrado"}), 404
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Cliente actualizado correctamente"}), 200
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "La cédula o NIT ya está registrado"}), 400


# ── Productos ─────────────────
def _leer_entero(data, clave, default):
    try:
        return int(float(data.get(clave) or default))
    except (ValueError, TypeError):
        return default


def _leer_flotante(data, clave, default=0.0):
    try:
        return float(data.get(clave) or default)
    except (ValueError, TypeError):
        return default

def _desglosar_iva(precio_final, iva_tasa):
    """Separa un precio FINAL (con IVA) en (precio_base, valor_iva).

      base = precio_final / (1 + tasa/100)
      iva  = precio_final - base
    Si la tasa es 0, el precio final es todo base (producto exento). Los valores
    se redondean a pesos para que base + iva cuadre exactamente con el final.
    """
    precio_final = round(float(precio_final or 0), 2)
    if iva_tasa <= 0:
        return precio_final, 0.0
    base = round(precio_final / (1 + iva_tasa / 100))
    return float(base), float(precio_final - base)


@bp.route('/api/productos', methods=['GET', 'POST'])
@login_required
def handle_productos():
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'GET':
        return _listar_productos(cursor, conn)

    return _crear_o_reabastecer_producto(cursor, conn)

# Límite por defecto de resultados del buscador del POS (rendimiento).
POS_LIMITE_DEFECTO = 30
POS_LIMITE_MAXIMO = 100
@bp.route('/api/productos/pos', methods=['GET'])
@login_required
def buscar_productos_pos():
    # Buscador liviano del POS: devuelve solo los campos que el punto de venta
    # necesita y limita los resultados para mantener la respuesta pequena y el
    # render rapido. Se apoya en los indices de nombre, codigo_barras y categoria.""
    busqueda = (request.args.get('q') or '').strip()
    try:
        limite = int(request.args.get('limite') or POS_LIMITE_DEFECTO)
    except (ValueError, TypeError):
        limite = POS_LIMITE_DEFECTO
    limite = max(1, min(limite, POS_LIMITE_MAXIMO))

    sql = ("SELECT id, nombre, codigo_barras, precio_venta, stock_actual, dimensiones "
           "FROM productos WHERE COALESCE(activo, 1) = 1")
    params = []
    if busqueda:
        sql += " AND (nombre LIKE ? OR codigo_barras LIKE ? OR categoria LIKE ?)"
        patron = f"%{busqueda}%"
        params.extend([patron, patron, patron])
    sql += " ORDER BY nombre COLLATE NOCASE ASC LIMIT ?"
    params.append(limite)

    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "nombre": r[1], "codigo_barras": r[2] or "",
        "precio_venta": r[3], "stock_actual": r[4], "dimensiones": r[5] or "",
    } for r in rows])


def _listar_productos(cursor, conn):
    busqueda = (request.args.get('q') or '').strip()
    incluir_inactivos = request.args.get('incluir_inactivos') == '1'
    try:
        limite = min(int(request.args.get('limite') or 0), 2000) or None
        offset = max(int(request.args.get('offset') or 0), 0)
    except (ValueError, TypeError):
        limite, offset = None, 0

    sql = ("SELECT id, nombre, categoria, dimensiones, codigo_barras, precio_venta, "
           "stock_actual, stock_minimo, auditado, stock_inicial, fecha_auditoria, "
           "COALESCE(precio_base, precio_venta), COALESCE(iva_valor, 0), "
           "COALESCE(iva_tasa, 0) FROM productos")
    where, params = [], []
    if not incluir_inactivos:
        where.append("COALESCE(activo, 1) = 1")
    if busqueda:
        where.append("(nombre LIKE ? OR codigo_barras LIKE ? OR categoria LIKE ?)")
        patron = f"%{busqueda}%"
        params.extend([patron, patron, patron])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY nombre COLLATE NOCASE ASC"
    if limite:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limite, offset])

    rows = cursor.execute(sql, params).fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "nombre": r[1], "categoria": r[2], "dimensiones": r[3],
        "codigo_barras": r[4] or "", "precio_venta": r[5],
        "stock_actual": r[6], "stock_minimo": r[7],
        "auditado": bool(r[8]), "stock_inicial": r[9] or 0, "fecha_auditoria": r[10],
        "precio_base": r[11], "iva_valor": r[12], "iva_tasa": r[13],
    } for r in rows])


def _crear_o_reabastecer_producto(cursor, conn):
    data = request.json
    if not data or not str(data.get('nombre', '')).strip():
        conn.close()
        return jsonify({"error": "Datos del producto no válidos: el nombre es obligatorio"}), 400

    nombre = data['nombre'].strip()
    categoria = data.get('categoria', '').strip()
    dimensiones = data.get('dimensiones', '').strip()
    codigo_barras = str(data.get('codigo_barras', '')).strip() or None
    precio_costo = _leer_flotante(data, 'precio_costo')
    precio_venta = _leer_flotante(data, 'precio_venta')
    stock_ingresado = _leer_entero(data, 'stock_actual', 0)
    stock_minimo = max(0, _leer_entero(data, 'stock_minimo', 5))

    # Desglose de IVA del producto. El precio_venta que llega del formulario es
    # SIEMPRE el precio FINAL (lo que paga el cliente). A partir de el y de la
    # tasa se calcula el valor base (sin IVA) y el iva incluido:
    #   base = precio_final / (1 + tasa/100)   iva = precio_final - base
    iva_tasa = max(0.0, min(100.0, _leer_flotante(data, 'iva_tasa', 0)))
    precio_base, iva_valor = _desglosar_iva(precio_venta, iva_tasa)

    if not nombre or precio_venta < 0 or precio_costo < 0:
        conn.close()
        return jsonify({"error": "El nombre es obligatorio y los precios no pueden ser negativos"}), 400

    if codigo_barras:
        codigo_existente = cursor.execute(
            "SELECT id FROM productos WHERE codigo_barras = ?", (codigo_barras,)
        ).fetchone()
        if codigo_existente:
            mismo = cursor.execute(
                "SELECT id FROM productos WHERE LOWER(nombre) = LOWER(?) AND LOWER(dimensiones) = LOWER(?)",
                (nombre, dimensiones),
            ).fetchone()
            if not mismo or mismo[0] != codigo_existente[0]:
                conn.close()
                return jsonify({"error": "Ese código de barras ya está asignado a otro producto"}), 400

    prod_existente = cursor.execute(
        "SELECT id, stock_actual FROM productos "
        "WHERE LOWER(nombre) = LOWER(?) AND LOWER(COALESCE(dimensiones, '')) = LOWER(?)",
        (nombre, dimensiones),
    ).fetchone()

    if prod_existente:
        id_prod, stock_actual = prod_existente
        nuevo_stock = (stock_actual or 0) + stock_ingresado
        cursor.execute(
            """
            UPDATE productos
            SET categoria = ?, codigo_barras = COALESCE(?, codigo_barras), precio_costo = ?,
                precio_venta = ?, stock_actual = ?, stock_minimo = ?,
                precio_base = ?, iva_valor = ?, iva_tasa = ?
            WHERE id = ?
            """,
            (categoria, codigo_barras, precio_costo, precio_venta, nuevo_stock, stock_minimo,
             precio_base, iva_valor, iva_tasa, id_prod),
        )
        cursor.execute(
            "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
            "VALUES (:id, 'entrada', :cantidad, :motivo, :usuario, :fecha)",
            {'id': id_prod, 'cantidad': stock_ingresado, 'motivo': 'Reabastecimiento',
             'usuario': session['usuario'], 'fecha': _ahora()},
        )
        registrar_auditoria(conn, 'reabastecer', 'producto', id_prod, f'Entrada de {stock_ingresado} unidades')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": f"Stock reabastecido con éxito. Nuevo stock total: {nuevo_stock}"}), 200

    cursor.execute(
        "INSERT INTO productos (nombre, categoria, dimensiones, codigo_barras, precio_costo, "
        "precio_venta, stock_actual, stock_minimo, stock_inicial, auditado, activo, "
        "precio_base, iva_valor, iva_tasa) "
        "VALUES (:nombre, :categoria, :dimensiones, :codigo, :costo, :venta, :stock, :minimo, "
        ":stock, 0, 1, :base, :iva, :tasa)",
        {'nombre': nombre, 'categoria': categoria, 'dimensiones': dimensiones,
         'codigo': codigo_barras, 'costo': precio_costo, 'venta': precio_venta,
         'stock': stock_ingresado, 'minimo': stock_minimo,
         'base': precio_base, 'iva': iva_valor, 'tasa': iva_tasa},
    )
    id_producto = cursor.lastrowid
    cursor.execute(
        "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
        "VALUES (:id, 'entrada', :cantidad, :motivo, :usuario, :fecha)",
        {'id': id_producto, 'cantidad': stock_ingresado, 'motivo': 'Inventario inicial',
         'usuario': session['usuario'], 'fecha': _ahora()},
    )
    registrar_auditoria(conn, 'crear', 'producto', id_producto, 'Producto creado')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Producto agregado con éxito"}), 201


@bp.route('/api/productos/<int:id_producto>', methods=['PUT', 'DELETE'])
@login_required
def editar_producto(id_producto):
    conn = get_db()
    if request.method == 'DELETE':
        return _eliminar_producto(conn, id_producto)
    return _actualizar_producto(conn, id_producto)


def _borrar_producto(cursor, id_producto):
    """Elimina un producto y devuelve (resultado, nombre).

    NO hace commit ni cierra la conexion: eso lo decide quien llama, para poder
    reutilizar esta funcion tanto en el borrado individual como en el masivo. 

    El borrado es siempre FISICO: la fila desaparece de la tabla `productos`
    para siempre. Ya NO se marca activo = 0, porque eso dejaba el producto
    escondido pero todavía presente en la base de datos.

    Casos:
      * 'fisico'  -> la fila del producto se borra definitivamente de la base.

    Antes de borrar se limpian las filas que apuntan al producto y que NO son
    facturas: movimientos de inventario, lineas de pedidos y de cotizaciones.
    Las facturas (detalle_ventas) NO se tocan: si el producto ya se vendio, su
    linea queda tal como estaba, para no dejar documentos ya emitidos sin su
    nombre ni su precio.

    Devuelve ('fisico', nombre) o ('no_encontrado', None) si el id no existe.
    """
    producto = cursor.execute(
        "SELECT nombre FROM productos WHERE id = ?", (id_producto,)
    ).fetchone()
    if not producto:
        return 'no_encontrado', None
    nombre = producto[0]

    # Se borran las referencias que no son historial de ventas. Si alguna tabla
    # no existiera en esta version de la base, no se interrumpe el borrado.
    limpiezas = (
        "DELETE FROM movimientos_inventario WHERE id_producto = ?",
        "DELETE FROM detalle_pedidos WHERE id_producto = ?",
        "DELETE FROM detalle_cotizaciones WHERE id_producto = ?",
    )
    for sentencia in limpiezas:
        try:
            cursor.execute(sentencia, (id_producto,))
        except sqlite3.Error:
            pass
    cursor.execute("DELETE FROM productos WHERE id = ?", (id_producto,))
    registrar_auditoria(cursor, 'eliminar', 'producto', id_producto,
                        f'Producto eliminado definitivamente: {nombre}')
    return 'fisico', nombre

def _eliminar_producto(conn, id_producto):
    if session.get('rol') != 'admin':
        conn.close()
        return jsonify({"error": "Solo el administrador puede eliminar productos"}), 403
    resultado, nombre = _borrar_producto(conn, id_producto)
    if resultado == 'no_encontrado':
        conn.close()
        return jsonify({"error": "Producto no encontrado"}), 404
    conn.commit()
    conn.close()
    return jsonify({
        "mensaje": "Producto eliminado definitivamente de la base de datos",
        "tipo": "fisico",
    })

# Tope de ids por peticion: evita que una peticion gigante bloquee el servidor
# y da un limite claro al mensaje de error del frontend.
# Se deja holgado a proposito: el catalogo ronda los 1500 productos y conviene
# poder vaciar una busqueda entera de una sola vez.
LOTE_ELIMINAR_MAXIMO = 3000
@bp.route('/api/productos/eliminar-lote', methods=['POST'])
@login_required
def eliminar_productos_lote():
    """Elimina VARIOS productos de una sola vez (seleccion multiple del inventario).

    Se borra uno por uno con la misma logica del borrado individual, dentro de
    UNA sola transaccion. Si algo falla a mitad de camino se deshace todo el
    lote (rollback), para no dejar el inventario a medias.

    Recibe: {"ids": [1, 2, 3]}
    Responde el conteo y los nombres, que es lo que el frontend necesita para
    avisar con precision de lo que paso.
    """
    if session.get('rol') != 'admin':
        return jsonify({"error": "Solo el administrador puede eliminar productos"}), 403
    data = request.json or {}
    ids_crudos = data.get('ids')
    if not isinstance(ids_crudos, list) or not ids_crudos:
        return jsonify({"error": "Debe seleccionar al menos un producto"}), 400
    if len(ids_crudos) > LOTE_ELIMINAR_MAXIMO:
        return jsonify({
            "error": f"Máximo {LOTE_ELIMINAR_MAXIMO} productos por vez. "
                     f"Seleccionó {len(ids_crudos)}."
        }), 400
    # Se normalizan y deduplican los ids: el frontend puede mandar numeros o
    # texto, y no tiene sentido procesar el mismo id dos veces.
    ids = []
    for valor in ids_crudos:
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            continue
        if numero not in ids:
            ids.append(numero)
    if not ids:
        return jsonify({"error": "Los ids enviados no son válidos"}), 400
    conn = get_db()
    eliminados = []
    no_encontrados = []
    try:
        for id_producto in ids:
            resultado, nombre = _borrar_producto(conn, id_producto)
            if resultado == 'fisico':
                eliminados.append({'id': id_producto, 'nombre': nombre})
            else:
                no_encontrados.append(id_producto)
        conn.commit()
    except sqlite3.Error as error:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"No se pudo eliminar el lote: {error}"}), 500
    conn.close()
    total = len(eliminados)
    return jsonify({
        "mensaje": f"Se eliminaron {total} producto(s) definitivamente.",
        "eliminados": total,
        "no_encontrados": no_encontrados,
        "ids_afectados": [p['id'] for p in eliminados],
    })


def _actualizar_producto(conn, id_producto):
    data = request.json or {}
    try:
        conn.execute(
            """
            UPDATE productos SET nombre = ?, categoria = ?, dimensiones = ?, codigo_barras = ?,
            precio_costo = ?, precio_venta = ?, stock_minimo = ? WHERE id = ?
            """,
            (str(data.get('nombre', '')).strip(), str(data.get('categoria', '')).strip(),
             str(data.get('dimensiones', '')).strip(), str(data.get('codigo_barras', '')).strip() or None,
             float(data.get('precio_costo', 0)), float(data.get('precio_venta', 0)),
             int(data.get('stock_minimo', 0)), id_producto),
        )
        if conn.total_changes == 0:
            conn.close()
            return jsonify({"error": "Producto no encontrado"}), 404
        registrar_auditoria(conn, 'editar', 'producto', id_producto, 'Datos del producto actualizados')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Producto actualizado"})
    except (sqlite3.IntegrityError, ValueError, TypeError):
        conn.close()
        return jsonify({"error": "El código de barras ya pertenece a otro producto o hay datos inválidos"}), 400


@bp.route('/api/productos/<int:id_producto>/reconteo', methods=['POST'])
@login_required
def reconteo_producto(id_producto):
    data = request.json or {}
    try:
        cantidad_real = int(float(data.get('stock_real', data.get('stock_actual'))))
    except (ValueError, TypeError):
        return jsonify({"error": "Debe indicar una cantidad contada válida"}), 400

    conn = get_db()
    producto = conn.execute(
        "SELECT nombre, stock_actual FROM productos WHERE id = ?", (id_producto,)
    ).fetchone()
    if not producto:
        conn.close()
        return jsonify({"error": "Producto no encontrado"}), 404

    nombre, stock_anterior = producto
    stock_anterior = stock_anterior or 0
    diferencia = cantidad_real - stock_anterior
    ahora = _ahora()

    try:
        conn.execute(
            "UPDATE productos SET stock_actual = ?, auditado = 1, fecha_auditoria = ? WHERE id = ?",
            (cantidad_real, ahora, id_producto),
        )
        conn.execute(
            "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
            "VALUES (:id, 'ajuste', :cantidad, :motivo, :usuario, :fecha)",
            {'id': id_producto, 'cantidad': abs(diferencia),
             'motivo': f'Reconteo físico: antes {stock_anterior} -> contado {cantidad_real}',
             'usuario': session.get('usuario', 'sistema'), 'fecha': ahora},
        )
        registrar_auditoria(conn, 'reconteo', 'producto', id_producto,
                            f'{nombre}: stock {stock_anterior} -> {cantidad_real} (auditado)')
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"No se pudo guardar el reconteo: {e}"}), 500

    conn.close()
    return jsonify({
        "mensaje": f"Reconteo guardado. Stock de '{nombre}': {cantidad_real}",
        "id_producto": id_producto,
        "stock_actual": cantidad_real,
        "stock_anterior": stock_anterior,
        "auditado": True,
        "fecha_auditoria": ahora,
    }), 200


@bp.route('/api/productos/barcode/<string:codigo>', methods=['GET'])
@login_required
def buscar_por_barcode(codigo):
    """Busca un producto por código de barras (escáner físico y cámara)."""
    codigo = codigo.strip()
    if not codigo:
        return jsonify({"error": "Código de barras vacío"}), 400

    conn = get_db()
    row = conn.execute(
        "SELECT id, nombre, categoria, dimensiones, codigo_barras, precio_venta, stock_actual, stock_minimo "
        "FROM productos WHERE codigo_barras = ? AND COALESCE(activo, 1) = 1", (codigo,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Producto no encontrado"}), 404
    return jsonify({
        "id": row[0], "nombre": row[1], "categoria": row[2], "dimensiones": row[3],
        "codigo_barras": row[4], "precio_venta": row[5],
        "stock_actual": row[6], "stock_minimo": row[7],
    })


# ── Movimientos de inventario ─────────────────
@bp.route('/api/inventario/movimientos', methods=['GET', 'POST'])
@login_required
def movimientos_inventario():
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute(
            """
            SELECT m.id, p.nombre, m.tipo, m.cantidad, m.motivo, m.usuario, m.fecha
            FROM movimientos_inventario m JOIN productos p ON p.id = m.id_producto
            ORDER BY m.id DESC LIMIT 200
            """
        ).fetchall()
        conn.close()
        return jsonify([{"id": r[0], "producto": r[1], "tipo": r[2], "cantidad": r[3],
                         "motivo": r[4], "usuario": r[5], "fecha": r[6]} for r in rows])

    data = request.json or {}
    try:
        id_producto = int(data['id_producto'])
        cantidad = int(data['cantidad'])
        tipo = data.get('tipo', 'ajuste')
        if cantidad <= 0 or tipo not in ('entrada', 'salida', 'ajuste'):
            raise ValueError

        if tipo == 'ajuste':
            stock_row = conn.execute(
                "SELECT stock_actual FROM productos WHERE id = ?", (id_producto,)
            ).fetchone()
            if not stock_row:
                conn.close()
                return jsonify({"error": "Producto no encontrado"}), 400
            conn.execute("UPDATE productos SET stock_actual = ? WHERE id = ?", (cantidad, id_producto))
        else:
            delta = cantidad if tipo == 'entrada' else -cantidad
            conn.execute(
                "UPDATE productos SET stock_actual = COALESCE(stock_actual, 0) + ? WHERE id = ?",
                (delta, id_producto),
            )
            if conn.total_changes == 0:
                conn.close()
                return jsonify({"error": "Producto no encontrado"}), 400

        conn.execute(
            "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
            "VALUES (:id, :tipo, :cantidad, :motivo, :usuario, :fecha)",
            {'id': id_producto, 'tipo': tipo, 'cantidad': cantidad,
             'motivo': str(data.get('motivo', '')).strip() or 'Ajuste manual',
             'usuario': session['usuario'], 'fecha': _ahora()},
        )
        registrar_auditoria(conn, 'movimiento', 'producto', id_producto, f'{tipo}: {cantidad}')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Movimiento registrado"}), 201
    except (ValueError, KeyError, TypeError):
        conn.close()
        return jsonify({"error": "Datos de movimiento inválidos"}), 400


@bp.route('/api/clientes/<int:id_cliente>/historial', methods=['GET'])
@login_required
def historial_cliente(id_cliente):
    conn = get_db()
    rows = conn.execute(
        "SELECT v.id, v.fecha_dia, v.hora, v.total_venta, v.tipo_pago, v.saldo_pendiente, v.anulada "
        "FROM ventas v WHERE v.id_cliente = ? ORDER BY v.id DESC", (id_cliente,)
    ).fetchall()
    conn.close()
    return jsonify([{"id": r[0], "fecha": f'{r[1]} {r[2]}', "total": r[3], "tipo_pago": r[4],
                     "saldo": r[5], "anulada": bool(r[6])} for r in rows])


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
