"""Pruebas de los 8 arreglos estructurales del XML (auditoría DIAN).

Cada prueba fija un Xpath exacto que la auditoría encontró roto. Están
escritas para que, si alguien revierte un arreglo, falle diciendo QUÉ nodo falta
y no "un test falló".

Los 8:
  1. `cbc:UUID` con `@schemeName` (el CUFE/CUDE).
  2. `sts:InvoiceControl` con resolución y rango autorizado.
  3. `cbc:IssueTime` con zona horaria -05:00.
  4. `cac:PartyLegalEntity` en el emisor.
  5. `cbc:IndustryClassificationCode` (CIIU).
  6. `AdditionalAccountID` en el adquirente.
  7. `cac:CountrySubentityCode` separado de `cac:CountrySubentity`.
  8. Que `cbc:DespatchDateLine` NO exista.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_pos, dian_xml  # noqa: E402

NIT = '900187391'
DV = '2'
CUDE = 'e561e9a9114bf833b3602c779153e2d4087013e06a05abb0fb29fe0' + '0' * 20


@pytest.fixture
def emisor():
    return {
        'nombre_comercial': 'Ferretería Prueba',
        'razon_social': 'FERRETERIA PRUEBA SA',
        'nit': NIT, 'digito_verificacion': DV,
        'direccion': 'CALLE 1 # 2-3', 'ciiu': '4665', 'ciudad': 'Bogota D.C.',
        'municipio': '11001', 'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
        'telefono': '3001234567', 'email': 'facturacion@prueba.co',
    }


@pytest.fixture
def adquirente():
    return {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'digito_verificacion': '9', 'nombre': 'CLIENTE SA',
        'direccion': 'AV 6 # 78-90', 'municipio': '11001', 'ciudad': 'Bogota D.C.',
        'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
    }


def _documento(tipo_documento='POS', numero='SETP-1', **extra):
    documento = {
        'numero': numero, 'fecha': '2026-10-01', 'hora': '10:30:00',
        'cuide': CUDE, 'tipo_ambiente': '2', 'moneda': 'COP',
        'tipo_operacion': '10', 'valor_total': 119000.0, 'tipo_pago': 'efectivo',
        'id_venta': 1, 'tipo_documento': tipo_documento,
        'numero_resolucion': '187640', 'prefijo_resolucion': 'SETP',
        'rango_desde': 1, 'rango_hasta': 999999,
        'nombre_software': 'POS Ferreteria DIAN', 'version_software': '1.0',
        'empresa_software': 'DESARROLLO SAS',
        'nit_proveedor_software': '1054552590',
    }
    documento.update(extra)
    return documento


def _items():
    return [{'descripcion': 'CEMENTO', 'cantidad': 1.0,
             'precio_unitario': 100000.0, 'unidad': '94', 'codigo': 'CE001',
             'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
             'base': 100000.0}]


def _totales():
    return {'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
            'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
            'iva_valor': 19000.0, 'inc_valor': 0,
            'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
            'impuestos_inc': []}


def _xml(emisor, adquirente, documento=None, extras=None):
    extras = extras or {'software_id': 'SW-1', 'software_security_code': 'PIN-1'}
    raiz = dian_xml.construir_invoice(
        documento or _documento(), emisor, adquirente, _items(), _totales(), extras)
    return dian_xml.a_texto(raiz)


# ═══════════════════════════════════════════
# 1. cbc:UUID con @schemeName
# ═══════════════════════════════════════════

def test_1_el_cufe_va_en_cbc_uuid(emisor, adquirente):
    """Xpath: /Invoice/cbc:UUID

    Antes el CUFE estaba en `sts:DianExtensions/sts:CUDE`, un lugar que la DIAN
    no lee. Sin `cbc:UUID` el documento no tiene identificador fiscal.
    """
    xml = _xml(emisor, adquirente)

    m = re.search(r'<cbc:UUID([^>]*)>([^<]*)</cbc:UUID>', xml)
    assert m, 'FALTA /Invoice/cbc:UUID'
    assert m.group(2) == CUDE, 'El UUID debe traer el CUFE/CUDE calculado'


def test_1_el_uuid_no_va_dentro_de_dian_extensions(emisor, adquirente):
    """El lugar viejo debe quedar VACÍO, no duplicado.

    Dejarlo en los dos sitios es peor que dejarlo solo en el bueno: si el valor
    se corrige uno y el otro no, el documento lleva dos huellas distintas.
    """
    xml = _xml(emisor, adquirente)

    bloque = re.search(
        r'<sts:DianExtensions>(.*?)</sts:DianExtensions>', xml, re.S)
    assert '<sts:CUDE>' not in bloque.group(1), \
        'El CUDE ya no va en sts:DianExtensions, solo en cbc:UUID'


@pytest.mark.parametrize('tipo,esperado', [
    ('FV', 'CUFE-SHA384'),    # factura de venta
    ('POS', 'CUDE-SHA384'),   # documento equivalente POS
    ('NC', 'CUDE-SHA384'),    # nota credito
    ('ND', 'CUDE-SHA384'),    # nota debito
])
def test_1_el_scheme_name_depende_del_tipo(emisor, adquirente, tipo, esperado):
    """Le dice a la DIAN con qué algoritmo se calculó la huella."""
    xml = _xml(emisor, adquirente, _documento(tipo_documento=tipo))

    m = re.search(r'<cbc:UUID[^>]*schemeName="([^"]+)"', xml)
    assert m, 'cbc:UUID debe llevar @schemeName'
    assert m.group(1) == esperado


def test_1_el_uuid_del_evento_va_tambien_en_cbc_uuid(emisor):
    """Los eventos (RADIAN) también llevan su huella ahí."""
    raiz = dian_xml.construir_evento(CUDE, 'SETP-1', '030', 'Acuse', emisor)
    xml = dian_xml.a_texto(raiz)

    assert 'cbc:UUID' in xml, 'FALTA cbc:UUID en el ApplicationResponse'


# ═══════════════════════════════════════════
# 2. sts:InvoiceControl
# ═══════════════════════════════════════════

def test_2_existe_invoice_control(emisor, adquirente):
    """Xpath: sts:DianExtensions/sts:InvoiceControl

    Sin este grupo el documento no declara su numeración autorizada.
    """
    xml = _xml(emisor, adquirente)

    assert 'sts:InvoiceControl' in xml, \
        'FALTA sts:DianExtensions/sts:InvoiceControl'


def test_2_declara_el_numero_de_resolucion(emisor, adquirente):
    """Xpath: sts:InvoiceControl/sts:InvoiceAuthorization/sts:AuthorizationNumber"""
    xml = _xml(emisor, adquirente)

    m = re.search(r'<sts:AuthorizationNumber>([^<]*)</sts:AuthorizationNumber>', xml)
    assert m, 'FALTA sts:AuthorizationNumber'
    assert m.group(1) == '187640'


def test_2_declara_el_rango_autorizado(emisor, adquirente):
    """Xpath: sts:InvoiceControl/sts:AuthorizedInvoices/{Prefix,From,To}"""
    xml = _xml(emisor, adquirente)

    for etiqueta, esperado in (('Prefix', 'SETP'), ('From', '1'),
                              ('To', '999999')):
        m = re.search(rf'<sts:{etiqueta}>([^<]*)</sts:{etiqueta}>', xml)
        assert m, f'FALTA sts:{etiqueta}'
        assert m.group(1) == esperado


def test_2_omite_el_rango_si_no_hay_hasta(emisor, adquirente):
    """Un rango 0-0 es peor que no declararlo: la DIAN lo lee como rango real."""
    xml = _xml(emisor, adquirente,
               _documento(prefijo_resolucion='SETP', rango_hasta=0))

    assert 'sts:AuthorizedInvoices' not in xml


def test_2_omite_el_rango_si_no_hay_prefijo(emisor, adquirente):
    xml = _xml(emisor, adquirente,
               _documento(prefijo_resolucion='', rango_hasta=999999))

    assert 'sts:AuthorizedInvoices' not in xml


# ═══════════════════════════════════════════
# 3. Zona horaria
# ═══════════════════════════════════════════

def test_3_la_hora_trae_la_zona_de_colombia(emisor, adquirente):
    """Xpath: /Invoice/cbc:IssueTime -> '10:30:00-05:00'

    Sin el offset el documento se rechaza por esquema.
    """
    xml = _xml(emisor, adquirente)

    m = re.search(r'<cbc:IssueTime>([^<]*)</cbc:IssueTime>', xml)
    assert m, 'FALTA cbc:IssueTime'
    assert m.group(1) == '10:30:00-05:00', \
        f'La hora debe traer -05:00, vino {m.group(1)!r}'


@pytest.mark.parametrize('entrada,esperado', [
    ('10:30:00', '10:30:00-05:00'),
    ('14:03:07.123', '14:03:07-05:00'),          # quita milisegundos
    ('10:30:00-05:00', '10:30:00-05:00'),        # ya trae offset
    ('10:30:00Z', '10:30:00+00:00'),              # UTC explicito
    ('', None),                                    # vacio -> ahora
])
def test_3_normalizar_hora(entrada, esperado):
    resultado = dian_pos.normalizar_hora(entrada)

    if esperado is None:
        assert resultado.endswith('-05:00')
    else:
        assert resultado == esperado


def test_3_el_offset_es_fijo_aunque_el_servidor_mida_otro_pais():
    """Colombia no aplica horario de verano: el offset es -05:00 todo el año.

    Si se usara la zona del servidor con un offset fijo, un servidor en UTC
    emitiría una hora desplazada por su cuenta.
    """
    import datetime

    resultado = dian_pos.normalizar_hora(datetime.datetime(2026, 10, 1, 10, 30, 0))

    assert resultado == '10:30:00-05:00'


# ═══════════════════════════════════════════
# 4 y 5. Emisor: PartyLegalEntity y CIIU
# ═══════════════════════════════════════════

def test_4_el_emisor_tiene_party_legal_entity(emisor, adquirente):
    """Xpath: cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity

    La razón social va ahí, no como `cbc:Name` suelto en el Party.
    """
    xml = _xml(emisor, adquirente)

    bloque = re.search(
        r'<cac:AccountingSupplierParty>(.*?)</cac:AccountingSupplierParty>',
        xml, re.S).group(1)
    assert 'cac:PartyLegalEntity' in bloque, \
        'FALTA cac:PartyLegalEntity en el emisor'


def test_4_la_razon_social_va_registro_name(emisor, adquirente):
    xml = _xml(emisor, adquirente)

    m = re.search(r'<cbc:RegistrationName>([^<]*)</cbc:RegistrationName>', xml)
    assert m, 'FALTA cbc:RegistrationName'
    assert m.group(1) == 'FERRETERIA PRUEBA SA'


def test_4_el_nit_va_en_company_id_con_los_atributos_del_anexo(emisor, adquirente):
    """El NIT va en `cbc:CompanyID`, dentro de `cac:PartyLegalEntity`.

    Anexo Tecnico V1.9, pagina 44:
      FAJ45  @schemeAgencyID   = "195"
      FAJ46  @schemeAgencyName = "CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)"
      FAJ47  @schemeID         = el digito de verificacion
      FAJ48  @schemeName       = "31"

    Esta prueba exigia antes `@schemeID="4"` con `@schemeName="CorporateScheme"`.
    Ninguno de los dos valores aparece en las 753 paginas del Anexo: el codigo
    correcto es 31 y la agencia es la DIAN. Lo que se compara son los atributos
    por separado y no el texto entero, porque su ORDEN no lo fija la norma.
    """
    xml = _xml(emisor, adquirente)

    m = re.search(r'<cbc:CompanyID([^>]*)>([^<]*)</cbc:CompanyID>', xml)
    assert m, 'FALTA cbc:CompanyID'
    atributos = m.group(1)

    assert m.group(2) == NIT
    assert 'schemeAgencyID="195"' in atributos
    assert 'schemeAgencyName="CO, DIAN' in atributos
    assert f'schemeID="{DV}"' in atributos, 'el DV va en @schemeID (FAJ47)'
    assert 'schemeName="31"' in atributos
    assert 'CorporateScheme' not in atributos


def test_4_el_dv_va_en_el_atributo_y_no_en_corporate_id(emisor, adquirente):
    """El DV NO va en `cbc:CorporateID`: ese elemento no aparece en el Anexo.

    Este test exigia la existencia de `cbc:CorporateID` con el DV. Se cambio
    cuando se cotejo el documento: el DV es el @schemeID de `cbc:CompanyID`
    (FAJ47), y `cbc:CorporateID` es un nodo que la norma no define. Emitirlo es
    un rechazo por etiqueta desconocida.
    """
    xml = _xml(emisor, adquirente)

    assert 'CorporateID' not in xml, \
        'cbc:CorporateID no existe en el Anexo: el DV va en @schemeID'

    m = re.search(r'<cbc:CompanyID([^>]*)>', xml)
    assert m and f'schemeID="{DV}"' in m.group(1)


def test_5_el_emisor_declara_su_ciiu(emisor, adquirente):
    """Xpath: cac:PartyLegalEntity/cbc:IndustryClassificationCode

    Es obligatorio. Sin él el documento se rechaza.
    """
    xml = _xml(emisor, adquirente)

    m = re.search(
        r'<cbc:IndustryClassificationCode>([^<]*)</cbc:IndustryClassificationCode>',
        xml)
    assert m, 'FALTA cbc:IndustryClassificationCode (CIIU del emisor)'
    assert m.group(1) == '4665'


def test_5_sin_ciiu_no_se_emite_el_nodo(emisor, adquirente):
    """Sin dato no se inventa: mejor ausente que con un codigo erroneo."""
    emisor.pop('ciiu')
    xml = _xml(emisor, adquirente)

    assert 'IndustryClassificationCode' not in xml


# ═══════════════════════════════════════════
# 6. Adquiriente
# ═══════════════════════════════════════════

def test_6_el_adquiriente_tiene_party_legal_entity(emisor, adquirente):
    xml = _xml(emisor, adquirente)

    bloque = re.search(
        r'<cac:AccountingCustomerParty>(.*?)</cac:AccountingCustomerParty>',
        xml, re.S).group(1)
    assert 'cac:PartyLegalEntity' in bloque


def test_6_tiene_additional_account_id(emisor, adquirente):
    """Es OBLIGATORIO: 1 = Persona Jurídica, 2 = Persona Natural."""
    xml = _xml(emisor, adquirente)

    m = re.search(
        r'<cbc:AdditionalAccountID>([^<]*)</cbc:AdditionalAccountID>', xml)
    assert m, 'FALTA cbc:AdditionalAccountID'
    assert m.group(1) in ('1', '2')


def test_6_un_nit_da_persona_juridica(emisor, adquirente):
    xml = _xml(emisor, adquirente)

    m = re.search(
        r'<cbc:AdditionalAccountID>([^<]*)</cbc:AdditionalAccountID>', xml)
    assert m.group(1) == '1'


def test_6_una_cedula_da_persona_natural(emisor, adquirente):
    adquirente = dict(adquirente, tipo_documento='CC')
    xml = _xml(emisor, adquirente)

    m = re.search(
        r'<cbc:AdditionalAccountID>([^<]*)</cbc:AdditionalAccountID>', xml)
    assert m.group(1) == '2'


def test_6_el_consumidor_final_es_persona_natural(emisor):
    """El consumidor final del anexo es tipo 13, siempre persona natural."""
    xml = _xml(emisor, {})

    m = re.search(
        r'<cbc:AdditionalAccountID>([^<]*)</cbc:AdditionalAccountID>', xml)
    assert m.group(1) == '2'
    assert '222222222222' in xml


# ═══════════════════════════════════════════
# 7. Dirección: código y nombre separados
# ═══════════════════════════════════════════

def test_7_el_departamento_trae_codigo_y_nombre(emisor, adquirente):
    """`CountrySubentityCode` (DANE, 2 dígitos) y `CountrySubentity` (nombre).

    Antes el CÓDIGO iba en el nodo del NOMBRE y el de código no existía.
    """
    xml = _xml(emisor, adquirente)

    codigo = re.search(
        r'<cbc:CountrySubentityCode[^>]*>([^<]*)</cbc:CountrySubentityCode>', xml)
    assert codigo, 'FALTA cbc:CountrySubentityCode'
    assert codigo.group(1) == '11'
    assert re.search(r'<cbc:CountrySubentity>', xml), \
        'FALTA cbc:CountrySubentity (el nombre del departamento)'


def test_7_el_municipio_trae_codigo_dane_en_cbc_id(emisor, adquirente):
    """FAJ09 (Anexo V1.9): el codigo del municipio va en `cbc:ID` del Address.

    Este test buscaba `cbc:LocationID`, que no es el nodo que declara el Anexo.
    UBL si tiene `cbc:LocationID`, pero es un IDENTIFICADOR DE UBICACION
    ("a location where something is located"), no el codigo DANE del municipio;
    usarlo confunde dos cosas distintas. El codigo va en `cbc:ID`.
    """
    xml = _xml(emisor, adquirente)

    # Se recorta el bloque de la DIRECCION del cliente y se busca el `cbc:ID`
    # DENTRO de el. Un `re.search` sobre el XML entero es incorrecto aqui:
    # devolveria el primer `cbc:ID` del documento, que es el del FACTURANTE y
    # tiene forma distinta. Ese fue el fallo de esta prueba cuando se escribio.
    direccion = re.search(
        r'<cac:Address>.*?</cac:Address>', xml, re.DOTALL)
    assert direccion, 'no se encontro el bloque cac:Address'
    bloque = direccion.group(0)

    m = re.search(r'<cbc:ID[^>]*>([^<]*)</cbc:ID>', bloque)
    assert m, 'FALTA cbc:ID con el codigo DANE del municipio'
    assert m.group(1) == '11001'
    assert '<cbc:LocationID' not in xml, \
        'cbc:LocationID no es el codigo del municipio: ese va en cbc:ID'
    assert '<cbc:CityName>11001' not in xml, \
        'el codigo del municipio no es el nombre de la ciudad'


def test_7_el_ciudad_es_el_nombre_no_el_codigo(emisor, adquirente):
    """`CityName` es un NOMBRE: antes iba ahí el código del municipio."""
    xml = _xml(emisor, adquirente)

    m = re.search(r'<cbc:CityName>([^<]*)</cbc:CityName>', xml)
    assert m, 'FALTA cbc:CityName'
    assert m.group(1) == 'Bogotá D.C.'
    assert m.group(1) != '11001', 'CityName no puede ser el codigo'


def test_7_el_pais_va_en_country_identification_code(emisor, adquirente):
    xml = _xml(emisor, adquirente)

    m = re.search(
        r'<cbc:IdentificationCode[^>]*>([^<]*)</cbc:IdentificationCode>', xml)
    assert m and m.group(1) == 'CO'


# ═══════════════════════════════════════════
# 8. Elemento inventado
# ═══════════════════════════════════════════

def test_8_no_existe_despatch_date_line(emisor, adquirente):
    """`cbc:DespatchDateLine` NO existe en UBL 2.1 ni en el anexo técnico.

    Emitirlo produce un XML que no valida contra el XSD. Se eliminó; queda solo
    `cbc:DespatchDate`, que sí es válido.
    """
    xml = _xml(emisor, adquirente)

    assert 'DespatchDateLine' not in xml, \
        'cbc:DespatchDateLine no existe en el esquema'
    assert 'cbc:DespatchDate' in xml, \
        'cbc:DespatchDate sí debe seguir, es válido'


# ═══════════════════════════════════════════
# Regresión: lo que ya funcionaba sigue funcionando
# ═══════════════════════════════════════════

def test_los_totales_siguen_cuadrando(emisor, adquirente):
    """Los arreglos son de estructura: no deben tocar la aritmética."""
    xml = _xml(emisor, adquirente)

    assert '<cbc:LineExtensionAmount currencyID="COP">100000.00' in xml
    assert '<cbc:PayableAmount currencyID="COP">119000.00' in xml


def test_la_estructura_de_extensiones_no_se_rompio(emisor, adquirente):
    xml = _xml(emisor, adquirente)

    assert 'sts:DianExtensions' in xml
    assert 'sts:SoftwareID' in xml
    assert 'sts:SoftwareSecurityCode' in xml
    assert 'sts:SoftwareProvider' in xml


def test_el_xml_sigue_siendo_xml_valido(emisor, adquirente):
    """El XML debe parsear: si no, ni siquiera vale la pena auditarlo."""
    import xml.etree.ElementTree as ET

    xml = _xml(emisor, adquirente)

    try:
        ET.fromstring(xml)
    except ET.ParseError as error:
        pytest.fail(f'El XML no parsea: {error}')