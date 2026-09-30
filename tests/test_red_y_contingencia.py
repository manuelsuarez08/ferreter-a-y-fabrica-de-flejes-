"""Pruebas de la emisión sin red: lo que pasa cuando la DIAN no responde.

Este es el escenario más común en un mostrador con internet intermitente: la
DIAN no contesta. El POS tiene que seguir vendiendo, dejar el documento en
contingencia y reintentarlo después. Si eso no funciona, la ferretería se
queda sin facturar.

Se fija también el tiempo de espera máximo, porque un timeout mal puesto
convierte cada venta en una congelación de la pantalla.
"""
import os

import pytest

from ferreteria.services import dian_soap

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═════════════════════════════════════════════════════
# Tiempos de espera
# ═════════════════════════════════════════════════════
def test_el_timeout_no_es_tan_largo_que_congele_el_mostrador():
    """El peor caso (conexión + respuesta) tiene que ser tolerable.

    Con los valores anteriores (30 s + 60 s) cada venta se quedaba 90
    segundos sin respuesta. El cajero ve la aplicación colgada y no sabe si
    la venta entró.
    """
    peor_caso = dian_soap.TIMEOUT_CONEXION + dian_soap.TIMEOUT_RESPUESTA
    assert peor_caso <= 45, (
        f'el peor caso es {peor_caso} s; por encima de 45 s el cajero '
        f'percibe la aplicacion como colgada')
    # Y la conexión sola debe fallar rápido: si no abre el socket, la red del
    # local está caída y esperar más no sirve.
    assert dian_soap.TIMEOUT_CONEXION <= 15


def test_el_timeout_de_respuesta_alcanza_una_red_lenta():
    """Redu-cirlo no puede dejar fuera una DIAN lento pero sano."""
    assert dian_soap.TIMEOUT_RESPUESTA >= 20, (
        'por debajo de 20 s se declared contingencia un documento que la DIAN '
        'si iba a aceptar')


def test_el_set_de_pruebas_conserva_su_timeout():
    """El set de pruebas es un ZIP grande: tiene su propio margen."""
    assert dian_soap.TIMEOUT_SET_PRUEBAS > dian_soap.TIMEOUT_RESPUESTA


# ═════════════════════════════════════════════════════
# La cola es la red de seguridad
# ═════════════════════════════════════════════════════
def test_la_cola_distingue_rechazo_de_fallo_de_red():
    """Un rechazo de la DIAN no se reintenta; un fallo de red, sí.

    Si se reintentara un documento rechazado, la cola nunca pararía de
    consumir intentos y el POS acabaría con documentos que nunca se envían.
    """
    import io
    ruta = os.path.join(RAIZ, 'ferreteria', 'services', 'dian_emision.py')
    with io.open(ruta, encoding='utf-8') as f:
        src = f.read()
    # El rechazo de negocio se cierra como fallido, sin reprogramar.
    assert "'rechazado'" in src
    assert "estado == 'rechazado'" in src
    # Y existe un cierre con motivo, no un simple 'continue'.
    assert '_cerrar_trabajo' in src


def test_el_maximo_de_intentos_esta_definido():
    """Un trabajo no puede reintentarse para siempre."""
    from ferreteria.services import dian_emision
    assert dian_emision.MAX_INTENTOS_DEFECTO >= 3
    assert dian_emision.MAX_INTENTOS_DEFECTO <= 20


def test_la_cola_no_se_procesa_en_cada_venta():
    """El POS no debe esperar por la red al cobrar: se encola y se sigue.

    Si `procesar_cola` se llamara desde la venta, el cajero pagaría el tiempo
    de espera de la DIAN en cada cobro.
    """
    import io
    ruta = os.path.join(RAIZ, 'ferreteria', 'services', 'dian_emision.py')
    with io.open(ruta, encoding='utf-8') as f:
        src = f.read()
    i_emitir = src.find('def emitir_venta')
    i_cola = src.find('def procesar_cola')
    assert i_emitir != -1 and i_cola != -1
    # Entre la definición de emitir_venta y la de procesar_cola no debe haber
    # ninguna llamada a la cola.
    cuerpo = src[i_emitir:i_cola]
    assert 'procesar_cola(' not in cuerpo.replace('def procesar_cola(', '')
