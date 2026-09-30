"""El IVA por producto queda DESACTIVADO hasta que el dueño clasifique el catálogo.

QUÉ PASÓ
La ronda pasada cambió el cálculo para usar la tasa de cada producto en vez
de la tasa global. La idea era correcta: un cemento de tasa cero (art. 422 del
Estatuto Tributario) no puede cobrar 19% igual que un tubo.

Pero al auditar el catálogo，结果 fue este:

    iva_tasa = 0.0   →  1460 productos
    iva_tasa = 19.0  →     1 producto   (creado en una prueba)

`iva_tasa` se creó con `DEFAULT 0` y NUNCA se migró. Con la tasa por producto,
casi toda la venta habría salido sin impuesto: un ladrillo de $1.200 pasaba de
$1.428 a $1.200.

`iva_tipo_tarifa` está igual: los 1461 productos tienen '01' ("excluido",
art. 424), que es lo que el propio _msg.txt del primer turno advertía que
pasaría si se usaba el valor por defecto como criterio.

POR QUÉ NO SE MIGRA EL CATÁLOGO
Decidir qué productos son de tasa cero es una decisión FISCAL del negocio, no
del software. Ningún dato del catálogo permite deducirlo: el nombre dice
"Adhesivo cerámico para enchape" (gravado, 19%) con el mismo '01' que un
saco de arena. Automatizarlo sería inventar una clasificación fiscal.

QUÉ SE HACE EN LUGAR DE ESO
Un interruptor `configuracion.iva_por_producto`, en 0 por defecto. Con él
apagado, toda venta usa la tasa global: exactamente como funcionaba antes de
la corrección. Cuando el dueño clasifique el catálogo y ponga en 1, se activa
la vía por producto.

Estas pruebas fijan que el comportamiento por defecto NO cambia.
"""
import os
import shutil
import sqlite3
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


def _producto(nombre, precio, tasa=0):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO productos (nombre, precio_venta, precio_costo, precio_base,"
            " iva_valor, iva_tasa, stock_actual, activo) VALUES (?,?,0,0,0,?,500,1)",
            (nombre, precio, tasa))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _flag(valor):
    conn = get_db()
    try:
        conn.execute('UPDATE configuracion SET iva_por_producto = ? WHERE id = 1',
                     (valor,))
        conn.commit()
    finally:
        conn.close()


def _vender(admin, pid, cantidad, precio):
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': cantidad,
                   'precio_unitario': precio}]})
    assert res.status_code == 201, res.get_json()
    conn = get_db()
    try:
        return conn.execute(
            'SELECT total_venta, subtotal_venta, iva_valor FROM ventas WHERE id = ?',
            (res.get_json()['id_venta'],)).fetchone()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# El interruptor existe y está apagado
# ═════════════════════════════════════════════════════
def test_el_interruptor_esta_apagado_por_defecto():
    """Con el catálogo sin clasificar, la vía por producto no se usa."""
    conn = get_db()
    try:
        valor = conn.execute(
            'SELECT COALESCE(iva_por_producto, 0) FROM configuracion WHERE id = 1'
        ).fetchone()[0]
    finally:
        conn.close()
    assert valor == 0, (
        'el interruptor arranca en 1, pero el catálogo no está clasificado: '
        'la ferretería se quedaría sin cobrar IVA')


def test_la_configuracion_lo_expone(admin):
    """El administrador necesita verlo para saber que la vía existe."""
    d = admin.get('/api/configuracion').get_json()
    assert 'iva_por_producto' in d
    assert d['iva_por_producto'] is False


# ═════════════════════════════════════════════════════
# Con el interruptor apagado, NADA cambia
# ═════════════════════════════════════════════════════
def test_sin_clasificar_se_cobra_la_tasa_global(admin):
    """El comportamiento de siempre: todo al 19% del negocio.

    Este es el criterio importante. El producto tiene tasa 0 en el catálogo,
    pero como el interruptor está apagado se le aplica la tasa general.

    OJO con el número esperado: $1.200 es el PRECIO FINAL, así que el total
    es $1.200 y el IVA se EXTRAE de ahí ($192), no se le suma encima. Que dé
    $1.428 sería el bug del turno pasado.
    """
    _flag(0)
    pid = _producto('Ladrillo (sin clasificar)', 1200, tasa=0)
    total, base, iva = _vender(admin, pid, 1, 1200)
    assert total == 1200, f'total {total}: el precio final es 1200 y ese es el total'
    # 1200 / 1.19 = 1008.40 de base, y el IVA es la diferencia: 192.
    assert iva == 192, f'IVA {iva}: 1200/1.19 son 1008 de base y 192 de impuesto'
    assert base == 1008
    assert abs(base + iva - total) < 1


def test_la_aritmetica_sigue_cerrando(admin):
    """Sea cual sea la vía, subtotal + IVA = total."""
    _flag(0)
    pid = _producto('Producto', 50000, tasa=0)
    total, base, iva = _vender(admin, pid, 1, 50000)
    assert abs(base + iva - total) < 1
    assert total == 50000, 'el total es SIEMPRE el precio que escribió el cajero'


# ═════════════════════════════════════════════════════
# Con el interruptor encendido, manda el producto
# ═════════════════════════════════════════════════════
def test_clasificado_el_producto_manda(admin):
    """Ya con el catálogo en manos, un producto de tasa cero no paga IVA.

    Es el comportamiento correcto y el que se activa cuando el dueño clasifique
    el catálogo. El precio que paga el cliente NO cambia: cambia cómo se
    desglosa para el documento.
    """
    _flag(1)
    try:
        pid = _producto('Cemento exento (art. 422)', 50000, tasa=0)
        total, base, iva = _vender(admin, pid, 1, 50000)
        assert total == 50000, 'el total sigue siendo el precio final'
        assert iva == 0, f'IVA {iva}: un producto de tasa cero no debe pagarlo'
        assert base == 50000
    finally:
        _flag(0)


def test_clasificado_un_producto_gravado_si_paga(admin):
    _flag(1)
    try:
        pid = _producto('Adhesivo gravado', 50000, tasa=19)
        total, base, iva = _vender(admin, pid, 1, 50000)
        assert total == 50000
        assert iva == 7983, f'IVA {iva}: 50000/1.19 = 7983'
    finally:
        _flag(0)


def test_clasificado_una_venta_mixta(admin):
    """Un gravado y uno de tasa cero en la misma venta."""
    _flag(1)
    try:
        a = _producto('Gravado', 50000, tasa=19)
        b = _producto('Exento', 50000, tasa=0)
        res = admin.post('/api/ventas', json={
            'id_cliente': 1, 'tipo_pago': 'efectivo',
            'items': [{'id_producto': a, 'cantidad': 1, 'precio_unitario': 50000},
                      {'id_producto': b, 'cantidad': 1, 'precio_unitario': 50000}]})
        conn = get_db()
        try:
            total, base, iva = conn.execute(
                'SELECT total_venta, subtotal_venta, iva_valor FROM ventas WHERE id = ?',
                (res.get_json()['id_venta'],)).fetchone()
        finally:
            conn.close()
        assert total == 100000
        assert iva == 7983, f'IVA {iva}: solo la parte gravada aporta impuesto'
    finally:
        _flag(0)


# ═════════════════════════════════════════════════════
# Lo que falta, visible
# ═════════════════════════════════════════════════════
def test_cuantos_productos_estan_sin_clasificar():
    """Muestra el tamaño del problema en vez de esconderlo.

    Si este numero baja, el dueño ya fue clasificando el catálogo. Si sigue en
    1460, la vía por producto tiene que seguir apagada.
    """
    conn = get_db()
    try:
        sin_clasificar = conn.execute(
            'SELECT COUNT(*) FROM productos WHERE COALESCE(iva_tasa, 0) = 0'
        ).fetchone()[0]
        total = conn.execute('SELECT COUNT(*) FROM productos').fetchone()[0]
    finally:
        conn.close()
    print(f'\n  productos sin clasificar: {sin_clasificar} de {total}')
    assert sin_clasificar >= 0  # es un dato, no una aserción de comportamiento


def test_el_catalogo_de_la_semilla_tiene_la_misma_deuda():
    """La semilla arrastra el mismo problema: se migla, no se corrige a mano."""
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'ferreteria-semilla.db')
    conn = sqlite3.connect(ruta)
    try:
        filas = conn.execute(
            'SELECT COUNT(*) FROM productos WHERE COALESCE(iva_tasa, 0) = 0'
        ).fetchone()[0]
    finally:
        conn.close()
    print(f'\n  semilla: {filas} productos con tasa 0')
