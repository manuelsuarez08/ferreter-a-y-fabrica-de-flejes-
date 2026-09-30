"""Mide cuánto se demora el POS cuando la DIAN no responde.

Es la SIMULACIÓN de lo que vive el cajero con internet intermitente. No se toca
la DIAN real: se apunta el endpoint a un host que no resuelve, que es lo más
parecido a "se cayó el internet" sin depender de la red de verdad.

El objetivo es dejar por escrito por qué se bajaron los timeouts: con 30 s de
conexión y 60 s de respuesta, cada venta se quedaba 90 segundos esperando.
"""
import os
import shutil
import tempfile
import time

import pytest

_DIR = tempfile.mkdtemp()
os.environ['FERRETERIA_DB'] = os.path.join(_DIR, 'ferreteria.db')
shutil.copy2(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'ferreteria-semilla.db'),
    os.environ['FERRETERIA_DB'])

from ferreteria.services import dian_soap  # noqa: E402


@pytest.fixture
def sin_red(monkeypatch):
    """Apunta el endpoint a un host que nunca resuelve (RFC 6761).

    Se parchea `dian_soap._endpoint`, que es lo que usa `_llamar`. El parche
    tiene que hacerse por el objeto importado, no por una copia local: si el
    test llama a `dian_soap._llamar` y la funciónResolvedue `_endpoint` en su
    propio modulo, el monkeypatch sobre otro nombre no se ve.
    """
    monkeypatch.setattr(dian_soap, '_endpoint',
                        lambda ambiente: 'https://servidor-inexistente.invalid/wcf')
    return monkeypatch


def test_una_venta_no_se_congela_minuto_y_medio(sin_red):
    """El peor caso tiene que ser tolerable en un mostrador."""
    assert dian_soap._endpoint('2').endswith('.invalid/wcf'), (
        'el parche no se aplico: la prueba mediria la DIAN real')
    inicio = time.time()
    with pytest.raises(dian_soap.ErrorSOAP):
        dian_soap._llamar('SendBillSync', '<x/>', ambiente='2')
    real = time.time() - inicio

    peor_caso = dian_soap.TIMEOUT_CONEXION + dian_soap.TIMEOUT_RESPUESTA
    print(f'\n  fallo real en {real:.2f} s | peor caso configurado: {peor_caso} s')
    assert real < 20, f'tardó {real:.1f} s en fallar: el cajero no puede esperar eso'
    assert peor_caso <= 45, f'peor caso {peor_caso} s: demasiado'


def test_el_cambio_deja_constancia_del_valor_anterior():
    """Si alguien revierte los timeouts, esta prueba lo explica."""
    antes = 30 + 60
    ahora = dian_soap.TIMEOUT_CONEXION + dian_soap.TIMEOUT_RESPUESTA
    print(f'\n  antes: {antes} s | ahora: {ahora} s '
          f'({antes - ahora} s menos por venta sin red)')
    assert ahora < antes, 'los timeouts se revirtieron a los valores largos'
    assert ahora <= 45


def test_la_conexion_falla_rapido_por_separado():
    """Si no abre el socket, la red local está caída: no tiene sentido esperar.

    Por eso TIMEOUT_CONEXION va aparte y es mucho menor que el de respuesta.
    """
    assert dian_soap.TIMEOUT_CONEXION <= 15, (
        'con la conexión caída, esperar más de 15 s solo congela la pantalla')
    assert dian_soap.TIMEOUT_CONEXION < dian_soap.TIMEOUT_RESPUESTA
