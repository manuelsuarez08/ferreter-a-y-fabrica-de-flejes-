import sqlite3

def crear_base_de_datos():
    conexion = sqlite3.connect("ferreteria.db")
    cursor = conexion.cursor()

    # Tabla de Usuarios (Administrador / Empleado)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            usuario TEXT UNIQUE NOT NULL,
            clave TEXT NOT NULL,
            rol TEXT CHECK(rol IN ('admin', 'empleado')) NOT NULL
        )
    ''')

    # Tabla de Clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cedula_nit TEXT UNIQUE,
            telefono TEXT,
            direccion TEXT
        )
    ''')

    # Tabla de Productos / Flejes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_barras TEXT UNIQUE,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
            dimensiones TEXT,
            precio_costo REAL NOT NULL,
            precio_venta REAL NOT NULL,
            stock_actual INTEGER NOT NULL DEFAULT 0,
            stock_minimo INTEGER NOT NULL DEFAULT 5
        )
    ''')

    # Tabla de Ventas (ahora vinculada al cliente)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_usuario INTEGER,
            id_cliente INTEGER,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_venta REAL NOT NULL,
            tipo_pago TEXT CHECK(tipo_pago IN ('contado', 'credito')) NOT NULL,
            FOREIGN KEY (id_usuario) REFERENCES usuarios (id),
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')

    # Tabla de Detalle de Ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta INTEGER,
            id_producto INTEGER,
            cantidad INTEGER NOT NULL,
            precio_unitario REAL NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (id_venta) REFERENCES ventas (id),
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')

    # Tabla de Créditos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS creditos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta INTEGER,
            id_cliente INTEGER,
            saldo_pendiente REAL NOT NULL,
            estado TEXT CHECK(estado IN ('pendiente', 'pagado')) DEFAULT 'pendiente',
            FOREIGN KEY (id_venta) REFERENCES ventas (id),
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')

    # Tabla de Abonos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS abonos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_credito INTEGER,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            monto REAL NOT NULL,
            FOREIGN KEY (id_credito) REFERENCES creditos (id)
        )
    ''')

    # Insertar usuario administrador por defecto si no existe
    cursor.execute("SELECT * FROM usuarios WHERE usuario = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO usuarios (nombre, usuario, clave, rol) VALUES ('Administrador', 'admin', 'admin123', 'admin')")
        cursor.execute("INSERT INTO usuarios (nombre, usuario, clave, rol) VALUES ('Vendedor / Cajero', 'cajero', '1234', 'empleado')")

    # Insertar cliente general por defecto (para ventas rápidas de contado sin registrar datos)
    cursor.execute("SELECT * FROM clientes WHERE id = 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO clientes (id, nombre, cedula_nit, telefono, direccion) VALUES (1, 'Cliente Mostrador (General)', '222222222', '0000000000', 'Local')")

    conexion.commit()
    conexion.close()
    print("Base de datos actualizada correctamente con tablas de Clientes y Usuarios.")

if __name__ == "__main__":
    crear_base_de_datos()