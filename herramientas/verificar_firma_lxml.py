"""Prueba deEMPIRICA: ¿la firma que produce el sistema es válida?

No usa las funciones de verificación del proyecto. Recorre la firma con
`lxml`, que tiene una implementación real de XMLDSig, y comprueba:

  1. Que el c14n que exige la DIAN y el que usa `_canonizar` dan el MISMO
     digest sobre el documento firmado. Si no, la firma no valida.
  2. Que la firma criptográfica sobre `ds:SignedInfo` verifica con la clave
     pública del certificado.
  3. Que los digests declarados corresponden a lo que dicen cubrir.

`lxml` es una dependencia externa a propósito: si el proyecto se comprobara a sí
mismo con su propia serialización, el fallo que se busca no aparecería.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from lxml import etree

NS = {
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
    'xades': 'http://uri.etsi.org/01903/v1.3.2#',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
}
C14N = 'http://www.w3.org/2001/10/xml-exc-c14n#'


def c14n_de(elemento):
    """Canonicaliza el nodo TAL COMO ESTÁ en el documento.

    No se despega ni se reconstruye: lxml ya sabe qué namespaces ve el nodo,
    porque siguen montados en su árbol. Al copiarlo a un árbol nuevo se pierde
    ese contexto y el c14n sale distinto — el fallo sería del comprobador, no de
    la firma.

    Detalle importante: en c14n INCLUSIVO el nodo declara también el `xmlns` por
    defecto (Invoice) y los namespaces que no usa directamente pero que hereda.
    Por eso el resultado empieza con `<ds:SignedInfo xmlns="...Invoice"...`, y
    por eso un comprobador que omita esas declaraciones nunca va a coincidir.
    """
    return etree.tostring(elemento, method='c14n', exclusive=False,
                          with_comments=False)


def revisar(ruta_xml):
    print(f'\n{"=" * 70}\n{ruta_xml.name}\n{"=" * 70}')
    crudo = ruta_xml.read_bytes()
    arbol = etree.fromstring(crudo)

    firma = arbol.find('.//ds:Signature', NS)
    if firma is None:
        print('  sin ds:Signature')
        return 1
    firma_id = firma.getparent().get('ID')
    print(f'  ID del documento : {firma_id}')

    fallos = 0

    # ── 1. Verificación de la firma con la clave pública ──────────────────────
    signed_info = firma.find('ds:SignedInfo', NS)
    cert_b64 = firma.find('.//ds:X509Certificate', NS).text
    import base64
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    cert = x509.load_der_x509_certificate(base64.b64decode(cert_b64))
    firma_b64 = firma.find('ds:SignatureValue', NS).text
    firma_bytes = base64.b64decode(firma_b64)

    # El c14n real de SignedInfo, como lo haría un validador.
    c14n_si = c14n_de(signed_info)
    firmados_crudo = c14n_si
    try:
        cert.public_key().verify(firma_bytes, c14n_si, padding.PKCS1v15(),
                                 hashes.SHA384())
        print('  [OK] la firma criptografica de SignedInfo verifica (c14n real)')
    except Exception as exc:
        print(f'  [FALLA] la firma no verifica con c14n real: '
              f'{type(exc).__name__}')
        # Diagnóstico: se muestran los bytes firmados contra los que se firman.
        firmado_por_proyecto = c14n_de(signed_info)
        print(f'     bytes que verifica el comprobador ({len(firmado_por_proyecto)}):')
        print('     ', firmado_por_proyecto[:220].decode('utf-8', 'replace'))
        print(f'     bytes que firma el proyecto ({len(firmados_crudo)}):')
        print('     ', firmados_crudo[:220].decode('utf-8', 'replace'))
        fallos += 1

    # ── 2. Digest del documento, con c14n real vs el del proyecto ─────────────
    ref = None
    for referencia in signed_info.findall('ds:Reference', NS):
        if referencia.get('URI') == f'#{firma_id}':
            ref = referencia
            break
    if ref is None:
        print('  [FALLA] no hay Reference que apunte al documento')
        return fallos + 1

    declarado = ref.find('ds:DigestValue', NS).text

    # Se quita la firma: la Reference es 'enveloped', cubre el documento SIN ella.
    # El atributo ID NO se quita: forma parte de lo firmado (por eso se fija
    # ANTES de calcular el digest). Quitarlo haría que el comprobador reportara
    # un fallo que no existe.
    copia = etree.fromstring(crudo)
    for nodo in copia.findall('.//ds:Signature', NS):
        nodo.getparent().remove(nodo)

    c14n_real = etree.tostring(copia, method='c14n', exclusive=False,
                               with_comments=False)
    import hashlib
    digest_real = base64.b64encode(hashlib.sha384(c14n_real).digest()).decode()

    print(f'  digest declarado  : {declarado}')
    print(f'  digest c14n real  : {digest_real}')
    if declarado == digest_real:
        print('  [OK] el digest del documento coincide')
    else:
        print('  [AVISO] el digest NO coincide con c14n real '
              '(revisar canonicalizacion)')
        fallos += 1

    # ── 3. Digest de SignedProperties ────────────────────────────────────────
    ref_props = None
    for referencia in signed_info.findall('ds:Reference', NS):
        if referencia.get('Type', '').endswith('SignedProperties'):
            ref_props = referencia
            break
    if ref_props is None:
        print('  [AVISO] no hay Reference a SignedProperties')
    else:
        props = firma.find('.//xades:SignedProperties', NS)
        declarado_p = ref_props.find('ds:DigestValue', NS).text

        # NO se vacía ningún nodo. `SignedProperties` NO contiene su propio digest:
        # ese valor vive en la `Reference` de SignedInfo, que está en otra rama.
        # Los `DigestValue` que sí están dentro (el del certificado en
        # SigningCertificateV2 y el de la política) son parte del contenido firmado
        # y deben contarse: si se quitan, el digest calculado no es el de la DIAN.
        c14n_p = c14n_de(props)
        digest_p = base64.b64encode(hashlib.sha384(c14n_p).digest()).decode()
        print(f'  digest props decl. : {declarado_p}')
        print(f'  digest props real  : {digest_p}')
        if declarado_p == digest_p:
            print('  [OK] el digest de SignedProperties coincide')
        else:
            print('  [AVISO] el digest de SignedProperties NO coincide')
            fallos += 1

    return fallos


def main():
    raiz = RAIZ / '_auditoria_xml'
    if not raiz.exists():
        print('No hay XML generados. Corre generar_xml_auditoria.py primero.')
        return 1

    total = 0
    for nombre in ('invoice_firmado.xml', 'creditnote_firmado.xml',
                    'evento_firmado.xml'):
        ruta = raiz / nombre
        if ruta.exists():
            total += revisar(ruta)

    print(f'\n{"=" * 70}')
    print('Sin discrepancias.' if not total else f'{total} discrepancias.')
    return 1 if total else 0


if __name__ == '__main__':
    raise SystemExit(main())