"""Valida la ESTRUCTURA de la firma contra el XSD de XMLDSig.

POR QUÉ ESTE ARCHIVO
--------------------
`verificar_firma_lxml.py` comprueba que la FIRMA verifique: recalcula los
digests y usa la clave pública. Eso es una cosa; la otra es que el documento sea
un `ds:Signature` VÁLIDO como estructura.

Son fallos independientes, y el segundo se escapa del primero: una firma puede
verificar perfectamente sobre un `ds:Signature` cuyos hijos están en el orden
equivocado. Un validador estricto lo rechaza al leer el esquema, antes de mirar
la rúbrica. Por eso hacen falta las dos comprobaciones.

QUÉ COMPRUEBA
-------------
`ds:Signature` es una SECUENCIA, no un conjunto. El XSD declara:

    SignedInfo, SignatureValue, KeyInfo?, Object*

Este archivo recorre los hijos de la firma en orden y compara con esa lista. Lo
que se salga de lugar o sobre se marca.

El proyecto GENERABA `SignedInfo -> Object -> SignatureValue -> KeyInfo`, que no
valida contra el XSD. La firma de ese documento verificaba, y el documento
seguía siendo inválido: por eso hace falta esta comprobación aparte.

`IssuerSerial` también se revisa: es un `X509IssuerSerialType`, o sea una
secuencia con `X509IssuerName` (texto) y `X509SerialNumber` (entero). El
proyecto ponía ahí un base64 del emisor, que no valida.

No usa XMLDSig completo: el anexo de la DIAN para el documento equivalente POS
tiene su propio XSD, y lo que se revisa aquí es la capa `ds:Signature` que es
compartida. Lo que falte del anexo propio se revisa aparte.
"""
from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

RAIZ = Path(__file__).resolve().parent.parent
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS = {'ds': NS_DS}

# La secuencia del XSD, en orden. `None` marca un elemento opcional que puede
# repetirse.
ORDEN_ESPERADO = [
    'SignedInfo',     # obligatorio, una vez
    'SignatureValue',  # obligatorio, una vez
    'KeyInfo',        # opcional, una vez
    'Object',         # opcional, una o más veces
]


def _local(tag):
    return tag.split('}')[-1] if '}' in tag else tag


def revisar_estructura(xml_bytes):
    """Revisa el orden de los hijos de `ds:Signature`.

    Returns:
        Lista de problemas encontrados (vacía si todo está bien).
    """
    problemas = []
    arbol = etree.fromstring(xml_bytes)
    firma = arbol.find('.//ds:Signature', NS)
    if firma is None:
        return ['no hay ds:Signature']

    hijos = [_local(h.tag) for h in firma if isinstance(h.tag, str)]
    print(f'  hijos de ds:Signature: {hijos}')

    # El orden debe ser una versión de la secuencia del XSD.
    esperado = [h for h in ORDEN_ESPERADO]
    if hijos != esperado:
        # Se busca el primer punto en el que se desvía.
        for i, (obtenido, debe) in enumerate(zip(hijos, esperado)):
            if obtenido != debe:
                problemas.append(
                    f'posición {i + 1}: es <{obtenido}> y el XSD exige <{debe}>')
                break
        else:
            problemas.append(
                f'faltan elementos del XSD: {esperado[len(hijos):]}')

    # KeyInfo debe traer el certificado.
    key_info = firma.find('ds:KeyInfo', NS)
    if key_info is None:
        problemas.append('falta ds:KeyInfo con el certificado')
    elif key_info.find('.//ds:X509Certificate', NS) is None:
        problemas.append('ds:KeyInfo no trae ds:X509Certificate')

    # IssuerSerial: X509IssuerName + X509SerialNumber, en ese orden.
    serial = key_info.find('.//ds:IssuerSerial', NS) if key_info is not None \
        else None
    if serial is not None:
        nombres = [_local(h.tag) for h in serial]
        if nombres != ['X509IssuerName', 'X509SerialNumber']:
            problemas.append(
                f'ds:IssuerSerial tiene {nombres}; el XSD exige '
                'X509IssuerName y luego X509SerialNumber')
        else:
            numero = serial.find('ds:X509SerialNumber', NS).text
            if not (numero or '').isdigit():
                problemas.append(
                    f'ds:X509SerialNumber debe ser un entero, es "{numero}"')

    # SignedInfo: los hijos también están ordenados por el XSD.
    signed_info = firma.find('ds:SignedInfo', NS)
    if signed_info is not None:
        for hijo in signed_info:
            nombre = _local(hijo.tag)
            if nombre != 'Reference':
                continue
            # Todos los hijos de Reference están ordenados, pero `Transforms` es
            # OPCIONAL: la Reference a SignedProperties no lleva ninguno (es el
            # contenido del propio nodo, no hay que transformarlo). Solo se
            # comprueba el ORDEN RELATIVO de los que haya, comparando cada uno
            # con el siguiente que sí está presente.
            orden_ref = ['Transforms', 'DigestMethod', 'DigestValue']
            hijos_si = [_local(h.tag) for h in hijo]
            presentes = [n for n in orden_ref if n in hijos_si]
            for i in range(len(presentes) - 1):
                if orden_ref.index(presentes[i]) > \
                        orden_ref.index(presentes[i + 1]):
                    problemas.append(
                        f'ds:Reference: <{presentes[i]}> aparece después de '
                        f'<{presentes[i + 1]}>, y el XSD exige el orden inverso')
                    break

    # La Reference al DOCUMENTO debe llevar el transform 'enveloped': es lo que
    # dice que la firma cubre el documento excluyendo el propio bloque de firma.
    # Sin él, el digest incluiría la propia firma y nunca podría cuadrar.
    enveloped = 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'
    if signed_info is not None:
        doc_id = arbol.get('ID')
        ref_doc = [r for r in signed_info.findall('ds:Reference', NS)
                   if r.get('URI') == f'#{doc_id}']
        if not ref_doc:
            problemas.append(
                f'no hay ninguna ds:Reference que apunte a #{doc_id}')
        else:
            transform = ref_doc[0].find(
                'ds:Transforms/ds:Transform', NS)
            if transform is None:
                problemas.append(
                    'la Reference al documento no declara el transform '
                    "'enveloped'")
            elif transform.get('Algorithm') != enveloped:
                problemas.append(
                    f'transform incorrecto: {transform.get("Algorithm")}')

    return problemas


def revisar_todos(carpeta=None):
    """Verifica la estructura de las firmas y devuelve el numero de problemas.

    Acepta la carpeta para que la suite apunte a un temporal y no a los
    artefactos de la ultima ejecucion del generador.
    """
    directorio = Path(carpeta) if carpeta else RAIZ / '_auditoria_xml'
    if not directorio.exists():
        print(f'No hay XML en {directorio}.')
        print('Corre generar_xml_firmado.py primero.')
        return 1

    total = 0
    for nombre in ('invoice_firmado.xml', 'creditnote_firmado.xml',
                   'evento_firmado.xml'):
        ruta = directorio / nombre
        if not ruta.exists():
            continue
        print(f'\n{nombre}')
        problemas = revisar_estructura(ruta.read_bytes())
        if problemas:
            for p in problemas:
                print(f'  [FALLA] {p}')
            total += len(problemas)
        else:
            print('  [OK] la estructura de ds:Signature cumple el XSD')

    print()
    print('Estructura correcta.' if not total else f'{total} problemas.')
    return total


def main():
    return 1 if revisar_todos() else 0


if __name__ == '__main__':
    raise SystemExit(main())