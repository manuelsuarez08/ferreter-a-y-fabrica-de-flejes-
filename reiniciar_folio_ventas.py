"""Reinicia el consecutivo de facturas/ventas para que la proxima sea la N. 1.

Sirve tanto en el PC local como en el Shell de Render: resuelve la base de
datos igual que la app (variable FERRETERIA_DB, o la semilla junto al codigo).

QUE HACE
  1. Muestra el estado actual: ultima venta, total y contador AUTOINCREMENT.
  2. Pide confirmacion escrita (a menos que se use --si).
  3. Crea un RESPALDO con marca de tiempo antes de tocar nada.
  4. Vacia las tablas transaccionales ligadas a las ventas y reinicia a 1 sus
     contadores AUTOINCREMENT.
  5. Verifica y muestra el resultado.

QUE NO TOCA
  productos, clientes, usuarios, configuracion, equipos. Las ventas que se
  borran son SOLO de la base indicada: en Render es el disco persistente, en
  el PC es el archivo local. Son bases distintas.

USO
  python reiniciar_folio_ventas.py            # interactivo (recomendado)
  python reiniciar_folio_ventas.py --estado   # solo muestra el estado
  python reiniciar_folio_ventas.py --si       # sin preguntar (uso en Render)
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Tablas transaccionales que se vacian junto con las ventas. El orden importa
# poco porque solo se hace DELETE (no hay claves foraneas con ON DELETE).
TABLAS_A_REINICIAR = (
    'detalle_ventas',
    'abonos',
    'movimientos_inventario',
    'ventas',
)

# Maestras que NUNCA se tocan.
TABLAS_PROTEGIDAS = ('productos', 'clientes', 'usuarios', 'configuracion')


def resolver_db():
    """Misma regla que ferreteria/config.py: FERRETERIA_DB manda, si no la semilla."""
    return os.environ.get('FERRETERIA_DB') or os.path.join(BASE_DIR, 'ferreteria.db')


def existe_tabla(cursor, tabla):
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (tabla,)
    ).fetchone() is not None


def mostrar_estado(db):
    """Imprime el estado actual sin modificar nada."""
    if not os.path.exists(db):
        print(f'[ERROR] No existe la base de datos: {db}')
        return None
    conn = sqlite3.connect(db, timeout=15)
    cur = conn.cursor()
    max_id = cur.execute('SELECT COALESCE(MAX(id), 0) FROM ventas').fetchone()[0]
    total = cur.execute('SELECT COUNT(*) FROM ventas').fetchone()[0]
    fila = cur.execute("SELECT seq FROM sqlite_sequence WHERE name = 'ventas'").fetchone()
    print('Base de datos      :', db)
    print('Ultima venta (id)  :', max_id)
    print('Ventas registradas :', total)
    print('Contador actual    :', fila[0] if fila else 'no existe')
    print('Proxima factura    :', (fila[0] if fila else 0) + 1)
    conn.close()
    return max_id


def respaldar(db):
    """Copia de seguridad con marca de tiempo. Devuelve la ruta."""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destino = os.path.join(os.path.dirname(db), f'ferreteria-respaldo-folio-{stamp}.db')
    shutil.copy2(db, destino)
    return destino


def reiniciar(db):
    """Vacia las tablas ligadas a ventas y reinicia sus contadores a 1."""
    conn = sqlite3.connect(db, timeout=15)
    cur = conn.cursor()
    borrados = {}
    try:
        for tabla in TABLAS_A_REINICIAR:
            if not existe_tabla(cur, tabla):
                continue
            n = cur.execute(f'SELECT COUNT(*) FROM {tabla}').fetchone()[0]
            borrados[tabla] = n
            cur.execute(f'DELETE FROM {tabla}')
        # Borrar la fila de sqlite_sequence reinicia el AUTOINCREMENT a 1.
        for tabla in TABLAS_A_REINICIAR:
            if existe_tabla(cur, tabla):
                cur.execute('DELETE FROM sqlite_sequence WHERE name = ?', (tabla,))
        conn.commit()
        # Devolver el espacio en disco (opcional, no afecta los datos).
        cur.execute('VACUUM')
        conn.commit()
    finally:
        conn.close()
    return borrados


def verificar(db):
    conn = sqlite3.connect(db, timeout=15)
    cur = conn.cursor()
    resultado = {}
    for tabla in TABLAS_PROTEGIDAS + TABLAS_A_REINICIAR:
        if existe_tabla(cur, tabla):
            resultado[tabla] = cur.execute(f'SELECT COUNT(*) FROM {tabla}').fetchone()[0]
    fila = cur.execute("SELECT seq FROM sqlite_sequence WHERE name = 'ventas'").fetchone()
    conn.close()
    return resultado, (fila[0] if fila else 0)


def main():
    parser = argparse.ArgumentParser(description='Reinicia el consecutivo de facturas a 1.')
    parser.add_argument('--si', action='store_true', help='No pedir confirmacion (uso en Render).')
    parser.add_argument('--estado', action='store_true', help='Solo mostrar el estado, sin cambios.')
    args = parser.parse_args()

    db = resolver_db()
    print('=== Reinicio del consecutivo de facturas ===')
    anterior = mostrar_estado(db)
    if anterior is None:
        sys.exit(1)
    if args.estado:
        return

    print('\nOJO: se BORRARAN las ventas de ESTA base de datos (no toca productos/clientes).')
    if not args.si:
        respuesta = input('Escriba REINICIAR para confirmar: ').strip()
        if respuesta != 'REINICIAR':
            print('Cancelado. No se cambio nada.')
            return

    ruta = respaldar(db)
    print('\n[OK] Respaldo creado:', ruta)

    borrados = reiniciar(db)
    print('\n--- Registros eliminados ---')
    hubo = False
    for tabla, n in borrados.items():
        if n:
            print(f'  {tabla:24s} {n:>6}')
            hubo = True
    if not hubo:
        print('  (no habia registros que borrar)')

    resultado, contador = verificar(db)
    print('\n--- Estado final ---')
    for tabla, n in resultado.items():
        etiqueta = 'CONSERVADA' if tabla in TABLAS_PROTEGIDAS else 'limpia'
        print(f'  {tabla:24s} {n:>6}  [{etiqueta}]')
    print('\nProxima factura sera la N.', contador + 1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nCancelado por el usuario.')
        sys.exit(1)
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)
