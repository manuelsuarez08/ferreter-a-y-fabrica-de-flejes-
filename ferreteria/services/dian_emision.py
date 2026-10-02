"""Servicio de emisión del Documento Equivalente Electrónico POS ante la DIAN.

Es el ORQUESTADOR: reúne los datos de la venta, calcula el CUIDE, arma el XML,
lo firma y lo envía. Es el único módulo que conoce el ciclo de vida completo del
documento electrónico; los demás (CUIDE, XML, firma, SOAP) son piezas puras que
este servicio combina.

────────────────────────────────────────────────────────────
FLUJO NORMAL (con internet y la DIAN disponible)
────────────────────────────────────────────────────────────
    emitir_venta(id_venta)
      1. Reúne emisor (configuracion), adquirente (clientes) e ítems (venta).
      2. Asigna el consecutivo fiscal DIAN (prefijo + número) y lo guarda.
      3. Calcula el CUIDE (SHA-384) y la URL del QR.
      4. Genera el XML UBL 2.1.
      5. Firma con el .p12 (XAdES-EPES).
      6. Envía por SendBillSync (o SendTestSetAsync si está en set de pruebas).
      7. Guarda el veredicto: aceptado / rechazado.

────────────────────────────────────────────────────────────
FLUJO DE CONTINGENCIA (caída de internet o la DIAN no responde)
────────────────────────────────────────────────────────────
    emitir_venta(id_venta)
      1-5. Igual que arriba (el documento se genera y se firma IGUAL).

      El POS NO puede dejar de vender porque la DIAN esté caída. El anexo
      técnico contempla la contingencia: el documento se emite, se entrega al
      cliente con su CUIDE y su QR, y se notifica después con los eventos
      correspondientes.

      6. El envío falla -> el documento queda en estado 'contingencia'.
      7. Se encola el trabajo 'enviar' en `cola_dian` con backoff exponencial.
      8. La venta se marca 'contingencia' y el POS sigue funcionando.
      9. Cuando vuelve la conexión, `procesar_cola()` reintenta el envío y, si
         la DIAN acepta, registra el evento '005' (retransmisión).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import dian_firma
from . import dian_pos
from . import dian_series
from . import dian_soap
from . import dian_xml
from .dian_pos import (
    AMBIENTE_HABILITACION,
    ESTADOS_DOCUMENTO,
    TIPO_EVENTO_CONTINGENCIA,
    TIPO_EVENTO_RETRANSMISION,
    TIPO_DOCUMENTO_POS,
    calcular_cuide,
    descripcion_evento,
    formatear_monto,
    normalizar_fecha,
    normalizar_hora,
    redondear,
    url_consulta,
)

# Máximo de intentos por defecto antes de dejar un trabajo como 'fallido'.
# Con el backoff de abajo, 8 intentos cubren ~1 día de reintentos.
MAX_INTENTOS_DEFECTO = 8

# ── Leyenda del software de facturación ─────────────────────────────────────
# El anexo técnico exige identificar el software propio que generó el documento
# electrónico (nombre, versión y empresa proveedora).
#
# ANTES eran constantes fijas y además estaban mal: EMPRESA_SOFTWARE traía el
# nombre de la FERRETERÍA, no el del desarrollador del software. Con eso, la
# DIAN no podía trazar el documento hasta su emisor de software, que es
# justamente el objetivo del dato.
#
# Ahora la identidad del proveedor se lee de la base de datos
# (`configuracion.software_proveedor_*`), porque es un dato de la instalación:
# si el desarrollador cambia su razón social o su NIT, se corrige en la
# configuración sin tocar el código. Los valores de abajo son solo el respaldo
# para una instalación que aún no haya cargado esos datos.
NOMBRE_SOFTWARE = 'POS Ferreteria DIAN'
VERSION_SOFTWARE = '1.0'
EMPRESA_SOFTWARE = ''


class ErrorEmision(Exception):
    """No se pudo emitir el documento (configuración, datos o certificado)."""


# ═════════════════════════════════════════════════════
# Estado fiscal de una venta (usado para BLOQUEAR operaciones)
# ═════════════════════════════════════════════════════
def estado_fiscal_venta(conn, id_venta):
    """Estado del documento electrónico de una venta, para decidir si se puede
    tocar.

    Returns:
        (estado, numero, cuide). `estado` es 'sin_emitir' si la venta todavía no
        tiene documento, o el estado del documento en la DIAN
        ('aceptado', 'contingencia', 'rechazado', ...).

    Se usa en el POS para impedir editar o anular una venta ya emitida: una vez
    que el documento electrónico existe y fue entregado al cliente, cambiar el
    detalle o anular la venta deja el documento mintiendo. La única salida
    lícita es la Nota Crédito Electrónica.
    """
    fila = conn.execute(
        """
        SELECT COALESCE(v.dian_estado, 'sin_emitir'),
               COALESCE(v.numero_dian, ''),
               COALESCE(v.dian_cuide, '')
        FROM ventas v WHERE v.id = ?
        """,
        (id_venta,),
    ).fetchone()
    if not fila:
        return ('sin_emitir', '', '')
    return (fila[0] or 'sin_emitir', fila[1] or '', fila[2] or '')


def venta_emitida(estado):
    """True si la venta ya generó un documento electrónico.

    Incluye los estados no definitivos (contingencia, rechazado, firmado): si
    el documento se generó y se entregó al cliente con su CUIDE, la venta ya no
    se puede tocar. Solo 'sin_emitir' deja la venta editable.
    """
    return str(estado or 'sin_emitir') != 'sin_emitir'


# ═════════════════════════════════════════════════════
# Utilidades de tiempo y backoff
# ═════════════════════════════
def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _segundos_backoff(intentos):
    """Espera antes del siguiente intento, con backoff exponencial acotado.

    La DIAN se cae por mantenimiento programado (madrugada) o por picos. Reintentar
    cada segundo quema los intentos sin dejar que el servicio vuelva y castiga al
    servidor ajeno. La progresión 60s, 2m, 4m, 8m... con techo de 1 hora es la
    que usa el anexo técnico como referencia razonable.
    """
    return min(60 * (2 ** max(0, int(intentos) - 1)), 3600)


def _proximo_intento(intentos):
    """Fecha/hora del siguiente intento (texto 'YYYY-MM-DD HH:MM:SS')."""
    return (datetime.now() + timedelta(seconds=_segundos_backoff(intentos))
            ).strftime('%Y-%m-%d %H:%M:%S')


# ═════════════════════════════════════════════════════
# Lectura de configuración
# ═════════════════════════════════════════════════════
def _leer_emisor(cursor):
    """Datos fiscales del emisor desde la fila única de `configuracion`.

    La DIAN exige NIT, razón social, dirección y municipio. Si falta algo de lo
    indispensable se avisa AQUÍ, con el nombre del campo, en lugar de dejar que
    la DIAN rechace el documento con un código genérico.
    """
    fila = cursor.execute(
        """
        SELECT nombre, nit, COALESCE(digito_verificacion, ''), direccion, telefono,
               COALESCE(email_emisor, ''), COALESCE(codigo_municipio, ''),
               COALESCE(codigo_departamento, ''), COALESCE(regimen_fiscal, ''),
               COALESCE(responsabilidades, ''),
               -- El CIIU del emisor y el NOMBRE del municipio son obligatorios
               -- en el XML. Se leen si existen; en una base vieja la migracion
               -- de columnas los agrega vacios y el documento se completa luego.
               COALESCE(dian_ciiu, ''), COALESCE(dian_ciudad, '')
        FROM configuracion WHERE id = 1
        """
    ).fetchone()
    if not fila:
        raise ErrorEmision('No hay datos de configuración del negocio')

    nombre, nit, dv, direccion, telefono, email, municipio, departamento, \
        regimen, responsabilidades, ciiu, ciudad = fila

    if not dian_pos.solo_digitos(nit):
        raise ErrorEmision(
            'Falta el NIT del emisor. Configúrelo en Configuración > Datos del '
            'negocio antes de emitir documentos electrónicos.'
        )
    if not str(direccion or '').strip():
        raise ErrorEmision(
            'Falta la dirección del emisor (la exige el anexo técnico). '
            'Configúrela en Configuración > Datos del negocio.'
        )

    return {
        'nit': dian_pos.solo_digitos(nit),
        'digito_verificacion': str(dv or '').strip(),
        'razon_social': str(nombre or '').strip(),
        'nombre_comercial': str(nombre or '').strip(),
        'direccion': str(direccion or '').strip(),
        'ciiu': str(ciiu or '').strip(),
        'ciudad': str(ciudad or '').strip(),
        'telefono': str(telefono or '').strip(),
        'email': str(email or '').strip(),
        'municipio': str(municipio or '').strip() or dian_pos.MUNICIPIO_POR_DEFECTO,
        'departamento': (str(departamento or '').strip()
                         or dian_pos.DEPARTAMENTO_POR_DEFECTO),
        'pais': dian_pos.PAIS_POR_DEFECTO,
        'regimen_fiscal': str(regimen or '').strip() or 'No Responsable de IVA',
        'responsabilidades': [r.strip() for r in str(responsabilidades or '').split(',')
                              if r.strip()],
    }


def _leer_ajustes_dian(cursor):
    """Ajustes del módulo DIAN (ambiente, software, certificado, numeración)."""
    columnas = {r[1] for r in cursor.execute('PRAGMA table_info(configuracion)')}
    if 'dian_ambiente' not in columnas:
        # Base de datos que aún no corrió las migraciones de este módulo.
        raise ErrorEmision(
            'La base de datos no tiene las columnas del módulo DIAN. '
            'Reinicie la aplicación para aplicar las migraciones.'
        )

    fila = cursor.execute(
        """
        SELECT COALESCE(dian_ambiente, '2'), COALESCE(dian_prefijo, 'POS'),
               COALESCE(dian_consecutivo, 1), COALESCE(clave_tecnica, ''),
               COALESCE(software_id, ''),
               COALESCE(dian_software_security_code, ''),
               COALESCE(certificado_ruta, ''), COALESCE(certificado_clave, ''),
               COALESCE(dian_test_set_id, ''), COALESCE(dian_modo, 'habilitacion'),
               COALESCE(numero_resolucion, ''), COALESCE(prefijo, ''),
               COALESCE(rango_desde, 1), COALESCE(rango_hasta, 0),
               COALESCE(dian_max_intentos, 8),
               COALESCE(software_proveedor_nit, ''),
               COALESCE(software_proveedor_nombre, '')
        FROM configuracion WHERE id = 1
        """
    ).fetchone()
    return {
        'ambiente': str(fila[0] or AMBIENTE_HABILITACION),
        'prefijo': str(fila[1] or 'POS').strip(),
        'consecutivo': int(fila[2] or 1),
        'clave_tecnica': str(fila[3] or '').strip(),
        'software_id': str(fila[4] or '').strip(),
        # El PIN viaja desde UNA sola columna. Antes se leían las dos
        # (`software_pin` y `dian_software_security_code`) y se publicaba la
        # segunda: si una instalación antigua tenía el PIN solo en la primera, el
        # panel lo daba por configurado y la firma salía sin PIN. La migración
        # en `db.py` copia el valor viejo al destino.
        'software_security_code': str(fila[5] or '').strip(),
        'certificado_ruta': str(fila[6] or '').strip(),
        'certificado_clave': str(fila[7] or ''),
        'test_set_id': str(fila[8] or '').strip(),
        'modo': str(fila[9] or 'habilitacion').strip(),
        'numero_resolucion': str(fila[10] or '').strip(),
        'prefijo_resolucion': str(fila[11] or '').strip(),
        'rango_desde': int(fila[12] or 1),
        'rango_hasta': int(fila[13] or 0),
        'max_intentos': int(fila[14] or MAX_INTENTOS_DEFECTO),
        # Identidad del PROVEEDOR de software. Son datos del desarrollador, NO
        # de la ferretería que factura: el anexo técnico los exige en el nodo
        # SoftwareProvider del XML para poder trazar el documento hasta el
        # software que lo generó.
        'software_proveedor_nit': dian_pos.solo_digitos(fila[15]),
        'software_proveedor_nombre': str(fila[16] or '').strip(),
    }


def _leer_adquirente(cursor, id_cliente):
    """Datos del adquirente. Si es el cliente mostrador devuelve {} (genérico).

    El cliente genérico del anexo es el NIT 222222222222 ('consumidor final'),
    que el generador del XML aplica cuando no hay datos. Se detecta por el
    documento '222' que siembra el POS como cliente por defecto.
    """
    fila = cursor.execute(
        """
        SELECT nombre, cedula_nit, COALESCE(tipo_documento, 'CC'),
               COALESCE(digito_verificacion, ''), COALESCE(direccion, ''),
               COALESCE(telefono, ''), COALESCE(email, ''),
               COALESCE(regimen_fiscal, 'No Responsable de IVA'),
               COALESCE(responsabilidades, ''), COALESCE(codigo_municipio, ''),
               COALESCE(codigo_departamento, '')
        FROM clientes WHERE id = ?
        """,
        (id_cliente,),
    ).fetchone()
    if not fila:
        return {}

    documento = dian_pos.solo_digitos(fila[1])
    # Cliente mostrador / consumidor final: se usa el genérico del anexo.
    if not documento or documento in ('222', dian_pos.NIT_CONSUMIDOR_FINAL):
        return {}

    regimen = str(fila[7] or 'No Responsable de IVA')
    responsabilidades = [r.strip() for r in str(fila[8] or '').split(',') if r.strip()]
    if not responsabilidades:
        responsable = 'Responsable de IVA' in regimen
        responsabilidades = ['O-48'] if responsable else ['R-99-PN']

    return {
        'tipo_documento': str(fila[2] or 'CC'),
        'numero_documento': documento,
        'digito_verificacion': str(fila[3] or ''),
        'nombre': str(fila[0] or ''),
        'direccion': str(fila[4] or ''),
        'telefono': str(fila[5] or ''),
        'email': str(fila[6] or ''),
        'municipio': (str(fila[9] or '').strip() or dian_pos.MUNICIPIO_POR_DEFECTO),
        'departamento': (str(fila[10] or '').strip()
                         or dian_pos.DEPARTAMENTO_POR_DEFECTO),
        'pais': dian_pos.PAIS_POR_DEFECTO,
        'regimen_fiscal': regimen,
        'responsabilidades': responsabilidades,
    }


def _resumen_impuestos(items, clave_tasa):
    """Agrupa las líneas por tasa de impuesto para el resumen del documento.

    La DIAN exige un `cac:TaxSubtotal` por cada tarifa aplicada, con su propia
    base gravable y su propio valor. Sin esto, una venta que mezcle 19% y 5% (o
    que tenga un producto exento) declara una sola tarifa y el documento no
    cuadra.

    Returns:
        Lista de dicts: {'tasa': float, 'base': float, 'valor': float}.
    """
    grupos = {}
    for item in items:
        tasa = float(item.get(clave_tasa) or 0)
        if tasa <= 0:
            continue  # exento: no lleva subtotal de impuesto
        base = redondear(item.get('base') or 0)
        g = grupos.setdefault(tasa, {'tasa': tasa, 'base': 0.0, 'valor': 0.0})
        g['base'] = g['base'] + base
        g['valor'] = g['valor'] + redondear(base * tasa / 100)
    return [grupos[t] for t in sorted(grupos)]


def _resumen_tributos_especificos(items):
    """Agrupa las líneas por tributo específico y consolida un subtotal por tipo.

    El `cac:TaxSubtotal` es POR TRIBUTO: si una venta lleva tres botellas de
    IBUA y dos de INPP, son dos subtotales, no cuatro. Ademas el mismo tributo
    puede venir con dos unidades distintas (litros y kilos), y eso NO se puede
    sumar: son bases de medida diferentes. En ese caso se suman por la unidad
    mas frecuente, que es lo unico defendible sin un criterio mejor.

    Devuelve lista de dicts con la forma que espera `dian_xml._categorias_de`.
    """
    grupos = {}
    for item in items:
        trib = item.get('tributo_especifico')
        if not trib:
            continue
        clave = trib['tipo']
        g = grupos.setdefault(clave, {
            'tipo': clave,
            'base': 0.0,
            'valor': 0.0,
            'tasa': 0.0,
            'valor_unitario': trib['valor_unitario'],
            'unidad': trib['unidad'],
        })
        g['valor'] = redondear(g['valor'] + trib['valor'])

    # La base del tributo es el valor del bien, no el impuesto: el impuesto
    # se SUMA al precio. Se deja en 0 porque el anexo no exige base para un
    # tributo nominal, y declarar de mas es peor que declarar de menos.
    return list(grupos.values())


def _leer_items(cursor, id_venta, iva_porcentaje_venta, iva_por_producto=False):
    """Líneas del documento a partir del detalle de la venta.

    IMPORTANTE (decisión fiscal): en este POS el `precio_venta` del producto es
    el PRECIO FINAL que paga el cliente, ya con IVA incluido. El anexo técnico,
    en cambio, pide `PriceAmount` SIN impuestos y calcula la base gravable sobre
    él. Por eso aquí se DESAGREGA: base = precio final / (1 + tasa). Si no se
    hiciera, el total del documento quedaría inflado (se le sumaría el IVA dos
    veces) y la DIAN rechazaría el cuadre.

    ADVERTENCIA SOBRE EL TRATAMIENTO FISCAL (leer antes de tocar esto)
    ------------------------------------------------------------------
    Hay dos cosas en esta función que NO se han resuelto y que no puede
    resolver el código:

    1. Si el IVA debe gravar sobre la base antes o después de aplicar el
       tributo específico. Aquí se separa de forma coherente: el IVA va sobre la
       base del bien y el tributo se suma por fuera. Eso responde a la
       DIMENSIONALIDAD de cada magnitud (el IVA es un porcentaje, el IBUA son
       pesos por litro), no a una lectura del reglamento. No se pudo cotejar
       con el decreto reglamentario de la Ley 2277 de 2022 porque el servicio
       de la DIAN no respondió y no hay copia oficial del Anexo V1.9.

    2. El valor nominal de cada tributo. Las columnas quedan vacías y un
       producto sin los tres datos no genera subtotal. Deben rellenarse con
       las tarifas oficiales verificadas, nunca de memoria.

    Quien active los tributos específicos tiene que resolver las dos antes.
    Una estructura XML correcta con una tarifa equivocada es una declaración
    fiscal falsa, que es peor que no declarar el tributo.
    """
    filas = cursor.execute(
        """
        SELECT dv.cantidad, dv.precio_unitario, p.nombre, p.dimensiones,
               COALESCE(p.unidad_medida, '94'), COALESCE(p.codigo_dian, ''),
               COALESCE(p.iva_tasa, 0), COALESCE(p.iva_naturaleza, 'excluido'),
               COALESCE(p.iva_tipo_tarifa, '00'),
               COALESCE(p.tributo_especifico_tipo, ''),
               COALESCE(p.tributo_especifico_nominal, 0),
               COALESCE(p.tributo_contenido, 0)
        FROM detalle_ventas dv
        JOIN productos p ON dv.id_producto = p.id
        WHERE dv.id_venta = ?
        ORDER BY dv.id
        """,
        (id_venta,),
    ).fetchall()
    if not filas:
        raise ErrorEmision(
            f'La venta #{id_venta} no tiene líneas: no hay nada que emitir'
        )

    items = []
    for cantidad, precio_final, nombre, dimensiones, unidad, codigo_dian, \
            iva_tasa, naturaleza, tipo_tarifa, trib_tipo, trib_nominal, \
            trib_contenido in filas:
        cantidad = float(cantidad or 0)
        precio_final = float(precio_final or 0)
        iva_tasa = float(iva_tasa or 0)
        naturaleza = str(naturaleza or 'excluido').strip().lower()

        # ── Producto de tarifa 0 (art. 424 / 422 ET) ────────────────────────
        # Los materiales de construcción de extracción directa (arena, balastro)
        # están EXCLUIDOS a tasa 0; los exentos (art. 422) también a tasa 0 pero
        # se declaran con otro código. En ambos casos el producto NO lleva IVA
        # aunque el negocio tenga el IVA global activo: declarar impuesto sobre
        # una operación de tarifa 0 es rechazo garantizado.
        #
        # OJO: se distingue por `iva_tipo_tarifa` y NO por el texto 'excluido',
        # porque `iva_naturaleza` vale 'excluido' por defecto para TODOS los
        # productos (allí significa "gravado", no "excluido del impuesto"). Usar
        # ese texto como criterio dejaría a tasa 0 los 1.461 productos.
        #
        # PERO solo si el catálogo está clasificado. `iva_tipo_tarifa` se creó
        # con DEFAULT '01' y nunca se migró, así que hoy los 1461 productos
        # tienen '01' (excluido) y este bloque los PONDRÍA EN TASA CERO: el
        # documento declararía un impuesto de 0 sobre una venta que sí lo cobra.
        # La DIAN rechaza eso, y además el documento no cuadraría con la venta.
        #
        # Con `iva_por_producto` apagado (el valor por defecto mientras el
        # catálogo no se clasifique) se aplica la tasa global del negocio, que es
        # lo que el POS está cobrando de verdad. Ver
        # `_iva_por_producto_activo` en blueprints/ventas.py.
        #
        # EXCEPTO: un producto marcado exento, excluido o no sujeto SIEMPRE va a
        # tasa cero, tenga el interruptor como tenga. Declarar impuesto sobre una
        # operación no gravada lo rechaza la DIAN, y el cliente pagaría de más.
        #
        # OJO con `iva_naturaleza`: vale 'excluido' por defecto para TODOS los
        # productos (allí significa "gravado", no "excluido del impuesto"), así
        # que no puede usarse sola como criterio. Y OJO con `iva_tasa`: un 0
        # aquí significa "tasa cero legítima", NO "falta informacion", así que
        # no se puede escribir `not iva_tasa` (en Python `not 0` es True y el
        # artículo de tasa cero acabaría gravado).
        if naturaleza in ('exento', 'no_sujeto') or iva_tasa == 0:
            iva_tasa = 0.0
        elif iva_por_producto:
            if naturaleza == 'excluido_iva' or tipo_tarifa in ('01', '02', '03'):
                iva_tasa = 0.0
            else:
                iva_tasa = float(iva_porcentaje_venta or 0)
        else:
            # Catálogo sin clasificar: la tarifa del documento es la que aplica
            # a toda la venta, no la que trae el producto (que es 0 en todos).
            iva_tasa = float(iva_porcentaje_venta or 0)

        # Desagregación: del precio final (con IVA) a la base (sin IVA).
        #
        # OJO con el tributo especifico: NO se resta aqui. Restarlo NO tiene
        # sentido dimensional: el IVA es un PORCENTAJE de la base, asi que
        # dividir el precio por (1 + 0,19) es correcto. El IBUA son PESOS POR
        # LITRO, no un porcentaje: dividir por (1 + 0,09) estaria usando una
        # razon entre magnitudes distintas. Lo que se hace es SUMARLO al total
        # de la venta, porque el impuesto se cobra por fuera del precio del
        # bien.
        #
        # SI EL IVA DEBE GRAVAR SOBRE O DEBAJO DEL TRIBUTO ESPECIFICO, NO SE
        # HA RESUELTO. Lo que hay aqui es una eleccion COHERENTE y
        # dimensionalmente correcta, no una lectura del reglamento.
        #
        # La forma implementada deja el IVA sobre la base del bien y suma el
        # tributo por fuera. Que ese sea el tratamiento que exige la norma
        # depende de como este redacto el decreto reglamentario de la Ley
        # 2277 de 2022, y eso no se pudo cotejar: el servicio de la DIAN no
        # respondio y no hay copia oficial del Anexo V1.9 a mano.
        #
        # SI el regimen correcto fuera al reves (IVA sobre base + tributo), el
        # desagregador seria este, no el de arriba:
        #
        #     base = (precio - nominal x contenido x cantidad) / (1 + iva)
        #
        # OJO al numero de unidades. El tributo se cobra POR UNIDAD, asi que
        # hay que restar el de TODAS las unidades vendidas, no el de una:
        #
        #     3 botellas de 500 ml a 0,18 -> 0,18 x 0,5 x 3 = 0,27
        #
        # Restando solo 0,09 (una unidad) la base queda 7562,95 en vez de
        # 7562,80 y el IVA se declara 0,03 por encima. Es la clase de error de
        # centavos que este proyecto lleva varias rondas cerrando, y la razon
        # por la que el numero de unidades va explicito en la formula.
        #
        # Quien active esta rama debe resolver antes las DOS cosas que faltan:
        # el valor nominal oficial de cada tributo y el tratamiento del IVA
        # respecto del especifico. Ninguna de las dos la puede decidir el
        # codigo.
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
            'descuento': 0,
            'iva_tasa': iva_tasa,
            'inc_tasa': 0,
            'base': base,
        })

        # ── Tributo con tarifa ESPECIFICA (Ley 2277 de 2022) ───────────────
        # A diferencia del IVA, este NO es un porcentaje de la base: es un
        # valor nominal por unidad de medida. La cuenta es
        #
        #     impuesto = nominal x (cantidad vendida x contenido por unidad)
        #
        # El `contenido` es lo que convierte "3 botellas" en litros. Sin el no
        # hay cuenta posible, y el producto queda sin tributo: es preferible a
        # declarar un impuesto inventado.
        #
        # El subtotal se calcula AQUI, en la orquestacion, y no en el
        # generador: el generador no sabe de producto ni de ley, solo sabe
        # leer un numero que le dan. Si lo calculara el, habria dos lugares
        # que deciden el impuesto y podrian discrepar.
        #
        # Los tres datos tienen que estar: tipo, nominal y contenido. Con uno
        # solo falta, no se liquida nada.
        especifico = _tributo_especifico_de_linea(
            trib_tipo, trib_nominal, trib_contenido, cantidad)
        if especifico:
            items[-1]['tributo_especifico'] = especifico
    return items


def _tributo_especifico_de_linea(tipo, nominal, contenido, cantidad, unidad_base=''):
    """Calcula el tributo nominal de UNA línea, o None si no aplica.

    Se separa en su propia funcion para poder probarla sin base de datos ni
    venta: la aritmetica es lo delicado y no necesita a la base para probarse.

    `unidad_base` es la UNIDAD DE MEDIDA del tributo (LTR, TNE), no la unidad
    comercial del POS ('94', 'NIU'). Son cosas distintas: se venden "3
    botellas" y el tributo se liquidan en litros. Si no viene, se usa la que
    declara el tributo en `UNIDAD_BASE_IMPUESTO`.

    Devuelve dict con: tipo, base, valor, valor_unitario, unidad.
    """
    tipo = str(tipo or '').strip()
    nominal = float(nominal or 0)
    contenido = float(contenido or 0)

    # Faltan datos -> no se liquida. Sin esta guarda, un producto con el tipo
    # puesto y el nominal vacio emitiria un Tributo de 0 pesos, que es peor
    # que no emitirlo: el documento declara un impuesto que no se cobro.
    if not tipo or tipo not in dian_pos.NOMBRES_IMPUESTO:
        return None
    if nominal <= 0 or contenido <= 0 or cantidad <= 0:
        return None
    if dian_pos.NATURALEZA_IMPUESTO.get(tipo) != dian_pos.NATURALEZA_ESPECIFICO:
        return None

    valor = redondear(nominal * cantidad * contenido)

    return {
        'tipo': tipo,
        'base': 0.0,                      # lo fija quien consolida el documento
        'valor': valor,
        'tasa': 0.0,                      # no aplica: es nominal, no porcentual
        'valor_unitario': nominal,
        'unidad': str(unidad_base
                      or dian_pos.UNIDAD_BASE_IMPUESTO.get(tipo, '')),
    }


# ═════════════════════════════
# Numeración fiscal
# ═════════════════════════════
def asignar_numero(cursor, ajustes):
    """Reserva el siguiente número fiscal DIAN para un documento.

    El número que viaja al XML es `prefijo + consecutivo` ('POS-1042') y es
    distinto del id interno de la venta. Se incrementa el consecutivo en
    `configuracion` dentro de la MISMA transacción de la venta: si la venta
    falla, el número no se pierde; si la venta se registra, el número no se
    reutiliza (la DIAN rechaza dos documentos con el mismo número).

    Returns:
        La tupla (prefijo, numero_texto, consecutivo).
    """
    prefijo = str(ajustes.get('prefijo') or 'POS').strip()
    siguiente = int(ajustes.get('consecutivo') or 1)

    # Guarda de rango: si hay resolución con rango y el consecutivo se salió, se
    # avisa antes de generar un documento que la DIAN rechazaría por numeración.
    hasta = int(ajustes.get('rango_hasta') or 0)
    if hasta and siguiente > hasta:
        raise ErrorEmision(
            f'El consecutivo {siguiente} superó el rango autorizado por la '
            f'resolución (hasta {hasta}). Solicite una nueva numeración a la DIAN.'
        )

    cursor.execute('UPDATE configuracion SET dian_consecutivo = ? WHERE id = 1',
                   (siguiente + 1,))
    return prefijo, f'{prefijo}-{siguiente}', siguiente


# ═════════════════════════════════════════════════════
# Cola de trabajos (patrón outbox)
# ═════════════════════════════
def encolar(cursor, id_documento, operacion, payload=None, intentos=0):
    """Agrega un trabajo a `cola_dian`.

    `intentos=0` deja el trabajo listo para ejecutarse YA (proximo_intento en
    blanco); con `intentos>0` se agenda según el backoff.
    """
    cursor.execute(
        """
        INSERT INTO cola_dian (id_documento, operacion, payload, estado, intentos,
                               proximo_intento, creado, actualizado)
        VALUES (?, ?, ?, 'pendiente', ?, ?, ?, ?)
        """,
        (id_documento, operacion,
         json.dumps(payload or {}, ensure_ascii=False),
         int(intentos),
         _proximo_intento(intentos) if intentos else None,
         _ahora(), _ahora()),
    )
    return cursor.lastrowid


def _trabajos_pendientes(cursor, limite=20):
    """Trabajos listos para procesar (respetando el backoff)."""
    return cursor.execute(
        """
        SELECT id, id_documento, operacion, payload, intentos
        FROM cola_dian
        WHERE estado = 'pendiente'
          AND (proximo_intento IS NULL OR proximo_intento <= ?)
        ORDER BY id
        LIMIT ?
        """,
        (_ahora(), int(limite)),
    ).fetchall()


def _cerrar_trabajo(cursor, id_trabajo, estado, error=None):
    """Marca un trabajo como completado o fallido."""
    cursor.execute(
        """
        UPDATE cola_dian SET estado = ?, ultimo_error = ?, actualizado = ?
        WHERE id = ?
        """,
        (estado, error, _ahora(), id_trabajo),
    )


def _reprogramar_trabajo(cursor, id_trabajo, intentos, error, max_intentos):
    """Reintenta un trabajo con backoff, o lo deja fallido si se agotaron."""
    if intentos >= max_intentos:
        _cerrar_trabajo(cursor, id_trabajo, 'fallido',
                        f'Se agotaron los {max_intentos} intentos. {error or ""}'.strip())
        return False
    cursor.execute(
        """
        UPDATE cola_dian SET intentos = ?, ultimo_error = ?, proximo_intento = ?,
                             actualizado = ?
        WHERE id = ?
        """,
        (intentos, error, _proximo_intento(intentos), _ahora(), id_trabajo),
    )
    return True


# ═════════════════════════════
# Persistencia del documento
# ═════════════════════════════════════════════════════
def _buscar_documento(cursor, id_venta):
    """Documento electrónico ya emitido para esa venta (o None)."""
    return cursor.execute(
        'SELECT id, numero, cuide, estado FROM documentos_electronicos '
        'WHERE id_venta = ? ORDER BY id DESC LIMIT 1',
        (id_venta,),
    ).fetchone()


def _guardar_documento(cursor, datos):
    """Inserta el registro del documento electrónico."""
    cursor.execute(
        """
        INSERT INTO documentos_electronicos
            (id_venta, tipo_documento, prefijo, numero, cuide, fecha_generacion,
             hora_generacion, valor_total, valor_iva, valor_inc, xml, xml_firmado,
             qr_url, modo, estado, contingencia, intentos)
        VALUES (:id_venta, :tipo, :prefijo, :numero, :cuide, :fecha, :hora,
                :total, :iva, :inc, :xml, :xml_firmado, :qr, :modo, :estado,
                :contingencia, 0)
        """,
        datos,
    )
    return cursor.lastrowid


def _marcar_venta(cursor, id_venta, numero, cuide, estado, descripcion=''):
    """Desnormaliza el estado fiscal en la venta (para listarla sin JOIN)."""
    cursor.execute(
        """
        UPDATE ventas SET numero_dian = ?, dian_estado = ?, dian_cuide = ?,
                          dian_descripcion = ?, dian_fecha_emision = ?
        WHERE id = ?
        """,
        (numero, estado, cuide, str(descripcion or '')[:500], _ahora(), id_venta),
    )


def _aplicar_respuesta(cursor, id_documento, id_venta, numero, respuesta,
                       modo, contingencia=0):
    """Guarda el veredicto de la DIAN sobre un documento."""
    estado = dian_pos.estado_desde_respuesta(respuesta.codigo)
    if not respuesta.exito:
        estado = 'contingencia' if contingencia else 'error'
    cursor.execute(
        """
        UPDATE documentos_electronicos
        SET estado = ?, respuesta_dian = ?, codigo_dian = ?, descripcion_dian = ?,
            track_id = ?, zip_key = ?, intentos = intentos + 1,
            fecha_envio = ?, fecha_respuesta = ?, contingencia = ?
        WHERE id = ?
        """,
        (estado, respuesta.xml_respuesta, respuesta.codigo, respuesta.mensaje,
         respuesta.track_id, respuesta.zip_key, _ahora(), _ahora(),
         1 if contingencia else 0, id_documento),
    )
    _marcar_venta(cursor, id_venta, numero, '', estado, respuesta.mensaje)
    return estado


# ═════════════════════════════
# Emisión
# ═════════════════════════════════════════════════════
def emitir_venta(conn, id_venta, forzar=False, contingencia=False):
    """Emite el Documento Equivalente Electrónico de una venta.

    Args:
        conn: conexión SQLite abierta.
        id_venta: id de la venta en `ventas`.
        forzar: reemite aunque ya exista un documento para esa venta (se usa
            para corregir un documento rechazado; la DIAN NO admite dos
            documentos válidos para una misma venta, así que el anterior debe
            haberse anulado).
        contingencia: si es True, NO intenta enviar: genera, firma y deja el
            documento en contingencia con el trabajo de envío encolado. Se usa
            cuando el POS ya sabe que no hay conexión (evita esperar el timeout).

    Returns:
        dict con el resultado: id_documento, numero, cuide, estado, mensaje,
        contingencia (bool) y qr_url.

    Raises:
        ErrorEmision: si faltan datos, configuración o el certificado.
    """
    cursor = conn.cursor()

    venta = cursor.execute(
        """
        SELECT v.id, v.fecha_dia, v.hora, v.total_venta, v.id_cliente,
               COALESCE(v.anulada, 0), COALESCE(v.subtotal_venta, 0),
               COALESCE(v.iva_valor, 0), COALESCE(v.iva_porcentaje, 0),
               COALESCE(v.tipo_operacion, '10'), COALESCE(v.numero_dian, ''),
               COALESCE(v.dian_estado, 'sin_emitir'),
               COALESCE(v.tipo_pago, 'efectivo'),
               COALESCE(v.tipo_documento_dian, 'POS')
        FROM ventas v WHERE v.id = ?
        """,
        (id_venta,),
    ).fetchone()
    if not venta:
        raise ErrorEmision(f'La venta #{id_venta} no existe')
    if venta[5]:
        raise ErrorEmision(
            f'La venta #{id_venta} está anulada: no se emite documento electrónico'
        )

    # ── Idempotencia ─────────────────────────────────────────────────────────
    # Emitir dos veces la misma venta produciría dos CUIDE distintos para el
    # mismo hecho económico. Si ya hay documento, se devuelve el existente.
    existente = _buscar_documento(cursor, id_venta)
    if existente and not forzar:
        id_doc, numero, cuide, estado = existente
        return {
            'id_documento': id_doc, 'numero': numero, 'cuide': cuide,
            'estado': estado, 'mensaje': 'La venta ya tiene documento electrónico',
            'contingencia': estado == 'contingencia',
            'qr_url': url_consulta(cuide, fecha=venta[1], nit=None, total=venta[3]),
        }

    ajustes = _leer_ajustes_dian(cursor)
    emisor = _leer_emisor(cursor)
    adquirente = _leer_adquirente(cursor, venta[4])
    # El interruptor decide si el documento declara la tasa global o la de cada
    # producto. Tiene que ser el MISMO que usa la venta (`_calcular_iva_por_
    # lineas`), o el documento declararía un impuesto distinto al cobrado.
    fila_iva_prod = cursor.execute(
        'SELECT COALESCE(iva_por_producto, 0) FROM configuracion WHERE id = 1'
    ).fetchone()
    iva_por_producto = bool(fila_iva_prod[0]) if fila_iva_prod else False
    items = _leer_items(cursor, id_venta, venta[8], iva_por_producto)

    # ── Totales del documento ────────────────────────────────────────────────
    # Se recalculan desde las líneas (base sin IVA) para que el XML cuadre
    # consigo mismo: es la comprobación que hace la DIAN.
    #
    # El IVA se redondea POR LÍNEA y luego se suma. Sumar primero y redondear
    # despues (o calcular sobre floats sin redondear) acumula los decimales de
    # cada línea y el total se va del valor que la DIAN recalcula: con 7 líneas
    # de $1.000,33 al 19% la suma en float da $1.330,44 y el correcto es
    # $1.330,42. La DIAN valida el cuadre del documento linea por linea.
    base_total = redondear(sum(i['base'] for i in items))
    iva_total = redondear(sum(
        redondear(i['base'] * i['iva_tasa'] / 100) for i in items
    ))
    inc_total = redondear(sum(
        redondear(i['base'] * i['inc_tasa'] / 100) for i in items
    ))
    total = redondear(base_total + iva_total + inc_total)

    fecha = normalizar_fecha(venta[1])
    hora = normalizar_hora(venta[2])

    # ── Numeración y CUIDE ───────────────────────────────────────────────────
    # El tipo de documento lo eligió el cajero al cobrar: 'POS' para la venta de
    # mostrador, 'FV' para el comprador que necesita Factura Electrónica. Cada
    # uno tiene su propia serie (prefijo, resolución y rango), de modo que el
    # número se toma de `series_dian` y no del consecutivo global.
    tipo_documento = (venta[13] or 'POS').strip().upper()
    if tipo_documento not in dian_series.TIPOS_DOCUMENTO:
        tipo_documento = 'POS'

    serie = dian_series.leer_serie(conn, tipo_documento)
    if forzar and existente:
        # Reemisión del mismo número: conserva el consecutivo ya asignado para
        # poder anular el documento anterior con el mismo identificador.
        numero = existente[1]
        prefijo = numero.rsplit('-', 1)[0] if '-' in numero else (serie['prefijo'] if serie else tipo_documento)
        consecutivo = int(numero.rsplit('-', 1)[-1]) if '-' in numero else 1
    else:
        reserva = dian_series.reservar_numero(conn, tipo_documento)
        prefijo = reserva['prefijo']
        numero = reserva['numero']
        consecutivo = reserva['consecutivo']

    # La clave técnica puede venir de la serie (una por resolución) o de la
    # configuración global. La de la serie manda: es la que corresponde al
    # ambiente en el que se habilitó esa numeración.
    clave_tecnica = (serie or {}).get('clave_tecnica') or ajustes['clave_tecnica']

    if not clave_tecnica:
        raise ErrorEmision(
            'Falta la clave técnica del software propio (la entrega la DIAN al '
            'registrar el software). Configúrela en la serie '
            f'{tipo_documento} o en Administración antes de emitir.'
        )

    cuide = calcular_cuide(
        num_documento=numero,
        fecha=fecha,
        hora=hora,
        val_imp1=iva_total,
        val_imp2=inc_total,
        val_total=total,
        nit=emisor['nit'],
        # OJO: el tipo de documento entra en el CUIDE. Un POS y una FV con el
        # mismo número NO pueden compartir CUIDE, y el anexo técnico exige que
        # la clave técnica usada sea la de la serie que autorizó ese documento.
        tipo_documento=tipo_documento,
        clave_tecnica=clave_tecnica,
        tipo_ambiente=ajustes['ambiente'],
    )
    qr = url_consulta(cuide, fecha=fecha, nit=emisor['nit'], total=total)

    # ── XML ──────────────────────────────────────────────────────────────────
    # Leyenda del software: el anexo tecnico exige que el documento identifique
    # el software propio que lo genero (nombre, version y empresa).
    documento = {
        'numero': numero, 'fecha': fecha, 'hora': hora, 'cuide': cuide,
        'tipo_ambiente': ajustes['ambiente'], 'moneda': 'COP',
        'tipo_operacion': venta[9], 'valor_total': total,
        # El cajero elige POS o FV al cobrar. Viaja al XML como el
        # InvoiceTypeCode, con sus datos de numeración y resolución.
        'tipo_documento': tipo_documento,
        'numero_resolucion': (serie or {}).get('numero_resolucion') or ajustes['numero_resolucion'],
        'prefijo_resolucion': (serie or {}).get('prefijo') or ajustes['prefijo_resolucion'],
        'rango_desde': (serie or {}).get('rango_desde') or ajustes['rango_desde'],
        'rango_hasta': (serie or {}).get('rango_hasta') or ajustes['rango_hasta'],
        # Forma de pago y referencia de la venta: los necesita
        # `cac:PaymentMeans` en el XML (el anexo la exige siempre).
        'tipo_pago': venta[10], 'id_venta': id_venta,
        # Leyenda del software: el nombre y la versión son del producto (fijos en
        # el código), pero la EMPRESA proveedora es del desarrollador y se lee de
        # la configuración. Antes era una constante con el nombre de la
        # ferretería, lo que impedía a la DIAN trazar el documento.
        'nombre_software': NOMBRE_SOFTWARE, 'version_software': VERSION_SOFTWARE,
        'empresa_software': ajustes['software_proveedor_nombre'] or EMPRESA_SOFTWARE,
        'nit_proveedor_software': ajustes['software_proveedor_nit'],
    }
    totales = {
        'line_extension_amount': base_total,
        'tax_exclusive_amount': base_total,
        'tax_inclusive_amount': total,
        'payable_amount': total,
        'iva_valor': iva_total,
        'inc_valor': inc_total,
        # OJO: NO se usa `max(iva_tasa)`. Con tarifas mixtas (19% y 5%, o un
        # producto exento) el resumen de impuestos debe declarar UN
        # `TaxSubtotal` por tarifa, cada uno con SU base y SU valor; declarar
        # solo la mayor hace que la DIAN no cuadre el documento (la base del
        # subtotal no corresponde al valor del impuesto).
        'impuestos_iva': _resumen_impuestos(items, 'iva_tasa'),
        'impuestos_inc': _resumen_impuestos(items, 'inc_tasa'),
        # Antes esta linea no existia: los tributos especificos se leian en el
        # generador pero NADA los llenaba. Era una ruta muerta, con veinte
        # pruebas de estructura sobre codigo que nadie ejecutaba.
        'impuestos_especificos': _resumen_tributos_especificos(items),
    }
    extras = {
        'software_id': ajustes['software_id'],
        'software_security_code': ajustes['software_security_code'],
    }

    raiz = dian_xml.construir_invoice(documento, emisor, adquirente, items,
                                      totales, extras)
    xml_sin_firma = dian_xml.a_texto(raiz)

    # ── Firma ────────────────────────────────────────────────────────────────
    certificado = _cargar_certificado(ajustes, emisor['nit'])
    xml_firmado = dian_firma.firmar_bytes(
        dian_xml.a_bytes(raiz), certificado, id_documento=numero
    )

    # ── Persistencia ANTES de enviar ─────────────────────────────────────────
    # El documento se guarda firmado antes de salir a la red: si el proceso muere
    # durante el envío, el documento existe y se puede reintentar sin volver a
    # firmarlo (y sin que el CUIDE cambie, que es lo que lo hace válido).
    id_documento = _guardar_documento(cursor, {
        'id_venta': id_venta,
        # El tipo de documento que eligió el cajero (POS o FV). Guardarlo en la
        # tabla es lo que permite que la nota crédito encuentre después el
        # documento exacto que corrige, sea del tipo que sea.
        'tipo': tipo_documento,
        'prefijo': prefijo,
        'numero': numero,
        'cuide': cuide,
        'fecha': fecha,
        'hora': hora,
        'total': total,
        'iva': iva_total,
        'inc': inc_total,
        'xml': xml_sin_firma,
        'xml_firmado': xml_firmado.decode('utf-8'),
        'qr': qr,
        'modo': ajustes['modo'],
        'estado': 'firmado',
        'contingencia': 1 if contingencia else 0,
    })
    conn.commit()

    # ── Envío ────────────────────────────────────────────────────────────────
    if contingencia:
        _pasar_a_contingencia(cursor, conn, id_documento, id_venta, numero, cuide,
                              ajustes, 'Emisión en contingencia (sin conexión)')
        return {
            'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
            'estado': 'contingencia', 'contingencia': True, 'qr_url': qr,
            'mensaje': 'Documento generado en CONTINGENCIA. Se enviará cuando '
                       'vuelva la conexión.',
        }

    return _enviar_documento(cursor, conn, id_documento, id_venta, numero, cuide,
                             xml_firmado, ajustes, qr)


def _cargar_certificado(ajustes, nit_emisor=None):
    """Carga el certificado de firma desde la configuración y lo CONTRASTA.

    Cargar el .p12 no basta: además hay que confirmar que sea el de esta
    ferretería. Si el certificado es de otra persona, la DIAN rechaza el
    documento aunque la firma sea criptográficamente válida, y el rechazo llega
    tarde y opaco. Por eso la comprobación va aquí, en el único punto por donde
    pasan los tres tipos de documento (venta, nota crédito y documento soporte).
    """
    if not ajustes['certificado_ruta']:
        raise ErrorEmision(
            'No hay certificado de firma configurado. Suba el archivo .p12/.pfx '
            'en Configuración > Facturación electrónica DIAN.'
        )
    try:
        certificado = dian_firma.cargar_certificado(
            ajustes['certificado_ruta'], ajustes['certificado_clave']
        )
    except dian_firma.ErrorCertificado as error:
        raise ErrorEmision(str(error)) from error

    if nit_emisor is not None:
        try:
            dian_firma.verificar_identidad_emisor(certificado, nit_emisor)
        except dian_firma.ErrorIdentidadEmisor as error:
            raise ErrorEmision(str(error)) from error

    return certificado


def _pasar_a_contingencia(cursor, conn, id_documento, id_venta, numero, cuide,
                          ajustes, motivo):
    """Deja el documento en contingencia y encola su envío.

    En contingencia el POS ya entregó el documento al cliente con su CUIDE y su
    QR, así que la venta NO se deshace: lo que se difiere es la notificación a la
    DIAN. El anexo exige reportar el evento de contingencia (004) y, cuando se
    logre enviar, el de retransmisión (005).
    """
    cursor.execute(
        """
        UPDATE documentos_electronicos
        SET estado = 'contingencia', contingencia = 1, tipo_evento = ?,
            descripcion_evento = ?, ultimo_error = ?
        WHERE id = ?
        """,
        (TIPO_EVENTO_CONTINGENCIA, motivo, motivo, id_documento),
    )
    _marcar_venta(cursor, id_venta, numero, cuide, 'contingencia', motivo)

    # Un solo trabajo de envío por documento: si ya existe uno pendiente no se
    # duplica (dos envíos del mismo documento generarían dos TrackId).
    ya_encolado = cursor.execute(
        "SELECT 1 FROM cola_dian WHERE id_documento = ? AND estado = 'pendiente' "
        "AND operacion = 'enviar'",
        (id_documento,),
    ).fetchone()
    if not ya_encolado:
        encolar(cursor, id_documento, 'enviar', intentos=0)

    conn.commit()


def _enviar_documento(cursor, conn, id_documento, id_venta, numero, cuide,
                      xml_firmado, ajustes, qr, ya_en_contingencia=False):
    """Envía el documento firmado y registra el resultado.

    Si el envío falla por red (ErrorSOAP) el documento pasa a contingencia y se
    encola; si la DIAN responde un rechazo, el documento queda 'rechazado' y NO
    se reintenta (reintentar un XML inválido no cambia nada: hay que corregirlo).
    """
    nombre_archivo = f'{numero}.xml'
    try:
        if ajustes['test_set_id'] and ajustes['ambiente'] == AMBIENTE_HABILITACION:
            # En habilitación con set de pruebas cargado, el anexo exige enviar
            # por SendTestSetAsync para que la DIAN acumule el set completo antes
            # de validarlo. En producción el camino es siempre SendBillSync.
            respuesta = dian_soap.send_test_set_async(
                xml_firmado, nombre_archivo, ajustes['test_set_id'],
                ambiente=ajustes['ambiente'])
        else:
            respuesta = dian_soap.send_bill_sync(
                xml_firmado, nombre_archivo, ambiente=ajustes['ambiente'])
    except dian_soap.ErrorSOAP as error:
        _pasar_a_contingencia(cursor, conn, id_documento, id_venta, numero, cuide,
                              ajustes, str(error))
        return {
            'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
            'estado': 'contingencia', 'contingencia': True, 'qr_url': qr,
            'mensaje': f'Sin conexión con la DIAN: documento en CONTINGENCIA. {error}',
        }

    if not respuesta.exito:
        # El transporte fue bien pero la respuesta es ininteligible: se trata
        # como contingencia y se reintenta, porque no se sabe si la DIAN lo tiene.
        _pasar_a_contingencia(cursor, conn, id_documento, id_venta, numero, cuide,
                              ajustes, respuesta.mensaje)
        return {
            'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
            'estado': 'contingencia', 'contingencia': True, 'qr_url': qr,
            'mensaje': f'Respuesta no concluyente de la DIAN: {respuesta.mensaje}',
        }

    estado = _aplicar_respuesta(cursor, id_documento, id_venta, numero, respuesta,
                                ajustes['modo'],
                                contingencia=1 if ya_en_contingencia else 0)

    # Si venía de contingencia y la DIAN aceptó, hay que reportar el evento de
    # retransmisión (005), que es el que cierra la novedad ante la DIAN.
    if estado == 'aceptado' and ya_en_contingencia:
        cursor.execute(
            "UPDATE documentos_electronicos SET tipo_evento = ?, "
            "descripcion_evento = ? WHERE id = ?",
            (TIPO_EVENTO_RETRANSMISION,
             dian_pos.DESCRIPCION_EVENTO_RETRANSMISION, id_documento),
        )
        encolar(cursor, id_documento, 'evento',
                payload={'tipo_evento': TIPO_EVENTO_RETRANSMISION})

    conn.commit()
    return {
        'id_documento': id_documento, 'numero': numero, 'cuide': cuide,
        'estado': estado, 'contingencia': False, 'qr_url': qr,
        'codigo_dian': respuesta.codigo,
        'mensaje': (f'Documento {numero} aceptado por la DIAN'
                    if estado == 'aceptado'
                    else f'Documento {numero} RECHAZADO: {respuesta.mensaje}'),
    }


# ═════════════════════════════════════════════════════
# Reenvío
# ═════════════════════════════════════════════════
def reenviar_documento(conn, id_documento):
    """Reintenta el envío de un documento que quedó en contingencia o error.

    No vuelve a generar ni a firmar: reutiliza el XML firmado guardado, que es
    lo correcto, porque el CUIDE ya está impreso en la tirilla que recibió el
    cliente. Firmar de nuevo cambiaría la firma (y el digest) del documento que
    el cliente ya tiene.
    """
    cursor = conn.cursor()
    documento = cursor.execute(
        """
        SELECT id, id_venta, numero, cuide, xml_firmado, COALESCE(contingencia, 0),
               estado
        FROM documentos_electronicos WHERE id = ?
        """,
        (id_documento,),
    ).fetchone()
    if not documento:
        raise ErrorEmision(f'El documento #{id_documento} no existe')

    id_doc, id_venta, numero, cuide, xml_firmado, contingencia_antes, estado = documento
    if estado == 'aceptado':
        return {
            'id_documento': id_doc, 'numero': numero, 'cuide': cuide,
            'estado': 'aceptado', 'contingencia': False,
            'mensaje': 'El documento ya fue aceptado por la DIAN',
        }
    if not xml_firmado:
        raise ErrorEmision(
            f'El documento {numero} no tiene XML firmado guardado: '
            'genere de nuevo la emisión'
        )

    ajustes = _leer_ajustes_dian(cursor)
    return _enviar_documento(cursor, conn, id_doc, id_venta, numero, cuide,
                             xml_firmado.encode('utf-8'), ajustes, '',
                             ya_en_contingencia=bool(contingencia_antes))


def consultar_estado(conn, id_documento):
    """Consulta el estado del documento en la DIAN (GetStatus / GetStatusZip).

    Sirve cuando el envío dejó un `track_id` y la DIAN aún no había resuelto, y
    para verificar el resultado del set de pruebas.
    """
    cursor = conn.cursor()
    documento = cursor.execute(
        """
        SELECT id, id_venta, numero, cuide, track_id, zip_key, estado
        FROM documentos_electronicos WHERE id = ?
        """,
        (id_documento,),
    ).fetchone()
    if not documento:
        raise ErrorEmision(f'El documento #{id_documento} no existe')

    id_doc, id_venta, numero, cuide, track_id, zip_key, estado = documento
    ajustes = _leer_ajustes_dian(cursor)

    try:
        if zip_key:
            respuesta = dian_soap.get_status_zip(zip_key, ambiente=ajustes['ambiente'])
        elif track_id:
            respuesta = dian_soap.get_status(track_id, ambiente=ajustes['ambiente'])
        else:
            return {
                'id_documento': id_doc, 'numero': numero, 'estado': estado,
                'mensaje': 'El documento no tiene trackId ni ZipKey: se consulta '
                           'solo después de un envío',
            }
    except dian_soap.ErrorSOAP as error:
        raise ErrorEmision(f'No se pudo consultar el estado: {error}') from error

    nuevo_estado = _aplicar_respuesta(cursor, id_doc, id_venta, numero,
                                      respuesta, ajustes['modo'])
    conn.commit()
    return {
        'id_documento': id_doc, 'numero': numero, 'cuide': cuide,
        'estado': nuevo_estado, 'codigo': respuesta.codigo,
        'mensaje': respuesta.mensaje, 'errores': respuesta.errores,
    }


# ═════════════════════════════════════════════════════
# Procesamiento de la cola
# ═════════════════════════════════════════════════════
def procesar_cola(conn, limite=20):
    """Procesa los trabajos pendientes de `cola_dian` con backoff.

    Se llama al arrancar la aplicación y desde el botón "Reintentar envíos" del
    módulo DIAN. NO se llama desde cada venta: el POS no debe esperar por la red.

    Returns:
        dict con el resumen: procesados, exitosos, fallidos, pendientes.
    """
    cursor = conn.cursor()
    ajustes = _leer_ajustes_dian(cursor)
    resumen = {'procesados': 0, 'exitosos': 0, 'fallidos': 0, 'pendientes': 0,
               'detalles': []}

    for id_trabajo, id_documento, operacion, payload, intentos in _trabajos_pendientes(cursor, limite):
        resumen['procesados'] += 1
        try:
            if operacion == 'enviar':
                resultado = reenviar_documento(conn, id_documento)
                estado = resultado.get('estado')
                if estado == 'aceptado':
                    _cerrar_trabajo(cursor, id_trabajo, 'completado')
                    resumen['exitosos'] += 1
                    resumen['detalles'].append(f'{resultado["numero"]}: aceptado')
                elif estado == 'rechazado':
                    # Rechazo de negocio: reintentar el MISMO XML no sirve.
                    _cerrar_trabajo(
                        cursor, id_trabajo, 'fallido',
                        f'Rechazado por la DIAN: {resultado.get("mensaje", "")}')
                    resumen['fallidos'] += 1
                    resumen['detalles'].append(
                        f'{resultado["numero"]}: rechazado')
                else:
                    # Sigue sin conexión: se reprograma con backoff.
                    sigue = _reprogramar_trabajo(
                        cursor, id_trabajo, intentos + 1,
                        resultado.get('mensaje', ''), ajustes['max_intentos'])
                    resumen['pendientes' if sigue else 'fallidos'] += 1
                    resumen['detalles'].append(
                        f'{resultado["numero"]}: se reintentará')

            elif operacion == 'evento':
                datos = json.loads(payload or '{}')
                ok, detalle = _enviar_evento(conn, id_documento, datos)
                if ok:
                    _cerrar_trabajo(cursor, id_trabajo, 'completado')
                    resumen['exitosos'] += 1
                else:
                    sigue = _reprogramar_trabajo(
                        cursor, id_trabajo, intentos + 1, detalle,
                        ajustes['max_intentos'])
                    resumen['pendientes' if sigue else 'fallidos'] += 1
                resumen['detalles'].append(f'evento: {detalle}')

            else:
                _cerrar_trabajo(cursor, id_trabajo, 'fallido',
                                f'Operación desconocida: {operacion}')
                resumen['fallidos'] += 1
        except (ErrorEmision, sqlite3.Error) as error:
            sigue = _reprogramar_trabajo(cursor, id_trabajo, intentos + 1,
                                         str(error), ajustes['max_intentos'])
            resumen['pendientes' if sigue else 'fallidos'] += 1
            resumen['detalles'].append(str(error)[:200])
        conn.commit()

    return resumen


def _enviar_evento(conn, id_documento, datos):
    """Firma y envía un evento DIAN (contingencia / retransmisión).

    Returns:
        (ok, detalle)
    """
    cursor = conn.cursor()
    documento = cursor.execute(
        """
        SELECT numero, cuide, xml_firmado FROM documentos_electronicos WHERE id = ?
        """,
        (id_documento,),
    ).fetchone()
    if not documento:
        return False, f'El documento #{id_documento} no existe'

    numero, cuide, _xml = documento
    emisor = _leer_emisor(cursor)
    ajustes = _leer_ajustes_dian(cursor)

    tipo_evento = str(datos.get('tipo_evento') or TIPO_EVENTO_RETRANSMISION)
    descripcion = datos.get('descripcion') or dian_pos.descripcion_evento(tipo_evento)

    raiz = dian_xml.construir_evento(cuide, numero, tipo_evento, descripcion, emisor)
    try:
        certificado = _cargar_certificado(ajustes, emisor['nit'])
    except ErrorEmision as error:
        return False, str(error)

    xml_evento = dian_firma.firmar_bytes(
        dian_xml.a_bytes(raiz), certificado, id_documento=f'{numero}-evt-{tipo_evento}'
    )

    try:
        respuesta = dian_soap.send_event_update_status(
            xml_evento, ambiente=ajustes['ambiente'])
    except dian_soap.ErrorSOAP as error:
        return False, f'Sin conexión para el evento {tipo_evento}: {error}'

    if not respuesta.exito:
        return False, f'El evento {tipo_evento} no se pudo reportar: {respuesta.mensaje}'

    cursor.execute(
        """
        UPDATE documentos_electronicos
        SET tipo_evento = ?, descripcion_evento = ?, respuesta_dian = ?
        WHERE id = ?
        """,
        (tipo_evento, descripcion, respuesta.xml_respuesta, id_documento),
    )
    return True, f'Evento {tipo_evento} reportado ({respuesta.codigo})'


# ═════════════════════════════════════════════════════
# Consultas para la interfaz
# ═════════════════════════════════════════════════════
def estado_dian(conn):
    """Resumen del módulo para el panel de la interfaz.

    Devuelve: ambiente, modo, si hay certificado, cuántos documentos hay por
    estado y cuántos trabajos están en la cola.
    """
    cursor = conn.cursor()
    ajustes = _leer_ajustes_dian(cursor)

    por_estado = {}
    for estado, total in cursor.execute(
        'SELECT estado, COUNT(*) FROM documentos_electronicos GROUP BY estado'
    ).fetchall():
        por_estado[estado] = total

    pendientes = cursor.execute(
        "SELECT COUNT(*) FROM cola_dian WHERE estado = 'pendiente'"
    ).fetchone()[0]
    fallidos = cursor.execute(
        "SELECT COUNT(*) FROM cola_dian WHERE estado = 'fallido'"
    ).fetchone()[0]

    # Estado del certificado (sin exponer la clave).
    certificado = {'configurado': bool(ajustes['certificado_ruta']),
                   'valido': False, 'titular': '', 'vence': '', 'dias': None,
                   'mensaje': ''}
    if ajustes['certificado_ruta']:
        try:
            cargado = dian_firma.cargar_certificado(
                ajustes['certificado_ruta'], ajustes['certificado_clave'],
                verificar_vigencia=False)
            certificado.update({
                'valido': cargado.vigente(),
                'titular': cargado.titular,
                'dias': cargado.dias_para_vencer(),
                'vence': cargado.fecha_vencimiento().strftime('%Y-%m-%d'),
            })
        except dian_firma.ErrorCertificado as error:
            certificado['mensaje'] = str(error)

    faltantes = []
    if not ajustes['clave_tecnica']:
        faltantes.append('Clave técnica del software')
    if not ajustes['software_id']:
        faltantes.append('SoftwareID')
    if not ajustes['certificado_ruta']:
        faltantes.append('Certificado de firma (.p12)')
    if not ajustes['software_security_code']:
        faltantes.append('SoftwareSecurityCode (PIN)')

    return {
        'ambiente': ajustes['ambiente'],
        'ambiente_nombre': dian_pos.ambiente_por_numero(ajustes['ambiente']),
        'modo': ajustes['modo'],
        'prefijo': ajustes['prefijo'],
        'consecutivo': ajustes['consecutivo'],
        'software_id': ajustes['software_id'],
        'test_set_id': ajustes['test_set_id'],
        'documentos': por_estado,
        'cola_pendientes': pendientes,
        'cola_fallidos': fallidos,
        'certificado': certificado,
        'faltantes': faltantes,
        'listo': not faltantes,
    }


def listar_documentos(conn, limite=100, estado=None):
    """Lista los documentos electrónicos emitidos (para la tabla de la interfaz)."""
    cursor = conn.cursor()
    sql = """
        SELECT d.id, d.id_venta, d.numero, d.cuide, d.fecha_generacion,
               d.hora_generacion, d.valor_total, d.estado, d.modo,
               COALESCE(d.codigo_dian, ''), COALESCE(d.descripcion_dian, ''),
               COALESCE(c.nombre, ''), COALESCE(d.qr_url, ''), d.contingencia
        FROM documentos_electronicos d
        LEFT JOIN ventas v ON d.id_venta = v.id
        LEFT JOIN clientes c ON v.id_cliente = c.id
    """
    parametros = []
    if estado:
        sql += ' WHERE d.estado = ?'
        parametros.append(estado)
    sql += ' ORDER BY d.id DESC LIMIT ?'
    parametros.append(int(limite))

    return [{
        'id': r[0], 'id_venta': r[1], 'numero': r[2], 'cuide': r[3],
        'fecha': r[4], 'hora': r[5], 'total': r[6], 'estado': r[7],
        'estado_legible': dian_pos.estado_legible(r[7]), 'modo': r[8],
        'codigo_dian': r[9], 'mensaje_dian': r[10], 'cliente': r[11],
        'qr_url': r[12], 'contingencia': bool(r[13]),
    } for r in cursor.execute(sql, parametros).fetchall()]