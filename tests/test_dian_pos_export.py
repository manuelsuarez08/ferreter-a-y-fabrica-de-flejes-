"""Prueba de regresión del fallo que cortaba el modal de factura.

Una función existe en el archivo, se usa desde otro lado, pero NO está en el
objeto que se exporta. En JS eso no da ReferenceError sino TypeError, porque
el guardia `window.DianPos && window.DianPos.estaEmitida(...)` pasa (el objeto
existe) y revienta al llamar a la propiedad ausente.

Aquí se cruzaron los dos patrones:
  - `badgeFacturacion`   : se USA pero no se DEFINÍA (el día anterior).
  - `DianPos.estaEmitida` : se DEFINE pero no se EXPORTABA (este).
Los dos cortan la función que los contiene, y si esa función es `async` el
error desaparece como promesa rechazada: nada en consola, modal mudo.
"""
import io
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIAN = os.path.join(RAIZ, 'static', 'js', 'dian-pos.js')
PLANTILLA = os.path.join(RAIZ, 'templates', 'index.html')

with io.open(DIAN, encoding='utf-8') as f:
    dian = f.read()
with io.open(PLANTILLA, encoding='utf-8') as f:
    html = f.read()

codigo = re.sub(r'/\*.*?\*/', '', html, flags=re.S)
codigo = re.sub(r'(?m)//[^\n]*$', '', codigo)

# Palabras clave y constructores que no son funciones del proyecto.
PALABRAS_CLAVE = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'typeof',
    'new', 'await', 'do', 'else', 'try', 'String', 'Number', 'Boolean', 'Array',
    'Object', 'Date', 'JSON', 'Math', 'parseInt', 'parseFloat', 'isNaN',
    'setTimeout', 'setInterval', 'clearTimeout', 'fetch', 'alert', 'confirm',
    'prompt', 'Map', 'Set', 'FormData', 'URLSearchParams', 'RegExp', 'Error',
    'Promise', 'Symbol', 'decodeURIComponent', 'encodeURIComponent',
    'structuredClone', 'ErrorEvent', 'Event', 'CustomEvent', 'decodeURI',
    'encodeURI', 'escape', 'unescape', 'isFinite', 'BigInt', 'Proxy', 'Reflect',
    'WeakMap', 'WeakSet', 'Intl', 'Number.parseFloat', 'parseFloat',
}

# ── 1. Todo lo que se llama como DianPos.algo debe estar exportado ──
m = re.search(r'global\.DianPos\s*=\s*\{(.*?)\n\s*\};', dian, re.S)
assert m, 'no se encontro el export de DianPos'
exportadas = set(re.findall(r'(\w+)\s*:', m.group(1)))

llamadas = set(re.findall(r'DianPos\.(\w+)', codigo))
print('DianPos exporta:', sorted(exportadas))
print('DianPos se usa :', sorted(llamadas))

faltan = sorted(llamadas - exportadas)
print()
print('=== METODOS QUE SE USAN PERO NO ESTAN EXPORTADOS ===')
print('  ', faltan or 'ninguno')
assert not faltan, (
    f'DianPos no exporta {faltan}, pero el JS los llama. '
    f'TypeError en tiempo de ejecucion: el guardia `window.DianPos &&` '
    f'pasa (el objeto existe) y la llamada a la propiedad ausente revienta.')

# ── 2. Todo lo que se define en dian-pos.js que verFactura necesita ──
for nombre in ('estaEmitida', 'pintarBloque'):
    assert nombre in exportadas, f'DianPos.{nombre} no esta exportado'


# ═════════════════════════════════════════════════════
# La misma clase de bug, en la otra direccion
# ═════════════════════════════════════════════════════
def test_toda_funcion_del_js_que_se_usa_esta_definida():
    """El otro patron: `badgeFacturacion` se USABA sin definirse.

    Se comprueba contra TODAS las funciones que el JS invoca, no solo las de
    un onclick: un fallo dentro de una async no se ve en consola.
    """
    bloque = next(b for b in re.findall(r'<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>', html, re.S)
                  if 'function verFactura' in b)
    t = re.sub(r'/\*.*?\*/', '', bloque, flags=re.S)
    t = re.sub(r'(?m)//[^\n]*$', '', t)

    definidas = set(re.findall(r'function\s+(\w+)\s*\(', t))
    definidas |= set(re.findall(r'(?:const|let|var)\s+(\w+)\s*=', t))
    # Los metodos de DianPos tambien son validos.
    definidas |= exportadas
    externas = {'bootstrap', 'TomSelect', 'html2pdf', 'Html5Qrcode', '$', 'jQuery',
                'getOrCreateInstance', 'getInstance', 'fetchJson', 'sesionExpirada',
                'url_for', 'url_for_static', 'get_flashed_messages'}
    huerfanas = set()
    for mm in re.finditer(r'(?<![\w.$>])([A-Za-z_$][\w$]*)\s*\(', t):
        n = mm.group(1)
        if n in definidas or n in externas:
            continue
        if n in PALABRAS_CLAVE:
            continue
        # Solo cuentan los nombres con convención de código: llevan `$` o `_`,
        # o son camelCase / PascalCase. Así se filtran las palabras en español
        # que aparecen en comentarios ("IVA (19%)", "Mostrador (General)").
        if not (('$' in n) or ('_' in n) or re.search(r'[a-z][A-Z]', n)):
            continue
        huerfanas.add(n)

    print('funciones invocadas sin definir:', sorted(huerfanas) or 'ninguna')
    assert not huerfanas, f'funciones invocadas pero no definidas: {sorted(huerfanas)}'
