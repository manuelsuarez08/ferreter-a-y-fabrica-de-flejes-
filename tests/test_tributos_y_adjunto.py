"""Tributos y el adjunto del evento: dos cosas que el anexo exige y no se emitian.

ESTE ARCHIVO FIJA TRES COSAS
----------------------------
1. `cac:TaxCategory` lleva `cbc:ID` (la categoria del tributo) ANTES de
   `cbc:Percent`. UBL 2.1 lo declara como "an identifier for this tax category",
   y la categoria NO es el impuesto: el impuesto va en
   `cac:TaxScheme/cbc:ID` ('01' IVA, '04' INC). Son dos cosas distintas y
   confundirlas es un rechazo tipico.

   El proyecto no emitia el ID. Para un producto EXENTO eso dejaba un
   `Percent` en 0.00 sin categoria, y la DIAN rechaza el documento. No era un caso
   teorico: el catalogo tiene cientos de productos exentos.

2. El adjunto del evento (`cac:Attachment/cac:ExternalReference`) llevaba mime y
   descripcion pero NINGUN CONTENIDO: el parametro `xml_documento` se aceptaba y
   se ignoraba. Ademas declaraba `EncodingCode="UTF-8"` sobre algo que no
   viajaba. Ahora el XML va embebido en base64 dentro de `cbc:URI`, en UNA SOLA
   LINEA: el estandar parte el base64 en lineas de 76 caracteres, y si se dejan
   dentro del XML hay que quitarlas al leer. Un espacio de mas cambia el
   documento al que apunta el hash.

3. El orden dentro de `cac:ExternalReference` lo declara UBL:
   URI, Hash, DocumentHash, MimeCode, EncodingCode, Description.
"""
from __future__ import annotations

import base64
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_xml  # noqa: E402

NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'

CATEGORIA_ESTANDAR = dian_xml.CATEGORIA_TASA_ESTANDAR
CATEGORIA_CERO = dian_xml.CATEGORIA_TASA_CERO


@pytest.fixture(scope='module', autouse=True)
def xmls_generados():
    """Se generan los XML antes de leerlos: leer un archivo viejo es un falso
    verde — el archivo en disco es del último script, no del código actual.

    Se llama a `main()` en ESTE proceso en vez de lanzar un subproceso: un
    `subprocess.run` por módulo añadía ~1 s de arranque del intérprete a cada
    archivo y empujaba la suite por encima de dos minutos.
    """
    import importlib.util

    ruta = os.path.join(RAIZ, 'herramientas', 'generar_xml_auditoria.py')
    spec = importlib.util.spec_from_file_location('generar_xml_auditoria', ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo.main()


def _documento(**extra):
    documento = {
        'numero': 'SETP-1', 'fecha': '2026-10-01', 'hora': '10:30:00',
        'cuide': 'a' * 96, 'tipo_documento': 'POS', 'tipo_ambiente': '2',
        'moneda': 'COP', 'valor_total': 119000.0,
        'numero_resolucion': '187640', 'prefijo_resolucion': 'SETP',
        'rango_desde': 1, 'rango_hasta': 999999,
    }
    documento.update(extra)
    return documento


_EMISOR = {'nit': '900187391', 'digito_verificacion': '2',
           'razon_social': 'PRUEBA SAS', 'nombre_comercial': 'El Tornillo',
           'direccion': 'CALLE 1', 'municipio': '11001',
           'departamento': '11', 'pais': 'CO',
           'regimen_fiscal': 'Responsable de IVA',
           'responsabilidades': ['O-13'], 'ciiu': '4665'}
_CLIENTE = {'tipo_documento': 'NIT', 'numero_documento': '830114978',
            'nombre': 'CLIENTE SA', 'direccion': 'AV 6',
            'municipio': '11001', 'departamento': '11', 'pais': 'CO',
            'regimen_fiscal': 'Responsable de IVA',
            'responsabilidades': ['O-13']}


# ═══════════════════════════════════════════
# 1. La categoría del tributo
# ═══════════════════════════════════════════

def test_la_categoria_del_tributo_va_antes_del_porcentaje():
    """UBL 2.1: `TaxCategory` es una secuencia que empieza por `cbc:ID`.

    Con el orden invertido el documento no valida, aunque el contenido sea
    correcto. Es el mismo tipo de fallo que el de `ds:Signature`.
    """
    subtotal = dian_xml._subtotal_impuesto('01', 'IVA', 100000.0, 19000.0, 19.0,
                                           categoria=CATEGORIA_ESTANDAR)
    categoria = subtotal.find(f'{{{NS_CAC}}}TaxCategory')
    hijos = [h.tag.split('}')[-1] for h in categoria]

    assert hijos[0] == 'ID', f'TaxCategory empieza por {hijos[0]}, debe ser ID'
    assert 'Percent' in hijos and 'TaxScheme' in hijos
    assert hijos.index('ID') < hijos.index('Percent')
    assert hijos.index('Percent') < hijos.index('TaxScheme')


def test_la_categoria_no_es_el_impuesto():
    """El ID de `TaxCategory` y el de `TaxScheme` son cosas distintas.

    `TaxCategory/ID` es la CATEGORÍA (UN/EDIFACT 5153: S, Z, E, F).
    `TaxScheme/ID` es el IMPUESTO ('01' IVA, '04' INC). Poner el impuesto en los
    dos sitios —o el mismo valor— es un rechazo típico.
    """
    subtotal = dian_xml._subtotal_impuesto('01', 'IVA', 100000.0, 19000.0, 19.0,
                                           categoria=CATEGORIA_ESTANDAR)
    categoria = subtotal.find(f'{{{NS_CAC}}}TaxCategory')

    assert categoria.find(f'{{{NS_CBC}}}ID').text == 'S'
    assert categoria.find(
        f'{{{NS_CAC}}}TaxScheme/{{{NS_CBC}}}ID').text == '01'

    assert categoria.find(f'{{{NS_CBC}}}ID').text != categoria.find(
        f'{{{NS_CAC}}}TaxScheme/{{{NS_CBC}}}ID').text


def test_la_tasa_cero_declara_categoria_cero():
    """Un producto exento o de tasa cero va con categoría 'Z', no con 'S'.

    Es el caso que no existía antes: sin categoría, un `Percent=0.00` no dice
    si el producto es exento, de tasa cero, o si simplemente se olvidó liquidar
    el impuesto. Y el catálogo tiene cientos de productos exentos.
    """
    subtotal = dian_xml._subtotal_impuesto('01', 'IVA', 50000.0, 0.0, 0.0,
                                           categoria=CATEGORIA_CERO)
    categoria = subtotal.find(f'{{{NS_CAC}}}TaxCategory')

    assert categoria.find(f'{{{NS_CBC}}}ID').text == 'Z'
    assert float(categoria.find(f'{{{NS_CBC}}}Percent').text) == 0.0


def test_una_exencion_declara_el_motivo():
    """`cbc:TaxExemptionReason` va DESPUÉS de `TaxScheme`.

    Sin él, una tasa cero no dice por qué no se pagó impuesto, que es
    exactamente lo que la DIAN pide para justificar la exención.
    """
    subtotal = dian_xml._subtotal_impuesto(
        '01', 'IVA', 50000.0, 0.0, 0.0,
        categoria=dian_xml.CATEGORIA_EXENTO,
        motivo='Articulo 422 Estatuto Tributario')

    categoria = subtotal.find(f'{{{NS_CAC}}}TaxCategory')
    hijos = [h.tag.split('}')[-1] for h in categoria]

    assert 'TaxExemptionReason' in hijos
    assert (categoria.find(f'{{{NS_CBC}}}TaxExemptionReason').text ==
            'Articulo 422 Estatuto Tributario')
    assert hijos.index('TaxScheme') < hijos.index('TaxExemptionReason')


def test_el_subtotal_respeta_el_orden_del_xsd():
    """`TaxSubtotal` es una secuencia: TaxableAmount, TaxAmount, ... TaxCategory."""
    subtotal = dian_xml._subtotal_impuesto('01', 'IVA', 100000.0, 19000.0, 19.0,
                                           categoria=CATEGORIA_ESTANDAR)
    hijos = [h.tag.split('}')[-1] for h in subtotal]

    assert hijos[0] == 'TaxableAmount'
    assert hijos[1] == 'TaxAmount'
    assert hijos[-1] == 'TaxCategory'


def test_el_documento_real_lleva_la_categoria():
    """Comprobado sobre el XML generado, no solo sobre la función suelta."""
    import xml.etree.ElementTree as ET

    raiz = ET.parse(os.path.join(RAIZ, '_auditoria_xml', 'invoice.xml')).getroot()

    categorias = raiz.findall(f'.//{{{NS_CAC}}}TaxCategory')
    assert categorias, 'el documento no tiene ninguna categoría de tributo'

    for categoria in categorias:
        identificador = categoria.find(f'{{{NS_CBC}}}ID')
        assert identificador is not None, (
            'una TaxCategory sin cbc:ID no dice si el tributo es gravado, '
            'exento o de tasa cero')
        assert identificador.text in ('S', 'Z', 'E', 'F'), identificador.text


# ═══════════════════════════════════════════
# 2. El adjunto del evento
# ═══════════════════════════════════════════

def _evento(xml_documento):
    raiz = dian_xml.construir_evento(
        'a' * 96, 'SETP-1', '030', 'Acuse de recibo', _EMISOR,
        xml_documento=xml_documento)
    return raiz.find(f'.//{{{NS_CAC}}}ExternalReference')


def test_el_adjunto_lleva_el_contenido_del_documento():
    """Antes el parámetro se aceptaba y se ignoraba: adjunto declarado y vacío."""
    referencia = _evento(b'<Invoice><cbc:ID>SETP-1</cbc:ID></Invoice>')
    assert referencia is not None

    uri = referencia.find(f'{{{NS_CBC}}}URI')
    assert uri is not None, 'el adjunto no lleva cbc:URI, o sea no lleva nada'

    recuperado = base64.b64decode(uri.text)
    assert b'SETP-1' in recuperado, 'el base64 no contiene el documento'


def test_el_base64_via_en_una_sola_linea():
    """El estándar parte el base64 en líneas de 76 caracteres.

    Dentro del XML no deben viajar: quien lo lee tiene que quitarlas, y un
    espacio de más cambia el documento al que apunta el hash.
    """
    documento = b'<Invoice>' + b'<datos>' * 400 + b'</datos>' + b'</Invoice>'
    referencia = _evento(documento)

    uri = referencia.find(f'{{{NS_CBC}}}URI').text
    assert '\n' not in uri and '\r' not in uri, 'el base64 lleva saltos de línea'
    assert ' ' not in uri, 'el base64 lleva espacios'


def test_el_adjunto_declara_su_propia_codificacion():
    """El `EncodingCode` describe LO QUE VIAJA.

    Antes decía 'UTF-8' sobre un documento que no se enviaba. Ahora es 'base64',
    que es la codificación real del contenido.
    """
    referencia = _evento(b'<Invoice/>')

    encoding = referencia.find(f'{{{NS_CBC}}}EncodingCode')
    assert encoding is not None
    assert encoding.text == 'base64', encoding.text

    mime = referencia.find(f'{{{NS_CBC}}}MimeCode')
    assert mime is not None
    assert 'xml' in mime.text, mime.text


def test_el_orden_dentro_del_adjunto_es_el_del_xsd():
    """UBL declara URI, Hash, DocumentHash, MimeCode, EncodingCode, Description.

    Antes iba MimeCode, EncodingCode y Description sin URI: el orden invertido
    y sin el contenido.
    """
    referencia = _evento(b'<Invoice/>')
    hijos = [h.tag.split('}')[-1] for h in referencia]

    assert hijos[0] == 'URI', f'el adjunto empieza por {hijos[0]}'
    assert 'MimeCode' in hijos and 'EncodingCode' in hijos
    assert hijos.index('URI') < hijos.index('MimeCode')
    assert hijos.index('MimeCode') < hijos.index('EncodingCode')
    assert hijos.index('EncodingCode') < hijos.index('Description')


def test_un_adjunto_vacio_se_rechaza_en_vez_de_emitirse():
    """Un adjunto declarado y vacío es peor que uno ausente.

    Parece que se envió el documento cuando no se envió nada: el documento se
    acepta en la cola y falla después, cuando ya consumió un consecutivo.
    """
    with pytest.raises(ValueError) as error:
        _evento(b'')
    assert 'vacío' in str(error.value)

    with pytest.raises(ValueError):
        _evento(b'   ')


def test_sin_documento_no_se_emite_adjunto():
    """Sin documento no hay adjunto: no se declara un `ExternalReference` vacío."""
    raiz = dian_xml.construir_evento(
        'a' * 96, 'SETP-1', '030', 'Acuse de recibo', _EMISOR)
    assert raiz.find(f'.//{{{NS_CAC}}}Attachment') is None
