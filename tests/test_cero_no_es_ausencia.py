"""La clase de bug del CERO tratado como "falso".

Tres veces, en tres módulos distintos, un 0 legítimo desapareció por usar
`or`/`not` como si significaran "no informado":

  1. `dian_emision._leer_items`:   `elif not iva_tasa` → un producto de tasa
     CERO (cemento de uso arquitectónico) acababa con 19% en el XML.
  2. `dian_nota_credito`:          `elif not iva_tasa` → la nota crédito de
     ese producto declaraba un impuesto que nunca se cobró. La DIAN rechaza
     una corrección que no cuadra con el documento que corrige.
  3. Modal de producto rápido:    `value || 19` → elegir "0% (tasa cero)"
     guardaba 19, porque en JavaScript "0" es falsy.

En Python `not 0` es True y en JS `"0" || 19` da 19. El cero es un VALOR, no
una ausencia: en este dominio significa "tasa cero", "monto cero" o "cero
unidades", y es exactamente lo que más caro sale confundir con "no informado".

Estas pruebas fijan los tres sitios y_barren el código buscando el patrón.
"""
import io
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _leer(rel):
    with io.open(os.path.join(RAIZ, rel), encoding='utf-8') as f:
        # Se limpian comentarios: nombran el código viejo para explicar el bug.
        t = re.sub(r'"""(?:.|\n)*?"""', '', f.read())
        t = re.sub(r"'''(?:.|\n)*?'''", '', t)
        t = re.sub(r'#.*$', '', t, flags=re.M)
        t = re.sub(r'//.*$', '', t, flags=re.M)
        return t


# ═════════════════════════════════════════════════════
# 1. La emisión de la venta
# ═════════════════════════════════════════════════════
def test_la_emision_no_grava_un_producto_de_tasa_cero():
    """El XML de una venta declara tasa cero para lo que no se gravó."""
    src = _leer('ferreteria/services/dian_emision.py')
    m = re.search(r'if naturaleza in \(.exento., .no_sujeto.\) or iva_tasa == 0:', src)
    assert m, (
        'dian_emision._leer_items debe comprobar `iva_tasa == 0` de forma '
        'explícita. Con `not iva_tasa` un producto de tasa cero terminaba '
        'gravado al 19% en el documento electrónico')
    assert 'elif not iva_tasa' not in src, (
        '`not iva_tasa` es True cuando la tasa es 0: vuelve a colar la tasa '
        'global en un artículo de tasa cero')


# ═════════════════════════════════════════════════════
# 2. La nota crédito
# ═════════════════════════════════════════════════════
def test_la_nota_credito_usa_la_misma_regla_que_el_documento_que_corrige():
    """La nota debe declarar el mismo impuesto que la factura original.

    Si difieren, la DIAN rechaza la corrección: no se puede descontar un
    impuesto que no se cobró.
    """
    src = _leer('ferreteria/services/dian_nota_credito.py')
    assert 'or iva_tasa == 0:' in src, (
        'la nota crédito debe reconocer la tasa cero explícitamente')
    assert 'elif not iva_tasa' not in src, (
        '`not iva_tasa` en la nota crédito deja gravar un artículo de tasa cero')


# ═════════════════════════════════════════════════════
# 3. El modal de producto rápido
# ═════════════════════════════════════════════════════
def test_el_modal_de_producto_rapido_guarda_la_tasa_que_se_eligio():
    """Elegir "0% (tasa cero)" tiene que guardar 0, no 19."""
    src = _leer('templates/index.html')
    m = re.search(r'iva_tasa:\s*Number\(([^\n]+)\)', src)
    assert m, 'no se encontró el iva_tasa del modal de producto rápido'
    expresion = m.group(1)
    assert '||' not in expresion, (
        f'el modal usa `{expresion}`: en JavaScript "0" es falsy, así que elegir '
        f'tasa cero guardaría 19')


# ═════════════════════════════════════════════════════
# Barrido: que no vuelva a aparecer
# ═════════════════════════════════════════════════════
CAMPOS = 'iva_tasa|iva_porcentaje|iva_valor|precio_base|stock_actual'


def test_python_no_trata_un_cero_como_ausencia():
    """`not <campo numerico>` y `<campo> or <defecto>` sobre un cero."""
    problemas = []
    for rel in ('ferreteria/services/dian_emision.py',
                'ferreteria/services/dian_nota_credito.py',
                'ferreteria/blueprints/ventas.py',
                'ferreteria/blueprints/catalogo.py'):
        src = _leer(rel)
        for i, linea in enumerate(src.split('\n'), 1):
            if re.search(r'if not \w*(?:' + CAMPOS + r')\b', linea, re.I):
                problemas.append(f'{rel}: {i}: {linea.strip()[:80]}')
            if re.search(r'get\(.(\w*(?:' + CAMPOS + r').)\)\s*or\s+', linea, re.I):
                problemas.append(f'{rel}: {i}: {linea.strip()[:80]}')
    assert not problemas, (
        'el cero es un valor, no una ausencia. Revisar:\n  ' + '\n  '.join(problemas))


def test_javascript_no_trata_un_cero_como_ausencia():
    """`x || defecto` donde el CERO es un valor de negocio.

    OJO con el alcance. `cantidad || 1` y `stock || 0` son CORRECTOS: un
    campo de cantidad vacío significa "no escribió nada" y el 1 es un default
    razonable; un stock vacío es 0, que es lo mismo que cero. El problema
    aparece cuando el cero significa "tasa cero" o "precio cero", que son
    DECISIONES del negocio y no ausencias.

    Solo se revisan los campos de tasa de IVA y de precio: ahí el 0 es un
    valor que el usuario eligió.
    """
    src = _leer('templates/index.html')
    CAMPOS = ('iva', 'precio', 'tarifa-iva', 'iva-tasa')
    problemas = []
    for i, linea in enumerate(src.split('\n'), 1):
        m = re.search(r"getElementById\('([^']+)'\)\.value\s*\|\|\s*(\d+)", linea)
        if not m:
            continue
        campo = m.group(1).lower()
        if any(c in campo for c in CAMPOS) and m.group(2) != '0':
            problemas.append(
                f'línea {i}: {m.group(1)} || {m.group(2)}  '
                f'(si el valor es "0", sale el defecto en vez del cero)')
    assert not problemas, (
        'un <select> devuelve texto y "0" es falsy en JavaScript. Un cero aquí '
        'es una decisión (tasa cero, precio cero), no una ausencia:\n  '
        + '\n  '.join(problemas))


def test_los_ceros_que_si_deben_ser_falsy_siguen_asi():
    """Contexto: no todo 0 es un error.

    `if (!total)` sobre la CANTIDAD de productos de una lista vacía sí es
    correcto: no es un importe, es un conteo. Este test deja claro que la
    regla es sobre importes y tasas, no sobre cualquier cero.
    """
    src = _leer('templates/index.html')
    # Estas dos son conteos de una lista, no importes: se dejan como están.
    assert 'if (!total) {' in src
    # Y no se inventan reglas nuevas sobre ellas.
    assert 'if (!totalProductos)' not in src
