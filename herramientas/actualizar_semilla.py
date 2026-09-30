"""Actualiza la base semilla con el esquema actual.

La semilla es la plantilla que se copia al crear la instancia de cada
ferretería. Si le faltan columnas, el provisionamiento avisa pero no escribe
(es lo correcto: no se bloquea el alta), así que mantenerla al día desde aquí
evita depender de ese aviso.
"""
import os
import shutil
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria import db  # noqa: E402
from ferreteria.services.provisionamiento import (  # noqa: E402
    NIT_PROVEEDOR_SOFTWARE,
)

SEMILLA = os.path.join(RAIZ, 'ferreteria-semilla.db')


def actualizar(ruta):
    conn = sqlite3.connect(ruta)
    cursor = conn.cursor()

    db._crear_tablas_base(cursor)
    db._crear_tablas_operacion(cursor)
    db._crear_tablas_alquiler(cursor)
    db._crear_tablas_pedidos(cursor)
    db._crear_tablas_cotizaciones(cursor)
    db._crear_tablas_dian(cursor)
    db._aplicar_migraciones(cursor)
    db.crear_indices(cursor)

    # Datos por defecto del negocio: la identidad del proveedor de software es
    # IGUAL para todas las ferreterías, así que va sembrada en la plantilla.
    # El nombre queda vacío a propósito: cada ferretería pone el suyo al ser
    # provisionada, y una plantilla con un nombre fijo haría que todas salieran
    # con el nombre de la primera.
    columnas = {r[1] for r in cursor.execute('PRAGMA table_info(configuracion)')}
    if 'software_proveedor_nit' in columnas:
        cursor.execute(
            "UPDATE configuracion SET software_proveedor_nit = ? WHERE id = 1",
            (NIT_PROVEEDOR_SOFTWARE,),
        )
    if 'software_proveedor_nombre' in columnas:
        cursor.execute(
            "UPDATE configuracion SET software_proveedor_nombre = '' WHERE id = 1"
        )

    conn.commit()

    verificacion = {
        'software_proveedor_nit': 'software_proveedor_nit' in columnas,
        'ferreterias': bool(cursor.execute(
            "SELECT name FROM sqlite_master WHERE name = 'ferreterias'"
        ).fetchone()),
        'historial': bool(cursor.execute(
            "SELECT name FROM sqlite_master WHERE name = 'historial_provisionamiento'"
        ).fetchone()),
        'productos': cursor.execute(
            'SELECT COUNT(*) FROM productos').fetchone()[0],
        'integridad': cursor.execute('PRAGMA integrity_check').fetchone()[0],
    }
    conn.close()
    return verificacion


if __name__ == '__main__':
    if not os.path.exists(SEMILLA):
        sys.exit(f'No existe la semilla: {SEMILLA}')

    # Respaldo antes de tocar: la semilla es la plantilla de TODAS las
    # ferreterías, y un error acá afecta a cada alta futura.
    respaldo = SEMILLA + '.bak'
    shutil.copy2(SEMILLA, respaldo)

    resultado = actualizar(SEMILLA)
    print('Semilla actualizada. Respaldo en:', os.path.basename(respaldo))
    for clave, valor in resultado.items():
        print(f'  {clave}: {valor}')