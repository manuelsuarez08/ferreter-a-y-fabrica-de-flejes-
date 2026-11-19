"""Prueba del cliente SOAP de Facturatech (sin red).

Contrato fijado:
  1. Un password que ya es hash de 64 hex se envía tal cual (verificado contra
     el demo: así autentican las credenciales de Facturatech).
  2. Un password normal se envía como SHA-256 hex.
  3. El sobre SOAP escapa el contenido y usa el namespace del WSDL.
  4. Sin variables de entorno falla con un error claro, sin llamar a la red.
"""
import base64
import hashlib
import io
import os
import sys
import urllib.error
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ferreteria.services import facturatech_ws as ws  # noqa: E402


def test_hash_ya_calculado_se_respeta():
    h = 'ab' * 32
    assert ws.hash_password(h) == h
    assert ws.hash_password(h.upper()) == h


def test_hash_de_password_normal():
    assert ws.hash_password('clave') == hashlib.sha256(b'clave').hexdigest()


def test_sobre_soap():
    ns = 'urn:https://ws-pos.facturatech.co/v1/demo/'
    datos = ws._sobre('uploadDocument', ns, [
        ('username', 'xsd:string', 'u'), ('xmlBase64', 'xsd:string', 'a<b&c')])
    raiz = ET.fromstring(datos)
    cuerpo = raiz[0][0]
    assert cuerpo.tag == f'{{{ns}}}uploadDocument'
    assert [h.tag for h in cuerpo] == ['username', 'xmlBase64']
    assert cuerpo[1].text == 'a<b&c'


def test_envio_factura_base64_preserva_xml_utf8(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    monkeypatch.setenv('FACTURATECH_USUARIO', 'usuario-prueba')
    monkeypatch.setenv('FACTURATECH_PASSWORD', 'clave-prueba')
    original = '<?xml version="1.0" encoding="UTF-8"?><FACTURA><EMI>Niño &amp; Cía</EMI></FACTURA>'.encode('utf-8')
    capturado = {}

    def capturar(operacion, parametros):
        capturado['operacion'] = operacion
        capturado['sobre'] = ws._sobre(operacion, 'urn:prueba/', parametros)
        return {'code': '200'}

    monkeypatch.setattr(ws, 'llamar', capturar)
    assert ws.upload_document(original)['ok']
    cuerpo = ET.fromstring(capturado['sobre'])[0][0]
    valor = cuerpo.find('xmlBase64').text
    assert capturado['operacion'] == 'FtechAction.uploadInvoiceFile'
    assert base64.b64decode(valor, validate=True) == original
    assert '\n' not in valor and '\r' not in valor
    assert ET.fromstring(base64.b64decode(valor)).tag == 'FACTURA'


def test_url_wsdl_se_convierte_en_base_del_servicio(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro/index.php?wsdl')
    monkeypatch.setenv('FACTURATECH_USUARIO', 'usuario-prueba')
    monkeypatch.setenv('FACTURATECH_PASSWORD', 'clave-prueba')
    assert ws._config()[0] == 'https://ws.facturatech.co/v2/pro'


def test_http_403_no_se_confunde_con_error_de_xml(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    monkeypatch.setenv('FACTURATECH_USUARIO', 'usuario-prueba')
    monkeypatch.setenv('FACTURATECH_PASSWORD', 'clave-prueba')

    def bloqueado(peticion, timeout):
        raise urllib.error.HTTPError(peticion.full_url, 403, 'Forbidden', {}, io.BytesIO(b'<html>Bloqueado</html>'))

    monkeypatch.setattr(ws.urllib.request, 'urlopen', bloqueado)
    with pytest.raises(ws.ErrorFacturatech, match='HTTP 403'):
        ws.check_credits()


def test_http_403_soap_no_se_considera_rechazo_fiscal(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    monkeypatch.setenv('FACTURATECH_USUARIO', 'usuario-prueba')
    monkeypatch.setenv('FACTURATECH_PASSWORD', 'clave-prueba')
    contenido = (b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                 b'<soap:Body><response><code>403</code></response></soap:Body></soap:Envelope>')

    def bloqueado(peticion, timeout):
        raise urllib.error.HTTPError(peticion.full_url, 403, 'Forbidden', {}, io.BytesIO(contenido))

    monkeypatch.setattr(ws.urllib.request, 'urlopen', bloqueado)
    with pytest.raises(ws.ErrorFacturatech, match='HTTP 403'):
        ws.check_credits()


def test_html_con_http_200_no_es_respuesta_soap(monkeypatch):
    monkeypatch.setenv('FACTURATECH_URL', 'https://ws.facturatech.co/v2/pro')
    monkeypatch.setenv('FACTURATECH_USUARIO', 'usuario-prueba')
    monkeypatch.setenv('FACTURATECH_PASSWORD', 'clave-prueba')
    monkeypatch.setattr(ws.urllib.request, 'urlopen',
                        lambda peticion, timeout: io.BytesIO(b'<html>Acceso restringido</html>'))
    with pytest.raises(ws.ErrorFacturatech, match='SOAP'):
        ws.check_credits()


@pytest.mark.parametrize('url, operacion, base', [
    ('https://ws.facturatech.co/v2/pro/index.php?wsdl', 'FtechAction.uploadInvoiceFile',
     'https://ws.facturatech.co/v2/pro'),
    ('https://ws-pos.facturatech.co/v1/pro/index.php?wsdl', 'uploadDocument',
     'https://ws-pos.facturatech.co/v1/pro'),
])
def test_peticion_completa_usa_url_y_base64_correctos(monkeypatch, url, operacion, base):
    monkeypatch.setenv('FACTURATECH_URL', url)
    monkeypatch.setenv('FACTURATECH_USUARIO', 'usuario-prueba')
    monkeypatch.setenv('FACTURATECH_PASSWORD', 'clave-prueba')
    capturado = {}
    original = '<?xml version="1.0" encoding="UTF-8"?><FACTURA>Maíz &amp; piñón</FACTURA>'.encode('utf-8')

    def responder(peticion, timeout):
        capturado['peticion'] = peticion
        return io.BytesIO(b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                          b'<soap:Body><response><code>201</code></response></soap:Body></soap:Envelope>')

    monkeypatch.setattr(ws.urllib.request, 'urlopen', responder)
    assert ws.upload_document(original)['ok']
    peticion = capturado['peticion']
    assert peticion.full_url == base + '/index.php'
    assert peticion.get_header('User-agent') == 'FerreControl/1.0'
    cuerpo = ET.fromstring(peticion.data)[0][0]
    assert cuerpo.tag == '{urn:' + base + '/}' + operacion
    assert base64.b64decode(cuerpo.find('xmlBase64').text, validate=True) == original
    separador = '#' if '/v2/' in base else ''
    assert peticion.get_header('Soapaction') == 'urn:' + base + '/' + separador + operacion


def test_sin_configuracion():
    anteriores = {k: os.environ.pop(k, None) for k in (
        'FACTURATECH_URL', 'FACTURATECH_USUARIO', 'FACTURATECH_PASSWORD')}
    try:
        try:
            ws.check_credits()
        except ws.ErrorFacturatech:
            return
        raise AssertionError('debio fallar sin configuracion')
    finally:
        for k, v in anteriores.items():
            if v is not None:
                os.environ[k] = v


if __name__ == '__main__':
    pruebas = [f for n, f in sorted(globals().items()) if n.startswith('test_')]
    for prueba in pruebas:
        prueba()
        print('ok', prueba.__name__)
    print(f'{len(pruebas)}/{len(pruebas)} pasaron')
