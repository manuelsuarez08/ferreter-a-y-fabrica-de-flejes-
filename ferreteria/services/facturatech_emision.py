"""Emisión de documentos electrónicos a través de Facturatech.

Facturatech genera el UBL, lo firma y lo envía a la DIAN. Este módulo hace de
puente: lee la venta con los mismos helpers que `dian_emision`, arma el XML
simplificado (`facturatech_xml`), lo sube (`facturatech_ws`) y guarda el
resultado en `documentos_electronicos` con `modo = 'facturatech'`.

Se activa solo si están definidas las variables FACTURATECH_URL,
FACTURATECH_USUARIO y FACTURATECH_PASSWORD; si no, la app sigue usando el flujo
directo con la DIAN.
"""
import os
import time
from datetime import datetime, timedelta

from . import dian_pos, dian_series, facturatech_ws as ws, facturatech_xml as fxml
from .dian_pos import normalizar_fecha, redondear

MODO = 'facturatech'
URL_CONSULTA_DIAN = 'https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey='
ESPERA_FIRMA_SEGUNDOS = (2, 3, 5)

# (forma de pago Tabla 26, medio de pago Tabla 5) según `ventas.tipo_pago`.
PAGOS = {
    'efectivo': ('1', '10'), 'contado': ('1', '10'),
    'tarjeta': ('1', '48'), 'transferencia': ('1', '47'),
    'credito': ('2', '30'), 'abono': ('2', '30'),
}


def activo():
    return all(os.environ.get(k) for k in (
        'FACTURATECH_URL', 'FACTURATECH_USUARIO', 'FACTURATECH_PASSWORD'))


def _error_emision():
    from .dian_emision import ErrorEmision
    return ErrorEmision


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _ambiente():
    return '1' if '/pro' in os.environ.get('FACTURATECH_URL', '') else '2'


def _guardar_estado(conn, id_documento, id_venta, numero, estado, mensaje,
                    codigo='', transaccion=None, cuide=None):
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE documentos_electronicos
        SET estado = ?, codigo_dian = ?, descripcion_dian = ?,
            track_id = COALESCE(?, track_id), cuide = COALESCE(?, cuide),
            intentos = intentos + 1, fecha_envio = COALESCE(fecha_envio, ?),
            fecha_respuesta = ?, ultimo_error = ?
        WHERE id = ?
        """,
        (estado, codigo, str(mensaje or '')[:500], transaccion, cuide, _ahora(),
         _ahora(), None if estado in ('aceptado', 'firmado') else str(mensaje)[:500],
         id_documento))
    cursor.execute(
        """
        UPDATE ventas SET numero_dian = ?, dian_estado = ?,
            dian_cuide = COALESCE(?, dian_cuide), dian_descripcion = ?,
            dian_fecha_emision = ?
        WHERE id = ?
        """,
        (numero, estado, cuide, str(mensaje or '')[:500], _ahora(), id_venta))
    conn.commit()


def _resolver_firma(conn, id_documento, id_venta, numero, prefijo, folio,
                    transaccion, esperar=True):
    """Consulta el estado hasta que Facturatech firme (o se agote la espera)."""
    pausas = ESPERA_FIRMA_SEGUNDOS if esperar else (0,)
    resultado = None
    for pausa in pausas:
        if pausa:
            time.sleep(pausa)
        resultado = ws.document_status(transaccion)
        if resultado['estado'] == 'SIGNED_XML' or not resultado['ok']:
            break
    if resultado['estado'] == 'SIGNED_XML':
        cufe = ''
        try:
            cufe = ws.download_cufe(prefijo, folio)['datos']
        except ws.ErrorFacturatech:
            pass
        _guardar_estado(conn, id_documento, id_venta, numero, 'aceptado',
                        f'Documento {numero} firmado por Facturatech',
                        resultado['codigo'], transaccion, cufe or None)
        if cufe:
            conn.execute('UPDATE documentos_electronicos SET qr_url = ? WHERE id = ?',
                         (URL_CONSULTA_DIAN + cufe, id_documento))
            conn.commit()
        return 'aceptado', f'Documento {numero} aceptado', cufe
    if not resultado['ok']:
        _guardar_estado(conn, id_documento, id_venta, numero, 'rechazado',
                        resultado['mensaje'], resultado['codigo'], transaccion)
        return 'rechazado', resultado['mensaje'], ''
    _guardar_estado(conn, id_documento, id_venta, numero, 'firmado',
                    'En proceso en Facturatech', resultado['codigo'], transaccion)
    return 'firmado', 'Documento en proceso en Facturatech', ''


def _error_de_cuenta(carga):
    """401/409 y 'usuario/password incorrecto' son de la cuenta, no del documento."""
    mensaje = carga['mensaje'].lower()
    return (carga['codigo'] in ('401', '409') or 'password integrador' in mensaje
            or 'nombre de usuario' in mensaje)


def _subir(conn, id_documento, id_venta, numero, prefijo, folio, xml_bytes,
           contingencia_previa=False):
    from .dian_emision import encolar
    try:
        carga = ws.upload_document(xml_bytes)
        if not carga['ok'] and _error_de_cuenta(carga):
            raise ws.ErrorFacturatech(f"Cuenta Facturatech: {carga['mensaje']}")
    except ws.ErrorFacturatech as error:
        # Sin conexión no se sabe si llegó: queda en contingencia y se reintenta.
        _guardar_estado(conn, id_documento, id_venta, numero, 'contingencia',
                        str(error))
        if not contingencia_previa:
            encolar(conn.cursor(), id_documento, 'enviar')
            conn.commit()
        return {'id_documento': id_documento, 'numero': numero, 'cuide': '',
                'estado': 'contingencia', 'contingencia': True, 'qr_url': '',
                'mensaje': f'Sin conexión con Facturatech: documento en '
                           f'CONTINGENCIA. {error}'}
    if not carga['ok']:
        _guardar_estado(conn, id_documento, id_venta, numero, 'rechazado',
                        carga['mensaje'], carga['codigo'])
        return {'id_documento': id_documento, 'numero': numero, 'cuide': '',
                'estado': 'rechazado', 'contingencia': False, 'qr_url': '',
                'codigo_dian': carga['codigo'],
                'mensaje': f'Documento {numero} RECHAZADO: {carga["mensaje"]}'}
    estado, mensaje, cufe = _resolver_firma(
        conn, id_documento, id_venta, numero, prefijo, folio, carga['transaccion'])
    return {'id_documento': id_documento, 'numero': numero, 'cuide': cufe,
            'estado': estado, 'contingencia': False,
            'qr_url': URL_CONSULTA_DIAN + cufe if cufe else '',
            'codigo_dian': carga['codigo'], 'mensaje': mensaje}


def emitir_venta(conn, id_venta, forzar=False, contingencia=False):
    from . import dian_emision as de
    ErrorEmision = _error_emision()
    cursor = conn.cursor()
    venta = cursor.execute(
        """
        SELECT v.fecha_dia, v.hora, v.id_cliente, COALESCE(v.anulada, 0),
               COALESCE(v.iva_porcentaje, 0), COALESCE(v.tipo_operacion, '10'),
               COALESCE(v.tipo_pago, 'efectivo'),
               COALESCE(v.tipo_documento_dian, 'POS')
        FROM ventas v WHERE v.id = ?
        """, (id_venta,)).fetchone()
    if not venta:
        raise ErrorEmision(f'La venta #{id_venta} no existe')
    fecha_dia, hora, id_cliente, anulada, iva_venta, tipo_op, tipo_pago, tipo_doc = venta
    if anulada:
        raise ErrorEmision(
            f'La venta #{id_venta} está anulada: no se emite documento electrónico')

    existente = de._buscar_documento(cursor, id_venta)
    if existente and not forzar:
        id_doc, numero, cuide, estado = existente
        return {'id_documento': id_doc, 'numero': numero, 'cuide': cuide,
                'estado': estado, 'contingencia': estado == 'contingencia',
                'mensaje': 'La venta ya tiene documento electrónico',
                'qr_url': URL_CONSULTA_DIAN + cuide if cuide else ''}

    emisor = de._leer_emisor(cursor)
    adquirente = de._leer_adquirente(cursor, id_cliente)
    por_producto = cursor.execute(
        'SELECT COALESCE(iva_por_producto, 0) FROM configuracion WHERE id = 1'
    ).fetchone()
    items = de._leer_items(cursor, id_venta, iva_venta,
                           bool(por_producto and por_producto[0]))

    base = redondear(sum(i['base'] for i in items))
    iva = redondear(sum(redondear(i['base'] * i['iva_tasa'] / 100) for i in items))
    inc = redondear(sum(redondear(i['base'] * i['inc_tasa'] / 100) for i in items))
    total = redondear(base + iva + inc)

    tipo_doc = (tipo_doc or 'POS').strip().upper()
    if tipo_doc not in dian_series.TIPOS_DOCUMENTO:
        tipo_doc = 'POS'
    serie = dian_series.leer_serie(conn, tipo_doc)
    if forzar and existente:
        numero = existente[1]
        prefijo, _, folio = numero.rpartition('-')
    else:
        try:
            reserva = dian_series.reservar_numero(conn, tipo_doc, estricto=True)
        except dian_series.ErrorSerie as error:
            raise ErrorEmision(str(error)) from error
        prefijo, folio = reserva['prefijo'], str(reserva['consecutivo'])
        numero = reserva['numero']

    forma, medio = PAGOS.get(str(tipo_pago).strip().lower(), ('1', '10'))
    fecha = normalizar_fecha(fecha_dia)
    documento = {
        'prefijo': prefijo, 'folio': folio, 'tipo_ambiente': _ambiente(),
        'tipo_operacion': tipo_op, 'forma_pago': forma, 'medio_pago': medio,
        'numero': numero,
    }
    if forma == '2':
        documento['fecha_vencimiento'] = (
            datetime.strptime(fecha, '%Y-%m-%d') + timedelta(days=30)
        ).strftime('%Y-%m-%d')
    if 'ws-pos' in os.environ.get('FACTURATECH_URL', ''):
        documento['pos'] = {'codigo_venta': str(id_venta)}
    serie = serie or {}
    resolucion = {
        'numero': serie.get('numero_resolucion'),
        'fecha_inicio': (serie.get('fecha_inicio')
                         or os.environ.get('FACTURATECH_RESOLUCION_INICIO')),
        'fecha_fin': serie.get('fecha_vencimiento'),
        'desde': serie.get('rango_desde'), 'hasta': serie.get('rango_hasta'),
    }
    totales = {'line_extension_amount': base, 'payable_amount': total}

    try:
        # ICC_9 debe ser igual al prefijo de la resolución (regla FAB10a).
        xml_bytes = fxml.a_bytes(fxml.construir_factura(
            documento, dict(emisor, prefijo=prefijo), adquirente, items, totales,
            resolucion))
    except ValueError as error:
        raise ErrorEmision(f'No se pudo armar el documento: {error}') from error

    if existente:
        id_documento = existente[0]
        cursor.execute(
            'UPDATE documentos_electronicos SET xml = ?, xml_firmado = ?, estado = ? '
            'WHERE id = ?', (xml_bytes.decode('utf-8'),) * 2 + ('firmado', id_documento))
    else:
        id_documento = de._guardar_documento(cursor, {
            'id_venta': id_venta, 'tipo': tipo_doc, 'prefijo': prefijo,
            'numero': numero, 'cuide': '', 'fecha': fecha,
            'hora': dian_pos.normalizar_hora(hora), 'total': total, 'iva': iva,
            'inc': inc, 'xml': xml_bytes.decode('utf-8'),
            'xml_firmado': xml_bytes.decode('utf-8'), 'qr': '', 'modo': MODO,
            'estado': 'firmado', 'contingencia': 1 if contingencia else 0})
    conn.commit()

    if contingencia:
        _guardar_estado(conn, id_documento, id_venta, numero, 'contingencia',
                        'Emisión en contingencia (sin conexión)')
        de.encolar(cursor, id_documento, 'enviar')
        conn.commit()
        return {'id_documento': id_documento, 'numero': numero, 'cuide': '',
                'estado': 'contingencia', 'contingencia': True, 'qr_url': '',
                'mensaje': 'Documento generado en CONTINGENCIA. Se enviará '
                           'cuando vuelva la conexión.'}
    return _subir(conn, id_documento, id_venta, numero, prefijo, folio, xml_bytes)


def _documento(conn, id_documento):
    ErrorEmision = _error_emision()
    fila = conn.execute(
        'SELECT id, id_venta, numero, prefijo, xml, track_id, estado, '
        'COALESCE(contingencia, 0) FROM documentos_electronicos WHERE id = ?',
        (id_documento,)).fetchone()
    if not fila:
        raise ErrorEmision(f'El documento #{id_documento} no existe')
    return fila


def es_de_facturatech(conn, id_documento):
    fila = conn.execute(
        'SELECT modo FROM documentos_electronicos WHERE id = ?',
        (id_documento,)).fetchone()
    return bool(fila and fila[0] == MODO)


def reenviar_documento(conn, id_documento):
    """Reintenta: si ya hay transacción solo consulta; si no, sube el XML."""
    id_doc, id_venta, numero, prefijo, xml, transaccion, estado, cont = \
        _documento(conn, id_documento)
    folio = numero.rpartition('-')[2]
    if estado == 'aceptado':
        return {'id_documento': id_doc, 'numero': numero, 'estado': 'aceptado',
                'contingencia': False, 'mensaje': 'El documento ya fue aceptado'}
    if transaccion:
        estado, mensaje, cufe = _resolver_firma(
            conn, id_doc, id_venta, numero, prefijo, folio, transaccion)
        return {'id_documento': id_doc, 'numero': numero, 'cuide': cufe,
                'estado': estado, 'contingencia': False, 'mensaje': mensaje}
    return _subir(conn, id_doc, id_venta, numero, prefijo, folio,
                  xml.encode('utf-8'), contingencia_previa=True)


def consultar_estado(conn, id_documento):
    id_doc, id_venta, numero, prefijo, _xml, transaccion, estado, _c = \
        _documento(conn, id_documento)
    if not transaccion:
        return {'id_documento': id_doc, 'numero': numero, 'estado': estado,
                'mensaje': 'El documento aún no se ha enviado a Facturatech'}
    nuevo, mensaje, cufe = _resolver_firma(
        conn, id_doc, id_venta, numero, prefijo, numero.rpartition('-')[2],
        transaccion, esperar=False)
    return {'id_documento': id_doc, 'numero': numero, 'cuide': cufe,
            'estado': nuevo, 'mensaje': mensaje, 'errores': []}


def descargar_pdf(conn, id_documento):
    """PDF de la representación gráfica, en bytes."""
    import base64
    _, _, numero, prefijo, *_ = _documento(conn, id_documento)
    resultado = ws.download_pdf(prefijo, numero.rpartition('-')[2])
    if not resultado['ok'] or not resultado['datos']:
        raise ws.ErrorFacturatech(resultado['mensaje'] or 'PDF no disponible')
    return base64.b64decode(resultado['datos'])
