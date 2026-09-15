"""Servicio de órdenes de figurado (flejes).

Encapsula la creación de una orden de fábrica a partir de una venta, incluido
el descuento de stock de hierro por calibre. Concentra aquí la regla de
negocio (antes dispersa en app.py) y reutiliza el cálculo puro de fleje.
"""
from datetime import datetime

from .auditoria import registrar_auditoria
from .fleje import calcular_consumo_fleje


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _extraer_geometria(item, cantidad_fallback=1):
    """Normaliza los alias de campo del front-end a un dict de geometría."""
    return {
        'calibre': str(item.get('calibre') or item.get('calibre_hierro') or '').strip(),
        'ancho_cm': float(item.get('ancho_cm') or item.get('ancho') or 0),
        'largo_cm': float(item.get('largo_cm') or item.get('largo') or 0),
        'gancho_cm': float(
            item.get('largo_gancho_cm') or item.get('gancho_cm')
            or item.get('largo_gancho') or 0
        ),
        'cantidad_piezas': int(item.get('cantidad_piezas') or item.get('cantidad') or cantidad_fallback),
    }


def generar_numero_orden(id_venta=None):
    """Genera un número de orden de figurado único por día."""
    base = datetime.now().strftime('%Y%m%d')
    sufijo = id_venta if id_venta is not None else int(datetime.now().timestamp()) % 100000
    return f'FL-{base}-{sufijo}'


def registrar_orden_fleje_desde_venta(conn, id_venta, id_cliente, item, producto_nombre=''):
    """Crea una orden de figurado a partir de un ítem de venta y descuenta el hierro.

    Devuelve un dict con {id_orden, numero_orden, consumo_kg} o None si el ítem
    no corresponde a un fleje (sin calibre o sin geometría válida).
    """
    geo = _extraer_geometria(item)
    if not geo['calibre'] or geo['ancho_cm'] <= 0 or geo['largo_cm'] <= 0:
        return None

    calculo = calcular_consumo_fleje(
        geo['ancho_cm'], geo['largo_cm'], geo['gancho_cm'],
        geo['cantidad_piezas'], geo['calibre'],
    )
    if calculo['consumo_kg'] <= 0:
        return None

    numero_orden = f'FL-{datetime.now().strftime("%Y%m%d")}-{id_venta}'
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ordenes_figurado (
            id_venta, id_cliente, numero_orden, estado, fecha_creacion, fecha_actualizacion,
            observaciones, kg_total
        ) VALUES (:id_venta, :id_cliente, :numero_orden, 'En Cola', :fecha, :fecha,
                  :observaciones, :kg_total)
        """,
        {
            'id_venta': id_venta,
            'id_cliente': id_cliente,
            'numero_orden': numero_orden,
            'fecha': _ahora(),
            'observaciones': f"Pedido generado desde venta #{id_venta} - {producto_nombre}",
            'kg_total': calculo['consumo_kg'],
        },
    )
    id_orden = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO detalles_fleje (
            id_orden, calibre, ancho_cm, largo_cm, largo_gancho_cm, cantidad_piezas,
            metros_lineales, consumo_kg, estado
        ) VALUES (:id_orden, :calibre, :ancho, :largo, :gancho, :piezas,
                  :metros, :consumo, 'En Cola')
        """,
        {
            'id_orden': id_orden,
            'calibre': geo['calibre'],
            'ancho': geo['ancho_cm'],
            'largo': geo['largo_cm'],
            'gancho': geo['gancho_cm'],
            'piezas': geo['cantidad_piezas'],
            'metros': calculo['metros_lineales'],
            'consumo': calculo['consumo_kg'],
        },
    )

    row_stock = cursor.execute(
        "SELECT id, stock_kg FROM inventario_hierro WHERE calibre = ? ORDER BY id LIMIT 1",
        (geo['calibre'],),
    ).fetchone()
    if row_stock:
        if row_stock[1] < calculo['consumo_kg']:
            raise ValueError(
                f"Inventario insuficiente de hierro calibre {geo['calibre']}. "
                f"Disponible: {row_stock[1]} kg"
            )
        cursor.execute(
            "UPDATE inventario_hierro SET stock_kg = stock_kg - ?, ultimo_update = ? WHERE id = ?",
            (calculo['consumo_kg'], _ahora(), row_stock[0]),
        )
    else:
        cursor.execute(
            "INSERT INTO inventario_hierro (calibre, diametro_mm, stock_kg, ultimo_update) "
            "VALUES (?, ?, 0, ?)",
            (geo['calibre'], calculo['diametro_mm'], _ahora()),
        )
        raise ValueError(
            f"No existe inventario de hierro para el calibre {geo['calibre']}. "
            "Registre el material antes de vender flejes."
        )

    registrar_auditoria(
        conn, 'crear', 'orden_figurado', id_orden,
        f'Orden #{numero_orden} creada. Consumo estimado: {calculo["consumo_kg"]} kg',
    )
    return {'id_orden': id_orden, 'numero_orden': numero_orden, 'consumo_kg': calculo['consumo_kg']}
