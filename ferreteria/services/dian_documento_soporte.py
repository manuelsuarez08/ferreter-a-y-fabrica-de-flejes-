"""Servicio del Documento Soporte a No Obligados a Facturar.

Respalda compras hechas a PROVEEDORES INFORMALES: personas naturales que venden
materiales (arena, balastro) o servicios (transporte, maquinaria) y que NO
están obligadas a emitir factura electrónica.

Por qué existe: esas compras son reales y las necesita el inventario, pero el
proveedor no puede entregar una factura validada por la DIAN. El documento
soporte es el puente: registra la operación con la trazabilidad de un documento
electrónico (XML firmado, CUIDE, transmisión) para que la compra tenga soporte
ante un auditor, sin que el proveedor tenga que convertirse en facturador.

NO es una factura: no substitutes la factura electrónica de un proveedor
obligado, y no genera IVA a favor del comprador que este no pueda descontar. Su
finalidad es documental.

Al igual que la nota crédito, el flujo es el mismo de siempre: generar -> firmar
-> enviar -> registrar el veredicto, con contingencia y cola de reintentos.
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
    _leer_ajustes_dian,
    _leer_emisor,
    _resumen_impuestos,
    encolar,
)
from .dian_pos import calcular_cuide, normalizar_fecha, normalizar_hora, redondear, url_consulta

PREFIJO_SOPORTE_DEFECTO = 'DS'
TIPO_DOCUMENTO_SOPORTE = 'DS'


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _leer_proveedor(cursor, id_proveedor):
    """Datos fiscales del proveedor informal.

    El proveedor no es un emisor suyo: la ferretería emite el soporte y lo
    referencia. Se leen sus datos para el bloque del receptor del XML.
    """
    fila = cursor.execute(
        """
        SELECT nombre, tipo_documento, numero_documento, telefono, direccion,
               COALESCE(municipio, ''), COALESCE(departamento, ''),
               COALESCE(notas, '')
        FROM proveedores_informales WHERE id = ? AND activo = 1
        """,
        (id_proveedor,),
    ).fetchone()
    if not fila:
        raise ErrorEmision(
            f'El proveedor #{id_proveedor} no existe o está inactivo.'
        )
    nombre, tipo_doc, num_doc, tel, dir_, municipio, departamento, _notas = fila
    return {
        'nombre': str(nombre or 'Proveedor'),
        'tipo_documento': str(tipo_doc or 'CC'),
        'numero_documento': dian_pos.solo_digitos(num_doc),
        'telefono': str(tel or ''),
        'direccion': str(dir_ or ''),
        'municipio': str(municipio or dian_pos.MUNICIPIO_POR_DEFECTO),
        'departamento': str(departamento or dian_pos.DEPARTAMENTO_POR_DEFECTO),
        'pais': dian_pos.PAIS_POR_DEFECTO,
        'regimen_fiscal': 'No Responsable de IVA',
        'responsabilidades': ['R-99-PN'],
    }


def _leer_items(cursor, items_payload, iva_porcentaje):
    """Normaliza las líneas de la compra descrita en el JSON del endpoint.

    items_payload: lista de dicts con cantidad, id_producto (opcional) y
        precio_unitario. El precio es el FINAL que se le pagó al proveedor; se
        desagrega igual que en la venta para que la base gravable del soporte
        cuadre con el valor declarado.
    """
    items = []
    for entrada in items_payload:
        cantidad = float(entrada.get('cantidad') or 0)
        precio_final = float(entrada.get('precio_unitario') or 0)
        if cantidad <= 0:
            raise ErrorEmision('La cantidad de una línea del soporte debe ser mayor que cero.')
        if precio_final < 0:
            raise ErrorEmision('El precio de una línea del soporte no puede ser negativo.')

        iva_tasa = entrada.get('iva_tasa')
        iva_tasa = float(iva_porcentaje if iva_tasa is None else iva_tasa)
        naturaleza = str(entrada.get('iva_naturaleza') or 'excluido').strip().lower()
        if naturaleza in ('exento', 'excluido_iva', 'no_sujeto'):
            iva_tasa = 0.0

        precio_base = (precio_final / (1 + iva_tasa / 100)) if iva_tasa else precio_final
        precio_base = redondear(precio_base)
        base = redondear(cantidad * precio_base)

        descripcion = str(entrada.get('descripcion') or '')
        if entrada.get('id_producto'):
            fila = cursor.execute(
                'SELECT nombre, dimensiones, unidad_medida, codigo_dian'
                ' FROM productos WHERE id = ?',
                (entrada['id_producto'],),
            ).fetchone()
            if fila:
                descripcion = fila[0] or descripcion
                if fila[1]:
                    descripcion = f'{descripcion} ({fila[1]})'
                unidad = fila[2]
                codigo = fila[3]
            else:
                unidad, codigo = '94', ''
        else:
            unidad = entrada.get('unidad') or '94'
            codigo = entrada.get('codigo') or ''

        items.append({
            'descripcion': descripcion or 'Material',
            'cantidad': cantidad,
            'precio_unitario': precio_base,
            'unidad': str(unidad or '94'),
            'codigo': str(codigo or ''),
            'iva_tasa': iva_tasa,
            'inc_tasa': 0,
            'base': base,
        })
    if not items:
        raise ErrorEmision('El documento soporte necesita al menos una línea.')
    return items


def _resumen(items):
    base_total = redondear(sum(i['base'] for i in items))
    iva_total = redondear(sum(redondear(i['base'] * i['iva_tasa'] / 100) for i in items))
    inc_total = redondear(sum(redondear(i['base'] * i['inc_tasa'] / 100) for i in items))
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


def emitir_documento_soporte(conn, id_proveedor, items_payload,
                             documento_proveedor='', contingencia=False):
    """Genera, firma y envía un Documento Soporte a No Obligados a Facturar.

    Args:
        conn: conexión SQLite abierta.
        id_proveedor: id en `proveedores_informales`.
        items_payload: lista de líneas de la compra (ver `_leer_items`).
        documento_proveedor: número de la factura de papel que trae el
            proveedor. Opcional pero recomendable: es el soporte físico.
        contingencia: si es True, genera y firma sin enviar.

    Returns:
        dict con numero, cuide, estado, id_documento, qr_url y mensaje.
    """
    cursor = conn.cursor()

    ajustes = _leer_ajustes_dian(cursor)
    if not ajustes['clave_tecnica']:
        raise ErrorEmision(
            'Falta la clave técnica del software propio (la entrega la DIAN al '
            'registrar el software). Configúrela antes de emitir.'
        )
    emisor = _leer_emisor(cursor)
    proveedor = _leer_proveedor(cursor, id_proveedor)

    # El IVA por defecto sale de la configuración del negocio, como en la venta.
    cfg = cursor.execute(
        "SELECT COALESCE(iva_porcentaje, 0), COALESCE(iva_activo, 1)"
        " FROM configuracion WHERE id = 1"
    ).fetchone()
    iva_porcentaje = float(cfg[0] or 0) if cfg[1] else 0

    items = _leer_items(cursor, items_payload, iva_porcentaje)
    totales = _resumen(items)

    prefijo = PREFIJO_SOPORTE_DEFECTO
    # OJO: `ajustes['consecutivo']` es un entero, pero se accede por clave como
    # en el resto del modulo. Se normaliza con int() porque el valor viene de
    # SQLite y puede llegar como texto si la columna cambio de tipo.
    siguiente = int(ajustes.get('consecutivo') or 1)
    cursor.execute('UPDATE configuracion SET dian_consecutivo = ? WHERE id = 1',
                   (siguiente + 1,))
    numero = f'{prefijo}-{siguiente}'

    ahora = datetime.now()
    fecha = normalizar_fecha(ahora)
    hora = normalizar_hora(ahora)

    cuide = calcular_cuide(
        num_documento=numero, fecha=fecha, hora=hora,
        val_imp1=totales['iva_valor'], val_imp2=totales['inc_valor'],
        val_total=totales['payable_amount'], nit=emisor['nit'],
        tipo_documento=TIPO_DOCUMENTO_SOPORTE,
        clave_tecnica=ajustes['clave_tecnica'],
        tipo_ambiente=ajustes['ambiente'],
    )
    qr = url_consulta(cuide, fecha=fecha, nit=emisor['nit'],
                      total=totales['payable_amount'])

    documento = {
        'numero': numero, 'fecha': fecha, 'hora': hora, 'cuide': cuide,
        'tipo_ambiente': ajustes['ambiente'], 'moneda': 'COP',
        'valor_total': totales['payable_amount'],
        'documento_proveedor': documento_proveedor or '',
    }
    extras = {
        'software_id': ajustes['software_id'],
        'software_security_code': ajustes['software_security_code'],
    }

    raiz = dian_notas.construir_documento_soporte(
        documento, emisor, proveedor, items, totales, extras)
    xml_sin_firma = dian_notas.a_texto(raiz)

    certificado = _cargar_certificado(ajustes)
    xml_firmado = dian_firma.firmar_bytes(
        dian_notas.a_bytes(raiz), certificado, id_documento=numero)

    cursor.execute(
        """
        INSERT INTO documentos_soporte
            (id_proveedor, prefijo, numero, cuide, fecha_generacion,
             hora_generacion, documento_proveedor, valor_total, valor_iva,
             valor_inc, xml, xml_firmado, qr_url, modo, estado, contingencia,
             intentos)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'firmado', ?, 0)
        """,
        (id_proveedor, prefijo, numero, cuide, fecha, hora,
         documento_proveedor or '', totales['payable_amount'],
         totales['iva_valor'], totales['inc_valor'],
         xml_sin_firma, xml_firmado.decode('utf-8'), qr, ajustes['modo'],
         1 if contingencia else 0),
    )
    id_documento = cursor.lastrowid
    conn.commit()

    if contingencia:
        cursor.execute(
            "UPDATE documentos_soporte SET estado = 'contingencia',"
            " contingencia = 1, ultimo_error = ? WHERE id = ?",
            ('Generado sin conexión.', id_documento))
        encolar(cursor, id_documento, 'enviar', intentos=0)
        conn.commit()
        return {
            'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
            'estado': 'contingencia', 'qr_url': qr,
            'mensaje': 'Documento soporte generado en CONTINGENCIA. Se enviará '
                       'cuando vuelva la conexión.',
        }

    return _enviar(conn, id_documento, numero, cuide, xml_firmado, ajustes, qr)


def _enviar(conn, id_documento, numero, cuide, xml_firmado, ajustes, qr):
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
        return _contingencia(conn, id_documento, numero, cuide, qr, str(error))

    if not respuesta.exito:
        return _contingencia(conn, id_documento, numero, cuide, qr, respuesta.mensaje)

    estado = dian_pos.estado_desde_respuesta(respuesta.codigo)
    cursor.execute(
        """
        UPDATE documentos_soporte
        SET estado = ?, respuesta_dian = ?, codigo_dian = ?, descripcion_dian = ?,
            track_id = ?, intentos = intentos + 1, fecha_envio = ?,
            fecha_respuesta = ?
        WHERE id = ?
        """,
        (estado, respuesta.xml_respuesta, respuesta.codigo, respuesta.mensaje,
         respuesta.track_id, _ahora(), _ahora(), id_documento),
    )
    conn.commit()

    return {
        'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
        'estado': estado, 'codigo_dian': respuesta.codigo, 'qr_url': qr,
        'mensaje': (f'Documento soporte {numero} aceptado por la DIAN'
                    if estado == 'aceptado'
                    else f'Documento soporte {numero} RECHAZADO: {respuesta.mensaje}'),
    }


def _contingencia(conn, id_documento, numero, cuide, qr, motivo):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE documentos_soporte SET estado = 'contingencia',"
        " contingencia = 1, ultimo_error = ? WHERE id = ?",
        (motivo, id_documento))
    ya = cursor.execute(
        "SELECT 1 FROM cola_dian WHERE id_documento = ? AND estado = 'pendiente'"
        " AND operacion = 'enviar'", (id_documento,)).fetchone()
    if not ya:
        encolar(cursor, id_documento, 'enviar', intentos=0)
    conn.commit()
    return {
        'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
        'estado': 'contingencia', 'qr_url': qr,
        'mensaje': f'Documento soporte en CONTINGENCIA: {motivo}',
    }
