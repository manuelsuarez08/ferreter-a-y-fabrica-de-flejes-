"""Extrae el Anexo Tecnico V1.9 a texto y lo indexa por terminos.

El PDF es la norma. Este script lo deja como texto consultable para que las
decisiones del generador puedan citarse en vez de inferirse.

NO guarda datos del proyecto: solo convierte el PDF en un archivo de texto con
el numero de pagina de cada linea. El numero de pagina es lo importante: permite
decir "el Anexo, pagina 214, dice que X" y que sea verificable.
"""
import re
import sys
from pathlib import Path

import pypdf

PDF = Path(r'c:\Users\Manuela Bedoya\Desktop'
           r'\Anexo-Tecnico-Factura-Electronica-de-Venta-vr-1-9.pdf')
RAIZ = Path(__file__).resolve().parent.parent
TEXTO = RAIZ / 'docs' / 'anexo_tecnico_v1_9.txt'


def limpiar(texto):
    """Quita el encabezado repetido de cada pagina de la Resolucion.

    Siete lineas por pagina x 753 paginas = 5000 lineas de ruido que输出的每一
    coincidencia de busqueda. Se quitan para que el indice signifique algo.
    """
    ruido = re.compile(
        r'^\s*(Resoluci[oó]n No\. 000165.*|Direcci[oó]n de Gesti[oó]n de '
        r'Impuestos\s*|Carrera 8 .*|C[oó]digo postal 111711\s*|'
        r'www\.dian\.gov\.co\s*|Formule su petici[oó]n.*|P[aá]gina \d+ de 753\s*'
        r'|Unidad Administrativa Especial.*)$')
    lineas = []
    for linea in texto.splitlines():
        if ruido.match(linea):
            continue
        lineas.append(linea)
    return '\n'.join(lineas)


def main():
    if not PDF.exists():
        sys.exit(f'No existe el PDF: {PDF}')

    lector = pypdf.PdfReader(str(PDF))
    partes = []
    for indice, pagina in enumerate(lector.pages, 1):
        texto = pagina.extract_text() or ''
        partes.append(f'\n<<<PAGINA {indice}>>>\n{limpiar(texto)}')

    TEXTO.parent.mkdir(exist_ok=True)
    TEXTO.write_text(''.join(partes), encoding='utf-8')

    lineas = TEXTO.read_text(encoding='utf-8').splitlines()
    utiles = [l for l in lineas if l.strip() and not l.startswith('<<<PAGINA')]
    print(f'PDF        : {PDF.name}')
    print(f'Paginas    : {len(lector.pages)}')
    print(f'Escrito en : {TEXTO}')
    print(f'Lineas utiles: {len(utiles)}')


def buscar(termino, limite=25):
    """Busca un termino y devuelve pagina + linea."""
    if not TEXTO.exists():
        main()
    lineas = TEXTO.read_text(encoding='utf-8').splitlines()
    pagina = 0
    encontradas = []
    for linea in lineas:
        m = re.match(r'<<<PAGINA (\d+)>>>', linea)
        if m:
            pagina = int(m.group(1))
            continue
        if re.search(termino, linea, re.I):
            encontradas.append((pagina, linea.strip()))
    for p, l in encontradas[:limite]:
        print(f'p.{p:>4}  {l[:150]}')
    print(f'\n{len(encontradas)} coincidencias de /{termino}/')
    return encontradas


if __name__ == '__main__':
    if len(sys.argv) > 1:
        buscar(' '.join(sys.argv[1:]))
    else:
        main()