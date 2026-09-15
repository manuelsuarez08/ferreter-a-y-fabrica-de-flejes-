"""Actualización masiva de precios y stock por Excel.

Flujo de trabajo en dos pasos:

    1) EXPORTAR: genera "productos_para_editar.xlsx" con todos los productos
       activos y sus columnas editables (precio_venta, stock_actual, stock_minimo).

    2) Editar ese Excel a gusto (poner precios, stock, etc.).

    3) IMPORTAR: aplica los cambios de vuelta a la base de datos, emparejando
       cada producto por su columna `id` (inmutable). Solo actualiza las filas
       cuya columna "id" esté presente.

Uso:
    python editar_precios.py exportar
    python editar_precios.py importar                 # aplica productos_para_editar.xlsx
    python editar_precios.py importar --archivo OTRO.xlsx
    python editar_precios.py importar --dry-run       # simula sin escribir
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DEFECTO = os.path.join(BASE_DIR, 'ferreteria.db')
ARCHIVO_EDITABLE = os.path.join(BASE_DIR, 'productos_para_editar.xlsx')

COLUMNAS = ['id', 'nombre', 'categoria', 'dimensiones',
            'precio_venta', 'stock_actual', 'stock_minimo']


def exportar(db, archivo, incluir_inactivos=False):
    """Exporta los productos a un Excel editable."""
    import openpyxl

    conn = sqlite3.connect(db)
    where = '' if incluir_inactivos else 'WHERE COALESCE(activo, 1) = 1'
    filas = conn.execute(
        f"SELECT id, nombre, categoria, dimensiones, precio_venta, stock_actual, stock_minimo "
        f"FROM productos {where} ORDER BY nombre COLLATE NOCASE ASC"
    ).fetchall()
    conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Productos'
    ws.append(COLUMNAS)
    for fila in filas:
        ws.append(list(fila))

    # Anchos de columna para que sea cómodo de editar.
    anchos = {'A': 8, 'B': 45, 'C': 28, 'D': 18, 'E': 14, 'F': 14, 'G': 14}
    for col, ancho in anchos.items():
        ws.column_dimensions[col].width = ancho

    wb.save(archivo)
    print(f'Exportados {len(filas)} productos a: {archivo}')
    print('Edita las columnas precio_venta / stock_actual / stock_minimo y luego ejecuta:')
    print(f'   python editar_precios.py importar')


def _a_numero(valor, defecto=None):
    if valor is None or str(valor).strip() == '':
        return defecto
    try:
        return float(valor)
    except (ValueError, TypeError):
        return defecto


def importar(db, archivo, dry_run=False):
    """Aplica los cambios del Excel a la base de datos, emparejando por id."""
    if not os.path.exists(archivo):
        raise FileNotFoundError(f'No existe el archivo: {archivo}')

    copia = os.path.join(tempfile.gettempdir(), 'productos_editar_temp.xlsx')
    try:
        shutil.copy2(archivo, copia)
    except PermissionError as e:
        raise PermissionError(
            f'No se pudo leer "{archivo}". Cierra el archivo en Excel e intenta de nuevo.'
        ) from e

    import openpyxl
    wb = openpyxl.load_workbook(copia, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    filas = list(ws.iter_rows(values_only=True))
    wb.close()

    encabezado = [str(c).strip() if c is not None else '' for c in (filas[0] if filas else [])]
    if 'id' not in encabezado:
        raise ValueError('El Excel debe tener una columna "id". Usa primero "exportar".')
    idx = {nombre: encabezado.index(nombre) for nombre in COLUMNAS if nombre in encabezado}

    conn = sqlite3.connect(db, timeout=15)
    cursor = conn.cursor()
    actualizados = 0
    saltados = 0
    no_encontrados = 0
    sin_cambios = 0

    try:
        for fila in filas[1:]:
            if not fila:
                continue
            id_val = fila[idx['id']] if idx['id'] < len(fila) else None
            try:
                id_producto = int(float(id_val))
            except (ValueError, TypeError):
                saltados += 1
                continue

            actual = cursor.execute(
                "SELECT precio_venta, stock_actual, stock_minimo FROM productos WHERE id = ?",
                (id_producto,),
            ).fetchone()
            if not actual:
                no_encontrados += 1
                continue

            cambios = {}
            for campo in ('precio_venta', 'stock_actual', 'stock_minimo'):
                if campo not in idx or idx[campo] >= len(fila):
                    continue
                valor = _a_numero(fila[idx[campo]])
                if valor is None:
                    continue
                if campo in ('stock_actual', 'stock_minimo'):
                    cambios[campo] = int(valor)
                else:
                    cambios[campo] = float(valor)

            if not cambios:
                sin_cambios += 1
                continue

            # Solo actualiza si algo cambia de verdad respecto al valor actual.
            if all(
                (k == 'precio_venta' and float(actual[0] or 0) == float(v))
                or (k == 'stock_actual' and int(actual[1] or 0) == int(v))
                or (k == 'stock_minimo' and int(actual[2] or 0) == int(v))
                for k, v in cambios.items()
            ):
                sin_cambios += 1
                continue

            if not dry_run:
                sets = ', '.join(f"{k} = ?" for k in cambios)
                cursor.execute(
                    f"UPDATE productos SET {sets} WHERE id = ?",
                    list(cambios.values()) + [id_producto],
                )
            actualizados += 1

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return actualizados, sin_cambios, saltados, no_encontrados


def main():
    parser = argparse.ArgumentParser(description='Actualización masiva de precios y stock por Excel.')
    parser.add_argument('accion', choices=['exportar', 'importar'])
    parser.add_argument('--db', default=DB_DEFECTO, help='Ruta de la base SQLite.')
    parser.add_argument('--archivo', default=ARCHIVO_EDITABLE, help='Excel para exportar/importar.')
    parser.add_argument('--incluir-inactivos', action='store_true', help='Exporta también los inactivos.')
    parser.add_argument('--dry-run', action='store_true', help='Simula la importación sin escribir.')
    args = parser.parse_args()

    if args.accion == 'exportar':
        exportar(args.db, args.archivo, incluir_inactivos=args.incluir_inactivos)
        return

    print('Base de datos:', args.db)
    print('Archivo      :', args.archivo)
    print('Modo         :', 'SIMULACIÓN (dry-run)' if args.dry_run else 'APLICAR CAMBIOS')
    actualizados, sin_cambios, saltados, no_encontrados = importar(args.db, args.archivo, dry_run=args.dry_run)
    print('Actualizados    :', actualizados)
    print('Sin cambios     :', sin_cambios)
    print('Filas saltadas  :', saltados)
    print('IDs no hallados :', no_encontrados)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)
