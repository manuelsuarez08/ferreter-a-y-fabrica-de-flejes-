"""Emisión por Facturatech sin red: el cliente SOAP se reemplaza por un doble."""
import os
import sys
import tempfile

os.environ['FERRETERIA_DB'] = os.path.join(tempfile.mkdtemp(), 'ft.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402
from ferreteria.services import (  # noqa: E402
    dian_emision, dian_series, facturatech_emision as fe, facturatech_ws as ws)

ENV = {'FACTURATECH_URL': 'https://ws.facturatech.co/v2/demo',
       'FACTURATECH_USUARIO': 'u', 'FACTURATECH_PASSWORD': 'p'}


def _ok(**extra):
    base = {'codigo': '201', 'ok': True, 'mensaje': 'ok', 'transaccion': 'T1',
            'estado': '', 'datos': '', 'crudo': {}}
    return dict(base, **extra)


def _preparar():
    app = create_app()
    ctx = app.app_context()
    ctx.push()
    conn = get_db()
    conn.execute("DELETE FROM documentos_electronicos")
    conn.execute("DELETE FROM cola_dian")
    dian_series.actualizar_serie(conn, 'FV', {
        'prefijo': 'TCFA', 'consecutivo': 5, 'rango_desde': 1, 'rango_hasta': 99,
        'numero_resolucion': '123', 'fecha_inicio': '2019-01-01',
        'fecha_vencimiento': '2030-12-31'})
    conn.execute("UPDATE configuracion SET nit='901143311', digito_verificacion='8', "
                 "nombre='Emisor SAS', direccion='Calle 1', codigo_municipio='05001', "
                 "codigo_departamento='05', email_emisor='a@b.co' WHERE id=1")
    prod = conn.execute("SELECT id FROM productos LIMIT 1").fetchone()
    if not prod:
        conn.execute("INSERT INTO productos (nombre, precio_venta, stock) "
                     "VALUES ('Martillo', 10000, 10)")
        prod = conn.execute("SELECT id FROM productos LIMIT 1").fetchone()
    cur = conn.execute(
        "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente, "
        "tipo_pago, subtotal_venta, iva_valor, iva_porcentaje, anulada, "
        "tipo_documento_dian) VALUES (1, '2026-11-04', '10:00:00', 11900, 0, "
        "'efectivo', 10000, 1900, 19, 0, 'FV')")
    conn.execute("INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, "
                 "precio_unitario, subtotal) VALUES (?, ?, 1, 10000, 10000)",
                 (cur.lastrowid, prod[0]))
    conn.commit()
    return conn, cur.lastrowid


def test_emision_aceptada_y_idempotente():
    os.environ.update(ENV)
    conn, venta = _preparar()
    subidos = []
    ws.upload_document = lambda xml: subidos.append(xml) or _ok()
    ws.document_status = lambda t: _ok(estado='SIGNED_XML')
    ws.download_cufe = lambda p, f: _ok(datos='CUFE123')
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'aceptado' and res['cuide'] == 'CUFE123'
    assert res['numero'] == 'TCFA-5'
    xml = subidos[0].decode()
    assert '<ENC_6>TCFA5</ENC_6>' in xml and '<ICC_9>TCFA</ICC_9>' in xml
    fila = conn.execute("SELECT modo, track_id, cuide FROM documentos_electronicos"
                        ).fetchone()
    assert tuple(fila) == ('facturatech', 'T1', 'CUFE123')
    assert dian_emision.emitir_venta(conn, venta)['mensaje'].startswith('La venta ya')
    assert len(subidos) == 1


def test_rechazo_no_se_trata_como_contingencia():
    os.environ.update(ENV)
    conn, venta = _preparar()
    ws.upload_document = lambda xml: _ok(codigo='404', ok=False, mensaje='regla X',
                                         transaccion='')
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'rechazado' and 'regla X' in res['mensaje']
    assert conn.execute("SELECT COUNT(*) FROM cola_dian").fetchone()[0] == 0


def test_sin_red_pasa_a_contingencia_y_se_encola():
    os.environ.update(ENV)
    conn, venta = _preparar()

    def caido(xml):
        raise ws.ErrorFacturatech('sin red')
    ws.upload_document = caido
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'contingencia' and res['contingencia']
    assert conn.execute("SELECT COUNT(*) FROM cola_dian").fetchone()[0] == 1


def test_error_de_cuenta_va_a_contingencia():
    os.environ.update(ENV)
    conn, venta = _preparar()
    ws.upload_document = lambda xml: _ok(
        codigo='409', ok=False, mensaje='no está configurado como cliente webservice',
        transaccion='')
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'contingencia'
    assert conn.execute("SELECT COUNT(*) FROM cola_dian").fetchone()[0] == 1


def test_sin_variables_no_se_activa():
    for k in ENV:
        os.environ.pop(k, None)
    assert not fe.activo()


if __name__ == '__main__':
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for prueba in pruebas:
        prueba()
        print('ok', prueba.__name__)
    print(f'{len(pruebas)}/{len(pruebas)} pasaron')
