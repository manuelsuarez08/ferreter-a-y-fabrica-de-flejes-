"""Prueba de la migración de pagos, sobre una copia de la base real."""

import os
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria import db as db_mod  # noqa: E402


def main():
    temporal = tempfile.mkdtemp(prefix='mig_pagos_')
    ruta = os.path.join(temporal, 'prueba.db')
    shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), ruta)

    # Se parte de una base con la tabla VIEJA: se construye a mano, sin las
    # columnas de pago y con el CHECK de tres estados, que es lo que hay hoy en
    # las instalaciones creadas antes de este cambio.
    conn = sqlite3.connect(ruta)
    conn.execute('DROP TABLE IF EXISTS ferreterias')
    conn.execute('''
        CREATE TABLE ferreterias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            nit TEXT DEFAULT '',
            archivo TEXT NOT NULL,
            usuario_dueno TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'activo'
                CHECK (estado IN ('activo', 'suspendido', 'cancelado')),
            telefono TEXT DEFAULT '',
            email TEXT DEFAULT '',
            direccion TEXT DEFAULT '',
            dias_suspendida INTEGER DEFAULT 0,
            notas TEXT DEFAULT '',
            creado_en TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
    ''')
    for nombre, estado in (
        ('FERRETERIA UNO', 'activo'),
        ('FERRETERIA DOS', 'suspendido'),
        ('FERRETERIA TRES', 'cancelado'),
    ):
        conn.execute(
            'INSERT INTO ferreterias (nombre, nit, archivo, usuario_dueno, '
            'estado, creado_en, actualizado_en) VALUES (?,?,?,?,?,?,?)',
            (nombre, '900111222', nombre.lower().replace(' ', '_') + '.db',
             'dueno_' + nombre[-1].lower(), estado,
             '2026-01-01 10:00:00', '2026-01-01 10:00:00'),
        )
    conn.commit()
    conn.close()
    print(f'Base con tabla VIEJA creada ({3} clientes, 3 estados)')

    # --- Se aplica la migración ---
    conn = sqlite3.connect(ruta)
    cursor = conn.cursor()
    db_mod._migrar_pagos_ferreterias(cursor)
    conn.commit()

    print('\nEsquema nuevo:')
    for r in cursor.execute('PRAGMA table_info(ferreterias)'):
        marca = '  <- nueva' if r[1] in ('plan', 'fecha_vencimiento',
                                          'dias_prorroga', 'desde_prorroga') else ''
        print(f'   {r[1]:22}{marca}')

    print('\nDatos conservados:')
    for r in cursor.execute(
        'SELECT nombre, estado, plan, fecha_vencimiento, dias_prorroga '
        'FROM ferreterias ORDER BY id'
    ):
        print(f'   {r[0]:20} {r[1]:12} plan={r[2]:8} vence={r[3]} '
              f'prorroga={r[4]}')

    # El dato clave: el estado que antes NO aceptaba la restricción ahora vale.
    print('\nPrueba del CHECK:')
    try:
        cursor.execute(
            "UPDATE ferreterias SET estado = 'prorrogado' WHERE nombre = ?",
            ('FERRETERIA UNO',))
        conn.commit()
        print('   OK    se pudo poner "prorrogado" (antes SQLite lo rechazaba)')
    except sqlite3.IntegrityError as e:
        print(f'   FALLA {e}')

    try:
        cursor.execute("UPDATE ferreterias SET estado = 'inventado' WHERE id = 1")
        conn.commit()
        print('   FALLA aceptó un estado inventado')
    except sqlite3.IntegrityError:
        print('   OK    un estado inventado sigue siendo rechazado')

    conn.close()

    # --- Idempotencia: correrla dos veces no debe romper nada ---
    conn = sqlite3.connect(ruta)
    db_mod._migrar_pagos_ferreterias(conn.cursor())
    conn.commit()
    total = conn.execute('SELECT COUNT(*) FROM ferreterias').fetchone()[0]
    tablas = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ferreterias%'"
    )]
    conn.close()
    print(f'\nIdempotencia: segunda pasada -> {total} clientes, tablas {tablas}')
    print('   OK' if total == 3 and tablas == ['ferreterias']
          else '   FALLA la migración no es idempotente')

    shutil.rmtree(temporal, ignore_errors=True)


if __name__ == '__main__':
    main()