"""Pruebas de la tirilla POS: el bloque DIAN debe sobrevivir a la impresion.

CONTEXTO (el bug que estas pruebas fijan):
  El QR se dibujaba en un `<canvas>`. Al imprimir, la app hace
  `contenido = el.cloneNode(true)` y escribe ese clon en una ventana nueva.
  `cloneNode` copia el NODO pero no el CONTENIDO dibujado de un canvas, asi
  que la tirilla impresa salia con un rectangulo vacio donde iba el QR. Con
  `html2pdf` (que usa html2canvas) pasaba lo mismo.

  La solucion es un `<img>`: el PNG que entrega /api/dian/qr viaja como atributo
  `src` y si llega al clon.

Que se verifica:
  1. La plantilla declara un <img> para el QR (no un <canvas>).
  2. El bloque DIAN existe con sus nodos: estado, numero, CUIDE, QR, resolucion.
  3. El CUIDE y la resolucion se pintan desde el JS.
  4. El endpoint del QR devuelve un PNG.
  5. Los estilos del bloque DIAN existen en CSS_TIRILLA (el que se inyecta al
     imprimir), no solo en la hoja de pantalla.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_tirilla_dian.py -q
"""
import os
import re
import sys
import urllib.parse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

PLANTILLA = os.path.join(RAIZ, 'templates', 'index.html')
JS_DIAN = os.path.join(RAIZ, 'static', 'js', 'dian-pos.js')

with open(PLANTILLA, encoding='utf-8') as f:
    HTML = f.read()
with open(JS_DIAN, encoding='utf-8') as f:
    JS = f.read()


def _bloque_html():
    """El HTML del bloque DIAN de la tirilla, SIN comentarios.

    Se quitan los comentarios porque el propio bloque documenta el problema
    (`<canvas>`) y un busqueda ingenua lo detectaria como un canvas real.
    """
    m = re.search(r'<div id="fac-bloque-dian".*?</div>\s*(?=<div class="recibo-pie")',
                  HTML, re.S)
    assert m, 'no se encontró el bloque #fac-bloque-dian en la plantilla'
    return re.sub(r'<!--.*?-->', '', m.group(0), flags=re.S)


def _css_tirilla():
    """El CSS que se inyecta en la ventana de impresión y en el PDF."""
    m = re.search(r'const CSS_TIRILLA\s*=\s*`(.*?)`;', HTML, re.S)
    assert m, 'no se encontró la constante CSS_TIRILLA'
    return m.group(1)


# ── El QR debe ser un <img>, no un <canvas> ──────────────────────────────────
def test_el_qr_no_es_un_canvas():
    """Con canvas, cloneNode lo deja vacío al imprimir. Debe ser un <img>."""
    bloque = _bloque_html()
    assert '<canvas' not in bloque, (
        'el QR sigue en un <canvas>: al imprimir con cloneNode el contenido '
        'se pierde y la tirilla sale sin QR'
    )
    assert '<img id="fac-dian-qr"' in bloque, 'falta el <img> del QR'


def test_el_qr_tiene_alt_para_accesibilidad():
    bloque = _bloque_html()
    img = re.search(r'<img id="fac-dian-qr"[^>]*>', bloque)
    assert img, 'no se encontró el <img> del QR'
    assert 'alt=' in img.group(0), 'el <img> del QR necesita texto alternativo'


# ── Nodos del bloque DIAN ────────────────────────────────────────────────────
def test_el_bloque_tiene_los_nodos_esperados():
    bloque = _bloque_html()
    for nodo in ('fac-dian-estado', 'fac-dian-numero', 'fac-dian-cuide',
                 'fac-dian-qr', 'fac-dian-resolucion'):
        assert f'id="{nodo}"' in bloque, f'falta el nodo #{nodo}'


def test_el_bloque_arranca_oculto():
    """Sin documento emitido no se imprime nada de la DIAN."""
    bloque = _bloque_html()
    assert 'display:none' in bloque, (
        '#fac-bloque-dian debe iniciar oculto: imprimir el QR de un documento '
        'inexistente es peor que no imprimirlo'
    )


# ── El JS debe pintar el CUIDE, la resolución y el QR ───────────────────────
def test_el_js_pinta_el_cuide():
    assert 'fac-dian-cuide' in JS, 'el JS no pinta el CUIDE'
    assert "'CUIDE: '" in JS or '"CUIDE: "' in JS, (
        'el JS debe anteponer "CUIDE: " al valor'
    )


def test_el_js_pinta_el_qr_en_un_img():
    """El JS asigna el src al <img>, no a un canvas."""
    assert "getElementById('fac-dian-qr')" in JS
    assert 'imagen.src' in JS, 'el JS debe asignar el src del PNG al <img>'
    assert 'getContext' not in JS, (
        'quedó código de canvas en el JS: el QR ya no se dibuja a mano'
    )


def test_el_js_pinta_la_resolucion():
    """El anexo exige declarar la resolución de facturación."""
    assert 'fac-dian-resolucion' in JS, 'el JS no pinta la resolución'
    for clave in ('numero_resolucion', 'prefijo_resolucion', 'rango_hasta'):
        assert clave in JS, f'falta el dato de resolución: {clave}'


def test_el_js_oculta_el_qr_si_no_hay_url():
    """Sin URL el QR no se muestra, pero el CUIDE sigue."""
    bloque_js = JS
    assert 'if (!qrUrl)' in bloque_js, 'debe validar que haya URL'


# ── Los estilos deben existir en el CSS de impresión ─────────────────────────
def test_el_css_de_impresion_estila_el_bloque_dian():
    """CSS_TIRILLA es lo que se inyecta al imprimir y al hacer el PDF.

    Si el bloque DIAN solo tuviera estilos en la hoja de pantalla, al imprimir
    el QR saldría sin tamaño y el CUIDE desbordaría la tirilla.
    """
    css = _css_tirilla()
    assert '.recibo-dian' in css, 'CSS_TIRILLA no estila .recibo-dian'
    assert '.recibo-cuide' in css, 'CSS_TIRILLA no estila .recibo-cuide'
    assert '.recibo-dian-qr' in css, 'CSS_TIRILLA no estila el QR'


def test_el_qr_imprime_a_un_tamano_legible():
    """Un QR minúsculo o deformado no lo lee el escáner."""
    css = _css_tirilla()
    m = re.search(r'\.recibo-dian-qr\s*\{(.*?)\}', css, re.S)
    assert m, 'falta la regla .recibo-dian-qr'
    regla = m.group(1)
    assert 'width' in regla and 'mm' in regla, (
        'el QR debe tener un tamaño fijo en mm para la tirilla térmica'
    )
    assert 'object-fit' in regla, 'el QR debe conservar su proporción (1:1)'


def test_el_bloque_dian_no_se_parte_al_imprimir():
    """El CUIDE y el QR deben salir juntos, no cortados entre dos páginas."""
    css = _css_tirilla()
    m = re.search(r'\.recibo-dian\s*\{(.*?)\}', css, re.S)
    if m:
        assert 'page-break-inside' in m.group(1) or \
               'break-inside' in m.group(1), (
            'el bloque DIAN debería evitar cortes de página'
        )


def test_el_qr_oculto_no_ocupa_espacio():
    """Con `style="display:none"` en línea, el QR oculto no debe aparecer."""
    css = _css_tirilla()
    assert 'display: none !important' in css or \
           'display:none' in css, (
        'falta la regla que respeta el display:none en línea del QR'
    )


# ── El endpoint del QR ──────────────────────────────────────────────────────
def test_el_endpoint_devuelve_png():
    from ferreteria.app_factory import create_app
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'admin'
        s['rol'] = 'admin'

    url = ('https://catalogo-vpfe.dian.gov.co/document/searchqr'
           '?DocumentKey=' + 'a' * 96)
    r = c.get('/api/dian/qr?url=' + urllib.parse.quote(url))
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'image/png'
    assert r.data[:8] == b'\x89PNG\r\n\x1a\n', 'no es un PNG valido'
