"""Cliente SOAP del Web Service POS de Facturatech (RPC/encoded, sin librerías).

Operaciones del WSDL (`.../v1/demo/index.php?wsdl`): uploadDocument,
documentStatus, downloadXML, downloadPDF, downloadCUFE, downloadMetaData y
checkCredits. Las credenciales salen de variables de entorno, nunca del código:

    FACTURATECH_URL       https://ws-pos.facturatech.co/v1/demo   (o /v1/pro)
    FACTURATECH_USUARIO   usuario de la plataforma
    FACTURATECH_PASSWORD  contraseña del WS (se envía como SHA-256 hex; si ya
                          viene como hash de 64 hex se envía tal cual)
"""
import base64
import hashlib
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

TIMEOUT_SEGUNDOS = 30
INTENTOS = 5
_HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')


class ErrorFacturatech(Exception):
    """Falla de red, de configuración o respuesta ilegible del proveedor."""


def hash_password(password):
    """El WS espera SHA-256 hex; un valor que ya es hash de 64 hex no se rehace."""
    if _HEX64.match(password):
        return password.lower()
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def _config():
    url = (os.environ.get('FACTURATECH_URL') or '').rstrip('/')
    usuario = os.environ.get('FACTURATECH_USUARIO') or ''
    password = os.environ.get('FACTURATECH_PASSWORD') or ''
    if not (url and usuario and password):
        raise ErrorFacturatech(
            'Faltan FACTURATECH_URL, FACTURATECH_USUARIO o FACTURATECH_PASSWORD')
    if not url.startswith('https://'):
        raise ErrorFacturatech('FACTURATECH_URL debe usar https')
    return url, usuario, password


def _sobre(operacion, namespace, parametros):
    cuerpo = ''.join(
        f'<{nombre} xsi:type="{tipo}">{escape(str(valor))}</{nombre}>'
        for nombre, tipo, valor in parametros)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP-ENV:Envelope '
        'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">'
        '<SOAP-ENV:Body>'
        f'<ns1:{operacion} xmlns:ns1="{namespace}" '
        'SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'{cuerpo}</ns1:{operacion}>'
        '</SOAP-ENV:Body></SOAP-ENV:Envelope>').encode('utf-8')


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def llamar(operacion, parametros, url=None):
    """Invoca una operación y devuelve `{campo: texto}` de la respuesta.

    `parametros`: lista de (nombre, tipo_xsd, valor), en el orden del WSDL.
    El servicio de facturas normales (/v2/) nombra las operaciones
    `FtechAction.<op>` y separa el SOAPAction con '#'.
    """
    base = url or _config()[0]
    namespace = f'urn:{base}/'
    endpoint = f'{base}/index.php'
    separador = '#' if '/v2/' in base else ''
    peticion = urllib.request.Request(
        endpoint, data=_sobre(operacion, namespace, parametros), method='POST',
        headers={'Content-Type': 'text/xml; charset=utf-8',
                 'SOAPAction': f'{namespace}{separador}{operacion}'})
    # El demo responde 500 vacío de forma intermitente; se reintenta. Reenviar
    # uploadDocument es seguro: un folio ya procesado se rechaza con código 201.
    datos = b''
    for intento in range(INTENTOS):
        try:
            with urllib.request.urlopen(peticion, timeout=TIMEOUT_SEGUNDOS) as resp:
                datos = resp.read()
        except urllib.error.HTTPError as error:
            datos = error.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if intento == INTENTOS - 1:
                raise ErrorFacturatech(f'No se pudo contactar a Facturatech: {error}')
        if datos:
            break
        time.sleep(1 + intento)
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError:
        raise ErrorFacturatech('Respuesta no válida de Facturatech')
    resultado = {}
    for nodo in raiz.iter():
        if len(nodo) == 0 and nodo.text is not None:
            resultado[_local(nodo.tag)] = nodo.text
    if 'faultstring' in resultado:
        raise ErrorFacturatech(f"Error SOAP: {resultado['faultstring']}")
    return resultado


def _credenciales():
    _, usuario, password = _config()
    return [('username', 'xsd:string', usuario),
            ('password', 'xsd:string', hash_password(password))]


def check_credits():
    return llamar('checkCredits', _credenciales())


def upload_document(xml_bytes):
    """Envía el XML simplificado; devuelve code, transactionID y message."""
    xml64 = base64.b64encode(xml_bytes).decode('ascii')
    return llamar('uploadDocument',
                  _credenciales() + [('xmlBase64', 'xsd:string', xml64)])


def document_status(transaccion_id):
    return llamar('documentStatus',
                  _credenciales() + [('transaccionID', 'xsd:string', transaccion_id)])


def _descarga(operacion, prefijo, folio):
    return llamar(operacion, _credenciales() + [
        ('prefix', 'xsd:string', prefijo), ('number', 'xsd:integer', int(folio))])


def download_xml(prefijo, folio):
    return _descarga('downloadXML', prefijo, folio)


def download_pdf(prefijo, folio):
    return _descarga('downloadPDF', prefijo, folio)


def download_cufe(prefijo, folio):
    return _descarga('downloadCUFE', prefijo, folio)


def download_metadata(prefijo, folio):
    return _descarga('downloadMetaData', prefijo, folio)
