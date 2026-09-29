"""Pruebas de los ajustes 1, 2, 7 y 9 (limpieza de modulos y regla de fabrica).

Estos puntos son "borrar cosas", y borrar cosas se rompe en silencio: un boton
que llama a una funcion eliminada da ReferenceError, y un panel que se mostro
en un `<li>` condicional con Jinja deja markup desbalanceado. Las pruebas fijan
que no queden rastros ni referencias colgando.

Tambien fijan la REGLA DE NEGOCIO de la orden a fabrica, que es la parte que
si importa: solo la categoria Fleje/Flejes genera orden.
"""
import io
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLA = os.path.join(RAIZ, 'templates', 'index.html')
VENTAS = os.path.join(RAIZ, 'ferreteria', 'blueprints', 'ventas.py')


@pytest.fixture(scope='module')
def html():
    with io.open(PLANTILLA, encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def ventas_py():
    with io.open(VENTAS, encoding='utf-8') as f:
        return f.read()


def _codigo(html):
    """Codigo sin comentarios: los comentarios explican el bug y menciona el
    codigo viejo, asi que buscarlos ahi daria falsos positivos."""
    t = re.sub(r'/\*.*?\*/', '', html, flags=re.S)
    t = re.sub(r'(?m)//[^\n]*$', '', t)
    return t


# ═════════════════════════════════════════════════════
# 1. Seccion "Flejes a medida" fuera del POS
# ═════════════════════════════════════════════════════
def test_la_seccion_de_flejes_a_medida_no_esta_en_el_pos(html):
    """El formulario de flejes a medida se elimino de la interfaz de venta."""
    codigo = _codigo(html)
    for id_fleje in ('fleje-calibre', 'fleje-ancho', 'fleje-largo',
                     'fleje-gancho', 'fleje-cantidad', 'tabla-flejes-pedido-body'):
        assert f'id="{id_fleje}"' not in html, f'quedo el campo {id_fleje} del POS'


def test_no_queda_el_js_de_flejes_a_medida(html):
    """Sus funciones quedaron huerfanas y usarian variables inexistentes."""
    codigo = _codigo(html)
    for fn in ('agregarFlejeAlPedido', 'renderTablaFlejesPedido',
               'eliminarFlejeDelPedido', 'enviarFlejesAFabrica'):
        assert f'function {fn}' not in codigo, f'quedo la funcion {fn}'
    assert 'carritoFlejes' not in codigo


def test_el_panel_de_fabrica_sigue_vivo(html):
    """Lo que se elimino es el formulario de venta, NO el modulo de fabrica."""
    assert 'id="flejes"' in html, 'se elimino por error el panel de Fabrica'
    assert 'cargarOrdenesFlejes' in html


# ═════════════════════════════════════════════════════
# 7. Modulo "Pedidos manuales" fuera
# ═════════════════════════════════════════════════════
def test_el_sub_panel_de_pedidos_manuales_no_existe(html):
    codigo = _codigo(html)
    assert 'id="subtab-manuales"' not in html
    assert 'subtab-manuales-btn' not in html
    assert 'id="modalNuevoPedido"' not in html


def test_no_queda_el_js_de_pedidos_manuales(html):
    codigo = _codigo(html)
    for fn in ('cargarPedidos', 'abrirModalNuevoPedido', 'guardarPedido',
               'renderItemsPedido', 'agregarItemPedido', 'quitarItemPedido'):
        assert f'function {fn}' not in codigo, f'quedo la funcion {fn}'
    for var in ('itemsPedidoActual', 'tsPedidoCliente', 'tsPedidoProducto'):
        assert var not in codigo, f'quedo la variable {var}'


def test_la_cola_de_despacho_se_conserva(html):
    """'Pedidos' sigue siendo la cola de despacho de las ventas 'para llevar'.

    Ese flujo usa `entregas_venta` y es otro modulo: borrarlo seria romper una
    funcionalidad que el cliente no pidio eliminar.
    """
    assert 'id="subtab-despachos"' in html
    assert 'cargarDespachos' in html
    # La tabla de la cola de despacho sigue presente.
    assert 'tabla-despachos' in html or 'despacho-filtro-estado' in html


# ═════════════════════════════════════════════════════
# 9. Regla de la orden a fabrica: SOLO categoria Fleje/Flejes
# ═════════════════════════════════════════════════════
def test_la_regla_es_por_categoria(ventas_py):
    """El criterio es la categoria, no el nombre ni las medidas del item."""
    m = re.search(r'def _es_producto_fleje\(.*?\n(?=\n\ndef |\n\nCATEGORIAS|\n\n@)', ventas_py, re.S)
    assert m, 'no se encontro _es_producto_fleje'
    cuerpo = m.group(0)
    # El nombre y las medidas ya no deciden.
    assert "get('calibre')" not in cuerpo
    assert "get('ancho_cm')" not in cuerpo
    assert "find('fleje')" not in cuerpo


@pytest.mark.parametrize('categoria, esperado', [
    ('Fleje', True),
    ('fleje', True),
    ('Flejes', True),
    ('FLEJES', True),
    ('  Fleje  ', True),
    ('Tuberia', False),
    ('Cemento', False),
    ('Flejes tubulares', False),
    ('No-fleje', False),
    ('', False),
    (None, False),
])
def test_la_categoria_decide_si_viene_orden_a_fabrica(ventas_py, categoria, esperado):
    """Caso por caso de la regla acordada con el cliente.

    Importante: 'Flejes tubulares' y 'No-fleje' NO deben generar orden. Con un
    `find('fleje')` si lo harian, y el taller recibiria ordenes que no espera.
    """
    import importlib
    import ferreteria.blueprints.ventas as v
    importlib.reload(v)
    assert v._es_producto_fleje('Cualquier nombre', categoria, {}) is esperado


def test_las_categorias_validas_estan_declaradas(ventas_py):
    assert "CATEGORIAS_FLEJE" in ventas_py
    assert "'fleje'" in ventas_py and "'flejes'" in ventas_py


def test_la_venta_sigue_llamando_a_la_orden(ventas_py):
    """La orden se sigue creando: solo cambio el criterio."""
    assert '_es_producto_fleje(' in ventas_py
    assert 'registrar_orden_fleje_desde_venta(' in ventas_py
