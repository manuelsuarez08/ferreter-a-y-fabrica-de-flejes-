"""¿El digest declarado corresponde al c14n real? Decisión con evidencia.

Compara TRES serializaciones del mismo nodo SignedInfo:
  A. ET.tostring (lo que usa `_canonizar` en el proyecto)
  B. lxml c14n2 reconstructivo (lo que hace el comprobador)
  C. lxml c14n2 sobre el árbol real, sin reconstruir

Si A != B, el fallo es del proyecto: `ET.tostring` no es canonicalización.
Si A == B y aun así no valida, el fallo es del comprobador.
"""
import base64
import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from lxml import etree

NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS = {'ds': NS_DS}

crudo = (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes()

# A. Lo que firma el proyecto: ElementTree, sin canonicalizar.
arbol_et = ET.fromstring(crudo.decode('utf-8'))
si_et = arbol_et.find(f'.//{{{NS_DS}}}SignedInfo')
serial_et = ET.tostring(si_et, encoding='utf-8', xml_declaration=False)

# B/C. lxml sobre el árbol real.
arbol_l = etree.fromstring(crudo)
si_l = arbol_l.find('.//ds:SignedInfo', NS)

# C. Canonicalización sobre el subárbol, dejando que lxml resuelva el scope.
try:
    c_real = etree.tostring(si_l, method='c14n2', exclusive=False,
                            with_comments=False)
except ValueError as exc:
    print(f'C. lxml no puede canonicalizar el subárbol aislado: {exc}')
    c_real = None

# B. Con los namespaces de los ancestros recreados en el nodo.
declaraciones = {}
for ancestro in si_l.iterancestors():
    for prefijo, uri in ancestro.nsmap.items():
        declaraciones.setdefault(uri, prefijo or '')
for d in si_l.iter():
    for prefijo, uri in d.nsmap.items():
        declaraciones.setdefault(uri, prefijo or '')
nsmap = {p: u for u, p in declaraciones.items() if p}
envoltorio = etree.Element(si_l.tag, nsmap=nsmap)
for k, v in si_l.attrib.items():
    envoltorio.set(k, v)
for hijo in si_l:
    envoltorio.append(hijo)
c_recon = etree.tostring(envoltorio, method='c14n2', exclusive=False,
                         with_comments=False)

declarado = None
for referencia in si_l.findall('ds:Reference', NS):
    if referencia.get('Type', '').endswith('SignedProperties'):
        continue
    d = referencia.find('ds:DigestValue', NS)
    if d is not None:
        declarado = d.text
        break

print('Digest declarado en la Reference del documento:')
print(' ', declarado)
print()
print('digest de ET.tostring      :',
      base64.b64encode(hashlib.sha384(serial_et).digest()).decode())
if c_real:
    print('digest de lxml (subárbol) :',
          base64.b64encode(hashlib.sha384(c_real).digest()).decode())
print('digest de lxml (reconstruido):',
      base64.b64encode(hashlib.sha384(c_recon).digest()).decode())
print()
print('ET.tostring == lxml reconstruido ?',
      hashlib.sha384(serial_et).digest() == hashlib.sha384(c_recon).digest())
print()
print('--- Serialización que el proyecto firma (primeros 400 bytes) ---')
print(serial_et[:400].decode('utf-8', 'replace'))
print()
print('--- Canonicalización real (primeros 400 bytes) ---')
print(c_recon[:400].decode('utf-8', 'replace'))