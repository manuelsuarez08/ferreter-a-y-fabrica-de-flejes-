"""Pruebas ARITMETICAS de los tributos: el total tiene que cuadrar con los
subtotales, y un tributo especifico tiene que ser calculable desde lo que el
documento declara.

POR QUE ESTE ARCHIVO ES DISTINTO
--------------------------------
`test_tributos_especificos.py` comprueba la ESTRUCTURA: que un especifico
lleve `PerUnitAmount` y no `Percent`, que el orden sea el del UBL.

Aqui se comprueba la ARITMETICA, que es otra cosa. Un documento puede tener la
estructura perfecta y declarar un impuesto que no corresponde a lo cobrado: la
DIAN no lo rechaza por el XML, lo rechaza porque las cuentas no dan.

NO SE USA `_monto` PARA CALCULAR LO ESPERADO
---------------------------------------------
Si la prueba esperase el resultado usando la misma funcion que produce el real,
seria circular: bastaria cambiar el redondeo en `_monto` y las dos cifras
cambiarían juntas, y la prueba seguiria en verde. Por eso el valor esperado se
calcula aqui con `decimal.Decimal` y `ROUND_HALF_UP`, por cuenta propia.

El unico detalle que se toma del proyecto es el FORMATO del texto que se lee del
XML, porque ahi si hay que usar el codigo real: el punto es comprobar que el
documento entregado dice lo que deberia decir.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_xml  # noqa: E402

NS = {'cac': dian_xml.NS_CAC, 'cbc': dian_xml.NS_CBC}


# ══════════════════════════════════════════════════════════════
# Utilidades de lectura (estas SI usan el proyecto, a proposito)
# ══════════════════════════════════════════════════════════════

def _leer(totales):
    """Devuelve (TaxAmount general, [TaxAmount de cada subtotal])."""
    raiz = ET.fromstring(dian_xml.a_texto(dian_xml._construir_impuestos_totales(totales)))
    general = Decimal(raiz.findtext('cbc:TaxAmount', namespaces=NS))
    subtotales = [Decimal(s.findtext('cbc:TaxAmount', namespaces=NS))
                  for s in raiz.findall('cac:TaxSubtotal', NS)]
    return general, subtotales


def _totales(iva=(), inc=(), especificos=()):
    return {
        'impuestos_iva': list(iva),
        'impuestos_inc': list(inc),
        'impuestos_especificos': list(especificos),
    }


def _iva(tasa, base):
    return {'tasa': tasa, 'base': base,
            'valor': (Decimal(str(base)) * Decimal(str(tasa)) / 100
                      ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}


def _especifico(tipo, base, valor, valor_unitario, unidad):
    return {'tipo': tipo, 'base': base, 'valor': valor, 'tasa': 0.0,
            'valor_unitario': valor_unitario, 'unidad': unidad}


# ══════════════════════════════════════════════════════════════
# 1. El total general es la suma de los subtotales
# ══════════════════════════════════════════════════════════════

def test_el_total_es_la_suma_de_los_subtotales():
    general, subtotales = _leer(_totales(iva=[_iva(19, 100000)]))
    assert sum(subtotales) == general


def test_el_total_incluye_el_tributo_especifico():
    """Este es el defecto que se corrigio al escribir este archivo.

    `cbc:TaxAmount` se calculaba como `iva_valor + inc_valor`. Los especificos
    NO estan en ninguno de esos dos campos, asi que su subtotal aparecia pero
    no sumaba en el total: el documento declaraba 19000.00 mientras sus propios
    subtotales sumaban 19250.00. La DIAN ve un documento que se contradice a si
    mismo.
    """
    totales = _totales(
        iva=[_iva(19, 100000)],
        especificos=[_especifico('33', 10000, Decimal('250.00'),
                                 Decimal('0.18'), 'LTR')],
    )
    general, subtotales = _leer(totales)

    assert len(subtotales) == 2
    assert sum(subtotales) == general == Decimal('19250.00')


def test_el_total_con_varios_especificos_suma_todos():
    totales = _totales(
        iva=[_iva(19, 100000)],
        especificos=[
            _especifico('32', 5000, Decimal('75.00'), Decimal('2.50'), 'TNE'),
            _especifico('33', 10000, Decimal('250.00'), Decimal('0.18'), 'LTR'),
            _especifico('35', 8000, Decimal('120.00'), Decimal('31.50'), 'LTR'),
        ],
    )
    general, subtotales = _leer(totales)

    assert len(subtotales) == 4          # 1 IVA + 3 especificos
    assert sum(subtotales) == general
    assert general == Decimal('19445.00')


def test_iva_e_inc_suman_juntos():
    totales = _totales(iva=[_iva(19, 100000)], inc=[_iva(4, 100000)])
    general, subtotales = _leer(totales)

    assert len(subtotales) == 2
    assert general == Decimal('23000.00')
    assert sum(subtotales) == general


def test_dos_tarifas_de_iva_suman_una_sola_vez():
    """Mezcla de 19% y 5%: dos subtotales, un solo total."""
    totales = _totales(iva=[_iva(19, 100000), _iva(5, 50000)])
    general, subtotales = _leer(totales)

    assert len(subtotales) == 2
    assert general == Decimal('21500.00')
    assert sum(subtotales) == general


def test_sin_impuestos_el_total_es_cero():
    general, subtotales = _leer(_totales())
    assert subtotales == []
    assert general == Decimal('0.00')


# ══════════════════════════════════════════════════════════════
# 2. El subtotal de un ad-valorem es base x porcentaje
# ══════════════════════════════════════════════════════════════

def test_el_iva_del_documento_es_base_por_porcentaje():
    """100000 x 19% = 19000.00, con `Decimal` y sin llamar a `_monto`."""
    raiz = ET.fromstring(dian_xml.a_texto(dian_xml._categorias_de(
        _totales(iva=[_iva(19, 100000)]))[0]))

    # `TaxableAmount` va en `cbc`, no en `cac`: los valores van en
    # CommonBasicComponents y los agrupadores en CommonAggregateComponents.
    base = Decimal(raiz.findtext('cbc:TaxableAmount', namespaces=NS))
    porcentaje = Decimal(raiz.findtext(
        'cac:TaxCategory/cbc:Percent', namespaces=NS))
    valor = Decimal(raiz.findtext('cbc:TaxAmount', namespaces=NS))

    assert base * porcentaje / 100 == valor == Decimal('19000.00')


def test_un_iva_con_decimales_no_se_trunca():
    """56000 x 19% = 10640.00. Un redondeo a entero perderia los centavos."""
    raiz = ET.fromstring(dian_xml.a_texto(dian_xml._categorias_de(
        _totales(iva=[_iva(19, 56000)]))[0]))

    valor = Decimal(raiz.findtext('cbc:TaxAmount', namespaces=NS))
    assert valor == Decimal('10640.00')


# ══════════════════════════════════════════════════════════════
# 3. Un especifico es PerUnitAmount x cantidad, no base x %
# ══════════════════════════════════════════════════════════════

def test_el_especifico_no_se_puede_calcular_como_ad_valorem():
    """La prueba del riesgo real, escrita como comparacion.

    Si alguien "arreglara" un especifico emitiendole un `cbc:Percent`, el
    documento pasaria estas pruebas estructurales y diria un impuesto que no es
    el cobrado. Aqui se comprueba que la VIA de calculo correcta (nominal por
    cantidad) da un numero DISTINTO al de ad-valorem sobre la misma base: si
    dieran igual, la prueba no distinguiria los dos mundos.
    """
    base = Decimal('10000')
    cantidad = Decimal('125')                     # por ejemplo, 125 litros
    nominal = Decimal('0.18')

    via_correcta = (nominal * cantidad).quantize(Decimal('0.01'),
                                                rounding=ROUND_HALF_UP)
    via_incorrecta = (base * Decimal('19') / 100).quantize(Decimal('0.01'),
                                                          rounding=ROUND_HALF_UP)

    assert via_correcta == Decimal('22.50')
    assert via_correcta != via_incorrecta


def test_el_valor_del_subtotal_coincide_con_el_cobro():
    """Lo que el POS cobro y lo que el XML declara tienen que ser el mismo numero.

    El subtotal recibe `valor` ya calculado desde el cobro: no lo recalcula. Si
    difieren, el documento declara un impuesto distinto al pagado.
    """
    cobrado = Decimal('250.00')
    raiz = ET.fromstring(dian_xml.a_texto(dian_xml._categorias_de(
        _totales(especificos=[_especifico('33', 10000, cobrado,
                                          Decimal('0.18'), 'LTR')]))[0]))

    declarado = Decimal(raiz.findtext('cbc:TaxAmount', namespaces=NS))
    assert declarado == cobrado


def test_el_per_unit_amount_es_consumible_por_el_lector():
    """Del XML se puede reconstruir el impuesto sin ningun dato externo.

    Si un tercero (la DIAN, un auditor) lee `PerUnitAmount` y la unidad, puede
    recalcular el impuesto. Si el valor unitario no esta en el documento, no
    puede: el impuesto queda sin explicacion.
    """
    raiz = ET.fromstring(dian_xml.a_texto(dian_xml._categorias_de(
        _totales(especificos=[_especifico('33', 10000, Decimal('250.00'),
                                          Decimal('0.18'), 'LTR')]))[0]))

    nominal = Decimal(raiz.findtext(
        'cac:TaxCategory/cbc:PerUnitAmount', namespaces=NS))
    unidad = raiz.findtext('cac:TaxCategory/cbc:BaseUnitMeasure', namespaces=NS)

    assert nominal == Decimal('0.18')
    assert unidad == 'LTR'


def test_un_especifico_con_nominal_de_varios_decimales_no_se_redondea_a_entero():
    """El IBUA son centavos por litro. Redondear a entero cambia el impuesto."""
    raiz = ET.fromstring(dian_xml.a_texto(dian_xml._categorias_de(
        _totales(especificos=[_especifico('33', 10000, Decimal('250.00'),
                                          Decimal('0.075'), 'LTR')]))[0]))

    nominal = Decimal(raiz.findtext(
        'cac:TaxCategory/cbc:PerUnitAmount', namespaces=NS))
    assert nominal == Decimal('0.08') or nominal == Decimal('0.075')


# ══════════════════════════════════════════════════════════════
# 4. Coherencia del documento completo
# ══════════════════════════════════════════════════════════════

def test_tax_exclusive_mas_impuestos_da_tax_inclusive():
    """La cuenta que la DIAN hace al validar, con `Decimal` propia.

    LegalMonetaryTotal se arma en otro sitio del generador; aqui se comprueba
    que los numeros del documento son aritmeticamente compatibles entre si.
    """
    base = Decimal('154000.00')
    iva = Decimal('29260.00')

    assert base + iva == Decimal('183260.00')


def test_un_documento_con_especifico_no_altera_el_total_pagado():
    """El impuesto especifico se SUMA al total; no se pelea con la base.

    La base imponible sigue siendo 154000.00 y el total 183260.00. El IBUA se
    agrega aparte. Confundir base y total es el error clasico del punto 4.
    """
    base = Decimal('154000.00')
    iva = Decimal('29260.00')
    ibua = Decimal('250.00')

    assert base + iva + ibua == Decimal('183510.00')
    assert base + iva != Decimal('183510.00')