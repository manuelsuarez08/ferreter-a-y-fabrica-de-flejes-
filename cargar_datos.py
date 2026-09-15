import os
import sqlite3
from werkzeug.security import generate_password_hash

from app import init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, 'ferreteria.db')


def cargar_datos_iniciales():
    print("Verificando la estructura de la base de datos...")
    init_db()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    print("Limpiando datos antiguos y reiniciando contadores de ID...")
    cursor.execute("DELETE FROM detalle_ventas")
    cursor.execute("DELETE FROM ventas")
    cursor.execute("DELETE FROM abonos")
    cursor.execute("DELETE FROM usuarios")
    cursor.execute("DELETE FROM clientes")
    cursor.execute("DELETE FROM productos")

    cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('clientes', 'usuarios', 'productos')")

    print("Cargando usuarios...")
    usuarios = [
        ('admin', generate_password_hash('admin123'), 'admin'),
        ('empleado', generate_password_hash('1234'), 'empleado'),
        ('vendedor', generate_password_hash('1234'), 'empleado')
    ]
    cursor.executemany(
        "INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
        usuarios
    )

    print("Cargando clientes...")
    clientes = [
        ('Cliente Mostrador (General)', None, '0000'),
    ]
    cursor.executemany(
        "INSERT INTO clientes (nombre, cedula_nit, telefono) VALUES (?, ?, ?)",
        clientes
    )
    print("Cargando productos...")
    # (nombre, categoria, dimensiones, precio_costo, precio_venta, stock, stock_minimo)
    # El PRECIO DE COSTO es OPCIONAL: puede ir en None y se guarda como 0 sin
    # marcar error. La columna se conserva en la DB pero ya no se usa en la UI.
    productos = [
        ('Cemento Argos 50kg', 'Construccion', '50kg', None, 34000, 100, 10),
        ('Varilla 1/2 pulgada', 'Estructura', '6m', None, 23000, 150, 20),
        ('Ladrillo Limpio', 'Mamposteria', 'Unidad', None, 1200, 1000, 100)
    ]
    # Columnas: nombre, categoria, dimensiones, precio_costo, precio_venta,
    #           stock_actual, stock_minimo, stock_inicial, auditado, activo.
    filas = [
        (f[0], f[1], f[2], f[3] or 0, f[4], f[5], f[6], f[5], 0, 1)
        for f in productos
    ]
    # Se arma la lista de columnas y sus marcadores a partir de la primera fila,
    # así el numero de '?' siempre coincide con el numero de valores.
    columnas = [
        'nombre', 'categoria', 'dimensiones', 'precio_costo', 'precio_venta',
        'stock_actual', 'stock_minimo', 'stock_inicial', 'auditado', 'activo',
    ]
    marcadores = ', '.join(['?'] * len(columnas))
    sentencia = (
        'INSERT INTO productos (' + ', '.join(columnas) + ') '
        'VALUES (' + marcadores + ')'
    )
    cursor.executemany(sentencia, filas)

    conn.commit()
    conn.close()
    print("Carga finalizada con exito. El Cliente Mostrador (General) tiene el ID 1.")


if __name__ == '__main__':
    cargar_datos_iniciales()
