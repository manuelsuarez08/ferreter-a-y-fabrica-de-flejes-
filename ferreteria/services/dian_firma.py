"""Firma digital XAdES-EPES del Documento Equivalente Electrónico POS.

Implementa la firma que la DIAN exige (Anexo Técnico 1.0, sección de firma
electrónica) sobre el certificado de firma electrónica del facturador:

    <ds:Signature>                          (enveloped, SHA-384)
      <ds:SignedInfo>
        <ds:CanonicalizationMethod>          c14n 1.0
        <ds:SignatureMethod>                 RSA-SHA384
        <ds:Reference URI="#<id del doc>">   SHA-384 + enveloped-signature
      <ds:SignatureValue>
      <ds:KeyInfo>
        <ds:X509Data><ds:X509Certificate>...
      <ds:Object>
        <xades:QualifyingProperties Target="#...">
          <xades:SignedProperties>
            <xades:SignedSignatureProperties>
              SigningTime, SigningCertificateV2, SignaturePolicyIdentifier
            <xades:SignedDataObjectProperties>
              <xades:DataObjectFormat>       (obligatorio en XAdES-EPES)

DECISIÓN DE DISEÑO: por qué `cryptography` y no `signxml`/`lxml`

La librería `signxml` resuelve la firma XML "en general", pero necesita `lxml`
(un paquete con binarios compilados). El POS corre en el mostrador de la
ferretería y también en Render, así que se prefiere la dependencia más pequeña
posible. `cryptography` es la librería de criptografía estándar de Python (y ya
viaja en muchos entornos), así que aquí se arma el XMLDSig a mano con
`xml.etree` y solo se delega en `cryptography` lo delicado: leer el PKCS#12,
extraer la clave privada y firmar con RSA-SHA384.

El módulo NO conoce la DIAN, ni la base de datos, ni Flask: recibe un XML y un
certificado y devuelve el XML firmado. Eso permite probarlo con un certificado
autofirmado generado en memoria.
"""
from __future__ import annotations

import base64
import hashlib
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .dian_xml import NS_CAC, NS_CBC, NS_DS, NS_EXT, NS_INVOICE, NS_STS

# ── Namespaces de XAdES ──────────────────────
# El anexo técnico pide XAdES-EPES 1.3.2 (namespace de 2006); la versión 1.4.1
# (2013) también es aceptada por la DIAN, pero 1.3.2 es la que documenta el
# anexo para el documento equivalente POS.
NS_XADES = 'http://uri.etsi.org/01903/v1.3.2#'
NS_XADES141 = 'http://uri.etsi.org/01903/v1.4.1#'
ET.register_namespace('xades', NS_XADES)

# Algoritmos exigidos por el anexo: SHA-384 para digests y RSA-SHA384 para la
# firma. La DIAN rechaza SHA-1 y SHA-256 en documentos nuevos.
ALG_C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
# c14n INCLUSIVO (el de arriba) es el que declara el SignatureMethod. Hay que
# canonicalizar EXACTAMENTE con el algoritmo que se declara, o la firma no
# verifica: el digest se calcula de una forma y el verificador rehace otra.
ALG_C14N_EXCLUSIVA = 'http://www.w3.org/2001/10/xml-exc-c14n#'
ALG_ENVELOPED = 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'
ALG_SHA384 = 'http://www.w3.org/2001/04/xmldsig-more#sha384'
ALG_RSA_SHA384 = 'http://www.w3.org/2001/04/xmldsig-more#rsa-sha384'

# Política de firma de la DIAN. El anexo técnico fija esta URL y este hash: si
# se cambia, la DIAN responde con el error de "política de firma no válida".
POLITICA_URL = 'https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf'
POLITICA_DESCRIPCION = 'Política de firma para facturas electrónicas de la República de Colombia.'
POLITICA_DIGEST = 'dMoMvtcG5eFhWpDrB3vHWmBTAcIh1S2iLmDmUuVjSbU='


class ErrorCertificado(Exception):
    """El certificado no se pudo leer, está vencido o la clave no coincide."""


class ErrorFirma(Exception):
    """El XML no se pudo firmar (estructura inesperada o fallo criptográfico)."""


# ═════
# Lectura del certificado .p12 / .pfx
# ═════
class Certificado:
    """Certificado de firma electrónica cargado desde un archivo PKCS#12.

    Mantiene juntos la clave privada, el certificado X.509 y su cadena, que es
    lo que necesita la firma. Se carga una sola vez y se reutiliza: leer el .p12
    es costoso y no debe hacerse en cada venta.
    """

    def __init__(self, clave_privada, certificado, cadena, ruta=None):
        self.clave_privada = clave_privada
        self.certificado = certificado
        self.cadena = cadena
        self.ruta = ruta

    @property
    def titular(self):
        """Nombre del titular del certificado (para mostrar en la interfaz)."""
        try:
            atributos = self.certificado.subject.get_attributes_for_oid(
                __import__('cryptography.x509', fromlist=['NameOID']).NameOID.COMMON_NAME
            )
            return atributos[0].value if atributos else ''
        except Exception:
            return ''

    @property
    def nit_titular(self):
        """NIT/documento del titular, si el certificado lo trae.

        Los certificados de firma electrónica colombianos incluyen el NIT en el
        campo `serialNumber` o en una extensión `subjectAltName`. Se busca en
        varios sitios porque cada entidad certificadora (Certicámara, ANDES,
        GSE...) lo pone en un lugar distinto.
        """
        from cryptography.x509 import NameOID
        from cryptography.x509.oid import ExtensionOID
        try:
            for oid in (NameOID.SERIAL_NUMBER,):
                attrs = self.certificado.subject.get_attributes_for_oid(oid)
                if attrs:
                    return ''.join(c for c in str(attrs[0].value) if c.isdigit())
            try:
                ext = self.certificado.extensions.get_extension_for_oid(
                    ExtensionOID.SUBJECT_ALTERNATIVE_NAME
                )
                for nombre in ext.value:
                    digitos = ''.join(c for c in str(nombre.value) if c.isdigit())
                    if len(digitos) >= 6:
                        return digitos
            except Exception:
                pass
        except Exception:
            pass
        return ''

    def vigente(self, momento=None):
        """True si el certificado está dentro de su periodo de validez."""
        momento = momento or datetime.now(timezone.utc)
        try:
            return (self.certificado.not_valid_before_utc
                    <= momento
                    <= self.certificado.not_valid_after_utc)
        except AttributeError:
            # cryptography < 42 exponía las fechas sin zona horaria.
            naive = momento.replace(tzinfo=None)
            return (self.certificado.not_valid_before
                    <= naive
                    <= self.certificado.not_valid_after)

    def dias_para_vencer(self, momento=None):
        """Días que faltan para que el certificado venza (puede ser negativo)."""
        momento = momento or datetime.now(timezone.utc)
        try:
            limite = self.certificado.not_valid_after_utc
        except AttributeError:
            limite = self.certificado.not_valid_after.replace(tzinfo=timezone.utc)
        return (limite - momento).days

    def fecha_vencimiento(self):
        """Fecha y hora de vencimiento del certificado, con zona horaria UTC.

        OJO: se usa `not_valid_after_utc` porque `not_valid_after` (sin sufijo)
        devuelve un datetime SIN zona horaria y `cryptography` lo marca como
        obsoleto: usarlo llena los logs de advertencias en cada arranque. El
        `getattr` es el respaldo para versiones antiguas de la librería, que solo
        tienen la propiedad sin zona horaria.
        """
        limite = getattr(self.certificado, 'not_valid_after_utc', None)
        if limite is not None:
            return limite
        return self.certificado.not_valid_after.replace(tzinfo=timezone.utc)


def cargar_certificado(ruta, clave, verificar_vigencia=True):
    """Carga un certificado .p12/.pfx desde disco.

    Args:
        ruta: ruta al archivo .p12/.pfx.
        clave: contraseña del archivo (str o bytes). Los .p12 de firma
            electrónica en Colombia casi siempre la traen.
        verificar_vigencia: si es True (por defecto) se rechaza un certificado
            vencido o aún no vigente. La DIAN rechazaría el documento de todos
            modos, y es mejor saberlo antes de intentar firmar.

    Returns:
        Un `Certificado` listo para firmar.

    Raises:
        ErrorCertificado: si el archivo no existe, la clave es incorrecta o el
            certificado está fuera de vigencia.
    """
    from cryptography.hazmat.primitives.serialization import pkcs12

    if not ruta:
        raise ErrorCertificado('No hay certificado configurado')
    if not os.path.exists(ruta):
        raise ErrorCertificado(f'No se encontró el archivo del certificado: {ruta}')

    with open(ruta, 'rb') as archivo:
        contenido = archivo.read()

    clave_bytes = clave.encode('utf-8') if isinstance(clave, str) else (clave or None)
    try:
        clave_privada, certificado, cadena = pkcs12.load_key_and_certificates(
            contenido, clave_bytes
        )
    except (ValueError, TypeError) as error:
        raise ErrorCertificado(
            'No se pudo abrir el certificado: revise la contraseña del archivo '
            f'(.p12/.pfx). Detalle: {error}'
        ) from error

    if clave_privada is None or certificado is None:
        raise ErrorCertificado('El archivo no contiene una clave privada y su certificado')

    resultado = Certificado(clave_privada, certificado, list(cadena or []), ruta=ruta)

    if verificar_vigencia and not resultado.vigente():
        raise ErrorCertificado(
            f'El certificado no está vigente (venció el '
            f'{resultado.certificado.not_valid_after:%Y-%m-%d}). Renueve el '
            'certificado de firma electrónica antes de emitir.'
        )
    return resultado


# ═════
# Coherencia entre el certificado y el emisor
# ═════
class ErrorIdentidadEmisor(ErrorCertificado):
    """El NIT del certificado no corresponde al NIT del emisor configurado.

    Hereda de `ErrorCertificado` a propósito: para quien emite, el problema es
    "el certificado sirve", y así un solo `except ErrorCertificado` lo cubre en
    los tres módulos que firman. Para el usuario el mensaje es explícito.
    """


def verificar_identidad_emisor(certificado, nit_emisor, exigido=True):
    """Comprueba que el NIT del certificado .p12 sea el del emisor del documento.

    Por qué este paso es obligatorio: la DIAN rechaza el documento si el firmante
    no coincide con el emisor declarado en el XML. Sin esta comprobación el
    rechazo llega como un error remoto, con el CUIDE ya consumido en el número de
    resolución y sin explicación útil ("El documento no cumple los requisitos").
    Compararlo antes de firmar convierte un rechazo fiscal opaco en un mensaje
    local y accionable.

    Args:
        certificado: un `Certificado` ya cargado.
        nit_emisor: NIT configurado en `configuracion.nit` (la ferretería).
        exigido: si es True (por defecto) y el certificado NO trae NIT legible,
            se falla. Un certificado sin NIT no se puede contrastar, y emitir a
            ciegas es exactamente lo que esta comprobación evita. En False se
            permite continuar (solo para pruebas automatizadas).

    Raises:
        ErrorIdentidadEmisor: si el NIT del certificado no es el del emisor, o si
            no se puede leer y `exigido` es True.
    """
    nit_emisor = ''.join(c for c in str(nit_emisor or '') if c.isdigit())
    if not nit_emisor:
        raise ErrorIdentidadEmisor(
            'No hay NIT de emisor configurado. Cargue el NIT de la ferretería en '
            'Configuración > Datos del negocio antes de emitir.'
        )

    nit_certificado = certificado.nit_titular

    if not nit_certificado:
        # No se puede comparar. Ciertamente hay certificados válidos sin el NIT
        # en los campos que sabemos leer, así que esto NO significa por sí solo
        # que el certificado sea inválido; significa que no hay garantía.
        if exigido:
            raise ErrorIdentidadEmisor(
                'No se pudo leer el NIT del titular en el certificado de firma, '
                'así que no se puede confirmar que sea el de esta ferretería '
                f'(NIT configurado: {nit_emisor}). Por seguridad la emisión se '
                'detiene. Verifique que el .p12 sea el certificado de la '
                'ferretería y que incluya el NIT, o emite en ambiente de '
                'habilitación para probar.'
            )
        return

    # Comparación por los últimos dígitos: algunos certificados incluyen el NIT
    # con el dígito de verificación separado ("123456789-1" → "1234567891") y
    # otros con guiones de por medio. Se normaliza a dígitos arriba, lo que suele
    # bastar; la comparación por sufijo cubre el caso en que el certificado
    # antepone un prefijo de país o tipo ("CO123456789").
    if nit_certificado != nit_emisor and not nit_certificado.endswith(nit_emisor):
        raise ErrorIdentidadEmisor(
            f'El certificado de firma es de otra persona: el NIT del certificado '
            f'es {nit_certificado} y el NIT del emisor configurado es '
            f'{nit_emisor}. La DIAN rechaza el documento cuando el firmante no '
            'es el emisor. Suba el certificado de la ferretería, o cambie el NIT '
            'de emisor para que coincidan.'
        )


# ═════
# Firma del documento
# ═════
def _sha384_b64(datos):
    """Digest SHA-384 en base64 (el formato que exige XMLDSig)."""
    return base64.b64encode(hashlib.sha384(datos).digest()).decode('ascii')


def _certificado_b64(certificado):
    """Certificado X.509 en base64 sin cabeceras PEM."""
    from cryptography.hazmat.primitives.serialization import Encoding
    der = certificado.public_bytes(Encoding.DER)
    return base64.b64encode(der).decode('ascii')


def _canonizar(elemento):
    """Serializa un elemento en canonicalización XML (c14n) real.

    ESTA FUNCIÓN ESTABA ROTA Y PRODUCEÍA FIRMAS INVÁLIDAS.
    -----------------------------------------------
    Antes hacía `ET.tostring(elemento)`, y se documentaba como "suficiente
    para el subconjunto que produce este proyecto". Medido contra `lxml`
    (que implementa c14n de verdad), NO lo era:

        proyecto  : <ns0:SignedInfo xmlns:ns0="...xmldsig#"><ns0:Reference ...>
        DIAN      : <ds:SignedInfo  xmlns:ds="...xmldsig#"><ds:Reference ...>

    El prefijo `ns0` es el que ElementTree genera cuando no hay registro de
    prefijos para ese namespace al serializar un subárbol. La DIAN calcula el
    digest sobre `<ds:...>`. Mismo contenido, bytes distintos, digest distinto:
    la firma NO verificaba con la clave pública del certificado. El documento
    completo era rechazado con "firma no válida".

    Tres cosas más que solo hace c14n real y `ET.tostring` no:

    1. Los prefijos de namespace que estaban declarados en los ANCESTROS se
       vuelven a declarar en el nodo que se canonicaliza. Sin esto, el digest de
       un subárbol depende de dónde cuelgue en el documento.
    2. Los elementos vacíos se cierran como `<a></a>`, no `<a />`. Otra forma
       de que los bytes no coincidan.
    3. El orden de atributos y de declaraciones es el canónico.

    `ALG_C14N` declara c14n INCLUSIVO, que es el que usan los validadores de la
    DIAN: se conservan TODOS los namespaces declarados en la cadena de ancestros
    (los "visibles"), no solo los que usa el nodo.

    `lxml` es una dependencia real (`requirements.txt`), no un extra de pruebas:
    sin canonicalización correcta no hay firma válida.
    """
    from lxml import etree as _etree

    conversion = _convertir_a_lxml(elemento)
    return _etree.tostring(conversion, method='c14n', exclusive=False,
                           with_comments=False)


def _convertir_a_lxml(elemento, prefijos=None):
    """Reconstruye un `ElementTree.Element` como árbol de lxml con sus namespaces.

    Al aislar un subárbol hay que recrear los `xmlns` que tenía de sus
    ancestros: si no, el nodo queda con prefijo `ns0` y el digest depende de
    dónde estaba colgado, no de qué contiene.

    `prefijos` se arrastra en la recursion para que hereden el mismo mapa los
    descendientes, que es lo que hace la canonicalizacion en el mundo real.
    """
    from lxml import etree as _etree

    if prefijos is None:
        prefijos = _prefijos_del_documento(elemento)

    conversion = _etree.Element(elemento.tag, nsmap=prefijos or None)
    for clave, valor in elemento.attrib.items():
        conversion.set(clave, valor)
    conversion.text = elemento.text
    for hijo in elemento:
        conversion.append(_convertir_a_lxml(hijo, prefijos))
        conversion[-1].tail = hijo.tail
    return conversion


def _prefijos_del_documento(elemento):
    """Prefijos de namespace declarados en la cadena que sube desde `elemento`.

    ElementTree no expone los ancestros de un `Element`, asi que se sube por la
    estructura del arbol solo cuando el elemento conserva su padre (lxml si lo
    tiene; ElementTree no). Cuando no hay padre — que es el caso de los subarboles
    que aqui se canonicalizan — se usan los prefijos REGISTRADOS en el modulo,
    que es la fuente de verdad de este proyecto: `register_namespace` fija
    'ds', 'xades', 'sts', 'ext'...
    """
    try:
        ancestros = list(elemento.iterancestors())
    except AttributeError:
        ancestros = []

    mapa = {}
    for ancestro in ancestros:
        for prefijo, uri in ancestro.nsmap.items():
            mapa.setdefault(uri, prefijo or '')
    if not mapa:
        # Sin ancestros disponibles se usan los prefijos que este modulo
        # REGISTRA. Es la unica fuente de verdad aqui dentro: los prefijos que
        # el proyecto escribe en el documento salen de estos `register_namespace`,
        # no de una convencion de ElementTree.
        registrados = {
            NS_DS: 'ds',
            NS_XADES: 'xades',
            NS_CBC: 'cbc',
            NS_CAC: 'cac',
            NS_STS: 'sts',
            NS_EXT: 'ext',
            NS_INVOICE: '',
        }
        for uri, prefijo in registrados.items():
            mapa.setdefault(uri, prefijo)

    nsmap = {prefijo: uri for uri, prefijo in mapa.items() if prefijo}
    # El namespace por defecto (sin prefijo) se pasa como None.
    for uri, prefijo in mapa.items():
        if not prefijo:
            nsmap[None] = uri
    return nsmap


def firmar_documento(raiz, certificado, id_documento=None, momento=None,
                     politica_url=POLITICA_URL, politica_digest=POLITICA_DIGEST):
    """Firma el documento XML en formato XAdES-EPES y devuelve los bytes firmados.

    El bloque `<ds:Signature>` se agrega como ÚLTIMO hijo del elemento raíz
    (firma enveloped), que es como lo exige el anexo técnico.

    Args:
        raiz: `Element` raíz del Invoice YA COMPLETO (todos los datos del
            documento). Si se modifica después de firmar, la firma deja de ser
            válida: la DIAN recalcula el digest y rechaza el documento.
        certificado: `Certificado` cargado con `cargar_certificado()`.
        id_documento: valor del atributo `ID` de la raíz. El anexo referencia el
            documento por su número; si no se pasa, se usa el `<cbc:ID>`.
        momento: fecha/hora de la firma (para pruebas reproducibles).
        politica_url, politica_digest: política de firma de la DIAN.

    Returns:
        Los bytes del XML firmado (UTF-8, con declaración XML).

    Raises:
        ErrorFirma: si el árbol no tiene el elemento raíz esperado.
    """
    if raiz is None:
        raise ErrorFirma('No hay documento que firmar')

    momento = momento or datetime.now(timezone.utc)
    if id_documento is None:
        nodo_id = raiz.find(f'{{{NS_CBC}}}ID')
        id_documento = (nodo_id.text if nodo_id is not None else None) or 'Documento'

    # El atributo ID en la raíz es lo que permite que la Reference apunte al
    # documento completo. Sin él la firma no se puede verificar.
    #
    # ORDEN CRÍTICO: el atributo se pone ANTES de calcular el digest, porque el
    # atributo forma parte del documento firmado. Si se añadiera después, el
    # digest declarado no correspondería al XML que se envía a la DIAN y esta lo
    # rechazaría con 'la firma no es válida'.
    raiz.set('ID', str(id_documento))

    # ── 1. Digest del documento (Reference #<id>) ────────────
    # NO se calcula aquí: se hace en el paso 7, sobre el documento montado.
    #
    # La razón es concreta. Al canonicalizar un árbol de ElementTree hay que
    # reconstruir a mano el mapa de prefijos, y esa reconstrucción depende del
    # NAMESPACE POR DEFECTO de la raíz, que cambia según el tipo de documento:
    # `Invoice` en la factura y la nota, `ar:ApplicationResponse` en el evento.
    # Con un mapa inventado el digest sale distinto y la firma no verifica.
    digest_documento = ''

    # ── 2. Armado del bloque Signature ───────────────────────
    firma = ET.Element(f'{{{NS_DS}}}Signature')
    firma.set('Id', f'Signature-{id_documento}')

    signed_info = ET.SubElement(firma, f'{{{NS_DS}}}SignedInfo')
    ET.SubElement(signed_info, f'{{{NS_DS}}}CanonicalizationMethod',
                  Algorithm=ALG_C14N)
    ET.SubElement(signed_info, f'{{{NS_DS}}}SignatureMethod',
                  Algorithm=ALG_RSA_SHA384)

    referencia_doc = ET.SubElement(signed_info, f'{{{NS_DS}}}Reference',
                                   Id=f'Reference-{id_documento}',
                                   URI=f'#{id_documento}')
    transformadas = ET.SubElement(referencia_doc, f'{{{NS_DS}}}Transforms')
    ET.SubElement(transformadas, f'{{{NS_DS}}}Transform', Algorithm=ALG_ENVELOPED)
    ET.SubElement(referencia_doc, f'{{{NS_DS}}}DigestMethod', Algorithm=ALG_SHA384)
    digest_nodo = ET.SubElement(referencia_doc, f'{{{NS_DS}}}DigestValue')
    digest_nodo.text = digest_documento  # se rellena en el paso 7

    # Reference a las propiedades firmadas (SignedProperties). XAdES exige que el
    # bloque de propiedades también esté cubierto por la firma.
    id_propiedades = f'SignedProperties-{id_documento}'
    referencia_prop = ET.SubElement(signed_info, f'{{{NS_DS}}}Reference',
                                    Id=f'Reference-{id_propiedades}',
                                    URI=f'#{id_propiedades}',
                                    Type='http://uri.etsi.org/01903#SignedProperties')
    ET.SubElement(referencia_prop, f'{{{NS_DS}}}DigestMethod', Algorithm=ALG_SHA384)
    digest_prop = ET.SubElement(referencia_prop, f'{{{NS_DS}}}DigestValue')

    # ── 3. Bloque XAdES con las propiedades firmadas ─────────────────────────
    objeto = ET.SubElement(firma, f'{{{NS_DS}}}Object')
    cualificadas = ET.SubElement(objeto, f'{{{NS_XADES}}}QualifyingProperties',
                                 Target=f'#{id_documento}')
    firmadas = ET.SubElement(cualificadas, f'{{{NS_XADES}}}SignedProperties',
                             Id=id_propiedades)
    props_firma = ET.SubElement(firmadas, f'{{{NS_XADES}}}SignedSignatureProperties')

    ET.SubElement(props_firma, f'{{{NS_XADES}}}SigningTime').text = (
        momento.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    )

    # SigningCertificateV2: hash del certificado de firma (obligatorio).
    certificado_firmante = ET.SubElement(
        props_firma, f'{{{NS_XADES}}}SigningCertificateV2'
    )
    cert_nodo = ET.SubElement(certificado_firmante, f'{{{NS_XADES}}}Cert')
    ET.SubElement(cert_nodo, f'{{{NS_DS}}}DigestMethod', Algorithm=ALG_SHA384)
    digest_cert_value = ET.SubElement(cert_nodo, f'{{{NS_DS}}}DigestValue')
    digest_cert_value.text = _sha384_b64(
        certificado.certificado.public_bytes(
            __import__('cryptography.hazmat.primitives.serialization',
                       fromlist=['Encoding']).Encoding.DER
        )
    )
    # Emisor del certificado: ayuda a la DIAN a resolver la cadena de confianza.
    emisor = ET.SubElement(certificado_firmante, f'{{{NS_XADES}}}IssuerSerialV2')
    emisor.text = base64.b64encode(
        certificado.certificado.issuer.public_bytes()
    ).decode('ascii')

    # Política de firma (EPES = Explicit Policy based Electronic Signature).
    politica = ET.SubElement(props_firma, f'{{{NS_XADES}}}SignaturePolicyIdentifier')
    politica_id = ET.SubElement(politica, f'{{{NS_XADES}}}SignaturePolicyId')
    ET.SubElement(politica_id, f'{{{NS_XADES}}}SigPolicyId').text = politica_url
    ET.SubElement(politica_id, f'{{{NS_XADES}}}SigPolicyDescription').text = (
        POLITICA_DESCRIPCION
    )
    hash_politica = ET.SubElement(politica_id, f'{{{NS_XADES}}}SigPolicyHash')
    ET.SubElement(hash_politica, f'{{{NS_DS}}}DigestMethod', Algorithm=ALG_SHA384)
    ET.SubElement(hash_politica, f'{{{NS_DS}}}DigestValue').text = politica_digest

    # DataObjectFormat: XAdES-EPES lo exige para describir qué se firmó.
    props_datos = ET.SubElement(firmadas, f'{{{NS_XADES}}}SignedDataObjectProperties')
    formato = ET.SubElement(props_datos, f'{{{NS_XADES}}}DataObjectFormat',
                            ObjectReference=f'#Reference-{id_documento}')
    ET.SubElement(formato, f'{{{NS_XADES}}}Description').text = (
        'Documento Equivalente Electrónico POS'
    )
    ET.SubElement(formato, f'{{{NS_XADES}}}MimeType').text = 'text/xml'
    ET.SubElement(formato, f'{{{NS_XADES}}}Encoding').text = 'UTF-8'

    # ── 4. Digest del bloque de propiedades firmadas ─────────────────────────
    # NO se calcula aquí. Este nodo todavía no está en su sitio dentro del
    # documento, y el digest depende de los namespaces que hereda. Se calcula
    # en el paso 7, junto con la firma, sobre el documento montado.
    digest_prop.text = ''

    # ── 5. Firma de SignedInfo con RSA-SHA384 ────────────────────────────────
    #
    # `SignedInfo` se canonicaliza DESPUÉS de estar colgado en el documento, no
    # como árbol suelto. La razón es concreta: la canonicalización depende de los
    # namespaces que el nodo VE, y eso lo dan sus ancestros. Si se firmara el
    # subárbol aislado, habria que reconstruir a mano ese mapa de prefijos y
    # cualquier prefijo olvidado (el `xsi`, por ejemplo) produce un digest
    # distinto del que recalcula la DIAN: la firma deja de verificar.
    #
    # Por eso aquí solo se coloca el nodo vacío; la firma real se calcula al
    # final, sobre el documento ya montado.
    firma_value = ET.SubElement(firma, f'{{{NS_DS}}}SignatureValue')
    firma_value.text = ''

    # ── 6. KeyInfo con el certificado (la DIAN verifica con la clave pública) ─
    key_info = ET.SubElement(firma, f'{{{NS_DS}}}KeyInfo')
    x509_data = ET.SubElement(key_info, f'{{{NS_DS}}}X509Data')
    ET.SubElement(x509_data, f'{{{NS_DS}}}X509Certificate').text = (
        _certificado_b64(certificado.certificado)
    )

    # La firma va al final del documento (enveloped).
    raiz.append(firma)

    # ── 7. Digests y firma sobre el documento montado ─────────────────────────
    #
    # Todo lo que depende de la canonicalización se hace AQUÍ, con el documento
    # ya completo, por una razón concreta: los nodos tienen que canonicalizarse
    # con los namespaces que heredan de sus ancestros, no como árboles sueltos.
    #
    # Antes se hacía en dos sitios y en los dos estaba mal: `ET.tostring` no es
    # canonicalización (el proyecto firmaba `<ns0:SignedInfo>` donde la DIAN
    # calcula sobre `<ds:SignedInfo>`), y aislar los nodos pierde `xsi` y
    # `xades`. El resultado era un documento cuya firma NO verificaba con la
    # clave pública del certificado: rechazo garantizado en la DIAN.
    _firmar_documento_completo(raiz, digest_nodo, digest_prop, firma_value,
                              certificado)

    return ET.tostring(raiz, encoding='utf-8', xml_declaration=True)


def _firmar_documento_completo(raiz, digest_nodo, digest_prop, firma_value,
                                certificado):
    """Calcula los dos digests y firma `SignedInfo`, en ese orden.

    Se serializa el documento completo, se vuelve a parsear con lxml y se
    canonicalizan los nodos YA COLGADOS en él. Es exactamente el camino que
    recorre el verificador de la DIAN: si aquí los digests cuadran, allá cuadran.

    El orden importa, y no es cosmético:
      1. digest del documento — exige quitar la firma (Reference 'enveloped');
      2. digest de `SignedProperties`;
      3. firma de `SignedInfo` — con el documento ya en su estado FINAL.
    Si se firmara antes de escribir los digests, se estarían firmando bytes que
    después cambian, y la firma no validaría.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree as _etree

    def _montado():
        """El documento con lo escrito hasta ahora, parseado por lxml."""
        crudo = ET.tostring(raiz, encoding='utf-8', xml_declaration=False)
        return _etree.fromstring(_sin_xmlns_duplicado(crudo))

    # (a) Digest del DOCUMENTO. La Reference es 'enveloped', así que cubre la
    # raíz sin el bloque de firma: se quita y se canonicaliza lo que queda.
    arbol = _montado()
    for sobrante in arbol.findall(f'.//{{{NS_DS}}}Signature'):
        sobrante.getparent().remove(sobrante)
    canonico_doc = _etree.tostring(arbol, method='c14n', exclusive=False,
                                   with_comments=False)
    digest_nodo.text = base64.b64encode(
        hashlib.sha384(canonico_doc).digest()).decode('ascii')

    # (b) Digest de SignedProperties, con el nodo en su contexto real.
    nodo_props = _montado().find(f'.//{{{NS_XADES}}}SignedProperties')
    if nodo_props is None:
        raise ErrorFirma('No se encontró el bloque SignedProperties')
    canonico_props = _etree.tostring(nodo_props, method='c14n', exclusive=False,
                                     with_comments=False)
    digest_prop.text = base64.b64encode(
        hashlib.sha384(canonico_props).digest()).decode('ascii')

    # (b) Firma de SignedInfo, sobre el documento ya actualizado.
    nodo_si = _montado().find(f'.//{{{NS_DS}}}SignedInfo')
    if nodo_si is None:
        raise ErrorFirma('No se encontró el bloque SignedInfo para firmar')
    canonico_si = _etree.tostring(nodo_si, method='c14n', exclusive=False,
                                  with_comments=False)
    firma_value.text = base64.b64encode(
        certificado.clave_privada.sign(canonico_si, padding.PKCS1v15(),
                                       hashes.SHA384())
    ).decode('ascii')


def _parsear(xml_bytes_o_texto):
    """Parsea un XML tolerando el `xmlns` duplicado del namespace Invoice.

    MOTIVO: `ET.tostring` puede emitir el atributo `xmlns` DOS veces para el
    namespace raíz si el mismo URI quedó registrado con dos prefijos distintos
    (ver la nota de `register_namespace` en `dian_xml`). Un `xmlns` repetido con
    el MISMO valor es un XML inválido para el parser (reporta 'duplicate
    attribute'), pero el valor es idéntico y el documento es correcto, así que se
    elimina la repetición antes de parsear en vez de fallar.

    Se eliminan solo las repeticiones EXACTAS y consecutivas: si alguna vez hay
    un `xmlns` con valor distinto, se deja intacto para que el parser lo reporte
    en lugar de tapar un problema real de namespaces.
    """
    if isinstance(xml_bytes_o_texto, bytes):
        texto = xml_bytes_o_texto.decode('utf-8')
    else:
        texto = xml_bytes_o_texto

    # Declaración xmlns repetida con el mismo valor, separada solo por espacios.
    return _sin_xmlns_duplicado(texto)


def _sin_xmlns_duplicado(texto_o_bytes):
    """Quita el `xmlns` repetido y devuelve BYTES listos para cualquier parser.

    `ET.tostring` puede emitir el `xmlns` DOS veces para el namespace raíz si el
    mismo URI quedó registrado con dos prefijos distintos (ver la nota de
    `register_namespace` en `dian_xml`). Un `xmlns` repetido con el MISMO valor
    es un XML inválido ('duplicate attribute') tanto para ElementTree como para
    lxml, así que se limpia antes de parsear.

    Se eliminan solo las repeticiones EXACTAS y consecutivas: si alguna vez hay
    un `xmlns` con valor distinto, se deja intacto para que el parser lo reporte
    en lugar de tapar un problema real de namespaces.

    Devuelve bytes, no un Element: la usan tanto `_parsear` (ElementTree) como
    `_firmar_signed_info` (lxml), y antes una devolvía Element y la otra bytes,
    lo que rompía el firma con 'a bytes-like object is required'.
    """
    import re
    if isinstance(texto_o_bytes, bytes):
        texto = texto_o_bytes.decode('utf-8')
    else:
        texto = texto_o_bytes
    patron = re.compile(r'(\sxmlns="([^"]+)")(\s+xmlns="\2")+')
    return patron.sub(r'\1', texto).encode('utf-8')


def firmar_bytes(xml_bytes, certificado, id_documento=None, momento=None):
    """Firma un XML ya serializado (comodidad para el flujo del servicio).

    Returns:
        Los bytes del XML firmado.
    """
    raiz = ET.fromstring(_parsear(xml_bytes))
    return firmar_documento(raiz, certificado, id_documento=id_documento,
                            momento=momento)


def tiene_firma(xml_bytes_o_texto):
    """True si el XML ya trae un bloque `<ds:Signature>`.

    Se usa para no firmar dos veces (una firma duplicada hace que la DIAN
    rechace el documento por esquema).
    """
    if isinstance(xml_bytes_o_texto, str):
        return ('ds:Signature' in xml_bytes_o_texto
                or f'{{{NS_DS}}}Signature' in xml_bytes_o_texto)
    try:
        raiz = _parsear(xml_bytes_o_texto)
    except ET.ParseError:
        return False
    return raiz.find(f'{{{NS_DS}}}Signature') is not None