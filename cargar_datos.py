import sqlite3

DB_NAME = 'ferreteria.db'

def cargar_datos_iniciales():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    print("🛠️ Creando/Verificando tablas en SQLite...")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        nombre TEXT,
        password TEXT,
        clave TEXT,
        rol TEXT NOT NULL
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        cedula_nit TEXT,
        telefono TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        categoria TEXT,
        dimensiones TEXT,
        precio_costo REAL NOT NULL,
        precio_venta REAL NOT NULL,
        stock_actual INTEGER NOT NULL,
        stock_minimo INTEGER DEFAULT 5
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_cliente INTEGER NOT NULL,
        fecha_dia TEXT NOT NULL,
        hora TEXT NOT NULL,
        tipo_pago TEXT NOT NULL,
        total_venta REAL NOT NULL,
        saldo_pendiente REAL NOT NULL,
        FOREIGN KEY (id_cliente) REFERENCES clientes (id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detalle_ventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_venta INTEGER NOT NULL,
        id_producto INTEGER NOT NULL,
        cantidad INTEGER NOT NULL,
        precio_unitario REAL NOT NULL,
        subtotal REAL NOT NULL,
        FOREIGN KEY (id_venta) REFERENCES ventas (id),
        FOREIGN KEY (id_producto) REFERENCES productos (id)
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS abonos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_cliente INTEGER NOT NULL,
        fecha TEXT NOT NULL,
        monto REAL NOT NULL,
        FOREIGN KEY (id_cliente) REFERENCES clientes (id)
    )""")

    print("🔄 Limpiando datos antiguos y reiniciando contadores de ID...")
    cursor.execute("DELETE FROM usuarios")
    cursor.execute("DELETE FROM clientes")
    cursor.execute("DELETE FROM productos")
    
    # Reiniciar el autoincremento de IDs desde 1
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='clientes'")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='usuarios'")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='productos'")

    print("👤 Cargando usuarios...")
    usuarios = [
        ('admin', 'Administrador', 'admin123', 'admin123', 'admin'),
        ('empleado', 'Empleado Mostrador', '1234', '1234', 'empleado'),
        ('vendedor', 'Vendedor Mostrador', '1234', '1234', 'empleado')
    ]
    cursor.executemany(
        "INSERT INTO usuarios (username, nombre, password, clave, rol) VALUES (?, ?, ?, ?, ?)",
        usuarios
    )

    print("👥 Cargando lista completa de clientes (iniciando desde el ID 1)...")
    clientes = [
        ('Cliente Mostrador (General)', '2222222222', '3000000000'),
        ('Aide de Perez', '', '3218933221'),
        ('Alberto Agudelo', '', '3127599723'),
        ('Alberto Arias', '', '3146479532'),
        ('Alejo Jimenez', '', '3225287739'),
        ('Alirio el paisa', '', '3148113702'),
        ('Alonso carnicero', '', '3116124036'),
        ('Alvaro Cardona', '', '3122137976'),
        ('Ananías Arias', '', '3117833076'),
        ('Angel maría zapatero', '', '3117498711'),
        ('Anibal piraquive', '', '3126868612'),
        ('Anselmo piraquive', '', '3217424699'),
        ('Argemiro marín', '', '3113943310'),
        ('Ariel piraquive', '', '3104675276'),
        ('Audelio Jimenez', '', '3137035987'),
        ('Audelo carnicero', '', '3147772648'),
        ('Augudelo la fiera', '', '3218731517'),
        ('Augusto Ceballos', '', '3104031780'),
        ('Camilo Garcia', '', '3104473663'),
        ('Carlos abuelo', '', '3122557763'),
        ('Carlos Albeiro Ramirez', '', '3137351605'),
        ('Carlos herrera', '', '3137330787'),
        ('Carlos Julio Herrera', '', '3104689849'),
        ('Carlos Mario Ceballos', '', '3127694380'),
        ('Cenaida la de marfil', '', '3104332822'),
        ('Cheo', '', '3127209503'),
        ('Chicas el de agua bonita', '', '3147822941'),
        ('Conrado', '', '3226343564'),
        ('Crisanto alvarez', '', '3136598583'),
        ('Daniel Ceballos', '', '3207435136'),
        ('Daniela Jimenez', '', '3116815779'),
        ('Dario orozco', '', '3116183344'),
        ('David Jimenez', '', '3216117267'),
        ('Delfin el pintor', '', '3147805178'),
        ('Didier piraquive', '', '3113350438'),
        ('Diego ciro', '', '3128919690'),
        ('Diego león arango', '', '3148644485'),
        ('Donaire arango', '', '3137328905'),
        ('Dubier Jimenez', '', '3113700445'),
        ('Eder orozco', '', '3148813897'),
        ('Edison herrera', '', '3113401760'),
        ('Eduardo Lopez', '', '3105041132'),
        ('Efraín agudelo', '', '3127885408'),
        ('Eladio carnicero', '', '3105018617'),
        ('Eldier Jimenez', '', '3117326656'),
        ('Elkin Ceballos', '', '3217743217'),
        ('Elvia oliveros', '', '3128322695'),
        ('Evelio zapata', '', '3148003666'),
        ('Fabio agudelo', '', '3147407002'),
        ('Fabio arango', '', '3122119109'),
        ('Fabio Ceballos', '', '3206746883'),
        ('Fabiola la de vergel', '', '3113063529'),
        ('Federico lopez', '', '3148679469'),
        ('Felipe alvarez', '', '3105437145'),
        ('Felipe Jimenez', '', '3117079940'),
        ('Fernando Agudelo', '', '3218731110'),
        ('Fernando orozco', '', '3218485295'),
        ('Francisnel marin', '', '3113028308'),
        ('Gabriel Ceballos', '', '3117621935'),
        ('Germán agudelo', '', '3136208638'),
        ('Gildardo piraquive', '', '3216440263'),
        ('Gonzalo perez', '', '3122067759'),
        ('Guillermo Ceballos', '', '3137979601'),
        ('Guillermo piraquive', '', '3217822934'),
        ('Gustavo arango', '', '3113824330'),
        ('Hernan Ceballos', '', '3117180479'),
        ('Hugo marin', '', '3113540673'),
        ('Humberto Agudelo', '', '3108945600'),
        ('Isaias agudelo', '', '3122108169'),
        ('Israel Ceballos', '', '3108422703'),
        ('Iván lopez', '', '3146178330'),
        ('Javier arango', '', '3206969567'),
        ('Jesus herrera', '', '3117717462'),
        ('Jesus marin', '', '3103859604'),
        ('Jhon Agudelo', '', '3105988301'),
        ('Jhon Fredy Ceballos', '', '3146820521'),
        ('Jhon Jairo Arango', '', '3113061658'),
        ('Jhonatan alvarez', '', '3128653818'),
        ('Jhonatan perez', '', '3147890202'),
        ('Jorge agudelo', '', '3146313170'),
        ('Jorge el electricista', '', '3117961234'),
        ('Jorge herrera', '', '3128510865'),
        ('Jose alvarez', '', '3113168270'),
        ('Jose Ceballos', '', '3127829910'),
        ('Jose Joaquin Perez', '', '3148187880'),
        ('Juan Carlos Agudelo', '', '3128509823'),
        ('Juan Carlos Marin', '', '3104107120'),
        ('Julian Ceballos', '', '3147402677'),
        ('Julio lopez', '', '3128668388'),
        ('Leonel agudelo', '', '3148281080'),
        ('Lina Maria Arango', '', '3103912850'),
        ('Lucia Jimenez', '', '3122967664'),
        ('Luis alvarez', '', '3113783930'),
        ('Luis Eduardo Perez', '', '3147910123'),
        ('Luis Fernando Ceballos', '', '3105128990'),
        ('Manuel Agudelo', '', '3136152480'),
        ('Marco Tulio Herrera', '', '3108331122'),
        ('Maria Elena Marin', '', '3127118899'),
        ('Mario arango', '', '3117300900'),
        ('Mauricio Ceballos', '', '3146200111'),
        ('Miguel Perez', '', '3128905544'),
        ('Milena Jimenez', '', '3104558833'),
        ('Nestor Agudelo', '', '3113992211'),
        ('Orlando Ceballos', '', '3148103322'),
        ('Oscar Marin', '', '3127774411'),
        ('Pablo Perez', '', '3103332211'),
        ('Pedro Alvarez', '', '3117889900'),
        ('Rafael Ceballos', '', '3146554433'),
        ('Ramon Agudelo', '', '3128112233'),
        ('Raul Jimenez', '', '3104445566'),
        ('Rodrigo Arango', '', '3113221144'),
        ('Ruben Marin', '', '3147665544'),
        ('Santiago Perez', '', '3128334455'),
        ('Sergio Ceballos', '', '3105223344'),
        ('Silvio Agudelo', '', '3117445566'),
        ('Victor Arango', '', '3146889900'),
        ('Wilson Jimenez', '', '3127001122'),
        ('Yolanda Marin', '', '3104112233')
    ]
    cursor.executemany(
        "INSERT INTO clientes (nombre, cedula_nit, telefono) VALUES (?, ?, ?)",
        clientes
    )

    print("📦 Cargando productos...")
    productos = [
        ('Cemento Argos 50kg', 'Construcción', '50kg', 28000, 34000, 100, 10),
        ('Varilla 1/2 pulgada', 'Estructura', '6m', 18000, 23000, 150, 20),
        ('Ladrillo Limpio', 'Mampostería', 'Unidad', 800, 1200, 1000, 100)
    ]
    cursor.executemany(
        "INSERT INTO productos (nombre, categoria, dimensiones, precio_costo, precio_venta, stock_actual, stock_minimo) VALUES (?, ?, ?, ?, ?, ?, ?)",
        productos
    )

    conn.commit()
    conn.close()
    print("✅ ¡Carga finalizada con éxito! El 'Cliente Mostrador (General)' ahora tiene el ID 1.")

if __name__ == '__main__':
    cargar_datos_iniciales()