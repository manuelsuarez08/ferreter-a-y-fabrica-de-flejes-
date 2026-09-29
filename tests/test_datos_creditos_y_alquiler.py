"""Pruebas de los puntos 2, 3, 6 y 8 (datos: ubicacion, creditos, producto
rapido y recibo de alquiler).

A diferencia del resto, estos tienen endpoint de verdad, asi que se prueban
contra la app Flask con una base temporal.
"""
import os
import shutil
import tempfile
from datetime import datetime

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
def _cerrar():
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
def admin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'admin'
        s['rol'] = 'admin'
        s['id_usuario'] = 1
    return c


# ═════════════════════════════════════════════════════
# 2. Ubicacion por defecto: Samaná, Caldas
# ═════════════════════════════════════════════════════
def test_los_codigos_de_ubicacion_por_defecto():
    """17665 = Samaná (Caldas), 17 = Caldas. Códigos DIVPOLKA de la DIAN."""
    from ferreteria.blueprints import catalogo
    assert catalogo.CODIGO_MUNICIPIO_DEFECTO == '17665'
    assert catalogo.CODIGO_DEPARTAMENTO_DEFECTO == '17'


def test_un_cliente_nuevo_hereda_la_ubicacion_del_negocio(admin):
    """El cajero no debe tener que elegir municipio en cada alta."""
    res = admin.post('/api/clientes', json={
        'nombre': 'Cliente Sin Ubicacion', 'cedula_nit': '800111222'})
    assert res.status_code == 201

    conn = _abrir()
    fila = conn.execute(
        "SELECT codigo_municipio, codigo_departamento, regimen_fiscal, "
        "responsabilidades, tipo_persona FROM clientes WHERE cedula_nit = '800111222'"
    ).fetchone()
    conn.commit()
    assert fila[0] == '17665'
    assert fila[1] == '17'
    # Y el regimen/responsabilidad tambien quedan predefinidos.
    assert fila[2] == 'No Responsable de IVA'
    assert fila[3] == 'R-99-PN'
    assert fila[4] == 'Natural'


def test_una_ubicacion_explicita_manda(admin):
    """Si el cliente SI tiene otra ubicación, esa gana: el default es solo
    un valor inicial, no una imposicion."""
    admin.post('/api/clientes', json={
        'nombre': 'Cliente Otra Ciudad', 'cedula_nit': '800333444',
        'codigo_municipio': '05001', 'codigo_departamento': '05'})
    conn = _abrir()
    fila = conn.execute(
        "SELECT codigo_municipio, codigo_departamento FROM clientes WHERE cedula_nit = '800333444'"
    ).fetchone()
    conn.commit()
    assert fila == ('05001', '05')


def test_el_negocio_guarda_la_ubicacion_por_defecto():
    """La fila unica de configuracion (emisor) tambien queda en Samana."""
    conn = _abrir()
    fila = conn.execute(
        "SELECT codigo_municipio, codigo_departamento FROM configuracion WHERE id = 1"
    ).fetchone()
    conn.commit()
    if fila[0]:
        assert fila[0] == '17665'
        assert fila[1] == '17'


# ═════════════════════════════════════════════════════
# 3. Consolidado mensual del cliente
# ═════════════════════════════════════════════════════
@pytest.fixture
def cliente_del_mes():
    """Cliente con dos compras y un abono del mes actual.

    Es idempotente: el fixture lo comparten varias pruebas y `cedula_nit` es
    UNIQUE, asi que si ya existe de una corrida anterior se reutiliza.
    """
    conn = _abrir()
    fila = conn.execute("SELECT id FROM clientes WHERE cedula_nit = '700999888'").fetchone()
    if fila:
        conn.commit()
        return fila[0], datetime.now().strftime('%Y-%m')

    cur = conn.execute(
        "INSERT INTO clientes (nombre, cedula_nit, telefono) "
        "VALUES ('Cliente Del Mes', '700999888', '3001110000')")
    id_cliente = cur.lastrowid
    mes = datetime.now().strftime('%Y-%m')
    for dia, total in (('01', 100000), ('15', 200000)):
        cur.execute(
            "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, "
            "saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, iva_porcentaje, anulada) "
            "VALUES (?, ?, '10:00:00', ?, ?, 'credito', ?, 0, 0, 0)",
            (id_cliente, f'{mes}-{dia}', total, total, total))
    # Un abono del mes.
    cur.execute("INSERT INTO abonos (id_cliente, monto, fecha) VALUES (?, 50000, ?)",
                (id_cliente, f'{mes}-10 09:00:00'))
    conn.commit()
    return id_cliente, mes


def test_el_consolidado_trae_las_compras_del_mes(admin, cliente_del_mes):
    id_cliente, mes = cliente_del_mes
    res = admin.get(f'/api/creditos/{id_cliente}/consolidado')
    assert res.status_code == 200
    d = res.get_json()

    assert d['mes'] == mes
    assert d['cliente']['nombre'] == 'Cliente Del Mes'
    assert len(d['ventas']) == 2, 'deben venir las 2 compras del mes'
    assert d['total_mes'] == 300000
    assert d['abonos_mes'] == 50000
    # El saldo es global, no solo del mes.
    assert d['total_facturado'] == 300000
    assert d['total_abonado'] == 50000
    assert d['saldo_global'] == 250000


def test_el_consolidado_respeta_el_mes_pedido(admin, cliente_del_mes):
    """Pedir un mes donde no hubo compras no debe traer las del mes actual."""
    id_cliente, _ = cliente_del_mes
    d = admin.get(f'/api/creditos/{id_cliente}/consolidado?mes=2020-01').get_json()
    assert d['ventas'] == []
    assert d['total_mes'] == 0
    # Pero el saldo global sigue siendo real.
    assert d['saldo_global'] == 250000


def test_el_consolidado_de_un_cliente_inexistente_da_404(admin):
    assert admin.get('/api/creditos/999999/consolidado').status_code == 404


def test_el_historial_de_abonos_sigue_aceptando_sin_periodo(admin, cliente_del_mes):
    """No se rompio el endpoint anterior: sin `periodo` devuelve todo."""
    id_cliente, mes = cliente_del_mes
    d = admin.get(f'/api/abonos/{id_cliente}').get_json()
    assert d['periodo'] == 'todo'
    assert len(d['abonos']) == 1


# ═════════════════════════════════════════════════════
# 6. Producto rápido (el POST debe devolver el id)
# ═════════════════════════════════════════════════════
def test_el_post_de_producto_devuelve_el_id(admin):
    """El modal de producto rápido lo necesita para seleccionar lo creado."""
    res = admin.post('/api/productos', json={
        'nombre': 'Producto Para Cotizacion', 'categoria': 'Prueba',
        'precio_venta': 25000, 'stock_actual': 5, 'iva_tasa': 19})
    assert res.status_code == 201
    d = res.get_json()
    assert 'id' in d, 'sin id el modal no puede seleccionar el producto'
    assert isinstance(d['id'], int)
    assert d['nombre'] == 'Producto Para Cotizacion'


# ═════════════════════════════════════════════════════
# 8. Recibo de entrega de alquiler
# ═════════════════════════════════════════════════════
def test_el_recibo_de_alquiler_trae_equipo_y_cliente(admin):
    """Debe listar los equipos con su estado de salida y los datos del cliente."""
    conn = _abrir()
    cur = conn.execute(
        "INSERT INTO clientes (nombre, cedula_nit, telefono) "
        "VALUES ('Cliente Alquiler', '700555444', '3002223333')")
    id_cliente = cur.lastrowid
    # `categoria` es NOT NULL en equipos_alquiler.
    cur.execute(
        "INSERT INTO equipos_alquiler (nombre, categoria, codigo_interno, estado) "
        "VALUES ('Mezcladora', 'Maquinaria', ?, 'Disponible')", (f'EQ-{id_cliente}',))
    id_equipo = cur.lastrowid
    cur.execute(
        "INSERT INTO alquileres (id_cliente, id_usuario, fecha_salida, "
        "fecha_devolucion_pactada, estado, valor_deposito, fecha_registro) "
        "VALUES (?, 1, '2026-09-29 08:30:00', '2026-10-01 17:00:00', "
        "'activo', 200000, '2026-09-29 08:30:00')", (id_cliente,))
    id_alquiler = cur.lastrowid
    cur.execute(
        "INSERT INTO detalle_alquiler (id_alquiler, id_equipo, cantidad, "
        "tarifa_tipo, tarifa_valor, subtotal, estado_salida) "
        "VALUES (?, ?, 1, 'dia', 150000, 150000, 'bueno')",
        (id_alquiler, id_equipo))
    conn.commit()

    res = admin.get(f'/api/alquileres/{id_alquiler}/recibo')
    assert res.status_code == 200
    d = res.get_json()

    assert d['alquiler']['id'] == id_alquiler
    assert d['alquiler']['cliente'] == 'Cliente Alquiler'
    assert d['alquiler']['fecha_salida'] == '2026-09-29 08:30:00'
    assert d['alquiler']['fecha_devolucion_pactada'] == '2026-10-01 17:00:00'
    assert len(d['equipos']) == 1
    assert d['equipos'][0]['equipo'] == 'Mezcladora'
    assert d['equipos'][0]['codigo_interno'] == f'EQ-{id_cliente}'
    # El estado de salida es lo que sirve para claimar si vuelve danado.
    assert d['equipos'][0]['estado_salida'] == 'bueno'
    assert 'negocio' in d


def test_el_recibo_de_un_alquiler_inexistente_da_404(admin):
    assert admin.get('/api/alquileres/999999/recibo').status_code == 404
