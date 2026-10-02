"""¿Por qué el digest del ApplicationResponse no cuadra?

En un evento el documento se firma 'enveloped': la Reference apunta al
ApplicationResponse SIN la firma. Pero el evento tiene una particularidad que
la factura no tiene: dentro lleva el documento al que se refiere, y ese
documento puede venir completo (`xml_documento`).

Se compara, byte a byte, el c14n que firma el proyecto con el que produce lxml
sobre el documento sin la firma.
"""
import base64
import hashlib
import sys
from pathlib import Path

from lxml import etree

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS = {'ds': NS_DS}

crudo = (RAIZ / '_auditoria_xml' / 'evento_firmado.xml').read_bytes()
arbol = etree.fromstring(crudo)
doc_id = arbol.get('ID')
firma = arbol.find('.//ds:Signature', NS)
ref = [r for r in firma.findall('.//ds:Reference', NS)
       if r.get('URI') == f'#{doc_id}'][0]
declarado = ref.find('ds:DigestValue', NS).text

sin_firma = etree.fromstring(crudo)
for n in sin_firma.findall('.//ds:Signature', NS):
    n.getparent().remove(n)

c14n = etree.tostring(sin_firma, method='c14n', exclusive=False,
                      with_comments=False)
calculado = base64.b64encode(hashlib.sha384(c14n).digest()).decode()

print(f'ID             : {doc_id}')
print(f'declarado      : {declarado}')
print(f'calculado      : {calculado}')
print(f'coincide       : {declarado == calculado}')
print()
print('--- c14n del evento sin la firma ---')
print(c14n[:1500].decode('utf-8', 'replace'))
print()
print('--- bytes finales ---')
print(c14n[-300:].decode('utf-8', 'replace'))