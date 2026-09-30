"""Pruebas de las series de numeración DIAN y del selector POS / FV.

El problema que resuelven: la numeración vivía en columnas sueltas de
`configuracion` (un solo prefijo y un solo consecutivo), con lo cual era
impossible emitir a la vez el Documento Equivalente POS del mostrador y la
Factura Electrónica que pide un maestro de obra. Cada una necesita su propia
resolución y su propio rango autorizado por la DIAN.

Aquí se fija:
  - Las series son independientes y cada una lleva su consecutivo.
  - El consecutivo NUNCA se reutiliza (la DIAN rechaza el duplicado).
  - Una Factura Electrónica sin NIT del cliente se rechaza ANTES de cobrar.
  - La nota crédito encuentra el documento original sea POS o FV.
"""
import importlib
import os

import pytest

# La base temporal y la variable FERRETERIA_DB las prepara `tests/conftest.py`,
# que se carga antes que este modulo.
from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402
from ferreteria.services import dian_series  # noqa: E402

# OJO: no se comprueba `cfg.DB_NAME == FERRETERIA_DB`. Otros modulos de prueba
# crean su propia copia y reasignan la variable, asi que la asercion fallaria
# segun el orden de recoleccion. Lo que importa es que NINGUN modulo apunte a
# la base real del proyecto, y eso lo garantiza conftest.

_conn = None


def _abrir():
    """Conexión de trabajo. Se abre y cierra por prueba (ver `_cerrar`)."""
    global _conn
    if _conn is None:
        _conn = get_db()
    return _conn


@pytest.fixture(scope='session', autouse=True)
def _base_migrada():
    """Aplica las migraciones UNA vez, antes de la primera prueba.

    `get_db()` solo abre la conexión: no crea ni migra el esquema. La copia de
    la semilla es de antes del cambio, así que sin esto `series_dian` y
    `ventas.tipo_documento_dian` no existirían.

    Va como fixture y no en `pytest_configure` a propósito: se ejecuta en el
    orden normal de pytest y es fácil de leer si algo falla.
    """
    conn = get_db()
    db_mod = importlib.import_module('ferreteria.db')
    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.commit()
    conn.close()
    yield


@pytest.fixture(autouse=True)
def _cerrar():
    yield
    global _conn
    if _conn is not None:
        _conn.commit()
        _conn.close()
        _conn = None


@pytest.fixture(autouse=True)
def _series_en_estado_base():
    """Cada prueba arranca con las series en su estado por defecto.

    Hace falta por dos motivos, ambos por interferencia entre modulos:

    1. El consecutivo se reinicia. `test_emision_con_series.py` emite de
       verdad y deja el contador movido; como el orden de pytest no es el del
       archivo, el modulo puede correr antes o despues.
    2. El prefijo vuelve a su valor por defecto, porque el test de emision
       configura SETP111111111 / SETP222222222 para probar el aislamiento.

    Un `autouse=True` aqui NO alcanza para lo que hace el otro modulo (los
    fixtures son por modulo), asi que cada lado se limpia a si mismo.
    """
    conn = _abrir()
    conn.execute("UPDATE series_dian SET consecutivo = 1, prefijo = tipo_documento")
    conn.commit()
    yield


@pytest.fixture
def app():
    # `create_app()` con `inicializar_db=True` puede Volver a sembrar el
    # archivo desde la semilla (que no conoce `series_dian`). Se le pide
    # explícitamente que NO toque la base: las migraciones ya corrieron en
    # `pytest_configure` y las pruebas necesitan esa tabla.
    return create_app(inicializar_db=False)


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


# ═════════════════════════════════════════════════════
# La tabla de series
# ═════════════════════════════════════════════════════
def test_la_tabla_de_series_existe():
    conn = _abrir()
    tablas = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'series_dian' in tablas


def test_las_series_base_estan_creadas():
    """POS, FV y NC deben existir desde el arranque: es lo que permite
    cambiar de tipo de documento sin configurar nada más."""
    conn = _abrir()
    tipos = {r[0] for r in conn.execute('SELECT tipo_documento FROM series_dian')}
    assert tipos == {'POS', 'FV', 'NC'}


def test_las_series_no_comparten_consecutivo():
    """Este es el punto: dos numeraciones independientes.

    Emitir un POS no debe avanzar el contador de las facturas, ni al revés. Con
    el consecutivo global anterior eso era imposible.
    """
    conn = _abrir()
    series = dian_series.leer_series(conn)
    assert series['POS']['consecutivo'] == 1
    assert series['FV']['consecutivo'] == 1

    r1 = dian_series.reservar_numero(conn, 'POS')
    assert r1['numero'] == 'POS-1'
    assert dian_series.leer_serie(conn, 'FV')['consecutivo'] == 1, (
        'emitir un POS no debe tocar el consecutivo de las facturas')


def test_el_numero_nunca_se_reutiliza():
    """La DIAN rechaza dos documentos con el mismo número."""
    conn = _abrir()
    numeros = [dian_series.reservar_numero(conn, 'FV')['numero'] for _ in range(4)]
    assert numeros == ['FV-1', 'FV-2', 'FV-3', 'FV-4']
    assert len(set(numeros)) == 4


def test_cada_serie_usa_su_propio_prefijo():
    """Es lo que permite tener resolución 'SETP...' en pruebas para POS y otra
    distinta para facturas, sin que se mezclen los números."""
    conn = _abrir()
    # Se fija el consecutivo a 1 para que el resultado no dependa del orden en
    # que pytest ejecuta las pruebas.
    dian_series.actualizar_serie(conn, 'POS', {'prefijo': 'SETP111111111', 'consecutivo': 1})
    dian_series.actualizar_serie(conn, 'FV', {'prefijo': 'SETP222222222', 'consecutivo': 1})
    assert dian_series.reservar_numero(conn, 'POS')['numero'] == 'SETP111111111-1'
    assert dian_series.reservar_numero(conn, 'FV')['numero'] == 'SETP222222222-1'


def test_el_rango_agotado_bloquea_la_serie():
    """Con resolución real hay que avisar antes de generar un número que la
    DIAN va a rechazar."""
    conn = _abrir()
    dian_series.actualizar_serie(conn, 'NC', {'prefijo': 'NC', 'rango_hasta': 2, 'consecutivo': 1})
    dian_series.reservar_numero(conn, 'NC')   # 1
    dian_series.reservar_numero(conn, 'NC')   # 2
    ok, motivo = dian_series.serie_operativa(dian_series.leer_serie(conn, 'NC'))
    assert not ok
    assert 'rango' in motivo.lower()
    with pytest.raises(dian_series.ErrorSerie):
        dian_series.reservar_numero(conn, 'NC', estricto=True)


def test_rango_cero_significa_sin_limite():
    """Mientras se trabaja con prefijo de pruebas no hay rango autorizado."""
    conn = _abrir()
    dian_series.actualizar_serie(conn, 'FV',
                                 {'prefijo': 'SETP99', 'rango_hasta': 0, 'consecutivo': 1})
    for _ in range(20):
        dian_series.reservar_numero(conn, 'FV')
    assert dian_series.leer_serie(conn, 'FV')['consecutivo'] == 21


def test_actualizar_una_serie_no_pisa_el_consecutivo():
    """El panel manda el formulario completo; el consecutivo no se toca salvo
    que se mande explícitamente."""
    conn = _abrir()
    dian_series.actualizar_serie(conn, 'FV', {'consecutivo': 1})
    dian_series.reservar_numero(conn, 'FV')
    dian_series.reservar_numero(conn, 'FV')
    dian_series.actualizar_serie(conn, 'FV', {'prefijo': 'SETP555', 'rango_hasta': 100})
    serie = dian_series.leer_serie(conn, 'FV')
    assert serie['prefijo'] == 'SETP555'
    assert serie['rango_hasta'] == 100
    assert serie['consecutivo'] == 3, 'el consecutivo debe seguir donde estaba'


# ═════════════════════════════════════════════════════
# Endpoints
# ═════════════════════════════════════════════════════
def test_se_listan_las_series(admin):
    res = admin.get('/api/dian/series')
    assert res.status_code == 200
    d = res.get_json()
    tipos = [s['tipo_documento'] for s in d['series']]
    assert set(tipos) == {'POS', 'FV', 'NC'}
    # El panel recibe el siguiente número ya calculado.
    for s in d['series']:
        assert s['siguiente_numero']


def test_solo_el_admin_configura_una_serie(empleado, admin):
    res = empleado.put('/api/dian/series/FV', json={'prefijo': 'SETP123'})
    assert res.status_code == 403
    assert admin.put('/api/dian/series/FV', json={'prefijo': 'SETP123'}).status_code == 200


def test_el_panel_rechaza_un_prefijo_invalido(admin):
    """La DIAN exige SETP en pruebas; un prefijo inventado se rechaza aquí."""
    res = admin.put('/api/dian/series/POS', json={'prefijo': 'INVENTADO'})
    assert res.status_code == 400
    assert 'prefijo' in res.get_json()['error'].lower()


def test_se_puede_configurar_una_serie_de_pruebas(admin):
    """El escenario pedido: trabajar sin resolución real."""
    res = admin.put('/api/dian/series/POS', json={
        'prefijo': 'SETP990000000', 'rango_desde': 1, 'rango_hasta': 0,
        'clave_tecnica': 'CLAVE-DE-PRUEBA'})
    assert res.status_code == 200
    series = {s['tipo_documento']: s for s in res.get_json()['series']}
    assert series['POS']['prefijo'] == 'SETP990000000'
    assert series['POS']['operativa'] is True


# ═════════════════════════════════════════════════════
# El selector POS / FV en la venta
# ═════════════════════════════════════════════════════
def _producto():
    return _abrir().execute('SELECT id, precio_venta FROM productos LIMIT 1').fetchone()


def test_una_venta_se_registra_como_pos_por_defecto(admin):
    pid, precio = _producto()
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': precio}]})
    assert res.status_code == 201
    tipo = _abrir().execute(
        'SELECT tipo_documento_dian FROM ventas WHERE id = ?',
        (res.get_json()['id_venta'],)).fetchone()[0]
    assert tipo == 'POS'


def test_una_venta_puede_registrarse_como_factura_electronica(admin):
    """Con un cliente que tiene NIT, el cajero elige FV y se guarda."""
    admin.post('/api/clientes', json={'nombre': 'Maestro de Obra', 'cedula_nit': '900111222'})
    clientes = admin.get('/api/clientes').get_json()
    cliente = next(c for c in clientes if c['cedula_nit'] == '900111222')

    pid, precio = _producto()
    res = admin.post('/api/ventas', json={
        'id_cliente': cliente['id'], 'tipo_pago': 'efectivo',
        'tipo_documento_dian': 'FV',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': precio}]})
    assert res.status_code == 201
    tipo = _abrir().execute(
        'SELECT tipo_documento_dian FROM ventas WHERE id = ?',
        (res.get_json()['id_venta'],)).fetchone()[0]
    assert tipo == 'FV'


def test_factura_electronica_sin_nit_se_rechaza_antes_de_cobrar(admin):
    """El cliente mostrador (documento 222) es el consumidor final genérico.

    Una FV a su nombre no sirve para deducir impuestos, así que se rechaza al
    registrar, no después de emitir.
    """
    pid, precio = _producto()
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo', 'tipo_documento_dian': 'FV',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': precio}]})
    assert res.status_code == 400
    assert 'cédula' in res.get_json()['error'].lower() or 'nit' in res.get_json()['error'].lower()


def test_un_tipo_de_documento_inventado_cae_a_pos(admin):
    """Si alguien manipula el request, no se crea una serie fantasma."""
    pid, precio = _producto()
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo', 'tipo_documento_dian': 'XXX',
        'items': [{'id_producto': pid, 'cantidad': 1, 'precio_unitario': precio}]})
    assert res.status_code == 201
    tipo = _abrir().execute(
        'SELECT tipo_documento_dian FROM ventas WHERE id = ?',
        (res.get_json()['id_venta'],)).fetchone()[0]
    assert tipo == 'POS'


def test_el_listado_de_ventas_informa_el_tipo(admin):
    ventas = admin.get('/api/ventas').get_json()
    assert ventas
    assert all('tipo_documento_dian' in v for v in ventas)


# ═════════════════════════════════════════════════════
# La nota crédito debe encontrar el documento sea POS o FV
# ═════════════════════════════════════════════════════
def test_la_nota_credito_busca_pos_y_fv():
    """Regresión: el filtro era `tipo_documento = 'POS'` y dejaba fuera las
    facturas electrónicas, así que la NC de una FV no encontraba su original."""
    import io
    import re
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'ferreteria', 'services', 'dian_nota_credito.py')
    with io.open(ruta, encoding='utf-8') as f:
        src = f.read()
    m = re.search(r"WHERE id_venta = \? AND tipo_documento ([^\n]+)", src)
    assert m, 'no se encontro el filtro del documento original'
    assert "'POS'" in m.group(1) and "'FV'" in m.group(1)
    # Y se sigue excluyendo la propia NC: una nota no se corrige con otra nota.
    assert "'NC'" not in m.group(1)
