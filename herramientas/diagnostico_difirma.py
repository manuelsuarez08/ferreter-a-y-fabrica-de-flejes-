"""Diferencia EXACTA entre los bytes que firma el proyecto y los que verifica
el comprobador. Los primeros 220 coinciden, así que la diferencia está al final."""
import base64
import hashlib
import sys
from pathlib import Path

from lxml import etree

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS = {'ds': NS_DS}

crudo = (RAIZ / '_auditoria_xml' / 'invoice_firmado.xml').read_bytes()

# Bytes que firma el proyecto: los mismos que usa _firmar_documento_completo.
from lxml import etree as _E

arbol_et = _E.fromstring(crudo)
nodo_si = arbol_et.find('.//ds:SignedInfo', NS)
proyecto = _E.tostring(nodo_si, method='c14n', exclusive=False,
                       with_comments=False)

# Bytes que verifica el comprobador: c14n_de().
arbol_l = etree.fromstring(crudo)
si_l = arbol_l.find('.//ds:SignedInfo', NS)
declaraciones = {}
for ancestro in si_l.iterancestors():
    for prefijo, uri in ancestro.nsmap.items():
        declaraciones.setdefault(prefijo, uri)
for prefijo, uri in si_l.nsmap.items():
    declaraciones.setdefault(prefijo, uri)
envoltorio = etree.Element('c14n-envoltorio', nsmap=declaraciones)
copia = etree.fromstring(etree.tostring(si_l))
for k, v in si_l.attrib.items():
    copia.set(k, v)
envoltorio.append(copia)
completo = etree.tostring(envoltorio, method='c14n', exclusive=False,
                          with_comments=False)
verificador = completo[completo.index(b'>') + 1:-len(b'</c14n-envoltorio>')]

print('proyecto    :', len(proyecto), 'bytes')
print('verificador :', len(verificador), 'bytes')
print('iguales     :', proyecto == verificador)
print()
if proyecto != verificador:
    for i, (a, b) in enumerate(zip(proyecto, verificador)):
        if a != b:
            ini = max(0, i - 60)
            print(f'Primera diferencia en el byte {i}:')
            print('  proyecto    :', proyecto[ini:i + 60])
            print('  verificador :', verificador[ini:i + 60])
            break
    else:
        print('Coinciden hasta el final de la parte comun; difieren en la longitud.')
        n = min(len(proyecto), len(verificador))
        print('  cola proyecto   :', proyecto[n - 80:])
        print('  cola verificador:', verificador[n - 80:])

print()
print('digest proyecto    :',
      base64.b64encode(hashlib.sha384(proyecto).digest()).decode())
print('digest verificador :',
      base64.b64encode(hashlib.sha384(verificador).digest()).decode())