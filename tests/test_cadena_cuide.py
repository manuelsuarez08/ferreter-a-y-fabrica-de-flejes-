"""Pruebas de la cadena del CUFE/CUFE, sin compartirem la función que prueban.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
Las pruebas anteriores calculaban el valor esperado con la MISMA función que
las que produden el valor real. Eso es circular: si la función cambia, las dos
cambian juntas y la prueba sigue pasando. Por eso al agregar el offset -05:00 a
`normalizar_hora` (para `cbc:IssueTime`) la cadena del CUFE se contaminó y
NINGUNA prueba lo noto.

Aquí el valor esperado está escrito a mano, con el valor correcto calculado por
separado. Si la cadena cambia, estas pruebas fallan.

LA REGLA QUE ESTAS PRUEBAS FIJAN
---------------------------------
`cbc:IssueTime` lleva offset '-05:00' (lo exige el anexo).
La cadena del CUFE NO lleva offset (el anexo también lo exige, y son reglas
distintas). Confundirlas produce documentos que la DIAN rechaza con
"el CUFE no corresponde", consumiendo el consecutivo de la resolución.
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_pos  # noqa: E402


# ═══════════════════════════════════════════
# 1. La cadena concatenada, valor escrito a mano
# ═══════════════════════════════════════════

def test_la_cadena_es_exactamente_la_esperada():
    """Valor esperado escrito a mano, no derivado de las funciones.

    SETP-1 + 2026-10-01 + 10:30:00 + 19000.00 + 0.00 + 119000.00 +
    900187391 + POS + CT + 2
    """
    cadena = dian_pos.cadena_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')

    esperado = ('SETP-1' + '2026-10-01' + '10:30:00' + '19000.00' + '0.00'
                + '119000.00' + '900187391' + 'POS' + 'CT' + '2')
    assert cadena == esperado


def test_la_cadena_no_lleva_offset():
    """El regresión más caro que encontramos: la hora con '-05:00' se colaba."""
    cadena = dian_pos.cadena_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')

    assert '-05:00' not in cadena
    assert '10:30:00' in cadena


def test_la_cadena_tiene_la_longitud_esperada():
    """Longitud fija si y solo si los 10 campos se emiten una vez."""
    cadena = dian_pos.cadena_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')

    # 6+10+8+8+4+9+9+3+2+1 = 60
    assert len(cadena) == 60


# ═══════════════════════════════════════════
# 2. El CUFE es SHA-384 de esa cadena
# ═══════════════════════════════════════════

def test_el_cuide_es_sha384_de_la_cadena_esperada():
    """Se hashea a mano, con hashlib, partiendo de la cadena esperada a mano."""
    esperado = ('SETP-1' + '2026-10-01' + '10:30:00' + '19000.00' + '0.00'
                + '119000.00' + '900187391' + 'POS' + 'CT' + '2')
    valor_esperado = hashlib.sha384(esperado.encode('utf-8')).hexdigest()

    valor = dian_pos.calcular_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')

    assert valor == valor_esperado


def test_el_cuide_tiene_96_caracteres_hexadecimales():
    """SHA-384 son 48 bytes = 96 hex, en minúsculas."""
    cuide = dian_pos.calcular_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')

    assert len(cuide) == 96
    assert cuide == cuide.lower()
    assert all(c in '0123456789abcdef' for c in cuide)


def test_cualquier_cambio_altera_el_cuide():
    """El hash es de sensibilidad: tocar un campo cambia la huella."""
    base = dict(num_documento='SETP-1', fecha='2026-10-01', hora='10:30:00',
                val_imp1=19000.0, val_imp2=0.0, val_total=119000.0,
                nit='900187391', tipo_documento='POS', clave_tecnica='CT',
                tipo_ambiente='2')
    referencia = dian_pos.calcular_cuide(**base)

    for campo, valor in (('fecha', '2026-10-02'), ('hora', '10:30:01'),
                         ('val_total', 119001.0), ('nit', '900187392'),
                         ('tipo_documento', 'FV'), ('clave_tecnica', 'CT2'),
                         ('tipo_ambiente', '1')):
        otro = dict(base)
        otro[campo] = valor
        assert dian_pos.calcular_cuide(**otro) != referencia, \
            f'Cambiar {campo} no alteró el CUFE'


# ═══════════════════════════════════════════
# 3. Las dos horas: la del XML y la del hash
# ═══════════════════════════════════════════

def test_las_dos_horas_son_distintas_a_proposito():
    """El requisito central: una lleva offset, la otra no."""
    assert dian_pos.normalizar_hora('10:30:00') == '10:30:00-05:00'
    assert dian_pos.hora_para_cuide('10:30:00') == '10:30:00'


def test_la_hora_del_cuide_ignora_un_offset_que_le_llegue():
    """Si alguien pasa la hora ya normalizada, el CUFE no se contamina."""
    hora_con_offset = dian_pos.normalizar_hora('10:30:00')

    assert dian_pos.hora_para_cuide(hora_con_offset) == '10:30:00'


@pytest.mark.parametrize('entrada', ['10:30:00', '10:30:00-05:00', '10:30:00Z',
                                    '10:30:00.123'])
def test_la_hora_del_cuide_es_siempre_la_misma(entrada):
    """Cualquier forma de escribir la hora da el mismo resultado."""
    assert dian_pos.hora_para_cuide(entrada) == '10:30:00'


def test_el_cuide_no_cambia_por_como_se_escriba_la_hora():
    """La cadena del hash depende del VALOR de la hora, no de su formato."""
    referencia = dian_pos.calcular_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')

    for hora in ('10:30:00', '10:30:00.999', '10:30:00-05:00'):
        assert dian_pos.calcular_cuide(
            'SETP-1', '2026-10-01', hora,
            19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2') == referencia


# ═══════════════════════════════════════════
# 4. Formato de los montos dentro de la cadena
# ═══════════════════════════════════════════

def test_los_montos_van_con_dos_decimales():
    """'19000.0' en vez de '19000.00' produce otro CUFE."""
    cadena = dian_pos.cadena_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000, 0, 119000, '900187391', 'POS', 'CT', '2')

    assert '19000.00' in cadena
    assert '0.00' in cadena
    assert '119000.00' in cadena


def test_el_nit_va_sin_guiones_ni_dv():
    cadena = dian_pos.cadena_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900.187.391', 'POS', 'CT', '2')

    assert '900187391' in cadena
    assert '900.187.391' not in cadena


def test_el_tipo_de_ambiente_es_el_del_documento():
    """Producción y habilitación dan CUFE distintos: no se pueden mezclar."""
    habilitacion = dian_pos.calcular_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '2')
    produccion = dian_pos.calcular_cuide(
        'SETP-1', '2026-10-01', '10:30:00',
        19000.0, 0.0, 119000.0, '900187391', 'POS', 'CT', '1')

    assert habilitacion != produccion