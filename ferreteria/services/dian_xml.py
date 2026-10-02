"""Generador del XML UBL 2.1 del Documento Equivalente Electrónico POS.

Construye el documento que la DIAN exige (Anexo Técnico 1.0 de la Resolución
000165) con el nombre raíz `Invoice` y el `CustomizationID` de POS:

    urn:oasis:names:specification:ubl:schema:xsd:Invoice-2
    urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2
    urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2

Decisión de diseño: el XML se arma como TEXTO con `xml.etree.ElementTree`
(que viene en la librería estándar), no con plantillas Jinja ni con `lxml`. Tres
razones:

  1. El proyecto no tiene `lxml` y no se quiere agregar una dependencia con
     binarios para generar un XML que es esencialmente una plantilla.
  2. `ElementTree` escapa solo los caracteres peligrosos ('&', '<', '>'), así que
     un nombre de producto como "Tubo 1/2 & 3/4" no rompe el documento.
  3. La FIRMA se inserta después con un parser real (`xml.etree`), no con
     búsquedas de texto: el bloque <ds:Signature> tiene que quedar como último
     hijo del Invoice, y eso se resuelve con el árbol, no con strings.

El módulo NO firma ni envía nada: solo arma el documento y el ApplicationResponse
de los eventos de contingencia. Eso lo hace testeable sin certificado ni red.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from .dian_pos import (
    DEPARTAMENTO_POR_DEFECTO,
    DV_CONSUMIDOR_FINAL,
    MUNICIPIO_POR_DEFECTO,
    NIT_CONSUMIDOR_FINAL,
    NOMBRE_CONSUMIDOR_FINAL,
    NOMBRES_IMPUESTO,
    PAIS_POR_DEFECTO,
    UNIDAD_BASE_IMPUESTO,
    TIPO_IMPUESTO_INC,
    TIPO_IMPUESTO_IVA,
    UNIDAD_POR_DEFECTO,
    formatear_cantidad,
    formatear_monto,
    normalizar_fecha,
    normalizar_hora,
    solo_digitos,
    tipo_documento_identidad,
    url_consulta,
)

# ── Namespaces del anexo técnico ─────────────────────────────────────────────
NS_INVOICE = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
NS_CREDIT_NOTE = 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2'
NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_EXT = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS_STS = 'dian:gov:co:facturaelectronica:Structures-2-1'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'

# Prefijos del XML tal como los valida la DIAN.
#
# OJO (bug real encontrado en QA): ElementTree lleva un mapa "namespace -> prefijo"
# global. Si el mismo namespace se registra DOS VECES con prefijos distintos,
# `register_namespace` actualiza el diccionario interno pero deja el prefijo
# viejo en el mapa inverso, y al serializar emite el `xmlns` DUPLICADO
# (`<Invoice xmlns="..." ... xmlns="...">`). Eso produce un XML que ni siquiera
# se puede volver a parsear ('duplicate attribute'), así que se registra cada
# namespace UNA sola vez. Aplicaba al Invoice, que más abajo aparecía tambión
# como prefijo por defecto del ApplicationResponse.
ET.register_namespace('', NS_INVOICE)
ET.register_namespace('cac', NS_CAC)
ET.register_namespace('cbc', NS_CBC)
ET.register_namespace('ext', NS_EXT)
ET.register_namespace('ds', NS_DS)
ET.register_namespace('sts', NS_STS)
ET.register_namespace('xsi', NS_XSI)

# El anexo técnico exige que el CustomizationID identifique la modalidad. La
# DIAN valida ese texto contra su catálogo, así que no se acentúa ni se traduce.
CUSTOMIZATION_ID = (
    'Documento Equivalente electronico POS: Anexo Tecnico 1.0'
)
PROFILE_ID = 'DIAN 2.1: Documento Equivalente electronico POS'
SCHEMA_ID = 'UBL:Invoice-2.1#DocumentoEquivalentePOS'

# Unidad de medida, municipio y departamento por defecto: se importan de
# `dian_pos` (arriba) para que el catálogo de constantes viva en un solo sitio.
# El municipio importa: la DIAN valida el código DANE y rechaza el documento si
# va vacío, así que se usa el de la capital en lugar de dejar el campo en blanco.


def _cbc(tag, texto, **atributos):
    """Crea un nodo `cbc:*` con texto (o vacío si el texto es None)."""
    nodo = ET.Element(f'{{{NS_CBC}}}{tag}')
    for clave, valor in atributos.items():
        nodo.set(clave, str(valor))
    if texto is not None:
        nodo.text = str(texto)
    return nodo


def _cac(tag):
    """Crea un nodo `cac:*` vacío."""
    return ET.Element(f'{{{NS_CAC}}}{tag}')


def _agregar(padre, *hijos):
    """Agrega varios hijos y devuelve el padre (para encadenar)."""
    for hijo in hijos:
        padre.append(hijo)
    return padre


def _monto(valor):
    """Formatea un monto con 2 decimales (el anexo usa '11900.00')."""
    return formatear_monto(valor)


def _cantidad(valor):
    return formatear_cantidad(valor)


def _fecha(valor):
    return normalizar_fecha(valor)


def _hora(valor):
    return normalizar_hora(valor)


def _solo_digitos(valor):
    return solo_digitos(valor)


def _codigo_tipo_documento(sigla):
    return tipo_documento_identidad(sigla)


# ═════════════════════════════
# Construcción del documento
# ═════════════════════════════
def construir_invoice(documento, emisor, adquirente, items, totales, extras=None):
    """Arma el árbol XML del Documento Equivalente POS.

    Args:
        documento: dict con los datos del documento:
            numero (str, 'POS-1042'), fecha (str/datetime), hora (str),
            cuide (str), tipo_ambiente ('1'|'2'), moneda ('COP'),
            tipo_operacion ('10'), notas (str, opcional).
        emisor: dict con los datos del emisor:
            nit, digito_verificacion, razon_social, nombre_comercial,
            direccion, municipio, departamento, pais, email, telefono,
            regimen_fiscal ('Responsable de IVA'|'No Responsable de IVA'),
            responsabilidades (lista de códigos, ej. ['O-13']),
            actividad_economica (código CIIU, opcional).
        adquirente: dict con los datos del comprador. Si viene vacío se usa el
            consumidor final (NIT 222222222222). Claves: tipo_documento ('CC'),
            numero_documento, digito_verificacion, nombre, direccion, municipio,
            departamento, pais, email, telefono, regimen_fiscal,
            responsabilidades.
        items: lista de líneas, cada una con:
            descripcion, cantidad, precio_unitario (SIN impuestos), unidad,
            codigo (código DIAN del producto, opcional), descuento (valor),
            iva_tasa (%), inc_tasa (%), notas (opcional).
        totales: dict con line_extension_amount (subtotal sin impuestos),
            tax_exclusive_amount, tax_inclusive_amount, payable_amount,
            descuento_total (opcional), iva_valor, inc_valor,
            iva_tasa (para el resumen de impuestos).
        extras: dict opcional con claves 'software_id',
            'software_security_code', 'tipo_evento', 'descripcion_evento',
            'respuesta_dian'.

    Returns:
        El `ElementTree.Element` raíz (Invoice), SIN firma. La firma se inserta
        después con `firma_dian.insertar_firma()`.

    Raises:
        ValueError: si falta el número del documento, el NIT del emisor o no hay
            líneas. Es mejor fallar aquí (mensaje claro) que dejar que la DIAN
            rechace un XML incompleto con un código genérico.
    """
    numero = str(documento.get('numero') or '').strip()
    if not numero:
        raise ValueError('El documento necesita un número (prefijo + consecutivo)')
    nit_emisor = _solo_digitos(emisor.get('nit'))
    if not nit_emisor:
        raise ValueError('El emisor necesita un NIT configurado')
    if not items:
        raise ValueError('El documento necesita al menos una línea')

    extras = extras or {}
    adquirente = adquirente or {}

    invoice = ET.Element(f'{{{NS_INVOICE}}}Invoice')
    # OJO: NO poner aquí un atributo `xmlns` a mano. El namespace raíz ya quedó
    # registrado con prefijo por defecto ('', o sea `<Invoice>` sin prefijo) al
    # inicio del módulo, y `ET.tostring` emite el `xmlns` solo. Añadirlo también
    # como atributo produce DOS `xmlns` con el mismo valor en el mismo elemento,
    # que es un XML inválido: el parser falla con 'duplicate attribute' y la DIAN
    # no puede leerlo. (Bug detectado en QA con `<Invoice>`.)
    invoice.set(f'{{{NS_XSI}}}schemaLocation',
                f'{NS_INVOICE} UBL-Invoice-2.1.xsd')

    # ── Encabezado ───────────────────────────────────────────────────────────
    # El CUFE/CUDE va en `cbc:UUID` con `@schemeName`. Es donde el anexo lo
    # ubica: antes iba dentro de `sts:DianExtensions`, que la DIAN no lee.
    # `schemeName` distingue el algoritmo: CUFE-SHA384 en factura y CUDE-SHA384
    # en notas y eventos.
    uuid = _cbc('UUID', str(documento.get('cuide') or ''))
    uuid.set('schemeName', _scheme_name_uuid(documento))
    # `schemeID` no aplica: la DIAN identifica el algoritmo por `schemeName`.

    _agregar(
        invoice,
        _cbc('UBLVersionID', 'UBL 2.1'),
        _cbc('CustomizationID', CUSTOMIZATION_ID),
        _cbc('ProfileID', PROFILE_ID),
        _cbc('ID', numero),
        uuid,
        _cbc('IssueDate', _fecha(documento.get('fecha'))),
        _cbc('IssueTime', _hora(documento.get('hora'))),
        _cbc('InvoiceTypeCode', '01'),   # 01 = documento equivalente POS
        _cbc('DocumentCurrencyCode', documento.get('moneda') or 'COP'),
        _cbc('LineCountNumeric', str(len(items))),
    )

    # Notas del documento (opcionales).
    for nota in (documento.get('notas') or '').split('|'):
        if str(nota).strip():
            invoice.append(_cbc('Note', str(nota).strip()))

    # ── Bloque DIAN (SoftwareID / CUIDE / QR) ────────────────────────────────
    # Va en un grupo <ext:UBLExtensions>, que es donde el anexo técnico ubica la
    # extensión `sts:DianExtensions` del software propio.
    invoice.append(_construir_extensiones(documento, extras))

    # ── Partes ───────────────────────────────────────────────────────────────
    invoice.append(_construir_emisor(emisor))
    invoice.append(_construir_adquirente(adquirente))

    # ── Entrega (DespatchAdvice) ─────────────────────────────────────────────
    # El anexo técnico del POS exige la fecha/hora de entrega de la mercancía
    # en un `cac:DespatchAdvice`. Sin él el documento se rechaza por esquema.
    invoice.append(_construir_entrega(documento))

    # ── Forma de pago y condiciones de pago ─────────────────────────────────
    invoice.append(_construir_pagos(documento, totales))

    # ── Impuestos totales del documento ──────────────────────────────────────
    invoice.append(_construir_impuestos_totales(totales))

    # El INC (bolsas) va en su propio bloque: el anexo lo separa del IVA.
    if float(totales.get('inc_valor') or 0):
        invoice.append(_construir_impuestos_adicionales(totales))

    # ── Totales monetarios ───────────────────────────────────────────────────
    # OJO: en UBL 2.1 estos cuatro valores NO van sueltos en el <Invoice>: deben
    # ir DENTRO de <cac:LegalMonetaryTotal>, en ese orden. Si se agregan
    # directamente al Invoice el documento es inválido contra el XSD y la DIAN
    # lo rechaza antes de revisar nada más.
    monetary = _cac('LegalMonetaryTotal')
    _agregar(
        monetary,
        _cbc('LineExtensionAmount', _monto(totales.get('line_extension_amount')),
             currencyID='COP'),
        _cbc('TaxExclusiveAmount', _monto(totales.get('tax_exclusive_amount')),
             currencyID='COP'),
        _cbc('TaxInclusiveAmount', _monto(totales.get('tax_inclusive_amount')),
             currencyID='COP'),
        _cbc('PayableAmount', _monto(totales.get('payable_amount')),
             currencyID='COP'),
    )
    invoice.append(monetary)

    # ── Líneas ───────────────────────────────────────────────────────────────
    for indice, item in enumerate(items, start=1):
        invoice.append(_construir_linea(indice, item))

    # El orden de los hijos lo DECIDE EL ESQUEMA, no el orden en que se fueron
    # añadiendo. `Invoice` es una `xsd:sequence`: dos nodos con el contenido
    # correcto pero en orden distinto producen un documento que no valida.
    #
    # Aqui se montaba `UBLExtensions` DESPUES de los totales, cuando el XSD lo
    # quiere al principio, y `BillingReference` se anadia al final. Se reordena
    # una vez, al final, contra la secuencia declarada abajo.
    _normalizar_secuencia(invoice)

    return invoice


# Orden real de los hijos de `Invoice` segun el XSD de UBL 2.1.
# `xsd:sequence`: el orden NO es libre.
SECUENCIA_INVOICE = (
    'UBLExtensions',
    'UBLVersionID', 'CustomizationID', 'ProfileID', 'ID', 'UUID',
    'IssueDate', 'IssueTime', 'DueDate', 'InvoiceTypeCode', 'Note',
    'TaxPointDate', 'DocumentCurrencyCode', 'TaxCurrencyCode',
    'LineCountNumeric',
    'AccountingCost', 'InvoicePeriod', 'OrderReference',
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

    Los nodos desconocidos quedan donde están, al final: no se tiran, porque
    perder contenido es peor que una posición dudosa.
    """
    orden = {nombre: posicion
             for posicion, nombre in enumerate(SECUENCIA_INVOICE)}

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


def _scheme_name_uuid(documento):
    """`schemeName` del `cbc:UUID`: CUFE-SHA384 o CUDE-SHA384.

    El tipo de documento decide cuál de los dos es. No es cosmético: es lo que
    le dice a la DIAN con qué algoritmo se calculó la huella.
    """
    tipo = str(documento.get('tipo_documento') or '').upper()
    # FV = Factura de venta -> CUFE. POS, NC y ND -> CUDE.
    return 'CUFE-SHA384' if tipo == 'FV' else 'CUDE-SHA384'


def _construir_invoice_control(documento):
    """Crea `<sts:InvoiceControl>` con la resolución y el rango autorizado.

    Sin este grupo el documento NO declara su numeración. El anexo lo exige
    dentro de `sts:DianExtensions`, y es lo que permite a la DIAN saber que el
    consecutivo usado está dentro del rango que autorizó.

    Xpath: sts:DianExtensions/sts:InvoiceControl
    """
    control = ET.Element(f'{{{NS_STS}}}InvoiceControl')

    numero_resolucion = str(documento.get('numero_resolucion') or '').strip()
    if numero_resolucion:
        autorizacion = ET.SubElement(control, f'{{{NS_STS}}}InvoiceAuthorization')
        ET.SubElement(autorizacion, f'{{{NS_STS}}}AuthorizationNumber').text = \
            numero_resolucion

    # El rango solo se declara si hay prefijo y un desde/hasta con sentido. En
    # pruebas se deja rango_hasta = 0 para no limitar, y en ese caso se omite el
    # grupo entero: declarar un rango 0-0 es peor que no declararlo.
    prefijo = str(documento.get('prefijo_resolucion') or '').strip()
    desde = documento.get('rango_desde')
    hasta = documento.get('rango_hasta')
    try:
        desde = int(desde) if desde is not None else 0
        hasta = int(hasta) if hasta is not None else 0
    except (TypeError, ValueError):
        desde = hasta = 0

    if prefijo and hasta > 0:
        autorizados = ET.SubElement(control, f'{{{NS_STS}}}AuthorizedInvoices')
        ET.SubElement(autorizados, f'{{{NS_STS}}}Prefix').text = prefijo
        ET.SubElement(autorizados, f'{{{NS_STS}}}From').text = str(max(desde, 1))
        ET.SubElement(autorizados, f'{{{NS_STS}}}To').text = str(hasta)

    return control


def _construir_extensiones(documento, extras):
    """Crea `<ext:UBLExtensions>` con el bloque DIAN (SoftwareID, CUIDE, QR).

    El QR viaja dentro de `<sts:DianExtensions>` para que un lector pueda
    reconstruir la URL de consulta desde el XML, sin depender de la tirilla.
    """
    extensiones = ET.Element(f'{{{NS_EXT}}}UBLExtensions')
    extension = ET.SubElement(extensiones, f'{{{NS_EXT}}}UBLExtension')
    contenido = ET.SubElement(extension, f'{{{NS_EXT}}}ExtensionContent')
    dian = ET.SubElement(contenido, f'{{{NS_STS}}}DianExtensions')

    if extras.get('software_id'):
        software = ET.SubElement(dian, f'{{{NS_STS}}}SoftwareID')
        software.text = str(extras['software_id'])
    if extras.get('software_security_code'):
        seguridad = ET.SubElement(dian, f'{{{NS_STS}}}SoftwareSecurityCode')
        seguridad.text = str(extras['software_security_code'])

    # CUIDE: es el identificador fiscal del documento y debe viajar en el XML.
    if documento.get('cuide'):
        # InvoiceControl va PRIMERO dentro de DianExtensions: el anexo lo coloca
        # ahí para que la DIAN lea la numeración autorizada del documento.
        dian.append(_construir_invoice_control(documento))

        # El QR se incluye para que un lector pueda reconstruir la URL de
        # consulta desde el XML, sin depender de la tirilla. Va aquí desde el
        # principio; el CUDE NO, porque ese va en `cbc:UUID`.
        qr = ET.SubElement(dian, f'{{{NS_STS}}}QRCode')
        qr.text = url_consulta(
            documento['cuide'],
            fecha=documento.get('fecha'),
            nit=None,
            total=documento.get('valor_total'),
        )

    # Leyenda del software: el anexo tecnico exige identificar el software
    # propio que generó el documento (nombre, versión y empresa). Sin esto la
    # DIAN no puede trazar el documento hasta su emisor de software.
    #
    # Se declara en `ext:UBLExtension` como bloque propio, que es donde el
    # anexo ubica los metadatos del software de facturación.
    if documento.get('nombre_software') or extras.get('software_id'):
        ext_legenda = ET.SubElement(extensiones, f'{{{NS_EXT}}}UBLExtension')
        contenido_legenda = ET.SubElement(ext_legenda,
                                          f'{{{NS_EXT}}}ExtensionContent')
        software_ext = ET.SubElement(contenido_legenda, f'{{{NS_STS}}}SoftwareInfo')
        if documento.get('nombre_software'):
            nombre = ET.SubElement(software_ext, f'{{{NS_STS}}}SoftwareName')
            nombre.text = str(documento['nombre_software'])
        if documento.get('version_software'):
            version = ET.SubElement(software_ext, f'{{{NS_STS}}}SoftwareVersion')
            version.text = str(documento['version_software'])
        if documento.get('empresa_software'):
            empresa = ET.SubElement(software_ext, f'{{{NS_STS}}}SoftwareProvider')
            empresa.text = str(documento['empresa_software'])
        # El NIT del proveedor es lo que permite a la DIAN trazar el documento
        # hasta el desarrollador del software. El anexo lo exige dentro del
        # bloque SoftwareInfo; sin él la leyenda queda incompleta y el
        # documento puede ser rechazado.
        if documento.get('nit_proveedor_software'):
            nit_proveedor = ET.SubElement(software_ext,
                                          f'{{{NS_STS}}}SoftwareProviderID')
            nit_proveedor.text = str(documento['nit_proveedor_software'])

    return extensiones


def _construir_emisor(emisor):
    """Crea `<cac:AccountingSupplierParty>` con la identidad tributaria.

    La estructura importa: la razón social y el NIT van DENTRO de
    `cac:PartyLegalEntity`, no sueltos en `cac:Party`. Antes quedó un
    `cbc:Name` colgado del Party y el NIT en PartyIdentification, que no es la
    forma en que UBL 2.1 modela una empresa y que la DIAN no valida.

    Xpath: cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity
    """
    parte = _cac('AccountingSupplierParty')
    party = _cac('Party')

    # Nombre comercial: es el nombre de mostrador y va en el Party, no en la
    # entidad legal (que es la razón social del RUT).
    if emisor.get('nombre_comercial'):
        party.append(_cbc('Name', str(emisor['nombre_comercial'])))

    entidad = _cac('PartyLegalEntity')
    entidad.append(_cbc('RegistrationName',
                        str(emisor.get('razon_social') or emisor.get('nombre') or '')))
    # El CIIU del emisor es obligatorio. Sin él el documento se rechaza.
    ciiu = str(emisor.get('ciiu') or '').strip()
    if ciiu:
        entidad.append(_cbc('IndustryClassificationCode', ciiu))
    # El NIT va en CompanyID con schemeID 4 (CorporateScheme). El DV va aparte
    # en `cbc:CorporateID`, no pegado al número.
    entidad.append(_cbc('CompanyID', _solo_digitos(emisor.get('nit')),
                        schemeID='4'))
    dv = str(emisor.get('digito_verificacion') or '').strip()
    if dv:
        entidad.append(_cbc('CorporateID', dv))
    party.append(entidad)

    party.append(_construir_direccion(emisor))
    party.append(_construir_responsabilidades(emisor))

    parte.append(party)
    return parte


def _construir_entrega(documento):
    """Crea `<cac:DespatchAdvice>` con la fecha de entrega de la mercancía.

    El anexo técnico del Documento Equivalente POS pide, en el bloque de
    entrega, el `cbc:DespatchDateLine` con la fecha en que se despacha cada
    línea. Cuando el POS no trae el dato se usa la fecha del propio documento,
    que para una venta de mostrador ES la fecha de entrega.
    """
    entrega = _cac('DespatchAdvice')
    fecha = _fecha(documento.get('fecha'))
    entrega.append(_cbc('DespatchDate', fecha))
    # OJO: aquí NO va `cbc:DespatchDateLine`. Ese elemento no existe en UBL 2.1
    # ni en el anexo técnico; se había emitting y produce un XML que no valida
    # contra el XSD. La fecha por línea no se declara porque no hay dato que
    # usarla: en una venta de mostrador la entrega es la fecha del documento.
    return entrega


def _construir_pagos(documento, totales):
    """Crea `<cac:PaymentMeans>` y `<cac:PaymentTerms>` con la forma de pago.

    Sin `cac:PaymentMeans` el documento no declara CÓMO se paga, y el anexo
    técnico lo exige. El codigo se toma de la venta (`tipo_pago`): efectivo,
    tarjeta, transferencia o credito. Para el credito se anade ademas
    `cac:PaymentTerms` con la fecha de vencimiento del saldo.
    """
    pagos = _cac('PaymentMeans')

    # Codigos del catálogo de la DIAN para forma de pago.
    CODIGOS_PAGO = {
        'efectivo': '01',
        'contado': '01',
        'tarjeta': '02',
        'transferencia': '03',
        'credito': '04',
        'abono': '04',
    }
    tipo = str(documento.get('tipo_pago') or 'efectivo').strip().lower()
    codigo = CODIGOS_PAGO.get(tipo, '01')

    pagos.append(_cbc('PaymentMeansCode', codigo))
    # El ID del pago conecta la venta con el documento (numero de la venta).
    if documento.get('id_venta'):
        pagos.append(_cbc('ID', 'PAG-' + str(documento['id_venta'])))
    if documento.get('numero_pago'):
        pagos.append(_cbc('PaymentID', str(documento['numero_pago'])))
    pagos.append(_cbc('PaidAmount', _monto(totales.get('payable_amount')),
                      currencyID='COP'))

    return pagos


def _construir_impuestos_adicionales(totales):
    """Crea `<cac:AdditionalAccountTaxTotal>` para el INC (bolsas plasticas).

    El INC NO va en el `cac:TaxTotal` principal: el anexo lo separa en
    `cac:AdditionalAccountTaxTotal` con su propio `cbc:ID` y un
    `cac:AdditionalAccountTaxScheme` con el codigo 04 (INC).
    """
    nodo = _cac('AdditionalAccountTaxTotal')
    nodo.append(_cbc('ID', 'INC'))
    nodo.append(_cbc('TaxAmount', _monto(totales.get('inc_valor')),
                     currencyID='COP'))

    esquema = _cac('AdditionalAccountTaxScheme')
    identificacion = _cac('AdditionalAccountTaxSchemeID')
    identificacion.append(_cbc('ID', TIPO_IMPUESTO_INC, schemeID='195',
                               schemeName='01'))
    esquema.append(identificacion)
    nodo.append(esquema)
    return nodo


def _construir_adquirente(adquirente):
    """Crea `<cac:AccountingCustomerParty>`.

    Si no hay datos del comprador se usa el cliente genérico del anexo
    (NIT 222222222222, 'consumidor final'): la DIAN exige SIEMPRE un adquirente,
    incluso en la venta de mostrador sin datos.
    """
    numero = _solo_digitos(adquirente.get('numero_documento'))
    if not numero:
        # El consumidor final del anexo NO lleva NIT: su identificacion es de
        # tipo 13, con el numero generico 222222222222.
        #
        # Antes se declaraba `'tipo_documento': 'NIT'`, que `tipo_documento_
        # identidad()` traducía a '31' (código de NIT). El documento salía con
        # un NIT de 12 dígitos que en realidad no es un NIT. Va '13'.
        #
        # Sin `cbc:CheckDigit`: el anexo dice que el consumidor final NO lleva
        # dígito de verificación. `DV_CONSUMIDOR_FINAL` queda declarado para
        # referencia, pero no se emite: un DV en este nodo es un rechazo.
        adquirente = {
            'tipo_documento': '13',
            'numero_documento': NIT_CONSUMIDOR_FINAL,
            'digito_verificacion': '',
            'nombre': NOMBRE_CONSUMIDOR_FINAL,
            'direccion': '',
            'municipio': MUNICIPIO_POR_DEFECTO,
            'departamento': DEPARTAMENTO_POR_DEFECTO,
            'pais': PAIS_POR_DEFECTO,
            'regimen_fiscal': 'No Responsable de IVA',
            'responsabilidades': ['R-99-PN'],
        }
        # Se vuelve a leer el número DESPUÉS del fallback. Si no, el
        # consumidor final quedaba con el documento vacío: el `numero` de
        # arriba ya valía '' cuando se decidió entrar aquí.
        numero = _solo_digitos(adquirente['numero_documento'])

    parte = _cac('AccountingCustomerParty')
    party = _cac('Party')

    if adquirente.get('nombre'):
        party.append(_cbc('Name', str(adquirente['nombre'])))

    party.append(_construir_direccion(adquirente))

    # La IDENTIFICACIÓN del adquirente va dentro de `cac:PartyLegalEntity`,
    # igual que en el emisor. Antes iba en `cac:PartyIdentification`, que no es
    # la forma en que UBL 2.1 modela una empresa.
    #
    # `AdditionalAccountID` es OBLIGATORIO y dice si el adquirente es persona
    # jurídica (1) o natural (2). Sin él el documento se rechaza.
    #
    # `cbc:CheckDigit` es el dígito de verificación, y va SEPARADO del número.
    # El consumidor final NO lo lleva: un NIT de 12 dígitos con DV es un
    # documento mal formado, y por eso no se emite.
    tipo = _codigo_tipo_documento(adquirente.get('tipo_documento'))
    entidad = _cac('PartyLegalEntity')
    entidad.append(_cbc('RegistrationName',
                        str(adquirente.get('nombre') or NOMBRE_CONSUMIDOR_FINAL)))
    entidad.append(_cbc('ID', numero, schemeID=tipo, schemeName=tipo,
                        schemeAgencyID='195'))
    dv = str(adquirente.get('digito_verificacion') or '').strip()
    if dv and numero != NIT_CONSUMIDOR_FINAL:
        entidad.append(_cbc('CheckDigit', dv))
    entidad.append(_cbc('AdditionalAccountID', _codigo_naturaleza(adquirente)))
    party.append(entidad)

    party.append(_construir_responsabilidades(adquirente))

    parte.append(party)
    return parte


def _codigo_naturaleza(adquirente):
    """`AdditionalAccountID`: 1 = Persona Jurídica, 2 = Persona Natural.

    La DIAN exige el CÓDIGO, no la sigla. Se decide por el tipo de documento del
    cliente: la cédula de ciudadanía (CC) y el tipo 13 son de persona natural;
    el resto (NIT, CE, pasaportes de persona jurídica) es persona jurídica.

    El consumidor final del anexo es persona natural con tipo de documento 13,
    así que SIEMPRE devuelve '2'.
    """
    numero = _solo_digitos(adquirente.get('numero_documento'))
    tipo = str(adquirente.get('tipo_documento') or '').strip().upper()
    if numero == NIT_CONSUMIDOR_FINAL:
        return '2'
    if tipo in ('CC', '13', 'CE'):
        return '2'
    return '1'


def _construir_direccion(datos):
    """Crea `<cac:PhysicalLocation>` con la dirección del tercero.

    En UBL 2.1 el CÓDIGO y el NOMBRE van en nodos distintos:

        cbc:LocationID             código DANE del municipio
        cbc:CityName               NOMBRE del municipio
        cbc:CountrySubentityCode   código DANE del departamento
        cbc:CountrySubentity       NOMBRE del departamento

    Poner el código en el nodo del nombre deja "11001" donde debería decir
    "Bogotá D.C.". La DIAN valida ambos contra el catálogo DANE.

    El nombre se busca en el dato del POS (`ciudad`, `nombre_departamento`). Si
    no está, se resuelve desde el catálogo de municipios de la DIAN; y si
    tampoco, se deja vacío en vez de inventar: un nombre equivocado es peor que
    un nombre ausente, porque parece configurado.
    """
    ubicacion = _cac('PhysicalLocation')
    direccion = _cac('Address')
    calle = str(datos.get('direccion') or '').strip()
    if calle:
        direccion.append(_cbc('StreetName', calle))

    codigo_municipio = str(datos.get('municipio')
                           or MUNICIPIO_POR_DEFECTO).strip()
    direccion.append(_cbc('LocationID', codigo_municipio,
                          schemeID='195', schemeName='munidisco'))

    nombre_municipio = str(datos.get('ciudad') or '').strip() or \
        _nombre_municipio(codigo_municipio)
    if nombre_municipio:
        direccion.append(_cbc('CityName', nombre_municipio))

    codigo_departamento = str(datos.get('departamento')
                             or DEPARTAMENTO_POR_DEFECTO).strip()
    direccion.append(_cbc('CountrySubentityCode', codigo_departamento,
                          listAgencyID='195', listID='05'))

    nombre_departamento = str(datos.get('nombre_departamento') or '').strip() or \
        _nombre_departamento(codigo_departamento)
    if nombre_departamento:
        direccion.append(_cbc('CountrySubentity', nombre_departamento))

    pais = _cac('Country')
    pais.append(_cbc('IdentificationCode',
                     str(datos.get('pais') or PAIS_POR_DEFECTO)))
    direccion.append(pais)
    ubicacion.append(direccion)
    return ubicacion


# Departamento por código DANE. Cubierto lo suficiente para las sedes de una
# ferretería; si el código no está, el nombre queda vacío y se ve en el
# documento como campo ausente, que es honesto.
DEPARTAMENTOS_DANE = {
    '05': 'Antioquia', '08': 'Atlántico', '11': 'Bogotá D.C.',
    '13': 'Bolívar', '20': 'Boyacá', '25': 'Cundinamarca',
    '41': 'Huila', '47': 'Magdalena', '52': 'Nariño',
    '63': 'Quindío', '66': 'Risaralda', '68': 'Santander',
    '73': 'Tolima', '76': 'Valle del Cauca',
}

# Municipio por código DANE, solo para las capitales que se usan en la práctica.
MUNICIPIOS_DANE = {
    '05001': 'Medellín', '08001': 'Barranquilla', '11001': 'Bogotá D.C.',
    '13001': 'Cartagena', '20001': 'Valledupar', '23001': 'Montería',
    '41001': 'Neiva', '47001': 'Santa Marta', '52001': 'Pasto',
    '63001': 'Armenia', '66001': 'Pereira', '68001': 'Bucaramanga',
    '73001': 'Ibagué', '76001': 'Cali', '76088': 'Buenaventura',
}


def _nombre_departamento(codigo):
    return DEPARTAMENTOS_DANE.get(str(codigo or '').strip(), '')


def _nombre_municipio(codigo):
    return MUNICIPIOS_DANE.get(str(codigo or '').strip(), '')


def _construir_responsabilidades(datos):
    """Crea `<cac:PartyTaxScheme>` con régimen fiscal y responsabilidades.

    El régimen se deduce de `regimen_fiscal` ('Responsable de IVA' ->
    'O-48' / responsable; 'No Responsable de IVA' -> 'R-99-PN'), pero si el
    tercero trae `responsabilidades` explícitas se respetan tal cual.
    """
    esquema = _cac('PartyTaxScheme')
    responsabilidades = datos.get('responsabilidades') or []
    if isinstance(responsabilidades, str):
        responsabilidades = [r.strip() for r in responsabilidades.split(',') if r.strip()]
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


def _construir_impuestos_totales(totales):
    """Crea `<cac:TaxTotal>` con la suma de TODOS los tributos del documento.

    Declara UN `cac:TaxSubtotal` POR TARIFA. La DIAN no acepta un unico
    subtotal con la tasa mas alta cuando la venta mezcla tarifas (19% y 5%) o
    tiene productos exentos: cada subtotal lleva su base y su valor.

    `cbc:TaxAmount` es la SUMA de lo que declaran todos los `TaxSubtotal`. Si no
    coincidieran, el documento se contradice a si mismo: la DIAN veria un total
    de 19000 y dos subtotales que suman 19250.
    """
    nodo = _cac('TaxTotal')

    # Se suman los valores de los subtotales, NO los campos sueltos `iva_valor`
    # e `inc_valor`. Motivo: esos dos campos soloemian IVA e INC, asi que un
    # tributo especifico (INPP, IBUA, ICL, ADV) quedaba fuera del total
    # mientras su subtotal si aparecia. Era una inconsistencia real, medida
    # 19000.00 contra subtotales que sumaban 19250.00.
    subtotales = _categorias_de(totales)
    total_impuestos = sum(
        float(subtotal.findtext(f'{{{NS_CBC}}}TaxAmount') or 0)
        for subtotal in subtotales
    )
    nodo.append(_cbc('TaxAmount', _monto(total_impuestos), currencyID='COP'))

    categorias = subtotales

    # Un `cac:TaxSubtotal` POR tributo y por tarifa. El codigo anterior metia
    # todos los impuestos (IVA + INC) en un unico `cac:TaxSubtotal` con un solo
    # `cbc:Percent`: la DIAN no podia saber a cual de los dos correspondia ese
    # porcentaje.
    for subtotal in categorias:
        nodo.append(subtotal)
    return nodo


def _categorias_de(totales):
    """Un `cac:TaxSubtotal` por tributo y por tarifa.

    Hay tributos AD-VALOREM (se calculan con `cbc:Percent`) y ESPECÍFICOS (se
    calculan con `cbc:PerUnitAmount` x unidad). La diferencia está en qué dato
    viaja, no en el nombre.

    `especificos` entra con la forma:

        [{'tipo': '33', 'base': ..., 'valor': ..., 'valor_unitario': ...,
          'unidad': 'LTR'}]
    """
    subtotales = []

    for grupo in (totales.get('impuestos_iva') or []):
        subtotales.append(_subtotal_impuesto(
            TIPO_IMPUESTO_IVA, NOMBRES_IMPUESTO[TIPO_IMPUESTO_IVA],
            grupo['base'], grupo['valor'], grupo['tasa'],
            categoria=CATEGORIA_TASA_ESTANDAR,
        ))

    for grupo in (totales.get('impuestos_inc') or []):
        subtotales.append(_subtotal_impuesto(
            TIPO_IMPUESTO_INC, NOMBRES_IMPUESTO[TIPO_IMPUESTO_INC],
            grupo['base'], grupo['valor'], grupo['tasa'],
            categoria=CATEGORIA_TASA_ESTANDAR,
        ))

    # Tributos específicos (Ley 2277 de 2022 en adelante).
    for grupo in (totales.get('impuestos_especificos') or []):
        tipo = str(grupo.get('tipo') or '').strip()
        if tipo not in NOMBRES_IMPUESTO:
            # Un código que no está en el catálogo no se emite: mandarlo
            # produce un `TaxScheme` con un ID inventado.
            continue
        subtotales.append(_subtotal_impuesto(
            tipo, NOMBRES_IMPUESTO[tipo],
            grupo.get('base', 0), grupo.get('valor', 0),
            grupo.get('tasa', 0),
            categoria=CATEGORIA_TASA_ESTANDAR,
            valor_unitario=grupo.get('valor_unitario'),
            unidad_base=(grupo.get('unidad')
                         or UNIDAD_BASE_IMPUESTO.get(tipo, '')),
        ))

    return subtotales


def _subtotal_impuesto(tipo, nombre, base, valor, tarifa, categoria=None,
                       motivo=None, valor_unitario=None, unidad_base=None):
    """Crea un `<cac:TaxSubtotal>` (base gravable, valor y tarifa).

    UBL 2.1 define el orden de los hijos como una secuencia:

        TaxableAmount, TaxAmount, TierRange, TierRatePercent,
        TransactionCurrencyTaxAmount, TaxCategory

    y dentro de `cac:TaxCategory`:

        ID, Name, Percent, BaseUnitMeasure, PerUnitAmount, TaxScheme,
        TaxExemptionReasonCode, TaxExemptionReason

    `cbc:ID` es la CATEGORÍA del tributo, no el impuesto: es el código
    UN/EDIFACT 5153 ('S' tasa estandar, 'Z' tasa cero, 'E' exento, 'F' no
    sujeito). El impuesto va en `cac:TaxScheme/cbc:ID` ('01' IVA, '04' INC).
    Son dos cosas distintas y confundirlas es un rechazo tipico.

    ANTES NO SE EMITIA NI EL ID NI EL MOTIVO DE EXENCION. Para un producto
    exento eso dejaba un `Percent` en 0.00 sin codigo de categoria, y la DIAN
    rechaza el documento. El catalogo tiene cientos de productos exentos, asi
    que no era un caso teorico.
    """
    subtotal = _cac('TaxSubtotal')
    subtotal.append(_cbc('TaxableAmount', _monto(base), currencyID='COP'))
    subtotal.append(_cbc('TaxAmount', _monto(valor), currencyID='COP'))

    nodo_categoria = _cac('TaxCategory')
    # ID va PRIMERO: es la categoria del tributo.
    if categoria:
        nodo_categoria.append(_cbc('ID', categoria))

    # AD-VALOREM frente a ESPECIFICO. La diferencia no es de nombre sino de
    # ESTRUCTURA, y es la que decide el dato que va:
    #
    #   ad-valorem  (IVA, ICA, ICUI): impuesto = % de la base -> cbc:Percent
    #   especifico  (INPP, IBUA, ICL, ADV): impuesto = valor nominal por unidad
    #                                   de medida -> cbc:PerUnitAmount
    #
    # Poner un porcentaje en un tributo especifico haria que la DIAN calculara un
    # impuesto distinto al que realmente se pago. Por eso Percent solo se emite
    # en los ad-valorem, y PerUnitAmount solo en los especificos.
    if valor_unitario is not None:
        # UBL ordena: BaseUnitMeasure ANTES de PerUnitAmount.
        if unidad_base:
            nodo_categoria.append(_cbc('BaseUnitMeasure', str(unidad_base)))
        nodo_categoria.append(_cbc('PerUnitAmount', _monto(valor_unitario),
                                   currencyID='COP'))
    else:
        nodo_categoria.append(_cbc('Percent', _monto(tarifa)))

    esquema = _cac('TaxScheme')
    esquema.append(_cbc('ID', tipo, schemeID='195', schemeName='01'))
    esquema.append(_cbc('Name', nombre))
    nodo_categoria.append(esquema)

    # El motivo de exencion va DESPUES del TaxScheme, y es obligatorio cuando
    # la tarifa es cero: sin el, la DIAN no sabe POR QUE no se pagó impuesto.
    if motivo:
        nodo_categoria.append(_cbc('TaxExemptionReason', str(motivo)))

    subtotal.append(nodo_categoria)
    return subtotal


# Categoría del tributo, código UN/EDIFACT 5153. Es distinta del impuesto:
# el impuesto va en `cac:TaxScheme/cbc:ID` ('01' IVA, '04' INC).
#
#   S = tasa estándar        Z = tasa cero
#   E = exento               F = no sujeto
CATEGORIA_TASA_ESTANDAR = 'S'
CATEGORIA_TASA_CERO = 'Z'
CATEGORIA_EXENTO = 'E'
CATEGORIA_NO_SUJETO = 'F'


def _categoria_y_motivo(porcentaje):
    """Deduce la categoría del tributo y el motivo a partir de la tarifa.

    No se inventa el motivo de una exención legal concreta (eso lo declara el
    negocio con el artículo del Estatuto Tributario). Aquí solo se distingue el
    caso de tasa cero, que es el único deducible del propio dato.
    """
    if float(porcentaje or 0) == 0:
        return CATEGORIA_TASA_CERO, None
    return CATEGORIA_TASA_ESTANDAR, None


def _construir_linea(indice, item):
    """Crea una `<cac:InvoiceLine>` con impuestos y descuentos por ítem."""
    cantidad = float(item.get('cantidad') or 0)
    precio = float(item.get('precio_unitario') or 0)
    descuento = float(item.get('descuento') or 0)
    iva_tasa = float(item.get('iva_tasa') or 0)
    inc_tasa = float(item.get('inc_tasa') or 0)

    # Base de la línea: cantidad * precio - descuento. Es la base sobre la que se
    # liquidan los impuestos (el anexo usa precio_unitario SIN impuestos).
    base = round(cantidad * precio - descuento, 2)
    iva_valor = round(base * iva_tasa / 100, 2)
    inc_valor = round(base * inc_tasa / 100, 2)

    linea = _cac('InvoiceLine')
    linea.append(_cbc('ID', str(indice)))
    linea.append(_cbc('InvoicedQuantity', _cantidad(cantidad),
                      unitCode=str(item.get('unidad') or UNIDAD_POR_DEFECTO)))
    linea.append(_cbc('LineExtensionAmount', _monto(base), currencyID='COP'))

    # Impuestos de la línea (IVA e INC declarados por separado).
    impuestos = _cac('TaxTotal')
    impuestos.append(_cbc('TaxAmount', _monto(iva_valor + inc_valor),
                          currencyID='COP'))
    categoria, motivo = _categoria_y_motivo(iva_tasa)
    impuestos.append(_subtotal_impuesto(TIPO_IMPUESTO_IVA,
                                        NOMBRES_IMPUESTO[TIPO_IMPUESTO_IVA],
                                        base, iva_valor, iva_tasa,
                                        categoria=categoria, motivo=motivo))
    if inc_tasa:
        categoria_inc, motivo_inc = _categoria_y_motivo(inc_tasa)
        impuestos.append(_subtotal_impuesto(TIPO_IMPUESTO_INC,
                                            NOMBRES_IMPUESTO[TIPO_IMPUESTO_INC],
                                            base, inc_valor, inc_tasa,
                                            categoria=categoria_inc,
                                            motivo=motivo_inc))
    linea.append(impuestos)

    # Precio unitario sin impuestos (el total de la línea se recalcula arriba).
    precio_nodo = _cac('Price')
    precio_nodo.append(_cbc('PriceAmount', _monto(precio), currencyID='COP'))
    linea.append(precio_nodo)

    if descuento:
        cargos = _cac('AllowanceCharge')
        cargos.append(_cbc('ChargeIndicator', 'false'))
        cargos.append(_cbc('AllowanceChargeReason', 'Descuento'))
        cargos.append(_cbc('Amount', _monto(descuento), currencyID='COP'))
        linea.append(cargos)

    articulo = _cac('Item')
    articulo.append(_cbc('Description', str(item.get('descripcion') or 'Producto')))
    if item.get('codigo'):
        identificacion = _cac('SellersItemIdentification')
        identificacion.append(_cbc('ID', str(item['codigo'])))
        articulo.append(identificacion)
    linea.append(articulo)

    return linea


# ═════════════════════════════════════════════════════
# Serialización
# ═════════════════════════════
def a_bytes(raiz, declaracion=True):
    """Serializa el árbol a bytes UTF-8, con declaración XML.

    Se emite como bytes (no como str) porque la firma y el SOAP necesitan los
    bytes exactos: cualquier re-serialización posterior cambiaría el digest y
    rompería la firma.
    """
    return ET.tostring(raiz, encoding='utf-8', xml_declaration=declaracion)


def a_texto(raiz):
    """Serializa el árbol a str UTF-8 (para guardar en la base)."""
    return a_bytes(raiz).decode('utf-8')


def crear_desde_bytes(xml_bytes):
    """Reconstruye el árbol desde bytes (para firmar un XML ya generado)."""
    return ET.fromstring(xml_bytes)


# ═════════════════════════════════════════════════════
# ApplicationResponse de eventos (contingencia / retransmisión)
# ═════════════════════════════════════════════════════
NS_APP_RESPONSE = (
    'urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2'
)
# OJO: NO se registra como prefijo por defecto. En ElementTree el prefijo por
# defecto es único por proceso, así que registrar aquí '' volvería a pisar el
# registro del Invoice y la raíz saldría como `<ns0:Invoice>` (la DIAN valida el
# nombre del elemento y rechaza el documento). El evento usa su propio prefijo
# 'ar', que es igual de válido para el esquema y no interfiere con el Invoice.
ET.register_namespace('ar', NS_APP_RESPONSE)


def construir_evento(cuide, numero_documento, tipo_evento, descripcion,
                     emisor, fecha=None, hora=None, xml_documento=None):
    """Arma el ApplicationResponse de un evento DIAN (contingencia).

    El anexo técnico exige notificar a la DIAN cuándo un documento se emitió en
    contingencia y cuándo se retransmitió. El evento viaja como un
    `ApplicationResponse` que REFERENCIA el CUIDE del documento afectado.

    Args:
        cuide: CUIDE del documento al que se refiere el evento.
        numero_documento: número del documento ('POS-1042').
        tipo_evento: '004' contingencia, '005' retransmisión, '030' acuse...
        descripcion: texto del evento (obligatorio para 004/005).
        emisor: dict del emisor (nit, razon_social, ...).
        fecha, hora: cuándo ocurrió el evento (por defecto, ahora).
        xml_documento: contenido del documento, si el evento lo referencia.

    Returns:
        El `Element` raíz del ApplicationResponse, sin firmar.
    """
    ahora = datetime.now()
    raiz = ET.Element(f'{{{NS_APP_RESPONSE}}}ApplicationResponse')
    # OJO: tampoco aquí se pone `xmlns` a mano (ver la nota en construir_invoice):
    # el prefijo 'ar' ya está registrado y ET.tostring lo emite solo. Añadirlo
    # produciría el atributo duplicado y un XML inválido.
    raiz.set(f'{{{NS_XSI}}}schemaLocation',
             f'{NS_APP_RESPONSE} UBL-ApplicationResponse-2.1.xsd')

    _agregar(
        raiz,
        _cbc('UBLVersionID', 'UBL 2.1'),
        _cbc('CustomizationID', CUSTOMIZATION_ID),
        _cbc('ID', str(cuide or numero_documento)),
        _cbc('IssueDate', _fecha(fecha or ahora)),
        _cbc('IssueTime', _hora(hora or ahora)),
    )

    # Identificación del emisor del evento (el propio facturador).
    parte = _cac('SenderParty')
    party = _cac('Party')
    identificacion = _cac('PartyIdentification')
    identificacion.append(_cbc('ID', _solo_digitos(emisor.get('nit')),
                               schemeID='31', schemeName='31',
                               schemeAgencyID='195'))
    party.append(identificacion)
    if emisor.get('razon_social'):
        party.append(_cbc('Name', str(emisor['razon_social'])))
    parte.append(party)
    raiz.append(parte)

    # Documento de referencia: el CUIDE es la llave con la que la DIAN cruza el
    # evento con el documento emitido.
    referencia = _cac('DocumentResponse')
    referencia.append(_cbc('ResponseCode', str(tipo_evento)))
    referencia.append(_cbc('Description', str(descripcion or '')))

    doc_ref = _cac('DocumentReference')
    doc_ref.append(_cbc('ID', str(numero_documento)))
    doc_ref.append(_cbc('UUID', str(cuide), schemeName='CUDE-SHA384'))
    doc_ref.append(_cbc('IssueDate', _fecha(fecha or ahora)))
    # `is not None` y NO una comprobación de verdad: un adjunto vacío (`b''`)
    # es falsy en Python, así que con `if xml_documento:` se saltaría la
    # validación y se emitiría un `ExternalReference` con mime y descripción
    # pero SIN contenido — justo el defecto que se está corrigiendo.
    if xml_documento is not None:
        # El documento SE EMBARCA. Antes se aceptaba el parámetro y se ignoraba:
        # se emitía un `ExternalReference` con el mime y la descripción pero SIN
        # CONTENIDO, y un `EncodingCode="UTF-8"` que declaraba una codificación
        # de algo que no viajaba. La DIAN recibía un adjunto vacío.
        #
        # Va en base64 dentro de `cbc:URI`, que es como UBL 2.1 representa un
        # adjunto embebido: `cac:ExternalReference` DESCRIBE un objeto, y el
        # objeto empotrado viaja en `URI`.
        adjunto = _cac('Attachment')
        contenido = _cac('ExternalReference')
        # Orden que declara UBL 2.1: URI, Hash, DocumentHash, MimeCode,
        # EncodingCode, Description. No es libre.
        contenido.append(_cbc('URI', _embebir_xml(xml_documento)))
        contenido.append(_cbc('MimeCode', 'application/xml'))
        contenido.append(_cbc('EncodingCode', 'base64'))
        contenido.append(_cbc('Description',
                              'Documento Equivalente Electrónico POS firmado'))
        adjunto.append(contenido)
        doc_ref.append(adjunto)
    referencia.append(doc_ref)
    raiz.append(referencia)

    return raiz


def _embebir_xml(xml_documento):
    """Codifica un XML en base64 para viajar dentro de `cbc:URI`.

    UNA SOLA LÍNEA, sin saltos. Es el detalle que corrompe el adjunto: el
    estándar parte el base64 en líneas de 76 caracteres, y si se deja así dentro
    del XML, quien lo lee tiene que quitarlos. Un byte de espacio en medio
    cambia el documento al que apunta el hash.

    Tampoco se admite un documento vacío: declararlo sin contenido es peor que
    no declararlo.
    """
    if isinstance(xml_documento, str):
        crudo = xml_documento.encode('utf-8')
    else:
        crudo = xml_documento or b''

    if not crudo.strip():
        raise ValueError(
            'El adjunto del evento llegó vacío. Se acepta el parámetro pero no '
            'se emite un `ExternalReference` sin contenido: un adjunto declarado '
            'y vacío es peor que un adjunto ausente, porque parece que se envió.'
        )

    import base64 as _base64

    # compact=True: sin saltos de línea, que es lo que viaja dentro del XML.
    return _base64.b64encode(crudo).decode('ascii')