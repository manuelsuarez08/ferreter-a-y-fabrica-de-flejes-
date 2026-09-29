"""Pruebas del BLOQUEO FISCAL: una venta emitida no se puede tocar.

REGLAS DE LA DIAN que fijan estas pruebas:

  1. Una vez emitido el documento electrónico, la venta NO se puede modificar
     (precios, cantidades) ni anular marcando `anulada`. El documento firmado ya
     no coincidiría con la venta, y la DIAN rechaza esa inconsistencia.
  2. La única salida legal es la NOTA CRÉDITO ELECTRÓNICA, vinculada por CUIDE
     al documento original.
  3. Bloquear en el SERVIDOR es obligatorio: ocultar el botón en la interfaz no
     impide nada si alguien llama la API a mano.

Se prueba sobre una base temporal para no tocar los datos reales.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_bloqueo_fiscal.py -q
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
from ferreteria.services import dian_emision  # noqa: E402
import ferreteria.config as cfg  # noqa: E402


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
def venta(app):
    """Crea una venta de prueba con una línea y devuelve su id."""
    conn = sqlite3.connect(cfg.DB_NAME)
    id_prod = conn.execute('SELECT id FROM productos LIMIT 1').fetchone()[0]
    id_cli = conn.execute('SELECT id FROM clientes LIMIT 1').fetchone()[0]
    cur = conn.execute(
        "INSERT INTO ventas (fecha_dia, hora, total_venta, tipo_pago,"
        " id_cliente, subtotal_venta, iva_valor, iva_porcentaje, anulada,"
        " saldo_pendiente, direccion_cliente)"
        " VALUES ('2026-09-29','10:00:00',119000,'efectivo',?,100000,"
        "19000,19,0,0,'')",
        (id_cli,))
    id_venta = cur.lastrowid
    conn.execute(
        "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,"
        " precio_unitario, subtotal) VALUES (?,?,2,50000,100000)",
        (id_venta, id_prod))
    conn.commit()
    conn.close()
    return id_venta


def _emitir(cliente, id_venta):
    """Marca la venta como emitida, como si ya tuviera documento."""
    conn = sqlite3.connect(cfg.DB_NAME)
    conn.execute(
        "UPDATE ventas SET dian_estado='aceptado', numero_dian='POS-1',"
        " dian_cuide=? WHERE id=?",
        ('a' * 96, id_venta))
    conn.commit()
    conn.close()


# ── El helper de estado fiscal ───────────────────────────────────────────────
def test_venta_sin_documento_no_esta_emitida():
    conn = sqlite3.connect(cfg.DB_NAME)
    id_cli = conn.execute('SELECT id FROM clientes LIMIT 1').fetchone()[0]
    cur = conn.execute(
        "INSERT INTO ventas (fecha_dia, hora, total_venta, tipo_pago,"
        " id_cliente, saldo_pendiente)"
        " VALUES ('2026-09-29','10:00:00',100,'efectivo',?,0)",
        (id_cli,))
    id_venta = cur.lastrowid
    conn.commit()

    estado, numero, cuide = dian_emision.estado_fiscal_venta(conn, id_venta)
    conn.close()
    assert estado == 'sin_emitir'
    assert dian_emision.venta_emitida(estado) is False


@pytest.mark.parametrize('estado', [
    'aceptado', 'contingencia', 'rechazado', 'firmado', 'enviado', 'error',
])
def test_todo_estado_distinto_de_sin_emitir_cuenta_como_emitido(estado):
    """Aunque el documento este en contingencia o rechazado, ya se entrego al
    cliente con su CUIDE: la venta ya no se puede tocar."""
    assert dian_emision.venta_emitida(estado) is True


# ── Bloqueo de ANULACIÓN ─────────────────────────────────────────────────────
def test_no_se_puede_anular_una_venta_emitida(cliente, venta):
    _emitir(cliente, venta)
    r = cliente.put(f'/api/ventas/{venta}/anular', json={'motivo': 'error'})
    assert r.status_code == 409, 'la venta emitida se pudo anular'
    d = r.get_json()
    assert d.get('requiere_nota_credito') is True
    assert 'NOTA CRÉDITO' in d['error'].upper()
    assert 'POS-1' in d['error']


def test_anular_emitida_no_toca_la_base(cliente, venta):
    """El bloqueo debe ocurrir ANTES de modificar nada."""
    _emitir(cliente, venta)
    conn = sqlite3.connect(cfg.DB_NAME)
    stock_antes = conn.execute(
        'SELECT stock_actual FROM productos WHERE id ='
        ' (SELECT id_producto FROM detalle_ventas WHERE id_venta=?)',
        (venta,)).fetchone()[0]
    conn.close()

    cliente.put(f'/api/ventas/{venta}/anular', json={'motivo': 'error'})

    conn = sqlite3.connect(cfg.DB_NAME)
    fila = conn.execute(
        'SELECT anulada FROM ventas WHERE id=?', (venta,)).fetchone()
    stock_despues = conn.execute(
        'SELECT stock_actual FROM productos WHERE id ='
        ' (SELECT id_producto FROM detalle_ventas WHERE id_venta=?)',
        (venta,)).fetchone()[0]
    conn.close()
    assert fila[0] == 0, 'la venta quedó anulada'
    assert stock_despues == stock_antes, 'se movió el stock pese al bloqueo'


def test_una_venta_no_emitida_sigue_pudiendo_anularse(cliente, venta):
    """El bloqueo no debe romper el flujo normal del POS."""
    r = cliente.put(f'/api/ventas/{venta}/anular', json={'motivo': 'error'})
    assert r.status_code == 200, r.get_json()


# ── Bloqueo de EDICIÓN ───────────────────────────────────────────────────────
def test_no_se_pueden_editar_precios_de_una_venta_emitida(cliente, venta):
    _emitir(cliente, venta)
    conn = sqlite3.connect(cfg.DB_NAME)
    id_prod = conn.execute(
        'SELECT id_producto FROM detalle_ventas WHERE id_venta=?',
        (venta,)).fetchone()[0]
    conn.close()

    r = cliente.put(f'/api/ventas/{venta}/detalle', json={'items': [
        {'id_producto': id_prod, 'cantidad': 99, 'precio_unitario': 1},
    ]})
    assert r.status_code == 409, 'se pudo editar una venta emitida'
    assert r.get_json().get('requiere_nota_credito') is True


def test_no_se_pueden_cambiar_cantidades_de_una_venta_emitida(cliente, venta):
    _emitir(cliente, venta)
    conn = sqlite3.connect(cfg.DB_NAME)
    id_prod = conn.execute(
        'SELECT id_producto FROM detalle_ventas WHERE id_venta=?',
        (venta,)).fetchone()[0]
    conn.close()

    r = cliente.put(f'/api/ventas/{venta}/detalle', json={'items': [
        {'id_producto': id_prod, 'cantidad': 5, 'precio_unitario': 50000},
    ]})
    assert r.status_code == 409


def test_la_venta_no_emitida_sigue_siendo_editable(cliente, venta):
    conn = sqlite3.connect(cfg.DB_NAME)
    id_prod = conn.execute(
        'SELECT id_producto FROM detalle_ventas WHERE id_venta=?',
        (venta,)).fetchone()[0]
    conn.close()
    r = cliente.put(f'/api/ventas/{venta}/detalle', json={'items': [
        {'id_producto': id_prod, 'cantidad': 3, 'precio_unitario': 50000},
    ]})
    assert r.status_code == 200, r.get_json()


def test_no_se_pueden_cambiar_los_datos_facturados_del_cliente(cliente, venta):
    """Editar el NIT/direccion con los que se facturo tambien rompe el cuadre
    con el documento electronico."""
    _emitir(cliente, venta)
    r = cliente.put(f'/api/ventas/{venta}/cliente', json={
        'nombre': 'Otro', 'cedula_nit': '999', 'telefono': '1',
        'direccion': 'x', 'email': '', 'tipo_documento': 'CC',
    })
    assert r.status_code == 409, 'se pudieron cambiar los datos facturados'


# ── Redondeo fiscal ──────────────────────────────────────────────────────────
def test_el_redondeo_del_iva_es_desempate_al_alza():
    """La DIAN redondea siempre al alza; `round()` de Python hace banker's
    rounding (round(0.5)=0) y produce un centavo de diferencia."""
    from ferreteria.services.dian import redondear_pesos
    assert redondear_pesos(2.5) == 3
    assert redondear_pesos(0.5) == 1
    assert redondear_pesos(2.675) == 3
    # Casos donde el desempate a .5 difiere del banker's rounding nativo:
    assert redondear_pesos(1.5) == 2 and round(1.5) == 2
    assert redondear_pesos(2.5) == 3 and round(2.5) == 2
    assert redondear_pesos(3.5) == 4 and round(3.5) == 4
    # La serie que separa a los dos metodos de forma consistente:
    assert redondear_pesos(0.5) == 1 and round(0.5) == 0
    assert redondear_pesos(4.5) == 5 and round(4.5) == 4


def test_el_cuadre_de_totales_se_respeta(cliente, venta):
    """Tras una edición, el total debe seguir cuadrando con la tolerancia del
    anexo (±1 peso)."""
    conn = sqlite3.connect(cfg.DB_NAME)
    id_prod = conn.execute(
        'SELECT id_producto FROM detalle_ventas WHERE id_venta=?',
        (venta,)).fetchone()[0]
    conn.close()
    cliente.put(f'/api/ventas/{venta}/items', json={'items': [
        {'id_producto': id_prod, 'cantidad': 7, 'precio_unitario': 3333.33},
    ]})
    d = cliente.get(f'/api/ventas/{venta}').get_json()
    sub = d['subtotal_venta']
    iva = d['iva_valor']
    total = d['total_venta']
    assert abs((sub + iva) - total) <= 1
