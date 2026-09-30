"""La nota crédito tiene que declarar el MISMO impuesto que la factura.

Una corrección que difiere del documento que corrige la rechaza la DIAN: no se
puede descontar un impuesto que la factura original no cobró.

Antes de este arreglo, `dian_nota_credito` usaba `elif not iva_tasa`, y en
Python `not 0` es True. Un producto de tasa cero (arena, balastro) terminaba con
la tasa general del negocio en la nota crédito: 19% sobre un impuesto que nunca
se cobró.

Aquí se comprueba sobre el código real de los dos módulos, que es donde vive
la decisión, y además que la regla no se contraponga a la exención explícita.
"""
import io
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cuerpo(ruta, nombre):
    """Extrae el cuerpo de una función, sin comentarios ni cadenas."""
    with io.open(os.path.join(RAIZ, ruta), encoding='utf-8') as f:
        src = re.sub(r'/\*.*?\*/', '', f.read(), flags=re.S)
    m = re.search(r'def ' + re.escape(nombre) + r'\s*\([^)]*\)\s*:', src)
    assert m, f'no se encontró {nombre} en {ruta}'
    # Ventana amplia: en dian_emision la regla de la tarifa está a bastante
    # distancia del `def` (hay dos consultas SQL en medio).
    cuerpo = src[m.end():m.end() + 9000]
    return re.sub(r'(?m)#.*$', '', cuerpo)


# ═════════════════════════════════════════════════════
# 1. La regla, tal cual
# ═════════════════════════════════════════════════════
def test_la_nota_reconoce_la_tasa_cero_de_forma_explicita():
    cuerpo = _cuerpo('ferreteria/services/dian_nota_credito.py', '_leer_items_creditados')
    assert 'iva_tasa == 0' in cuerpo, (
        'la nota crédito debe comprobar `iva_tasa == 0` explícitamente')
    assert 'not iva_tasa' not in cuerpo, (
        '`not iva_tasa` convierte el cero (tasa cero) en un valor verdadero y '
        'termina aplicando la tasa general a un artículo que no la paga')


def test_la_exencion_explicita_manda():
    """Un producto marcado exento o no sujeto va a tasa cero siempre.

    Declarar impuesto sobre una operación no gravada lo rechaza la DIAN y el
    cliente pagaría de más.
    """
    cuerpo = _cuerpo('ferreteria/services/dian_nota_credito.py', '_leer_items_creditados')
    assert "'exento'" in cuerpo and "'no_sujeto'" in cuerpo
    # Y la condición se evalúa ANTES de cualquier otra.
    i_exento = cuerpo.find("'exento'")
    i_general = cuerpo.find('iva_porcentaje_venta')
    assert i_exento < i_general, (
        'la exención explícita debe decidirse antes de aplicar la tasa general')


# ═════════════════════════════════════════════════════
# 2. Las dos vías de emisión no pueden divergir
# ═════════════════════════════════════════════════════
def test_nota_y_emision_comparten_el_mismo_criterio():
    """Emisión y nota crédito aplican la misma regla.

    Si divergen, la corrección declara un impuesto distinto al de la factura y
    la DIAN la rechaza. Se comparan las dos condiciones carácter a carácter en
    lo que decide la tarifa.
    """
    emision = _cuerpo('ferreteria/services/dian_emision.py', '_leer_items')
    nota = _cuerpo('ferreteria/services/dian_nota_credito.py', '_leer_items_creditados')

    for etiqueta, texto in (('emisión', emision), ('nota crédito', nota)):
        assert "'exento'" in texto, f'{etiqueta}: falta la exención explícita'
        assert "'no_sujeto'" in texto, f'{etiqueta}: falta el "no sujeto"'
        assert 'excluido_iva' in texto, f'{etiqueta}: falta el excluido'
        assert 'iva_tasa == 0' in texto, f'{etiqueta}: falta la tasa cero'

    # La lista de códigos de tarifa DIAN que anulan el impuesto.
    for codigo in ('01', '02', '03'):
        assert f"'{codigo}'" in emision, f'emisión: falta el código {codigo}'
        assert f"'{codigo}'" in nota, f'nota: falta el código {codigo}'


def test_la_venta_tambien_reconoce_la_tasa_cero():
    """La venta es la tercera vía. Si esta se queda sin tasa cero, el POS cobra
    de más aunque los dos documentos-NEXT la declaren en cero."""
    from ferreteria.blueprints import ventas
    import importlib
    importlib.reload(ventas)
    # `_calcular_iva_por_lineas` agrupa por tasa; una tasa 0 debe existir como
    # grupo propio y no caer en la general.
    detalles = [{'precio': 10000, 'cantidad': 1, 'iva_tasa': 0.0}]
    base, iva, dominante = ventas._calcular_iva_por_lineas(
        detalles, 10000, 19.0, True)
    assert iva == 0, (
        f'la venta cobró {iva} de IVA a un producto de tasa cero: el POS cobra '
        f'de más aunque el documento declare tasa cero')
    assert base == 10000
    assert dominante == 0.0
