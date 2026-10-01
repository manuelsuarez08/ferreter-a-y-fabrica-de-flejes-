"""Reglas fiscales del Documento Equivalente Electrónico POS (DIAN, Anexo 1.0).

Este módulo concentra lo que NO depende de red ni de base de datos:

  1. Constantes del anexo técnico (ambientes, tipos de documento, tarifas de
     impuesto, unidades de medida, tipos de evento, códigos de respuesta).
  2. El algoritmo del CUIDE (hash SHA-384 sobre una cadena canónica).
  3. La URL de consulta pública que se codifica en el Código QR de la tirilla.
  4. Helpers de formato (fechas, montos, dígitos) usados por el generador XML.

Igual que `ferreteria/services/dian.py`, es lógica pura: sin Flask, sin SQLite y
sin `requests`. Eso permite probar el CUIDE y el QR en milisegundos y reutilizar
el código desde scripts de diagnóstico.

Referencia normativa: Resolución DIAN 000165 de 2023, Anexo Técnico 1.0
"Documento Equivalente Electrónico POS".
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

# ── Ambientes y endpoints ────────────────────────────────────────────────────
# La DIAN publica los mismos servicios en dos ambientes; el número de ambiente
# ('1' producción, '2' habilitación) viaja en el XML y entra al hash del CUIDE.
AMBIENTE_PRODUCCION = '1'
AMBIENTE_HABILITACION = '2'

AMBIENTES = {
    AMBIENTE_PRODUCCION: 'Producción',
    AMBIENTE_HABILITACION: 'Habilitación (set de pruebas)',
}

# URL del servicio web (WCF) por ambiente. Es la misma para SendBillSync,
# SendTestSetAsync, GetStatus y GetStatusZip.
#
# SIN `?wsdl`. Ese sufijo es el del DESCRIPTOR del servicio (el XML que declara
# que operaciones existen), no el del servicio. El descriptor se consulta solo,
# para leer; las operaciones van a la URL a secas. Medido contra el servicio real
# de habilitación: con `?wsdl` y sin él el comportamiento es identico, asi que
# el sufijo no era la causa de los timeouts (ver la nota de diagnostico de abajo).
#
# DIAGNOSTICO 2026-10-01 (ambiente de habilitación, desde esta red):
#   - DNS resuelve (190.24.13.32), puerto 443 abre y el TLS es valido.
#   - GET ?wsdl responde HTTP 200 con el descriptor (15 KB, 15 operaciones,
#     targetNamespace http://wcf.dian.colombia).
#   - Un sobre SOAP VACIO responde HTTP 500 en 0.4 s: el servidor recibe y
#     rechaza pronto.
#   - Un GetStatus bien formado NO responde: se agota el tiempo a los 40 s.
#   - Una operacion INEXISTENTE tampoco responde. Si el servidor validara el
#     SOAPAction devolveria un error rapido; que se cuelgue igual significa que
#     el problema esta ANTES de la aplicacion, no en nuestra peticion.
#
# Conclusion: el envelope, los namespaces (SOAP 1.2), el targetNamespace y el
# SOAPAction (`.../IWcfDianCustomerServices/<Op>`) son correctos segun el WSDL.
# El cuelgue es del servicio o de la ruta de red hacia el, no del codigo. Por eso
# NO se toco el transporte: cambiarlo a ciegas seria cambiar algo que ya esta
# bien.
#
# Al corregir esto hay que volver a medir; si el servicio responde, el POS mandara
# los documentos con normalidad sin ningun otro cambio.
WSDL_HABILITACION = (
    'https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc'
)
WSDL_PRODUCCION = (
    'https://vpfe.dian.gov.co/WcfDianCustomerServices.svc'
)
ENDPOINTS = {
    AMBIENTE_HABILITACION: WSDL_HABILITACION,
    AMBIENTE_PRODUCCION: WSDL_PRODUCCION,
}

# URL de la consulta pública del documento. A partir del 2023 la DIAN usa
# /consultarDocumento (antes /Documentos/...). Es la que se codifica en el QR.
URL_CONSULTA = 'https://catalogo-vpfe.dian.gov.co/document/searchqr'

# ── Tipos de documento del anexo técnico ─────────────────────────────────────
TIPO_DOCUMENTO_POS = 'POS'          # Documento Equivalente Electrónico POS
TIPO_DOCUMENTO_FACTURA = 'FV'       # Factura electrónica de venta
TIPO_DOCUMENTO_NOTA_CREDITO = 'NC'
TIPO_DOCUMENTO_NOTA_DEBITO = 'ND'

# Códigos de tipo de documento de identidad del adquirente (columna "código" de
# `ferreteria/services/dian.py`; se repiten aquí para no acoplar los dos
# módulos y porque el XML exige el código numérico, no la sigla).
# Cliente genérico / consumidor final: NIT 222222222222.
NIT_CONSUMIDOR_FINAL = '222222222222'
DV_CONSUMIDOR_FINAL = '0'
NOMBRE_CONSUMIDOR_FINAL = 'consumidor final'
MUNICIPIO_POR_DEFECTO = '11001'          # Bogotá D.C. (código DANE)
DEPARTAMENTO_POR_DEFECTO = '11'          # Cundinamarca (código DANE)

# Zona horaria oficial de Colombia. El anexo técnico exige que `cbc:IssueTime` la
# traiga explícita: '10:30:00-05:00'. Colombia no aplica horario de verano, así
# que el offset es fijo todo el año.
OFFSET_HORA_COLOMBIA = '-05:00'
PAIS_POR_DEFECTO = 'CO'

# ── Tarifas de impuesto ──────────────────────────────────────────────────────
# Código UN/ECE 5305 para el esquema de impuestos. El POS solo usa IVA ('01')
# e INC ('04'), pero se deja la tabla abierta a los demás por completitud.
TIPO_IMPUESTO_IVA = '01'
TIPO_IMPUESTO_IC = '02'
TIPO_IMPUESTO_INC = '04'
TIPO_IMPUESTO_BOLSA = '22'
TIPO_IMPUESTO_IC_DATOS = '05'

NOMBRES_IMPUESTO = {
    TIPO_IMPUESTO_IVA: 'IVA',
    TIPO_IMPUESTO_IC: 'IC',
    TIPO_IMPUESTO_INC: 'INC',
    TIPO_IMPUESTO_BOLSA: 'Bolsas',
    TIPO_IMPUESTO_IC_DATOS: 'IC Datos',
}

# Tarifas vigentes de IVA en Colombia (%). La DIAN valida contra este catálogo.
TARIFAS_IVA = {
    '0.00': '0.00',
    '5.00': '5.00',
    '19.00': '19.00',
}
# INC (impuesto nacional al consumo) — bolsas plásticas y otros.
TARIFAS_INC = {'8.00': '8.00', '16.00': '16.00'}

# ── Tipos de evento (contingencia y retransmisión) ───────────────────────────
# 030 = acuse de recibo, 031 = reclamo, 032 = aceptación expresa, 033 = rechazo.
# El Anexo 1.0 usa 034 para "recepción de la mercancía", 035/036 para las notas
# de ajuste y 004 para el evento de contingencia que aquí interesa.
TIPO_EVENTO_CONTINGENCIA = '004'          # Evento de contingencia (falla el servicio)
TIPO_EVENTO_RETRANSMISION = '005'         # Retransmisión del documento en contingencia
TIPO_EVENTO_ACUSE = '030'
TIPO_EVENTO_ACEPTACION = '032'
TIPO_EVENTO_RECHAZO = '033'

DESCRIPCION_EVENTO_CONTINGENCIA = (
    'Falla en el servicio de recepción de la DIAN / caída de conectividad'
)
DESCRIPCION_EVENTO_RETRANSMISION = (
    'Retransmisión del Documento Equivalente Electrónico POS emitido en contingencia'
)

# ── Códigos de respuesta de la DIAN ──────────────────────────────────────────
# Los códigos de la familia "00" son aceptación; "99" y los "-" son errores.
# Se listan los que el POS necesita interpretar para decidir si reintenta.
CODIGO_ACEPTADO = '00'
CODIGO_ACEPTADO_CON_NOTIFICACION = '01'
CODIGOS_RECHAZO_DIAN = (
    '99',   # error no especificado: NO reintentar, corregir el XML
    '2',    # documento rechazado
    '3',    # documento con inconsistencias
)

ESTADOS_DOCUMENTO = (
    'pendiente',       # generado, sin firmar o sin enviar
    'firmado',         # XML firmado, listo para envío
    'enviado',         # aceptado por la DIAN y en proceso (trackId)
    'aceptado',        # aceptado definitivamente
    'rechazado',       # rechazado por la DIAN (requiere corrección)
    'contingencia',    # emitido sin conexión; pendiente de retransmitir
    'error',           # falló el envío tras agotar los intentos
)

OPERACIONES_COLA = ('enviar', 'consultar_estado', 'enviar_set_pruebas', 'evento')

# ── Unidades de medida (UN/ECE Rec 20) más usadas en ferretería ──────────────
UNIDAD_POR_DEFECTO = '94'
UNIDADES_MEDIDA_POS = (
    '94', 'C62', 'KGM', 'GRM', 'TNE', 'MTR', 'MTK', 'MTQ', 'LTR', 'BLL',
    'PAR', 'KTM', 'SET', 'BG', 'RO',
)

# ── Tipos de operación (columna "tipo de operación" del anexo) ───────────────
TIPO_OPERACION_ESTANDAR = '10'


# ═════════════════════════════════════════════════════
# Helpers de formato
# ═════════════════════════════
def normalizar_fecha(valor):
    """Devuelve 'YYYY-MM-DD' a partir de un date, datetime o texto.

    El anexo técnico exige ese formato exacto tanto en el XML como en la cadena
    del CUIDE. Acepta también fechas con hora ('2026-09-27 14:03:00'), que es
    como las guarda el POS.
    """
    if isinstance(valor, datetime):
        return valor.strftime('%Y-%m-%d')
    if isinstance(valor, date):
        return valor.strftime('%Y-%m-%d')
    texto = str(valor or '').strip()
    if not texto:
        return datetime.now().strftime('%Y-%m-%d')
    # De 'YYYY-MM-DD HH:MM:SS' o 'YYYY-MM-DDTHH:MM:SS' se toma la fecha.
    return texto.replace('T', ' ').split(' ')[0][:10]


def normalizar_hora(valor):
    """Devuelve 'HH:MM:SS-05:00' a partir de un datetime, un time o un texto.

    La DIAN exige hora de 24 horas CON zona horaria oficial de Colombia
    (UTC-05:00). Sin el offset el documento se rechaza por esquema.

    El offset va FIJO en -05:00 y no se calcula de la zona del servidor: el
    anexo pide la hora oficial de Colombia, no la del equipo donde se firma. Si
    el servidor quedara en otra zona, usar su hora con un -05:00 fijo daría una
    hora falsa.

    Si el texto ya trae offset se respeta; si no, se le agrega -05:00.
    """
    if isinstance(valor, datetime):
        return valor.strftime('%H:%M:%S') + OFFSET_HORA_COLOMBIA
    texto = str(valor or '').strip()
    if not texto:
        return datetime.now().strftime('%H:%M:%S') + OFFSET_HORA_COLOMBIA
    texto = texto.replace('T', ' ').split(' ')[-1]

    # 1) Se separa el offset si el texto ya lo trae. `Z` significa UTC.
    offset = OFFSET_HORA_COLOMBIA
    if texto.endswith(('Z', 'z')):
        offset = '+00:00'
        texto = texto[:-1]
    else:
        encontrado = re.search(r'([+-])(\d{2}):?(\d{2})$', texto)
        if encontrado:
            signo, horas, minutos = encontrado.groups()
            # `+-05:00` -> se reconstruye con un solo guion, sin duplicarlo.
            offset = f'{signo}{horas}:{minutos}'
            texto = texto[:encontrado.start()]

    # 2) Se normaliza la hora, sin offset y sin fracciones.
    partes = texto.replace('.', ':').split(':')
    if len(partes) < 2:
        return datetime.now().strftime('%H:%M:%S') + OFFSET_HORA_COLOMBIA
    hora = partes[0][-2:].zfill(2)
    minuto = partes[1][:2].zfill(2)
    segundo = (partes[2][:2].zfill(2) if len(partes) > 2 else '00')

    # 3) Se devuelve con el offset: el anexo lo exige explícito.
    return f'{hora}:{minuto}:{segundo}{offset}'


def formatear_monto(valor):
    """Formatea un valor monetario con 2 decimales, como exige el UBL 2.1.

    Se usa Decimal(str(...)) y no round() de float para que 2.675 no termine en
    2.67 por el error de representación binaria: la DIAN cuadra los totales con
    tolerancia de 1 peso, pero el desglose debe ser consistente consigo mismo.
    """
    try:
        numero = Decimal(str(valor if valor is not None else 0)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except (ArithmeticError, ValueError, TypeError):
        numero = Decimal('0.00')
    return f'{numero:.2f}'


def formatear_cantidad(valor):
    """Formatea cantidades con hasta 6 decimales, sin ceros sobrantes.

    El UBL permite decimales en las cantidades (kilos, metros). Se normaliza para
    que 3 y 3.000000 produzcan el mismo texto '3.00'.
    """
    try:
        numero = Decimal(str(valor if valor is not None else 0)).quantize(
            Decimal('0.000001'), rounding=ROUND_HALF_UP
        )
    except (ArithmeticError, ValueError, TypeError):
        numero = Decimal('0.000000')
    texto = f'{numero:.6f}'.rstrip('0').rstrip('.')
    return texto if texto else '0'


def solo_digitos(valor):
    """Extrae los dígitos de un NIT/documento ('900.187.391' -> '900187391')."""
    return ''.join(c for c in str(valor or '') if c.isdigit())


def redondear(valor):
    """Redondea a 2 decimales con ROUND_HALF_UP y devuelve un float.

    Se usa para los cálculos intermedios del documento (base gravable, IVA por
    línea). OJO: no es lo mismo que `formatear_monto`, que devuelve TEXTO para
    el XML; aquí se devuelve un número para poder seguir operando.

    Se emplea Decimal(str(...)) para no arrastrar el error binario del float: sin
    esto, una base de 10000.005 podría dar 10000.0 y descuadrar el total en un
    peso (dentro de la tolerancia, pero ensucia los subtotales).
    """
    try:
        numero = Decimal(str(valor if valor is not None else 0)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    except (ArithmeticError, ValueError, TypeError):
        numero = Decimal('0.00')
    return float(numero)


def descripcion_evento(tipo_evento):
    """Descripción por defecto de un evento DIAN según su tipo.

    El anexo exige que los eventos de contingencia (004) y retransmisión (005)
    lleven una descripción; para los demás se devuelve una genérica.
    """
    tipo = str(tipo_evento or '').strip()
    if tipo == TIPO_EVENTO_CONTINGENCIA:
        return DESCRIPCION_EVENTO_CONTINGENCIA
    if tipo == TIPO_EVENTO_RETRANSMISION:
        return DESCRIPCION_EVENTO_RETRANSMISION
    return {
        TIPO_EVENTO_ACUSE: 'Acuse de recibo del documento',
        TIPO_EVENTO_ACEPTACION: 'Aceptación expresa del documento',
        TIPO_EVENTO_RECHAZO: 'Rechazo del documento',
    }.get(tipo, 'Evento del documento electrónico')


def tipo_documento_identidad(sigla):
    """Traduce la sigla del POS al código numérico que exige el anexo técnico.

    El POS guarda 'CC', 'NIT', 'CE'...; el XML necesita '13', '31', '22'...
    Se repite la tabla (en vez de importarla de `dian.py`) para que este módulo
    siga siendo autónomo y testeable sin arrastrar el resto del paquete.
    """
    tabla = {
        'RC': '11', 'TI': '12', 'CC': '13', 'TE': '14', 'CE': '22',
        'NIT': '31', 'PP': '41', 'DE': '21', 'NUIP': '91', 'NIT_OTRO': '50',
    }
    return tabla.get(str(sigla or '').strip().upper(), '13')


# ═════════════════════════════════════════════════════
# CUIDE — Código Único de Documento Equivalente Electrónico
# ═════════════════════════════════════════════════════
def hora_para_cuide(hora):
    """'HH:MM:SS' para la cadena del CUFE/CUFE, SIN zona horaria.

    Es gemela de `normalizar_hora` pero deliberadamente distinta: el anexo pide
    la hora sin offset en la cadena que se hashea, y con offset en
    `cbc:IssueTime`. Mezclarlas rompe una de las dos cosas, y como las pruebas
    derivan el valor esperado de la misma función, el error pasa inadvertido.
    """
    from datetime import datetime as _dt
    if isinstance(hora, _dt):
        return hora.strftime('%H:%M:%S')
    texto = str(hora or '').strip()
    if not texto:
        return _dt.now().strftime('%H:%M:%S')
    texto = texto.replace('T', ' ').split(' ')[-1]
    # Se descarta el offset si viene puesto: aquí no forma parte.
    if texto.endswith(('Z', 'z')):
        texto = texto[:-1]
    else:
        encontrado = re.search(r'([+-]\d{2}):?(\d{2})$', texto)
        if encontrado:
            texto = texto[:encontrado.start()]
    partes = texto.replace('.', ':').split(':')
    if len(partes) < 2:
        return _dt.now().strftime('%H:%M:%S')
    hora_ = partes[0][-2:].zfill(2)
    minuto = partes[1][:2].zfill(2)
    segundo = (partes[2][:2].zfill(2) if len(partes) > 2 else '00')
    return f'{hora_}:{minuto}:{segundo}'


def cadena_cuide(num_documento, fecha, hora, val_imp1, val_imp2, val_total,
                 nit, tipo_documento, clave_tecnica, tipo_ambiente):
    """Construye la cadena canónica sobre la que se calcula el SHA-384.

    El orden y los separadores son los del Anexo Técnico 1.0 y NO son
    intercambiables: cambiar un separador produce un CUIDE distinto (y la DIAN
    rechaza el documento con el error de "CUDE/CUIDE no corresponde").

    Formato:

        NumDocumento + Fecha + Hora + ValImp1 + ValImp2 + ValTotal +
        NIT + TipoDoc + ClaveTecnica + TipoAmbiente

    Los valores van CONCATENADOS (sin separador) y cada uno con el formato que
    exige el anexo:

      - NumDocumento:   consecutivo con prefijo, sin espacios ('POS-1042')
      - Fecha:          'YYYY-MM-DD'
      - Hora:           'HH:MM:SS' (24 h, SIN zona horaria)
      - ValImp1:        valor del IVA con 2 decimales ('1900.00')
      - ValImp2:        valor del INC con 2 decimales ('0.00')
      - ValTotal:       total del documento con 2 decimales ('11900.00')
      - NIT:            NIT del emisor sin dígito de verificación ni puntos
      - TipoDoc:        tipo de documento ('POS')
      - ClaveTecnica:   clave técnica del software propio
      - TipoAmbiente:   '1' producción, '2' habilitación

    OJO: la hora va SIN el offset '-05:00'. Es un requisito distinto del de
    `cbc:IssueTime`, que sí lo lleva. Por eso NO se reutiliza
    `normalizar_hora()` aquí sino `hora_para_cuide()`: si se compartieran,
    agregar el offset al IssueTime habría cambiado también la cadena del CUFE y
    TODOS los documentos se habrían calculado mal de una vez, sin que ninguna
    prueba lo notara (las pruebas calculan el valor esperado con la misma
    función, así que ambas se equivocarían juntas).

    Returns:
        La cadena lista para `hashlib.sha384(...)`.
    """
    return ''.join((
        str(num_documento or '').strip(),
        normalizar_fecha(fecha),
        hora_para_cuide(hora),
        formatear_monto(val_imp1),
        formatear_monto(val_imp2),
        formatear_monto(val_total),
        solo_digitos(nit),
        str(tipo_documento or TIPO_DOCUMENTO_POS).strip(),
        str(clave_tecnica or '').strip(),
        str(tipo_ambiente or AMBIENTE_HABILITACION).strip(),
    ))


def calcular_cuide(num_documento, fecha, hora, val_imp1, val_imp2, val_total,
                   nit, tipo_documento, clave_tecnica, tipo_ambiente):
    """Calcula el CUIDE: SHA-384 en hexadecimal (96 caracteres en minúscula).

    El CUIDE es el equivalente del CUFE para el documento POS. Se calcula sobre
    la cadena de `cadena_cuide()` y se expresa en hexadecimal en minúsculas.

    Args:
        num_documento: consecutivo con prefijo ('POS-1042').
        fecha: fecha de generación ('YYYY-MM-DD' o datetime).
        hora: hora de generación ('HH:MM:SS' o datetime).
        val_imp1: valor total del IVA del documento.
        val_imp2: valor total del INC (0 si no aplica).
        val_total: total del documento (con impuestos).
        nit: NIT del emisor (sin DV).
        tipo_documento: 'POS' (o 'FV', 'NC', 'ND').
        clave_tecnica: clave técnica asignada al software propio.
        tipo_ambiente: '1' producción, '2' habilitación.

    Returns:
        El CUIDE como cadena hexadecimal de 96 caracteres en minúscula.
    """
    cadena = cadena_cuide(
        num_documento, fecha, hora, val_imp1, val_imp2, val_total,
        nit, tipo_documento, clave_tecnica, tipo_ambiente,
    )
    return hashlib.sha384(cadena.encode('utf-8')).hexdigest()


# ═════════════════════════════════════════════════════
# Código QR obligatorio en la tirilla
# ═════════════════════════════
def url_consulta(cuide, fecha=None, nit=None, total=None, base=URL_CONSULTA):
    """Arma la URL de consulta pública que se codifica en el QR.

    La DIAN resuelve el documento con el CUIDE; el resto de parámetros son
    redundantes pero hacen la URL auditable a simple vista (y es lo que imprimen
    la mayoría de proveedores). Se codifican con `urlencode` para que el QR no
    se rompa si el CUIDE trae caracteres reservados.

    Args:
        cuide: CUIDE del documento (96 hex).
        fecha: fecha de generación (se imprime en la URL).
        nit: NIT del emisor.
        total: total del documento (se formatea con 2 decimales).
        base: URL base del servicio de consulta (parametrizable para pruebas).

    Returns:
        La URL completa, o '' si no hay CUIDE (sin él el QR no sirve de nada).
    """
    cuide = str(cuide or '').strip()
    if not cuide:
        return ''
    parametros = {'DocumentKey': cuide}
    if fecha:
        parametros['Fecha'] = normalizar_fecha(fecha)
    if nit:
        parametros['Nit'] = solo_digitos(nit)
    if total is not None:
        parametros['Total'] = formatear_monto(total)
    return f'{base}?{urllib.parse.urlencode(parametros)}'


def qr_consultar_documento(cuide, nit=None, fecha=None, total=None):
    """URL corta de consulta (formato `qr_consultar_documento`).

    Variante que la DIAN documenta para el POS y que solo necesita el CUIDE.
    Se ofrece aparte de `url_consulta()` porque algunos lectores de QR del
    mostrador muestran la URL completa y otros el texto corto; la tirilla puede
    usar cualquiera de las dos, pero el contenido del QR debe ser una sola.

    Returns:
        'https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=<CUIDE>'
    """
    cuide = str(cuide or '').strip()
    if not cuide:
        return ''
    parametros = {'documentkey': cuide}
    if nit:
        parametros['nit'] = solo_digitos(nit)
    if fecha:
        parametros['fecha'] = normalizar_fecha(fecha)
    if total is not None:
        parametros['total'] = formatear_monto(total)
    return f'{URL_CONSULTA}?{urllib.parse.urlencode(parametros)}'


# ═════════════════════════════════════════════════════
# Interpretación de la respuesta de la DIAN
# ═════════════════════════════
def es_codigo_aceptacion(codigo):
    """True si el código de respuesta de la DIAN implica aceptación.

    El anexo define la familia '00' como aceptación ('00' aceptado, '01'
    aceptado con notificación). Los códigos '2', '3' y '99' son rechazo.
    """
    codigo = str(codigo or '').strip()
    return codigo in (CODIGO_ACEPTADO, CODIGO_ACEPTADO_CON_NOTIFICACION)


def es_rechazo_definitivo(codigo):
    """True si el rechazo es definitivo y reintentar el MISMO XML no sirve.

    Un error '99' (no especificado) o de validación de esquema no se arregla
    reintentando: hay que corregir los datos y volver a generar el documento.
    Distinguirlo evita que la cola queme los intentos contra un XML inválido.
    """
    codigo = str(codigo or '').strip()
    return bool(codigo) and not es_codigo_aceptacion(codigo)


def estado_desde_respuesta(codigo):
    """Traduce el código de respuesta al estado interno del documento."""
    if es_codigo_aceptacion(codigo):
        return 'aceptado'
    return 'rechazado'


def estado_legible(estado):
    """Etiqueta en español para mostrar el estado en la interfaz."""
    return {
        'sin_emitir': 'Sin emitir',
        'pendiente': 'Pendiente de envío',
        'firmado': 'Firmado, listo para envío',
        'enviado': 'Enviado a la DIAN (en proceso)',
        'aceptado': 'Aceptado por la DIAN',
        'rechazado': 'Rechazado por la DIAN',
        'contingencia': 'Contingencia (sin enviar)',
        'error': 'Error de envío',
    }.get(str(estado or '').strip(), str(estado or ''))


def ambiente_por_numero(numero):
    """Devuelve el nombre legible de un ambiente ('2' -> 'Habilitación...')."""
    return AMBIENTES.get(str(numero or '').strip(), 'Habilitación (set de pruebas)')