"""Pruebas unitarias de las utilidades tributarias DIAN (ferreteria/services/dian.py).

Este modulo es logica pura (sin Flask ni base de datos), asi que se prueba en
milisegundos con pytest, sin navegador ni servidor. Cubre las tres piezas que
consumen los blueprints de ventas/catalogo:

  1. calcular_digito_verificacion / validar_nit  (algoritmo oficial mod 11)
  2. redondear_pesos                             (peso colombiano, sin centavos)
  3. validar_cuadre                              (tolerancia del anexo tecnico)

Uso:
    .venv/Scripts/python.exe -m pytest tests -q
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services.dian import (  # noqa: E402
    UNIDADES_MEDIDA,
    calcular_digito_verificacion,
    descripcion_unidad,
    es_nit,
    redondear_pesos,
    validar_cuadre,
    validar_nit,
)


# ── Digito de verificacion (algoritmo oficial DIAN) ─────────────────────────
# Los DV esperados NO se copiaron de memoria: se calcularon a mano, paso a paso,
# aplicando los pesos oficiales de derecha a izquierda sobre cada NIT y tomando
# el modulo 11. Ejemplo: 900187391 -> 768 mod 11 = 9 -> DV = 11 - 9 = 2.
def test_dv_nit_conocido_900187391():
    assert calcular_digito_verificacion("900187391") == "2"


def test_dv_otros_nits_verificados_a_mano():
    """Cada valor sale de la misma cuenta manual que documenta el modulo."""
    assert calcular_digito_verificacion("800197268") == "4"   # 733 mod 11 = 7
    assert calcular_digito_verificacion("830114978") == "9"   # 739 mod 11 = 2
    assert calcular_digito_verificacion("900123456") == "8"   # 586 mod 11 = 3


def test_dv_ignora_guiones_puntos_y_espacios():
    """El NIT se escribe con separadores; deben ignorarse."""
    assert calcular_digito_verificacion("900.187.391") == "2"
    assert calcular_digito_verificacion("900-187-391") == "2"
    assert calcular_digito_verificacion(" 900 187 391 ") == "2"


def test_dv_acepta_entero():
    assert calcular_digito_verificacion(900187391) == "2"


def test_dv_residuo_0_y_1_se_devuelven_tal_cual():
    """Cuando el residuo es 0 o 1 el DV es el residuo, no 11 - residuo.

    Se buscan NITs cuyo residuo sea 0 o 1 y se comprueba que el DV es 0 o 1. Si
    el modulo usara siempre 11 - residuo, esos casos darian 11 o 10 (invalido:
    un DV es un solo digito).
    """
    PESOS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
    comprobados = 0
    for nit in range(100000, 400000):
        digitos = str(nit)
        suma = sum(int(d) * PESOS[i] for i, d in enumerate(reversed(digitos)) if i < len(PESOS))
        residuo = suma % 11
        if residuo in (0, 1):
            dv = calcular_digito_verificacion(digitos)
            assert dv == str(residuo), f"{digitos}: residuo {residuo} dio DV {dv}"
            comprobados += 1
    assert comprobados > 0, "no se encontro ningun NIT con residuo 0 o 1 en el rango"


def test_dv_siempre_es_un_digito():
    """Un DV valido es 0-9; nunca 10 u 11."""
    for nit in range(100000, 200000, 97):
        dv = int(calcular_digito_verificacion(str(nit)))
        assert 0 <= dv <= 9, f"DV fuera de rango para {nit}: {dv}"


def test_dv_nit_vacio_o_sin_digitos_devuelve_vacio():
    assert calcular_digito_verificacion("") == ""
    assert calcular_digito_verificacion(None) == ""
    assert calcular_digito_verificacion("abc") == ""


def test_dv_nit_mas_largo_que_los_pesos():
    """Con mas de 15 digitos el algoritmo corta: no debe reventar."""
    dv = calcular_digito_verificacion("12345678901234567890")
    assert dv.isdigit()


def test_validar_nit_coincide():
    assert validar_nit("900187391", "2") is True
    assert validar_nit("900.187.391", " 2 ") is True


def test_validar_nit_no_coincide():
    assert validar_nit("900187391", "5") is False
    assert validar_nit("900187391", "") is False
    assert validar_nit("", "2") is False


def test_validar_nit_none_no_revienta():
    assert validar_nit(None, None) is False


# ── Redondeo a peso entero ──────────────────────────────────────────────────
def test_redondear_pesos_desempate_al_alza():
    """ROUND_HALF_UP: 0.5 sube. Con float nativo, 0.5 tambien sube pero
    2.675 daria problemas de representacion binaria; Decimal lo evita."""
    assert redondear_pesos(0.5) == 1
    assert redondear_pesos(1.5) == 2
    assert redondear_pesos(2.5) == 3


def test_redondear_pesos_no_depende_del_error_binario():
    """2.675 en float es 2.67499999...; con Decimal(str()) redondea a 3."""
    assert redondear_pesos(2.675) == 3


def test_redondear_pesos_redondea_hacia_abajo_cuando_corresponde():
    assert redondear_pesos(2.4) == 2
    assert redondear_pesos(2.4999) == 2


def test_redondear_pesos_enteros_y_cero():
    assert redondear_pesos(1000) == 1000
    assert redondear_pesos(0) == 0


def test_redondear_pesos_none_y_basura_devuelve_cero():
    assert redondear_pesos(None) == 0.0
    assert redondear_pesos("") == 0.0
    assert redondear_pesos("no es un numero") == 0.0


def test_redondear_pesos_negativos():
    """Los negativos pueden aparecer en notas credito."""
    assert redondear_pesos(-2.5) == -3
    assert redondear_pesos(-2.4) == -2


# ── Cuadre de totales (tolerancia del anexo tecnico) ────────────────────────
def test_cuadre_exacto():
    ok, motivo = validar_cuadre(subtotal=100000, iva=19000, retenciones=0,
                                total=119000, total_neto=119000)
    assert ok is True, motivo
    assert motivo == ""


def test_cuadre_admite_diferencia_de_un_peso():
    """La DIAN acepta +/- 1 peso entre lo calculado y lo enviado."""
    ok, _ = validar_cuadre(100000, 19000, 0, 119001, 119001)
    assert ok is True
    ok, _ = validar_cuadre(100000, 19000, 0, 118999, 118999)
    assert ok is True


def test_cuadre_rechaza_diferencia_de_dos_pesos():
    ok, motivo = validar_cuadre(100000, 19000, 0, 119002, 119002)
    assert ok is False
    assert "Total no cuadra" in motivo


def test_cuadre_con_retenciones():
    """total_neto = total - retenciones."""
    ok, motivo = validar_cuadre(100000, 19000, 5000, 119000, 114000)
    assert ok is True, motivo


def test_cuadre_rechaza_neto_mal_calculado():
    ok, motivo = validar_cuadre(100000, 19000, 5000, 119000, 118000)
    assert ok is False
    assert "Total neto no cuadra" in motivo


def test_cuadre_tolerancia_personalizada():
    """Con tolerancia 0 no se admite ni 1 peso."""
    ok, _ = validar_cuadre(100000, 19000, 0, 119001, 119001, tolerancia=0)
    assert ok is False
    ok, _ = validar_cuadre(100000, 19000, 0, 119000, 119000, tolerancia=0)
    assert ok is True


# ── Helpers de catalogo ─────────────────────────────────────────────────────
def test_es_nit_solo_para_nit():
    assert es_nit("NIT") is True
    assert es_nit("nit") is True
    assert es_nit(" NIT ") is True
    assert es_nit("CC") is False
    assert es_nit("") is False
    assert es_nit(None) is False


def test_descripcion_unidad_traduce_codigos():
    assert descripcion_unidad("94") == "Unidad"
    assert descripcion_unidad("KGM") == "Kilogramo"
    assert descripcion_unidad("kgm") == "Kilogramo"


def test_descripcion_unidad_desconocida_devuelve_el_codigo():
    assert descripcion_unidad("ZZZ") == "ZZZ"
    assert descripcion_unidad("") == "Unidad"
    assert descripcion_unidad(None) == "Unidad"


def test_unidades_medida_sin_codigos_duplicados():
    """Un codigo duplicado haria que descripcion_unidad devolviera la etiqueta
    equivocada segun el orden de la tupla."""
    codigos = [c for c, _ in UNIDADES_MEDIDA]
    assert len(codigos) == len(set(codigos)), "hay codigos UN/ECE repetidos"
