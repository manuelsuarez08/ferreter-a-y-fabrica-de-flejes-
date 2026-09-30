"""POS y backend tienen que dar EXACTAMENTE el mismo total.

Esta es la prueba que faltaba tras corregir el IVA. El bug era consistente de
punta a punta: el POS hacía la misma cuenta inflada que el servidor, así que el
cajero veía el total inflado y escribía ese número. Nadie lo notaba porque los
dos "concordaban".

Si se arregla solo uno de los dos, el cajero ve un precio y se guarda otro: es
peor que el bug original, porque ahora hay una discrepancia visible.

Se ejecuta el MISMO cálculo del JS en Python y se compara con lo que guarda el
servidor, para varios precios y cantidades.
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


# ── El cálculo que hace el POS, copiado literalmente de actualizarTotalesCarrito ──
def _total_del_pos(lineas, iva_porcentaje=19):
    subtotal_final = sum(cantidad * precio for cantidad, precio in lineas)
    if iva_porcentaje > 0:
        base = round(subtotal_final / (1 + iva_porcentaje / 100))
        iva = subtotal_final - base
    else:
        base, iva = subtotal_final, 0
    return base, iva, subtotal_final


def _vender(admin, nombre, lineas, iva=19):
    conn = get_db()
    try:
        # La tasa es la del PRODUCTO, no una global: un artículo de tasa cero
        # debe cobrarse sin impuesto aunque el negocio esté en 19%.
        pid = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, stock_actual, activo) VALUES (?,?,0,0,0,?,500,1)",
            (nombre, lineas[0][1], iva)).lastrowid
        conn.commit()
    finally:
        conn.close()
    items = [{'id_producto': pid, 'cantidad': c, 'precio_unitario': p}
             for c, p in lineas]
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo', 'items': items})
    assert res.status_code == 201, res.get_json()
    conn = get_db()
    try:
        total, base, iva = conn.execute(
            'SELECT total_venta, subtotal_venta, iva_valor FROM ventas WHERE id = ?',
            (res.get_json()['id_venta'],)).fetchone()
    finally:
        conn.close()
    return total, base, iva


@pytest.mark.parametrize('lineas', [
    [(1, 5000)],          # un tinto
    [(2, 15000)],         # el caso del bug
    [(1, 12345)],         # precio no redondo
    [(3, 10000), (2, 25000)],   # varias líneas
    [(1, 999)],           # precio pequeño
    [(7, 3333)],          # varias unidades
])
def test_el_pos_y_el_servidor_dan_el_mismo_total(admin, lineas):
    """Lo que ve el cajero es lo que se guarda. Ni un peso de diferencia."""
    nombre = f'Coherencia {lineas}'
    total, base, iva = _vender(admin, nombre, lineas)
    base_pos, iva_pos, total_pos = _total_del_pos(lineas)

    assert total == total_pos, (
        f'el POS muestra {total_pos} y el servidor guarda {total}: '
        f'el cajero ve un precio y se cobra otro')
    assert base == base_pos, f'base: POS {base_pos} vs servidor {base}'
    assert iva == iva_pos, f'IVA: POS {iva_pos} vs servidor {iva}'


def test_la_aritmetica_del_documento_cierra(admin):
    """La condición que exige la DIAN: subtotal + IVA = total."""
    total, base, iva = _vender(admin, 'Aritmetica', [(4, 25000)])
    assert abs(base + iva - total) < 1, (
        f'subtotal {base} + IVA {iva} != total {total}: el documento no cuadra '
        f'y la DIAN lo rechaza')


def test_editar_una_factaura_conserva_la_tasa_cero(admin):
    """La edicion usa la MISMA función de calculo que la creacion.

    Si el IVA viviera en dos sitios, editar una factura con un producto de tasa
    cero podria empezar a cobrarle 19%: la venta se habria creado bien y se
    corromperia al corregir un precio.
    """
    conn = get_db()
    try:
        exento = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, stock_actual, activo) "
            "VALUES ('Exento',50000,0,0,0,0,500,1)"
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': exento, 'cantidad': 2, 'precio_unitario': 50000}]})
    id_venta = res.get_json()['id_venta']

    put = admin.put(f'/api/ventas/{id_venta}/detalle', json={
        'items': [{'id_producto': exento, 'cantidad': 3, 'precio_unitario': 50000}]})
    assert put.status_code == 200
    d = put.get_json()

    assert d['total_venta'] == 150000, f'total tras editar: {d["total_venta"]}'
    assert d['iva_valor'] == 0, (
        f'la edicion cobro {d["iva_valor"]} de IVA a un producto de tasa cero')
def test_un_producto_de_tasa_cero_no_cobra_iva(admin):
    """Cemento de uso arquitectónico (art. 422) o material de extracción
    (art. 424): el producto tiene `iva_tasa = 0` y así se cobra.

    Antes toda la venta usaba la tasa global del negocio, así que un artículo
    de tasa cero pagaba 19% igual que uno gravado, y el documento declararía una
    tarifa que no era la del producto.
    """
    total, base, iva = _vender(admin, 'Tasa cero', [(2, 50000)], iva=0)
    assert total == 100000, f'total {total}: un producto de tasa cero no debe llevar impuesto'
    assert iva == 0
    assert base == 100000


def test_una_venta_mixta_tiene_iva_solo_de_lo_gravado(admin):
    """Dos productos, uno al 19% y otro a tasa cero.

    El total sigue siendo lo que puso el cajero, pero el impuesto solo se
    extrae de la parte gravada.
    """
    conn = get_db()
    try:
        gravado = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, stock_actual, activo) VALUES ('Gravado',50000,0,0,0,19,500,1)"
        ).lastrowid
        exento = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, stock_actual, activo) VALUES ('Exento',50000,0,0,0,0,500,1)"
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': gravado, 'cantidad': 1, 'precio_unitario': 50000},
                  {'id_producto': exento, 'cantidad': 1, 'precio_unitario': 50000}]})
    assert res.status_code == 201
    conn = get_db()
    try:
        total, base, iva = conn.execute(
            'SELECT total_venta, subtotal_venta, iva_valor FROM ventas WHERE id = ?',
            (res.get_json()['id_venta'],)).fetchone()
    finally:
        conn.close()

    assert total == 100000, f'total {total}, el cajero escribio 100000'
    # Solo la mitad gravada aporta impuesto: 50000 / 1.19 = 7983.19 -> 7983
    # (con el redondeo a pesos que exige la DIAN). Si también se gravara la
    # parte exenta, el IVA seria 19000.
    assert iva == 7983, f'IVA {iva}: solo debe gravarse la parte del 19% (7983)'
    assert iva < 10000, 'parece que se está gravando también la parte de tasa cero'
    assert abs(base + iva - total) < 1
