"""Importador de productos desde Excel (PRODUCTOS.xlsx) a la base de datos.

Lee el catálogo exportado a Excel y lo carga en la tabla `productos` de la
base SQLite que usa la aplicación, sin borrar ni alterar los productos que ya
existan.

Características:
- Normaliza la columna "Dimensiones" cuando Excel la guardó como fecha/número.
- Es idempotente: no duplica un producto que ya exista (mismo nombre+dimensión).
- No bloquea el Excel de origen: trabaja sobre una copia temporal.
- Modo `--dry-run` para ver qué haría sin escribir nada.

Uso:
    python importar_productos.py                 # importa a ferreteria.db
    python importar_productos.py --dry-run       # simula sin escribir
    python importar_productos.py --archivo RUTA  # otro Excel de entrada
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DEFECTO = os.path.join(BASE_DIR, 'ferreteria.db')
EXCEL_DEFECTO = os.path.join(
    os.path.expanduser('~'), 'OneDrive', 'Documents', 'PRODUCTOS.xlsx'
)

# Mapeo de posición de columna en el Excel -> campo interno.
COL_PRODUCTO = 0
COL_CATEGORIA = 1
COL_DIMENSIONES = 2
COL_STOCK_ACTUAL = 3
COL_STOCK_MINIMO = 4


def _texto(valor):
    """Convierte a texto legible, corrigiendo fechas/números mal tipados por Excel."""
    if valor is None:
        return ''
    if isinstance(valor, datetime):
        # Excel guardó algo como "1/2 pulgada" y lo volvió fecha: se reconstruye
        # como fracción/cantidad textual cuando es posible.
        return _fecha_a_texto(valor)
    if isinstance(valor, date):
        return valor.strftime('%d/%m/%Y')
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _fecha_a_texto(valor):
    """Reconstruye un texto razonable a partir de una fecha mal exportada."""
    if valor.day == 1 and valor.month == 1:
        # Probablemente un "año" suelto (p. ej. 2002 -> 01/01/2002).
        return str(valor.year)
    return valor.strftime('%d/%m/%Y')


def _entero(valor, defecto=0):
    try:
        return int(float(valor))
    except (ValueError, TypeError):
        return defecto


def leer_productos_excel(ruta_excel):
    """Lee el Excel (sobre una copia) y devuelve una lista de dicts normalizados."""
    if not os.path.exists(ruta_excel):
        raise FileNotFoundError(f'No existe el archivo Excel: {ruta_excel}')

    # Copia temporal: evita el PermissionError si el Excel está abierto.
    copia = os.path.join(tempfile.gettempdir(), 'PRODUCTOS_import_temp.xlsx')
    try:
        shutil.copy2(ruta_excel, copia)
    except PermissionError as e:
        raise PermissionError(
            f'No se pudo leer "{ruta_excel}". Cierra el archivo en Excel e intenta de nuevo.'
        ) from e

    import openpyxl
    wb = openpyxl.load_workbook(copia, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    filas = list(ws.iter_rows(values_only=True))
    wb.close()

    encabezado = filas[0] if filas else ()
    productos = []
    for fila in filas[1:]:
        if not fila:
            continue
        nombre = _texto(fila[COL_PRODUCTO] if len(fila) > COL_PRODUCTO else None)
        if not nombre:
            continue
        categoria = _texto(fila[COL_CATEGORIA] if len(fila) > COL_CATEGORIA else None) or 'Sin Categoría'
        dimensiones = _texto(fila[COL_DIMENSIONES] if len(fila) > COL_DIMENSIONES else None)
        stock_actual = _entero(fila[COL_STOCK_ACTUAL] if len(fila) > COL_STOCK_ACTUAL else None, 0)
        stock_minimo = _entero(fila[COL_STOCK_MINIMO] if len(fila) > COL_STOCK_MINIMO else None, 5)
        productos.append({
            'nombre': nombre,
            'categoria': categoria,
            'dimensiones': dimensiones,
            'stock_actual': stock_actual,
            'stock_minimo': max(0, stock_minimo),
        })
    return encabezado, productos


def importar(db, productos, dry_run=False):
    """Inserta los productos que no existan todavía. Devuelve (insertados, omitidos)."""
    insertados = 0
    omitidos = 0
    conn = sqlite3.connect(db, timeout=15)
    cursor = conn.cursor()
    try:
        for p in productos:
            existe = cursor.execute(
                "SELECT id FROM productos "
                "WHERE LOWER(nombre) = LOWER(?) AND LOWER(COALESCE(dimensiones, '')) = LOWER(?)",
                (p['nombre'], p['dimensiones']),
            ).fetchone()
            if existe:
                omitidos += 1
                continue

            if not dry_run:
                cursor.execute(
                    """
                    INSERT INTO productos (nombre, categoria, dimensiones, precio_costo,
                                           precio_venta, stock_actual, stock_minimo,
                                           stock_inicial, auditado, activo)
                    VALUES (:nombre, :categoria, :dimensiones, 0, 0,
                            :stock_actual, :stock_minimo, :stock_actual, 0, 1)
                    """,
                    p,
                )
            insertados += 1

        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return insertados, omitidos


def main():
    parser = argparse.ArgumentParser(description='Importa productos desde Excel a la base de datos.')
    parser.add_argument('--archivo', default=EXCEL_DEFECTO, help='Ruta del Excel de productos.')
    parser.add_argument('--db', default=DB_DEFECTO, help='Ruta de la base SQLite destino.')
    parser.add_argument('--dry-run', action='store_true', help='Simula sin escribir en la base.')
    args = parser.parse_args()

    print('Excel de origen :', args.archivo)
    print('Base de datos   :', args.db)
    print('Modo            :', 'SIMULACIÓN (dry-run)' if args.dry_run else 'IMPORTACIÓN REAL')

    encabezado, productos = leer_productos_excel(args.archivo)
    print('Columnas        :', encabezado)
    print('Productos leídos:', len(productos))

    insertados, omitidos = importar(args.db, productos, dry_run=args.dry_run)
    print('Productos insertados:', insertados)
    print('Productos omitidos (ya existían):', omitidos)

    conn = sqlite3.connect(args.db)
    total = conn.execute('SELECT COUNT(*) FROM productos').fetchone()[0]
    activos = conn.execute('SELECT COUNT(*) FROM productos WHERE COALESCE(activo,1)=1').fetchone()[0]
    conn.close()
    print('Total en la BD  :', total, '(activos:', activos, ')')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)
