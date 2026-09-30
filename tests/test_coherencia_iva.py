"""El IVA tiene que ser el MISMO en el backend, en el POS y en la edición.

El bug era que `precio_venta` es el precio FINAL (con IVA dentro) y el cálculo
lo trataba como base sin impuesto, sumándole el IVA encima: un producto de
$15.000 se cobraba $17.850, en TODAS las ventas.

Lo grave no era solo el backend. El POS hacía la misma cuenta, así que el
cajero veía el total inflado en pantalla y escribía ESE número: el bug era
consistente de punta a punta y por eso nadie lo notaba. Al arreglar solo el
servidor, el cajero habría visto un precio y se habría guardado otro.

Estas pruebas fijan que los tres lugares coincidan.
"""
import io
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLA = os.path.join(RAIZ, 'templates', 'index.html')

with io.open(PLANTILLA, encoding='utf-8') as f:
    HTML = f.read()

# El JS sin comentarios: los comentarios nombran el código viejo para explicar
# el bug, y sin limpiarlos la prueba lo detectaría como si siguiera ahí.
JS = re.sub(r'/\*.*?\*/', '', HTML, flags=re.S)
JS = re.sub(r'(?m)//[^\n]*$', '', JS)


def _cuerpo(nombre):
    """Extrae el cuerpo de una función del JS."""
    m = re.search(r'function\s+' + re.escape(nombre) + r'\s*\([^)]*\)\s*\{', JS)
    assert m, f'no se encontro la función {nombre}'
    # Contar llaves para sacar el cuerpo completo.
    nivel = 0
    pos = m.end() - 1
    while pos < len(JS):
        if JS[pos] == '{':
            nivel += 1
        elif JS[pos] == '}':
            nivel -= 1
            if nivel == 0:
                return JS[m.end():pos]
        pos += 1
    return ''


# ═════════════════════════════════════════════════════
# El POS
# ═════════════════════════════════════════════════════
def test_el_carrito_extrae_el_iva_en_vez_de_sumarlo():
    """`base = final / (1 + tasa)`, no `iva = final * tasa`."""
    cuerpo = _cuerpo('actualizarTotalesCarrito')
    assert '/ (1 + ivaPorcentaje / 100)' in cuerpo, (
        'el carrito tiene que DIVIDIR el precio final para sacar la base; '
        'multiplicar por la tasa vuelve a inflar el total un 19%')
    # La forma antigua, tal cual, no debe aparecer.
    assert 'subtotal * ivaPorcentaje / 100' not in cuerpo
    assert 'monedaCOP(subtotal + ivaValor)' not in cuerpo, (
        'el total del carrito es la suma de los precios finales, no base+IVA')


def test_el_total_mostrado_es_el_precio_final():
    """Lo que ve el cajero tiene que ser lo que se guarda."""
    cuerpo = _cuerpo('actualizarTotalesCarrito')
    assert re.search(r"total-venta-texto'\)\.innerText = monedaCOP\(subtotalFinal\)", cuerpo), (
        'el total del carrito debe ser la suma de los precios que puso el '
        'cajero, sin sumarle IVA encima')


# ═════════════════════════════════════════════════════
# La edición de la factura
# ═════════════════════════════════════════════════════
def test_la_edicion_extrae_el_iva_en_vez_de_sumarlo():
    """La previsualización de la edición tenía el mismo error.

    Con el backend arreglado y esto sin tocar, el admin vería un total que el
    servidor no guarda y creería que falló el guardado.
    """
    cuerpo = _cuerpo('previsualizarTotalEditado')
    assert '/ (1 + ivaPorcentaje / 100)' in cuerpo
    assert 'subtotal * ivaPorcentaje / 100' not in cuerpo
    assert 'monedaCOP(subtotalFinal)' in cuerpo


# ═════════════════════════════════════════════════════
# Lo que NO hay que tocar
# ═════════════════════════════════════════════════════
def test_la_entrada_de_producto_conserva_su_desglose():
    """El formulario de producto SÍ debe poder tomar el precio con o sin IVA.

    Ahi el conmutador es explícito ('el precio de la factura trae IVA'): si
    viene con impuesto se desglosa, si no, se le suma. Es el único lugar donde
    sumar IVA es correcto, porque el usuario está decidiendo cuál de los dos
    números está escribiendo.
    """
    cuerpo = _cuerpo('leerPrecioEIVA')
    assert 'facturaConIva' in cuerpo, (
        'debe seguir el modo "el precio de la factura trae IVA"')
    assert '/ (1 + tasa / 100)' in cuerpo, 'en ese caso desglosa el precio'
    assert '* tasa / 100' in cuerpo, 'en el otro caso suma el IVA'


def test_el_xml_trabaja_sobre_la_base_no_sobre_el_precio_final():
    """El generador de XML calcula el IVA sobre la BASE, y es lo correcto.

    El anexo técnico pide subtotal (sin impuesto) e IVA aparte. Si alguien
    'corrigió' esto aplicando el criterio del POS, el documento se rechazaría.
    """
    ruta = os.path.join(RAIZ, 'ferreteria', 'services', 'dian_xml.py')
    with io.open(ruta, encoding='utf-8') as f:
        src = re.sub(r'/\*.*?\*/', '', f.read(), flags=re.S)
    # El IVA del XML sale de la base de cada línea.
    assert re.search(r'base\s*\*\s*(tasa|iva)/100', src) or \
        re.search(r'base \* .* / 100', src), (
        'el XML debe calcular el IVA sobre la base de la línea, no sobre el '
        'precio final')
