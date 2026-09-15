"""Rutas del módulo de pedidos: creación, listado y máquina de estados por rol."""
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..config import ESTADOS_PEDIDO, TRANSICIONES_PEDIDO
from ..db import get_db
from ..security import login_required
from ..services.auditoria import registrar_auditoria

bp = Blueprint('pedidos', __name__)

_Q_PEDIDOS = """
    SELECT p.id, p.id_cliente, c.nombre, c.telefono,
           p.direccion_entrega, p.observaciones, p.estado,
           p.usuario_vendedor, p.usuario_bodega,
           p.id_motocarguero, um.usuario,
           p.fecha_creacion, p.fecha_actualizacion,
           COALESCE(SUM(dp.subtotal), 0)
    FROM pedidos p
    JOIN clientes c ON c.id = p.id_cliente
    LEFT JOIN usuarios um ON um.id = p.id_motocarguero
    LEFT JOIN detalle_pedidos dp ON dp.id_pedido = p.id
"""


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _pedido_row_to_dict(r):
    return {
        "id": r[0], "id_cliente": r[1], "nombre_cliente": r[2],
        "telefono_cliente": r[3], "direccion_entrega": r[4] or "",
        "observaciones": r[5] or "", "estado": r[6],
        "usuario_vendedor": r[7], "usuario_bodega": r[8] or "",
        "id_motocarguero": r[9], "nombre_motocarguero": r[10] or "",
        "fecha_creacion": r[11], "fecha_actualizacion": r[12] or "",
        "total_pedido": r[13] or 0,
    }


@bp.route('/api/pedidos', methods=['GET', 'POST'])
@login_required
def handle_pedidos():
    conn = get_db()
    rol = session.get('rol')
    usuario = session.get('usuario')

    if request.method == 'GET':
        return _listar_pedidos(conn, rol, usuario)
    return _crear_pedido(conn, rol, usuario)


def _listar_pedidos(conn, rol, usuario):
    estado_filtro = request.args.get('estado', '')
    desde = request.args.get('desde', '')
    where_clauses = []
    params = []

    if rol == 'motocarguero':
        row_id = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        if not row_id:
            conn.close()
            return jsonify([])
        where_clauses.append("p.id_motocarguero = ?")
        params.append(row_id[0])

    if estado_filtro:
        where_clauses.append("p.estado = ?")
        params.append(estado_filtro)
    if desde:
        where_clauses.append("p.fecha_creacion >= ?")
        params.append(desde)

    sql = _Q_PEDIDOS
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " GROUP BY p.id ORDER BY p.fecha_creacion DESC LIMIT 200"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([_pedido_row_to_dict(r) for r in rows])


def _crear_pedido(conn, rol, usuario):
    if rol not in ('admin', 'empleado', 'bodega'):
        conn.close()
        return jsonify({"error": "Solo vendedores o administradores pueden crear pedidos"}), 403

    data = request.json or {}
    id_cliente = data.get('id_cliente')
    items = data.get('items', [])
    if not id_cliente or not items:
        conn.close()
        return jsonify({"error": "Se requiere id_cliente y al menos un producto"}), 400

    if not conn.execute("SELECT id FROM clientes WHERE id = ?", (id_cliente,)).fetchone():
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404

    ahora = _ahora()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO pedidos (id_cliente, direccion_entrega, observaciones, estado,
                             usuario_vendedor, fecha_creacion, fecha_actualizacion)
        VALUES (:cliente, :direccion, :obs, 'pendiente', :usuario, :fecha, :fecha)
        """,
        {'cliente': id_cliente, 'direccion': data.get('direccion_entrega', '').strip(),
         'obs': data.get('observaciones', '').strip(), 'usuario': usuario, 'fecha': ahora},
    )
    id_pedido = cursor.lastrowid

    total = 0
    for item in items:
        prod = conn.execute(
            "SELECT precio_venta FROM productos WHERE id = ?", (item['id_producto'],)
        ).fetchone()
        if not prod:
            conn.rollback()
            conn.close()
            return jsonify({"error": f"Producto ID {item['id_producto']} no encontrado"}), 404
        cantidad = int(item['cantidad'])
        precio = float(prod[0])
        subtotal = round(cantidad * precio, 2)
        total += subtotal
        cursor.execute(
            """
            INSERT INTO detalle_pedidos (id_pedido, id_producto, cantidad, precio_unitario, subtotal)
            VALUES (:pedido, :producto, :cantidad, :precio, :subtotal)
            """,
            {'pedido': id_pedido, 'producto': item['id_producto'], 'cantidad': cantidad,
             'precio': precio, 'subtotal': subtotal},
        )

    registrar_auditoria(conn, 'crear', 'pedido', id_pedido,
                        f'Pedido creado por {usuario} — {len(items)} productos — Total ${total:,.0f}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Pedido creado con exito", "id_pedido": id_pedido, "total": total}), 201


@bp.route('/api/pedidos/<int:id_pedido>', methods=['GET'])
@login_required
def get_pedido(id_pedido):
    conn = get_db()
    row = conn.execute(_Q_PEDIDOS + " WHERE p.id = ? GROUP BY p.id", (id_pedido,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Pedido no encontrado"}), 404

    pedido = _pedido_row_to_dict(row)
    items = conn.execute(
        """
        SELECT dp.id, pr.nombre, pr.dimensiones, pr.categoria,
               dp.cantidad, dp.precio_unitario, dp.subtotal,
               pr.stock_actual, pr.codigo_barras
        FROM detalle_pedidos dp
        JOIN productos pr ON pr.id = dp.id_producto
        WHERE dp.id_pedido = ?
        """, (id_pedido,)
    ).fetchall()
    conn.close()
    pedido['items'] = [{
        "id": i[0], "nombre": i[1], "dimensiones": i[2] or "", "categoria": i[3] or "",
        "cantidad": i[4], "precio_unitario": i[5], "subtotal": i[6],
        "stock_actual": i[7], "codigo_barras": i[8] or "",
    } for i in items]
    return jsonify(pedido)


@bp.route('/api/pedidos/<int:id_pedido>/estado', methods=['PUT'])
@login_required
def cambiar_estado_pedido(id_pedido):
    rol = session.get('rol')
    usuario = session.get('usuario')
    data = request.json or {}
    nuevo_estado = (data.get('estado') or '').strip().lower()

    if nuevo_estado not in ESTADOS_PEDIDO:
        return jsonify({"error": f"Estado invalido. Validos: {', '.join(ESTADOS_PEDIDO)}"}), 400

    conn = get_db()
    pedido = conn.execute("SELECT estado FROM pedidos WHERE id = ?", (id_pedido,)).fetchone()
    if not pedido:
        conn.close()
        return jsonify({"error": "Pedido no encontrado"}), 404

    estado_actual = pedido[0]
    permitidos = TRANSICIONES_PEDIDO.get(estado_actual, {})
    if rol not in permitidos or nuevo_estado not in permitidos[rol]:
        conn.close()
        return jsonify({"error": f"El rol '{rol}' no puede cambiar de '{estado_actual}' a '{nuevo_estado}'"}), 403

    updates = ["estado = :nuevo", "fecha_actualizacion = :fecha"]
    cambios = {'nuevo': nuevo_estado, 'fecha': _ahora()}

    if rol == 'bodega' and estado_actual == 'pendiente':
        updates.append("usuario_bodega = :bodega")
        cambios['bodega'] = usuario

    if data.get('id_motocarguero'):
        id_moto = int(data['id_motocarguero'])
        moto = conn.execute(
            "SELECT id FROM usuarios WHERE id = ? AND rol = 'motocarguero'", (id_moto,)
        ).fetchone()
        if not moto:
            conn.close()
            return jsonify({"error": "Motocarguero no encontrado"}), 404
        updates.append("id_motocarguero = :moto")
        cambios['moto'] = id_moto

    cambios['id'] = id_pedido
    conn.execute(f"UPDATE pedidos SET {', '.join(updates)} WHERE id = :id", cambios)
    registrar_auditoria(conn, 'estado_pedido', 'pedido', id_pedido,
                        f'{estado_actual} -> {nuevo_estado} por {usuario}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Pedido actualizado a '{nuevo_estado}'", "estado": nuevo_estado})


@bp.route('/api/pedidos/notificaciones', methods=['GET'])
@login_required
def notificaciones_pedidos():
    rol = session.get('rol')
    usuario = session.get('usuario')
    conn = get_db()

    if rol in ('admin', 'bodega'):
        total = conn.execute("SELECT COUNT(*) FROM pedidos WHERE estado = 'pendiente'").fetchone()[0]
        resumen = f"{total} pedido(s) pendiente(s) por alistar"
    elif rol == 'motocarguero':
        row_id = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        total = 0
        if row_id:
            total = conn.execute(
                "SELECT COUNT(*) FROM pedidos WHERE estado = 'listo' AND id_motocarguero = ?",
                (row_id[0],),
            ).fetchone()[0]
        resumen = f"{total} pedido(s) listo(s) para recoger"
    else:
        total = conn.execute(
            "SELECT COUNT(*) FROM pedidos WHERE estado NOT IN ('entregado','cancelado') "
            "AND usuario_vendedor = ?",
            (usuario,),
        ).fetchone()[0]
        resumen = f"{total} pedido(s) activo(s)"

    conn.close()
    return jsonify({"total": total, "resumen": resumen})


@bp.route('/api/usuarios/motocargueros', methods=['GET'])
@login_required
def get_motocargueros():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, usuario, nombre_completo FROM usuarios WHERE rol = 'motocarguero' ORDER BY usuario"
    ).fetchall()
    conn.close()
    return jsonify([{"id": r[0], "usuario": r[1], "nombre": r[2] or r[1]} for r in rows])


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
