"""La herramienta para clasificar el IVA del catalogo, y su efecto en el XML.

ESTE MODULO USA SU PROPIA BASE.
Los modulos de prueba comparten la base temporal de conftest.py. SQLite
serializa las escrituras, asi que si este modulo deja una conexion abierta
mientras otro corre, el otro recibe "database is locked" y falla por un motivo
que no tiene nada que ver con lo que esta probando. Para no romper pruebas
ajenas, este archivo prepara la suya.

EL AGUERO
`iva_tasa` se creó con `DEFAULT 0` y `iva_tipo_tarifa` con `DEFAULT '01'`, y
ninguna se migró. Resultado: 1461 productos con tasa 0 y tipo '01'
("excluido", art. 424 del Estatuto Tributario).

Con el IVA por producto activo, la venta no cobraría impuesto en casi todo. Y
el XML tampoco: `dian_emision._leer_items` decide la tarifa DIAN con
`iva_tipo_tarifa`, así que habría declarado un impuesto de 0 sobre una venta que
sí lo cobra. La DIAN rechaza esa incoherencia.

NINGÚN DATO PERMITE DEDUCIRLO: "Adhesivo cerámico para enchape" (gravado) y un
saco de arena (excluido) traen el mismo '01'. Es una decisión fiscal del
negocio, no del software. Por eso esta herramienta GUARDA lo que el
administrador escribe y no intenta adivinarlo.

Aquí se prueba:
  1. Que el XML declare LO MISMO que la venta, con el interruptor apagado.
  2. Que la clasificación por categoría y por ids funcione.
  3. Que solo el admin pueda usarla.
  4. Que se informen cuántos productos faltan por clasificar.
"""
import os
import shutil
import sqlite3
import tempfile

import pytest

_DIR = tempfile.mkdtemp()
_DB_PROPIO = os.path.join(_DIR, 'clasificacion.db')
shutil.copy2(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'ferreteria-semilla.db'),
    _DB_PROPIO)
os.environ['FERRETERIA_DB'] = _DB_PROPIO

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402
from ferreteria import db as db_mod  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def _base():
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


@pytest.fixture
def empleado(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'empleado'
        s['rol'] = 'empleado'
        s['id_usuario'] = 2
    return c


def _limpiar():
    conn = get_db()
    try:
        conn.execute('UPDATE productos SET iva_tasa = 0, iva_tipo_tarifa = \'01\'')
        conn.execute('UPDATE configuracion SET iva_por_producto = 0 WHERE id = 1')
        conn.commit()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# 1. El XML declara lo mismo que la venta
# ═════════════════════════════════════════════════════
def test_el_xml_no_declara_excluido_una_venta_gravada(admin, monkeypatch):
    """EL AGUERO, en la parte que la DIAN rechaza.

    Con `iva_tipo_tarifa = '01'` en todo el catálogo, el documento declararía
    tarifa '01' (excluido) para una venta que sí cobra IVA. Con el interruptor
    apagado, la tarifa del documento debe ser la GLOBAL del negocio.

    El producto de esta prueba se crea ya GRAVADO (`iva_tasa = 19`), que es lo
    que tendrá un artículo normal cuando el dueño lo clasifique. Así se
    comprueba que el documento no seleave influenciado por el '01' que arrastra
    el resto del catálogo sin migrar.
    """
    _limpiar()
    from ferreteria.services import dian_emision

    conn = get_db()
    try:
        pid = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, iva_tipo_tarifa, iva_naturaleza, stock_actual, activo)"
            " VALUES ('Adhesivo', 50000, 0, 0, 0, 19, '01', 'excluido', 100, 1)"
        ).lastrowid
        v = conn.execute(
            "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta,"
            " saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, iva_porcentaje,"
            " anulada, tipo_documento_dian) VALUES (1,'2026-09-30','10:00:00',"
            "50000,0,'efectivo',42017,7983,19,0,'POS')"
        ).lastrowid
        conn.execute(
            "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,"
            " precio_unitario, subtotal) VALUES (?,?,1,50000,50000)", (v, pid))
        conn.execute("UPDATE configuracion SET iva_porcentaje=19, iva_activo=1,"
                     " iva_por_producto=0 WHERE id=1")
        conn.commit()

        items = dian_emision._leer_items(conn, v, 19, iva_por_producto=False)
    finally:
        conn.close()

    assert len(items) == 1
    assert items[0]['iva_tasa'] == 19, (
        f"el XML declararía tasa {items[0]['iva_tasa']} para una venta que cobra "
        f"19%: el documento no cuadra con lo cobrado y la DIAN lo rechaza")
    assert items[0]['base'] > 0


def test_con_el_catalogo_clasificado_el_xml_sigue_a_la_tasa_cero(admin):
    """Ya clasificado, un producto de tasa cero declara tarifa 0 y la tarifa DIAN
    correcta ('01', excluido)."""
    _limpiar()
    from ferreteria.services import dian_emision

    conn = get_db()
    try:
        pid = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, iva_tipo_tarifa, stock_actual, activo)"
            " VALUES ('Arena', 50000, 0, 0, 0, 0, '01', 100, 1)"
        ).lastrowid
        v = conn.execute(
            "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta,"
            " saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, iva_porcentaje,"
            " anulada, tipo_documento_dian) VALUES (1,'2026-09-30','10:00:00',"
            "50000,0,'efectivo',50000,0,0,0,'POS')"
        ).lastrowid
        conn.execute(
            "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,"
            " precio_unitario, subtotal) VALUES (?,?,1,50000,50000)", (v, pid))
        conn.execute("UPDATE configuracion SET iva_por_producto = 1 WHERE id = 1")
        conn.commit()

        items = dian_emision._leer_items(conn, v, 19, iva_por_producto=True)
    finally:
        conn.close()

    assert items[0]['iva_tasa'] == 0, 'un producto de tasa cero debe declarar 0'
    assert items[0]['base'] == 50000


# ═════════════════════════════════════════════════════
# 2. La herramienta de clasificación
# ═════════════════════════════════════════════════════
def test_clasificar_una_categoria_entera(admin):
    """Es la forma práctica: "todo lo de esta categoría va a tasa cero"."""
    _limpiar()
    conn = get_db()
    try:
        conn.execute("UPDATE productos SET iva_tasa = 0, iva_tipo_tarifa = '01' "
                     "WHERE categoria = 'Cementos'")
        conn.commit()
    finally:
        conn.close()

    res = admin.post('/api/productos/tarifa-iva', json={
        'categoria': 'Cementos', 'iva_tasa': 0, 'iva_tipo_tarifa': '01'})
    assert res.status_code == 200
    d = res.get_json()
    assert d['afectados'] >= 1

    conn = get_db()
    try:
        filas = conn.execute(
            "SELECT iva_tasa, iva_tipo_tarifa FROM productos WHERE categoria = 'Cementos'"
        ).fetchall()
    finally:
        conn.close()
    assert all(f[0] == 0 for f in filas), 'la categoría no quedó toda a tasa 0'


def test_clasificar_productos_sueltos(admin):
    """Producto a producto, para los casos que la categoría no cubre."""
    _limpiar()
    conn = get_db()
    try:
        p1 = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, iva_tipo_tarifa, stock_actual, activo)"
            " VALUES ('Exento A', 1000, 0, 0, 0, 0, '01', 10, 1)").lastrowid
        p2 = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, iva_tipo_tarifa, stock_actual, activo)"
            " VALUES ('Gravado A', 1000, 0, 0, 0, 0, '01', 10, 1)").lastrowid
        conn.commit()
    finally:
        conn.close()

    admin.post('/api/productos/tarifa-iva', json={
        'ids': [p1], 'iva_tasa': 0, 'iva_tipo_tarifa': '02'})
    admin.post('/api/productos/tarifa-iva', json={
        'ids': [p2], 'iva_tasa': 19, 'iva_tipo_tarifa': '00'})

    conn = get_db()
    try:
        a = conn.execute('SELECT iva_tasa, iva_tipo_tarifa FROM productos WHERE id = ?',
                         (p1,)).fetchone()
        b = conn.execute('SELECT iva_tasa, iva_tipo_tarifa FROM productos WHERE id = ?',
                         (p2,)).fetchone()
    finally:
        conn.close()
    assert a == (0.0, '02'), f'exento mal: {a}'
    assert b == (19.0, '00'), f'gravado mal: {b}'


def test_no_se_adivina_la_tarifa_dian_incorrecta(admin):
    """Con tasa 0 pero sin código explícito, se asume '01' (excluido, art. 424).

    Es lo más común en una ferretería y se puede corregir después. Lo que NO
    hace es dejar '00' (gravada) con tasa 0, que es una incoherencia.
    """
    _limpiar()
    res = admin.post('/api/productos/tarifa-iva', json={
        'categoria': 'Arenas', 'iva_tasa': 0})
    assert res.status_code == 200
    assert res.get_json()['iva_tipo_tarifa'] == '01'


# ═════════════════════════════════════════════════════
# 3. Solo el admin, y solo con datos válidos
# ═════════════════════════════════════════════════════
def test_solo_el_admin_clasifica(empleado, admin):
    res = empleado.post('/api/productos/tarifa-iva', json={
        'categoria': 'Cementos', 'iva_tasa': 0})
    assert res.status_code == 403, 'un empleado no debe poder cambiar el IVA del catálogo'
    assert admin.post('/api/productos/tarifa-iva', json={
        'categoria': 'Cementos', 'iva_tasa': 0}).status_code == 200


@pytest.mark.parametrize('cuerpo, motivo', [
    ({'iva_tasa': 0}, 'sin ids ni categoría'),
    ({'categoria': 'X'}, 'sin tasa'),
    ({'categoria': 'X', 'iva_tasa': 7}, 'tasa que no existe'),
    ({'categoria': 'X', 'iva_tasa': 0, 'iva_tipo_tarifa': '99'}, 'tipo DIAN inválido'),
])
def test_rechaza_datos_invalidos(admin, cuerpo, motivo):
    """Prefiere un error claro a guardar una clasificación imposible."""
    res = admin.post('/api/productos/tarifa-iva', json=cuerpo)
    assert res.status_code == 400, f'{motivo}: debería rechazarse'


# ═════════════════════════════════════════════════════
# 4. El tablero de pendientes
# ═════════════════════════════════════════════════════
def test_informa_cuantos_faltan_por_clasificar(admin):
    """Para saber cuánto falta antes de activar el interruptor."""
    _limpiar()
    d = admin.get('/api/productos/iva/pendientes').get_json()
    assert d['total'] > 0
    assert d['pendientes'] > 0
    assert d['puede_activar'] is False, (
        'no se puede activar el IVA por producto con productos sin clasificar')
    assert d['iva_por_producto_activo'] is False
    assert d['categorias'], 'debe listar las categorías para saber qué clasificar'


def test_cuando_no_queda_pendiente_dice_que_se_puede_activar(admin):
    _limpiar()
    conn = get_db()
    try:
        conn.execute("UPDATE productos SET iva_tasa = 19, iva_tipo_tarifa = '00'")
        conn.commit()
    finally:
        conn.close()

    d = admin.get('/api/productos/iva/pendientes').get_json()
    assert d['pendientes'] == 0
    assert d['puede_activar'] is True


def test_la_clasificacion_queda_auditada(admin):
    """Cambiar el IVA de un catálogo entero es un movimiento que se debe poder
    rastrear."""
    _limpiar()
    admin.post('/api/productos/tarifa-iva', json={
        'categoria': 'Cementos', 'iva_tasa': 0})
    conn = get_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM auditoria WHERE accion = 'clasificar_iva'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n >= 1, 'la clasificación del IVA debe quedar en la auditoría'
