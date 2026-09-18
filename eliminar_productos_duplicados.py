"""Elimina productos duplicados y basura de importacion del catalogo.

Que borra (y que NO):

  1. DUPLICADOS EXACTOS: mismo nombre + dimension + categoria + precio + stock.
     Es el unico criterio que representa un producto realmente repetido. Hoy da 0.

  2. DIMENSION CORRUPTA POR EXCEL: filas cuya "dimensiones" quedo convertida en
     fecha o anio por el export de Excel (p. ej. "02/01/2008", "2002", "1964").
     Son filas de importacion basura (dimension real perdida). Solo se borran si
     el producto NO tiene ventas, movimientos ni pedidos asociados.

Lo que NUNCA se borra:
  - Variantes por medida (mismo nombre, distinta dimension): son productos
    distintos y legitimos (Tornillo Drywall en 3/4", 1", etc.).

Hace respaldo automatico antes de escribir y tiene --dry-run.

Uso:
    python eliminar_productos_duplicados.py --dry-run   # muestra que haria
    python eliminar_productos_duplicados.py             # ejecuta (con respaldo)
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DEFECTO = os.path.join(BASE_DIR, 'ferreteria.db')

# Dimension que Excel convirtio en fecha (dd/mm/aaaa o dd/mm/aa) o en anio suelto.
PATRON_FECHA = re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}$')
PATRON_ANIO = re.compile(r'^(19|20)\d{2}$')

# Tablas que referencian productos: si un producto aparece aqui, no se borra.
TABLAS_DEPENDIENTES = (
    ('detalle_ventas', 'id_producto'),
    ('detalle_pedidos', 'id_producto'),
    ('movimientos_inventario', 'id_producto'),
    ('detalle_cotizaciones', 'id_producto'),
)


def _es_dimension_corrupta(dimension):
    """True si la dimension es en realidad una fecha/anio mal exportado por Excel."""
    d = (dimension or '').strip()
    return bool(PATRON_FECHA.match(d) or PATRON_ANIO.match(d))


def _tiene_dependencias(cursor, id_producto):
    """True si el producto esta referenciado en ventas, pedidos o movimientos."""
    for tabla, columna in TABLAS_DEPENDIENTES:
        try:
            fila = cursor.execute(
                f"SELECT 1 FROM {tabla} WHERE {columna} = ? LIMIT 1", (id_producto,)
            ).fetchone()
        except sqlite3.OperationalError:
            continue  # la tabla no existe en esta base
        if fila:
            return True
    return False


def analizar(db):
    """Devuelve (duplicados_exactos, dimension_corrupta) sin escribir nada."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    # --- 1) Duplicados exactos: se conserva el de menor id de cada grupo. ---
    filas = cursor.execute("""
        SELECT LOWER(TRIM(nombre)), LOWER(TRIM(COALESCE(dimensiones,''))),
               LOWER(TRIM(COALESCE(categoria,''))), COALESCE(precio_venta,0),
               COALESCE(stock_actual,0), id, nombre, dimensiones
        FROM productos
        ORDER BY id
    """).fetchall()

    vistos = {}
    duplicados = []
    for clave_n, clave_d, clave_c, clave_p, clave_s, pid, nombre, dim in filas:
        clave = (clave_n, clave_d, clave_c, clave_p, clave_s)
        if clave in vistos:
            duplicados.append((pid, nombre, dim, f'repetido de id {vistos[clave]}'))
        else:
            vistos[clave] = pid

    # --- 2) Ruido de importacion: dimension corrupta CON hermano sano. ---
    # Solo se marca una fila con dimension corrupta si EXISTE otro producto con
    # el mismo nombre y una dimension valida. Asi se garantiza que la fila es
    # una traza de la importacion y NO el unico ejemplar del producto: borrarla
    # no elimina el producto, solo su copia con la dimension perdida.
    # Las filas corruptas sin hermano sano NO se tocan (borrarlas perderia el
    # producto real, cuya dimension simplemente se cargo mal).
    todos = cursor.execute(
        "SELECT id, nombre, COALESCE(dimensiones,'') FROM productos"
    ).fetchall()
    por_nombre = {}
    for pid, nombre, dim in todos:
        por_nombre.setdefault(nombre.strip().lower(), []).append((pid, dim))

    corruptas = []
    for pid, nombre, dim in todos:
        if not _es_dimension_corrupta(dim):
            continue
        hermanos = por_nombre.get(nombre.strip().lower(), [])
        tiene_hermano_sano = any(
            otro_id != pid and not _es_dimension_corrupta(otra_dim)
            for otro_id, otra_dim in hermanos
        )
        if not tiene_hermano_sano:
            continue
        if _tiene_dependencias(cursor, pid):
            continue
        corruptas.append((pid, nombre, dim, 'ruido de Excel con hermano sano'))

    conn.close()
    return duplicados, corruptas


def respaldar(db):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destino = os.path.join(BASE_DIR, f'ferreteria-respaldo-duplicados-{stamp}.db')
    shutil.copy2(db, destino)
    return destino


def eliminar(db, ids):
    """Borra los productos indicados. Devuelve cuantos elimino."""
    if not ids:
        return 0
    conn = sqlite3.connect(db, timeout=15)
    cursor = conn.cursor()
    marcas = ','.join('?' * len(ids))
    cursor.execute(f"DELETE FROM productos WHERE id IN ({marcas})", ids)
    n = cursor.rowcount
    conn.commit()
    conn.close()
    return n


def main():
    parser = argparse.ArgumentParser(
        description='Elimina productos duplicados exactos y basura de importacion.'
    )
    parser.add_argument('--db', default=DB_DEFECTO)
    parser.add_argument('--dry-run', action='store_true',
                        help='Muestra que haria sin escribir ni respaldar.')
    parser.add_argument('--limite', type=int, default=40,
                        help='Cuantas filas mostrar en pantalla.')
    args = parser.parse_args()

    print('Base de datos :', args.db)
    print('Modo          :', 'SIMULACION (dry-run)' if args.dry_run else 'ELIMINACION REAL')

    total_antes = sqlite3.connect(args.db).execute('SELECT COUNT(*) FROM productos').fetchone()[0]
    duplicados, corruptas = analizar(args.db)

    print(f'\n--- 1) Duplicados EXACTOS (mismo nombre+dimension+categoria+precio+stock): {len(duplicados)} ---')
    for pid, nombre, dim, motivo in duplicados[:args.limite]:
        print(f'   id={pid:<5} "{nombre[:45]}" dim="{dim}"  [{motivo}]')

    print(f'\n--- 2) Dimension corrupta (fecha/anio de Excel, sin dependencias): {len(corruptas)} ---')
    for pid, nombre, dim, motivo in corruptas[:args.limite]:
        print(f'   id={pid:<5} "{nombre[:45]}" dim="{dim}"  [{motivo}]')

    ids = [pid for pid, *_ in duplicados] + [pid for pid, *_ in corruptas]
    print(f'\nTotal a eliminar: {len(ids)}  (de {total_antes} productos)')

    if args.dry_run:
        print('\n(Modo simulacion: no se escribio nada.)')
        return

    if not ids:
        print('No hay nada que eliminar.')
        return

    ruta = respaldar(args.db)
    print('\n[OK] Respaldo creado:', ruta)
    n = eliminar(args.db, ids)
    total_despues = sqlite3.connect(args.db).execute('SELECT COUNT(*) FROM productos').fetchone()[0]
    print(f'[OK] Productos eliminados: {n}')
    print(f'[OK] Productos antes/despues: {total_antes} -> {total_despues}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)
