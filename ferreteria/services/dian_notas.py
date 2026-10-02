"""Nota Crédito Electrónica (UBL 2.1) y Documento Soporte a No Obligados.

Este módulo concentra las reglas fiscales de los dos documentos que la DIAN
exige para respaldar las operaciones del POS que NO son una factura de venta
electrónica:

  1. NOTA CRÉDITO ELECTRÓNICA (tipo 'NC')
     Corrige un Documento Equivalente POS ya emitido. Es la ÚNICA forma legal
     de revertir una venta transmitida: anular la venta en el POS dejaría un
     documento válido en el catálogo de la DIAN por el total completo.
     La nota va REFERENCIADA al documento original: lleva su CUIDE, su número y
     el motivo del ajuste (`cac:AdditionalDocumentReference` +
     `cbc:LineID` + `cbc:Note`).

  2. DOCUMENTO SOPORTE A NO OBLIGADOS A FACTURAR (tipo 'DS')
     Respalda una compra hecha a un proveedor informal que no entrega factura
     electrónica (compra de arena, balastro y materiales de construcción de
     extracción directa a un pequeño miningo o transportador). El documento
     registra lo que el proveedor informal no puede facturar pero la ferretería
     sí necesita soportar para justificar la entrada al inventario y la
     deducción de costos.
     NO lleva a un emesor propio registrado ni sustituye una factura: su función
     es dar soporte documental a la compra.

DECISIÓN DE DISEÑO: por qué está en un archivo aparte de `dian_xml.py`
`dian_xml.py` construye el POS, que es un documento de VENTA. La nota crédito
tiene una estructura distinta (referencia al documento original, InvoiceTypeCode
'01' de corrección, importes en negativo) y el documento soporte es un tipo
distinto con emisor y receptor invertidos respecto al POS. Mezclarlos en un solo
generador haría ese archivo más difícil de mantener y más fácil de romper por un
cambio de uno. Aquí se reaprovechan las piezas puras: `dian_pos` (constantes y
formato), `dian_firma` (XAdES-EPES) y `dian_soap` (transporte).

REPARTO DE RESPONSABILIDAD (importante, se corrigió aquí)
----------------------------------------------------------
`construir_nota_credito` usa `dian_xml.construir_documento_equivalente`: el
generador COMÚN ya auditado contra el anexo técnico. Antes este módulo tenía su
PROPIA copia del constructor (emisor, adquirente, impuestos, totales, líneas),
y por eso los arreglos estructurales que se le hicieron a `dian_xml.py` —el
`cbc:UUID` con el CUDE, el `sts:InvoiceControl`, el `cbc:IssueTime` con -05:00,
el `cac:PartyLegalEntity`, el CIIU, el `cbc:AdditionalAccountID` y el
`cac:CountrySubentityCode`— NUNCA llegaban a la nota crédito. La nota salía sin
CUDE y sin extensiones DIAN, es decir, un documento que la DIAN rechaza de
entrada, y las pruebas no lo veían porque cada copia tenía sus propias pruebas.

La lección es la razón de este párrafo: en facturación electrónica no puede
haber dos constructores del mismo documento. Si el próximo documento nuevo
(arranque de sesión, evento de reversión, nota débito) necesita esa estructura,
se llama al generador común; si necesita elementos que el generador común no
tiene, se AGREGAN ahí, no se copia el generador.

NO valida contra el XSD oficial de la DIAN: el anexo del Documento Soporte no
está publicado con esa estructura. La forma del documento sigue las reglas del
documento electrónico estándar (UBL 2.1) y los campos que el anexo del DS
declara, pero conviene confirmar los nombres de elemento contra la resolución
vigente antes de operar en producción.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from . import dian_xml
from .dian_pos import (
    DEPARTAMENTO_POR_DEFECTO,
    DV_CONSUMIDOR_FINAL,
    MUNICIPIO_POR_DEFECTO,
    NIT_CONSUMIDOR_FINAL,
    NOMBRE_CONSUMIDOR_FINAL,
    NOMBRES_IMPUESTO,
    PAIS_POR_DEFECTO,
    TIPO_DOCUMENTO_NOTA_CREDITO,
    TIPO_IMPUESTO_INC,
    TIPO_IMPUESTO_IVA,
    UNIDAD_POR_DEFECTO,
    formatear_cantidad,
    formatear_monto,
    normalizar_fecha,
    normalizar_hora,
    redondear,
    solo_digitos,
    tipo_documento_identidad,
)

# Namespaces: los mismos que usa el POS (UBL 2.1).
NS_INVOICE = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_STS = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
NS_EXT = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'

for _prefijo, _uri in (('ext', NS_EXT), ('xsi', NS_XSI), ('xades', None)):
    if _uri:
        ET.register_namespace(_prefijo, _uri)

# ── Constantes fiscales ─────────────────────────────────────────────────────
# CustomizationID del documento: la DIAN lo valida contra su catálogo, así que
# no se acentúa ni se traduce.
CUSTOMIZATION_ID_NC = (
    'Nota Credito Electronica: Anexo Tecnico 1.0'
)
CUSTOMIZATION_ID_DS = (
    'Documento Soporte a No Obligados a Facturar: Anexo Tecnico 1.0'
)
PROFILE_ID_NC = 'DIAN 2.1: Nota Credito Electronica'
PROFILE_ID_DS = 'DIAN 2.1: Documento Soporte a No Obligados'

# Tipo de operación del documento soporte: '20' compra de bienes/servicios.
TIPO_OPERACION_COMPRA = '20'

# Motivos de la nota crédito. El código es el que viaja al XML y la DIAN exige
# que sea uno del catálogo.
MOTIVOS_NOTA_CREDITO = (
    ('1', 'Devolución total de la venta'),
    ('2', 'Devolución parcial de la venta'),
    ('3', 'Devolución por cambio de mercadería'),
    ('4', 'Devolución por cambio de precio o errata en la cantidad'),
    ('5', 'Descuento o bonificación posterior'),
    ('6', 'Anulación de la operación'),
    ('7', 'Ajuste por error en el RUNT o en la operación previa'),
)

# ── Helpers de construcción (mismo estilo que `dian_xml`) ────────────────────
def _cbc(tag, texto, **atributos):
    nodo = ET.Element(f'{{{NS_CBC}}}{tag}')
    for clave, valor in atributos.items():
        nodo.set(clave, str(valor))
    if texto is not None:
        nodo.text = str(texto)
    return nodo


def _cac(tag):
    return ET.Element(f'{{{NS_CAC}}}{tag}')


def _agregar(padre, *hijos):
    for hijo in hijos:
        padre.append(hijo)
    return padre


def _monto(valor):
    return formatear_monto(valor)


# ═════════════════════════════════════════════════════
# Nota Crédito Electrónica
# ═════════════════════════════════════════════════════
def construir_nota_credito(documento, emisor, adquirente, items, totales,
                           extras=None):
    """Arma el árbol XML de la Nota Crédito Electrónica.

    DELEGA en `dian_xml.construir_documento_equivalente`, el generador común ya
    auditado contra el anexo técnico. Antes este módulo tenía su propia copia
    del constructor y los arreglos estructurales nunca llegaban aquí: la nota
    salía sin `cbc:UUID`, sin `sts:DianExtensions` y sin `cac:PartyLegalEntity`,
    es decir, un documento que la DIAN rechaza por esquema.

    Args:
        documento: dict con numero, fecha, hora, cuide, tipo_ambiente, moneda,
            tipo_operacion, valor_total, Y los datos de la referencia:
                documento_referido (id_venta), numero_referido, cuide_referido,
                fecha_referido, motivo_codigo, motivo_descripcion.
        emisor / adquirente / items / totales: igual que en el POS.
        extras: software_id, software_security_code, nombre/version/empresa del
            software.

    Returns:
        ElementTree.Element raíz (Invoice), SIN firma.

    Raises:
        ValueError: si falta el número de la nota o la REFERENCIA al documento
            original. Una nota crédito sin referencia no es válida: sin ella la
            DIAN no sabe a qué documento corrige.
    """
    numero = str(documento.get('numero') or '').strip()
    if not numero:
        raise ValueError('La nota crédito necesita un número (prefijo + consecutivo)')

    cuide_ref = str(documento.get('cuide_referido') or '').strip()
    if not cuide_ref:
        raise ValueError(
            'La nota crédito debe referenciar el CUIDE del documento que corrige. '
            'Sin esa referencia la DIAN no sabe a qué documento se aplica la nota.'
        )

    # Se delega todo el esqueleto al generador común. `tipo_documento` decide el
    # schemeName del UUID: para la nota es CUDE-SHA384.
    documento_base = dict(documento)
    documento_base['tipo_documento'] = 'NC'

    invoice = dian_xml.construir_invoice(
        documento_base, emisor, adquirente, items, totales, extras)

    # ── Identidad del TIPO de documento ────────────────────────────────────────
    # El generador común pone los valores del POS. La nota crédito es otro tipo
    # documental y la DIAN valida su `CustomizationID` contra su catálogo, así que
    # aquí se reemplazan por los de la nota. Solo son dos etiquetas de cabecera:
    # el resto de la estructura (UUID, extensiones, partes, horas) es la que ya
    # está auditada y se conserva.
    for etiqueta, valor in (('CustomizationID', CUSTOMIZATION_ID_NC),
                            ('ProfileID', PROFILE_ID_NC)):
        nodo = invoice.find(f'{{{NS_CBC}}}{etiqueta}')
        if nodo is not None:
            nodo.text = valor

    # ── Lo que hace que sea una CORRECCIÓN y no una venta negativa suelta ──────
    #
    # La referencia va en `cac:BillingReference/cac:InvoiceDocumentReference`, que
    # es donde UBL 2.1 la ubica dentro de `Invoice`. Antes iba en
    # `cac:AdditionalDocumentReference` como hijo DIRECTO de la raíz, que el XSD
    # no permite ahí.
    #
    # El orden importa igual que en la firma: `Invoice` es una `xsd:sequence`, y
    # `BillingReference` va entre `OrderReference` y `DespatchAdvice`. Si se
    # añade al final del árbol, el documento no valida aunque el contenido sea
    # correcto. Por eso se inserta por índice, no con `append`.
    _insertar_en_secuencia(invoice, _construir_billing_reference(documento),
                           'BillingReference')

    # El concepto de corrección. En UBL va en `cac:DiscrepancyResponse`, que es
    # hijo de `cac:InvoiceResponse`, y ese a su vez en la posición que le toca
    # dentro de la secuencia. Sin él la DIAN no sabe POR QUÉ se corrigió y
    # devuelve "concepto no válido".
    if documento.get('motivo_codigo'):
        _insertar_en_secuencia(invoice, _construir_discrepancia(documento),
                               'DiscrepancyResponse')

    # 3. Los importes NO llevan `NegativeValue`.
    #
    # CORRECCIÓN: antes se ponía `NegativeValue="true"` en los cuatro montos de
    # `LegalMonetaryTotal`. Eso viola la asignación de UBL 2.1, donde el único
    # monto que se declara positivo O NEGATIVO es `PayableRoundingAmount`
    # ("the rounding amount (positive or negative) added to produce the line
    # extension amount"). Los demás son magnitudes: van positivos siempre.
    #
    # El error era plausible porque `NegativeValue` EXISTE en `cbc:Amount` y se
    # ve como la forma "correcta" de restar. Pero un documento con todos sus
    # totales en negativo es un documento que la DIAN rechaza por aritmética: no
    # hay figura en el anexo para una nota crédito con importes negativos.
    #
    # La resta la expresa el TIPO de documento, no el signo:
    #   - `cbc:InvoiceTypeCode` = '01' (nota crédito que corrige a un documento);
    #   - `cac:DiscrepancyResponse/cbc:ResponseCode`, el concepto de corrección;
    #   - `cac:AdditionalDocumentReference` al documento que se corrige.
    # Eso es lo que le dice a la DIAN "esto revierte, no esto vende".

    return invoice


def _construir_discrepancia(documento):
    """`<cac:DiscrepancyResponse>` con el código de concepto del anexo.

    Xpath: cac:DiscrepancyResponse/cbc:ResponseCode
    Valores 1-6 para nota crédito (devolución total/parcial, anulación de la
    operación, devolución por cambio en la forma de pago,(ServiceType) ajuste de
    precio, etc.).
    """
    grupo = ET.Element(f'{{{dian_xml.NS_CAC}}}DiscrepancyResponse')
    codigo = ET.SubElement(grupo, f'{{{dian_xml.NS_CBC}}}ResponseCode')
    codigo.text = str(documento['motivo_codigo'])
    return grupo


def _construir_billing_reference(documento):
    """`<cac:BillingReference>` que apunta al documento que se corrige.

    UBL 2.1 ubica la referencia a la factura dentro de
    `cac:BillingReference/cac:InvoiceDocumentReference`, con el CUIDE, el número
    y la fecha del documento original. Es lo que le dice a la DIAN a qué documento
    se aplica la corrección: sin esto la nota sería una venta negativa suelta.

    Antes se usaba `cac:AdditionalDocumentReference` como hijo directo de la raíz,
    que es donde NO va.
    """
    billing = ET.Element(f'{{{dian_xml.NS_CAC}}}BillingReference')
    referencia = ET.SubElement(
        billing, f'{{{dian_xml.NS_CAC}}}InvoiceDocumentReference')
    _agregar(
        referencia,
        _cbc('ID', documento.get('cuide_referido')),
        _cbc('IssueDate', normalizar_fecha(documento.get('fecha_referido'))),
    )
    # El número del documento original, por separado del CUIDE: el CUIDE es lo
    # que la DIAN usa para localizarlo, el número es para el auditor.
    if documento.get('numero_referido'):
        _agregar(referencia, _cbc('ID', str(documento['numero_referido'])))

    # Descripción libre del motivo: qué ajustó el negocio, en palabras.
    if documento.get('motivo_descripcion'):
        nota = ET.SubElement(referencia, f'{{{dian_xml.NS_CAC}}}Note')
        nota.append(_cbc('Description',
                         str(documento['motivo_descripcion'])))
    return billing


# Orden real de los hijos de `Invoice` segun el XSD de UBL 2.1. Es una
# `xsd:sequence`: el orden NO es libre, y un elemento fuera de su lugar hace que
# el documento no valide aunque el contenido sea correcto.
SECUENCIA_INVOICE = (
    # `UBLExtensions` va PRIMERO. Es donde va `sts:DianExtensions`, y el anexo lo
    # ubica al comienzo del documento; el generador comun lo anadia despues de
    # los totales.
    'UBLExtensions',
    'UBLVersionID', 'CustomizationID', 'ProfileID', 'ID', 'UUID',
    'IssueDate', 'IssueTime', 'DueDate', 'InvoiceTypeCode', 'Note',
    'TaxPointDate', 'DocumentCurrencyCode', 'TaxCurrencyCode',
    'LineCountNumeric',
    'AccountingCost', 'InvoicePeriod', 'OrderReference',
    # `BillingReference` va AQUI, entre `OrderReference` y `DespatchAdvice`.
    'BillingReference',
    'DespatchAdvice', 'AccountingSupplierParty', 'AccountingCustomerParty',
    'PaymentMeans',
    'PaymentTerms', 'PrepaidPayment',
    'AllowanceCharge', 'TaxExchangeRate', 'PricingExchangeRate',
    'PaymentExchangeRate', 'PaymentAlternativeExchangeRate',
    'TaxTotal', 'WithholdingTaxTotal', 'LegalMonetaryTotal',
    'InvoiceLine',
    'DiscrepancyResponse',
)


def _normalizar_secuencia(raiz):
    """Reordena los hijos de `raiz` según el XSD, conservando el orden interno.

    No es un detalle menor: `Invoice` es una `xsd:sequence`, así que dos nodos
    con el contenido correcto pero en orden distinto producen un documento que no
    valida. Aquí el problema venía del generador común, que añadía
    `UBLExtensions` al final, cuando el XSD lo quiere al principio.

    Los nodos que no están en la lista NO se mueven: se quedan donde están.
    """
    orden = {}
    for posicion, nombre in enumerate(SECUENCIA_INVOICE):
        orden.setdefault(nombre, posicion)

    hijos = list(raiz)

    def clave(par):
        indice, hijo = par
        etiqueta = hijo.tag.split('}')[-1]
        return (0, orden[etiqueta]) if etiqueta in orden else (1, indice)

    ordenados = [hijo for _, hijo in sorted(enumerate(hijos), key=clave)]
    if ordenados == hijos:
        return False

    for hijo in hijos:
        raiz.remove(hijo)
    for hijo in ordenados:
        raiz.append(hijo)
    return True


def _insertar_en_secuencia(raiz, nodo, nombre):
    """Coloca `nodo` en la posición que le toca dentro de `raiz`.

    Se compara con `SECUENCIA_INVOICE`, no con el orden en que se construyeron
    los nodos. Si el generador común cambia, esta lista queda desactualizada: por
    eso, si el nombre no está en la secuencia, se avisa en vez de insertar en
    cualquier lado.
    """
    if nombre not in SECUENCIA_INVOICE:
        raise ValueError(
            f'"{nombre}" no está en SECUENCIA_INVOICE. Si se acaba de añadir al '
            'generador, hay que añadirlo también a la secuencia del XSD, o el '
            'documento no valida.'
        )

    destino = SECUENCIA_INVOICE.index(nombre)

    for indice, hijo in enumerate(raiz):
        etiqueta = hijo.tag.split('}')[-1]
        if etiqueta not in SECUENCIA_INVOICE:
            # Nodo que no conoce la secuencia: se asume que va después.
            raiz.insert(indice, nodo)
            return
        if SECUENCIA_INVOICE.index(etiqueta) > destino:
            raiz.insert(indice, nodo)
            return
    raiz.append(nodo)


def _construir_emisor(emisor):
    """`<cac:AccountingSupplierParty>`: en una nota crédito el emisor es el
    MISMO que en la venta original (quien devuelve el dinero)."""
    parte = _cac('AccountingSupplierParty')
    party = _cac('Party')
    if emisor.get('nombre_comercial'):
        party.append(_cbc('Name', str(emisor['nombre_comercial'])))

    identificacion = _cac('PartyIdentification')
    identificacion.append(_cbc('ID', solo_digitos(emisor.get('nit')),
                               schemeID='31', schemeName='31',
                               schemeAgencyID='195'))
    party.append(identificacion)
    party.append(_construir_direccion(emisor))
    party.append(_construir_responsabilidades(emisor))
    parte.append(party)
    return parte


def _construir_adquirente(adquirente):
    """`<cac:AccountingCustomerParty>`: quien devolvió la mercancía."""
    numero = solo_digitos(adquirente.get('numero_documento'))
    if not numero:
        adquirente = {
            'tipo_documento': 'NIT',
            'numero_documento': NIT_CONSUMIDOR_FINAL,
            'digito_verificacion': DV_CONSUMIDOR_FINAL,
            'nombre': NOMBRE_CONSUMIDOR_FINAL,
            'direccion': '',
            'municipio': MUNICIPIO_POR_DEFECTO,
            'departamento': DEPARTAMENTO_POR_DEFECTO,
            'pais': PAIS_POR_DEFECTO,
            'regimen_fiscal': 'No Responsable de IVA',
            'responsabilidades': ['R-99-PN'],
        }

    parte = _cac('AccountingCustomerParty')
    party = _cac('Party')
    party.append(_construir_direccion(adquirente))

    identificacion = _cac('PartyIdentification')
    codigo = tipo_documento_identidad(adquirente.get('tipo_documento'))
    identificacion.append(_cbc('ID', solo_digitos(adquirente.get('numero_documento')),
                               schemeID=codigo, schemeName=codigo,
                               schemeAgencyID='195'))
    party.append(identificacion)
    party.append(_construir_responsabilidades(adquirente))
    parte.append(party)
    return parte


def _construir_direccion(datos):
    ubicacion = _cac('PhysicalLocation')
    direccion = _cac('Address')
    direccion.append(_cbc('StreetName', str(datos.get('direccion') or 'Sin dirección')))
    direccion.append(_cbc('CityName', str(datos.get('municipio') or MUNICIPIO_POR_DEFECTO)))
    direccion.append(_cbc('CountrySubentity',
                          str(datos.get('departamento') or DEPARTAMENTO_POR_DEFECTO)))
    pais = _cac('Country')
    pais.append(_cbc('IdentificationCode', str(datos.get('pais') or PAIS_POR_DEFECTO)))
    direccion.append(pais)
    ubicacion.append(direccion)
    return ubicacion


def _construir_responsabilidades(datos):
    esquema = _cac('PartyTaxScheme')
    responsabilidades = datos.get('responsabilidades') or []
    if isinstance(responsabilidades, str):
        responsabilidades = [r.strip() for r in responsabilidades.split(',')
                             if r.strip()]
    if not responsabilidades:
        responsable = 'Responsable de IVA' in str(datos.get('regimen_fiscal') or '')
        responsabilidades = ['O-48'] if responsable else ['R-99-PN']
    esquema.append(_cbc('TaxLevelCode', ';'.join(responsabilidades),
                        listAgencyID='195', listID='05'))
    tributario = _cac('TaxScheme')
    tributario.append(_cbc('ID', 'ZZ', schemeID='195', schemeName='01'))
    tributario.append(_cbc('Name', 'No aplica'))
    esquema.append(tributario)
    return esquema


def _construir_impuestos(totales):
    """`<cac:TaxTotal>` con un subtotal POR TARIFA (igual que el POS)."""
    nodo = _cac('TaxTotal')
    iva = float(totales.get('iva_valor') or 0)
    inc = float(totales.get('inc_valor') or 0)
    nodo.append(_cbc('TaxAmount', _monto(iva + inc), currencyID='COP'))

    for grupo in (totales.get('impuestos_iva') or []):
        nodo.append(_subtotal_impuesto(
            TIPO_IMPUESTO_IVA, NOMBRES_IMPUESTO[TIPO_IMPUESTO_IVA],
            grupo['base'], grupo['valor'], grupo['tasa']))
    for grupo in (totales.get('impuestos_inc') or []):
        nodo.append(_subtotal_impuesto(
            TIPO_IMPUESTO_INC, NOMBRES_IMPUESTO[TIPO_IMPUESTO_INC],
            grupo['base'], grupo['valor'], grupo['tasa']))
    return nodo


def _subtotal_impuesto(tipo, nombre, base, valor, tarifa):
    subtotal = _cac('TaxSubtotal')
    subtotal.append(_cbc('TaxableAmount', _monto(base), currencyID='COP'))
    subtotal.append(_cbc('TaxAmount', _monto(valor), currencyID='COP'))
    categoria = _cac('TaxCategory')
    categoria.append(_cbc('Percent', _monto(tarifa)))
    esquema = _cac('TaxScheme')
    esquema.append(_cbc('ID', tipo, schemeID='195', schemeName='01'))
    esquema.append(_cbc('Name', nombre))
    categoria.append(esquema)
    subtotal.append(categoria)
    return subtotal


def _construir_linea(indice, item):
    """`<cac:InvoiceLine>` de la nota. El importe va POSITIVO.

    Que la nota reste del documento original lo dicen el tipo de documento
    (`InvoiceTypeCode` '01'), el concepto de corrección y la referencia, no un
    signo: los montos de UBL 2.1 son magnitudes.
    """
    cantidad = float(item.get('cantidad') or 0)
    precio = float(item.get('precio_unitario') or 0)
    base = float(item.get('base') or 0) or (redondear(cantidad * precio))
    iva_tasa = float(item.get('iva_tasa') or 0)

    linea = _cac('InvoiceLine')
    _agregar(
        linea,
        _cbc('ID', str(indice)),
        _cbc('InvoicedQuantity', formatear_cantidad(cantidad),
             unitCode=str(item.get('unidad') or UNIDAD_POR_DEFECTO)),
        _cbc('LineExtensionAmount', _monto(base), currencyID='COP'),
    )

    impuestos = _cac('TaxTotal')
    iva_valor = redondear(base * iva_tasa / 100)
    impuestos.append(_cbc('TaxAmount', _monto(iva_valor), currencyID='COP'))
    if iva_tasa:
        impuestos.append(_subtotal_impuesto(
            TIPO_IMPUESTO_IVA, NOMBRES_IMPUESTO[TIPO_IMPUESTO_IVA],
            base, iva_valor, iva_tasa))
    linea.append(impuestos)

    precio_nodo = _cac('Price')
    precio_nodo.append(_cbc('PriceAmount', _monto(precio), currencyID='COP'))
    linea.append(precio_nodo)

    articulo = _cac('Item')
    articulo.append(_cbc('Description', str(item.get('descripcion') or 'Producto')))
    if item.get('codigo'):
        identificacion = _cac('SellersItemIdentification')
        identificacion.append(_cbc('ID', str(item['codigo'])))
        articulo.append(identificacion)
    linea.append(articulo)
    return linea


# ═════════════════════════════════════════════════════
# Documento Soporte a No Obligados a Facturar
# ═════════════════════════════════════════════════════
def construir_documento_soporte(documento, emisor, proveedor, items, totales,
                                 extras=None):
    """Arma el árbol XML del Documento Soporte a No Obligados a Facturar.

    A diferencia de la nota crédito, aquí las partes están INVERTIDAS respecto
    al POS: el emisor es quien vende (la ferretería) y el receptor es el
    proveedor informal que no está obligado a facturar.

    Args:
        documento: dict con numero, fecha, hora, cuide, tipo_ambiente, moneda,
            valor_total, documento_proveedor (el número de la factura de papel
            que entrega el proveedor, o un número interno de soporte).
        emisor: datos de la ferretería (quien compra y emite el soporte).
        proveedor: datos del proveedor informal. Claves: tipo_documento,
            numero_documento, nombre, direccion, municipio, departamento, pais,
            regimen_fiscal, responsabilidades. `no_obligado` marca que no está
            obligado a facturar (por eso este documento existe).
        items / totales: las líneas compradas y sus impuestos.
    """
    numero = str(documento.get('numero') or '').strip()
    if not numero:
        raise ValueError('El documento soporte necesita un número (prefijo + consecutivo)')

    extras = extras or {}

    invoice = ET.Element(f'{{{NS_INVOICE}}}Invoice')
    invoice.set(f'{{{NS_XSI}}}schemaLocation', f'{NS_INVOICE} UBL-Invoice-2.1.xsd')

    _agregar(
        invoice,
        _cbc('UBLVersionID', 'UBL 2.1'),
        _cbc('CustomizationID', CUSTOMIZATION_ID_DS),
        _cbc('ProfileID', PROFILE_ID_DS),
        _cbc('ID', numero),
        _cbc('IssueDate', normalizar_fecha(documento.get('fecha'))),
        _cbc('IssueTime', normalizar_hora(documento.get('hora'))),
        _cbc('InvoiceTypeCode', '01'),
        _cbc('DocumentCurrencyCode', documento.get('moneda') or 'COP'),
        _cbc('LineCountNumeric', str(len(items))),
    )

    # Referencia al papel que trae el proveedor. No es un documento electrónico
    # validado por la DIAN, es el soporte físico que justifica la compra.
    if documento.get('documento_proveedor'):
        ref = _cac('AdditionalDocumentReference')
        _agregar(ref,
                 _cbc('ID', str(documento['documento_proveedor'])),
                 _cbc('DocumentTypeCode', '01'))
        invoice.append(ref)

    invoice.append(_construir_emisor(emisor))
    invoice.append(_construir_receptor_proveedor(proveedor))
    invoice.append(_construir_impuestos(totales))

    monetary = _cac('LegalMonetaryTotal')
    _agregar(
        monetary,
        _cbc('LineExtensionAmount', _monto(totales.get('line_extension_amount')),
             currencyID='COP'),
        _cbc('TaxExclusiveAmount', _monto(totales.get('tax_exclusive_amount')),
             currencyID='COP'),
        _cbc('TaxInclusiveAmount', _monto(totales.get('tax_inclusive_amount')),
             currencyID='COP'),
        _cbc('PayableAmount', _monto(totales.get('payable_amount')), currencyID='COP'),
    )
    invoice.append(monetary)

    for indice, item in enumerate(items, start=1):
        invoice.append(_construir_linea(indice, item))

    return invoice


def _construir_receptor_proveedor(proveedor):
    """`<cac:AccountingCustomerParty>` con el proveedor informal.

    Se marca como 'No Obligado a Facturar' en el nombre del TaxScheme, que
    es lo que distingue este documento de una factura normal: el receptor no
    estaba obligado a emitir factura electrónica.
    """
    parte = _cac('AccountingCustomerParty')
    party = _cac('Party')
    party.append(_cbc('Name', str(proveedor.get('nombre') or 'Proveedor')))

    identificacion = _cac('PartyIdentification')
    codigo = tipo_documento_identidad(proveedor.get('tipo_documento'))
    identificacion.append(_cbc('ID', solo_digitos(proveedor.get('numero_documento')),
                               schemeID=codigo, schemeName=codigo,
                               schemeAgencyID='195'))
    party.append(identificacion)
    party.append(_construir_direccion(proveedor))

    # PartyTaxScheme con el nombre que declara que NO está obligado a facturar.
    esquema = _cac('PartyTaxScheme')
    responsabilidades = proveedor.get('responsabilidades') or ['R-99-PN']
    if isinstance(responsabilidades, str):
        responsabilidades = [r.strip() for r in responsabilidades.split(',')
                             if r.strip()]
    esquema.append(_cbc('TaxLevelCode', ';'.join(responsabilidades),
                        listAgencyID='195', listID='05'))
    tributario = _cac('TaxScheme')
    tributario.append(_cbc('ID', 'ZZ', schemeID='195', schemeName='01'))
    tributario.append(_cbc('Name', 'No Obligado a Facturar'))
    esquema.append(tributario)
    party.append(esquema)

    parte.append(party)
    return parte


# ═════════════════════════════════════════════════════
# Serialización
# ═════════════════════════════════════════════════════
def a_bytes(raiz, declaracion=True):
    return ET.tostring(raiz, encoding='utf-8', xml_declaration=declaracion)


def a_texto(raiz):
    """Serializa a texto (lo que se guarda en la base para descarga)."""
    return a_bytes(raiz, declaracion=False).decode('utf-8')
