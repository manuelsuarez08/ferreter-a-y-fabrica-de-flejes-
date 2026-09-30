"""Pruebas de la rotación de respaldos de arranque.

El problema: `respaldar_base_de_datos()` solo borraba el respaldo MÁS
RECIENTE y dejaba todos los anteriores. En una máquina que reinicia seguido
(que es exactamente el caso de un POS de mostrador) los `.db` se acumulaban:
en este repositorio había 110 copias de la base de datos, todas iguales de
tamaño, ocupando espacio sin que nadie lo notara.

La rotación ahora conserva los últimos N y borra el resto.
"""
import glob
import os
import shutil

import pytest


@pytest.fixture
def proyecto(tmp_path):
    """Un directorio que imita la carpeta de la app, con su propia base."""
    base = tmp_path / 'ferreteria.db'
    shutil.copy2(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'ferreteria-semilla.db'),
        base)
    return tmp_path, base


def _respaldos(carpeta):
    return sorted(glob.glob(os.path.join(carpeta, 'ferreteria-respaldo-arranque-*.db')))


def test_el_tope_esta_definido():
    """Debe existir un tope. Sin él, el disco se llena solo."""
    from ferreteria import db
    assert db.MAX_RESPALDOS_ARRANQUE >= 1
    assert db.MAX_RESPALDOS_ARRANQUE <= 30, (
        'un tope mayor a 30 seria absurdo para un POS de mostrador')


def test_la_rotacion_conserva_solo_los_ultimos(proyecto, monkeypatch):
    """Se crean 12 respaldos y solo deben quedar los últimos N.

    Se escriben a mano los archivos con marca de tiempo en vez de llamar a
    `respaldar_base_de_datos()`: esa función tiene una guarda de "si la base no
    cambio, no respaldo", y como aqui la base cambia en el mismo segundo, dos
    copias seguidas tienen el mismo tamaño y mtime y la segunda se salta. Lo
    que se quiere probar aquí es la ROTACIÓN, que va justo antes de esa guarda.
    """
    from ferreteria import db

    carpeta, base = proyecto
    monkeypatch.setattr(db, 'DB_NAME', str(base))
    monkeypatch.setattr(db, 'MAX_RESPALDOS_ARRANQUE', 5)

    for i in range(12):
        marca = f'20260901-{100000 + i}'
        shutil.copy2(base, os.path.join(carpeta, f'ferreteria-respaldo-arranque-{marca}.db'))

    assert len(_respaldos(carpeta)) == 12
    db.respaldar_base_de_datos()
    # Aunque la guarda interna devuelva None (la base no cambió), la limpieza
    # de los viejos ya ocurrió: eso es lo que se verifica.
    files = _respaldos(carpeta)
    assert len(files) == 5, (
        f'quedaron {len(files)} respaldos, deberian ser 5 '
        f'(se acumularon: la rotacion no esta funcionando)')
    # Y se conservan los más recientes, no los más viejos.
    assert files[-1] == os.path.join(
        carpeta, 'ferreteria-respaldo-arranque-20260901-100011.db')


def test_la_rotacion_no_toca_los_otros_respaldos(proyecto, monkeypatch):
    """Los respaldos manuales o de otro tipo NO se deben borrar."""
    from ferreteria import db

    carpeta, base = proyecto
    monkeypatch.setattr(db, 'DB_NAME', str(base))
    monkeypatch.setattr(db, 'MAX_RESPALDOS_ARRANQUE', 2)

    manual = os.path.join(carpeta, 'ferreteria-respaldo-manual.db')
    shutil.copy2(base, manual)
    otro = os.path.join(carpeta, 'ferreteria-antes-de-limpiar.db')
    shutil.copy2(base, otro)

    for i in range(6):
        marca = f'20260901-{200000 + i}'
        shutil.copy2(base, os.path.join(carpeta, f'ferreteria-respaldo-arranque-{marca}.db'))
    db.respaldar_base_de_datos()

    assert os.path.exists(manual), 'no debe borrar un respaldo con otro nombre'
    assert os.path.exists(otro), 'no debe borrar la copia de seguridad manual'


def test_si_la_base_no_cambio_no_se_genera_respaldo(proyecto, monkeypatch):
    """Reiniciar sin cambios no debe llenar el disco de copias idénticas."""
    from ferreteria import db

    carpeta, base = proyecto
    monkeypatch.setattr(db, 'DB_NAME', str(base))
    monkeypatch.setattr(db, 'MAX_RESPALDOS_ARRANQUE', 5)

    primero = db.respaldar_base_de_datos()
    # Segundo intento sin tocar la base: debe devolver None.
    segundo = db.respaldar_base_de_datos()
    assert segundo is None, 'se genero un respaldo sin que la base cambiara'
    assert primero and os.path.exists(primero)
    assert len(_respaldos(carpeta)) == 1
