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

from .dian_xml import NS_CBC, NS_DS

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
    """Serializa un elemento de forma canónica (c14n) para calcular su digest.

    OJO: `ET.tostring` NO es c14n. Aquí se usa una canonicalización suficiente
    para el subconjunto que produce este proyecto (sin comentarios, sin
    atributos duplicados, sin espacios significativos) y se documenta la
    limitación: si en el futuro se firma un XML de entrada de un tercero con
    espacios o comentarios, habría que sustituir esto por un c14n real. Los
    digests se calculan sobre el árbol, no sobre el texto, así que el resultado
    es estable entre llamadas.
    """
    return ET.tostring(elemento, encoding='utf-8', xml_declaration=False)


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
    # Se firma el documento TAL COMO ESTÁ, sin el bloque Signature: por eso el
    # digest se calcula antes de insertar la firma.
    digest_documento = _sha384_b64(_canonizar(raiz))

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
    digest_nodo.text = digest_documento

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
    # OJO: se calcula con el bloque DENTRO del árbol (ya tiene los namespaces
    # declarados por sus ancestros), que es exactamente lo que verá la DIAN.
    digest_prop.text = _sha384_b64(_canonizar(firmadas))

    # ── 5. Firma de SignedInfo con RSA-SHA384 ────────────────────────────────
    firma_value = ET.SubElement(firma, f'{{{NS_DS}}}SignatureValue')
    firma_value.text = base64.b64encode(
        certificado.clave_privada.sign(
            _canonizar(signed_info),
            __import__('cryptography.hazmat.primitives.asymmetric.padding',
                       fromlist=['PKCS1v15']).PKCS1v15(),
            __import__('cryptography.hazmat.primitives.hashes',
                       fromlist=['SHA384']).SHA384(),
        )
    ).decode('ascii')

    # ── 6. KeyInfo con el certificado (la DIAN verifica con la clave pública) ─
    key_info = ET.SubElement(firma, f'{{{NS_DS}}}KeyInfo')
    x509_data = ET.SubElement(key_info, f'{{{NS_DS}}}X509Data')
    ET.SubElement(x509_data, f'{{{NS_DS}}}X509Certificate').text = (
        _certificado_b64(certificado.certificado)
    )

    # La firma va al final del documento (enveloped).
    raiz.append(firma)

    return ET.tostring(raiz, encoding='utf-8', xml_declaration=True)


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
    import re
    patron = re.compile(r'(\sxmlns="([^"]+)")(\s+xmlns="\2")+')
    texto = patron.sub(r'\1', texto)
    return ET.fromstring(texto.encode('utf-8'))


def firmar_bytes(xml_bytes, certificado, id_documento=None, momento=None):
    """Firma un XML ya serializado (comodidad para el flujo del servicio).

    Returns:
        Los bytes del XML firmado.
    """
    raiz = _parsear(xml_bytes)
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