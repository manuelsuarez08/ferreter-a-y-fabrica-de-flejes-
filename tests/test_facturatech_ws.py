"""Prueba del cliente SOAP de Facturatech (sin red).

Contrato fijado:
  1. Un password que ya es hash de 64 hex se envía tal cual (verificado contra
     el demo: así autentican las credenciales de Facturatech).
  2. Un password normal se envía como SHA-256 hex.
  3. El sobre SOAP escapa el contenido y usa el namespace del WSDL.
  4. Sin variables de entorno falla con un error claro, sin llamar a la red.
"""
import hashlib
import os
import sys
import xml.etree.ElementTree as ET

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
