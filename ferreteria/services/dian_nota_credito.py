"""Servicio de Notas Crédito Electrónicas: emisión y reversión de una venta.

Es el ORQUESTADOR del módulo de corrección. Recibe el id de una venta YA
EMITIDA y produce el documento electrónico que la DIAN exige para revertirla:

    emitir_nota_credito(conn, id_venta, motivo_codigo, motivo_descripcion)
      1. Verifica que la venta tenga un documento POS emitido (sin eso no hay
         nada que corregir).
      2. Verifica que NO exista ya una nota crédito aceptada para esa venta
         (idempotencia: la DIAN rechaza dos notas sobre el mismo documento).
      3. Reúne emisor, adquirente e ítems del detalle de la venta.
      4. Asigna el consecutivo fiscal de la nota y calcula su CUIDE.
      5. Genera el XML UBL 2.1 REFERENCIANDO el CUIDE del documento original.
      6. Firma con el .p12 (XAdES-EPES).
      7. Envía por SendBillSync (o SendTestSetAsync en habilitación con set).
      8. Guarda el veredicto.
      9. Si la DIAN ACEPTA, revierte la venta y devuelve el stock.

PUNTO CLAVE: la venta NO se anula hasta que la nota crédito es aceptada por la
DIAN. Antes de eso el documento original sigue vigente y la venta no se toca.
Es lo contrario de lo que hacía el POS antes de este módulo, que marcaba
`anulada` sin preguntar a la DIAN.

Si el envío falla (sin red), el documento queda en 'contingencia' y la venta NO
se revierte: cuando vuelva la conexión se reintenta y, al aceptarse, se aplica
la reversión. Nunca se devuelve mercancía dos veces.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import dian_firma
from . import dian_notas
from . import dian_pos
from . import dian_soap
from .dian_emision import (
    ErrorEmision,
    _cargar_certificado,
    _leer_adquirente,
    _leer_ajustes_dian,
    _leer_emisor,
    _resumen_impuestos,
    encolar,
)
from .dian_pos import (
    TIPO_DOCUMENTO_NOTA_CREDITO,
    calcular_cuide,
    normalizar_fecha,
    normalizar_hora,
    redondear,
    url_consulta,
)

# Prefijo por defecto de la nota crédito. Es un consecutivo APARTE del POS: la
# DIAN exige que la numeración de notas crédito no se mezcle con la de los
# documentos equivalentes, porque son consecutivos de documento y de tipo
# distintos.
PREFIJO_NOTA_DEFECTO = 'NC'

# Tipo de documento que aparece en la tabla de documentos.
TIPO_NOTA_CREDITO = TIPO_DOCUMENTO_NOTA_CREDITO


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ═════════════════════════════════════════════════════
# Validaciones previas
# ═════════════════════════════════════════════════════
def _documento_original(conn, id_venta):
    """Documento POS emitido de la venta que se va a corregir.

    Returns:
        sqlite3.Row con el documento original, o None.

    Raises:
        ErrorEmision: si la venta no existe, no tiene documento o nunca se
            emitió. En esos casos no hay nada que corregir y la vía legal sería
            otra (por ejemplo, anular una venta nunca emitida, que sí se
            permite en el POS).
    """
    venta = conn.execute(
        'SELECT id, total_venta FROM ventas WHERE id = ?', (id_venta,)
    ).fetchone()
    if not venta:
        raise ErrorEmision(f'La venta #{id_venta} no existe')

    # Se busca el documento que corrige la venta, sea POS o FV.
    #
    # OJO: antes el filtro era `tipo_documento = 'POS'`, lo que dejaba fuera las
    # Facturas Electrónicas de Venta: la nota crédito de una FV no encontraba su
    # documento original y no se podía emitir. Ahora se aceptan los dos tipos
    # que generan una venta; se sigue EXCLUYENDO la propia nota crédito ('NC'),
    # porque una NC no se corrige con otra NC.
    doc = conn.execute(
        """
        SELECT id, numero, cuide, fecha_generacion, valor_total, estado,
               tipo_documento
        FROM documentos_electronicos
        WHERE id_venta = ? AND tipo_documento IN ('POS', 'FV')
        ORDER BY id DESC LIMIT 1
        """,
        (id_venta,),
    ).fetchone()
    if not doc:
        raise ErrorEmision(
            f'La venta #{id_venta} no tiene documento electrónico. Una nota '
            'crédito solo corrige documentos ya emitidos: si la venta nunca se '
            'transmitió, se puede anular normalmente en el POS.'
        )
    # OJO: se accede por ÍNDICE y no por nombre. La conexión puede venir sin
    # `row_factory = sqlite3.Row` (los tests y algunos scripts la abren en crudo)
    # y `doc['cuide']` reventaría con TypeError. Por índice funciona siempre.
    if not doc[2]:  # 2 = cuide
        raise ErrorEmision(
            f'El documento {doc[1]} no tiene CUIDE, así que la nota '
            'crédito no se puede vincular a él. Reemita el documento original.'
        )
    return doc


def _nota_ya_aceptada(conn, id_venta):
    """True si ya existe una nota crédito ACEPTADA para esta venta.

    Idempotencia: la DIAN rechaza dos notas sobre el mismo documento, y
    revertir el stock dos veces descuadraría el inventario.
    """
    return conn.execute(
        """
        SELECT 1 FROM documentos_electronicos
        WHERE id_venta = ? AND tipo_documento = ?
          AND estado IN ('aceptado', 'enviado', 'firmado', 'contingencia')
        LIMIT 1
        """,
        (id_venta, TIPO_NOTA_CREDITO),
    ).fetchone() is not None


# ═════════════════════════════════════════════════════
# Datos de la nota
# ═════════════════════════════════════════════════════
def _leer_items_creditados(cursor, id_venta, iva_porcentaje_venta):
    """Líneas de la nota crédito, con el mismo criterio de desagregación que la
    venta original (precio final con IVA -> base sin IVA).

    Una nota crédito DEBE cuadrar con el documento que corrige: se emiten
    exactamente las mismas líneas y con las mismas tasas. Si el producto cambió
    su tasa de IVA desde la venta, la base no coincidiría y la DIAN rechazaría
    el documento.
    """
    filas = cursor.execute(
        """
        SELECT dv.cantidad, dv.precio_unitario, p.nombre, p.dimensiones,
               COALESCE(p.unidad_medida, '94'), COALESCE(p.codigo_dian, ''),
               COALESCE(p.iva_tasa, 0), COALESCE(p.iva_naturaleza, 'excluido'),
               COALESCE(p.iva_tipo_tarifa, '00')
        FROM detalle_ventas dv
        JOIN productos p ON dv.id_producto = p.id
        WHERE dv.id_venta = ?
        ORDER BY dv.id
        """,
        (id_venta,),
    ).fetchall()
    if not filas:
        raise ErrorEmision(f'La venta #{id_venta} no tiene líneas que descontar')

    items = []
    for cantidad, precio_final, nombre, dimensiones, unidad, codigo_dian, \
            iva_tasa, naturaleza, tipo_tarifa in filas:
        cantidad = float(cantidad or 0)
        precio_final = float(precio_final or 0)
        iva_tasa = float(iva_tasa or 0)
        naturaleza = str(naturaleza or 'excluido').strip().lower()

        # Mismo criterio que el documento que corrige (dian_emision._leer_items).
        # Si aquí no coincide, la nota declara un impuesto distinto al de la
        # factura original y la DIAN rechaza la corrección.
        #
        # OJO con `not iva_tasa`: en Python `not 0` es True, así que un producto
        # de tasa CERO caía en la rama siguiente y se le ponía la tasa general.
        # Una nota crédito sobre un artículo excluido declaraba IVA 19% sobre un
        # impuesto que nunca se cobró. Se compara con `== 0`.
        #
        # Tampoco se puede usar `iva_naturaleza` sola: vale 'excluido' por
        # defecto en todo el catálogo, donde significa "gravado".
        if naturaleza in ('exento', 'no_sujeto') or iva_tasa == 0:
            iva_tasa = 0.0
        elif naturaleza == 'excluido_iva' or tipo_tarifa in ('01', '02', '03'):
            iva_tasa = 0.0
        elif iva_porcentaje_venta:
            iva_tasa = float(iva_porcentaje_venta or 0)

        precio_base = (precio_final / (1 + iva_tasa / 100)) if iva_tasa else precio_final
        precio_base = redondear(precio_base)
        base = redondear(cantidad * precio_base)

        descripcion = str(nombre or 'Producto')
        if dimensiones:
            descripcion = f'{descripcion} ({dimensiones})'

        items.append({
            'descripcion': descripcion,
            'cantidad': cantidad,
            'precio_unitario': precio_base,
            'unidad': str(unidad or dian_pos.UNIDAD_POR_DEFECTO),
            'codigo': str(codigo_dian or '').strip(),
            'iva_tasa': iva_tasa,
            'inc_tasa': 0,
            'base': base,
        })
    return items


def _resumen(items):
    """Totales de la nota, con el IVA redondeado POR LÍNEA (igual que el POS)."""
    base_total = redondear(sum(i['base'] for i in items))
    iva_total = redondear(sum(
        redondear(i['base'] * i['iva_tasa'] / 100) for i in items
    ))
    inc_total = redondear(sum(
        redondear(i['base'] * i['inc_tasa'] / 100) for i in items
    ))
    return {
        'line_extension_amount': base_total,
        'tax_exclusive_amount': base_total,
        'tax_inclusive_amount': base_total + iva_total + inc_total,
        'payable_amount': base_total + iva_total + inc_total,
        'iva_valor': iva_total,
        'inc_valor': inc_total,
        'impuestos_iva': _resumen_impuestos(items, 'iva_tasa'),
        'impuestos_inc': _resumen_impuestos(items, 'inc_tasa'),
    }


# ═════════════════════════════════════════════════════
# Numeración
# ═════════════════════════════════════════════════════
def _asignar_numero(conn, ajustes):
    """Consecutivo fiscal de la nota crédito (independiente del POS).

    Usa la columna `dian_consecutivo` de la configuración igual que el POS, pero
    con prefijo 'NC'. Se comparte el contador para que no haya dos documentos
    con el mismo número; como el prefijo es distinto, 'NC-15' y 'POS-15' no
    colisionan en la restricción UNIQUE(prefijo, numero).
    """
    prefijo = PREFIJO_NOTA_DEFECTO
    siguiente = int(ajustes.get('consecutivo') or 1)

    hasta = int(ajustes.get('rango_hasta') or 0)
    if hasta and siguiente > hasta:
        raise ErrorEmision(
            f'El consecutivo {siguiente} superó el rango autorizado por la '
            f'resolución (hasta {hasta}). Solicite una nueva numeración a la DIAN.'
        )

    conn.execute('UPDATE configuracion SET dian_consecutivo = ? WHERE id = 1',
                 (siguiente + 1,))
    return prefijo, f'{prefijo}-{siguiente}', siguiente


# ═════════════════════════════════════════════════════
# Reversión de la venta e inventario
# ═════════════════════════════════════════════════════
def revertir_venta(conn, id_venta, usuario, motivo):
    """Marca la venta como revertida y devuelve el stock.

    SOLO se llama cuando la nota crédito fue ACEPTADA por la DIAN. Hasta ese
    momento la venta sigue vigente.

    Idempotente: si la venta ya está revertida, no devuelve el stock dos veces.
    """
    venta = conn.execute(
        'SELECT anulada FROM ventas WHERE id = ?', (id_venta,)
    ).fetchone()
    if not venta:
        raise ErrorEmision(f'La venta #{id_venta} no existe')
    if venta[0]:
        # Ya revertida: no se vuelve a tocar el stock.
        return False

    detalles = conn.execute(
        'SELECT id_producto, cantidad FROM detalle_ventas WHERE id_venta = ?',
        (id_venta,),
    ).fetchall()
    for id_producto, cantidad in detalles:
        conn.execute(
            'UPDATE productos SET stock_actual = stock_actual + ? WHERE id = ?',
            (cantidad, id_producto))
        conn.execute(
            """
            INSERT INTO movimientos_inventario
                (id_producto, tipo, cantidad, motivo, usuario, fecha)
            VALUES (?, 'entrada', ?, ?, ?, ?)
            """,
            (id_producto, cantidad,
             f'Devolución por nota crédito', usuario, _ahora()),
        )

    conn.execute(
        'UPDATE ventas SET anulada = 1, motivo_anulacion = ?,'
        ' saldo_pendiente = 0 WHERE id = ?',
        (f'Revertida por nota crédito: {motivo}', id_venta))
    return True


# ═════════════════════════════════════════════════════
# Emisión
# ═════════════════════════════════════════════════════
def emitir_nota_credito(conn, id_venta, motivo_codigo='1', motivo_descripcion='',
                        contingencia=False, usuario='sistema'):
    """Emite la Nota Crédito Electrónica de una venta emitida.

    Args:
        conn: conexión SQLite abierta.
        id_venta: id de la venta a revertir.
        motivo_codigo: código del catálogo de motivos (ver MOTIVOS_NOTA_CREDITO).
        motivo_descripcion: texto libre que explica el ajuste.
        contingencia: si es True, genera y firma SIN intentar enviar (cuando
            el POS ya sabe que no hay red).
        usuario: quién solicita la corrección; queda en el movimiento de
            inventario y en la auditoría.

    Returns:
        dict con numero, cuide, estado, id_documento, mensaje y si la venta
        quedó revertida.

    Raises:
        ErrorEmision: si la venta no tiene documento emitido, ya tiene nota, o
            falta la configuración / el certificado.
    """
    cursor = conn.cursor()

    # ── 1. Validaciones ──────────────────────────────────────────────────────
    original = _documento_original(conn, id_venta)
    if _nota_ya_aceptada(conn, id_venta):
        raise ErrorEmision(
            f'La venta #{id_venta} ya tiene una nota crédito. No se puede emitir '
            'otra: la DIAN rechaza dos notas sobre el mismo documento y el '
            'stock se devolvería dos veces.'
        )

    codigos_validos = {c for c, _ in dian_notas.MOTIVOS_NOTA_CREDITO}
    if str(motivo_codigo) not in codigos_validos:
        raise ErrorEmision(
            f'Motivo de nota crédito inválido: {motivo_codigo}. Debe ser uno '
            f'de {sorted(codigos_validos)}.'
        )

    ajustes = _leer_ajustes_dian(cursor)
    emisor = _leer_emisor(cursor)

    venta = cursor.execute(
        """
        SELECT v.id_cliente, COALESCE(v.iva_porcentaje, 0), v.fecha_dia, v.hora
        FROM ventas v WHERE v.id = ?
        """,
        (id_venta,),
    ).fetchone()
    adquirente = _leer_adquirente(cursor, venta[0])
    items = _leer_items_creditados(cursor, id_venta, venta[1])
    totales = _resumen(items)

    # ── 2. Numeración y CUIDE ────────────────────────────────────────────────
    prefijo, numero, _consecutivo = _asignar_numero(conn, ajustes)

    fecha = normalizar_fecha(venta[2])
    hora = normalizar_hora(venta[3])

    cuide = calcular_cuide(
        num_documento=numero,
        fecha=fecha,
        hora=hora,
        val_imp1=totales['iva_valor'],
        val_imp2=totales['inc_valor'],
        val_total=totales['payable_amount'],
        nit=emisor['nit'],
        tipo_documento=TIPO_NOTA_CREDITO,
        clave_tecnica=ajustes['clave_tecnica'],
        tipo_ambiente=ajustes['ambiente'],
    )
    qr = url_consulta(cuide, fecha=fecha, nit=emisor['nit'],
                      total=totales['payable_amount'])

    # ── 3. XML ───────────────────────────────────────────────────────────────
    documento = {
        'numero': numero, 'fecha': fecha, 'hora': hora, 'cuide': cuide,
        'tipo_ambiente': ajustes['ambiente'], 'moneda': 'COP',
        'tipo_operacion': '10', 'valor_total': totales['payable_amount'],
        # Referencia al documento que se corrige (obligatoria).
        'documento_referido': id_venta,
        'numero_referido': original[1],          # 1 = numero
        'cuide_referido': original[2],           # 2 = cuide
        'fecha_referido': original[3],           # 3 = fecha_generacion
        'tipo_documento_referido': original[6] or 'POS',  # 6 = tipo_documento
        'motivo_codigo': motivo_codigo,
        'motivo_descripcion': motivo_descripcion,
        'tipo_pago': 'efectivo', 'id_venta': id_venta,
    }
    extras = {
        'software_id': ajustes['software_id'],
        'software_security_code': ajustes['software_security_code'],
    }

    raiz = dian_notas.construir_nota_credito(
        documento, emisor, adquirente, items, totales, extras)
    xml_sin_firma = dian_notas.a_texto(raiz)

    # ── 4. Firma ─────────────────────────────────────────────────────────────
    certificado = _cargar_certificado(ajustes)
    xml_firmado = dian_firma.firmar_bytes(
        dian_notas.a_bytes(raiz), certificado, id_documento=numero)

    # ── 5. Persistencia ANTES de enviar ──────────────────────────────────────
    cursor.execute(
        """
        INSERT INTO documentos_electronicos
            (id_venta, tipo_documento, prefijo, numero, cuide, fecha_generacion,
             hora_generacion, valor_total, valor_iva, valor_inc, xml,
             xml_firmado, qr_url, modo, estado, contingencia, intentos,
             documento_referido, motivo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'firmado', ?, 0, ?, ?)
        """,
        (id_venta, TIPO_NOTA_CREDITO, prefijo, numero, cuide, fecha, hora,
         totales['payable_amount'], totales['iva_valor'], totales['inc_valor'],
         xml_sin_firma, xml_firmado.decode('utf-8'), qr, ajustes['modo'],
         1 if contingencia else 0,
         original[2], motivo_descripcion or motivo_codigo),
    )
    id_documento = cursor.lastrowid
    conn.commit()

    # ── 6. Envío ─────────────────────────────────────────────────────────────
    if contingencia:
        return _pendiente(conn, id_documento, id_venta, numero, cuide, qr,
                          'Nota crédito generada en contingencia (sin conexión). '
                          'La venta NO se revierte hasta que la DIAN la acepte.')

    return _enviar(conn, id_documento, id_venta, numero, cuide, xml_firmado,
                   ajustes, qr, motivo_descripcion or motivo_codigo,
                   usuario=usuario)


def _enviar(conn, id_documento, id_venta, numero, cuide, xml_firmado, ajustes,
            qr, motivo, usuario):
    """Envía la nota y, si la DIAN la acepta, revierte la venta.

    Este es el punto donde la venta se toca: solo tras un veredicto de
    aceptación. Un rechazo o una caída de red dejan la venta intacta.
    """
    cursor = conn.cursor()
    nombre_archivo = f'{numero}.xml'

    try:
        if ajustes['test_set_id'] and ajustes['ambiente'] == dian_pos.AMBIENTE_HABILITACION:
            respuesta = dian_soap.send_test_set_async(
                xml_firmado, nombre_archivo, ajustes['test_set_id'],
                ambiente=ajustes['ambiente'])
        else:
            respuesta = dian_soap.send_bill_sync(
                xml_firmado, nombre_archivo, ambiente=ajustes['ambiente'])
    except dian_soap.ErrorSOAP as error:
        return _contingencia(conn, id_documento, id_venta, numero, cuide,
                             f'Sin conexión con la DIAN: {error}', usuario)

    if not respuesta.exito:
        return _contingencia(conn, id_documento, id_venta, numero, cuide,
                             respuesta.mensaje, usuario)

    estado = dian_pos.estado_desde_respuesta(respuesta.codigo)

    cursor.execute(
        """
        UPDATE documentos_electronicos
        SET estado = ?, respuesta_dian = ?, codigo_dian = ?, descripcion_dian = ?,
            track_id = ?, intentos = intentos + 1, fecha_envio = ?,
            fecha_respuesta = ?
        WHERE id = ?
        """,
        (estado, respuesta.xml_respuesta, respuesta.codigo, respuesta.mensaje,
         respuesta.track_id, _ahora(), _ahora(), id_documento),
    )

    revertida = False
    if estado == 'aceptado':
        # La DIAN aceptó: AHORA sí se revierte la venta y se devuelve el stock.
        revertida = revertir_venta(conn, id_venta, usuario, motivo)
        cursor.execute(
            'UPDATE ventas SET dian_estado = ?, dian_descripcion = ? WHERE id = ?',
            ('revertido', f'Revertida por nota crédito {numero}', id_venta),
        )

    conn.commit()

    return {
        'id_documento': id_documento,
        'numero': numero,
        'cuide': cuide,
        'estado': estado,
        'codigo_dian': respuesta.codigo,
        'qr_url': qr,
        'venta_revertida': revertida,
        'mensaje': (
            f'Nota crédito {numero} aceptada. La venta #{id_venta} se revirtió y '
            'el stock volvió al inventario.'
            if estado == 'aceptado' and revertida else
            f'Nota crédito {numero} ACEPTADA por la DIAN.'
            if estado == 'aceptado' else
            f'Nota crédito {numero} RECHAZADA: {respuesta.mensaje}. '
            'La venta NO se revirtió.'
        ),
    }


def _contingencia(conn, id_documento, id_venta, numero, cuide, motivo, usuario):
    """Deja la nota en contingencia y encola el reenvío.

    La venta NO se revierte: el documento original sigue vigente hasta que la
    DIAN acepte la corrección.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE documentos_electronicos
        SET estado = 'contingencia', contingencia = 1, ultimo_error = ?,
            tipo_evento = ?, descripcion_evento = ?
        WHERE id = ?
        """,
        (motivo, dian_pos.TIPO_EVENTO_CONTINGENCIA,
         dian_pos.DESCRIPCION_EVENTO_CONTINGENCIA, id_documento),
    )
    ya_encolado = cursor.execute(
        "SELECT 1 FROM cola_dian WHERE id_documento = ? AND estado = 'pendiente'"
        " AND operacion = 'enviar'",
        (id_documento,),
    ).fetchone()
    if not ya_encolado:
        encolar(cursor, id_documento, 'enviar', intentos=0)
    conn.commit()

    return {
        'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
        'estado': 'contingencia', 'venta_revertida': False,
        'mensaje': (
            f'Nota crédito en CONTINGENCIA: {motivo}. La venta #{id_venta} NO se '
            'revierte hasta que la DIAN acepte la nota.'
        ),
    }


def _pendiente(conn, id_documento, id_venta, numero, cuide, qr, mensaje):
    """Nota generada y firmada, encolada para enviar. Venta intacta."""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE documentos_electronicos SET contingencia = 1, estado = 'contingencia',"
        " tipo_evento = ?, descripcion_evento = ? WHERE id = ?",
        (dian_pos.TIPO_EVENTO_CONTINGENCIA,
         dian_pos.DESCRIPCION_EVENTO_CONTINGENCIA, id_documento),
    )
    encolar(cursor, id_documento, 'enviar', intentos=0)
    conn.commit()
    return {
        'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
        'estado': 'contingencia', 'venta_revertida': False, 'qr_url': qr,
        'mensaje': mensaje,
    }
