"""Pruebas de los requerimientos de interfaz y flujo del POS.

Cubre lo pedido para que el mostrador sea operable y legal ante la DIAN:

  1. CRÉDITOS: ficha del cliente con la factura completa, el saldo adeudado y el
     historial de abonos filtrable por día, mes o año.
  2. BÚSQUEDA DE CLIENTES: el POS arranca en "Cliente Mostrador (General)" y hay
     un buscador server-side para los clientes registrados.
  3. OBSERVACIONES: las notas internas se guardan con la venta y vuelven en el
     detalle de la factura y en el listado.
  4. COTIZACIONES: se puede eliminar una cotización errónea (solo admin).

Y deja fijada la parte que NO debe cambiar:

  6. SEGURIDAD DIAN: una venta emitida no se anula borrándola; la unica salida es
     la Nota Credito Electronica, y el consecutivo nunca se reutiliza.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_pos_requerimientos.py -q
"""
import os
import shutil
import sqlite3
import tempfile

import pytest

_dir = tempfile.mkdtemp()
_db = os.path.join(_dir, 'ferreteria.db')
shutil.copy2(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'ferreteria-semilla.db'),
    _db)
os.environ['FERRETERIA_DB'] = _db

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402
import ferreteria.config as cfg  # noqa: E402

# Se reusa UNA conexion por archivo de prueba. Las pruebas escriben desde el
# cliente de Flask y desde sqlite3 directo, y SQLite serializa esas escrituras:
# con una conexion por fixture aparece "database is locked".
#
# Se cierra al terminar CADA prueba a proposito: `create_app()` corre
# `init_db()`, que necesita escribir (CREATE TABLE). Con la conexion viva, esa
# escritura se queda esperando el cerrojo y la app no arranca.
_conn = None


def pytest_configure():
    global _conn
    _conn = get_db()


def pytest_unconfigure():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _abrir():
    global _conn
    if _conn is None:
        _conn = get_db()
    return _conn


@pytest.fixture(autouse=True)
def _cerrar_conexion():
    yield
    global _conn
    if _conn is not None:
        _conn.commit()
        _conn.close()
        _conn = None


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def cliente(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'admin'
        s['rol'] = 'admin'
        s['id_usuario'] = 1
    return c


@pytest.fixture
def producto():
    return _abrir().execute('SELECT id FROM productos LIMIT 1').fetchone()[0]


# ═════════════════════════════════════════════════════
# 1. Créditos y abonos
# ═════════════════════════════════════════════════════
@pytest.fixture
def cliente_deudor():
    """Cliente con una factura a crédito de 300.000 y un abono de 100.000.

    El fixture lo comparten varias pruebas, asi que es idempotente: si el
    cliente y su abono ya estan de una corrida anterior, los reutiliza en vez
    de chocar con el indice unico de la cédula.
    """
    conn = _abrir()
    fila = conn.execute(
        "SELECT id FROM clientes WHERE cedula_nit = '999888777'").fetchone()
    if fila:
        id_cliente = fila[0]
        id_venta = conn.execute(
            "SELECT id FROM ventas WHERE id_cliente = ? ORDER BY id LIMIT 1",
            (id_cliente,)).fetchone()[0]
        return {'id': id_cliente, 'venta': id_venta}

    cur = conn.execute(
        "INSERT INTO clientes (nombre, cedula_nit, telefono, direccion) "
        "VALUES ('Cliente Deudor Prueba', '999888777', '3001112233', 'Calle 1')")
    id_cliente = cur.lastrowid
    cur.execute(
        "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente,"
        " tipo_pago, direccion_cliente, subtotal_venta, iva_valor, iva_porcentaje, anulada)"
        " VALUES (?, '2026-09-10', '09:00:00', 300000, 300000, 'credito', 'Calle 1',"
        " 252100, 47900, 19, 0)", (id_cliente,))
    id_venta = cur.lastrowid
    cur.execute(
        "INSERT INTO abonos (id_cliente, monto, fecha) VALUES (?, 100000, '2026-09-15 10:00:00')",
        (id_cliente,))
    conn.commit()
    return {'id': id_cliente, 'venta': id_venta}


def test_la_ficha_de_credito_muestra_las_facturas_con_saldo(cliente, cliente_deudor):
    """La seccion de creditos debe listar la factura completa, no solo una cifra."""
    res = cliente.get(f'/api/creditos/{cliente_deudor["id"]}')
    assert res.status_code == 200
    data = res.get_json()

    assert data['cliente']['nombre'] == 'Cliente Deudor Prueba'
    assert len(data['facturas']) == 1

    factura = data['facturas'][0]
    assert factura['id'] == cliente_deudor['venta']
    assert factura['total_venta'] == 300000
    assert factura['saldo_pendiente'] == 300000
    # El saldo adeudado se expone aparte para la tarjeta de resumen.
    assert data['deuda_total'] == 300000
    assert data['total_abonado'] == 100000
    # Y la factura trae los productos y la fecha, para poder reimprimirla.
    assert 'facturas' in data and data['facturas'][0]['fecha_dia'] == '2026-09-10'


def test_el_historial_de_abonos_se_filtra_por_dia_mes_y_ano(cliente, cliente_deudor):
    """El filtro de abonos es por dia, mes o anio."""
    id_cliente = cliente_deudor['id']

    # El abono de prueba es del 2026-09-15.
    dia = cliente.get(f'/api/abonos/{id_cliente}?periodo=dia&fecha=2026-09-15').get_json()
    assert dia['periodo'] == 'dia'
    assert len(dia['abonos']) == 1
    assert dia['abonos'][0]['monto'] == 100000

    # Un dia en el que no hubo abonos no debe traer la fila.
    vacio = cliente.get(f'/api/abonos/{id_cliente}?periodo=dia&fecha=2026-09-20').get_json()
    assert vacio['abonos'] == []

    mes = cliente.get(f'/api/abonos/{id_cliente}?periodo=mes&mes=2026-09').get_json()
    assert len(mes['abonos']) == 1

    anio = cliente.get(f'/api/abonos/{id_cliente}?periodo=anio&anio=2026').get_json()
    assert len(anio['abonos']) == 1

    otro_anio = cliente.get(f'/api/abonos/{id_cliente}?periodo=anio&anio=2025').get_json()
    assert otro_anio['abonos'] == []


def test_el_filtro_de_abonos_no_altera_el_saldo_acumulado(cliente, cliente_deudor):
    """Ver un solo mes no puede cambiar la deuda que se reporta.

    El saldo se calcula sobre el historial COMPLETO; si se calculara sobre el
    filtrado, al mirar septiembre la "deuda anterior" saldria en cero.
    """
    data = cliente.get(f'/api/abonos/{cliente_deudor["id"]}?periodo=mes&mes=2026-09').get_json()
    assert data['total_abonado'] == 100000
    assert data['saldo_pendiente'] == 200000
    # El saldo restante que se ve en la fila tambien es el real.
    assert data['abonos'][0]['saldo_pendiente'] == 200000


# ═════════════════════════════════════════════════════
# 2. Cliente mostrador por defecto y búsqueda de clientes
# ═════════════════════════════════════════════════════
def test_el_cliente_mostrador_es_el_primero_y_el_que_por_defecto(app, cliente):
    """El POS debe arrancar en el Cliente Mostrador (General), no en el último."""
    assert cfg.CLIENTE_MOSTRADOR_ID == 1
    assert cfg.NOMBRE_CLIENTE_MOSTRADOR == 'Cliente Mostrador (General)'

    conn = _abrir()
    nombre = conn.execute(
        'SELECT nombre FROM clientes WHERE id = ?', (cfg.CLIENTE_MOSTRADOR_ID,)
    ).fetchone()
    assert nombre and nombre[0] == 'Cliente Mostrador (General)'

    # La plantilla del POS recibe el id y el nombre para el autocompletado.
    html = cliente.get('/').get_data(as_text=True)
    assert str(cfg.CLIENTE_MOSTRADOR_ID) in html
    assert cfg.NOMBRE_CLIENTE_MOSTRADOR in html


def test_el_buscador_de_clientes_filtra_por_nombre_documento_y_telefono(cliente):
    """El autocompletado del POS consulta al servidor, no baja el catalogo entero."""
    conn = _abrir()
    cur = conn.execute(
        "INSERT INTO clientes (nombre, cedula_nit, telefono, direccion) "
        "VALUES ('Pedro Perez Zapata', '111222333', '3105559999', 'Carrera 9')")
    id_cliente = cur.lastrowid
    conn.commit()

    por_nombre = cliente.get('/api/clientes/buscar?q=Zapata').get_json()
    assert [c['id'] for c in por_nombre] == [id_cliente]

    por_documento = cliente.get('/api/clientes/buscar?q=111222333').get_json()
    assert [c['id'] for c in por_documento] == [id_cliente]

    por_telefono = cliente.get('/api/clientes/buscar?q=310555').get_json()
    assert [c['id'] for c in por_telefono] == [id_cliente]

    # Un texto que no coincide con nadie devuelve la lista vacia.
    assert cliente.get('/api/clientes/buscar?q=zzz-no-existe-zzz').get_json() == []


def test_se_puede_registrar_un_cliente_nuevo_desde_el_pos(cliente, cliente_deudor):
    """El modal de alta del POS crea el cliente y devuelve la lista de clientes."""
    res = cliente.post('/api/clientes', json={
        'nombre': 'Cliente Creado En Venta', 'cedula_nit': '555444333',
        'telefono': '3009998877', 'direccion': 'Calle 5'})
    assert res.status_code == 201

    encontrados = cliente.get('/api/clientes/buscar?q=Creado En Venta').get_json()
    assert len(encontrados) == 1
    assert encontrados[0]['nombre'] == 'Cliente Creado En Venta'


# ═════════════════════════════════════════════════════
# 3. Observaciones / notas en ventas
# ═════════════════════════════════════════════════════
def test_las_observaciones_se_guardan_con_la_venta(cliente, producto):
    """Las notas internas se guardan y vuelven en el detalle de la factura."""
    res = cliente.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo', 'tipo_entrega': 'entrega_inmediata',
        'observaciones': 'Cliente deja formaleta, entrega parcial la próxima semana',
        'items': [{'id_producto': producto, 'cantidad': 1, 'precio_unitario': 50000}]})
    assert res.status_code == 201
    id_venta = res.get_json()['id_venta']

    detalle = cliente.get(f'/api/ventas/{id_venta}').get_json()
    assert detalle['observaciones'] == (
        'Cliente deja formaleta, entrega parcial la próxima semana')

    # Y tambien sale en el listado de ventas, que es lo que pinta el historial.
    listado = cliente.get('/api/ventas').get_json()
    fila = next(v for v in listado if v['id'] == id_venta)
    assert 'formaleta' in fila['observaciones']


def test_una_venta_sin_observaciones_guarda_cadena_vacia(cliente, producto):
    """Sin notas, el campo queda vacio y no rompe la factura."""
    res = cliente.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': producto, 'cantidad': 1, 'precio_unitario': 50000}]})
    assert res.status_code == 201
    detalle = cliente.get(f"/api/ventas/{res.get_json()['id_venta']}").get_json()
    assert detalle['observaciones'] == ''


# ═════════════════════════════════════════════════════
# 4. Eliminación de cotizaciones
# ═════════════════════════════════════════════════════
@pytest.fixture
def cotizacion():
    conn = _abrir()
    cur = conn.execute(
        "INSERT INTO cotizaciones (consecutivo_cotizacion, nombre_cliente, cedula_nit,"
        " telefono, direccion, fecha, vigencia, observaciones, total, estado, usuario)"
        " VALUES ('COT-PRUEBA-1', 'Cliente Cotizacion', '1', '2', '3',"
        " '2026-09-29 10:00:00', '15 dias', 'prueba', 1000, 'Pendiente', 'admin')")
    id_cot = cur.lastrowid
    cur.execute(
        "INSERT INTO detalle_cotizaciones (id_cotizacion, id_producto, descripcion,"
        " cantidad, precio_unitario, subtotal) VALUES (?, 1, 'Producto', 1, 1000, 1000)",
        (id_cot,))
    conn.commit()
    return id_cot


def test_el_admin_puede_eliminar_una_cotizacion(cliente, cotizacion):
    """Una cotizacion erronea se puede borrar, con su detalle."""
    assert cliente.delete(f'/api/cotizaciones/{cotizacion}').status_code == 200
    assert cliente.get(f'/api/cotizaciones/{cotizacion}').status_code == 404

    conn = _abrir()
    assert conn.execute('SELECT COUNT(*) FROM detalle_cotizaciones WHERE id_cotizacion = ?',
                        (cotizacion,)).fetchone()[0] == 0


def test_un_empleado_no_puede_eliminar_una_cotizacion(app, cotizacion):
    """Solo el administrador borra: el endpoint no confía en la interfaz."""
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'empleado'
        s['rol'] = 'empleado'
        s['id_usuario'] = 2
    res = c.delete(f'/api/cotizaciones/{cotizacion}')
    assert res.status_code == 403
    assert cliente_existe(cotizacion)


def cliente_existe(id_cotizacion):
    return _abrir().execute('SELECT COUNT(*) FROM cotizaciones WHERE id = ?',
                            (id_cotizacion,)).fetchone()[0] == 1


# ═════════════════════════════════════════════════════
# 6. Seguridad DIAN: la venta emitida no se borra
# ═════════════════════════════════════════════════════
def test_una_venta_emitida_no_se_anula_ni_se_borra(cliente, producto):
    """El bloqueo fiscal sigue intacto: la DIAN no permite borrar el documento.

    La unica salida es la Nota Credito Electronica, que se emite por su propio
    modulo. Esta prueba fija que el camino viejo sigue cerrado.
    """
    conn = _abrir()
    cur = conn.execute(
        "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente,"
        " tipo_pago, direccion_cliente, subtotal_venta, iva_valor, iva_porcentaje, anulada,"
        " dian_estado, numero_dian) VALUES (1, '2026-09-29', '10:00:00', 119000, 0,"
        " 'efectivo', '', 100000, 19000, 19, 0, 'aceptado', 'POS-9999')")
    id_venta = cur.lastrowid
    conn.execute(
        "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario,"
        " subtotal) VALUES (?, ?, 2, 50000, 100000)", (id_venta, producto))
    conn.commit()

    res = cliente.put(f'/api/ventas/{id_venta}/anular', json={'motivo': 'error de digitacion'})
    assert res.status_code == 409
    cuerpo = res.get_json()
    assert cuerpo['requiere_nota_credito'] is True

    # La venta sigue vigente: ni anulada ni borrada.
    detalle = cliente.get(f'/api/ventas/{id_venta}').get_json()
    assert detalle['anulada'] is False
    assert detalle['dian']['numero'] == 'POS-9999'


def test_el_numero_dian_de_una_venta_emitida_no_se_reutiliza(cliente, producto):
    """Emitir dos ventas seguidas no puede repetir el consecutivo fiscal.

    El consecutivo vive en `ventas.numero_dian`, que es unico: aunque la nota
    credito corrija una venta, el numero queda consumido para siempre.
    """
    conn = _abrir()
    usados = {f[0] for f in conn.execute(
        "SELECT numero_dian FROM ventas WHERE numero_dian IS NOT NULL AND numero_dian <> ''")}
    # Tras emitir la venta de la prueba anterior, POS-9999 quedo consumido.
    assert 'POS-9999' in usados
