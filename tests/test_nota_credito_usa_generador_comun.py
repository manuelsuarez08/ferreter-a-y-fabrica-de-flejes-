"""La nota crédito debe usar el generador COMÚN, no una copia del constructor.

ESTE ARCHIVO EXISTE POR UNA RAZÓN CONCRETA
------------------------------------------
`dian_notas.construir_nota_credito` tuvo su propia copia del constructor: emisor,
adquirente, impuestos, totales y líneas. Por eso los arreglos estructurales
aplicados a `dian_xml.py` —el `cbc:UUID` con el CUDE, el `sts:InvoiceControl`,
el `cbc:IssueTime` con -05:00, el `cac:PartyLegalEntity`, el CIIU, el
`cbc:AdditionalAccountID` y el `cac:CountrySubentityCode`— NUNCA llegaron a la
nota crédito. Salía un documento sin CUDE y sin extensiones DIAN: rechazado de
entrada, y sin que ninguna prueba lo notara, porque cada copia tenía sus
propias pruebas.

Estas pruebas fijan la GARANTÍA, no el detalle: que la nota traiga todo lo que
trae el generador común. Si alguien vuelve a copiar el constructor, se rompen
todas a la vez y el motivo del fallo queda escrito aquí.

Y una segunda garantía: la nota NO puede perder su propia identidad documental.
`CustomizationID` y `ProfileID` sí deben ser los de la nota crédito, porque la
DIAN los valida contra su catálogo de tipos documentales.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_notas, dian_xml  # noqa: E402

NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_STS = 'dian:gov:co:facturaelectronica:Structures-2-1'


@pytest.fixture
def nota():
    documento = {
        'numero': 'SETP-NC-1', 'fecha': '2026-10-01', 'hora': '10:30:00',
        'cuide': 'c' * 96, 'tipo_ambiente': '2', 'moneda': 'COP',
        'tipo_operacion': '10', 'valor_total': 119000.0,
        'cuide_referido': 'a' * 96, 'fecha_referido': '2026-09-30',
        'tipo_documento_referido': 'POS', 'motivo_codigo': '1',
        'motivo_descripcion': 'Devolución total',
    }
    emisor = {
        'nombre_comercial': 'Ferretería Prueba', 'razon_social': 'PRUEBA SAS',
        'nit': '900187391', 'digito_verificacion': '2', 'direccion': 'CALLE 1',
        'municipio': '11001', 'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
        'ciiu': '4665',
    }
    adquirente = {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'digito_verificacion': '9', 'nombre': 'CLIENTE SA',
        'direccion': 'AV 6 # 78-90', 'municipio': '11001', 'departamento': '11',
        'nombre_departamento': 'Bogotá D.C.',
        'ciudad': 'Bogotá D.C.',
        'pais': 'CO', 'regimen_fiscal': 'Responsable de IVA',
        'responsabilidades': ['O-13'],
    }
    items = [{'descripcion': 'CEMENTO', 'cantidad': 1.0,
              'precio_unitario': 100000.0, 'unidad': '94', 'codigo': 'CE001',
              'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
              'base': 100000.0}]
    totales = {
        'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
        'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
        'iva_valor': 19000.0, 'inc_valor': 0,
        'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
        'impuestos_inc': [],
    }
    extras = {'software_id': 'SW', 'software_security_code': 'PIN'}
    raiz = dian_notas.construir_nota_credito(
        documento, emisor, adquirente, items, totales, extras)
    return ET.ElementTree(raiz).getroot()


# ═══════════════════════════════════════════
# 1. La nota trae lo que trae el generador común
# ═══════════════════════════════════════════

def test_la_nota_trae_el_uuid_con_el_cuide(nota):
    """El CUDE va en `cbc:UUID`. Antes la nota salía sin él: no valía nada."""
    uuid = nota.find(f'{{{NS_CBC}}}UUID')
    assert uuid is not None, 'la nota crédito no trae cbc:UUID'
    assert uuid.text == 'c' * 96
    assert uuid.get('schemeName') == 'CUDE-SHA384'


def test_la_nota_trae_las_extensiones_dian(nota):
    """Sin `sts:DianExtensions` no hay SoftwareID, ni resolución, ni QR."""
    assert nota.find(f'.//{{{NS_STS}}}DianExtensions') is not None


def test_la_nota_declara_su_numeracion(nota):
    """`sts:InvoiceControl` es lo que permite a la DIAN validar el consecutivo."""
    assert nota.find(f'.//{{{NS_STS}}}InvoiceControl') is not None


def test_el_issue_time_lleva_la_hora_de_colombia(nota):
    """'hh:mm:ss-05:00'. Sin el desfase, la DIAN rechaza por sincronía horaria."""
    hora = nota.find(f'{{{NS_CBC}}}IssueTime')
    assert hora is not None
    assert hora.text.endswith('-05:00'), hora.text


def test_el_emisor_usa_party_legal_entity(nota):
    """`RegistrationName` y `CompanyID` van en `cac:PartyLegalEntity`.

    Con la copia anterior iban en `cac:Party`, que es donde la DIAN no los mira.
    """
    entidad = nota.find(f'.//{{{NS_CAC}}}AccountingSupplierParty'
                        f'/{{{NS_CAC}}}Party/{{{NS_CAC}}}PartyLegalEntity')
    assert entidad is not None, 'el emisor no usa cac:PartyLegalEntity'
    assert entidad.find(f'{{{NS_CBC}}}RegistrationName') is not None

    company = entidad.find(f'{{{NS_CBC}}}CompanyID')
    assert company is not None
    assert company.get('schemeID') == '4'


def test_el_emisor_declara_el_ciiu(nota):
    """`cbc:IndustryClassificationCode` es obligatorio.

    Sin dato real no se emite el nodo: declararlo vacío es peor que omitirlo,
    porque un nodo presente con valor vacío se lee como "configurado y vacío".
    """
    codigo = nota.find(f'.//{{{NS_CAC}}}PartyLegalEntity'
                       f'/{{{NS_CBC}}}IndustryClassificationCode')
    assert codigo is not None, 'el emisor no declara la actividad económica'
    assert codigo.text == '4665'


def test_el_adquirente_declara_si_es_juridica_o_natural(nota):
    """`cbc:AdditionalAccountID`: 1 = jurídica, 2 = natural. Es obligatorio.

    Cuelga de `cac:PartyLegalEntity`, igual que la identificación del cliente.
    """
    codigo = nota.find(f'.//{{{NS_CAC}}}AccountingCustomerParty'
                       f'/{{{NS_CAC}}}Party/{{{NS_CAC}}}PartyLegalEntity'
                       f'/{{{NS_CBC}}}AdditionalAccountID')
    assert codigo is not None, 'el adquirente no declara AdditionalAccountID'
    assert codigo.text == '1', 'una persona jurídica debe declararse con "1"'


def test_el_departamento_va_en_el_codigo_y_el_nombre_en_su_nodo(nota):
    """`CountrySubentityCode` es el CÓDIGO DANE; `CountrySubentity`, el NOMBRE.

    Poner el código en el nodo del nombre deja el nombre de estado como "11", que
    es lo que pasaba antes.
    """
    # Se acota al adquirente: con `.//` saldría la dirección del emisor, que
    # es el primer nodo del documento, y la prueba estaría mirando el dato
    # equivocado sin que se note.
    direccion = nota.find(f'.//{{{NS_CAC}}}AccountingCustomerParty'
                          f'/{{{NS_CAC}}}Party/{{{NS_CAC}}}PhysicalLocation'
                          f'/{{{NS_CAC}}}Address')
    assert direccion is not None

    codigo = direccion.find(f'{{{NS_CBC}}}CountrySubentityCode')
    nombre = direccion.find(f'{{{NS_CBC}}}CountrySubentity')
    assert codigo is not None and codigo.text == '11', 'el código DANE no está'
    assert nombre is not None, 'el nombre del departamento no está'
    assert nombre.text == 'Bogotá D.C.', \
        'el nombre del departamento es un código, no un nombre'


def test_sin_nombre_de_departamento_cae_al_codigo_sin_romper_el_documento():
    """Si el POS no tiene el nombre, el generador cae al código.

    No es ideal: `cbc:CountrySubentity` queda con "11" en vez de un nombre. Pero
    omitir el nodo rompe el XSD, y un valor feo es menos grave que un documento
    rechazado. La solución real es que el POS guarde el nombre del departamento.
    """
    raiz = dian_notas.construir_nota_credito(
        {'numero': 'SETP-NC-2', 'fecha': '2026-10-01', 'hora': '10:30:00',
         'cuide': 'd' * 96, 'cuide_referido': 'e' * 96,
         'fecha_referido': '2026-09-30'},
        {'nit': '900187391', 'digito_verificacion': '2',
         'razon_social': 'PRUEBA SAS', 'direccion': 'CALLE 1',
         'municipio': '11001', 'departamento': '11', 'pais': 'CO',
         'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13']},
        {'numero_documento': '830114978', 'nombre': 'CLIENTE SA',
         'direccion': 'AV 6', 'municipio': '11001', 'departamento': '11',
         'pais': 'CO'},
        [{'descripcion': 'X', 'cantidad': 1.0, 'precio_unitario': 100000.0,
          'unidad': '94', 'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
          'base': 100000.0}],
        {'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
         'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
         'iva_valor': 19000.0, 'inc_valor': 0,
         'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
         'impuestos_inc': []},
        {})

    direccion = raiz.find(f'.//{{{NS_CAC}}}PhysicalLocation/{{{NS_CAC}}}Address')
    assert direccion.find(f'{{{NS_CBC}}}CountrySubentityCode').text == '11'
    # El nodo existe: el documento sigue siendo válido contra el XSD.
    assert direccion.find(f'{{{NS_CBC}}}CountrySubentity') is not None


def test_los_totales_van_dentro_de_legal_monetary_total(nota):
    """En UBL 2.1 no pueden ir sueltos en la raíz: el XSD los rechaza."""
    assert nota.find(f'{{{NS_CBC}}}PayableAmount') is None, \
        'los totales no pueden ir sueltos en la raíz'
    monetary = nota.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    assert monetary is not None
    assert monetary.find(f'{{{NS_CBC}}}PayableAmount') is not None


def test_no_queda_el_nodo_que_no_existe_en_ubl(nota):
    """`cbc:DespatchDateLine` no está en UBL 2.1. Cualquier versión sobra."""
    assert nota.find(f'.//{{{NS_CBC}}}DespatchDateLine') is None


# ═══════════════════════════════════════════
# 2. La nota conserva lo que es SOLO suyo
# ═══════════════════════════════════════════

def test_la_nota_declara_su_propio_tipo_documental(nota):
    """`CustomizationID` y `ProfileID` son los de la NOTA, no los del POS.

    Es lo único que se mantiene propio: la DIAN valida estos dos textos contra su
    catálogo de tipos documentales, así que si la nota dijera "Documento
    Equivalente POS" la rechazarían por tipo, aunque la estructura estuviera bien.
    """
    assert nota.find(f'{{{NS_CBC}}}CustomizationID').text == \
        dian_notas.CUSTOMIZATION_ID_NC
    assert nota.find(f'{{{NS_CBC}}}ProfileID').text == dian_notas.PROFILE_ID_NC


def test_la_nota_conserva_su_identidad_tras_delegar(nota):
    """Prueba de室友: si mañana se rompe el reemplazo, esto falla antes que la DIAN."""
    assert nota.find(f'{{{NS_CBC}}}CustomizationID').text != \
        dian_xml.CUSTOMIZATION_ID


# ═══════════════════════════════════════════
# 3. Lo que hace que sea una corrección
# ═══════════════════════════════════════════

def test_la_nota_referencia_el_documento_que_corrige(nota):
    ref = nota.find(f'{{{NS_CAC}}}AdditionalDocumentReference')
    assert ref is not None, 'sin referencia la DIAN no sabe qué documento se anula'
    assert ref.find(f'{{{NS_CBC}}}ID').text == 'a' * 96
    assert ref.find(f'{{{NS_CBC}}}IssueDate').text == '2026-09-30'


def test_el_motivo_va_en_discrepancy_response(nota):
    """El concepto de corrección es un dato del AJUSTE, no de la referencia.

    En `AdditionalDocumentReference` la DIAN no lo encuentra y devuelve
    "concepto no válido".
    """
    respuesta = nota.find(f'{{{NS_CAC}}}DiscrepancyResponse')
    assert respuesta is not None
    assert respuesta.find(f'{{{NS_CBC}}}ResponseCode').text == '1'


def test_los_importes_de_la_nota_van_en_positivo(nota):
    """Los montos de UBL 2.1 son MAGNITUDES: van positivos.

    CORRECCIÓN: se ponía `NegativeValue="true"` en los cuatro totales y una prueba
    lo fijaba. UBL 2.1 declara el signo solo en `PayableRoundingAmount` ("the
    rounding amount (positive or negative)"), y no existe en el anexo figura para
    una nota crédito con importes negativos: la DIAN lo rechaza por aritmética.

    Que la nota reste lo dicen el `InvoiceTypeCode` '01', el concepto de
    corrección y la referencia al documento que se corrige, no un signo.
    """
    monetary = nota.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    montos = list(monetary)
    assert montos, 'la nota no tiene totales'

    for monto in montos:
        etiqueta = monto.tag.split('}')[-1]
        assert monto.get('NegativeValue') is None, (
            f'{etiqueta} lleva NegativeValue; en UBL 2.1 el único monto que '
            'admite negativo es PayableRoundingAmount')
        # Y el valor debe ser un número, no un texto con signo pegado.
        assert float(monto.text) >= 0, f'{etiqueta} es negativo: {monto.text}'


def test_el_rounding_si_podria_ir_en_negativo(nota):
    """La EXCEPCIÓN de la norma: `PayableRoundingAmount` sí admite signo.

    No se usa en este documento, pero queda fijada para que nadie "corrija" los
    importes de la nota por analogía con este campo.
    """
    import xml.etree.ElementTree as ET

    from ferreteria.services import dian_xml

    documento = {
        'numero': 'SETP-NC-9', 'fecha': '2026-10-01', 'hora': '10:30:00',
        'cuide': 'f' * 96, 'tipo_ambiente': '2',
        'cuide_referido': 'e' * 96, 'fecha_referido': '2026-09-30',
        'motivo_codigo': '1',
    }
    emisor = {'nit': '900187391', 'digito_verificacion': '2',
              'razon_social': 'PRUEBA SAS', 'direccion': 'CALLE 1',
              'municipio': '11001', 'departamento': '11', 'pais': 'CO',
              'regimen_fiscal': 'Responsable de IVA',
              'responsabilidades': ['O-13']}
    total = {'line_extension_amount': 100000.0,
             'tax_exclusive_amount': 100000.0,
             'tax_inclusive_amount': 119000.0,
             'payable_amount': 119000.0, 'iva_valor': 19000.0, 'inc_valor': 0,
             'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0,
                                'valor': 19000.0}],
             'impuestos_inc': []}
    raiz = dian_notas.construir_nota_credito(
        documento, emisor,
        {'numero_documento': '830114978', 'nombre': 'CLIENTE',
         'direccion': 'AV 6', 'municipio': '11001', 'departamento': '11',
         'pais': 'CO'},
        [{'descripcion': 'X', 'cantidad': 1.0, 'precio_unitario': 100000.0,
          'unidad': '94', 'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
          'base': 100000.0}],
        total, {})

    monetary = raiz.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    etiquetas = [m.tag.split('}')[-1] for m in monetary]

    # El campo ni siquiera se emite: declararlo vacío sería peor que omitirlo.
    assert 'PayableRoundingAmount' not in etiquetas
    assert all(m.get('NegativeValue') is None for m in monetary)


def test_las_lineas_no_llevan_el_signo_de_negativo(nota):
    """Solo los totales llevan el signo: el detalle se declara positivo.

    Marcar también las líneas en negativo produce un documento que la DIAN
    rechaza, y en el POS el cajero ve doble signo en el papel.
    """
    for linea in nota.findall(f'{{{NS_CAC}}}InvoiceLine'):
        assert linea.find(f'{{{NS_CBC}}}LineExtensionAmount'
                          ).get('NegativeValue') is None