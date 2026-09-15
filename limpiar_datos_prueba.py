"""Limpieza de datos de prueba para iniciar operaciones reales.

CONSERVA intactos: productos, clientes, usuarios, configuracion, equipos.
ELIMINA y reinicia a 0: ventas, detalles, abonos, movimientos, auditoría,
cierres, gastos, pedidos, alquileres, fábrica (órdenes/flejes/hierro) y
notificaciones.

Hace una COPIA DE SEGURIDAD automática antes de tocar nada y reinicia los
contadores AUTOINCREMENT para que el primer folio real empiece en 1.

Uso:
    python limpiar_datos_prueba.py --dry-run   # muestra qué haría (no escribe)
    python limpiar_datos_prueba.py             # ejecuta la limpieza
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DEFECTO = os.path.join(BASE_DIR, 'ferreteria.db')

# Tablas de datos transaccionales/de prueba: se vacían por completo.
TABLAS_A_LIMPIAR = (
    'notificaciones_flejes',
    'detalles_fleje',
    'ordenes_figurado',
    'inventario_hierro',
    'detalle_alquiler',
    'alquileres',
    'mantenimientos',
    'detalle_pedidos',
    'pedidos',
    'cierres_caja',
    'gastos',
    'movimientos_inventario',
    'abonos',
    'detalle_ventas',
    'ventas',
    'auditoria',
)

# Tablas maestras que NUNCA se tocan.
TABLAS_PROTEGIDAS = (
    'productos', 'clientes', 'usuarios', 'configuracion', 'equipos', 'equipos_alquiler',
)


def _existe_tabla(cursor, tabla):
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (tabla,)
    ).fetchone() is not None


def respaldar(db):
    """Crea una copia de seguridad con marca de tiempo. Devuelve la ruta."""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destino = os.path.join(BASE_DIR, f'ferreteria-respaldo-limpieza-{stamp}.db')
    shutil.copy2(db, destino)
    return destino


def limpiar(db, dry_run=False):
    """Vacia las tablas transaccionales y reinicia sus autoincrementos."""
    conn = sqlite3.connect(db, timeout=15)
    cursor = conn.cursor()
    vaciadas = {}
    try:
        for tabla in TABLAS_A_LIMPIAR:
            if not _existe_tabla(cursor, tabla):
                continue
            n = cursor.execute(f'SELECT COUNT(*) FROM {tabla}').fetchone()[0]
            vaciadas[tabla] = n
            if not dry_run and n:
                cursor.execute(f'DELETE FROM {tabla}')

        # Reiniciar los contadores AUTOINCREMENT de las tablas limpiadas para
        # que el primer registro real tenga id 1. Nunca se tocan las protegidas.
        if not dry_run:
            for tabla in TABLAS_A_LIMPIAR:
                if _existe_tabla(cursor, tabla):
                    cursor.execute('DELETE FROM sqlite_sequence WHERE name = ?', (tabla,))
            conn.commit()
    finally:
        conn.close()
    return vaciadas


def verificar(db):
    """Devuelve el conteo final de las tablas maestras y transaccionales."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    resultado = {}
    for tabla in TABLAS_PROTEGIDAS + TABLAS_A_LIMPIAR:
        if _existe_tabla(cursor, tabla):
            resultado[tabla] = cursor.execute(f'SELECT COUNT(*) FROM {tabla}').fetchone()[0]
    conn.close()
    return resultado


def main():
    parser = argparse.ArgumentParser(description='Limpieza de datos de prueba (conserva maestros).')
    parser.add_argument('--db', default=DB_DEFECTO, help='Ruta de la base SQLite.')
    parser.add_argument('--dry-run', action='store_true', help='Simula sin escribir cambios.')
    parser.add_argument('--sin-respaldo', action='store_true', help='Omite el respaldo (no recomendado).')
    args = parser.parse_args()

    print('Base de datos:', args.db)
    print('Modo         :', 'SIMULACIÓN (dry-run)' if args.dry_run else 'LIMPIEZA REAL')

    if not args.dry_run and not args.sin_respaldo:
        ruta = respaldar(args.db)
        print('✅ Respaldo creado:', ruta)

    vaciadas = limpiar(args.db, dry_run=args.dry_run)
    print('\n--- Registros que se eliminan ---')
    for tabla, n in vaciadas.items():
        if n:
            print(f'  {tabla:28s} {n:>6}')

    print('\n--- Conteo final ---')
    for tabla, n in verificar(args.db).items():
        etiqueta = 'CONSERVADA' if tabla in TABLAS_PROTEGIDAS else 'limpia'
        print(f'  {tabla:28s} {n:>6}  [{etiqueta}]')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)
