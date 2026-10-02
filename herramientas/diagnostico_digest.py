"""Aísla POR QUÉ no cuadra el digest, sin suposiciones.

Compara el digest que declara el proyecto con el que produce lxml sobre el
XML tal como está. Si no coinciden, la diferencia está en QUÉ se canonicaliza,
no en cómo. Este script imprime el c14n real del documento para poder comparar
a ojo con el que firmaría la DIAN.
"""
import base64
import hashlib
import sys
from pathlib import Path

from lxml import etree

RAIZ = Path(__file__).resolve().parent.parent
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS = {'ds': NS_DS,
      'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'}

crudo = (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes()
arbol = etree.fromstring(crudo)
doc_id = arbol.get('ID')

firma = arbol.find('.//ds:Signature', NS)
ref = [r for r in firma.findall('.//ds:Reference', NS)
       if r.get('URI') == f'#{doc_id}'][0]
declarado = ref.find('ds:DigestValue', NS).text

# Se quita la firma (Reference enveloped).
copia = etree.fromstring(crudo)
for n in copia.findall('.//ds:Signature', NS):
    n.getparent().remove(n)
raiz = copia

print(f'ID del documento: {doc_id}')
print(f'digest declarado: {declarado}')
print()
print('=' * 70)
print('C14N INCLUSIVO del documento sin la firma (lo que firma la DIAN):')
print('=' * 70)
c_inc = etree.tostring(raiz, method='c14n', exclusive=False, with_comments=False)
print(c_inc[:900].decode('utf-8', 'replace'))
print()
print('digest de ese c14n:',
      base64.b64encode(hashlib.sha384(c_inc).digest()).decode())
print()
print('=' * 70)
print('C14N EXCLUSIVO:')
print('=' * 70)
c_exc = etree.tostring(raiz, method='c14n', exclusive=True, with_comments=False)
print('digest:',
      base64.b64encode(hashlib.sha384(c_exc).digest()).decode())
print()
print('=' * 70)
print('C14N INCLUSIVO SIN comentarios, del SUBARBOL con sus namespaces:')
print('=' * 70)
print('digest:',
      base64.b64encode(hashlib.sha384(c_inc).digest()).decode())
print()
print('COINCIDE con el declarado?',
      base64.b64encode(hashlib.sha384(c_inc).digest()).decode() == declarado)