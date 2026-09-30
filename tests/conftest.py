"""Fija la base temporal ANTES de que cualquier modulo de `ferreteria` la lea.

`ferreteria.config` resuelve `DB_NAME` en el momento del import. Si un modulo
de prueba lo importa antes de asignar `FERRETERIA_DB`, la app queda apuntando
a la base REAL del proyecto y todas las pruebas escriben sobre ella.

Por eso la copia y la variable de entorno se preparan aqui, en el conftest:
los conftest se cargan antes que cualquier modulo de `tests/`.
"""
import os
import shutil
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DIR = tempfile.mkdtemp(prefix='ferreteria-test-')
_DB = os.path.join(_DIR, 'ferreteria.db')
shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), _DB)
os.environ['FERRETERIA_DB'] = _DB
