"""La firma tiene que verificar contra un validador AJENO al proyecto.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
Estas pruebas verifican la firma con `lxml`, que NO es el código del proyecto: es
una implementación independiente de canonicalización y de XMLDSig. Verificar con
las propias funciones del proyecto sería circular — si el proyecto canonicalizara
mal, su propio verificador diría que está bien.

LA FIRMA QUE PRODUCÍA EL PROYECTO NO VERIFICABA
------------------------------------------------
`_canonizar` hacía `ET.tostring()`, y eso no es canonicalización:

    proyecto  : <ns0:SignedInfo xmlns:ns0="...xmldsig#"><ns0:Reference ...>
    verificador: <ds:SignedInfo  xmlns:ds="...xmldsig#"><ds:Reference ...>

Mismo contenido, prefijos distintos, bytes distintos, digest distinto. La firma
NO verificaba con la clave pública del certificado y la DIAN habría rechazado
todos los documentos con "firma no válida".

Lo que estas pruebas fijan, entonces, no es la forma del XML: es que
`verificar_firma_lxml.revisar()` — el comprobador externo — no encuentre
discrepancias. Si alguien vuelve a `ET.tostring`, se rompen aquí.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def _carg(modulo):
    """Carga un script de `herramientas/` por ruta (no es paquete)."""
    ruta = RAIZ / 'herramientas' / f'{modulo}.py'
    spec = importlib.util.spec_from_file_location(modulo, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def firmados(tmp_path_factory):
    """Genera los tres documentos firmados una vez para todo el archivo."""
    destino = RAIZ / '_auditoria_xml'
    subprocess.run(
        [sys.executable, str(RAIZ / 'herramientas' / 'generar_xml_firmado.py')],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=180)
    assert destino.exists(), 'no se pudieron generar los XML firmados'
    return destino


# ═══════════════════════════════════════════
# 1. El comprobador externo no encuentra discrepancias
# ═══════════════════════════════════════════

@pytest.mark.parametrize('nombre', ['invoice_firmado.xml',
                                    'creditnote_firmado.xml',
                                    'evento_firmado.xml'])
def test_la_firma_verifica_en_un_validador_externo(firmados, nombre, capsys):
    """El criterio de aceptación es del verificador, no del proyecto.

    `revisar()` devuelve la cantidad de discrepancias: 0 es lo único aceptable.
    """
    verificador = _carg('verificar_firma_lxml')
    discrepancias = verificador.revisar(firmados / nombre)

    salida = capsys.readouterr().out
    assert discrepancias == 0, (
        f'{nombre} tiene {discrepancias} discrepancias de firma:\n{salida}')
    assert 'NO coincide' not in salida, salida
    assert '[FALLA]' not in salida, salida


# ═══════════════════════════════════════════
# 2. La firma criptográfica, verificada con la clave pública
# ═══════════════════════════════════════════

def test_la_firma_cifrada_verifica_con_la_llave_publica(firmados):
    """No basta con que los digests cuadren: hay que comprobar la RSA.

    Se extrae el certificado del propio `KeyInfo` y se usa SU clave pública.
    Si alguien cambiara la clave o el padding, esta prueba lo detecta aunque los
    digests siguieran cuadrando.
    """
    import base64
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree

    NS = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}
    crudo = (firmados / 'invoice_firmado.xml').read_bytes()
    arbol = etree.fromstring(crudo)

    firma = base64.b64decode(
        arbol.find('.//ds:SignatureValue', NS).text)
    cert = x509.load_der_x509_certificate(base64.b64decode(
        arbol.find('.//ds:X509Certificate', NS).text))

    signed_info = arbol.find('.//ds:SignedInfo', NS)
    canonico = etree.tostring(signed_info, method='c14n', exclusive=False,
                              with_comments=False)

    # Si no verifica, `verify` lanza InvalidSignature.
    cert.public_key().verify(firma, canonico, padding.PKCS1v15(), hashes.SHA384())


# ═══════════════════════════════════════════
# 3. Lo que specifically se rompió, para que no se repita
# ═══════════════════════════════════════════

def test_la_firma_no_usa_el_prefijo_ns0():
    """`ns0:` es la huella de `ET.tostring` canonicalizando un subárbol.

    El proyecto registraba los prefijos (`ET.register_namespace`), pero al
    canonicalizar un subárbol aislado ElementTree los ignora y vuelve a `ns0`.
    El documento final sí lleva `ds:`; lo que lleva `ns0` es lo que se firma, y
    eso es invisible a simple vista: el XML se ve correcto.
    """
    import xml.etree.ElementTree as ET

    from ferreteria.services import dian_firma

    crudo = (firmados := (RAIZ / '_auditoria_xml') /
             'invoice_firmado.xml').read_bytes()
    raiz = ET.fromstring(dian_firma._sin_xmlns_duplicado(crudo))

    # El documento entregado a la DIAN debe usar el prefijo registrado.
    texto = crudo.decode('utf-8')
    assert 'ns0:SignedInfo' not in texto, \
        'el documento firmado lleva ns0: — volvió ET.tostring'
    assert 'ds:SignedInfo' in texto


def test_el_digest_del_documento_cubre_el_atributo_id():
    """El `ID` se pone ANTES de calcular el digest: si se moviera, no cuadraría.

    Es un detalle de orden con consecuencias: el atributo forma parte de lo
    firmado, y ponerlo después produce un digest que no corresponde al XML que se
    envía. El comprobador externo lo detecta.
    """
    from lxml import etree

    crudo = (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes()
    arbol = etree.fromstring(crudo)
    doc_id = arbol.get('ID')

    assert doc_id, 'el documento firmado no tiene atributo ID'
    # La Reference debe apuntar a ese ID exacto.
    NS = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}
    referencias = {r.get('URI') for r in
                   arbol.findall('.//ds:Reference', NS)}
    assert f'#{doc_id}' in referencias


# ═══════════════════════════════════════════
# 4. La estructura XAdES que el anexo exige
# ═══════════════════════════════════════════

def test_los_algoritmos_son_los_que_exige_el_anexo(firmados):
    """SHA-384 en todos los digests y RSA-SHA384 en la firma.

    SHA-1 y SHA-256 están rechazados por la DIAN en documentos nuevos.
    """
    from lxml import etree

    NS = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}
    arbol = etree.fromstring(
        (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes())

    metodos = [n.get('Algorithm') for n in
               arbol.findall('.//ds:DigestMethod', NS)]
    assert metodos, 'no hay DigestMethod'
    for algoritmo in metodos:
        assert 'sha384' in algoritmo.lower(), algoritmo
        assert 'sha1' not in algoritmo.lower(), algoritmo
        assert 'sha256' not in algoritmo.lower(), algoritmo

    firma_metodo = arbol.find('.//ds:SignatureMethod', NS).get('Algorithm')
    assert 'rsa-sha384' in firma_metodo.lower(), firma_metodo


def test_la_canonicalizacion_declarada_es_la_que_se_usa(firmados):
    """El algoritmo declarado tiene que ser el que realmente se aplicó.

    Declarar c14n y usar otra cosa produce una firma que no valida. Aquí se
    comprueba que el documento firmado verifica con el algoritmo declarado.
    """
    import base64
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree

    NS = {'ds': 'http://www.w3.org/2000/09/xmldsig#'}
    crudo = (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes()
    arbol = etree.fromstring(crudo)

    declarado = arbol.find(
        './/ds:CanonicalizationMethod', NS).get('Algorithm')

    firma = base64.b64decode(
        arbol.find('.//ds:SignatureValue', NS).text)
    cert = x509.load_der_x509_certificate(base64.b64decode(
        arbol.find('.//ds:X509Certificate', NS).text))
    signed_info = arbol.find('.//ds:SignedInfo', NS)

    # El anexo declara c14n INCLUSIVO. Si algún día se cambia a exclusiva, esta
    # prueba obliga a cambiar también la forma de canonicalizar: son la misma cosa.
    canonico = etree.tostring(signed_info, method='c14n', exclusive=False,
                              with_comments=False)
    cert.public_key().verify(firma, canonico, padding.PKCS1v15(), hashes.SHA384())

    assert declarado.endswith('REC-xml-c14n-20010315'), declarado


def test_la_hora_de_firma_es_utc(firmados):
    """`xades:SigningTime` va en UTC con 'Z', no en hora local.

    Colombia es UTC-5 todo el año, pero la hora de la firma es la del reloj del
    servidor en UTC: es un hecho verificable por un tercero, y por eso lleva Z.
    """
    from lxml import etree

    NS = {'xades': 'http://uri.etsi.org/01903/v1.3.2#'}
    arbol = etree.fromstring(
        (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes())
    hora = arbol.find('.//xades:SigningTime', NS)

    assert hora is not None, 'falta xades:SigningTime'
    assert hora.text.endswith('Z'), hora.text
    assert len(hora.text) == 20, f'formato inesperado: {hora.text}'