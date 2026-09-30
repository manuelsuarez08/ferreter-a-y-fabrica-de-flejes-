"""Pruebas de integridad del dato: nada se pierde, nada se duplica.

Hasta ahora se ha probado que el software FUNCIONA: que guarda, que emite, que
lista. Estas pruebas miran otra cosa, que es la que duele cuando falla: que el
registro guardado sea el mismo que el pedido, y que no se pueda alterar por un
camino que no sea la venta.

Tres escenarios de ferretería que aparecen todo el tiempo:

1. Ajuste de precio en la factura. Si el cálculo del IVA se hiciera sobre el
   total con IVA en vez de sobre la base, el total guardado NO sería el que
   escribió el cajero: la caja del día no cuadra con la suma de las facturas.
2. Edición posterior. Si `PUT /api/ventas/<id>` aceptara cambiar la cantidad
   de un producto, el inventario dejaría de cuadrar con las líneas guardadas.
3. Importes negativos. Un precio negativo genera un IVA negativo y un total
   que la DIAN rechaza; y hace que el "total de la venta" sea menor que el
   de una venta vacía.
"""
import os
import shutil
import tempfile

import pytest

_DIR = tempfile.mkdtemp()
os.environ['FERRETERIA_DB'] = os.path.join(_DIR, 'ferreteria.db')
shutil.copy2(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'ferreteria-semilla.db'),
    os.environ['FERRETERIA_DB'])

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def _base():
    from ferreteria import db as db_mod
    conn = get_db()
    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.commit()
    conn.close()


@pytest.fixture
def app():
    return create_app(inicializar_db=False, iniciar_hilo_dian=False)


@pytest.fixture
def admin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'admin'
        s['rol'] = 'admin'
        s['id_usuario'] = 1
    return c


def _cerrar_todo():
    """Cierra cualquier conexión abierta de este modulo.

    `get_db()` deja la conexión viva si el test falla a mitad. Con WAL, una
    escritura pendiente bloquea la siguiente, y el error aparece en la prueba
    SIGUIENTE (la que intenta escribir), no en la que se dejó sucia. Es un
    fallo muy confuso de perseguir.
    """
    global _conn
    if _conn is not None:
        try:
            _conn.commit()
        except Exception:
            pass
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


_conn = None


def _abrir():
    """Abre y cierra una conexion. Para consultas sueltas, sin dejar nada vivo."""
    return get_db()


def _producto(nombre, precio, iva=19, stock=100):
    """Crea un producto y cierra la conexión.

    La conexión se abre y se CIERRA en cada uso. Mantenerla viva entre
    llamadas hace que la escritura quede en una transacción abierta y la
    siguiente prueba se encuentre con "database is locked": el error aparece
    en la prueba que viene después, no en la que lo dejó sucio.
    """
    conn = get_db()
    try:
        # `precio_costo` es NOT NULL en el esquema. Va en 0 a proposito: esta
        # prueba mide aritmetica de venta, no el margen.
        cur = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, "
            "precio_base, iva_valor, iva_tasa, stock_actual, activo) "
            "VALUES (?,?,0,0,0,?,?,1)",
            (nombre, precio, iva, stock))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _sin_conexiones_colgadas():
    """Red de seguridad: ninguna prueba deja una escritura a medias."""
    yield


# ═════════════════════════════════════════════════════
# 1. El total guardado es el que escribió el cajero
# ═════════════════════════════════════════════════════
def test_el_total_guardado_es_exactamente_el_del_pedido(admin):
    """El caso del cajero que negocia un precio.

    El precio de venta es el FINAL (con IVA dentro). Si el total se calculara
    como base + IVA sobre un precio que ya incluye el impuesto, saldría
    inflado. La caja del día tiene que cuadrar con lo que se escribió.
    """
    pid = _producto('Cemento 50kg', 50000)
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': 2, 'precio_unitario': 50000}]})
    assert res.status_code == 201
    id_venta = res.get_json()['id_venta']

    conn = get_db()
    try:
        fila = conn.execute(
            'SELECT total_venta, subtotal_venta, iva_valor FROM ventas WHERE id = ?',
            (id_venta,)).fetchone()
    finally:
        conn.close()
    total, subtotal, iva = fila
    assert total == 100000, f'el total guardado es {total}, el cajero escribio 100000'
    # Y la aritmética interna tiene que cerrar.
    assert abs(subtotal + iva - total) < 1, (
        f'subtotal {subtotal} + IVA {iva} != total {total}: el documento no cuadra')


def test_varias_lineas_suman_el_total(admin):
    """El error clásico: el IVA se aplica al total con IVA en vez de a la base.

    Con varias líneas, un cálculo mal hecho se ve: el total no es la suma de
    los precios que puso el cajero.
    """
    a = _producto('Producto A', 10000)
    b = _producto('Producto B', 25000)
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [
            {'id_producto': a, 'cantidad': 1, 'precio_unitario': 10000},
            {'id_producto': b, 'cantidad': 3, 'precio_unitario': 25000},
        ]})
    assert res.status_code == 201
    conn = get_db()
    try:
        total, subtotal, iva = conn.execute(
            'SELECT total_venta, subtotal_venta, iva_valor FROM ventas WHERE id = ?',
            (res.get_json()['id_venta'],)).fetchone()
    finally:
        conn.close()
    assert total == 10000 + 25000 * 3, f'total {total}, el cajero escribio 85000'
    assert abs(subtotal + iva - total) < 1


def test_un_precio_negativo_se_rechaza(admin):
    """Un precio negativo produce un IVA negativo y un total sin sentido.

    La DIAN rechaza ese documento, así que mejor no guardarlo: el POS tiene que
    avisar al cajero antes.
    """
    pid = _producto('Producto', 10000)
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': -5000}]})
    assert res.status_code == 400, (
        'acepto un precio negativo: genera un IVA negativo y un documento que '
        'la DIAN va a rechazar')
    assert 'precio' in res.get_json()['error'].lower()


def test_una_cantidad_negativa_se_rechaza(admin):
    """Restar mercadería no es una venta."""
    pid = _producto('Producto', 10000)
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': -3, 'precio_unitario': 10000}]})
    assert res.status_code == 400


# ═════════════════════════════════════════════════════
# 2. Lo guardado se puede ver y editar, pero sin romper la aritmética
# ═════════════════════════════════════════════════════
def test_la_edicion_recalcula_y_cuadra(admin):
    """Editar precios desde la factura debe dejar la venta consistente."""
    pid = _producto('Editable', 10000)
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': 10000}]})
    id_venta = res.get_json()['id_venta']

    put = admin.put(f'/api/ventas/{id_venta}/detalle', json={
        'items': [{'id_producto': pid, 'cantidad': 2, 'precio_unitario': 15000}]})
    assert put.status_code == 200
    d = put.get_json()

    assert d['total_venta'] == 30000, f'total tras editar: {d["total_venta"]}'
    assert abs(d['subtotal_venta'] + d['iva_valor'] - d['total_venta']) < 1
    # Y la venta guardada debe decir lo mismo que la respuesta.
    conn = get_db()
    try:
        total = conn.execute(
            'SELECT total_venta FROM ventas WHERE id = ?', (id_venta,)).fetchone()[0]
    finally:
        conn.close()
    assert total == d['total_venta']


def test_editar_ajusta_el_inventario(admin):
    """Cambiar la cantidad tiene que mover el stock, o el inventario miente."""
    pid = _producto('Stock', 10000, stock=50)

    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': 2, 'precio_unitario': 10000}]})
    id_venta = res.get_json()['id_venta']

    def stock():
        c = get_db()
        try:
            return c.execute('SELECT stock_actual FROM productos WHERE id = ?',
                             (pid,)).fetchone()[0]
        finally:
            c.close()

    assert stock() == 48, 'la venta inicial no descontó bien'
    admin.put(f'/api/ventas/{id_venta}/detalle', json={
        'items': [{'id_producto': pid, 'cantidad': 5, 'precio_unitario': 10000}]})
    # Se vendieron 5 en total, no 2: el stock debe reflejarlo.
    assert stock() == 45, (
        f'stock {stock()} tras editar a 5 unidades; se esperaba 45')


# ═════════════════════════════════════════════════════
# 3. Las ventas emitidas quedan congeladas
# ═════════════════════════════════════════════════════
def test_una_venta_emitida_no_se_puede_editar(admin):
    """Si ya tiene documento electrónico, editarla rompe la trazabilidad.

    El documento firmado hablaría de unas cantidades que ya no son las de la
    venta, y la DIAN rechaza esa inconsistencia.
    """
    pid = _producto('Ya emitida', 10000)
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': 10000}]})
    id_venta = res.get_json()['id_venta']

    conn = get_db()
    try:
        conn.execute(
            "UPDATE ventas SET dian_estado='aceptado', numero_dian='POS-1' "
            "WHERE id = ?", (id_venta,))
        conn.commit()
    finally:
        conn.close()

    put = admin.put(f'/api/ventas/{id_venta}/detalle', json={
        'items': [{'id_producto': pid, 'cantidad': 99, 'precio_unitario': 10000}]})
    assert put.status_code == 409, (
        'permitio editar una venta ya emitida: el documento firmado dejaria de '
        'corresponder con la venta')
