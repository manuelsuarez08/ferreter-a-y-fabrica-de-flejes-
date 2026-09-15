"""Rutas del módulo de fábrica: órdenes de figurado y stock de hierro."""
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request

from ..config import ESTADOS_ORDEN_FLEJE
from ..db import get_db
from ..security import login_required
from ..services.auditoria import registrar_auditoria
from ..services.fleje import calcular_consumo_fleje, parse_calibre_mm
from ..services.ordenes_fleje import _extraer_geometria, generar_numero_orden

bp = Blueprint('fabrica', __name__)


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@bp.route('/api/fabrica/ordenes', methods=['GET', 'POST'])
@login_required
def gestion_ordenes_fabrica():
    if request.method == 'GET':
        return _listar_ordenes()
    return _crear_orden()


def _listar_ordenes():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT o.id, o.numero_orden, o.estado, o.fecha_creacion, o.fecha_actualizacion,
               c.nombre AS cliente, o.kg_total, o.observaciones,
               COALESCE(GROUP_CONCAT(d.calibre || ' / ' || d.ancho_cm || 'x' || d.largo_cm
                        || ' cm / ' || d.cantidad_piezas || ' pcs', ' | '), '') AS detalles
        FROM ordenes_figurado o
        JOIN clientes c ON c.id = o.id_cliente
        LEFT JOIN detalles_fleje d ON d.id_orden = o.id
        GROUP BY o.id
        ORDER BY CASE o.estado
           WHEN 'En Cola' THEN 1
           WHEN 'En Figurado' THEN 2
           WHEN 'Completado' THEN 3
           ELSE 4
        END, o.id DESC
        """
    ).fetchall()
    conn.close()
    return jsonify([{
        'id': r[0], 'numero_orden': r[1], 'estado': r[2], 'fecha_creacion': r[3],
        'fecha_actualizacion': r[4], 'cliente': r[5], 'kg_total': r[6],
        'observaciones': r[7] or '', 'detalles': r[8] or '',
    } for r in rows])


def _validar_items_y_calcular(cursor, conn, items):
    """Valida cada fleje contra el stock de hierro. Devuelve (total_kg, detalles) o error."""
    total_kg = 0.0
    detalles_guardados = []

    for item in items:
        geo = _extraer_geometria(item)
        if not geo['calibre'] or geo['ancho_cm'] <= 0 or geo['largo_cm'] <= 0 or geo['cantidad_piezas'] <= 0:
            return None, (jsonify({'error': 'Cada fleje debe incluir calibre, ancho, largo y cantidad válidos'}), 400)

        calculo = calcular_consumo_fleje(
            geo['ancho_cm'], geo['largo_cm'], geo['gancho_cm'], geo['cantidad_piezas'], geo['calibre']
        )
        if calculo['consumo_kg'] <= 0:
            return None, (jsonify({'error': f'No se pudo calcular el consumo del fleje {geo["calibre"]}'}), 400)

        row_stock = cursor.execute(
            'SELECT id, stock_kg FROM inventario_hierro WHERE calibre = ? ORDER BY id LIMIT 1',
            (geo['calibre'],),
        ).fetchone()
        if not row_stock:
            return None, (jsonify({
                'error': f'No existe inventario de hierro para el calibre {geo["calibre"]}. '
                         'Registre el material antes de crear la orden.'
            }), 400)
        if row_stock[1] < calculo['consumo_kg']:
            return None, (jsonify({
                'error': f'Inventario insuficiente para el calibre {geo["calibre"]}. '
                         f'Disponible: {row_stock[1]} kg'
            }), 400)

        total_kg += calculo['consumo_kg']
        detalles_guardados.append({**geo, 'metros_lineales': calculo['metros_lineales'],
                                   'consumo_kg': calculo['consumo_kg'], 'stock_id': row_stock[0]})

    return (total_kg, detalles_guardados), None


def _crear_orden():
    data = request.json or {}
    id_cliente = int(data.get('id_cliente')) if str(data.get('id_cliente') or '').strip() else None
    items = data.get('items') or []
    observaciones = str(data.get('observaciones') or '').strip()

    if not id_cliente:
        return jsonify({'error': 'Debe seleccionar un cliente para la orden'}), 400
    if not items:
        return jsonify({'error': 'Debe agregar al menos un fleje para la orden'}), 400

    conn = get_db()
    cursor = conn.cursor()
    if cursor.execute('SELECT id FROM clientes WHERE id = ?', (id_cliente,)).fetchone() is None:
        conn.close()
        return jsonify({'error': 'Cliente no encontrado'}), 400

    resultado, error = _validar_items_y_calcular(cursor, conn, items)
    if error:
        conn.close()
        return error
    total_kg, detalles_guardados = resultado

    numero_orden = generar_numero_orden()
    ahora = _ahora()
    cursor.execute(
        """
        INSERT INTO ordenes_figurado (id_venta, id_cliente, numero_orden, estado, fecha_creacion,
                                      fecha_actualizacion, observaciones, kg_total)
        VALUES (NULL, :cliente, :numero, 'En Cola', :fecha, :fecha, :obs, :kg)
        """,
        {'cliente': id_cliente, 'numero': numero_orden, 'fecha': ahora,
         'obs': observaciones or 'Orden creada desde el panel de ventas', 'kg': round(total_kg, 4)},
    )
    id_orden = cursor.lastrowid

    for detalle in detalles_guardados:
        cursor.execute(
            """
            INSERT INTO detalles_fleje (id_orden, calibre, ancho_cm, largo_cm, largo_gancho_cm,
                                        cantidad_piezas, metros_lineales, consumo_kg, estado)
            VALUES (:orden, :calibre, :ancho, :largo, :gancho, :piezas, :metros, :consumo, 'En Cola')
            """,
            {'orden': id_orden, 'calibre': detalle['calibre'], 'ancho': detalle['ancho_cm'],
             'largo': detalle['largo_cm'], 'gancho': detalle['gancho_cm'],
             'piezas': detalle['cantidad_piezas'], 'metros': detalle['metros_lineales'],
             'consumo': detalle['consumo_kg']},
        )
        cursor.execute(
            'UPDATE inventario_hierro SET stock_kg = stock_kg - ?, ultimo_update = ? WHERE id = ?',
            (detalle['consumo_kg'], ahora, detalle['stock_id']),
        )

    registrar_auditoria(
        conn, 'crear', 'orden_figurado', id_orden,
        f'Orden #{numero_orden} creada manualmente desde ventas. Consumo total: {round(total_kg, 4)} kg',
    )
    conn.commit()
    conn.close()
    return jsonify({
        'mensaje': f'Orden #{numero_orden} creada en fábrica',
        'id_orden': id_orden, 'numero_orden': numero_orden,
        'kg_total': round(total_kg, 4), 'estado': 'En Cola',
    }), 201


@bp.route('/api/fabrica/inventario-hierro', methods=['GET', 'POST'])
@login_required
def gestion_inventario_hierro():
    conn = get_db()

    if request.method == 'GET':
        rows = conn.execute(
            "SELECT calibre, diametro_mm, stock_kg, ultimo_update FROM inventario_hierro ORDER BY calibre"
        ).fetchall()
        conn.close()
        return jsonify([{'calibre': r[0], 'diametro_mm': r[1], 'stock_kg': r[2],
                         'ultimo_update': r[3]} for r in rows])

    data = request.json or {}
    calibre = str(data.get('calibre') or '').strip()
    try:
        stock_kg = float(data.get('stock_kg') or 0)
    except (ValueError, TypeError):
        stock_kg = 0.0
    observacion = str(data.get('observacion') or '').strip()

    if not calibre:
        conn.close()
        return jsonify({'error': 'Debe indicar el calibre del hierro'}), 400
    if stock_kg < 0:
        conn.close()
        return jsonify({'error': 'El stock no puede ser negativo'}), 400

    diametro_mm = parse_calibre_mm(calibre)
    ahora = _ahora()
    existing = conn.execute(
        'SELECT id FROM inventario_hierro WHERE calibre = ? ORDER BY id LIMIT 1', (calibre,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE inventario_hierro SET diametro_mm = ?, stock_kg = ?, ultimo_update = ? WHERE id = ?",
            (diametro_mm, stock_kg, ahora, existing[0]),
        )
        mensaje = f'Inventario de {calibre} actualizado correctamente'
    else:
        conn.execute(
            "INSERT INTO inventario_hierro (calibre, diametro_mm, stock_kg, ultimo_update) "
            "VALUES (:calibre, :diametro, :stock, :fecha)",
            {'calibre': calibre, 'diametro': diametro_mm, 'stock': stock_kg, 'fecha': ahora},
        )
        mensaje = f'Inventario de {calibre} registrado correctamente'

    registrar_auditoria(conn, 'guardar', 'inventario_hierro', calibre,
                        f'{mensaje}. Observación: {observacion or "Sin observación"}')
    conn.commit()
    conn.close()
    return jsonify({'mensaje': mensaje}), (200 if existing else 201)


@bp.route('/api/fabrica/ordenes/<int:id_orden>/estado', methods=['PUT'])
@login_required
def actualizar_estado_fabrica(id_orden):
    data = request.json or {}
    nuevo_estado = str(data.get('estado') or '').strip()
    if nuevo_estado not in ESTADOS_ORDEN_FLEJE:
        return jsonify({"error": "Estado inválido. Debe ser En Cola, En Figurado o Completado"}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    orden = cursor.execute(
        """
        SELECT o.id, o.numero_orden, o.estado, COALESCE(c.nombre, 'Cliente no registrado') AS cliente
        FROM ordenes_figurado o
        LEFT JOIN clientes c ON c.id = o.id_cliente
        WHERE o.id = ?
        """, (id_orden,)
    ).fetchone()
    if not orden:
        conn.close()
        return jsonify({"error": "Orden no encontrada"}), 404

    ahora = _ahora()
    cursor.execute(
        "UPDATE ordenes_figurado SET estado = ?, fecha_actualizacion = ?, fecha_entrega = ? WHERE id = ?",
        (nuevo_estado, ahora, ahora if nuevo_estado == 'Completado' else None, id_orden),
    )
    cursor.execute("UPDATE detalles_fleje SET estado = ? WHERE id_orden = ?", (nuevo_estado, id_orden))

    if nuevo_estado == 'Completado' and orden['estado'] != 'Completado':
        mensaje = f"✅ Los flejes de la orden {orden['numero_orden']} ({orden['cliente']}) ya están listos"
        cursor.execute(
            "INSERT INTO notificaciones_flejes (id_orden, numero_orden, cliente, mensaje, fecha) "
            "VALUES (:orden, :numero, :cliente, :mensaje, :fecha)",
            {'orden': id_orden, 'numero': orden['numero_orden'], 'cliente': orden['cliente'],
             'mensaje': mensaje, 'fecha': ahora},
        )

    registrar_auditoria(conn, 'estado_orden', 'orden_figurado', id_orden, f'Estado -> {nuevo_estado}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Orden actualizada a '{nuevo_estado}'", "estado": nuevo_estado})


@bp.route('/api/fabrica/notificaciones', methods=['GET'])
@login_required
def api_fabrica_notificaciones():
    """Notificaciones de flejes listos. destino=general|pedidos marca su flag de leída."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, id_orden, numero_orden, cliente, mensaje, leida, leida_pedidos, fecha "
        "FROM notificaciones_flejes ORDER BY id DESC LIMIT 50"
    ).fetchall()
    total_general = conn.execute(
        "SELECT COUNT(*) FROM notificaciones_flejes WHERE leida = 0"
    ).fetchone()[0]
    total_pedidos = conn.execute(
        "SELECT COUNT(*) FROM notificaciones_flejes WHERE leida_pedidos = 0"
    ).fetchone()[0]
    conn.close()
    return jsonify({"total_general": total_general, "total_pedidos": total_pedidos,
                    "notificaciones": [dict(r) for r in rows]})


@bp.route('/api/fabrica/notificaciones/marcar_leidas', methods=['POST'])
@login_required
def api_fabrica_notificaciones_marcar():
    data = request.get_json(force=True) or {}
    destino = str(data.get('destino') or 'general').strip()
    # La columna se elige de una lista blanca: nunca proviene de entrada libre.
    columna = 'leida_pedidos' if destino == 'pedidos' else 'leida'
    conn = get_db()
    conn.execute(f"UPDATE notificaciones_flejes SET {columna} = 1 WHERE {columna} = 0")
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Notificaciones marcadas como leídas"})


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
