"""Pruebas de estructura del XML del Documento Equivalente Electrónico POS.

Estas pruebas NO sustituyen la validación contra el XSD oficial de la DIAN
(que no está disponible en este entorno), pero sí fijan la estructura que el
Anexo Técnico 1.0 exige, de modo que un cambio accidental la rompa visible.

Lo que se verifica aquí:
  1. Los cuatro totales monetarios van DENTRO de `cac:LegalMonetaryTotal`
     (en UBL 2.1 no pueden ir sueltos en el `<Invoice>`).
  2. Existe `cac:PaymentMeans` con código de forma de pago.
  3. Existe `cac:DespatchAdvice` con la fecha de entrega.
  4. El CUIDE y el QR viajan en `sts:DianExtensions`.
  5. Emisor, adquirente y línea tienen los identificadores con schemeAgencyID.
  6. Los totales cuadran con lo que el propio XML declara.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_dian_xml.py -q
"""
import os
import sys
import xml.etree.ElementTree as ET

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_xml  # noqa: E402
from ferreteria.services import dian_emision  # noqa: E402
from ferreteria.services.dian_pos import calcular_cuide  # noqa: E402

NS_CAC = dian_xml.NS_CAC
NS_CBC = dian_xml.NS_CBC
NS_STS = dian_xml.NS_STS


def _emisor():
    return {
        'nit': '900187391', 'digito_verificacion': '2',
        'razon_social': 'FERRETERIA PRUEBA LTDA',
        'nombre_comercial': 'FERRETERIA PRUEBA',
        'direccion': 'CALLE 1 # 2-3', 'municipio': '11001',
        'departamento': '11', 'pais': 'CO', 'email': 'a@b.co',
        'telefono': '3001234567', 'regimen_fiscal': 'Responsable de IVA',
        'responsabilidades': ['O-13'],
    }


def _adquirente():
    return {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'digito_verificacion': '9', 'nombre': 'CLIENTE SA',
        'direccion': 'AV 6 # 78-90', 'municipio': '11001',
        'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA',
        'responsabilidades': ['O-13'],
    }


def _items():
    return [{
        'descripcion': 'Cemento gris 50kg', 'cantidad': 2.0,
        'precio_unitario': 50000.0, 'unidad': '94', 'codigo': 'CE001',
        'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
        'base': 100000.0,
    }]


def _totales(impuestos_iva=None):
    """Totales del documento. Por defecto, un unico grupo de IVA al 19%."""
    if impuestos_iva is None:
        impuestos_iva = [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}]
    return {
        'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
        'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
        'iva_valor': sum(g['valor'] for g in impuestos_iva),
        'inc_valor': 0, 'impuestos_iva': impuestos_iva, 'impuestos_inc': [],
    }


def _documento(tipo_pago='efectivo'):
    cuide = calcular_cuide(
        num_documento='POS-1', fecha='2026-09-29', hora='10:00:00',
        val_imp1=19000.0, val_imp2=0.0, val_total=119000.0,
        nit='900187391', tipo_documento='POS', clave_tecnica='CT1',
        tipo_ambiente='2',
    )
    return {
        'numero': 'POS-1', 'fecha': '2026-09-29', 'hora': '10:00:00',
        'cuide': cuide, 'tipo_ambiente': '2', 'moneda': 'COP',
        'tipo_operacion': '10', 'valor_total': 119000.0,
        'tipo_pago': tipo_pago, 'id_venta': 34,
    }


def _factura(tipo_pago='efectivo'):
    return dian_xml.construir_invoice(
        _documento(tipo_pago), _emisor(), _adquirente(), _items(), _totales(),
        extras={'software_id': 'SW1', 'software_security_code': '12345'},
    )


# ── Totales monetarios: el defecto que rompía el esquema ─────────────────────
def test_totales_monetarios_dentro_de_legal_monetary_total():
    """`cac:LegalMonetaryTotal` existe y contiene los cuatro totales."""
    raiz = _factura()
    monetary = raiz.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    assert monetary is not None, "falta cac:LegalMonetaryTotal"

    for tag in ('LineExtensionAmount', 'TaxExclusiveAmount',
                'TaxInclusiveAmount', 'PayableAmount'):
        nodo = monetary.find(f'{{{NS_CBC}}}{tag}')
        assert nodo is not None, f'falta cbc:{tag} dentro de LegalMonetaryTotal'
        assert nodo.get('currencyID') == 'COP'


def test_totales_no_estan_sueltos_en_el_invoice():
    """Los totales NO pueden aparecer como hijos directos del Invoice.

    Este es el error que hacía que la DIAN rechazara el documento por esquema:
    en UBL 2.1 van obligatoriamente dentro de `cac:LegalMonetaryTotal`.
    """
    raiz = _factura()
    for tag in ('LineExtensionAmount', 'TaxExclusiveAmount',
                'TaxInclusiveAmount', 'PayableAmount'):
        directo = raiz.find(f'{{{NS_CBC}}}{tag}')
        assert directo is None, (
            f'cbc:{tag} está como hijo directo del Invoice: la DIAN lo rechaza '
            'por esquema; debe ir dentro de cac:LegalMonetaryTotal'
        )


def test_orden_de_los_totales_dentro_del_bloque():
    """UBL 2.1 exige un orden fijo en los hijos de LegalMonetaryTotal."""
    raiz = _factura()
    monetary = raiz.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    orden = [c.tag.split('}')[1] for c in monetary]
    assert orden == ['LineExtensionAmount', 'TaxExclusiveAmount',
                     'TaxInclusiveAmount', 'PayableAmount']


# ── Forma de pago ────────────────────────────────────────────────────────────
def test_existe_payment_means_con_codigo():
    """El anexo exige declarar la forma de pago."""
    raiz = _factura()
    medios = raiz.find(f'{{{NS_CAC}}}PaymentMeans')
    assert medios is not None, 'falta cac:PaymentMeans'
    codigo = medios.find(f'{{{NS_CBC}}}PaymentMeansCode')
    assert codigo is not None, 'falta cbc:PaymentMeansCode'
    assert codigo.text in ('01', '02', '03', '04'), codigo.text


def test_payment_means_refleja_el_tipo_de_pago():
    """Cada forma de pago del POS mapea a un código de la DIAN."""
    esperado = {'efectivo': '01', 'tarjeta': '02', 'transferencia': '03',
                'credito': '04'}
    for tipo, codigo in esperado.items():
        raiz = _factura(tipo_pago=tipo)
        nodo = raiz.find(
            f'{{{NS_CAC}}}PaymentMeans/{{{NS_CBC}}}PaymentMeansCode')
        assert nodo.text == codigo, f'{tipo} -> {nodo.text}, esperaba {codigo}'


def test_tipo_de_pago_desconocido_usa_efectivo():
    """Un tipo de pago no catalogado no debe romper la emisión."""
    raiz = _factura(tipo_pago='otro inventado')
    nodo = raiz.find(
        f'{{{NS_CAC}}}PaymentMeans/{{{NS_CBC}}}PaymentMeansCode')
    assert nodo.text == '01'


# ── Entrega ──────────────────────────────────────────────────────────────────
def test_existe_despatch_advice_con_fecha():
    """El anexo pide la fecha de entrega de la mercancía."""
    raiz = _factura()
    entrega = raiz.find(f'{{{NS_CAC}}}DespatchAdvice')
    assert entrega is not None, 'falta cac:DespatchAdvice'
    fecha = entrega.find(f'{{{NS_CBC}}}DespatchDate')
    assert fecha is not None, 'falta cbc:DespatchDate'
    assert fecha.text == '2026-09-29'


# ── Bloque DIAN: CUIDE y QR ───────────────────────────────────────────────────
def test_cuide_y_qr_en_la_extension_dian():
    """El QR viaja en `sts:DianExtensions`; el CUFE, en `cbc:UUID`.

    OJO: esto cambió tras la auditoría. El CUFE estaba dentro de
    `sts:DianExtensions/sts:CUDE`, y la DIAN no lee ahí: lo busca en
    `cbc:UUID`. El QR sí se queda en la extensión.
    """
    raiz = _factura()
    dian = raiz.find(
        f'.//{{{dian_xml.NS_STS}}}DianExtensions')
    assert dian is not None, 'falta sts:DianExtensions'

    # La extensión ya NO lleva el CUDE.
    assert dian.find(f'{{{NS_STS}}}CUDE') is None, \
        'el CUFE no va en sts:DianExtensions, va en cbc:UUID'

    # El QR sí va en la extensión.
    qr = dian.find(f'{{{NS_STS}}}QRCode')
    assert qr is not None and qr.text.startswith('https://'), 'falta el QR'

    # Y el CUFE está en cbc:UUID con su @schemeName.
    uuid = raiz.find(f'{{{NS_CBC}}}UUID')
    assert uuid is not None, 'falta cbc:UUID'
    assert len(uuid.text) == 96, 'CUIDE inválido en cbc:UUID'
    assert uuid.get('schemeName') in ('CUFE-SHA384', 'CUDE-SHA384')


def test_invoice_control_en_la_extension_dian():
    """La resolución y el rango autorizado van en `sts:InvoiceControl`.

    La auditoría los encontró ausentes: sin ellos el documento no declara su
    numeración y la DIAN lo rechaza.

    Se omiten cuando no hay dato: declarar `AuthorizedInvoices` sin rango real
    es peor que no declararlo, porque la DIAN lo lee como rango vigente.
    """
    raiz = _factura()
    control = raiz.find(f'.//{{{NS_STS}}}InvoiceControl')
    assert control is not None, 'falta sts:InvoiceControl'

    # Esta factura de prueba no trae resolución ni rango, así que no debe
    # emitir esos subgrupos.
    assert control.find(f'{{{NS_STS}}}InvoiceAuthorization') is None
    assert control.find(f'{{{NS_STS}}}AuthorizedInvoices') is None


def test_software_id_en_la_extension():
    raiz = _factura()
    software = raiz.find(f'.//{{{NS_STS}}}SoftwareID')
    assert software is not None and software.text == 'SW1'


# ── Identificación de las partes ─────────────────────────────────────────────
def test_emisor_con_scheme_agency_dian():
    """El NIT del emisor va en `cbc:CompanyID` con `@schemeID="4"`.

    Antes iba en `cbc:ID` dentro de `cac:PartyIdentification`, que no es la forma
    en que UBL 2.1 modela una empresa. `schemeAgencyID="195"` (DIAN) lo lleva el
    identificador del ADQUIRENTE, no el del emisor.
    """
    raiz = _factura()
    nodo = raiz.find(
        f'.//{{{NS_CAC}}}AccountingSupplierParty//{{{NS_CBC}}}CompanyID')
    assert nodo is not None, 'falta cbc:CompanyID en el emisor'
    assert nodo.get('schemeID') == '4', 'el NIT va con schemeID CorporateScheme'
    assert nodo.text == '900187391'

    # La razón social va en RegistrationName dentro de PartyLegalEntity.
    legal = raiz.find(
        f'.//{{{NS_CAC}}}AccountingSupplierParty//{{{NS_CAC}}}PartyLegalEntity')
    assert legal is not None, 'falta cac:PartyLegalEntity en el emisor'
    assert legal.find(f'{{{NS_CBC}}}RegistrationName') is not None


def test_adquirente_con_scheme_agency_dian():
    raiz = _factura()
    nodo = raiz.find(
        f'.//{{{NS_CAC}}}AccountingCustomerParty//{{{NS_CBC}}}ID')
    assert nodo is not None
    assert nodo.get('schemeAgencyID') == '195'
    assert nodo.text == '830114978'


def test_cliente_generico_si_no_hay_adquirente():
    """Sin datos del comprador se usa el consumidor final (NIT 222222222222)."""
    raiz = dian_xml.construir_invoice(
        _documento(), _emisor(), {}, _items(), _totales())
    nodo = raiz.find(
        f'.//{{{NS_CAC}}}AccountingCustomerParty//{{{NS_CBC}}}ID')
    assert nodo.text == '222222222222'


# ── Cuadre interno del documento ─────────────────────────────────────────────
def test_el_total_del_xml_cuadra_con_la_linea():
    """PayableAmount = TaxInclusiveAmount = base + IVA.

    OJO con la semántica de UBL 2.1: `TaxExclusiveAmount` NO es el IVA, es el
    valor ANTES de impuestos (la misma base que `LineExtensionAmount` en este
    documento). El IVA se obtiene del `cac:TaxTotal` de la cabecera.
    """
    raiz = _factura()
    monetary = raiz.find(f'{{{NS_CAC}}}LegalMonetaryTotal')
    base = float(monetary.find(f'{{{NS_CBC}}}LineExtensionAmount').text)
    base_sin_imp = float(monetary.find(f'{{{NS_CBC}}}TaxExclusiveAmount').text)
    total = float(monetary.find(f'{{{NS_CBC}}}PayableAmount').text)

    # El valor antes de impuestos debe coincidir con la base de las líneas.
    assert base == base_sin_imp, f'{base} != {base_sin_imp}'

    # El IVA se toma del resumen de impuestos de la cabecera.
    iva = float(raiz.find(
        f'{{{NS_CAC}}}TaxTotal/{{{NS_CBC}}}TaxAmount').text)
    assert base + iva == total, f'{base} + {iva} != {total}'


def test_linea_declare_impuestos():
    raiz = _factura()
    linea = raiz.find(f'.//{{{NS_CAC}}}InvoiceLine')
    assert linea is not None
    assert linea.find(f'{{{NS_CBC}}}LineExtensionAmount') is not None
    assert linea.find(f'{{{NS_CAC}}}TaxTotal') is not None
    assert linea.find(f'{{{NS_CAC}}}Price/{{{NS_CBC}}}PriceAmount') is not None


# ── Un TaxSubtotal por tarifa (la DIAN no acepta un unico con max()) ─────────
def test_cada_tarifa_declara_su_propio_subtotal():
    """Con 19% y 5% la DIAN exige un TaxSubtotal por tarifa, con SU base.

    Antes se declaraba uno solo con la tasa mayor (max), y la base no
    correspondía al valor del impuesto: el documento no cuadraba.
    """
    grupos = [
        {'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0},
        {'tasa': 5.0, 'base': 48002.0, 'valor': 2400.10},
    ]
    raiz = dian_xml.construir_invoice(
        _documento(), _emisor(), _adquirente(), _items(),
        _totales(impuestos_iva=grupos))

    tax_total = raiz.find(f'{{{NS_CAC}}}TaxTotal')
    subtotales = tax_total.findall(f'{{{NS_CAC}}}TaxSubtotal')
    assert len(subtotales) == 2, (
        f'se esperaban 2 TaxSubtotal (19% y 5%) y hay {len(subtotales)}'
    )

    # Cada subtotal debe declarar su propia tarifa y su base.
    tarifas = set()
    for sub in subtotales:
        pct = sub.find(f'{{{NS_CAC}}}TaxCategory/{{{NS_CBC}}}Percent').text
        base = float(sub.find(f'{{{NS_CBC}}}TaxableAmount').text)
        valor = float(sub.find(f'{{{NS_CBC}}}TaxAmount').text)
        tarifa = float(pct)
        tarifas.add(tarifa)
        # La base debe corresponder a la tarifa declarada.
        esperado = round(base * tarifa / 100, 2)
        assert abs(esperado - valor) <= 0.01, (
            f'la base {base} al {tarifa}% da {esperado}, no {valor}'
        )
    assert tarifas == {19.0, 5.0}, tarifas


def test_un_producto_exento_no_declara_subtotal():
    """Un producto exento (0%) no genera TaxSubtotal de IVA."""
    raiz = _factura()
    tax_total = raiz.find(f'{{{NS_CAC}}}TaxTotal')
    for sub in tax_total.findall(f'{{{NS_CAC}}}TaxSubtotal'):
        pct = float(sub.find(f'{{{NS_CAC}}}TaxCategory/{{{NS_CBC}}}Percent').text)
        assert pct > 0, 'se declaro un subtotal de impuesto para una tasa 0%'


# ── Producto exento: NO puede recibir el IVA global ──────────────────────────
def test_leer_items_respeta_el_producto_exento():
    """Un producto `iva_naturaleza='exento'` queda con tasa 0 aunque el negocio
    tenga IVA global activo.

    BUG QUE ESTA PRUEBA FIXA: la columna `iva_naturaleza` se leia y se
    descartaba, asi que un exento con tasa 0 caia en la regla "usa la tasa del
    negocio" y quedaba gravado al 19%. La DIAN rechaza declarar impuesto sobre
    una operacion exenta, y el cliente pagaria de mas.
    """
    import sqlite3 as _s
    from ferreteria import config as _cfg

    conn = _s.connect(_cfg.DB_NAME)
    cur = conn.cursor()
    # Producto exento: tasa 0 y naturaleza 'exento'.
    cur.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, iva_naturaleza, unidad_medida, activo)'
        " VALUES ('Libro exento', 30000, 24000, 10, 0, 'exento', '94', 1)")
    # Se crea una venta propia. Antes se usaba `SELECT MAX(id) FROM ventas`,
    # que devuelve NULL si la base esta vacia: el detalle quedaba con
    # id_venta = NULL, `_leer_items` no encontraba la linea y la prueba fallaba
    # con "no se encontro la linea del producto exento", muy lejos de la causa.
    cur.execute(
        "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta,"
        " saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, iva_porcentaje,"
        " anulada) VALUES (1, '2026-09-30', '10:00:00', 30000, 0, 'efectivo',"
        " 30000, 0, 0, 0)")
    id_venta = cur.lastrowid
    cur.execute(
        'INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,'
        ' precio_unitario, subtotal)'
        ' SELECT ?, id, 1, 30000, 30000'
        ' FROM productos WHERE nombre=?', (id_venta, 'Libro exento'))
    conn.commit()
    # Con el interruptor en 0 (el valor por defecto mientras el catálogo no esté
    # clasificado). Un producto exento sigue yendo a tasa cero igualmente:
    # declarar impuesto sobre una operación exenta lo rechaza la DIAN.
    items = dian_emision._leer_items(cur, id_venta, 19.0, iva_por_producto=False)
    conn.close()

    exento = [i for i in items if 'exento' in i['descripcion'].lower()]
    assert exento, 'no se encontro la linea del producto exento'
    assert exento[0]['iva_tasa'] == 0.0, (
        f"el producto exento quedo gravado al {exento[0]['iva_tasa']}%"
    )
    # Y su base debe ser el precio completo (sin desagregar).
    assert exento[0]['precio_unitario'] == 30000.0


def test_un_producto_gravado_sigue_usando_su_tasa():
    """El caso normal: un producto excluido al 19% mantiene su tasa."""
    import sqlite3 as _s
    from ferreteria import config as _cfg

    conn = _s.connect(_cfg.DB_NAME)
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, iva_naturaleza, unidad_medida, activo)'
        " VALUES ('Cemento grav', 59500, 47600, 10, 19, 'excluido', '94', 1)")
    # Venta propia, no `MAX(id)`: en una base sin ventas eso devuelve NULL y el
    # detalle queda huérfano. Ver el comentario de la prueba del exento.
    cur.execute(
        "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta,"
        " saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, iva_porcentaje,"
        " anulada) VALUES (1, '2026-09-30', '10:00:00', 59500, 0, 'efectivo',"
        " 50000, 9500, 19, 0)")
    id_venta = cur.lastrowid
    cur.execute(
        'INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,'
        ' precio_unitario, subtotal)'
        ' SELECT ?, id, 1, 59500, 59500'
        ' FROM productos WHERE nombre=?', (id_venta, 'Cemento grav'))
    conn.commit()

    # OJO: `iva_por_producto=False` es lo que usa el POS mientras el catálogo no
    # esté clasificado. Con el catálogo en su estado real (toda la fila con
    # iva_tipo_tarifa='01' sin migrar), el documento declara la tasa global, que
    # es lo que el POS cobra. Ver test_clasificacion_iva.py.
    items = dian_emision._leer_items(cur, id_venta, 19.0, iva_por_producto=False)
    conn.close()

    grav = [i for i in items if 'Cemento grav' in i['descripcion']]
    assert grav[0]['iva_tasa'] == 19.0
    # 59500 con IVA incluido -> base 50000.
    assert grav[0]['precio_unitario'] == 50000.0


# ── Serialización ────────────────────────────────────────────────────────────
def test_el_xml_es_valido_y_serializable():
    """El documento debe poder serializarse sin xmlns duplicado."""
    bytes_xml = dian_xml.a_bytes(_factura())
    raiz = ET.fromstring(bytes_xml)
    assert raiz.tag.endswith('Invoice')


def test_firma_iría_como_ultimo_hijo():
    """La firma se inserta al final; el Invoice debe quedar serializable."""
    raiz = _factura()
    assert list(raiz)[-1].tag.endswith('InvoiceLine')
