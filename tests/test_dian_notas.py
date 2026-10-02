"""Pruebas de la Nota Crédito Electrónica y del Documento Soporte.

Tres bloques:

  1. ESTRUCTURA DEL XML de la nota crédito: debe REFERENCIAR el CUIDE del
     documento que corrige (sin esa referencia la DIAN no sabe a qué documento
     aplica la corrección) y declarar los importes en negativo.
  2. REVERSIÓN: la venta NO se revierte hasta que la DIAN acepta la nota, y el
     stock solo se devuelve una vez.
  3. DOCUMENTO SOPORTE: el receptor se marca como "No Obligado a Facturar" y
     las partes van invertidas respecto al POS.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_dian_notas.py -q
"""
import os
import sys
import xml.etree.ElementTree as ET

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_notas  # noqa: E402

NS_CAC = dian_notas.NS_CAC
NS_CBC = dian_notas.NS_CBC


# ═════════════════════════════════════════════════════
# Datos de prueba
# ═════════════════════════════════════════════════════
def _emisor():
    return {
        'nit': '900187391', 'razon_social': 'FERRETERIA PRUEBA',
        'nombre_comercial': 'FERRETERIA PRUEBA', 'direccion': 'CALLE 1',
        'municipio': '11001', 'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
    }


def _adquirente():
    return {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'nombre': 'CLIENTE SA', 'direccion': 'AV 6', 'municipio': '11001',
        'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
    }


def _items():
    return [{
        'descripcion': 'Cemento 50kg', 'cantidad': 2.0,
        'precio_unitario': 50000.0, 'unidad': '94', 'codigo': 'CE001',
        'iva_tasa': 19.0, 'inc_tasa': 0, 'base': 100000.0,
    }]


def _totales():
    return {
        'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
        'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
        'iva_valor': 19000.0, 'inc_valor': 0,
        'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
        'impuestos_inc': [],
    }


def _documento():
    return {
        'numero': 'NC-1', 'fecha': '2026-09-29', 'hora': '10:00:00',
        'cuide': 'b' * 96, 'tipo_ambiente': '2', 'moneda': 'COP',
        'valor_total': 119000.0,
        'documento_referido': 12, 'numero_referido': 'POS-1',
        'cuide_referido': 'a' * 96, 'fecha_referido': '2026-09-28',
        'tipo_documento_referido': 'POS', 'motivo_codigo': '1',
        'motivo_descripcion': 'Devolución total',
    }


def _nota():
    return dian_notas.construir_nota_credito(
        _documento(), _emisor(), _adquirente(), _items(), _totales(),
        extras={'software_id': 'SW1'})


# ═════════════════════════════════════════════════════
# 1. Estructura del XML de la nota crédito
# ═════════════════════════════════════════════════════
def test_la_nota_credito_referencia_el_cuide_original():
    """SIN la referencia al CUIDE del documento original, la DIAN no sabe a qué
    documento corrige la nota. Este es el campo que la hace una corrección.

    Va en `cac:BillingReference/cac:InvoiceDocumentReference`. Antes se usaba
    `cac:AdditionalDocumentReference` como hijo DIRECTO de la raíz, que es donde
    UBL 2.1 no lo coloca dentro de `Invoice`."""
    raiz = _nota()
    ref = raiz.find(
        f'{{{NS_CAC}}}BillingReference/{{{NS_CAC}}}InvoiceDocumentReference')
    assert ref is not None, 'falta cac:BillingReference'
    identificador = ref.find(f'{{{NS_CBC}}}ID')
    assert identificador is not None
    assert identificador.text == 'a' * 96, 'la referencia no lleva el CUIDE original'
    fecha = ref.find(f'{{{NS_CBC}}}IssueDate')
    assert fecha is not None and fecha.text == '2026-09-28'


def test_la_referencia_no_va_como_hijo_directo_de_la_raiz():
    """UBL 2.1 coloca la referencia DENTRO de `BillingReference`.

    `AdditionalDocumentReference` como hijo directo de `Invoice` no valida contra
    el XSD: no es un hijo permitido ahí.
    """
    raiz = _nota()
    assert raiz.find(f'{{{NS_CAC}}}AdditionalDocumentReference') is None, (
        'la referencia no puede ir como hijo directo de la raíz')
    assert raiz.find(f'{{{NS_CAC}}}BillingReference') is not None


def test_la_nota_credito_declara_el_motivo():
    """El anexo exige el motivo del ajuste.

    Va en `cac:DiscrepancyResponse/cbc:ResponseCode`, NO en
    `cac:AdditionalDocumentReference/cbc:LineID` (que era donde estaba antes).
    La referencia apunta al documento que se corrige; el motivo es un dato
    DISTINTO, del ajuste. Puesto en la referencia, la DIAN no encuentra el
    concepto de corrección y devuelve "concepto no válido".
    """
    raiz = _nota()
    respuesta = raiz.find(f'{{{NS_CAC}}}DiscrepancyResponse')
    assert respuesta is not None, 'falta el grupo del concepto de corrección'
    codigo = respuesta.find(f'{{{NS_CBC}}}ResponseCode')
    assert codigo is not None, 'falta el código de motivo'
    assert codigo.text == '1'


def test_la_nota_credito_es_de_tipo_correccion():
    """InvoiceTypeCode '01' = nota crédito (vs '02' nota débito)."""
    raiz = _nota()
    codigo = raiz.find(f'{{{NS_CBC}}}InvoiceTypeCode')
    assert codigo is not None and codigo.text == '01'


def test_el_total_de_la_nota_va_en_positivo():
    """El total NO lleva `NegativeValue`: es una magnitud, no un saldo.

    CORRECCIÓN: esta prueba exigía `NegativeValue="true"` y con ella se fijó el
    error. UBL 2.1 solo admite signo en `PayableRoundingAmount`; el resto de los
    montos son magnitudes. Que la nota reste lo dice el `InvoiceTypeCode` '01',
    el concepto de corrección y la referencia al documento corregido.
    """
    raiz = _nota()
    payable = raiz.find(
        f'{{{NS_CAC}}}LegalMonetaryTotal/{{{NS_CBC}}}PayableAmount')
    assert payable is not None
    assert payable.get('NegativeValue') is None, (
        'PayableAmount no admite NegativeValue en UBL 2.1; el signo lo lleva '
        'la resta de los conceptos, y solo PayableRoundingAmount puede ser negativo'
    )
    assert float(payable.text) > 0


def test_los_totales_de_la_nota_van_en_legal_monetary_total():
    raiz = _nota()
    monetary = raiz.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    assert monetary is not None
    for tag in ('LineExtensionAmount', 'TaxExclusiveAmount',
                'TaxInclusiveAmount', 'PayableAmount'):
        assert monetary.find(f'{{{NS_CBC}}}{tag}') is not None, f'falta {tag}'
    # Y NO sueltos en el Invoice.
    assert raiz.find(f'{{{NS_CBC}}}PayableAmount') is None


def test_la_nota_firma_su_propia_invoice_type():
    """La nota usa su propio CustomizationID, no el del POS."""
    raiz = _nota()
    custom = raiz.find(f'{{{NS_CBC}}}CustomizationID').text
    assert 'Nota Credito' in custom, custom
    assert 'POS' not in custom


def test_la_nota_credito_exige_referencia():
    """Sin CUIDE de referencia, el constructor debe fallar."""
    doc = _documento()
    doc['cuide_referido'] = ''
    try:
        dian_notas.construir_nota_credito(
            doc, _emisor(), _adquirente(), _items(), _totales())
    except ValueError as error:
        assert 'CUIDE' in str(error)
    else:
        raise AssertionError('se construyó una nota crédito sin referencia')


def test_la_nota_credito_sin_numero_falla():
    doc = _documento()
    doc['numero'] = ''
    try:
        dian_notas.construir_nota_credito(
            doc, _emisor(), _adquirente(), _items(), _totales())
    except ValueError as error:
        assert 'número' in str(error).lower()
    else:
        raise AssertionError('se construyó una nota crédito sin número')


def test_la_nota_tiene_lineas():
    raiz = _nota()
    lineas = raiz.findall(f'{{{NS_CAC}}}InvoiceLine')
    assert len(lineas) == 1
    linea = lineas[0]
    assert linea.find(f'{{{NS_CBC}}}InvoicedQuantity') is not None
    assert linea.find(f'{{{NS_CAC}}}TaxTotal') is not None
    assert linea.find(f'{{{NS_CAC}}}Price/{{{NS_CBC}}}PriceAmount') is not None


def test_la_nota_es_serializable():
    datos = dian_notas.a_bytes(_nota())
    raiz = ET.fromstring(datos)
    assert raiz.tag.endswith('Invoice')


def test_los_motivos_son_codigos_del_catalogo():
    """Los motivos deben ser strings numéricos cortos (el XML los tipa)."""
    for codigo, descripcion in dian_notas.MOTIVOS_NOTA_CREDITO:
        assert codigo.isdigit(), f'motivo no numérico: {codigo}'
        assert descripcion, 'un motivo sin descripción'


# ═════════════════════════════════════════════════════
# 3. Documento Soporte a No Obligados a Facturar
# ═════════════════════════════════════════════════════
def _proveedor():
    return {
        'nombre': 'DON PEDRO (BALASTRO)', 'tipo_documento': 'CC',
        'numero_documento': '12345678', 'direccion': 'VEREDA EL CARRETERO',
        'municipio': '11001', 'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'No Responsable de IVA',
        'responsabilidades': ['R-99-PN'],
    }


def _soporte():
    documento = {
        'numero': 'DS-1', 'fecha': '2026-09-29', 'hora': '10:00:00',
        'cuide': 'c' * 96, 'tipo_ambiente': '2', 'moneda': 'COP',
        'valor_total': 119000.0, 'documento_proveedor': '001-045',
    }
    return dian_notas.construir_documento_soporte(
        documento, _emisor(), _proveedor(), _items(), _totales(),
        extras={'software_id': 'SW1'})


def test_el_documento_soporte_marca_al_proveedor_como_no_obligado():
    """Es lo que distingue este documento de una factura normal."""
    raiz = _soporte()
    nombres = [n.text for n in raiz.iter(f'{{{NS_CBC}}}Name')]
    assert 'No Obligado a Facturar' in nombres, nombres


def test_el_documento_soporte_tiene_el_proveedor_como_receptor():
    raiz = _soporte()
    receptor = raiz.find(f'{{{NS_CAC}}}AccountingCustomerParty')
    assert receptor is not None
    nombres = [n.text for n in receptor.iter(f'{{{NS_CBC}}}Name')]
    assert any('BALASTRO' in (n or '') for n in nombres), nombres


def test_el_documento_soporte_referencia_el_papel_del_proveedor():
    """El número de la factura de papel del proveedor es el soporte físico."""
    raiz = _soporte()
    ref = raiz.find(f'{{{NS_CAC}}}AdditionalDocumentReference')
    assert ref is not None, 'falta la referencia al documento del proveedor'
    assert ref.find(f'{{{NS_CBC}}}ID').text == '001-045'


def test_el_documento_soporte_tiene_customization_propio():
    raiz = _soporte()
    custom = raiz.find(f'{{{NS_CBC}}}CustomizationID').text
    assert 'No Obligados a Facturar' in custom, custom


def test_el_documento_soporte_es_serializable():
    datos = dian_notas.a_bytes(_soporte())
    raiz = ET.fromstring(datos)
    assert raiz.tag.endswith('Invoice')


def test_el_documento_soporte_necesita_numero():
    documento = {
        'numero': '', 'fecha': '2026-09-29', 'hora': '10:00:00',
        'cuide': 'c' * 96, 'tipo_ambiente': '2', 'moneda': 'COP',
        'valor_total': 100, 'documento_proveedor': '',
    }
    try:
        dian_notas.construir_documento_soporte(
            documento, _emisor(), _proveedor(), _items(), _totales())
    except ValueError:
        pass
    else:
        raise AssertionError('se construyó un soporte sin número')
