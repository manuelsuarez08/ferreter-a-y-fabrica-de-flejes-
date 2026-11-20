"""Emisión por Facturatech sin red: el cliente SOAP se reemplaza por un doble."""
import os
import sqlite3
import sys
import tempfile

import pytest

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


def test_ambiente_facturatech_es_configurable_y_debe_coincidir_con_url(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/demo')
    monkeypatch.setenv('FACTURATECH_AMBIENTE', '2')
    assert fe._ambiente() == '2'

    monkeypatch.setenv('FACTURATECH_AMBIENTE', '1')
    with pytest.raises(dian_emision.ErrorEmision, match='no coincide'):
        fe._ambiente()


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
    conn.execute("UPDATE clientes SET nombre='Cliente Prueba', cedula_nit='123456789', "
                 "tipo_documento='CC', direccion='Carrera 10 #20-30', "
                 "codigo_municipio='05001', codigo_departamento='05', "
                 "telefono='3001234567', email='' WHERE id=1")
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


def test_emision_aceptada_y_idempotente(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()
    subidos = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: subidos.append(xml) or _ok())
    monkeypatch.setattr(ws, 'document_status', lambda transaccion: _ok(estado='SIGNED_XML'))
    monkeypatch.setattr(ws, 'download_cufe', lambda prefijo, folio: _ok(datos='CUFE123'))
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


def test_rechazo_no_se_trata_como_contingencia(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()
    monkeypatch.setattr(ws, 'upload_document', lambda xml: _ok(
        codigo='404', ok=False, mensaje='regla X', transaccion=''))
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'rechazado' and 'regla X' in res['mensaje']
    assert conn.execute("SELECT COUNT(*) FROM cola_dian").fetchone()[0] == 0


def test_rechazo_conserva_transaccion_para_consultar_detalle(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()
    monkeypatch.setattr(ws, 'upload_document', lambda xml: _ok(
        codigo='422', ok=False, mensaje='Comprobante rechazado',
        transaccion='TX-RECHAZO'))
    resultado = fe.emitir_venta(conn, venta)
    assert resultado['estado'] == 'rechazado'
    assert resultado['track_id'] == 'TX-RECHAZO'
    id_documento = resultado['id_documento']
    assert conn.execute('SELECT track_id FROM documentos_electronicos WHERE id = ?',
                        (id_documento,)).fetchone()[0] == 'TX-RECHAZO'

    monkeypatch.setattr(ws, 'document_status', lambda transaccion: _ok(
        codigo='422', ok=False, mensaje='Detalle DIAN: DFA_8 inválido'))
    consulta = fe.consultar_estado(conn, id_documento)
    assert 'DFA_8 inválido' in consulta['mensaje']


def test_factura_facturatech_rechazada_regenera_xml_al_emitir_de_nuevo(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setattr(fe, 'ESPERA_FIRMA_SEGUNDOS', (0,))
    conn, venta = _preparar()
    cursor = conn.cursor()
    id_anterior = dian_emision._guardar_documento(cursor, {
        'id_venta': venta, 'tipo': 'FV', 'prefijo': 'TCFA', 'numero': 'TCFA-5',
        'cuide': '', 'fecha': '2026-11-04', 'hora': '10:00:00', 'total': 11900,
        'iva': 1900, 'inc': 0, 'xml': '<FACTURA><ENC><ENC_20>2</ENC_20></ENC><EMI><EMI_13/><EMI_19/></EMI></FACTURA>',
        'xml_firmado': '<FACTURA><ENC><ENC_20>2</ENC_20></ENC><EMI><EMI_13/><EMI_19/></EMI></FACTURA>',
        'qr': '', 'modo': 'facturatech', 'estado': 'rechazado', 'contingencia': 0,
    })
    conn.commit()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml) or _ok())
    monkeypatch.setattr(ws, 'document_status', lambda transaccion: _ok(estado='SIGNED_XML'))
    monkeypatch.setattr(ws, 'download_cufe', lambda prefijo, folio: _ok(datos='CUFE-REINTENTO'))
    try:
        resultado = fe.emitir_venta(conn, venta)
        assert resultado['estado'] == 'aceptado'
        assert resultado['id_documento'] == id_anterior
        assert resultado['numero'] == 'TCFA-5'
        xml = __import__('xml.etree.ElementTree', fromlist=['']).fromstring(enviados[0])
        assert xml.findtext('EMI/EMI_13') == 'Medellín'
        assert xml.findtext('EMI/EMI_19') == 'Antioquia'
        assert conn.execute('SELECT COUNT(*) FROM documentos_electronicos WHERE id_venta = ?',
                            (venta,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_sin_red_pasa_a_contingencia_y_se_encola(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()

    def caido(xml):
        raise ws.ErrorFacturatech('sin red')
    monkeypatch.setattr(ws, 'upload_document', caido)
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'contingencia' and res['contingencia']
    assert conn.execute("SELECT COUNT(*) FROM cola_dian").fetchone()[0] == 1


def test_error_de_cuenta_va_a_contingencia(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()
    monkeypatch.setattr(ws, 'upload_document', lambda xml: _ok(
        codigo='409', ok=False, mensaje='no está configurado como cliente webservice',
        transaccion=''))
    res = dian_emision.emitir_venta(conn, venta)
    assert res['estado'] == 'contingencia'
    assert conn.execute("SELECT COUNT(*) FROM cola_dian").fetchone()[0] == 1


def test_bloqueo_http_no_se_marca_como_rechazo_fiscal(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()

    def bloqueado(xml):
        raise ws.ErrorFacturatech('Facturatech respondió HTTP 403; revise el acceso al servicio.')

    monkeypatch.setattr(ws, 'upload_document', bloqueado)
    try:
        resultado = dian_emision.emitir_venta(conn, venta)
        assert resultado['estado'] == 'contingencia'
        assert conn.execute("SELECT COUNT(*) FROM cola_dian WHERE estado = 'pendiente'").fetchone()[0] == 1
    finally:
        conn.close()


def test_sin_variables_no_se_activa(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert not fe.activo()


@pytest.mark.parametrize('transaccion', [None, 'T-DEMO'])
def test_no_reenvia_documento_demo_al_cambiar_a_produccion(monkeypatch, transaccion):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE documentos_electronicos '
                 '(id INTEGER, id_venta INTEGER, numero TEXT, prefijo TEXT, xml TEXT, '
                 'track_id TEXT, estado TEXT, contingencia INTEGER)')
    conn.execute('INSERT INTO documentos_electronicos VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (1, 1, 'TEST-1', 'TEST', '<FACTURA><ENC><ENC_20>2</ENC_20></ENC></FACTURA>',
                  transaccion, 'contingencia', 1))
    llamadas = []
    monkeypatch.setattr(ws, 'upload_document', lambda *args: llamadas.append('upload'))
    monkeypatch.setattr(ws, 'document_status', lambda *args: llamadas.append('status'))
    try:
        with pytest.raises(dian_emision.ErrorEmision, match='ambiente'):
            fe.reenviar_documento(conn, 1)
        assert llamadas == []
    finally:
        conn.close()


@pytest.mark.parametrize('xml', ['', '<FACTURA>', '<FACTURA><ENC/></FACTURA>',
                                 '<FACTURA><ENC><ENC_20>3</ENC_20></ENC></FACTURA>'])
def test_documento_sin_ambiente_verificable_no_se_reenvia(monkeypatch, xml):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    with pytest.raises(dian_emision.ErrorEmision, match='ambiente'):
        fe._verificar_ambiente_documento(xml)


def test_documento_del_mismo_ambiente_puede_reintentarse(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    assert fe._verificar_ambiente_documento(
        '<FACTURA><ENC><ENC_20>1</ENC_20></ENC></FACTURA>') is None


@pytest.mark.parametrize('cambios', [
    {'activa': False}, {'numero_resolucion': ''}, {'fecha_inicio': ''},
    {'fecha_vencimiento': '2001-01-01'}, {'rango_hasta': 0},
    {'consecutivo': 0}, {'consecutivo': 100},
])
def test_serie_produccion_invalida_se_bloquea(monkeypatch, cambios):
    monkeypatch.delenv('FACTURATECH_RESOLUCION_INICIO', raising=False)
    serie = {'activa': True, 'prefijo': 'FV', 'numero_resolucion': '123',
             'fecha_inicio': '2000-01-01', 'fecha_vencimiento': '2099-12-31',
             'rango_desde': 1, 'rango_hasta': 99, 'consecutivo': 5}
    serie.update(cambios)
    with pytest.raises(dian_emision.ErrorEmision):
        fe._validar_serie_produccion(serie)


def test_serie_produccion_vigente_es_valida():
    serie = {'activa': True, 'prefijo': 'FV', 'numero_resolucion': '123',
             'fecha_inicio': '2000-01-01', 'fecha_vencimiento': '2099-12-31',
             'rango_desde': 1, 'rango_hasta': 99, 'consecutivo': 5}
    assert fe._validar_serie_produccion(serie) is None


def test_produccion_sin_serie_activa_no_reserva_ni_envia(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    conn, venta = _preparar()
    llamadas = []
    monkeypatch.setattr(ws, 'upload_document', lambda *args: llamadas.append('upload'))
    try:
        dian_series.actualizar_serie(conn, 'FV', {'activa': False})
        conn.commit()
        anterior = dian_series.leer_serie(conn, 'FV')['consecutivo']
        with pytest.raises(dian_emision.ErrorEmision, match='activa'):
            fe.emitir_venta(conn, venta)
        assert dian_series.leer_serie(conn, 'FV')['consecutivo'] == anterior
        assert conn.execute('SELECT COUNT(*) FROM documentos_electronicos').fetchone()[0] == 0
        assert llamadas == []
    finally:
        conn.close()


def test_produccion_con_serie_valida_envia_ambiente_productivo_simulado(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    monkeypatch.setattr(fe, 'ESPERA_FIRMA_SEGUNDOS', (0,))
    conn, venta = _preparar()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml) or _ok())
    monkeypatch.setattr(ws, 'document_status', lambda transaccion: _ok(estado='SIGNED_XML'))
    monkeypatch.setattr(ws, 'download_cufe', lambda prefijo, folio: _ok(datos='CUFE-PRUEBA'))
    try:
        dian_series.actualizar_serie(conn, 'FV', {'activa': True})
        conn.commit()
        resultado = fe.emitir_venta(conn, venta)
        assert resultado['estado'] == 'aceptado'
        assert b'<ENC_20>1</ENC_20>' in enviados[0]
    finally:
        conn.close()


def test_factura_productiva_respeta_persona_natural_del_emisor(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    monkeypatch.setattr(fe, 'ESPERA_FIRMA_SEGUNDOS', (0,))
    conn, venta = _preparar()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml) or _ok())
    monkeypatch.setattr(ws, 'document_status', lambda transaccion: _ok(estado='SIGNED_XML'))
    monkeypatch.setattr(ws, 'download_cufe', lambda prefijo, folio: _ok(datos='CUFE-PRUEBA'))
    try:
        conn.execute("UPDATE configuracion SET dian_tipo_persona = '2' WHERE id = 1")
        dian_series.actualizar_serie(conn, 'FV', {'activa': True})
        conn.commit()
        fe.emitir_venta(conn, venta)
        assert b'<EMI_1>2</EMI_1>' in enviados[0]
    finally:
        conn.close()


def test_configuracion_tipo_persona_emisor():
    conn, _ = _preparar()
    conn.close()
    aplicacion = create_app(inicializar_db=False, iniciar_hilo_dian=False)
    cliente = aplicacion.test_client()
    with cliente.session_transaction() as sesion:
        sesion['usuario'] = 'admin'
        sesion['rol'] = 'admin'
    assert cliente.put('/api/dian/configuracion', json={'tipo_persona_emisor': '2'}).status_code == 200
    assert cliente.get('/api/dian/configuracion').get_json()['tipo_persona_emisor'] == '2'
    assert cliente.put('/api/dian/configuracion', json={'tipo_persona_emisor': '3'}).status_code == 400


def test_servicio_productivo_de_facturas_no_envia_pos(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    conn, venta = _preparar()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml))
    try:
        conn.execute("UPDATE ventas SET tipo_documento_dian = 'POS' WHERE id = ?", (venta,))
        conn.commit()
        anterior = dian_series.leer_serie(conn, 'POS')['consecutivo']
        with pytest.raises(dian_emision.ErrorEmision, match='POS'):
            fe.emitir_venta(conn, venta)
        assert dian_series.leer_serie(conn, 'POS')['consecutivo'] == anterior
        assert enviados == []
    finally:
        conn.close()


@pytest.mark.parametrize('ambiente', ['demo', 'pro'])
def test_servicio_pos_no_envia_factura_electronica(monkeypatch, ambiente):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', f'https://ws-pos.facturatech.co/v1/{ambiente}')
    monkeypatch.delenv('FACTURATECH_POS_FACTURA', raising=False)
    conn, venta = _preparar()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml))
    try:
        anterior = dian_series.leer_serie(conn, 'FV')['consecutivo']
        with pytest.raises(dian_emision.ErrorEmision, match='factura electrónica'):
            fe.emitir_venta(conn, venta)
        assert dian_series.leer_serie(conn, 'FV')['consecutivo'] == anterior
        assert enviados == []
    finally:
        conn.close()


def test_factura_lag_por_ws_pos_habilitado_explicitamente(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws-pos.facturatech.co/v1/pro/index.php?wsdl')
    monkeypatch.setenv('FACTURATECH_POS_FACTURA', '1')
    monkeypatch.setattr(fe, 'ESPERA_FIRMA_SEGUNDOS', (0,))
    conn, venta = _preparar()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml) or _ok())
    monkeypatch.setattr(ws, 'document_status', lambda transaccion: _ok(estado='SIGNED_XML'))
    monkeypatch.setattr(ws, 'download_cufe', lambda prefijo, folio: _ok(datos='CUFE-PRUEBA'))
    try:
        conn.execute("UPDATE configuracion SET dian_tipo_persona = '2' WHERE id = 1")
        conn.execute('UPDATE detalle_ventas SET precio_unitario = 11900, subtotal = 11900 WHERE id_venta = ?', (venta,))
        dian_series.actualizar_serie(conn, 'FV', {
            'prefijo': 'LAG', 'consecutivo': 1, 'activa': True,
            'numero_resolucion': '123', 'fecha_inicio': '2000-01-01',
            'fecha_vencimiento': '2099-12-31', 'rango_desde': 1, 'rango_hasta': 50000,
        })
        conn.commit()
        resultado = fe.emitir_venta(conn, venta)
        assert resultado['numero'] == 'LAG-1'
        assert resultado['estado'] == 'aceptado'
        assert b'<ENC_1>INVOIC</ENC_1>' in enviados[0]
        assert b'<ENC_9>01</ENC_9>' in enviados[0]
        assert b'<ENC_20>1</ENC_20>' in enviados[0]
        assert b'<ENC_7>' not in enviados[0]
        assert b'<ENC_8>' not in enviados[0]
        assert b'<ENC_16>' not in enviados[0]
        assert b'<IPV>' in enviados[0]
        assert b'<IPV_5>1</IPV_5>' in enviados[0]
        assert b'<EMI_1>2</EMI_1>' in enviados[0]
        assert b'<DRF_4>LAG</DRF_4>' in enviados[0]
        assert dian_series.leer_serie(conn, 'FV')['consecutivo'] == 2
    finally:
        conn.close()


def test_factura_ws_pos_sigue_exigiendo_serie_activa(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws-pos.facturatech.co/v1/pro')
    monkeypatch.setenv('FACTURATECH_POS_FACTURA', '1')
    conn, venta = _preparar()
    enviados = []
    monkeypatch.setattr(ws, 'upload_document', lambda xml: enviados.append(xml))
    try:
        dian_series.actualizar_serie(conn, 'FV', {'activa': False})
        conn.commit()
        anterior = dian_series.leer_serie(conn, 'FV')['consecutivo']
        with pytest.raises(dian_emision.ErrorEmision, match='activa'):
            fe.emitir_venta(conn, venta)
        assert dian_series.leer_serie(conn, 'FV')['consecutivo'] == anterior
        assert enviados == []
    finally:
        conn.close()


def test_reemision_forzada_no_convierte_documento_demo_a_produccion(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    conn, venta = _preparar()
    try:
        resultado = fe.emitir_venta(conn, venta, contingencia=True)
        original = conn.execute('SELECT xml FROM documentos_electronicos WHERE id = ?',
                                (resultado['id_documento'],)).fetchone()[0]
        monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
        with pytest.raises(dian_emision.ErrorEmision, match='ambiente'):
            fe.emitir_venta(conn, venta, forzar=True)
        assert conn.execute('SELECT xml FROM documentos_electronicos WHERE id = ?',
                            (resultado['id_documento'],)).fetchone()[0] == original
    finally:
        conn.close()


def test_estado_muestra_ambiente_efectivo_de_facturatech(monkeypatch):
    for clave, valor in ENV.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    conn, _ = _preparar()
    try:
        conn.execute("UPDATE configuracion SET dian_ambiente = '2' WHERE id = 1")
        estado = dian_emision.estado_dian(conn)
        assert estado['ambiente'] == '2'
        assert estado['proveedor_emision'] == 'Facturatech'
        assert estado['ambiente_efectivo'] == '1'
        assert estado['ambiente_efectivo_nombre'] == 'Producción'
    finally:
        conn.close()


if __name__ == '__main__':
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for prueba in pruebas:
        prueba()
        print('ok', prueba.__name__)
    print(f'{len(pruebas)}/{len(pruebas)} pasaron')
