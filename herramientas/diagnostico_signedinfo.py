"""Aísla los dos fallos que quedan, sin suposiciones.

1. El digest de SignedProperties no coincide. En XMLDSig, ese Reference apunta
   a un nodo por ID y el verificador lo localiza por `Id`. Aquí se calcula sobre
   el nodo aislado. Si el verificador lo reconstruye distinto, el fallo es del
   comprobador.

2. La firma no verifica. En XMLDSig se firma el `SignedInfo` YA CANONICALIZADO
   dentro del documento. Aquí el proyecto lo firma de otra forma. Se comparan
   byte a byte.
"""
import base64
import hashlib
import sys
from pathlib import Path

from lxml import etree

RAIZ = Path(__file__).resolve().parent.parent
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS_XADES = 'http://uri.etsi.org/01903/v1.3.2#'
NS = {'ds': NS_DS, 'xades': NS_XADES}

crudo = (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes()
arbol = etree.fromstring(crudo)
firma = arbol.find('.//ds:Signature', NS)

print('=' * 70)
print('1. SignedInfo: bytes que el proyecto firma vs. los del documento')
print('=' * 70)
si_doc = firma.find('ds:SignedInfo', NS)

# Del documento, en su contexto (lo que firmaría la DIAN).
cadena = list(si_doc.iterancestors())
cadena.reverse()
cadena.append(si_doc)
raiz_c = None
padre = None
for nivel in cadena:
    nuevo = etree.Element(nivel.tag, nsmap=nivel.nsmap)
    for k, v in nivel.attrib.items():
        nuevo.set(k, v)
    padre = nuevo if padre is None else padre
    if padre is nuevo:
        raiz_c = nuevo
    else:
        padre.append(nuevo)
    padre = nuevo
c14n_doc = etree.tostring(raiz_c, method='c14n', exclusive=False,
                          with_comments=False)
print(f'longitud canonicalizada en contexto: {len(c14n_doc)}')
print('primeros 300:')
print(c14n_doc[:300].decode('utf-8', 'replace'))

# Aislado, sin ancestros.
aislado = etree.fromstring(etree.tostring(si_doc))
c14n_aislado = etree.tostring(aislado, method='c14n', exclusive=False,
                              with_comments=False)
print()
print(f'longitud canonicalizada aislada    : {len(c14n_aislado)}')
print('primeros 300:')
print(c14n_aislado[:300].decode('utf-8', 'replace'))
print()
print('¿Coinciden?', c14n_doc == c14n_aislado)

print()
print('=' * 70)
print('2. SignedProperties')
print('=' * 70)
props = firma.find('.//xades:SignedProperties', NS)
props_id = props.get('Id')
print(f'Id del nodo  : {props_id}')
ref_p = [r for r in firma.findall('.//ds:Reference', NS)
         if r.get('URI') == f'#{props_id}'][0]
print('URI de la Reference:', ref_p.get('URI'))
declarado = ref_p.find('ds:DigestValue', NS).text

cadena_p = list(props.iterancestors())
cadena_p.reverse()
cadena_p.append(props)
padre = None
raiz_p = None
for nivel in cadena_p:
    nuevo = etree.Element(nivel.tag, nsmap=nivel.nsmap)
    for k, v in nivel.attrib.items():
        nuevo.set(k, v)
    if padre is None:
        raiz_p = nuevo
    else:
        padre.append(nuevo)
    padre = nuevo
c14n_p = etree.tostring(raiz_p, method='c14n', exclusive=False,
                        with_comments=False)
print('digest declarado:', declarado)
print('digest en contexto:',
      base64.b64encode(hashlib.sha384(c14n_p).digest()).decode())