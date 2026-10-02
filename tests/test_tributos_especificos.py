"""Pruebas de los tributos con tarifa ESPECÍFICA (códigos 32 a 36 del Anexo).

El Anexo separa dos naturalezas de tributo que se ven IGUALES en el POS pero
exigen estructuras opuestas en el XML:

  - AD-VALOREM: impuesto = porcentaje de la base -> `cbc:Percent`.
  - ESPECÍFICO: impuesto = valor nominal por unidad de medida (peso, litro,
    gramo) -> `cbc:PerUnitAmount` + `cbc:BaseUnitMeasure`.

El error grave es tratar un específico como ad-valorem: se emitiría un
porcentaje inventado y la DIAN calcularía un impuesto distinto al realmente
pagado. Eso no es un rechazo de sintaxis, es una declaración fiscal falsa.

Este archivo fija:
  1. Los cinco códigos 32-36 existen y se emiten en `cac:TaxScheme/cbc:ID`.
  2. Un específico emite `PerUnitAmount` y NO emite `Percent`.
  3. Un ad-valorem emite `Percent` y NO emite `PerUnitAmount`.
  4. El orden interno de `cac:TaxCategory` es el del UBL 2.1.
  5. Un código fuera del catálogo no se emite.
  6. El nombre del tributo viaja en `cac:TaxScheme/cbc:Name`.
"""
from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_pos, dian_xml  # noqa: E402

NS = {
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}

ESPECIFICOS = ['32', '33', '34', '35', '36']
ESPECIFICOS_DECLARADOS = ['32', '33', '35', '36']   # 34 es ad-valorem


# ══════════════════════════════════════════════════════════════
# 1. Los códigos existen y están en la naturaleza correcta
# ══════════════════════════════════════════════════════════════

def test_los_cinco_codigos_de_la_ley_2277_existen():
    """32 INPP, 33 IBUA, 34 ICUI, 35 ICL, 36 ADV.

    Si un código desaparece, el POS que lo trae no falla con un error claro:
    se emite un `TaxScheme` con el ID vacío o directamente no se emite, y el
    documento sale con un impuesto que nadie declaró.
    """
    assert dian_pos.TIPO_IMPUESTO_INPP == '32'
    assert dian_pos.TIPO_IMPUESTO_IBUA == '33'
    assert dian_pos.TIPO_IMPUESTO_ICUI == '34'
    assert dian_pos.TIPO_IMPUESTO_ICL == '35'
    assert dian_pos.TIPO_IMPUESTO_ADV == '36'


def test_todo_tributo_del_catalogo_declara_su_naturaleza():
    """Cada código de `NOMBRES_IMPUESTO` tiene que decir cómo se calcula.

    Un código sin naturaleza definida acaba en la rama del `else`, que es
    `porcentaje`: un tributo especifico saldría como ad-valorem sin que nadie
    lo note. Es un fallo silencioso por tabla incompleta.
    """
    faltantes = set(dian_pos.NOMBRES_IMPUESTO) - set(dian_pos.NATURALEZA_IMPUESTO)
    assert not faltantes, f'sin naturaleza declarada: {faltantes}'


def test_inpp_ibua_icl_y_adv_son_especificos():
    """Por unidad de medida, no por porcentaje.

    El ICUI queda fuera a proposito: es ad-valorem. Es el unico de los cinco que
    no depende de una medida fisica, asi que se liquida como porcentaje de la
    base. Confundirlo con los demas cambia el impuesto.
    """
    for tipo in ESPECIFICOS_DECLARADOS:
        assert dian_pos.NATURALEZA_IMPUESTO[tipo] == dian_pos.NATURALEZA_ESPECIFICO, \
            f'el tributo {tipo} deberia ser especifico'


def test_icui_es_ad_valorem():
    assert dian_pos.NATURALEZA_IMPUESTO['34'] == dian_pos.NATURALEZA_AD_VALOREM


# ══════════════════════════════════════════════════════════════
# Helpers para construir el XML
# ══════════════════════════════════════════════════════════════

NIT = '900187391'


def _subtotal(grupo):
    """Construye UN `cac:TaxSubtotal` y lo pasa a texto XML.

    Se prueba el subtotal aislado y no el documento entero a proposito: asi un
    fallo dice que la estructura del tributo esta mal, y no "algo no cuadra en la
    factura". UBL exige ademas el `TaxSubtotal` dentro de un `TaxTotal`, y eso
    lo cubren otras pruebas.
    """
    totales = {
        'impuestos_iva': [],
        'impuestos_inc': [],
        'impuestos_especificos': [grupo],
    }
    subtotales = dian_xml._categorias_de(totales)
    assert subtotales, 'no se genero ningun TaxSubtotal'
    return dian_xml.a_texto(subtotales[0])


def _grupo(tipo, valor_unitario=2.5, unidad='LTR', tasa=0.0):
    return {
        'tipo': tipo,
        'base': 10000.0,
        'valor': 250.0,
        'tasa': tasa,
        'valor_unitario': valor_unitario,
        'unidad': unidad,
    }


# ══════════════════════════════════════════════════════════════
# 2. Un específico NO lleva Percent
# ══════════════════════════════════════════════════════════════

def test_un_inpp_no_emite_porcentaje():
    """Este es el punto central del archivo.

    Con `cbc:Percent` en vez de `cbc:PerUnitAmount`, la DIAN calcula
    10000 * tarifa y el resultado no es el 250 que se pagó. El documento es
    sintacticamente valido y declara un impuesto falso.
    """
    xml = _subtotal(_grupo('32', valor_unitario=2.5, unidad='TNE'))

    assert '<cbc:Percent' not in xml, \
        'un tributo especifico NO puede llevar cbc:Percent'
    assert '<cbc:PerUnitAmount' in xml


def test_un_ibua_no_emite_porcentaje():
    xml = _subtotal(_grupo('33', valor_unitario=0.18, unidad='LTR'))
    assert '<cbc:Percent' not in xml
    assert '<cbc:PerUnitAmount' in xml


def test_una_bebida_declara_su_unidad_de_medida():
    """El litro tiene que viajar: sin unidad el PerUnitAmount no significa nada."""
    xml = _subtotal(_grupo('33', valor_unitario=0.18, unidad='LTR'))
    assert '<cbc:BaseUnitMeasure>LTR</cbc:BaseUnitMeasure>' in xml


def test_el_valor_unitario_es_el_que_viaja():
    xml = _subtotal(_grupo('35', valor_unitario=31.5, unidad='LTR'))

    m = re.search(r'<cbc:PerUnitAmount[^>]*>([^<]*)<', xml)
    assert m, 'falta el PerUnitAmount'
    assert m.group(1).replace(',', '') == '31.50'


def test_usa_la_unidad_por_defecto_del_tributo_si_no_viene():
    """Si el POS no trae unidad, se usa la del tributo (TNE para el INPP).

    Es mejor una unidad por defecto documentada que un PerUnitAmount sin unidad:
    el segundo no se puede calcular.
    """
    grupo = _grupo('32', valor_unitario=1.0)
    grupo.pop('unidad')
    xml = _subtotal(grupo)

    assert '<cbc:BaseUnitMeasure>TNE</cbc:BaseUnitMeasure>' in xml


# ══════════════════════════════════════════════════════════════
# 3. Un ad-valorem NO lleva PerUnitAmount
# ══════════════════════════════════════════════════════════════

def test_el_iva_sigue_siendo_porcentaje():
    """Los cambios de los específicos no pueden tocar el IVA.

    El IVA es el caso mas frecuente: si un cambio en la rama del `else` lo
    afecta por accidente, se emiten miles de facturas con la estructura
    equivocada.
    """
    totales = {
        'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
        'impuestos_inc': [],
        'impuestos_especificos': [],
    }
    xml = dian_xml.a_texto(dian_xml._categorias_de(totales)[0])

    assert '<cbc:Percent' in xml
    assert '<cbc:PerUnitAmount' not in xml


def test_el_iva_conserva_su_codigo_de_impuesto():
    xml = dian_xml.a_texto(dian_xml._categorias_de({
        'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
        'impuestos_inc': [],
        'impuestos_especificos': [],
    })[0])

    assert '<cbc:ID schemeID="195" schemeName="01">01</cbc:ID>' in xml


# ══════════════════════════════════════════════════════════════
# 4. El orden dentro de TaxCategory
# ══════════════════════════════════════════════════════════════

def test_el_orden_interno_de_tax_category_es_el_del_ubl():
    """`TaxCategory` es una `xsd:sequence`, no un conjunto.

    UBL declara: ID, Name, Percent, BaseUnitMeasure, PerUnitAmount, TaxScheme.
    En un nodo específico el orden real es ID, BaseUnitMeasure, PerUnitAmount,
    TaxScheme: BaseUnitMeasure ANTES que PerUnitAmount. Invertirlos produce un
    documento que no valida aunque todo el contenido este bien, que es
    exactamente el fallo que paso con `ds:Signature`.
    """
    xml = _subtotal(_grupo('33'))

    # Se lee el ARBOL, no el texto. Con `findall` sobre el texto el orden era
    # imposible de fijar: `cbc:ID` aparece dos veces (la categoria y el
    # impuesto) y el nombre del tributo se cuela entre medias. Parseando, la
    # comprobacion es la del esquema y no la de una expresion regular.
    raiz = ET.fromstring(xml)
    orden = [hijo.tag.split('}')[-1] for hijo in raiz.find('cac:TaxCategory', NS)]

    assert orden == ['ID', 'BaseUnitMeasure', 'PerUnitAmount', 'TaxScheme'], \
        f'ordeno incorrecto: {orden}'


def test_base_unit_measure_viene_antes_que_per_unit_amount():
    xml = _subtotal(_grupo('33'))

    assert xml.index('<cbc:BaseUnitMeasure') < xml.index('<cbc:PerUnitAmount')


def test_el_id_de_categoria_no_es_el_codigo_del_impuesto():
    """Son dos identificadores distintos y confundirlos es un rechazo tipico.

    `cbc:ID` = CATEGORIA del tributo (UN/EDIFACT 5153: 'S', 'Z', 'E', 'F').
    `cac:TaxScheme/cbc:ID` = el IMPUESTO ('01' IVA, '33' IBUA).
    """
    xml = _subtotal(_grupo('33'))

    assert '<cbc:ID>S</cbc:ID>' in xml
    assert '>33</cbc:ID>' in xml, 'el 33 debe ser el TaxScheme, no la categoria'


# ══════════════════════════════════════════════════════════════
# 5. El nombre del tributo
# ══════════════════════════════════════════════════════════════

def test_el_tributo_declara_su_nombre_fiscal():
    """`cac:TaxScheme/cbc:Name`: sin el, la DIAN no sabe que impuesto es."""
    xml = _subtotal(_grupo('33'))
    assert 'Bebidas Ultraprocesadas Azucaradas' in xml


def test_cada_tributo_tiene_nombre_propio():
    nombres = [dian_pos.NOMBRES_IMPUESTO[t] for t in ESPECIFICOS]
    assert len(set(nombres)) == len(nombres), 'dos tributos comparten nombre'


def test_los_nombres_no_contienen_basura_de_codificacion():
    """Guarda contra una regresion concreta: se colaron caracteres corruptos.

    El nombre viaja al XML y de ahi al PDF. Un 'ß' o un caracter suelto queda
    visible en un comprobante que va a un cliente.
    """
    permitidos = set(
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        'abcdefghijklmnopqrstuvwxyz'
        '0123456789 ()/.-áéíóúÁÉÍÓÚñÑüÜ'
    )
    for tipo, nombre in dian_pos.NOMBRES_IMPUESTO.items():
        raros = set(nombre) - permitidos
        assert not raros, f'nombre del tributo {tipo} con caracteres raros: {raros}'


# ══════════════════════════════════════════════════════════════
# 6. Lo que no está en el catálogo
# ══════════════════════════════════════════════════════════════

def test_un_codigo_inventado_no_se_emite():
    """Enviar un `TaxScheme` con un ID que la DIAN no conoce es peor que no
    enviarlo: la DIAN rechaza el documento entero.

    El POS puede traer cualquier cadena si alguien lo maldigita; el generador
    tiene que filtrarla.
    """
    totales = {
        'impuestos_iva': [],
        'impuestos_inc': [],
        'impuestos_especificos': [{'tipo': '99', 'base': 1000.0, 'valor': 10.0,
                                   'tasa': 1.0, 'valor_unitario': 1.0,
                                   'unidad': 'KGM'}],
    }
    assert dian_xml._categorias_de(totales) == []


def test_varios_especificos_producen_varios_subtotales():
    """Una venta puede llevar plastico Y bebida: son dos tributos, no uno."""
    totales = {
        'impuestos_iva': [],
        'impuestos_inc': [],
        'impuestos_especificos': [
            _grupo('32', valor_unitario=1.0, unidad='TNE'),
            _grupo('33', valor_unitario=0.18, unidad='LTR'),
        ],
    }
    subtotales = dian_xml._categorias_de(totales)

    assert len(subtotales) == 2
    xml = ' '.join(dian_xml.a_texto(s) for s in subtotales)
    assert '>32<' in xml and '>33<' in xml


def test_iva_y_especifico_conviven():
    """El caso real de una ferreteria con bebidas azucaradas y plastico."""
    totales = {
        'impuestos_iva': [{'tasa': 19.0, 'base': 50000.0, 'valor': 9500.0}],
        'impuestos_inc': [],
        'impuestos_especificos': [_grupo('33', valor_unitario=0.18,
                                          unidad='LTR')],
    }
    subtotales = dian_xml._categorias_de(totales)

    assert len(subtotales) == 2
    iva = dian_xml.a_texto(subtotales[0])
    ibua = dian_xml.a_texto(subtotales[1])

    assert '<cbc:Percent' in iva
    assert '<cbc:PerUnitAmount' in ibua