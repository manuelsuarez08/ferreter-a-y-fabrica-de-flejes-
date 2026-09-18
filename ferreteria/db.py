"""Capa de acceso a datos: conexión SQLite y esquema/migraciones.

Responsabilidad única: proveer conexiones y construir/actualizar el esquema.
Ninguna regla de negocio HTTP vive aquí, lo que permite reutilizar la capa de
datos desde scripts (cargar_datos.py, database.py) sin arrastrar dependencias
de Flask más allá de lo necesario.
"""
import os
import shutil
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from .config import (
    DB_NAME,
    DB_SEMILLA,
    DB_TIMEOUT,
    DB_BUSY_TIMEOUT_MS,
    DB_CACHE_SIZE_KB,
    INDICES,
)


def get_db():
    """Conexión SQLite optimizada para uso rápido y concurrente."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT, check_same_thread=False)
    try:
        # WAL mejora la concurrencia lectura/escritura. No aplica a :memory:.
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(f"PRAGMA cache_size={DB_CACHE_SIZE_KB}")
        conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    except sqlite3.Error:
        pass
    return conn


def migrar_columna(conn, nombre_tabla, nombre_columna, definicion):
    """Agrega una columna si no existe (idempotente)."""
    try:
        conn.execute(f"ALTER TABLE {nombre_tabla} ADD COLUMN {nombre_columna} {definicion}")
    except sqlite3.OperationalError:
        pass


def crear_indices(conn):
    """Crea los índices de búsqueda frecuente (idempotente)."""
    for sentencia in INDICES:
        try:
            conn.execute(sentencia)
        except sqlite3.Error:
            pass


def _crear_tablas_base(cursor):
    """Crea el conjunto de tablas del núcleo (usuarios, catálogo, ventas)."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            clave TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cedula_nit TEXT UNIQUE,
            telefono TEXT,
            direccion TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria TEXT,
            dimensiones TEXT,
            codigo_barras TEXT UNIQUE,
            precio_costo REAL DEFAULT 0,
            precio_venta REAL NOT NULL,
            stock_actual INTEGER NOT NULL DEFAULT 0,
            stock_minimo INTEGER DEFAULT 5,
            auditado INTEGER NOT NULL DEFAULT 0,
            stock_inicial INTEGER DEFAULT 0,
            fecha_auditoria TEXT DEFAULT NULL,
            activo INTEGER NOT NULL DEFAULT 1
        )
    ''')
    # NOTA: 'precio_costo' se conserva en la DB (opcional, por defecto 0) aunque
    # ya no se use en la interfaz ni en los cálculos del POS.
    # NOTA: 'stock_actual' ya NO tiene restricción CHECK, por lo que puede quedar
    # en saldo negativo sin bloquear la venta.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            fecha_dia TEXT NOT NULL,
            hora TEXT NOT NULL,
            total_venta REAL NOT NULL,
            saldo_pendiente REAL NOT NULL,
            tipo_pago TEXT NOT NULL,
            direccion_cliente TEXT,
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')


def _crear_tablas_operacion(cursor):
    """Crea las tablas de detalle, créditos, inventario, fábrica y caja."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta INTEGER NOT NULL,
            id_producto INTEGER NOT NULL,
            cantidad INTEGER NOT NULL,
            precio_unitario REAL NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (id_venta) REFERENCES ventas (id),
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS abonos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            monto REAL NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movimientos_inventario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_producto INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            motivo TEXT NOT NULL,
            usuario TEXT NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventario_hierro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            calibre TEXT NOT NULL,
            diametro_mm REAL NOT NULL DEFAULT 0,
            stock_kg REAL NOT NULL DEFAULT 0,
            ultimo_update TEXT,
            UNIQUE(calibre)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ordenes_figurado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta INTEGER,
            id_cliente INTEGER NOT NULL,
            numero_orden TEXT NOT NULL UNIQUE,
            estado TEXT NOT NULL DEFAULT 'En Cola'
                CHECK (estado IN ('En Cola', 'En Figurado', 'Completado')),
            fecha_creacion TEXT NOT NULL,
            fecha_actualizacion TEXT,
            fecha_entrega TEXT,
            observaciones TEXT,
            kg_total REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (id_venta) REFERENCES ventas(id),
            FOREIGN KEY (id_cliente) REFERENCES clientes(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notificaciones_flejes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_orden INTEGER NOT NULL,
            numero_orden TEXT NOT NULL,
            cliente TEXT,
            mensaje TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            leida_pedidos INTEGER NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_orden) REFERENCES ordenes_figurado(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalles_fleje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_orden INTEGER NOT NULL,
            calibre TEXT NOT NULL,
            ancho_cm REAL NOT NULL,
            largo_cm REAL NOT NULL,
            largo_gancho_cm REAL NOT NULL DEFAULT 0,
            cantidad_piezas INTEGER NOT NULL DEFAULT 1,
            metros_lineales REAL NOT NULL DEFAULT 0,
            consumo_kg REAL NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'En Cola'
                CHECK (estado IN ('En Cola', 'En Figurado', 'Completado')),
            FOREIGN KEY (id_orden) REFERENCES ordenes_figurado(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            accion TEXT NOT NULL,
            entidad TEXT NOT NULL,
            entidad_id INTEGER,
            detalles TEXT,
            fecha TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            nombre TEXT NOT NULL DEFAULT 'Ferretería y Fábrica de Flejes',
            nit TEXT DEFAULT '',
            telefono TEXT DEFAULT '',
            direccion TEXT DEFAULT '',
            consecutivo INTEGER NOT NULL DEFAULT 1
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO configuracion (id) VALUES (1)")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            categoria TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            monto REAL NOT NULL,
            usuario TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cierres_caja (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT UNIQUE NOT NULL,
            base_inicial REAL NOT NULL DEFAULT 0,
            efectivo_esperado REAL NOT NULL,
            efectivo_contado REAL NOT NULL,
            diferencia REAL NOT NULL,
            observaciones TEXT,
            usuario TEXT NOT NULL,
            fecha_registro TEXT NOT NULL
        )
    ''')


def _crear_tablas_alquiler(cursor):
    """Crea las tablas del módulo de alquiler de maquinaria."""
    for tabla in ('equipos', 'equipos_alquiler'):
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {tabla} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_interno TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                categoria TEXT NOT NULL DEFAULT '',
                marca TEXT,
                modelo TEXT,
                numero_serie TEXT,
                estado TEXT NOT NULL DEFAULT 'Disponible'
                    CHECK (estado IN ('Disponible', 'En Alquiler', 'En Mantenimiento')),
                tipo_tarifa TEXT NOT NULL DEFAULT 'dia'
                    CHECK (tipo_tarifa IN ('dia', 'hora', 'turno', 'bulto')),
                tarifa REAL NOT NULL DEFAULT 0,
                tarifa_hora REAL DEFAULT 0,
                tarifa_turno REAL DEFAULT 0,
                tarifa_bulto REAL DEFAULT 0,
                medidas TEXT,
                especificaciones TEXT,
                cantidad_disponible INTEGER NOT NULL DEFAULT 1,
                cantidad_total INTEGER NOT NULL DEFAULT 1,
                fecha_compra TEXT,
                fecha_ultimo_mantenimiento TEXT,
                observaciones TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                fecha_registro TEXT,
                fecha_actualizacion TEXT
            )
        ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alquileres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            id_usuario INTEGER NOT NULL,
            fecha_salida TEXT NOT NULL,
            fecha_devolucion_pactada TEXT NOT NULL,
            fecha_devolucion_real TEXT,
            estado TEXT NOT NULL DEFAULT 'activo'
                CHECK (estado IN ('activo', 'devuelto', 'vencido', 'cancelado')),
            valor_deposito REAL NOT NULL DEFAULT 0,
            notas_salida TEXT,
            notas_devolucion TEXT,
            cargos_extra REAL NOT NULL DEFAULT 0,
            descuento REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            total_final REAL NOT NULL DEFAULT 0,
            fecha_registro TEXT NOT NULL,
            FOREIGN KEY (id_cliente) REFERENCES clientes(id),
            FOREIGN KEY (id_usuario) REFERENCES usuarios(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_alquiler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_alquiler INTEGER NOT NULL,
            id_equipo INTEGER NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 1,
            tarifa_tipo TEXT NOT NULL,
            tarifa_valor REAL NOT NULL,
            dias_usados INTEGER DEFAULT 0,
            horas_usadas INTEGER DEFAULT 0,
            turnos_usados INTEGER DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            estado_salida TEXT NOT NULL DEFAULT 'bueno'
                CHECK (estado_salida IN ('bueno', 'con_danos', 'faltante', 'otro')),
            estado_retorno TEXT,
            observaciones TEXT,
            FOREIGN KEY (id_alquiler) REFERENCES alquileres(id),
            FOREIGN KEY (id_equipo) REFERENCES equipos_alquiler(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mantenimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_equipo INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            costo REAL NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            responsable TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente'
                CHECK (estado IN ('pendiente', 'realizado', 'cancelado')),
            FOREIGN KEY (id_equipo) REFERENCES equipos_alquiler(id)
        )
    ''')


def _crear_tablas_pedidos(cursor):
    """Crea las tablas del módulo de pedidos."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedidos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente        INTEGER NOT NULL,
            direccion_entrega TEXT,
            observaciones     TEXT,
            estado            TEXT NOT NULL DEFAULT 'pendiente',
            -- Estados: pendiente | alistando | listo | en_camino | entregado | cancelado
            usuario_vendedor  TEXT NOT NULL,
            usuario_bodega    TEXT,
            id_motocarguero   INTEGER,
            fecha_creacion    TEXT NOT NULL,
            fecha_actualizacion TEXT,
            FOREIGN KEY (id_cliente)      REFERENCES clientes (id),
            FOREIGN KEY (id_motocarguero) REFERENCES usuarios (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_pedidos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            id_pedido       INTEGER NOT NULL,
            id_producto     INTEGER NOT NULL,
            cantidad        INTEGER NOT NULL,
            precio_unitario REAL    NOT NULL,
            subtotal        REAL    NOT NULL,
            FOREIGN KEY (id_pedido)   REFERENCES pedidos (id),
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')


def _aplicar_migraciones(cursor):
    """Migraciones idempotentes sobre bases de datos ya existentes."""
    for tabla, columna, definicion in (
        ('clientes', 'direccion', 'TEXT'),
        # Datos requeridos por la factura electrónica de Siigo.
        ('clientes', 'email', 'TEXT'),
        ('clientes', 'tipo_documento', "TEXT DEFAULT 'CC'"),
        ('ventas', 'direccion_cliente', 'TEXT'),
        ('productos', 'codigo_barras', 'TEXT'),
        ('productos', 'auditado', 'INTEGER NOT NULL DEFAULT 0'),
        ('productos', 'stock_inicial', 'INTEGER DEFAULT 0'),
        ('productos', 'fecha_auditoria', 'TEXT DEFAULT NULL'),
        ('productos', 'activo', 'INTEGER NOT NULL DEFAULT 1'),
        ('ventas', 'anulada', 'INTEGER NOT NULL DEFAULT 0'),
        ('ventas', 'motivo_anulacion', 'TEXT'),
        ('ventas', 'tipo_entrega', "TEXT NOT NULL DEFAULT 'entrega_inmediata'"),
        ('ventas', 'numero_pedido', 'INTEGER'),
        ('ventas', 'estado_despacho', "TEXT DEFAULT 'pendiente_preparar'"),
        # Trazabilidad del despacho: quién y cuándo preparó y entregó el pedido.
        ('ventas', 'despacho_preparado_por', 'TEXT'),
        ('ventas', 'despacho_preparado_fecha', 'TEXT'),
        ('ventas', 'despacho_entregado_por', 'TEXT'),
        ('ventas', 'despacho_entregado_fecha', 'TEXT'),
        ('usuarios', 'nombre_completo', 'TEXT'),
        # ── Facturación electrónica opcional (Siigo) ────────────────────────
        # Estado: 'no_solicitada' (venta normal) | 'pendiente' (solicitada, sin
        # transmitir) | 'aprobada' | 'error' (rechazada o sin conexión).
        ('ventas', 'siigo_estado', "TEXT NOT NULL DEFAULT 'no_solicitada'"),
        ('ventas', 'siigo_numero', 'TEXT'),
        ('ventas', 'siigo_cufe', 'TEXT'),
        ('ventas', 'siigo_pdf_url', 'TEXT'),
        ('ventas', 'siigo_xml_url', 'TEXT'),
        ('ventas', 'siigo_error', 'TEXT'),
        ('ventas', 'siigo_fecha_emision', 'TEXT'),
        ('ventas', 'siigo_intentos', 'INTEGER NOT NULL DEFAULT 0'),
    ):
        migrar_columna(cursor, tabla, columna, definicion)

    # Base inicial de caja para el módulo de cierre diario.
    migrar_columna(cursor, 'cierres_caja', 'base_inicial', 'REAL NOT NULL DEFAULT 0')

    # IVA configurable desde la app (porcentaje sobre el subtotal). Se guarda en
    # configuracion para poder cambiarlo sin tocar codigo. Por defecto 19%.
    migrar_columna(cursor, 'configuracion', 'iva_porcentaje', 'REAL NOT NULL DEFAULT 19')
    migrar_columna(cursor, 'configuracion', 'iva_activo', 'INTEGER NOT NULL DEFAULT 1')

    # Desglose de cada venta: subtotal (sin IVA) e IVA aplicado. Quedan a 0 en
    # las ventas antiguas, que se hicieron sin IVA.
    migrar_columna(cursor, 'ventas', 'subtotal_venta', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'ventas', 'iva_valor', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'ventas', 'iva_porcentaje', 'REAL NOT NULL DEFAULT 0')

    # Desglose de IVA por producto. El precio_venta sigue siendo el PRECIO FINAL
    # (lo que paga el cliente, ya con IVA). precio_base es el valor sin IVA y
    # iva_valor el impuesto incluido. iv_tasa guarda el % aplicado a ese
    # producto (0 = exento). Productos antiguos: base = precio_venta, iva = 0.
    migrar_columna(cursor, 'productos', 'precio_base', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'productos', 'iva_valor', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'productos', 'iva_tasa', 'REAL NOT NULL DEFAULT 0')

    for tabla in ('equipos_alquiler', 'equipos'):
        for columna, definicion in (
            ('medidas', 'TEXT'),
            ('especificaciones', 'TEXT'),
            ('cantidad_disponible', 'INTEGER NOT NULL DEFAULT 1'),
            ('cantidad_total', 'INTEGER NOT NULL DEFAULT 1'),
            ('fecha_registro', 'TEXT'),
            ('fecha_actualizacion', 'TEXT'),
        ):
            migrar_columna(cursor, tabla, columna, definicion)

    # Los productos existentes se consideran activos.
    try:
        cursor.execute("UPDATE productos SET activo = 1 WHERE activo IS NULL")
    except sqlite3.Error:
        pass
    # Productos creados ANTES del desglose de IVA: no lo traian, asi que su
    # precio_venta ES el valor base (iva 0). Se rellena una sola vez para que
    # las columnas nuevas no queden en 0 y las cuentas cuadren.
    try:
        cursor.execute("UPDATE productos SET precio_base = precio_venta "
                       "WHERE COALESCE(precio_base, 0) = 0 AND COALESCE(precio_venta, 0) > 0")
    except sqlite3.Error:
        pass

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos (codigo_barras)")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ventas_numero_pedido ON ventas (numero_pedido) WHERE numero_pedido IS NOT NULL"
    )


def _sembrar_datos_por_defecto(cursor):
    """Garantiza el usuario admin y el cliente mostrador por defecto."""
    usuario_admin = cursor.execute(
        "SELECT id, clave FROM usuarios WHERE usuario = ?", ('admin',)
    ).fetchone()
    if usuario_admin is None:
        cursor.execute(
            "INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
            ('admin', generate_password_hash('admin123'), 'admin'),
        )
    else:
        cursor.execute(
            "UPDATE usuarios SET clave = ?, rol = 'admin' WHERE usuario = ?",
            (generate_password_hash('admin123'), 'admin'),
        )

    cursor.execute("SELECT COUNT(*) FROM clientes WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO clientes (id, nombre, cedula_nit, telefono, direccion) "
            "VALUES (1, 'Cliente Mostrador (General)', '222', '0000', 'Local')"
        )


def init_db():
    """Construye/actualiza el esquema completo de la base de datos."""
    conn = get_db()
    cursor = conn.cursor()

    _crear_tablas_base(cursor)
    _crear_tablas_operacion(cursor)
    _crear_tablas_alquiler(cursor)
    _crear_tablas_pedidos(cursor)
    _crear_tablas_cotizaciones(cursor)
    _aplicar_migraciones(cursor)
    _sembrar_datos_por_defecto(cursor)

    crear_indices(cursor)

    # WAL: permite leer mientras se escribe una venta (mejor concurrencia).
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    try:
        cursor.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass

    conn.commit()
    conn.close()


def respaldar_base_de_datos():
    '''Guarda una copia de seguridad de la base antes de tocarla.

    Se ejecuta en cada arranque, PERO solo si la base cambio desde el ultimo
    respaldo, y conserva solo el mas reciente. Antes creaba una copia nueva en
    CADA arranque sin borrar las anteriores: en un host con reinicios frecuentes
    (p. ej. Render, que duerme y despierta el servicio) los .db se acumulaban y
    terminaban llenando el disco, tras lo cual SQLite no podia escribir y todas
    las consultas devolvian 500 ("funciona un momento y deja de funcionar").

    Devuelve la ruta del respaldo, o None si no habia nada que respaldar.
    '''
    if not os.path.exists(DB_NAME):
        return None
    carpeta = os.path.dirname(DB_NAME) or '.'
    try:
        # Si ya existe un respaldo con el mismo tamano y fecha, la base no
        # cambio: no se genera otro (evita llenar el disco con reinicios).
        import glob
        previos = sorted(glob.glob(os.path.join(carpeta, 'ferreteria-respaldo-arranque-*.db')))
        if previos:
            ultimo = previos[-1]
            try:
                if os.path.getsize(ultimo) == os.path.getsize(DB_NAME) and \
                        os.path.getmtime(ultimo) >= os.path.getmtime(DB_NAME):
                    return None
                # La base si cambio: se reemplaza el respaldo anterior por el nuevo.
                os.remove(ultimo)
            except OSError:
                pass
        marca = datetime.now().strftime('%Y%m%d-%H%M%S')
        destino = os.path.join(carpeta, f'ferreteria-respaldo-arranque-{marca}.db')
        shutil.copy2(DB_NAME, destino)
        return destino
    except OSError:
        return None

def asegurar_base_de_datos():
    """Prepara la base de datos efectiva, sembrándola si es necesario.

    Caso de uso (Render con disco persistente): la primera vez que arranca la
    app, el disco está vacío y `DB_NAME` apunta a él. Esta función copia la
    base "semilla" del repositorio (DB_SEMILLA) al disco para no perder el
    catálogo ya cargado. En arranques posteriores el disco ya tiene datos y no
    se sobrescribe nada.

    Devuelve True si sembró la base desde el repositorio, False si ya existía.
    """
    # Camino normal (misma ruta que la semilla): no hay nada que sembrar.
    if os.path.abspath(DB_NAME) == os.path.abspath(DB_SEMILLA):
        return False

    if os.path.exists(DB_NAME):
        return False

    # El destino (disco persistente) está vacío: intentamos sembrarlo.
    carpeta = os.path.dirname(DB_NAME)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    if os.path.exists(DB_SEMILLA):
        shutil.copy2(DB_SEMILLA, DB_NAME)
        # SQLite en modo WAL puede acompañarse de -wal/-shm; no se copian a
        # propósito para empezar con una base consistente.
        return True

    return False
def _crear_tablas_cotizaciones(cursor):
    # Tablas del módulo de cotizaciones. Guardar una cotización NO toca stock,
    # cartera ni ventas: es solo una propuesta de precio al cliente.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            consecutivo_cotizacion TEXT NOT NULL UNIQUE,
            id_cliente INTEGER,
            nombre_cliente TEXT NOT NULL,
            cedula_nit TEXT,
            telefono TEXT,
            direccion TEXT,
            fecha TEXT NOT NULL,
            vigencia TEXT,
            observaciones TEXT,
            total REAL NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'Pendiente'
                CHECK (estado IN ('Pendiente', 'Aprobada', 'Vencida', 'Anulada')),
            usuario TEXT,
            FOREIGN KEY (id_cliente) REFERENCES clientes(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cotizacion INTEGER NOT NULL,
            id_producto INTEGER,
            descripcion TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            precio_unitario REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (id_cotizacion) REFERENCES cotizaciones(id),
            FOREIGN KEY (id_producto) REFERENCES productos(id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cot_cliente ON cotizaciones (id_cliente)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cot_fecha ON cotizaciones (fecha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cot_consecutivo ON cotizaciones (consecutivo_cotizacion)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detcot_cotizacion ON detalle_cotizaciones (id_cotizacion)")
    # Notificaciones cuando un cliente aprueba una cotizacion desde el enlace.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notificaciones_cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cotizacion INTEGER NOT NULL,
            consecutivo TEXT NOT NULL,
            cliente TEXT,
            mensaje TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_cotizacion) REFERENCES cotizaciones(id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notcot_leida ON notificaciones_cotizaciones (leida)")
