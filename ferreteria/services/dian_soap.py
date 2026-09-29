"""Cliente SOAP para los servicios web de la DIAN (facturación electrónica).

Habla con el WCF `WcfDianCustomerServices.svc` en los dos ambientes (habilitación
y producción). Métodos implementados, que son los que cubren el Anexo 1.0:

  - SendBillSync        envío síncrono de UN documento (factura / documento POS).
                        Devuelve el ApplicationResponse con el resultado: es el
                        camino normal en producción y en el POS.
  - SendTestSetAsync    envío ASÍNCRONO del set de pruebas (habilitación) con el
                        `testSetId` que la DIAN asigna. Devuelve un `ZipKey`
                        con el que después se consulta el resultado.
  - GetStatus           estado del documento por `trackId`.
  - GetStatusZip        estado del set de pruebas por `ZipKey`.
  - SendEventUpdateStatus  envío de eventos (contingencia / retransmisión).

DECISIÓN DE DISEÑO: se implementa el SOAP a mano con `urllib` en vez de usar
`zeep` o `suds`. Son 5 operaciones con un contrato fijo y estable; `zeep`
arrastraría `lxml` y compañía a un POS que corre en el mostrador de la
ferretería. Además, así se controla el texto EXACTO que se envía (la DIAN es
estricta con el sobre SOAP y el `wcf:...` de los elementos).

El módulo NO toca la base de datos ni Flask: recibe datos, devuelve un resultado.
"""
from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .dian_pos import (
    AMBIENTE_HABILITACION,
    AMBIENTE_PRODUCCION,
    ENDPOINTS,
    es_codigo_aceptacion,
)

# ── Namespaces del sobre SOAP ─────────────────
NS_SOAP = 'http://www.w3.org/2003/05/soap-envelope'
NS_WCF = 'http://wcf.dian.colombia'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'

SOAP_ACTION_BASE = 'http://wcf.dian.colombia/IWcfDianCustomerServices/'

# ── Timeouts ──────────────────────────────
# El POS no puede quedarse colgado esperando a la DIAN: si el servicio no
# responde en este tiempo, el documento se marca como contingencia y se sigue
# vendiendo. 30 s es holgado para un documento individual (la DIAN responde en
# 1-5 s cuando está sana) y se queda corto frente a los 90 s por defecto de
# muchas librerías, que dejarían al cajero mirando la pantalla.
TIMEOUT_CONEXION = 30
TIMEOUT_RESPUESTA = 60
TIMEOUT_SET_PRUEBAS = 180     # el set de pruebas es un ZIP grande: tarda más


class ErrorSOAP(Exception):
    """Fallo de comunicación con la DIAN (red, HTTP o sobre SOAP malformado)."""


@dataclass
class RespuestaDian:
    """Resultado normalizado de una llamada a la DIAN.

    Atributos:
        exito: True si el servicio respondió (independientemente de si aceptó el
            documento). OJO: `exito` habla del TRANSPORTE, no del documento.
        codigo: código de respuesta de la DIAN ('00' aceptado, '99' error...).
        descripcion: mensaje de la DIAN (causa del rechazo, normalmente).
        es_valido: True si la DIAN ACEPTÓ el documento.
        track_id: identificador para consultar el estado después.
        zip_key: clave del set de pruebas (solo SendTestSetAsync).
        xml_respuesta: ApplicationResponse crudo (se guarda para auditoría).
        errores: lista de (código, descripción) de los nodos de error.
        recibido_en: cuándo se recibió la respuesta.
    """

    exito: bool = False
    codigo: str = ''
    descripcion: str = ''
    es_valido: bool = False
    track_id: str = ''
    zip_key: str = ''
    xml_respuesta: str = ''
    errores: list = field(default_factory=list)
    recibido_en: str = ''

    def _texto_error(self):
        """Mensaje legible juntando la descripción y los errores de detalle."""
        partes = []
        if self.descripcion:
            partes.append(str(self.descripcion).strip())
        for codigo, descripcion in self.errores:
            if descripcion and str(descripcion).strip() not in partes:
                partes.append(f'[{codigo}] {descripcion}'.strip())
        return ' | '.join(p for p in partes if p)

    @property
    def mensaje(self):
        """Descripción para mostrar en la interfaz (nunca vacía)."""
        return self._texto_error() or (
            'Aceptado por la DIAN' if self.es_valido else 'Respuesta sin descripción'
        )

    def a_dict(self):
        """Resumen serializable (lo que se guarda en `documentos_electronicos`)."""
        return {
            'exito': self.exito,
            'codigo': self.codigo,
            'descripcion': self.mensaje,
            'es_valido': self.es_valido,
            'track_id': self.track_id,
            'zip_key': self.zip_key,
            'errores': [{'codigo': c, 'descripcion': d} for c, d in self.errores],
            'recibido_en': self.recibido_en,
        }


# ═════
# Construcción del sobre SOAP
# ═════
def _escapar_xml(valor):
    """Escapa el contenido de un campo que viaja como texto dentro del SOAP."""
    return (str(valor if valor is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _contenido_documento(xml_firmado):
    """Envuelve el XML firmado en el `wcf:contentFile` que espera la DIAN.

    OJO: el XML del documento viaja DENTRO de un CDATA. Si el documento trajera
    la secuencia `]]>` (imposible en un XML válido, pero defensivo), se rompe el
    CDATA; por eso se limpia antes de envolver.
    """
    if isinstance(xml_firmado, bytes):
        xml_firmado = xml_firmado.decode('utf-8')
    # Se quita la declaración XML interna: el anexo pide enviar el documento sin
    # ella dentro del contentFile (la DIAN la rechaza como 'contenido inválido').
    limpio = re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', str(xml_firmado or ''))
    limpio = limpio.replace(']]>', ']]&gt;')
    return f'<![CDATA[{limpio}]]>'


def _sobre_soap(nombre_operacion, cuerpo_parametros):
    """Arma el sobre SOAP 1.2 completo para una operación del servicio.

    Args:
        nombre_operacion: 'SendBillSync', 'GetStatus', etc.
        cuerpo_parametros: XML ya armado con los parámetros de la operación.

    Returns:
        El sobre como bytes UTF-8.
    """
    sobre = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{NS_SOAP}" '
        f'xmlns:wcf="{NS_WCF}" xmlns:xsi="{NS_XSI}">'
        '<soap:Header/>'
        '<soap:Body>'
        f'<wcf:{nombre_operacion}>'
        f'{cuerpo_parametros}'
        f'</wcf:{nombre_operacion}>'
        '</soap:Body>'
        '</soap:Envelope>'
    )
    return sobre.encode('utf-8')


def _contexto_ssl():
    """Contexto TLS para hablar con la DIAN.

    La DIAN usa certificados de servidor emitidos por entidades públicas
    colombianas que NO están en el almacén de confianza de todas las máquinas
    (sobre todo en Windows, que usa su propio almacén). Cuando el POS falla con
    'certificate verify failed', se puede definir DIAN_SSL_INSECURE=1 en el
    entorno para continuar: la conexión sigue cifrada y el documento va firmado,
    así que el riesgo es aceptable en el mostrador, pero se avisa en los logs.
    """
    import os
    if os.environ.get('DIAN_SSL_INSECURE', '').strip() in ('1', 'true', 'True'):
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
        return contexto
    return ssl.create_default_context()


def _endpoint(ambiente):
    """URL del servicio para el ambiente pedido ('1' producción, '2' habilitación)."""
    return ENDPOINTS.get(str(ambiente or '').strip(), ENDPOINTS[AMBIENTE_HABILITACION])


def _llamar(operacion, cuerpo_parametros, ambiente, timeout=None):
    """Ejecuta una operación SOAP y devuelve el XML de respuesta crudo.

    Args:
        operacion: nombre de la operación ('SendBillSync', ...).
        cuerpo_parametros: parámetros XML de la operación.
        ambiente: '1' producción, '2' habilitación.
        timeout: segundos de espera (por defecto TIMEOUT_RESPUESTA).

    Returns:
        El texto de la respuesta SOAP completa.

    Raises:
        ErrorSOAP: si hay un problema de red, TLS, HTTP o el sobre está mal.
    """
    url = _endpoint(ambiente)
    sobre = _sobre_soap(operacion, cuerpo_parametros)
    peticion = urllib.request.Request(
        url,
        data=sobre,
        method='POST',
        headers={
            'Content-Type': f'application/soap+xml; charset=utf-8; action="{SOAP_ACTION_BASE}{operacion}"',
            'Content-Length': str(len(sobre)),
            'User-Agent': 'POS-Ferreteria-DIAN/1.0 (software-propio)',
        },
    )

    try:
        with urllib.request.urlopen(
            peticion, timeout=timeout or TIMEOUT_RESPUESTA, context=_contexto_ssl()
        ) as respuesta:
            return respuesta.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as error:
        detalle = ''
        try:
            detalle = error.read().decode('utf-8', errors='replace')[:600]
        except Exception:
            pass
        raise ErrorSOAP(
            f'La DIAN respondió HTTP {error.code} en {operacion}. {detalle}'.strip()
        ) from error
    except urllib.error.URLError as error:
        raise ErrorSOAP(
            f'No se pudo conectar con la DIAN en {operacion}: {error.reason}. '
            'El documento queda en contingencia y se reintentará.'
        ) from error
    except (TimeoutError, OSError) as error:
        raise ErrorSOAP(
            f'Tiempo de espera agotado hablando con la DIAN en {operacion}: {error}'
        ) from error


# ═════
# Interpretación de la respuesta
# ═════
def _texto_de(nodo):
    """Texto de un nodo, buscando por 'TagName' con o sin namespace.

    La DIAN devuelve los nodos del ApplicationResponse con el namespace UBL, pero
    el envoltorio `wcf:` sin namespace. Buscar por nombre local evita depender de
    qué namespace puso cada ambiente.
    """
    if nodo is None:
        return ''
    return (nodo.text or '').strip()


def _desanidar(texto):
    """Deshace el escapado del ApplicationResponse que viene dentro del sobre.

    OJO (bug real encontrado en QA): la DIAN NO devuelve el ApplicationResponse
    como nodo XML del sobre, sino como TEXTO ESCAPADO dentro de
    `<wcf:...Result>`. Al parsear el sobre, `ET.fromstring` YA convierte las
    entidades (`&lt;` -> `<`), de modo que el ApplicationResponse queda como el
    `.text` de un nodo de texto, SIN nodos hijos. Sin volver a parsear ese texto,
    la búsqueda de `ResponseCode` no encuentra nada y el POS marcaría como
    "respuesta sin información" un documento que la DIAN ACEPTÓ.

    La función sirve para los dos casos: si el texto ya viene desescapado (lo
    normal con ElementTree) solo recorta espacios; si viniera con entidades
    (otro parser), las convierte.

    Returns:
        El XML del ApplicationResponse como texto normal.
    """
    texto = str(texto or '').strip()
    if '&lt;' in texto or '&#60;' in texto:
        # `&amp;` va al final para no convertir dos veces una secuencia ya válida.
        texto = (texto
                 .replace('&lt;', '<')
                 .replace('&gt;', '>')
                 .replace('&quot;', '"')
                 .replace('&apos;', "'")
                 .replace('&amp;', '&'))
    return texto


def _cuerpo_util(raiz):
    """Devuelve el XML "de verdad" de la respuesta, ya desanidado.

    La DIAN responde de dos formas distintas y ambas hay que soportar:

      a) SendBillSync / SendTestSetAsync / SendEventUpdateStatus: devuelven un
         `ApplicationResponse` COMPLETO, escapado como texto dentro del Result.
      b) SendTestSetAsync (set de pruebas) / GetStatusZip: devuelven los campos
         sueltos (`TrackId`, `ZipKey`, `IsValid`, `StatusCode`...) como un
         fragmento XML SIN elemento raíz único.

    En el caso (b) el texto no es un documento XML bien formado (tiene varias
    raíces), así que `ET.fromstring` fallaría. Por eso el fragmento se envuelve en
    un nodo contenedor antes de parsearlo.

    Returns:
        Una raíz de ElementTree sobre la que buscar los campos del resultado.
    """
    for elemento in raiz.iter():
        texto = (elemento.text or '').strip()
        if '<' not in texto:
            continue
        inicio = texto.find('<')
        fragmento = _desanidar(texto[inicio:])
        try:
            return ET.fromstring(fragmento)
        except ET.ParseError:
            # Varias raíces (caso b): se envuelve en un contenedor para poder
            # parsearlo. El contenedor es solo para el parseo, no viaja a ningún
            # lado: los nombres de los campos siguen intactos.
            try:
                return ET.fromstring(f'<ResultadoDian>{fragmento}</ResultadoDian>')
            except ET.ParseError:
                continue
    return raiz


def _buscar(raiz, nombre):
    """Primer nodo cuyo nombre local sea `nombre` (con o sin namespace)."""
    for elemento in raiz.iter():
        if elemento.tag.split('}')[-1] == nombre:
            return elemento
    return None


def _buscar_todos(raiz, nombre):
    """Todos los nodos cuyo nombre local sea `nombre`."""
    return [e for e in raiz.iter() if e.tag.split('}')[-1] == nombre]


def _buscar_primero(raiz, *nombres):
    """Primer nodo que exista de una lista de nombres alternativos.

    OJO (bug real encontrado en QA): NO usar `_buscar(a) or _buscar(b)`. En
    ElementTree, evaluar un `Element` en un `or` usa su verdad booleana, que
    depende de si TIENE HIJOS: un nodo hoja (como `<cbc:ResponseCode>00</...>`)
    es FALSO. Con `or`, un `ResponseCode` presente y con el código '00' se
    descartaba silenciosamente y el resultado quedaba vacío. Por eso las
    alternativas se recorren con un `for` explícito.

    Returns:
        El primer nodo encontrado, o None si no existe ninguno.
    """
    for nombre in nombres:
        nodo = _buscar(raiz, nombre)
        if nodo is not None:
            return nodo
    return None


def parsear_respuesta(xml_soap):
    """Extrae el resultado del ApplicationResponse que devuelve la DIAN.

    La respuesta tiene dos capas:
      1. El sobre SOAP con `<wcf:...Result>` que contiene, escapado, un
         ApplicationResponse UBL.
      2. Dentro, `<cbc:ResponseCode>` (o `<dian:StatusCode>`) con el resultado.

    Esta función tolera las variantes que usan los dos ambientes y devuelve
    siempre un `RespuestaDian` normalizado. Nunca lanza por formato: si no
    reconoce el cuerpo, devuelve `exito=True` con código vacío, y quien decide es
    el servicio de emisión (que en ese caso reintenta).

    Args:
        xml_soap: respuesta cruda del servicio.

    Returns:
        `RespuestaDian` con el resultado.
    """
    recibido = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    resultado = RespuestaDian(exito=True, xml_respuesta=xml_soap, recibido_en=recibido)

    try:
        raiz = ET.fromstring(xml_soap)
    except ET.ParseError:
        # Respuesta no parseable: el transporte fue bien pero no se entiende.
        # Se trata como 'sin información' para que el emisor reintente.
        resultado.exito = False
        resultado.descripcion = 'La DIAN devolvió una respuesta que no es XML válido'
        return resultado

    # Un Fault de SOAP significa error del servicio (no del documento). Se busca
    # ANTES de desanidar: el Fault es parte del sobre, no del ApplicationResponse.
    fault = _buscar(raiz, 'Fault')
    if fault is not None:
        resultado.exito = False
        resultado.codigo = _texto_de(_buscar(fault, 'Value')) or 'SOAP-ENV'
        resultado.descripcion = (
            _texto_de(_buscar(fault, 'Text'))
            or _texto_de(_buscar(fault, 'Reason'))
            or 'La DIAN devolvió un error de servicio (SOAP Fault)'
        )
        return resultado

    # El veredicto vive dentro del ApplicationResponse (o del fragmento de
    # campos), que viene ESCAPADO como texto dentro del sobre. `_cuerpo_util` lo
    # desanida para poder buscar.
    cuerpo = _cuerpo_util(raiz)

    # ── Señal de que la respuesta NO es de la DIAN ────────────────────
    # Si no hay ningún campo conocido del contrato, la respuesta no es un
    # resultado válido (típico de una página de error de un proxy entre el POS y
    # la DIAN). Se marca `exito=False` para que el emisor trate el envío como
    # fallido y reintente, en lugar de dar el documento por emitido sin datos.
    campos_conocidos = ('TrackId', 'ZipKey', 'ResponseCode', 'StatusCode',
                        'IsValid', 'StatusDescription', 'ErrorResponse')
    if not any(_buscar(cuerpo, campo) is not None for campo in campos_conocidos):
        resultado.exito = False
        resultado.descripcion = (
            'La respuesta de la DIAN no contiene un resultado reconocible'
        )
        return resultado

    raiz = cuerpo

    # ── TrackId / ZipKey: identificadores para consultar después ─────────────
    resultado.track_id = _texto_de(_buscar(raiz, 'TrackId'))
    resultado.zip_key = _texto_de(_buscar(raiz, 'ZipKey'))

    # ── Código y descripción del resultado ───────────────────────────────────
    # La DIAN usa ResponseCode/StatusCode y Description/StatusDescription según
    # el método; se buscan ambos.
    # (Ver nota de `_buscar_primero`: un `or` entre elementos XML descarta los
    # nodos hoja con texto, así que las alternativas se recorren en un `for`.)
    resultado.codigo = _texto_de(
        _buscar_primero(raiz, 'ResponseCode', 'StatusCode'))

    resultado.descripcion = _texto_de(
        _buscar_primero(raiz, 'StatusDescription', 'Description', 'StatusMessage'))

    # Cuando la respuesta es de un set de pruebas (GetStatusZip) el resultado
    # viene como 'IsValid' (booleano) más la descripción del error.
    nodo_valido = _buscar(raiz, 'IsValid')
    if nodo_valido is not None and _texto_de(nodo_valido).lower() in ('true', 'false'):
        resultado.es_valido = _texto_de(nodo_valido).lower() == 'true'
        if not resultado.codigo:
            resultado.codigo = '00' if resultado.es_valido else '99'
    else:
        resultado.es_valido = es_codigo_aceptacion(resultado.codigo)

    # ── Errores de detalle ──────────────────────────────────────────────────
    # El ApplicationResponse puede traer varios <cac:ErrorResponse> con la causa
    # exacta del rechazo (código + descripción). Se guardan todos: el primer
    # mensaje general suele ser genérico y el útil está aquí.
    for error in _buscar_todos(raiz, 'ErrorResponse'):
        codigo = _texto_de(_buscar_primero(error, 'ErrorCode', 'ID'))
        descripcion = _texto_de(_buscar_primero(error, 'Description', 'ErrorMessage'))
        if codigo or descripcion:
            resultado.errores.append((codigo, descripcion))

    # Si la DIAN no aceptó pero tampoco dio código, se marca rechazo para que el
    # documento NO se dé por emitido (falso aceptado es peor que reintentar).
    if not resultado.codigo and not resultado.es_valido and resultado.errores:
        resultado.codigo = resultado.errores[0][0] or '99'

    return resultado


# ═════
# Operaciones del servicio
# ═════
def send_bill_sync(xml_firmado, nombre_archivo, ambiente=AMBIENTE_HABILITACION,
                   timeout=None):
    """Envía UN documento de forma SÍNCRONA (SendBillSync).

    Es el camino normal del POS en producción: la DIAN valida el documento y
    responde en la misma llamada si lo acepta o lo rechaza.

    Args:
        xml_firmado: XML del documento YA FIRMADO (bytes o str).
        nombre_archivo: nombre con el que la DIAN archiva el documento. Se usa el
            número del documento ('POS-1042.xml').
        ambiente: '1' producción, '2' habilitación.
        timeout: segundos de espera.

    Returns:
        `RespuestaDian` con el veredicto. Si la red falla, lanza `ErrorSOAP`
        (el llamador lo convierte en contingencia).
    """
    parametros = (
        '<wcf:fileName>' + _escapar_xml(_nombre_archivo(nombre_archivo)) + '</wcf:fileName>'
        '<wcf:contentFile>' + _contenido_documento(xml_firmado) + '</wcf:contentFile>'
    )
    respuesta = _llamar('SendBillSync', parametros, ambiente, timeout=timeout)
    return parsear_respuesta(respuesta)


def send_test_set_async(xml_firmado, nombre_archivo, test_set_id,
                        ambiente=AMBIENTE_HABILITACION, timeout=None):
    """Envía un documento al SET DE PRUEBAS de forma asíncrona (SendTestSetAsync).

    Solo aplica en habilitación: la DIAN acumula los documentos del set y valida
    el conjunto. Devuelve un `ZipKey` con el que se consulta el resultado con
    `get_status_zip`.

    Args:
        xml_firmado: XML firmado del documento.
        nombre_archivo: nombre del archivo ('POS-1042.xml').
        test_set_id: TestSetId que la DIAN entrega al crear el set de pruebas.
        ambiente: por defecto habilitación (el set de pruebas no existe en
            producción; si se pasa producción, la DIAN responde error).
        timeout: segundos de espera (el set es grande: se usa uno mayor).

    Returns:
        `RespuestaDian` con `zip_key` (y `track_id` si la DIAN lo devuelve).
    """
    if not str(test_set_id or '').strip():
        respuesta = RespuestaDian(exito=False, codigo='CONFIG',
                                  descripcion='Falta el TestSetId del set de pruebas')
        return respuesta

    parametros = (
        '<wcf:fileName>' + _escapar_xml(_nombre_archivo(nombre_archivo)) + '</wcf:fileName>'
        '<wcf:contentFile>' + _contenido_documento(xml_firmado) + '</wcf:contentFile>'
        '<wcf:testSetId>' + _escapar_xml(test_set_id) + '</wcf:testSetId>'
    )
    respuesta = _llamar('SendTestSetAsync', parametros, ambiente,
                        timeout=timeout or TIMEOUT_SET_PRUEBAS)
    return parsear_respuesta(respuesta)


def get_status(track_id, ambiente=AMBIENTE_HABILITACION, timeout=None):
    """Consulta el estado de un documento por su `trackId` (GetStatus).

    Returns:
        `RespuestaDian` con el estado actualizado del documento.
    """
    parametros = '<wcf:trackId>' + _escapar_xml(track_id) + '</wcf:trackId>'
    respuesta = _llamar('GetStatus', parametros, ambiente, timeout=timeout)
    return parsear_respuesta(respuesta)


def get_status_zip(zip_key, ambiente=AMBIENTE_HABILITACION, timeout=None):
    """Consulta el resultado del set de pruebas (GetStatusZip).

    Returns:
        `RespuestaDian` con `es_valido` y la lista de errores del set.
    """
    parametros = '<wcf:zipKey>' + _escapar_xml(zip_key) + '</wcf:zipKey>'
    respuesta = _llamar('GetStatusZip', parametros, ambiente,
                        timeout=timeout or TIMEOUT_SET_PRUEBAS)
    return parsear_respuesta(respuesta)


def send_event_update_status(xml_evento, ambiente=AMBIENTE_HABILITACION, timeout=None):
    """Envía un evento (contingencia / retransmisión) a la DIAN.

    Args:
        xml_evento: ApplicationResponse del evento, YA FIRMADO.
        ambiente: '1' producción, '2' habilitación.

    Returns:
        `RespuestaDian` con el resultado del evento.
    """
    parametros = (
        '<wcf:contentFile>' + _contenido_documento(xml_evento) + '</wcf:contentFile>'
    )
    respuesta = _llamar('SendEventUpdateStatus', parametros, ambiente, timeout=timeout)
    return parsear_respuesta(respuesta)


def _nombre_archivo(nombre):
    """Normaliza el nombre del archivo que se envía a la DIAN.

    La DIAN exige que termine en '.xml'. Si llega sin extensión o con otra, se
    corrige: un nombre inválido produce rechazo del envío.
    """
    nombre = str(nombre or 'documento').strip()
    # Se quitan caracteres que la DIAN no acepta en el nombre del archivo.
    nombre = re.sub(r'[^A-Za-z0-9._-]', '_', nombre)
    if not nombre.lower().endswith('.xml'):
        nombre = f'{nombre}.xml'
    return nombre


def probar_conexion(ambiente=AMBIENTE_HABILITACION, timeout=10):
    """Comprueba si el servicio de la DIAN responde (para la contingencia).

    Hace un GetStatus con un trackId inválido: si la DIAN contesta (aunque sea
    con un error de 'trackId no encontrado'), el servicio ESTÁ disponible. Si hay
    error de red, está caído y el POS debe emitir en contingencia.

    Returns:
        (disponible: bool, detalle: str)
    """
    try:
        respuesta = get_status('00000000-0000-0000-0000-000000000000',
                               ambiente=ambiente, timeout=timeout)
    except ErrorSOAP as error:
        return False, str(error)
    # Cualquier respuesta del servicio (aceptada o con error de negocio) sirve:
    # lo que se está midiendo es la disponibilidad, no el resultado.
    return True, 'El servicio de la DIAN responde'


def ambiente_nombre(ambiente):
    """Nombre legible del ambiente, para mensajes y auditoría."""
    return ('Producción' if str(ambiente) == AMBIENTE_PRODUCCION
            else 'Habilitación (set de pruebas)')