"""Pruebas del ciclo de pago: vencimiento, prórroga y suspensión.

El escenario que importa es el de un ferretería que DEBE seguir funcionando, y
un cliente que DEBE dejar de entrar. Un error en este código corta la operación de
quien pagó, o deja facturando a quien no pagó.

Cubren:
  1. La regla de la fecha (vigente / vencida / vencida+agotada).
  2. Que revisar NO escriba cuando se pide simular.
  3. Que una suspensión MANUAL no se levante sola al renovar la fecha.
  4. Que cambiar el plan o la fecha no borre los otros datos.
  5. El formato de fecha: uno mal escrito no rompe nada ni cuelga al cliente.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import padron_ferreterias as padron  # noqa: E402


@pytest.fixture
def conn_pagos(tmp_path):
    """Base del desarrollador con el padrón recién migrado."""
    from ferreteria import db as db_mod

    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    conn.commit()
    yield conn, str(tmp_path)
    conn.close()


def _crear(conn, nombre, **pago):
    """Inserta una ferretería con los datos de pago indicados."""
    valores = {
        'plan': 'mensual',
        'fecha_vencimiento': None,
        'dias_prorroga': 15,
        'estado': 'activo',
    }
    valores.update(pago)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO ferreterias (nombre, nit, archivo, usuario_dueno, estado, '
        'plan, fecha_vencimiento, dias_prorroga, creado_en, actualizado_en) '
        "VALUES (?, '', ?, ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (nombre, nombre.lower().replace(' ', '_') + '.db', 'dueno',
         valores['estado'], valores['plan'], valores['fecha_vencimiento'],
         valores['dias_prorroga']),
    )
    conn.commit()
    return cursor.lastrowid


def _en_dias(n):
    return (datetime.now() + timedelta(days=n)).strftime('%Y-%m-%d')


# ═══════════════════════════════════════════
# 1. La regla de la fecha
# ═══════════════════════════════════════════

def test_una_ferreteria_vigente_no_toca_nada(conn_pagos):
    conn, _ = conn_pagos
    _crear(conn, 'AL DIA', fecha_vencimiento=_en_dias(20))

    assert padron.revisar_vencimientos(conn) == []


def test_vencida_dentro_de_la_gracia_va_a_prorroga(conn_pagos):
    """Venció hace 5 días y la prórroga son 15: todavía opera, pero avisado."""
    conn, _ = conn_pagos
    id_ = _crear(conn, 'VENCIDA POCO', fecha_vencimiento=_en_dias(-5),
                dias_prorroga=15)

    cambios = padron.revisar_vencimientos(conn)

    assert len(cambios) == 1
    assert cambios[0]['a'] == 'prorrogado'
    estado = conn.execute('SELECT estado FROM ferreterias WHERE id = ?',
                          (id_,)).fetchone()[0]
    assert estado == 'prorrogado'


def test_vencida_al_limite_sigue_en_prorroga(conn_pagos):
    """El día 15 todavía es prórroga: se corta al día 16."""
    conn, _ = conn_pagos
    _crear(conn, 'LIMITE', fecha_vencimiento=_en_dias(-15), dias_prorroga=15)

    cambios = padron.revisar_vencimientos(conn)
    assert cambios[0]['a'] == 'prorrogado'


def test_prorroga_agotada_suspende(conn_pagos):
    """Venció hace 20 días con 15 de prórroga: le toca suspenderse."""
    conn, _ = conn_pagos
    id_ = _crear(conn, 'AGOTADA', fecha_vencimiento=_en_dias(-20),
                dias_prorroga=15)

    cambios = padron.revisar_vencimientos(conn)

    assert cambios[0]['a'] == 'suspendido'
    assert conn.execute('SELECT estado FROM ferreterias WHERE id = ?',
                        (id_,)).fetchone()[0] == 'suspendido'


def test_sin_fecha_no_se_suspende_nunca(conn_pagos):
    """Un cliente al que nunca se le puso fecha no puede ser bloqueado.

    Es la protección contra el peor error posible: que una ferretería que está
    pagando se quede fuera del sistema por un dato que nadie llenó.
    """
    conn, _ = conn_pagos
    _crear(conn, 'SIN FECHA', fecha_vencimiento=None)

    assert padron.revisar_vencimientos(conn) == []


def test_la_prorroga_cero_suspende_de_inmediato(conn_pagos):
    """Alguien que no da prórroga quiere que caduque el mismo día."""
    conn, _ = conn_pagos
    _crear(conn, 'SIN GRACIA', fecha_vencimiento=_en_dias(-1),
          dias_prorroga=0)

    cambios = padron.revisar_vencimientos(conn)
    assert cambios[0]['a'] == 'suspendido'


# ═══════════════════════════════════════════
# 2. Simular no escribe
# ═══════════════════════════════════════════

def test_simular_no_cambia_nada(conn_pagos):
    """El panel debe poder mostrar qué PASARÍA sin que pase."""
    conn, _ = conn_pagos
    id_ = _crear(conn, 'SIMULAR', fecha_vencimiento=_en_dias(-20),
                 dias_prorroga=15)

    cambios = padron.revisar_vencimientos(conn, aplicar=False)

    assert len(cambios) == 1, 'Debe avisar del cambio'
    assert cambios[0]['a'] == 'suspendido', '...pero sin aplicarlo'
    estado = conn.execute('SELECT estado FROM ferreterias WHERE id = ?',
                          (id_,)).fetchone()[0]
    assert estado == 'activo', 'La base no se puede tocar al simular'


def test_los_pendientes_se_listan_sin_aplicar(conn_pagos):
    """`suspendidos_pendientes` informa; no decide."""
    conn, _ = conn_pagos
    _crear(conn, 'PENDIENTE UNO', fecha_vencimiento=_en_dias(-30))
    _crear(conn, 'PENDIENTE DOS', fecha_vencimiento=_en_dias(-30))
    _crear(conn, 'AL DIA', fecha_vencimiento=_en_dias(30))

    pendientes = padron.suspendidos_pendientes(conn)

    assert len(pendientes) == 2
    # Y sigue sin haber cambiado nada.
    activos = conn.execute(
        "SELECT COUNT(*) FROM ferreterias WHERE estado = 'activo'").fetchone()[0]
    assert activos == 3


# ═══════════════════════════════════════════
# 3. Una suspensión manual no se levanta sola
# ═══════════════════════════════════════════

def test_renovar_la_fecha_no_reactiva_a_un_suspendido(conn_pagos):
    """La suspensión es una decisión del desarrollador, no un efecto del reloj.

    Si renovar la fecha levantara la suspensión sola, un cliente suspendido por
    impago volvería a operar en cuanto pagara la mitad, y el desarrollador
    perdería el control.
    """
    conn, _ = conn_pagos
    id_ = _crear(conn, 'SUSPENDIDO A MANO', estado='suspendido',
                fecha_vencimiento=_en_dias(-30))

    # Se renueva la fecha a un año vista.
    padron.configurar_pago(conn, id_, fecha_vencimiento=_en_dias(365),
                           aplicar_estado=True)

    estado = conn.execute('SELECT estado FROM ferreterias WHERE id = ?',
                          (id_,)).fetchone()[0]
    assert estado == 'suspendido', 'Debe seguir suspendido hasta que se reactive'


def test_reactivar_a_mano_sigue_disponible(conn_pagos):
    conn, _ = conn_pagos
    id_ = _crear(conn, 'A REACTIVAR', estado='suspendido',
                 fecha_vencimiento=_en_dias(-30))

    conn.execute("UPDATE ferreterias SET estado = 'activo' WHERE id = ?", (id_,))
    conn.commit()

    cambios = padron.revisar_vencimientos(conn)
    assert cambios[0]['a'] == 'suspendido', 'El reloj vuelve a pedir la suspensión'


# ═══════════════════════════════════════════
# 4. Guardar datos de pago
# ═══════════════════════════════════════════

def test_cambiar_el_plan_no_borra_la_fecha(conn_pagos):
    """Guardar el plan no debe pisar los demás datos de pago."""
    conn, _ = conn_pagos
    id_ = _crear(conn, 'PLAN', fecha_vencimiento=_en_dias(30),
                dias_prorroga=20)

    padron.configurar_pago(conn, id_, plan='anual')

    datos = padron.obtener_pago(conn, id_)
    assert datos['plan'] == 'anual'
    assert datos['fecha_vencimiento'] == _en_dias(30)
    assert datos['dias_prorroga'] == 20


def test_cambiar_la_fecha_no_borra_el_plan(conn_pagos):
    conn, _ = conn_pagos
    id_ = _crear(conn, 'FECHA', plan='anual', dias_prorroga=30)

    padron.configurar_pago(conn, id_, fecha_vencimiento=_en_dias(10))

    datos = padron.obtener_pago(conn, id_)
    assert datos['plan'] == 'anual'
    assert datos['dias_prorroga'] == 30


def test_una_fecha_mal_escrita_se_rechaza(conn_pagos):
    """Guardar 'pronto' como fecha dejaría al cliente sin vencer nunca."""
    conn, _ = conn_pagos
    id_ = _crear(conn, 'FECHA MALA')

    with pytest.raises(padron.ErrorPadron) as error:
        padron.configurar_pago(conn, id_, fecha_vencimiento='pronto')
    assert 'AAAA-MM-DD' in str(error.value)


def test_una_fecha_mal_esquerita_en_la_base_no_rompe_nada(conn_pagos):
    """Si alguien la escribió a mano mal, el panel abre igual."""
    conn, _ = conn_pagos
    id_ = _crear(conn, 'FECHA ROTA')
    conn.execute('UPDATE ferreterias SET fecha_vencimiento = ? WHERE id = ?',
                 ('15/03/2026', id_))
    conn.commit()

    datos = padron.obtener_pago(conn, id_)
    assert datos['dias_para_vencer'] is None
    assert padron.revisar_vencimientos(conn) == []


def test_un_plan_inventado_se_rechaza(conn_pagos):
    conn, _ = conn_pagos
    id_ = _crear(conn, 'PLAN RARO')

    with pytest.raises(padron.ErrorPadron):
        padron.configurar_pago(conn, id_, plan='trimestral')


def test_guardar_nada_es_un_error(conn_pagos):
    conn, _ = conn_pagos
    id_ = _crear(conn, 'NADA')

    with pytest.raises(padron.ErrorPadron):
        padron.configurar_pago(conn, id_)


def test_una_ferreteria_inexistente_falla(conn_pagos):
    conn, _ = conn_pagos
    with pytest.raises(padron.ErrorPadron):
        padron.configurar_pago(conn, 9999, plan='anual')


# ═══════════════════════════════════════════
# 5. El panel lo muestra
# ═══════════════════════════════════════════

def test_el_listado_trae_los_dias_que_faltan(conn_pagos):
    conn, directorio = conn_pagos
    _crear(conn, 'LISTADO', fecha_vencimiento=_en_dias(5))

    registros = padron.listar(conn, directorio)

    registro = next(r for r in registros if r['nombre'] == 'LISTADO')
    assert registro['dias_para_vencer'] == 5


def test_lo_que_le_toca_estado_se_muestra_sin_aplicarlo(conn_pagos):
    """El panel avisa "esto cambiaría" sin cambiarlo."""
    conn, directorio = conn_pagos
    _crear(conn, 'AVISO', fecha_vencimiento=_en_dias(-30))

    registros = padron.listar(conn, directorio)

    registro = next(r for r in registros if r['nombre'] == 'AVISO')
    assert registro['estado'] == 'activo'
    assert registro['suspension_automatica'] == 'suspendido'


def test_los_que_se_estan_venciendo_van_arriba(conn_pagos):
    """El orden es por urgencia, no alfabético."""
    conn, directorio = conn_pagos
    _crear(conn, 'ZZZ SANO', fecha_vencimiento=_en_dias(300))
    _crear(conn, 'AAA VENCIDO', fecha_vencimiento=_en_dias(-30))

    registros = padron.listar(conn, directorio)

    assert registros[0]['nombre'] == 'AAA VENCIDO'


def test_el_resumen_cuenta_los_vencimientos_pendientes(conn_pagos):
    conn, directorio = conn_pagos
    _crear(conn, 'PENDIENTE', fecha_vencimiento=_en_dias(-30))

    cifras = padron.resumen(conn, directorio)

    assert len(cifras['vencimientos_pendientes']) == 1
    # Y cuenta como algo que requiere atención, no como un cliente más.
    assert cifras['requiere_atencion'] >= 1


def test_prorroga_en_prorrogado_recalcula_el_dia_de_entrada(conn_pagos):
    conn, _ = conn_pagos
    id_ = _crear(conn, 'MARCADO', fecha_vencimiento=_en_dias(-3))

    padron.revisar_vencimientos(conn, aplicar=True)

    desde = conn.execute('SELECT desde_prorroga FROM ferreterias WHERE id = ?',
                         (id_,)).fetchone()[0]
    assert desde, 'Debe quedar registrado cuándo entró en prórroga'


def test_revisar_dos_veces_no_genera_cambios_repetidos(conn_pagos):
    """La segunda pasada ya no tiene nada que hacer."""
    conn, _ = conn_pagos
    _crear(conn, 'IDEMPOTENTE', fecha_vencimiento=_en_dias(-30))

    primero = padron.revisar_vencimientos(conn, aplicar=True)
    segundo = padron.revisar_vencimientos(conn, aplicar=True)

    assert len(primero) == 1
    assert segundo == []


def test_el_historial_registra_el_cambio_por_fecha(conn_pagos):
    conn, _ = conn_pagos
    id_ = _crear(conn, 'CON HISTORIAL', fecha_vencimiento=_en_dias(-30))

    padron.revisar_vencimientos(conn, aplicar=True)

    historial = padron.historial(conn, id_)
    acciones = [h['accion'] for h in historial]
    assert 'suspendido' in acciones