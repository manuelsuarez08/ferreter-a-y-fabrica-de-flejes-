from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3
import os
import shutil
from datetime import datetime
import math

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_ferreteria')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, 'ferreteria.db')

# Asignación para que servidores como Render/Gunicorn detecten la aplicación automáticamente
server = app


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'usuario' not in session:
            return jsonify({'error': 'Debe iniciar sesión'}), 401
        return view(*args, **kwargs)
    return wrapped_view


def rol_required(*roles_permitidos):
    """Decorador que permite el acceso solo a los roles indicados."""
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if 'usuario' not in session:
                return jsonify({'error': 'Debe iniciar sesión'}), 401
            if session.get('rol') not in roles_permitidos:
                return jsonify({'error': f'Acceso denegado. Se requiere uno de estos roles: {", ".join(roles_permitidos)}'}), 403
            return view(*args, **kwargs)
        return wrapped_view
    return decorator


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get('rol') != 'admin':
            return jsonify({'error': 'Acceso denegado. Solo el administrador puede realizar esta acción.'}), 403
        return view(*args, **kwargs)
    return wrapped_view


def calcular_total_alquiler(fecha_inicio, fecha_fin, tarifa_tipo, tarifa_valor, cantidad=1):
    try:
        inicio = datetime.fromisoformat(str(fecha_inicio).replace('Z', '+00:00'))
        fin = datetime.fromisoformat(str(fecha_fin).replace('Z', '+00:00'))
    except ValueError:
        return 0

    delta = fin - inicio
    cantidad = max(1, int(cantidad or 1))
    tarifa_valor = float(tarifa_valor or 0)

    if tarifa_tipo == 'dia':
        dias = max(1, delta.days + (1 if delta.seconds > 0 else 0))
        return round(dias * tarifa_valor * cantidad, 2)
    if tarifa_tipo == 'hora':
        horas = max(1, int(delta.total_seconds() / 3600))
        return round(horas * tarifa_valor * cantidad, 2)
    if tarifa_tipo == 'turno':
        horas = max(1, int(delta.total_seconds() / 3600))
        turnos = max(1, int(horas / 8))
        return round(turnos * tarifa_valor * cantidad, 2)
    if tarifa_tipo == 'bulto':
        return round(tarifa_valor * cantidad, 2)
    return round(tarifa_valor * cantidad, 2)


def registrar_auditoria(conn, accion, entidad, entidad_id=None, detalles=''):
    conn.execute("""
        INSERT INTO auditoria (usuario, accion, entidad, entidad_id, detalles, fecha)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session.get('usuario', 'sistema'), accion, entidad, entidad_id,
          detalles, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))


def _migrar_columna_tabla(conn, nombre_tabla, nombre_columna, definicion):
    try:
        conn.execute(f"ALTER TABLE {nombre_tabla} ADD COLUMN {nombre_columna} {definicion}")
    except sqlite3.OperationalError:
        pass


def _normalizar_datos_equipo(data):
    payload = data or {}
    nombre = str(payload.get('nombre') or payload.get('equipo') or '').strip()
    codigo = str(payload.get('codigo_interno') or payload.get('codigo') or '').strip()
    categoria = str(payload.get('categoria') or '').strip()
    tipo_tarifa = str(payload.get('tipo_tarifa') or payload.get('tarifa_tipo') or 'dia').strip() or 'dia'
    if tipo_tarifa not in ('dia', 'hora', 'turno', 'bulto'):
        tipo_tarifa = 'dia'
    tarifa = float(payload.get('tarifa') or payload.get('tarifa_dia') or payload.get('tarifa_valor') or 0)
    tarifa_hora = float(payload.get('tarifa_hora') or 0)
    tarifa_turno = float(payload.get('tarifa_turno') or 0)
    tarifa_bulto = float(payload.get('tarifa_bulto') or 0)
    estado = str(payload.get('estado') or 'Disponible').strip() or 'Disponible'
    if estado not in ('Disponible', 'En Alquiler', 'En Mantenimiento'):
        estado = 'Disponible'
    medidas = str(payload.get('medidas') or '').strip()
    especificaciones = str(payload.get('especificaciones') or payload.get('especificacion') or '').strip()
    cantidad_disponible = int(payload.get('cantidad_disponible') or payload.get('cantidad_total') or 1)
    cantidad_total = int(payload.get('cantidad_total') or cantidad_disponible or 1)
    return {
        'nombre': nombre,
        'codigo_interno': codigo,
        'categoria': categoria,
        'marca': str(payload.get('marca') or '').strip(),
        'modelo': str(payload.get('modelo') or '').strip(),
        'numero_serie': str(payload.get('numero_serie') or '').strip(),
        'estado': estado,
        'tipo_tarifa': tipo_tarifa,
        'tarifa': tarifa,
        'tarifa_hora': tarifa_hora,
        'tarifa_turno': tarifa_turno,
        'tarifa_bulto': tarifa_bulto,
        'medidas': medidas,
        'especificaciones': especificaciones,
        'cantidad_disponible': max(0, cantidad_disponible),
        'cantidad_total': max(0, cantidad_total),
        'fecha_compra': str(payload.get('fecha_compra') or '').strip() or None,
        'fecha_ultimo_mantenimiento': str(payload.get('fecha_ultimo_mantenimiento') or '').strip() or None,
        'observaciones': str(payload.get('observaciones') or '').strip(),
        'activo': 1,
    }


def _parse_calibre_mm(calibre):
    if calibre is None:
        return 0.0
    texto = str(calibre).strip().lower().replace(' ', '')
    if not texto:
        return 0.0
    equivalencias = {
        '1/4"': 6.35, '1/4in': 6.35, '1/4': 6.35,
        '3/8"': 9.53, '3/8in': 9.53, '3/8': 9.53,
        '1/2"': 12.7, '1/2in': 12.7, '1/2': 12.7,
        '5/8"': 15.88, '5/8in': 15.88, '5/8': 15.88,
        '3/4"': 19.05, '3/4in': 19.05, '3/4': 19.05,
        '7/8"': 22.23, '7/8in': 22.23, '7/8': 22.23,
        '1"': 25.4, '1in': 25.4, '1': 25.4,
        '6mm': 6.0, '8mm': 8.0, '10mm': 10.0, '12mm': 12.0,
        '16mm': 16.0, '18mm': 18.0, '20mm': 20.0, '22mm': 22.0,
        '25mm': 25.0, '32mm': 32.0
    }
    if texto in equivalencias:
        return float(equivalencias[texto])

    texto_normal = texto.replace('"', '').replace('in', '').replace('″', '')
    if texto_normal.endswith('mm'):
        try:
            return float(texto_normal[:-2])
        except ValueError:
            return 0.0
    if texto_normal.endswith('cm'):
        try:
            return float(texto_normal[:-2]) * 10
        except ValueError:
            return 0.0
    try:
        valor = float(texto_normal)
        return valor if valor > 0 else 0.0
    except ValueError:
        return 0.0


def _calcular_consumo_fleje(ancho_cm, largo_cm, largo_gancho_cm, cantidad_piezas, calibre):
    ancho = float(ancho_cm or 0)
    largo = float(largo_cm or 0)
    gancho = float(largo_gancho_cm or 0)
    piezas = max(1, int(cantidad_piezas or 1))
    diametro_mm = _parse_calibre_mm(calibre)
    perimetro_cm = (2 * (ancho + largo)) + (2 * gancho)
    metros_lineales = (perimetro_cm / 100.0) * piezas
    if diametro_mm <= 0:
        return {
            'perimetro_cm': round(perimetro_cm, 2),
            'metros_lineales': round(metros_lineales, 4),
            'diametro_mm': 0.0,
            'consumo_kg': 0.0,
            'densidad': 0.0
        }
    area_mm2 = math.pi * ((diametro_mm / 2.0) ** 2)
    consumo_kg = metros_lineales * area_mm2 * 0.00785
    return {
        'perimetro_cm': round(perimetro_cm, 2),
        'metros_lineales': round(metros_lineales, 4),
        'diametro_mm': round(diametro_mm, 2),
        'consumo_kg': round(consumo_kg, 4),
        'densidad': round(area_mm2 * 0.00785, 4)
    }


def _registrar_orden_fleje_desde_venta(conn, id_venta, id_cliente, item, producto_nombre=''):
    calibre = str(item.get('calibre') or item.get('calibre_hierro') or '').strip()
    ancho_cm = float(item.get('ancho_cm') or item.get('ancho') or 0)
    largo_cm = float(item.get('largo_cm') or item.get('largo') or 0)
    gancho_cm = float(item.get('largo_gancho_cm') or item.get('gancho_cm') or item.get('largo_gancho') or 0)
    cantidad_piezas = int(item.get('cantidad_piezas') or item.get('cantidad') or 1)
    if not calibre or ancho_cm <= 0 or largo_cm <= 0:
        return None

    calculo = _calcular_consumo_fleje(ancho_cm, largo_cm, gancho_cm, cantidad_piezas, calibre)
    if calculo['consumo_kg'] <= 0:
        return None

    numero_orden = f'FL-{datetime.now().strftime("%Y%m%d")}-{id_venta}'
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ordenes_figurado (
            id_venta, id_cliente, numero_orden, estado, fecha_creacion, fecha_actualizacion,
            observaciones, kg_total
        ) VALUES (?, ?, ?, 'En Cola', ?, ?, ?, ?)
    """, (
        id_venta,
        id_cliente,
        numero_orden,
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        f"Pedido generado desde venta #{id_venta} - {producto_nombre}",
        calculo['consumo_kg'],
    ))
    id_orden = cursor.lastrowid
    cursor.execute("""
        INSERT INTO detalles_fleje (
            id_orden, calibre, ancho_cm, largo_cm, largo_gancho_cm, cantidad_piezas,
            metros_lineales, consumo_kg, estado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'En Cola')
    """, (
        id_orden,
        calibre,
        ancho_cm,
        largo_cm,
        gancho_cm,
        cantidad_piezas,
        calculo['metros_lineales'],
        calculo['consumo_kg'],
    ))

    row_stock = cursor.execute(
        "SELECT id, stock_kg FROM inventario_hierro WHERE calibre = ? ORDER BY id LIMIT 1",
        (calibre,)
    ).fetchone()
    if row_stock:
        if row_stock[1] < calculo['consumo_kg']:
            raise ValueError(f"Inventario insuficiente de hierro calibre {calibre}. Disponible: {row_stock[1]} kg")
        cursor.execute(
            "UPDATE inventario_hierro SET stock_kg = stock_kg - ?, ultimo_update = ? WHERE id = ?",
            (calculo['consumo_kg'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), row_stock[0])
        )
    else:
        cursor.execute(
            "INSERT INTO inventario_hierro (calibre, diametro_mm, stock_kg, ultimo_update) VALUES (?, ?, 0, ?)",
            (calibre, calculo['diametro_mm'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        raise ValueError(f"No existe inventario de hierro para el calibre {calibre}. Registre el material antes de vender flejes.")

    registrar_auditoria(conn, 'crear', 'orden_figurado', id_orden, f'Orden #{numero_orden} creada. Consumo estimado: {calculo["consumo_kg"]} kg')
    return {'id_orden': id_orden, 'numero_orden': numero_orden, 'consumo_kg': calculo['consumo_kg']}


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Tabla de usuarios
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            clave TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    ''')

    # Tabla de clientes (incluye direccion)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cedula_nit TEXT UNIQUE,
            telefono TEXT,
            direccion TEXT
        )
    ''')

    # Tabla de productos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria TEXT,
            dimensiones TEXT,
            codigo_barras TEXT UNIQUE,
            precio_costo REAL NOT NULL,
            precio_venta REAL NOT NULL,
            stock_actual INTEGER NOT NULL,
            stock_minimo INTEGER DEFAULT 5
        )
    ''')

    # Tabla de ventas (incluye direccion_cliente)
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

    # Migraciones para agregar la columna direccion en caso de que la BD ya exista
    try:
        cursor.execute("ALTER TABLE clientes ADD COLUMN direccion TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN direccion_cliente TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE productos ADD COLUMN codigo_barras TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos (codigo_barras)")

    for column, definition in (
        ('anulada', 'INTEGER NOT NULL DEFAULT 0'),
        ('motivo_anulacion', 'TEXT')
    ):
        try:
            cursor.execute(f"ALTER TABLE ventas ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass

    # Tabla de detalle de ventas
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

    # Tabla de abonos a crédito
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
            efectivo_esperado REAL NOT NULL,
            efectivo_contado REAL NOT NULL,
            diferencia REAL NOT NULL,
            observaciones TEXT,
            usuario TEXT NOT NULL,
            fecha_registro TEXT NOT NULL
        )
    ''')

    # Usuario por defecto
    usuario_admin = cursor.execute("SELECT id, clave FROM usuarios WHERE usuario = ?", ('admin',)).fetchone()
    if usuario_admin is None:
        cursor.execute("INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
                       ('admin', generate_password_hash('admin123'), 'admin'))
    else:
        cursor.execute("UPDATE usuarios SET clave = ?, rol = 'admin' WHERE usuario = ?",
                       (generate_password_hash('admin123'), 'admin'))

    # Cliente general por defecto
    cursor.execute("SELECT COUNT(*) FROM clientes WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO clientes (id, nombre, cedula_nit, telefono, direccion) VALUES (1, 'Cliente Mostrador (General)', '222222222222', '0000000000', 'Local')")

    # ── MÓDULO ALQUILER DE MAQUINARIA ───────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS equipos (
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
        CREATE TABLE IF NOT EXISTS equipos_alquiler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_interno TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
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

    for tabla in ('equipos_alquiler', 'equipos'):
        for columna, definicion in (
            ('medidas', 'TEXT'),
            ('especificaciones', 'TEXT'),
            ('cantidad_disponible', 'INTEGER NOT NULL DEFAULT 1'),
            ('cantidad_total', 'INTEGER NOT NULL DEFAULT 1'),
            ('fecha_registro', 'TEXT'),
            ('fecha_actualizacion', 'TEXT'),
        ):
            _migrar_columna_tabla(cursor, tabla, columna, definicion)

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

    # ── MÓDULO PEDIDOS ────────────────────────────────────────────────────────
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

    # Índices para búsquedas rápidas por estado
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_estado ON pedidos (estado)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_moto  ON pedidos (id_motocarguero)")

    # Migración: agregar campo nombre_completo a usuarios si no existe
    try:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN nombre_completo TEXT")
    except sqlite3.OperationalError:
        pass

    # Migración: tipo_entrega y numero_pedido en ventas
    # tipo_entrega: 'entrega_inmediata' (mostrador) | 'para_llevar' (despacho)
    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN tipo_entrega TEXT NOT NULL DEFAULT 'entrega_inmediata'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN numero_pedido INTEGER")
    except sqlite3.OperationalError:
        pass
    # estado_despacho: solo aplica a ventas para_llevar
    # pendiente_preparar | preparando | listo | entregado
    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN estado_despacho TEXT DEFAULT 'pendiente_preparar'")
    except sqlite3.OperationalError:
        pass
    # Índice único para que el consecutivo nunca se repita
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ventas_numero_pedido ON ventas (numero_pedido) WHERE numero_pedido IS NOT NULL"
    )
    # ─────────────────────────────────────────────────────────────────────────

    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', usuario=session['usuario'], rol=session.get('rol', 'empleado'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = (request.form.get('usuario') or '').strip()
        clave = request.form.get('clave') or ''

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, usuario, clave, rol FROM usuarios WHERE usuario = ?", (user,))
        row = cursor.fetchone()
        conn.close()

        password_valid = False
        stored_hash = row[2] if row else None
        if row and (stored_hash is None or stored_hash == ''):
            password_valid = (row[1] == 'admin' and clave == 'admin123')
            if password_valid:
                conn = sqlite3.connect(DB_NAME)
                conn.execute("UPDATE usuarios SET clave = ? WHERE id = ?", (generate_password_hash(clave), row[0]))
                conn.commit()
                conn.close()
        elif row and isinstance(stored_hash, str):
            if stored_hash.startswith(('pbkdf2:', 'scrypt:')):
                password_valid = check_password_hash(stored_hash, clave)
            else:
                password_valid = stored_hash == clave

        if row and password_valid:
            if isinstance(stored_hash, str) and stored_hash == clave:
                conn = sqlite3.connect(DB_NAME)
                conn.execute("UPDATE usuarios SET clave = ? WHERE id = ?", (generate_password_hash(clave), row[0]))
                conn.commit()
                conn.close()
            session['usuario'] = row[1]
            session['rol'] = row[3]
            session['id_usuario'] = row[0]
            return redirect(url_for('index'))
        return render_template('login.html', error='Credenciales incorrectas.')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/backup', methods=['GET'])
@admin_required
def descargar_respaldo():
    return send_file(DB_NAME, as_attachment=True, download_name=f"ferreteria-respaldo-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")


@app.route('/api/configuracion', methods=['GET', 'PUT'])
@login_required
def configuracion_negocio():
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'GET':
        row = conn.execute("SELECT nombre, nit, telefono, direccion, consecutivo FROM configuracion WHERE id = 1").fetchone()
        conn.close()
        return jsonify({"nombre": row[0], "nit": row[1], "telefono": row[2], "direccion": row[3], "consecutivo": row[4]})
    if session.get('rol') != 'admin':
        conn.close()
        return jsonify({"error": "Solo el administrador puede cambiar la configuración"}), 403
    data = request.json or {}
    conn.execute("UPDATE configuracion SET nombre = ?, nit = ?, telefono = ?, direccion = ? WHERE id = 1",
                 (str(data.get('nombre', '')).strip(), str(data.get('nit', '')).strip(),
                  str(data.get('telefono', '')).strip(), str(data.get('direccion', '')).strip()))
    registrar_auditoria(conn, 'actualizar', 'configuracion', 1, 'Datos del negocio actualizados')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Configuración guardada"})


@app.route('/api/reportes', methods=['GET'])
@login_required
def reportes():
    conn = sqlite3.connect(DB_NAME)
    hoy = datetime.now().date()
    periodo = request.args.get('periodo', 'hoy')
    if periodo == 'semana':
        inicio = hoy.fromordinal(hoy.toordinal() - hoy.weekday())
    elif periodo == 'mes':
        inicio = hoy.replace(day=1)
    else:
        periodo = 'hoy'
        inicio = hoy
    fecha_inicio = inicio.strftime('%Y-%m-%d')
    fecha_fin = hoy.strftime('%Y-%m-%d')
    ventas = conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(total_venta), 0),
               COALESCE(SUM(total_venta - (SELECT COALESCE(SUM(dv.cantidad * p.precio_costo), 0)
               FROM detalle_ventas dv JOIN productos p ON p.id = dv.id_producto
               WHERE dv.id_venta = v.id)), 0)
        FROM ventas v
        WHERE fecha_dia BETWEEN ? AND ? AND anulada = 0
    """, (fecha_inicio, fecha_fin)).fetchone()
    abonos = conn.execute("""
        SELECT COALESCE(SUM(monto), 0) FROM abonos
        WHERE substr(fecha, 1, 10) BETWEEN ? AND ?
    """, (fecha_inicio, fecha_fin)).fetchone()[0]
    gastos = conn.execute("""
        SELECT COALESCE(SUM(monto), 0) FROM gastos
        WHERE fecha BETWEEN ? AND ?
    """, (fecha_inicio, fecha_fin)).fetchone()[0]
    cartera = conn.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) FROM ventas WHERE saldo_pendiente > 0 AND anulada = 0").fetchone()[0]
    stock_bajo = conn.execute("SELECT COUNT(*) FROM productos WHERE stock_actual <= stock_minimo").fetchone()[0]
    top = conn.execute("""
        SELECT p.nombre, SUM(dv.cantidad) cantidad
        FROM detalle_ventas dv JOIN productos p ON p.id = dv.id_producto JOIN ventas v ON v.id = dv.id_venta
        WHERE v.anulada = 0 AND v.fecha_dia BETWEEN ? AND ?
        GROUP BY p.id ORDER BY cantidad DESC LIMIT 5
    """, (fecha_inicio, fecha_fin)).fetchall()
    conn.close()
    return jsonify({"periodo": periodo, "fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin,
                    "ventas": ventas[0], "ingresos": ventas[1], "ganancia": ventas[2],
                    "gastos": gastos, "utilidad_neta": ventas[2] - gastos,
                    "abonos": abonos, "cartera": cartera, "stock_bajo": stock_bajo,
                    "mas_vendidos": [{"nombre": row[0], "cantidad": row[1]} for row in top]})


@app.route('/api/gastos', methods=['GET', 'POST'])
@login_required
def gastos_negocio():
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'GET':
        rows = conn.execute("SELECT id, fecha, categoria, descripcion, monto, usuario FROM gastos ORDER BY id DESC LIMIT 200").fetchall()
        conn.close()
        return jsonify([{"id": r[0], "fecha": r[1], "categoria": r[2], "descripcion": r[3], "monto": r[4], "usuario": r[5]} for r in rows])
    if session.get('rol') != 'admin':
        conn.close()
        return jsonify({"error": "Solo el administrador puede registrar gastos"}), 403
    data = request.json or {}
    try:
        fecha = str(data.get('fecha') or datetime.now().strftime('%Y-%m-%d'))
        categoria = str(data.get('categoria', '')).strip()
        descripcion = str(data.get('descripcion', '')).strip()
        monto = float(data.get('monto', 0))
        if not categoria or not descripcion or monto <= 0:
            raise ValueError
        cursor = conn.cursor()
        cursor.execute("INSERT INTO gastos (fecha, categoria, descripcion, monto, usuario) VALUES (?, ?, ?, ?, ?)", (fecha, categoria, descripcion, monto, session['usuario']))
        id_gasto = cursor.lastrowid
        registrar_auditoria(conn, 'registrar', 'gasto', id_gasto, f'Gasto de {monto:.2f}')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Gasto registrado", "id": id_gasto}), 201
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "Datos del gasto inválidos"}), 400


@app.route('/api/cierres-caja', methods=['GET', 'POST'])
@login_required
def cierres_caja():
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'GET':
        rows = conn.execute("SELECT id, fecha, efectivo_esperado, efectivo_contado, diferencia, observaciones, usuario, fecha_registro FROM cierres_caja ORDER BY id DESC LIMIT 100").fetchall()
        conn.close()
        return jsonify([{"id": r[0], "fecha": r[1], "efectivo_esperado": r[2], "efectivo_contado": r[3], "diferencia": r[4], "observaciones": r[5] or "", "usuario": r[6], "fecha_registro": r[7]} for r in rows])
    if session.get('rol') != 'admin':
        conn.close()
        return jsonify({"error": "Solo el administrador puede cerrar caja"}), 403
    data = request.json or {}
    fecha = str(data.get('fecha') or datetime.now().strftime('%Y-%m-%d'))
    try:
        esperado = float(data.get('efectivo_esperado', 0))
        contado = float(data.get('efectivo_contado', 0))
        observaciones = str(data.get('observaciones', '')).strip()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(total_venta), 0) FROM ventas
            WHERE fecha_dia = ? AND tipo_pago = 'efectivo' AND anulada = 0
        """, (fecha,))
        ventas_efectivo = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(monto), 0) FROM abonos WHERE substr(fecha, 1, 10) = ?", (fecha,))
        abonos = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE fecha = ?", (fecha,))
        gastos = cursor.fetchone()[0]
        efectivo_esperado = ventas_efectivo + abonos - gastos
        diferencia = contado - efectivo_esperado
        cursor.execute("""
            INSERT INTO cierres_caja (fecha, efectivo_esperado, efectivo_contado, diferencia, observaciones, usuario, fecha_registro)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fecha) DO UPDATE SET efectivo_esperado = excluded.efectivo_esperado,
            efectivo_contado = excluded.efectivo_contado, diferencia = excluded.diferencia,
            observaciones = excluded.observaciones, usuario = excluded.usuario, fecha_registro = excluded.fecha_registro
        """, (fecha, efectivo_esperado, contado, diferencia, observaciones, session['usuario'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        registrar_auditoria(conn, 'cerrar', 'caja', None, f'Cierre {fecha}: diferencia {diferencia:.2f}')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Cierre de caja guardado", "fecha": fecha, "efectivo_esperado": efectivo_esperado, "diferencia": diferencia}), 201
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "Datos del cierre inválidos"}), 400


@app.route('/api/auditoria', methods=['GET'])
@admin_required
def consultar_auditoria():
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute("SELECT usuario, accion, entidad, entidad_id, detalles, fecha FROM auditoria ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return jsonify([{"usuario": r[0], "accion": r[1], "entidad": r[2], "entidad_id": r[3], "detalles": r[4], "fecha": r[5]} for r in rows])

# --- API ADMINISTRACIÓN DE USUARIOS ---
@app.route('/api/usuarios', methods=['GET', 'POST'])
@admin_required
def handle_usuarios():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, usuario, clave, rol FROM usuarios ORDER BY id ASC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{"id": r[0], "usuario": r[1], "rol": r[3]} for r in rows])

    elif request.method == 'POST':
        data = request.json
        try:
            cursor.execute("INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
                           (data['usuario'], generate_password_hash(data['clave']), data.get('rol', 'empleado')))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Usuario creado con éxito"}), 201
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"error": "El nombre de usuario ya existe"}), 400

@app.route('/api/usuarios/<int:id_usuario>', methods=['PUT', 'DELETE'])
@admin_required
def update_delete_usuario(id_usuario):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'PUT':
        data = request.json
        nueva_clave = data.get('clave')
        nuevo_rol = data.get('rol')

        if nueva_clave and nuevo_rol:
            cursor.execute("UPDATE usuarios SET clave = ?, rol = ? WHERE id = ?", (generate_password_hash(nueva_clave), nuevo_rol, id_usuario))
        elif nueva_clave:
            cursor.execute("UPDATE usuarios SET clave = ? WHERE id = ?", (generate_password_hash(nueva_clave), id_usuario))
        elif nuevo_rol:
            cursor.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, id_usuario))

        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Usuario actualizado correctamente"}), 200

    elif request.method == 'DELETE':
        if id_usuario == 1:
            conn.close()
            return jsonify({"error": "No se puede eliminar el usuario administrador principal"}), 400

        cursor.execute("DELETE FROM usuarios WHERE id = ?", (id_usuario,))
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Usuario eliminado con éxito"}), 200

# --- API NOTIFICACIONES DE STOCK ---
@app.route('/api/notificaciones', methods=['GET'])
@login_required
def get_notificaciones():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nombre, categoria, dimensiones, stock_actual, stock_minimo
        FROM productos
        WHERE stock_actual <= stock_minimo
        ORDER BY stock_actual ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    notificaciones = []
    for r in rows:
        nivel_urgencia = "critico" if r[4] == 0 else "alto"
        notificaciones.append({
            "id": r[0],
            "nombre": r[1],
            "categoria": r[2] or "Sin Categoría",
            "dimensiones": r[3] or "",
            "stock_actual": r[4],
            "stock_minimo": r[5],
            "urgencia": nivel_urgencia
        })

    return jsonify({
        "total": len(notificaciones),
        "alertas": notificaciones
    })

# --- API CLIENTES ---
@app.route('/api/clientes', methods=['GET', 'POST'])
@login_required
def handle_clientes():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, nombre, cedula_nit, telefono, direccion FROM clientes ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{"id": r[0], "nombre": r[1], "cedula_nit": r[2], "telefono": r[3], "direccion": r[4] or ""} for r in rows])

    elif request.method == 'POST':
        data = request.json
        if not data or not str(data.get('nombre', '')).strip():
            conn.close()
            return jsonify({"error": "El nombre del cliente es obligatorio"}), 400
        cedula_nit = str(data.get('cedula_nit', '')).strip() or None
        try:
            cursor.execute("INSERT INTO clientes (nombre, cedula_nit, telefono, direccion) VALUES (?, ?, ?, ?)",
                           (data['nombre'].strip(), cedula_nit, data.get('telefono', '').strip(), data.get('direccion', '').strip()))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Cliente creado con éxito"}), 201
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"error": "La cédula o NIT ya está registrado"}), 400


@app.route('/api/clientes/<int:id_cliente>', methods=['PUT'])
@login_required
def actualizar_cliente(id_cliente):
    data = request.json or {}
    nombre = str(data.get('nombre', '')).strip()
    cedula_nit = str(data.get('cedula_nit', '')).strip() or None
    telefono = str(data.get('telefono', '')).strip()
    direccion = str(data.get('direccion', '')).strip()

    if not nombre:
        return jsonify({"error": "El nombre del cliente es obligatorio"}), 400

    conn = sqlite3.connect(DB_NAME)
    try:
        conn.execute("""
            UPDATE clientes
            SET nombre = ?, cedula_nit = ?, telefono = ?, direccion = ?
            WHERE id = ?
        """, (nombre, cedula_nit, telefono, direccion, id_cliente))
        if conn.total_changes == 0:
            conn.close()
            return jsonify({"error": "Cliente no encontrado"}), 404
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Cliente actualizado correctamente"}), 200
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "La cédula o NIT ya está registrado"}), 400

# --- API PRODUCTOS ---
@app.route('/api/productos', methods=['GET', 'POST'])
@login_required
def handle_productos():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, nombre, categoria, dimensiones, codigo_barras, precio_costo, precio_venta, stock_actual, stock_minimo FROM productos ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{
            "id": r[0], "nombre": r[1], "categoria": r[2], "dimensiones": r[3],
            "codigo_barras": r[4] or "", "precio_costo": r[5], "precio_venta": r[6],
            "stock_actual": r[7], "stock_minimo": r[8]
        } for r in rows])

    elif request.method == 'POST':
        data = request.json
        if not data:
            conn.close()
            return jsonify({"error": "Datos del producto no válidos"}), 400
        nombre = data['nombre'].strip()
        categoria = data.get('categoria', '').strip()
        dimensiones = data.get('dimensiones', '').strip()
        codigo_barras = str(data.get('codigo_barras', '')).strip() or None
        precio_costo = float(data['precio_costo'])
        precio_venta = float(data['precio_venta'])
        stock_ingresado = int(data['stock_actual'])
        stock_minimo = int(data.get('stock_minimo', 5))

        if not nombre or precio_costo < 0 or precio_venta < 0 or stock_ingresado < 0 or stock_minimo < 0:
            conn.close()
            return jsonify({"error": "Los datos del producto no pueden ser negativos y el nombre es obligatorio"}), 400

        if codigo_barras:
            cursor.execute("SELECT id FROM productos WHERE codigo_barras = ?", (codigo_barras,))
            codigo_existente = cursor.fetchone()
            if codigo_existente:
                cursor.execute("""
                    SELECT id FROM productos
                    WHERE LOWER(nombre) = LOWER(?) AND LOWER(dimensiones) = LOWER(?)
                """, (nombre, dimensiones))
                producto_mismo_nombre = cursor.fetchone()
                if not producto_mismo_nombre or producto_mismo_nombre[0] != codigo_existente[0]:
                    conn.close()
                    return jsonify({"error": "Ese código de barras ya está asignado a otro producto"}), 400

        cursor.execute("""
            SELECT id, stock_actual FROM productos 
            WHERE LOWER(nombre) = LOWER(?) AND LOWER(dimensiones) = LOWER(?)
        """, (nombre, dimensiones))
        prod_existente = cursor.fetchone()

        if prod_existente:
            id_prod, stock_actual = prod_existente
            nuevo_stock = stock_actual + stock_ingresado
            cursor.execute("""
                UPDATE productos 
                SET categoria = ?, codigo_barras = COALESCE(?, codigo_barras), precio_costo = ?, precio_venta = ?, stock_actual = ?, stock_minimo = ?
                WHERE id = ?
            """, (categoria, codigo_barras, precio_costo, precio_venta, nuevo_stock, stock_minimo, id_prod))
            cursor.execute("INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) VALUES (?, 'entrada', ?, ?, ?, ?)", (id_prod, stock_ingresado, 'Reabastecimiento', session['usuario'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            registrar_auditoria(conn, 'reabastecer', 'producto', id_prod, f'Entrada de {stock_ingresado} unidades')
            conn.commit()
            conn.close()
            return jsonify({"mensaje": f"Stock reabastecido con éxito. Nuevo stock total: {nuevo_stock}"}), 200
        else:
            cursor.execute("""
                INSERT INTO productos (nombre, categoria, dimensiones, codigo_barras, precio_costo, precio_venta, stock_actual, stock_minimo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (nombre, categoria, dimensiones, codigo_barras, precio_costo, precio_venta, stock_ingresado, stock_minimo))
            id_producto = cursor.lastrowid
            cursor.execute("INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) VALUES (?, 'entrada', ?, ?, ?, ?)", (id_producto, stock_ingresado, 'Inventario inicial', session['usuario'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            registrar_auditoria(conn, 'crear', 'producto', id_producto, 'Producto creado')
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Producto agregado con éxito"}), 201


@app.route('/api/productos/<int:id_producto>', methods=['PUT', 'DELETE'])
@login_required
def editar_producto(id_producto):
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'DELETE' and session.get('rol') != 'admin':
        conn.close()
        return jsonify({"error": "Solo el administrador puede desactivar productos"}), 403
    if request.method == 'DELETE':
        conn.execute("UPDATE productos SET stock_actual = 0, stock_minimo = 0 WHERE id = ?", (id_producto,))
        registrar_auditoria(conn, 'desactivar', 'producto', id_producto, 'Producto desactivado')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Producto desactivado"})
    data = request.json or {}
    try:
        conn.execute("""
            UPDATE productos SET nombre = ?, categoria = ?, dimensiones = ?, codigo_barras = ?,
            precio_costo = ?, precio_venta = ?, stock_minimo = ? WHERE id = ?
        """, (str(data.get('nombre', '')).strip(), str(data.get('categoria', '')).strip(),
              str(data.get('dimensiones', '')).strip(), str(data.get('codigo_barras', '')).strip() or None,
              float(data.get('precio_costo', 0)), float(data.get('precio_venta', 0)),
              int(data.get('stock_minimo', 0)), id_producto))
        if conn.total_changes == 0:
            conn.close()
            return jsonify({"error": "Producto no encontrado"}), 404
        registrar_auditoria(conn, 'editar', 'producto', id_producto, 'Datos del producto actualizados')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Producto actualizado"})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "El código de barras ya pertenece a otro producto"}), 400


@app.route('/api/productos/barcode/<string:codigo>', methods=['GET'])
@login_required
def buscar_por_barcode(codigo):
    """Busca un producto por código de barras. Usado por el escáner físico y la cámara."""
    codigo = codigo.strip()
    if not codigo:
        return jsonify({"error": "Código de barras vacío"}), 400
    conn = sqlite3.connect(DB_NAME)
    row = conn.execute(
        "SELECT id, nombre, categoria, dimensiones, codigo_barras, precio_costo, precio_venta, stock_actual, stock_minimo FROM productos WHERE codigo_barras = ?",
        (codigo,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Producto no encontrado"}), 404
    return jsonify({
        "id": row[0], "nombre": row[1], "categoria": row[2], "dimensiones": row[3],
        "codigo_barras": row[4], "precio_costo": row[5], "precio_venta": row[6],
        "stock_actual": row[7], "stock_minimo": row[8]
    })


@app.route('/api/inventario/movimientos', methods=['GET', 'POST'])
@login_required
def movimientos_inventario():
    conn = sqlite3.connect(DB_NAME)
    if request.method == 'GET':
        rows = conn.execute("""
            SELECT m.id, p.nombre, m.tipo, m.cantidad, m.motivo, m.usuario, m.fecha
            FROM movimientos_inventario m JOIN productos p ON p.id = m.id_producto
            ORDER BY m.id DESC LIMIT 200
        """).fetchall()
        conn.close()
        return jsonify([{"id": r[0], "producto": r[1], "tipo": r[2], "cantidad": r[3], "motivo": r[4], "usuario": r[5], "fecha": r[6]} for r in rows])
    data = request.json or {}
    try:
        id_producto = int(data['id_producto'])
        cantidad = int(data['cantidad'])
        tipo = data.get('tipo', 'ajuste')
        if cantidad <= 0 or tipo not in ('entrada', 'salida', 'ajuste'):
            raise ValueError
        delta = cantidad if tipo == 'entrada' else -cantidad
        if tipo == 'ajuste':
            nuevo_stock = cantidad
            stock_row = conn.execute("SELECT stock_actual FROM productos WHERE id = ?", (id_producto,)).fetchone()
            if not stock_row:
                conn.close()
                return jsonify({"error": "Producto no encontrado"}), 400
            anterior = stock_row[0]
            delta = nuevo_stock - anterior
        else:
            nuevo_stock = None
        if nuevo_stock is not None:
            conn.execute("UPDATE productos SET stock_actual = ? WHERE id = ?", (nuevo_stock, id_producto))
        else:
            conn.execute("UPDATE productos SET stock_actual = stock_actual + ? WHERE id = ? AND stock_actual + ? >= 0", (delta, id_producto, delta))
        if conn.total_changes == 0:
            conn.close()
            return jsonify({"error": "Producto no encontrado o stock insuficiente"}), 400
        conn.execute("INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) VALUES (?, ?, ?, ?, ?, ?)", (id_producto, tipo, cantidad, str(data.get('motivo', '')).strip() or 'Ajuste manual', session['usuario'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        registrar_auditoria(conn, 'movimiento', 'producto', id_producto, f'{tipo}: {cantidad}')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Movimiento registrado"}), 201
    except (ValueError, KeyError, TypeError):
        conn.close()
        return jsonify({"error": "Datos de movimiento inválidos"}), 400


@app.route('/api/clientes/<int:id_cliente>/historial', methods=['GET'])
@login_required
def historial_cliente(id_cliente):
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute("""
        SELECT v.id, v.fecha_dia, v.hora, v.total_venta, v.tipo_pago, v.saldo_pendiente, v.anulada
        FROM ventas v WHERE v.id_cliente = ? ORDER BY v.id DESC
    """, (id_cliente,)).fetchall()
    conn.close()
    return jsonify([{"id": r[0], "fecha": f'{r[1]} {r[2]}', "total": r[3], "tipo_pago": r[4], "saldo": r[5], "anulada": bool(r[6])} for r in rows])

# --- API CREDITOS Y ABONOS ---
@app.route('/api/creditos', methods=['GET'])
@login_required
def get_creditos():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = """
        SELECT c.id, c.nombre, c.telefono, SUM(v.saldo_pendiente) as deuda_total, c.direccion
        FROM clientes c
        LEFT JOIN ventas v ON c.id = v.id_cliente AND v.saldo_pendiente > 0
        GROUP BY c.id
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"id_cliente": r[0], "nombre": r[1], "telefono": r[2], "deuda_total": r[3], "direccion": r[4] or ""} for r in rows])

@app.route('/api/abonos', methods=['POST'])
@login_required
def registrar_abono():
    data = request.json
    id_cliente = data['id_cliente']
    monto_abono = float(data['monto'])

    if monto_abono <= 0:
        return jsonify({"error": "El monto del abono debe ser mayor a cero"}), 400

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT id, saldo_pendiente FROM ventas WHERE id_cliente = ? AND saldo_pendiente > 0 ORDER BY id ASC", (id_cliente,))
    ventas_pendientes = cursor.fetchall()

    if not ventas_pendientes:
        conn.close()
        return jsonify({"error": "El cliente no tiene saldo pendiente por pagar"}), 400

    deuda_total = sum(saldo for _, saldo in ventas_pendientes)
    if monto_abono > deuda_total:
        conn.close()
        return jsonify({"error": f"El abono no puede superar la deuda total de {deuda_total:.2f}"}), 400

    monto_restante = monto_abono
    for venta_id, saldo in ventas_pendientes:
        if monto_restante <= 0:
            break
        if monto_restante >= saldo:
            monto_restante -= saldo
            cursor.execute("UPDATE ventas SET saldo_pendiente = 0 WHERE id = ?", (venta_id,))
        else:
            cursor.execute("UPDATE ventas SET saldo_pendiente = saldo_pendiente - ? WHERE id = ?", (monto_restante, venta_id))
            monto_restante = 0

    fecha_hoy = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("INSERT INTO abonos (id_cliente, monto, fecha) VALUES (?, ?, ?)", (id_cliente, monto_abono, fecha_hoy))
    id_abono = cursor.lastrowid
    registrar_auditoria(conn, 'registrar', 'abono', id_abono, f'Abono de {monto_abono:.2f}')

    cursor.execute("""
        SELECT nombre, cedula_nit, telefono, direccion
        FROM clientes
        WHERE id = ?
    """, (id_cliente,))
    cliente = cursor.fetchone()
    saldo_despues = deuda_total - monto_abono

    conn.commit()
    conn.close()
    return jsonify({
        "mensaje": "Abono registrado con éxito",
        "abono": {
            "id": id_abono,
            "fecha": fecha_hoy,
            "cliente": cliente[0],
            "cedula_nit": cliente[1] or "",
            "telefono": cliente[2] or "",
            "direccion": cliente[3] or "",
            "deuda_anterior": deuda_total,
            "monto": monto_abono,
            "saldo_pendiente": saldo_despues
        }
    }), 201


@app.route('/api/abonos/<int:id_cliente>', methods=['GET'])
@login_required
def historial_abonos(id_cliente):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nombre, cedula_nit, telefono, direccion
        FROM clientes
        WHERE id = ?
    """, (id_cliente,))
    cliente = cursor.fetchone()
    if not cliente:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404

    cursor.execute("""
        SELECT COALESCE(SUM(total_venta), 0)
        FROM ventas
        WHERE id_cliente = ? AND tipo_pago = 'credito'
    """, (id_cliente,))
    deuda_original = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT id, monto, fecha
        FROM abonos
        WHERE id_cliente = ?
        ORDER BY id ASC
    """, (id_cliente,))
    abonos = cursor.fetchall()
    conn.close()

    historial = []
    total_abonado = 0
    for id_abono, monto, fecha in abonos:
        deuda_antes = max(deuda_original - total_abonado, 0)
        total_abonado += monto
        historial.append({
            "id": id_abono,
            "fecha": fecha,
            "cliente": cliente[0],
            "cedula_nit": cliente[1] or "",
            "telefono": cliente[2] or "",
            "direccion": cliente[3] or "",
            "deuda_anterior": deuda_antes,
            "monto": monto,
            "saldo_pendiente": max(deuda_original - total_abonado, 0)
        })

    return jsonify({
        "cliente": {
            "id": id_cliente,
            "nombre": cliente[0],
            "cedula_nit": cliente[1] or "",
            "telefono": cliente[2] or "",
            "direccion": cliente[3] or ""
        },
        "abonos": historial
    })

# --- API VENTAS ---
@app.route('/api/ventas', methods=['GET', 'POST'])
@login_required
def handle_ventas():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        fecha = request.args.get('fecha')
        query = """
            SELECT v.id, v.fecha_dia, v.hora, c.nombre, v.total_venta, v.tipo_pago,
                   GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') as detalles,
                   c.cedula_nit, c.telefono, v.id_cliente,
                   COALESCE(v.direccion_cliente, c.direccion, ''), v.anulada, v.motivo_anulacion,
                   v.tipo_entrega, v.numero_pedido
            FROM ventas v
            JOIN clientes c ON v.id_cliente = c.id
            LEFT JOIN detalle_ventas dv ON v.id = dv.id_venta
            LEFT JOIN productos p ON dv.id_producto = p.id
        """
        params = []
        if fecha:
            query += " WHERE v.fecha_dia = ?"
            params.append(fecha)

        query += " GROUP BY v.id ORDER BY v.id DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{
            "id": r[0], "fecha_dia": r[1], "hora": r[2], "cliente": r[3],
            "total_venta": r[4], "tipo_pago": r[5], "productos_detalle": r[6],
            "cedula_nit": r[7], "telefono": r[8], "id_cliente": r[9], "direccion": r[10] or "",
            "anulada": bool(r[11]), "motivo_anulacion": r[12] or "",
            "tipo_entrega": r[13] or "entrega_inmediata", "numero_pedido": r[14]
        } for r in rows])

    elif request.method == 'POST':
        data = request.json
        id_cliente = data['id_cliente']
        tipo_pago = data['tipo_pago']
        items = data['items']
        direccion_ingresada = str(data.get('direccion', '')).strip()

        cursor.execute("SELECT direccion FROM clientes WHERE id = ?", (id_cliente,))
        cliente = cursor.fetchone()
        if not cliente:
            conn.close()
            return jsonify({"error": "Cliente no encontrado"}), 400

        direccion_cliente = direccion_ingresada or (cliente[0] or '')
        if direccion_ingresada:
            cursor.execute("UPDATE clientes SET direccion = ? WHERE id = ?", (direccion_ingresada, id_cliente))

        if not items:
            conn.close()
            return jsonify({"error": "La venta debe contener al menos un producto"}), 400

        now = datetime.now()
        fecha_dia = now.strftime('%Y-%m-%d')
        hora = now.strftime('%H:%M:%S')

        try:
            total_venta = 0
            detalles = []

            for item in items:
                item_id = item['id_producto']
                cursor.execute("SELECT nombre, categoria, precio_venta, stock_actual FROM productos WHERE id = ?", (item_id,))
                prod = cursor.fetchone()
                if not prod:
                    conn.close()
                    return jsonify({"error": f"Producto ID {item_id} no encontrado"}), 400

                nombre_producto, categoria_producto, precio_venta, stock_actual = prod
                cantidad = int(item['cantidad'])

                if cantidad <= 0:
                    conn.close()
                    return jsonify({"error": "La cantidad debe ser mayor que cero"}), 400

                if cantidad > stock_actual:
                    conn.close()
                    return jsonify({"error": f"Stock insuficiente para el producto ID {item_id}"}), 400

                subtotal = cantidad * precio_venta
                total_venta += subtotal
                detalles.append((item_id, cantidad, precio_venta, subtotal, nombre_producto, categoria_producto, item))

            saldo_pendiente = total_venta if tipo_pago == 'credito' else 0

            tipo_entrega = data.get('tipo_entrega', 'entrega_inmediata')
            if tipo_entrega not in ('entrega_inmediata', 'para_llevar'):
                tipo_entrega = 'entrega_inmediata'

            numero_pedido = None
            if tipo_entrega == 'para_llevar':
                row = cursor.execute(
                    "SELECT COALESCE(MAX(numero_pedido), 0) + 1 FROM ventas WHERE numero_pedido IS NOT NULL"
                ).fetchone()
                numero_pedido = row[0]

            cursor.execute("""
                INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente,
                                   tipo_pago, direccion_cliente, tipo_entrega, numero_pedido)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente,
                  tipo_pago, direccion_cliente, tipo_entrega, numero_pedido))

            id_venta = cursor.lastrowid

            for d in detalles:
                id_producto, cantidad_det, precio_unitario, subtotal, nombre_producto, categoria_producto, item = d
                cursor.execute("""
                    INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal)
                    VALUES (?, ?, ?, ?, ?)
                """, (id_venta, id_producto, cantidad_det, precio_unitario, subtotal))

                cursor.execute("""
                    UPDATE productos SET stock_actual = stock_actual - ? WHERE id = ?
                """, (cantidad_det, id_producto))
                cursor.execute(
                    "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
                    "VALUES (?, 'salida', ?, ?, ?, ?)",
                    (id_producto, cantidad_det, f'Venta #{id_venta}', session['usuario'],
                     datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                )

                producto_es_fleje = bool(
                    (categoria_producto or '').lower().find('fleje') >= 0 or
                    (nombre_producto or '').lower().find('fleje') >= 0 or
                    item.get('calibre') or item.get('ancho_cm') or item.get('largo_cm')
                )
                if producto_es_fleje:
                    origen = {
                       'calibre': item.get('calibre') or item.get('calibre_hierro'),
                       'ancho_cm': item.get('ancho_cm') or item.get('ancho'),
                       'largo_cm': item.get('largo_cm') or item.get('largo'),
                       'largo_gancho_cm': item.get('largo_gancho_cm') or item.get('gancho_cm') or item.get('largo_gancho'),
                       'cantidad_piezas': item.get('cantidad_piezas') or cantidad_det,
                    }
                    _registrar_orden_fleje_desde_venta(conn, id_venta, id_cliente, origen, nombre_producto)

            etiqueta = f'Para llevar — Pedido #{numero_pedido}' if numero_pedido else 'Entrega en mostrador'
            registrar_auditoria(conn, 'crear', 'venta', id_venta,
                               f'Venta de {total_venta:.2f} — {etiqueta}')
            conn.commit()
            conn.close()

            msg = f"Venta #{id_venta} registrada con éxito"
            if numero_pedido:
                msg += f" — Pedido #{numero_pedido} creado para despacho"
            return jsonify({
                "mensaje": msg,
                "id_venta": id_venta,
                "tipo_entrega": tipo_entrega,
                "numero_pedido": numero_pedido
            }), 201

        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({"error": str(e)}), 500


@app.route('/api/fabrica/ordenes', methods=['GET', 'POST'])
@login_required
def gestion_ordenes_fabrica():
    if request.method == 'GET':
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        rows = cursor.execute("""
            SELECT o.id, o.numero_orden, o.estado, o.fecha_creacion, o.fecha_actualizacion,
                   c.nombre AS cliente, o.kg_total, o.observaciones,
                   COALESCE(GROUP_CONCAT(d.calibre || ' / ' || d.ancho_cm || 'x' || d.largo_cm || ' cm / ' || d.cantidad_piezas || ' pcs', ' | '), '') AS detalles
            FROM ordenes_figurado o
            JOIN clientes c ON c.id = o.id_cliente
            LEFT JOIN detalles_fleje d ON d.id_orden = o.id
            GROUP BY o.id
            ORDER BY CASE o.estado
               WHEN 'En Cola' THEN 1
               WHEN 'En Figurado' THEN 2
               WHEN 'Completado' THEN 3
               ELSE 4
            END, o.id DESC
        """).fetchall()
        conn.close()
        return jsonify([
            {
               'id': r[0],
               'numero_orden': r[1],
               'estado': r[2],
               'fecha_creacion': r[3],
               'fecha_actualizacion': r[4],
               'cliente': r[5],
               'kg_total': r[6],
               'observaciones': r[7] or '',
               'detalles': r[8] or ''
            } for r in rows
        ])

    data = request.json or {}
    id_cliente = int(data.get('id_cliente')) if str(data.get('id_cliente') or '').strip() else None
    items = data.get('items') or []
    observaciones = str(data.get('observaciones') or '').strip()
    if not id_cliente:
        return jsonify({'error': 'Debe seleccionar un cliente para la orden'}), 400
    if not items:
        return jsonify({'error': 'Debe agregar al menos un fleje para la orden'}), 400

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM clientes WHERE id = ?', (id_cliente,))
    if cursor.fetchone() is None:
        conn.close()
        return jsonify({'error': 'Cliente no encontrado'}), 400

    numero_orden = f"FL-{datetime.now().strftime('%Y%m%d')}-{int(datetime.now().timestamp()) % 100000}"
    total_kg = 0.0
    detalles_guardados = []

    for item in items:
        calibre = str(item.get('calibre') or '').strip()
        ancho_cm = float(item.get('ancho_cm') or item.get('ancho') or 0)
        largo_cm = float(item.get('largo_cm') or item.get('largo') or 0)
        gancho_cm = float(item.get('largo_gancho_cm') or item.get('gancho_cm') or item.get('largo_gancho') or 0)
        cantidad_piezas = int(item.get('cantidad_piezas') or item.get('cantidad') or 1)

        if not calibre or ancho_cm <= 0 or largo_cm <= 0 or cantidad_piezas <= 0:
            conn.close()
            return jsonify({'error': 'Cada fleje debe incluir calibre, ancho, largo y cantidad válidos'}), 400

        calculo = _calcular_consumo_fleje(ancho_cm, largo_cm, gancho_cm, cantidad_piezas, calibre)
        if calculo['consumo_kg'] <= 0:
            conn.close()
            return jsonify({'error': f'No se pudo calcular el consumo del fleje {calibre}'}), 400

        row_stock = cursor.execute(
            'SELECT id, stock_kg FROM inventario_hierro WHERE calibre = ? ORDER BY id LIMIT 1',
            (calibre,)
        ).fetchone()
        if not row_stock:
            conn.close()
            return jsonify({'error': f'No existe inventario de hierro para el calibre {calibre}. Registre el material antes de crear la orden.'}), 400
        if row_stock[1] < calculo['consumo_kg']:
            conn.close()
            return jsonify({'error': f'Inventario insuficiente para el calibre {calibre}. Disponible: {row_stock[1]} kg'}), 400

        total_kg += calculo['consumo_kg']
        detalles_guardados.append({
            'calibre': calibre,
            'ancho_cm': ancho_cm,
            'largo_cm': largo_cm,
            'largo_gancho_cm': gancho_cm,
            'cantidad_piezas': cantidad_piezas,
            'metros_lineales': calculo['metros_lineales'],
            'consumo_kg': calculo['consumo_kg'],
            'stock_id': row_stock[0]
        })

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        INSERT INTO ordenes_figurado (id_venta, id_cliente, numero_orden, estado, fecha_creacion, fecha_actualizacion, observaciones, kg_total)
        VALUES (?, ?, ?, 'En Cola', ?, ?, ?, ?)
    """, (None, id_cliente, numero_orden, ahora, ahora, observaciones or 'Orden creada desde el panel de ventas', round(total_kg, 4)))
    id_orden = cursor.lastrowid

    for detalle in detalles_guardados:
        cursor.execute("""
            INSERT INTO detalles_fleje (id_orden, calibre, ancho_cm, largo_cm, largo_gancho_cm, cantidad_piezas, metros_lineales, consumo_kg, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'En Cola')
        """, (
            id_orden,
            detalle['calibre'],
            detalle['ancho_cm'],
            detalle['largo_cm'],
            detalle['largo_gancho_cm'],
            detalle['cantidad_piezas'],
            detalle['metros_lineales'],
            detalle['consumo_kg']
        ))
        cursor.execute(
            'UPDATE inventario_hierro SET stock_kg = stock_kg - ?, ultimo_update = ? WHERE id = ?',
            (detalle['consumo_kg'], ahora, detalle['stock_id'])
        )

    registrar_auditoria(conn, 'crear', 'orden_figurado', id_orden, f'Orden #{numero_orden} creada manualmente desde ventas. Consumo total: {round(total_kg, 4)} kg')
    conn.commit()
    conn.close()
    return jsonify({
        'mensaje': f'Orden #{numero_orden} creada en fábrica',
        'id_orden': id_orden,
        'numero_orden': numero_orden,
        'kg_total': round(total_kg, 4),
        'estado': 'En Cola'
    }), 201


@app.route('/api/fabrica/inventario-hierro', methods=['GET', 'POST'])
@login_required
def gestion_inventario_hierro():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        rows = cursor.execute(
            "SELECT calibre, diametro_mm, stock_kg, ultimo_update FROM inventario_hierro ORDER BY calibre"
        ).fetchall()
        conn.close()
        return jsonify([
            {
                'calibre': r[0],
                'diametro_mm': r[1],
                'stock_kg': r[2],
                'ultimo_update': r[3]
            }
            for r in rows
        ])

    data = request.json or {}
    calibre = str(data.get('calibre') or '').strip()
    try:
        stock_kg = float(data.get('stock_kg') or 0)
    except (ValueError, TypeError):
        stock_kg = 0.0
    observacion = str(data.get('observacion') or '').strip()

    if not calibre:
        conn.close()
        return jsonify({'error': 'Debe indicar el calibre del hierro'}), 400
    if stock_kg < 0:
        conn.close()
        return jsonify({'error': 'El stock no puede ser negativo'}), 400

    diametro_mm = _parse_calibre_mm(calibre)
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    existing = cursor.execute('SELECT id FROM inventario_hierro WHERE calibre = ? ORDER BY id LIMIT 1', (calibre,)).fetchone()

    if existing:
        cursor.execute(
            "UPDATE inventario_hierro SET diametro_mm = ?, stock_kg = ?, ultimo_update = ? WHERE id = ?",
            (diametro_mm, stock_kg, ahora, existing[0])
        )
        mensaje = f'Inventario de {calibre} actualizado correctamente'
    else:
        cursor.execute(
            "INSERT INTO inventario_hierro (calibre, diametro_mm, stock_kg, ultimo_update) VALUES (?, ?, ?, ?)",
            (calibre, diametro_mm, stock_kg, ahora)
        )
        mensaje = f'Inventario de {calibre} registrado correctamente'

    registrar_auditoria(conn, 'guardar', 'inventario_hierro', calibre, f'{mensaje}. Observación: {observacion or "Sin observación"}')
    conn.commit()
    conn.close()
    return jsonify({'mensaje': mensaje}), 200 if existing else 201


@app.route('/api/fabrica/ordenes/<int:id_orden>/estado', methods=['PUT'])
@login_required
def actualizar_estado_fabrica(id_orden):
    data = request.json or {}
    nuevo_estado = str(data.get('estado') or '').strip()
    estados_validos = ('En Cola', 'En Figurado', 'Completado')
    if nuevo_estado not in estados_validos:
        return jsonify({"error": "Estado inválido. Debe ser En Cola, En Figurado o Completado"}), 400

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    orden = cursor.execute("""
        SELECT o.id, o.numero_orden, o.estado, COALESCE(c.nombre, 'Cliente no registrado') AS cliente
        FROM ordenes_figurado o
        LEFT JOIN clientes c ON c.id = o.id_cliente
        WHERE o.id = ?
    """, (id_orden,)).fetchone()
    if not orden:
        conn.close()
        return jsonify({"error": "Orden no encontrada"}), 404

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "UPDATE ordenes_figurado SET estado = ?, fecha_actualizacion = ?, fecha_entrega = ? WHERE id = ?",
        (nuevo_estado, ahora, ahora if nuevo_estado == 'Completado' else None, id_orden)
    )
    cursor.execute(
        "UPDATE detalles_fleje SET estado = ? WHERE id_orden = ?",
        (nuevo_estado, id_orden)
    )

    if nuevo_estado == 'Completado' and orden['estado'] != 'Completado':
        mensaje = f"✅ Los flejes de la orden {orden['numero_orden']} ({orden['cliente']}) ya están listos"
        cursor.execute("""
            INSERT INTO notificaciones_flejes (id_orden, numero_orden, cliente, mensaje, fecha)
            VALUES (?, ?, ?, ?, ?)
        """, (id_orden, orden['numero_orden'], orden['cliente'], mensaje, ahora))

    registrar_auditoria(conn, 'estado_orden', 'orden_figurado', id_orden, f'Estado -> {nuevo_estado}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Orden actualizada a '{nuevo_estado}'", "estado": nuevo_estado})


@app.route('/api/fabrica/notificaciones', methods=['GET'])
@login_required
def api_fabrica_notificaciones():
    """Notificaciones de flejes listos. destino=general marca leida global; destino=pedidos marca leida_pedidos."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, id_orden, numero_orden, cliente, mensaje, leida, leida_pedidos, fecha
        FROM notificaciones_flejes
        ORDER BY id DESC LIMIT 50
    """).fetchall()
    total_general = conn.execute("SELECT COUNT(*) FROM notificaciones_flejes WHERE leida = 0").fetchone()[0]
    total_pedidos = conn.execute("SELECT COUNT(*) FROM notificaciones_flejes WHERE leida_pedidos = 0").fetchone()[0]
    conn.close()
    return jsonify({
        "total_general": total_general,
        "total_pedidos": total_pedidos,
        "notificaciones": [dict(r) for r in rows]
    })


@app.route('/api/fabrica/notificaciones/marcar_leidas', methods=['POST'])
@login_required
def api_fabrica_notificaciones_marcar():
    data = request.get_json(force=True) or {}
    destino = str(data.get('destino') or 'general').strip()
    columna = 'leida_pedidos' if destino == 'pedidos' else 'leida'
    conn = sqlite3.connect(DB_NAME)
    conn.execute(f"UPDATE notificaciones_flejes SET {columna} = 1 WHERE {columna} = 0")
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Notificaciones marcadas como leídas"})


@app.route('/api/ventas/despachos', methods=['GET'])
@login_required
def get_despachos():
    """Devuelve las ventas marcadas 'para_llevar' ordenadas por numero_pedido."""
    conn = sqlite3.connect(DB_NAME)
    estado_filtro = request.args.get('estado', '')

    query = """
        SELECT v.id, v.numero_pedido, v.fecha_dia, v.hora,
               c.nombre, c.telefono, c.cedula_nit,
               COALESCE(v.direccion_cliente, c.direccion, '') AS direccion,
               v.total_venta, v.tipo_pago, v.anulada,
               GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') AS productos,
               COALESCE(v.estado_despacho, 'pendiente_preparar')
        FROM ventas v
        JOIN clientes c ON c.id = v.id_cliente
        LEFT JOIN detalle_ventas dv ON dv.id_venta = v.id
        LEFT JOIN productos p ON p.id = dv.id_producto
        WHERE v.tipo_entrega = 'para_llevar' AND v.anulada = 0
    """
    params = []
    if estado_filtro:
        query += " AND COALESCE(v.estado_despacho, 'pendiente_preparar') = ?"
        params.append(estado_filtro)
    query += " GROUP BY v.id ORDER BY v.numero_pedido ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([{
        "id_venta":        r[0],
        "numero_pedido":   r[1],
        "fecha_dia":       r[2],
        "hora":            r[3],
        "cliente":         r[4],
        "telefono":        r[5],
        "cedula_nit":      r[6],
        "direccion":       r[7] or "",
        "total_venta":     r[8],
        "tipo_pago":       r[9],
        "anulada":         bool(r[10]),
        "productos":       r[11] or "",
        "estado_despacho": r[12]
    } for r in rows])


@app.route('/api/ventas/<int:id_venta>/estado_despacho', methods=['PUT'])
@login_required
def cambiar_estado_despacho(id_venta):
    """Cambia el estado_despacho de una venta para_llevar."""
    ESTADOS_DESPACHO = ('pendiente_preparar', 'preparando', 'listo', 'entregado')
    data = request.json or {}
    nuevo = (data.get('estado_despacho') or '').strip()
    if nuevo not in ESTADOS_DESPACHO:
        return jsonify({"error": f"Estado inválido. Válidos: {', '.join(ESTADOS_DESPACHO)}"}), 400

    conn = sqlite3.connect(DB_NAME)
    venta = conn.execute(
        "SELECT id, tipo_entrega FROM ventas WHERE id = ?", (id_venta,)
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[1] != 'para_llevar':
        conn.close()
        return jsonify({"error": "Esta venta no es de tipo para_llevar"}), 400

    conn.execute("UPDATE ventas SET estado_despacho = ? WHERE id = ?", (nuevo, id_venta))
    registrar_auditoria(conn, 'estado_despacho', 'venta', id_venta,
                        f'Estado despacho → {nuevo} por {session["usuario"]}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Estado actualizado a '{nuevo}'"})


@app.route('/api/ventas/<int:id_venta>', methods=['GET'])
@login_required
def get_factura_detalle(id_venta):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
         SELECT v.id, v.fecha_dia, v.hora, c.nombre, c.cedula_nit, c.telefono,
             v.total_venta, v.tipo_pago, v.id_cliente,
             COALESCE(v.direccion_cliente, c.direccion, '')
        FROM ventas v
        JOIN clientes c ON v.id_cliente = c.id
        WHERE v.id = ?
    """, (id_venta,))
    venta = cursor.fetchone()

    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404

    cursor.execute("""
        SELECT p.nombre, p.dimensiones, dv.cantidad, dv.precio_unitario, dv.subtotal
        FROM detalle_ventas dv
        JOIN productos p ON dv.id_producto = p.id
        WHERE dv.id_venta = ?
    """, (id_venta,))
    detalles = cursor.fetchall()

    conn.close()

    return jsonify({
        "id": venta[0],
        "fecha_dia": venta[1],
        "hora": venta[2],
        "cliente": venta[3],
        "cedula_nit": venta[4],
        "telefono": venta[5],
        "total_venta": venta[6],
        "tipo_pago": venta[7],
        "id_cliente": venta[8],
        "direccion": venta[9] or "",
        "items": [{
            "nombre": d[0] + (f" ({d[1]})" if d[1] else ""),
            "cantidad": d[2],
            "precio_unitario": d[3],
            "subtotal": d[4]
        } for d in detalles]
    })


@app.route('/api/ventas/<int:id_venta>/anular', methods=['PUT'])
@admin_required
def anular_venta(id_venta):
    data = request.json or {}
    motivo = str(data.get('motivo', '')).strip()
    if not motivo:
        return jsonify({"error": "Debe indicar el motivo de la anulación"}), 400
    conn = sqlite3.connect(DB_NAME)
    venta = conn.execute("SELECT total_venta, saldo_pendiente, anulada FROM ventas WHERE id = ?", (id_venta,)).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[2]:
        conn.close()
        return jsonify({"error": "La venta ya está anulada"}), 400
    if venta[1] > 0 and venta[1] != venta[0]:
        conn.close()
        return jsonify({"error": "No se puede anular una venta de crédito que ya tiene abonos"}), 400
    detalles = conn.execute("SELECT id_producto, cantidad FROM detalle_ventas WHERE id_venta = ?", (id_venta,)).fetchall()
    for id_producto, cantidad in detalles:
        conn.execute("UPDATE productos SET stock_actual = stock_actual + ? WHERE id = ?", (cantidad, id_producto))
        conn.execute("INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) VALUES (?, 'entrada', ?, ?, ?, ?)", (id_producto, cantidad, f'Anulación venta #{id_venta}', session['usuario'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.execute("UPDATE ventas SET anulada = 1, motivo_anulacion = ?, saldo_pendiente = 0 WHERE id = ?", (motivo, id_venta))
    registrar_auditoria(conn, 'anular', 'venta', id_venta, motivo)
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Venta anulada y stock restaurado"})

@app.route('/api/ventas/<int:id_venta>/cliente', methods=['PUT'])
@login_required
def editar_cliente_factura(id_venta):
    data = request.json
    nombre = data.get('nombre')
    cedula_nit = data.get('cedula_nit')
    telefono = data.get('telefono')
    direccion = data.get('direccion', '')

    if not nombre:
        return jsonify({"error": "El nombre es obligatorio"}), 400

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT id_cliente FROM ventas WHERE id = ?", (id_venta,))
    res = cursor.fetchone()

    if not res:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404

    id_cliente = res[0]

    cursor.execute("""
        UPDATE clientes
        SET nombre = ?, cedula_nit = ?, telefono = ?, direccion = ?
        WHERE id = ?
    """, (nombre, cedula_nit, telefono, direccion, id_cliente))

    cursor.execute("""
        UPDATE ventas
        SET direccion_cliente = ?
        WHERE id = ?
    """, (direccion, id_venta))

    conn.commit()
    conn.close()

    return jsonify({"mensaje": "Datos del cliente y dirección actualizados correctamente"}), 200

# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO DE PEDIDOS
# ══════════════════════════════════════════════════════════════════════════════

ESTADOS_VALIDOS = ('pendiente', 'alistando', 'listo', 'en_camino', 'entregado', 'cancelado')

TRANSICIONES = {
    'pendiente':  {'admin': ['alistando', 'cancelado'], 'empleado': ['cancelado'], 'bodega': ['alistando', 'cancelado']},
    'alistando':  {'admin': ['listo', 'pendiente', 'cancelado'], 'bodega': ['listo', 'pendiente', 'cancelado']},
    'listo':      {'admin': ['en_camino', 'cancelado'], 'bodega': ['en_camino'], 'motocarguero': ['en_camino']},
    'en_camino':  {'admin': ['entregado', 'listo'], 'motocarguero': ['entregado']},
    'entregado':  {'admin': ['en_camino']},
    'cancelado':  {'admin': ['pendiente']},
}

def _pedido_row_to_dict(r):
    return {
        "id": r[0], "id_cliente": r[1], "nombre_cliente": r[2],
        "telefono_cliente": r[3], "direccion_entrega": r[4] or "",
        "observaciones": r[5] or "", "estado": r[6],
        "usuario_vendedor": r[7], "usuario_bodega": r[8] or "",
        "id_motocarguero": r[9], "nombre_motocarguero": r[10] or "",
        "fecha_creacion": r[11], "fecha_actualizacion": r[12] or "",
        "total_pedido": r[13] or 0
    }

_Q_PEDIDOS = """
    SELECT p.id, p.id_cliente, c.nombre, c.telefono,
           p.direccion_entrega, p.observaciones, p.estado,
           p.usuario_vendedor, p.usuario_bodega,
           p.id_motocarguero, um.usuario,
           p.fecha_creacion, p.fecha_actualizacion,
           COALESCE(SUM(dp.subtotal), 0)
    FROM pedidos p
    JOIN clientes c ON c.id = p.id_cliente
    LEFT JOIN usuarios um ON um.id = p.id_motocarguero
    LEFT JOIN detalle_pedidos dp ON dp.id_pedido = p.id
"""


@app.route('/api/pedidos', methods=['GET', 'POST'])
@login_required
def handle_pedidos():
    conn = sqlite3.connect(DB_NAME)
    rol = session.get('rol')
    usuario = session.get('usuario')

    if request.method == 'GET':
        estado_filtro = request.args.get('estado', '')
        desde = request.args.get('desde', '')
        where_clauses = []
        params = []

        if rol == 'motocarguero':
            row_id = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
            if row_id:
                where_clauses.append("p.id_motocarguero = ?")
                params.append(row_id[0])
            else:
                conn.close()
                return jsonify([])

        if estado_filtro:
            where_clauses.append("p.estado = ?")
            params.append(estado_filtro)
        if desde:
            where_clauses.append("p.fecha_creacion >= ?")
            params.append(desde)

        sql = _Q_PEDIDOS
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " GROUP BY p.id ORDER BY p.fecha_creacion DESC LIMIT 200"

        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify([_pedido_row_to_dict(r) for r in rows])

    # POST: crear pedido
    if rol not in ('admin', 'empleado', 'bodega'):
        conn.close()
        return jsonify({"error": "Solo vendedores o administradores pueden crear pedidos"}), 403

    data = request.json or {}
    id_cliente = data.get('id_cliente')
    items = data.get('items', [])

    if not id_cliente or not items:
        conn.close()
        return jsonify({"error": "Se requiere id_cliente y al menos un producto"}), 400

    if not conn.execute("SELECT id FROM clientes WHERE id = ?", (id_cliente,)).fetchone():
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pedidos (id_cliente, direccion_entrega, observaciones, estado,
                             usuario_vendedor, fecha_creacion, fecha_actualizacion)
        VALUES (?, ?, ?, 'pendiente', ?, ?, ?)
    """, (id_cliente, data.get('direccion_entrega', '').strip(),
          data.get('observaciones', '').strip(), usuario, ahora, ahora))
    id_pedido = cursor.lastrowid

    total = 0
    for item in items:
        prod = conn.execute("SELECT precio_venta FROM productos WHERE id = ?", (item['id_producto'],)).fetchone()
        if not prod:
            conn.rollback(); conn.close()
            return jsonify({"error": f"Producto ID {item['id_producto']} no encontrado"}), 404
        cantidad = int(item['cantidad'])
        precio = float(prod[0])
        subtotal = round(cantidad * precio, 2)
        total += subtotal
        cursor.execute("""
            INSERT INTO detalle_pedidos (id_pedido, id_producto, cantidad, precio_unitario, subtotal)
            VALUES (?, ?, ?, ?, ?)
        """, (id_pedido, item['id_producto'], cantidad, precio, subtotal))

    registrar_auditoria(conn, 'crear', 'pedido', id_pedido,
                        f'Pedido creado por {usuario} — {len(items)} productos — Total ${total:,.0f}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Pedido creado con exito", "id_pedido": id_pedido, "total": total}), 201


@app.route('/api/pedidos/<int:id_pedido>', methods=['GET'])
@login_required
def get_pedido(id_pedido):
    conn = sqlite3.connect(DB_NAME)
    row = conn.execute(_Q_PEDIDOS + " WHERE p.id = ? GROUP BY p.id", (id_pedido,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Pedido no encontrado"}), 404
    pedido = _pedido_row_to_dict(row)
    items = conn.execute("""
        SELECT dp.id, pr.nombre, pr.dimensiones, pr.categoria,
               dp.cantidad, dp.precio_unitario, dp.subtotal,
               pr.stock_actual, pr.codigo_barras
        FROM detalle_pedidos dp
        JOIN productos pr ON pr.id = dp.id_producto
        WHERE dp.id_pedido = ?
    """, (id_pedido,)).fetchall()
    conn.close()
    pedido['items'] = [{
        "id": i[0], "nombre": i[1], "dimensiones": i[2] or "",
        "categoria": i[3] or "", "cantidad": i[4],
        "precio_unitario": i[5], "subtotal": i[6],
        "stock_actual": i[7], "codigo_barras": i[8] or ""
    } for i in items]
    return jsonify(pedido)


@app.route('/api/pedidos/<int:id_pedido>/estado', methods=['PUT'])
@login_required
def cambiar_estado_pedido(id_pedido):
    rol = session.get('rol')
    usuario = session.get('usuario')
    data = request.json or {}
    nuevo_estado = (data.get('estado') or '').strip().lower()

    if nuevo_estado not in ESTADOS_VALIDOS:
        return jsonify({"error": f"Estado invalido. Validos: {', '.join(ESTADOS_VALIDOS)}"}), 400

    conn = sqlite3.connect(DB_NAME)
    pedido = conn.execute("SELECT estado FROM pedidos WHERE id = ?", (id_pedido,)).fetchone()
    if not pedido:
        conn.close()
        return jsonify({"error": "Pedido no encontrado"}), 404

    estado_actual = pedido[0]
    permitidos = TRANSICIONES.get(estado_actual, {})
    if rol not in permitidos or nuevo_estado not in permitidos[rol]:
        conn.close()
        return jsonify({"error": f"El rol '{rol}' no puede cambiar de '{estado_actual}' a '{nuevo_estado}'"}), 403

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    updates = ["estado = ?", "fecha_actualizacion = ?"]
    params = [nuevo_estado, ahora]

    if rol == 'bodega' and estado_actual == 'pendiente':
        updates.append("usuario_bodega = ?")
        params.append(usuario)

    if data.get('id_motocarguero'):
        id_moto = int(data['id_motocarguero'])
        moto = conn.execute("SELECT id FROM usuarios WHERE id = ? AND rol = 'motocarguero'", (id_moto,)).fetchone()
        if not moto:
            conn.close()
            return jsonify({"error": "Motocarguero no encontrado"}), 404
        updates.append("id_motocarguero = ?")
        params.append(id_moto)

    params.append(id_pedido)
    conn.execute(f"UPDATE pedidos SET {', '.join(updates)} WHERE id = ?", params)
    registrar_auditoria(conn, 'estado_pedido', 'pedido', id_pedido,
                        f'{estado_actual} -> {nuevo_estado} por {usuario}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Pedido actualizado a '{nuevo_estado}'", "estado": nuevo_estado})


@app.route('/api/pedidos/notificaciones', methods=['GET'])
@login_required
def notificaciones_pedidos():
    rol = session.get('rol')
    usuario = session.get('usuario')
    conn = sqlite3.connect(DB_NAME)

    if rol in ('admin', 'bodega'):
        total = conn.execute("SELECT COUNT(*) FROM pedidos WHERE estado = 'pendiente'").fetchone()[0]
        resumen = f"{total} pedido(s) pendiente(s) por alistar"
    elif rol == 'motocarguero':
        row_id = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        total = 0
        if row_id:
            total = conn.execute(
                "SELECT COUNT(*) FROM pedidos WHERE estado = 'listo' AND id_motocarguero = ?",
                (row_id[0],)
            ).fetchone()[0]
        resumen = f"{total} pedido(s) listo(s) para recoger"
    else:
        total = conn.execute(
            "SELECT COUNT(*) FROM pedidos WHERE estado NOT IN ('entregado','cancelado') AND usuario_vendedor = ?",
            (usuario,)
        ).fetchone()[0]
        resumen = f"{total} pedido(s) activo(s)"

    conn.close()
    return jsonify({"total": total, "resumen": resumen})


@app.route('/api/usuarios/motocargueros', methods=['GET'])
@login_required
def get_motocargueros():
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute(
        "SELECT id, usuario, nombre_completo FROM usuarios WHERE rol = 'motocarguero' ORDER BY usuario"
    ).fetchall()
    conn.close()
    return jsonify([{"id": r[0], "usuario": r[1], "nombre": r[2] or r[1]} for r in rows])


# ── MÓDULO ALQUILER DE MAQUINARIA ───────────────────────────────────────────
def _listar_equipos_alquiler(conn):
    try:
        conn.execute("SELECT 1 FROM equipos LIMIT 1")
        tabla = 'equipos'
    except sqlite3.Error:
        tabla = 'equipos_alquiler'
    rows = conn.execute(f"""
        SELECT *
        FROM {tabla}
        WHERE activo = 1
        ORDER BY nombre ASC
    """).fetchall()
    return [dict(r) if isinstance(r, sqlite3.Row) else {
        'id': r[0], 'codigo_interno': r[1], 'nombre': r[2], 'categoria': r[3], 'marca': r[4],
        'modelo': r[5], 'numero_serie': r[6], 'estado': r[7], 'tipo_tarifa': r[8], 'tarifa': r[9],
        'tarifa_hora': r[10], 'tarifa_turno': r[11], 'tarifa_bulto': r[12], 'medidas': r[13],
        'especificaciones': r[14], 'cantidad_disponible': r[15], 'cantidad_total': r[16],
        'fecha_compra': r[17], 'fecha_ultimo_mantenimiento': r[18], 'observaciones': r[19],
        'activo': r[20], 'fecha_registro': r[21], 'fecha_actualizacion': r[22]
    } for r in rows]


@app.route('/api/equipos', methods=['GET', 'POST'])
@login_required
def api_equipos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    if request.method == 'GET':
        rows = _listar_equipos_alquiler(conn)
        conn.close()
        return jsonify(rows)

    data = request.get_json(force=True) or {}
    equipo = _normalizar_datos_equipo(data)
    if not equipo['nombre'] or not equipo['codigo_interno']:
        conn.close()
        return jsonify({"error": "Nombre y código interno del equipo son obligatorios"}), 400

    try:
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = conn.cursor()

        row_equipo_alquiler = cursor.execute(
            "SELECT id FROM equipos_alquiler WHERE codigo_interno = ?",
            (equipo['codigo_interno'],)
        ).fetchone()
        if row_equipo_alquiler:
            id_equipo = row_equipo_alquiler[0]
            cursor.execute("""
                UPDATE equipos_alquiler
                SET nombre = ?, categoria = ?, marca = ?, modelo = ?, numero_serie = ?,
                    estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?, tarifa_turno = ?, tarifa_bulto = ?,
                    medidas = ?, especificaciones = ?, cantidad_disponible = ?, cantidad_total = ?,
                    fecha_compra = ?, fecha_ultimo_mantenimiento = ?, observaciones = ?, activo = ?,
                    fecha_actualizacion = ?
                WHERE id = ?
            """, (
                equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'], equipo['numero_serie'],
                equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'], equipo['tarifa_hora'],
                equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'], equipo['especificaciones'],
                equipo['cantidad_disponible'], equipo['cantidad_total'], equipo['fecha_compra'],
                equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'], equipo['activo'],
                ahora, id_equipo
            ))
        else:
            cursor.execute("""
                INSERT INTO equipos_alquiler (
                    codigo_interno, nombre, categoria, marca, modelo, numero_serie,
                    estado, tipo_tarifa, tarifa, tarifa_hora, tarifa_turno, tarifa_bulto,
                    medidas, especificaciones, cantidad_disponible, cantidad_total,
                    fecha_compra, fecha_ultimo_mantenimiento, observaciones, activo,
                    fecha_registro, fecha_actualizacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'],
                equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'],
                equipo['tarifa_hora'], equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'],
                equipo['especificaciones'], equipo['cantidad_disponible'], equipo['cantidad_total'],
                equipo['fecha_compra'], equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'],
                equipo['activo'], ahora, ahora
            ))
            id_equipo = cursor.lastrowid

        row_equipo = cursor.execute(
            "SELECT id FROM equipos WHERE codigo_interno = ?",
            (equipo['codigo_interno'],)
        ).fetchone()
        if row_equipo:
            cursor.execute("""
                UPDATE equipos
                SET nombre = ?, categoria = ?, marca = ?, modelo = ?, numero_serie = ?,
                    estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?, tarifa_turno = ?, tarifa_bulto = ?,
                    medidas = ?, especificaciones = ?, cantidad_disponible = ?, cantidad_total = ?,
                    fecha_compra = ?, fecha_ultimo_mantenimiento = ?, observaciones = ?, activo = ?,
                    fecha_registro = ?, fecha_actualizacion = ?
                WHERE id = ?
            """, (
                equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'], equipo['numero_serie'],
                equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'], equipo['tarifa_hora'],
                equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'], equipo['especificaciones'],
                equipo['cantidad_disponible'], equipo['cantidad_total'], equipo['fecha_compra'],
                equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'], equipo['activo'],
                ahora, ahora, row_equipo[0]
            ))
        else:
            cursor.execute("""
                INSERT INTO equipos (
                    id, codigo_interno, nombre, categoria, marca, modelo, numero_serie,
                    estado, tipo_tarifa, tarifa, tarifa_hora, tarifa_turno, tarifa_bulto,
                    medidas, especificaciones, cantidad_disponible, cantidad_total,
                    fecha_compra, fecha_ultimo_mantenimiento, observaciones, activo,
                    fecha_registro, fecha_actualizacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                id_equipo, equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'],
                equipo['modelo'], equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'],
                equipo['tarifa'], equipo['tarifa_hora'], equipo['tarifa_turno'], equipo['tarifa_bulto'],
                equipo['medidas'], equipo['especificaciones'], equipo['cantidad_disponible'],
                equipo['cantidad_total'], equipo['fecha_compra'], equipo['fecha_ultimo_mantenimiento'],
                equipo['observaciones'], equipo['activo'], ahora, ahora
            ))

        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Equipo registrado correctamente", "equipo": equipo}), 201
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Ya existe un equipo con ese código interno"}), 400


@app.route('/api/equipos/<int:id_equipo>', methods=['PUT', 'DELETE'])
@login_required
def api_equipo_por_id(id_equipo):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    if request.method == 'PUT':
        data = request.get_json(force=True) or {}
        equipo = _normalizar_datos_equipo(data)
        if not equipo['nombre'] or not equipo['codigo_interno']:
            conn.close()
            return jsonify({"error": "Nombre y código interno del equipo son obligatorios"}), 400

        try:
            conn.execute("""
                UPDATE equipos
                SET codigo_interno = ?, nombre = ?, categoria = ?, marca = ?, modelo = ?, numero_serie = ?,
                    estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?, tarifa_turno = ?, tarifa_bulto = ?,
                    medidas = ?, especificaciones = ?, cantidad_disponible = ?, cantidad_total = ?,
                    fecha_compra = ?, fecha_ultimo_mantenimiento = ?, observaciones = ?, fecha_actualizacion = ?
                WHERE id = ?
            """, (
                equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'],
                equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'],
                equipo['tarifa_hora'], equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'],
                equipo['especificaciones'], equipo['cantidad_disponible'], equipo['cantidad_total'],
                equipo['fecha_compra'], equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), id_equipo
            ))
            try:
                conn.execute("""
                    UPDATE equipos_alquiler
                    SET codigo_interno = ?, nombre = ?, categoria = ?, marca = ?, modelo = ?, numero_serie = ?,
                        estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?, tarifa_turno = ?, tarifa_bulto = ?,
                        medidas = ?, especificaciones = ?, cantidad_disponible = ?, cantidad_total = ?,
                        fecha_compra = ?, fecha_ultimo_mantenimiento = ?, observaciones = ?, fecha_actualizacion = ?
                    WHERE id = ?
                """, (
                    equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'],
                    equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'],
                    equipo['tarifa_hora'], equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'],
                    equipo['especificaciones'], equipo['cantidad_disponible'], equipo['cantidad_total'],
                    equipo['fecha_compra'], equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), id_equipo
                ))
            except sqlite3.Error:
                conn.execute("""
                    UPDATE equipos_alquiler
                    SET nombre = ?, categoria = ?, marca = ?, modelo = ?, numero_serie = ?,
                        estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?, tarifa_turno = ?, tarifa_bulto = ?,
                        medidas = ?, especificaciones = ?, cantidad_disponible = ?, cantidad_total = ?,
                        fecha_compra = ?, fecha_ultimo_mantenimiento = ?, observaciones = ?, fecha_actualizacion = ?
                    WHERE codigo_interno = ?
                """, (
                    equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'], equipo['numero_serie'],
                    equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'], equipo['tarifa_hora'],
                    equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'], equipo['especificaciones'],
                    equipo['cantidad_disponible'], equipo['cantidad_total'], equipo['fecha_compra'],
                    equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), equipo['codigo_interno']
                ))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Equipo actualizado correctamente"})
        except sqlite3.IntegrityError:
            conn.rollback()
            conn.close()
            return jsonify({"error": "Ya existe un equipo con ese código interno"}), 400

    conn.execute("UPDATE equipos SET activo = 0 WHERE id = ?", (id_equipo,))
    conn.execute("UPDATE equipos_alquiler SET activo = 0 WHERE id = ?", (id_equipo,))
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Equipo eliminado correctamente"})


@app.route('/api/alquiler/equipos', methods=['GET', 'POST'])
@login_required
def api_alquiler_equipos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    if request.method == 'GET':
        rows = _listar_equipos_alquiler(conn)
        conn.close()
        return jsonify(rows)

    data = request.get_json(force=True) or {}
    equipo = _normalizar_datos_equipo(data)
    if not equipo['codigo_interno'] or not equipo['nombre']:
        conn.close()
        return jsonify({"error": "Código interno y nombre son obligatorios"}), 400

    try:
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("""
            INSERT INTO equipos_alquiler (
                codigo_interno, nombre, categoria, marca, modelo, numero_serie,
                estado, tipo_tarifa, tarifa, tarifa_hora, tarifa_turno, tarifa_bulto,
                medidas, especificaciones, cantidad_disponible, cantidad_total,
                fecha_compra, fecha_ultimo_mantenimiento, observaciones, activo,
                fecha_registro, fecha_actualizacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'],
            equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'],
            equipo['tarifa_hora'], equipo['tarifa_turno'], equipo['tarifa_bulto'], equipo['medidas'],
            equipo['especificaciones'], equipo['cantidad_disponible'], equipo['cantidad_total'],
            equipo['fecha_compra'], equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'],
            equipo['activo'], ahora, ahora
        ))
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Equipo registrado correctamente"}), 201
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Ya existe un equipo con ese código interno"}), 400


@app.route('/api/alquileres', methods=['GET', 'POST'])
@login_required
def api_alquileres():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    if request.method == 'GET':
        rows = conn.execute("""
            SELECT a.*, c.nombre AS cliente_nombre
            FROM alquileres a
            JOIN clientes c ON c.id = a.id_cliente
            ORDER BY a.id DESC
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    data = request.get_json(force=True) or {}
    id_cliente = data.get('id_cliente')
    equipos = data.get('equipos') or []
    if not id_cliente:
        conn.close()
        return jsonify({"error": "Debe seleccionar un cliente"}), 400
    cliente = conn.execute("SELECT id FROM clientes WHERE id = ?", (int(id_cliente),)).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"error": "El cliente seleccionado no existe en el sistema"}), 400
    if not equipos:
        conn.close()
        return jsonify({"error": "Debe seleccionar al menos un equipo"}), 400

    fecha_salida = str(data.get('fecha_salida') or '').strip()
    fecha_pactada = str(data.get('fecha_devolucion_pactada') or '').strip()
    if not fecha_salida or not fecha_pactada:
        conn.close()
        return jsonify({"error": "Debe indicar la fecha de salida y la fecha pactada de devolución"}), 400

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO alquileres (
                id_cliente, id_usuario, fecha_salida, fecha_devolucion_pactada,
                estado, valor_deposito, notas_salida, fecha_registro
            ) VALUES (?, ?, ?, ?, 'activo', ?, ?, ?)
        """, (
            int(id_cliente),
            int(session.get('id_usuario') or 1),
            fecha_salida,
            fecha_pactada,
            float(data.get('valor_deposito') or 0),
            str(data.get('notas_salida') or '').strip(),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        id_alquiler = cursor.lastrowid

        subtotal_total = 0
        for item in equipos:
            id_equipo = int(item.get('id_equipo'))
            cantidad = max(1, int(item.get('cantidad') or 1))
            equipo = conn.execute("SELECT * FROM equipos_alquiler WHERE id = ?", (id_equipo,)).fetchone()
            if not equipo:
                conn.close()
                return jsonify({"error": f"Equipo con id {id_equipo} no existe"}), 400

            tarifa_tipo = str(item.get('tarifa_tipo') or equipo['tipo_tarifa'] or 'dia').strip()
            tarifa_valor = float(item.get('tarifa_valor') or equipo['tarifa'] or 0)
            if tarifa_tipo == 'hora':
                tarifa_valor = float(item.get('tarifa_valor') or equipo['tarifa_hora'] or 0)
            if tarifa_tipo == 'turno':
                tarifa_valor = float(item.get('tarifa_valor') or equipo['tarifa_turno'] or 0)
            if tarifa_tipo == 'bulto':
                tarifa_valor = float(item.get('tarifa_valor') or equipo['tarifa_bulto'] or 0)

            subtotal = calcular_total_alquiler(fecha_salida, fecha_pactada, tarifa_tipo, tarifa_valor, cantidad)
            subtotal_total += subtotal

            cursor.execute("""
                INSERT INTO detalle_alquiler (
                    id_alquiler, id_equipo, cantidad, tarifa_tipo, tarifa_valor,
                    subtotal, estado_salida, observaciones
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                id_alquiler,
                id_equipo,
                cantidad,
                tarifa_tipo,
                tarifa_valor,
                subtotal,
                str(item.get('estado_salida') or 'bueno').strip() or 'bueno',
                str(item.get('observaciones') or '').strip()
            ))

            cursor.execute("UPDATE equipos_alquiler SET estado = 'En Alquiler' WHERE id = ?", (id_equipo,))

        cursor.execute("UPDATE alquileres SET subtotal = ?, total_final = ? WHERE id = ?", (subtotal_total, subtotal_total, id_alquiler))
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Alquiler registrado correctamente", "id_alquiler": id_alquiler}), 201
    except ValueError:
        conn.close()
        return jsonify({"error": "Los valores enviados no son válidos"}), 400


@app.route('/api/alquileres/activos', methods=['GET'])
@login_required
def api_alquileres_activos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT a.id, a.estado, a.fecha_salida, a.fecha_devolucion_pactada,
               COALESCE(c.nombre, 'Cliente no registrado') AS cliente,
               COALESCE(c.telefono, '') AS telefono,
               COALESCE(e.nombre, 'Equipo no registrado') AS equipo,
               COALESCE(e.codigo_interno, '') AS codigo_interno,
               a.valor_deposito
        FROM alquileres a
        LEFT JOIN clientes c ON c.id = a.id_cliente
        LEFT JOIN detalle_alquiler d ON d.id_alquiler = a.id
        LEFT JOIN equipos_alquiler e ON e.id = d.id_equipo
        WHERE a.estado = 'activo'
        GROUP BY a.id
        ORDER BY a.fecha_devolucion_pactada ASC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/alquiler/alertas', methods=['GET'])
@login_required
def api_alquiler_alertas():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT a.id, a.fecha_salida, a.fecha_devolucion_pactada,
               COALESCE(c.nombre, 'Cliente no registrado') AS cliente,
               COALESCE(e.nombre, 'Equipo no registrado') AS equipo,
               COALESCE(e.codigo_interno, '') AS codigo_interno
        FROM alquileres a
        LEFT JOIN clientes c ON c.id = a.id_cliente
        LEFT JOIN detalle_alquiler d ON d.id_alquiler = a.id
        LEFT JOIN equipos_alquiler e ON e.id = d.id_equipo
        WHERE a.estado = 'activo'
        ORDER BY a.fecha_devolucion_pactada ASC
    """).fetchall()
    conn.close()

    hoy = datetime.now()
    resultado = []
    for row in rows:
        fecha_limite = datetime.fromisoformat(str(row['fecha_devolucion_pactada']).replace('Z', '+00:00'))
        resultado.append({
            'id': row['id'],
            'cliente': row['cliente'],
            'equipo': row['equipo'],
            'codigo_interno': row['codigo_interno'],
            'fecha_salida': row['fecha_salida'],
            'fecha_devolucion_pactada': row['fecha_devolucion_pactada'],
            'vencido': fecha_limite < hoy
        })
    return jsonify(resultado)


@app.route('/api/alquileres/<int:id_alquiler>/devolver', methods=['POST'])
@login_required
def api_devolver_alquiler(id_alquiler):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    data = request.get_json(force=True) or {}

    alquiler = conn.execute("SELECT * FROM alquileres WHERE id = ?", (id_alquiler,)).fetchone()
    if not alquiler:
        conn.close()
        return jsonify({"error": "Alquiler no encontrado"}), 404

    fecha_real = str(data.get('fecha_devolucion_real') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')).strip()
    cargos_extra = float(data.get('cargos_extra') or 0)
    descuento = float(data.get('descuento') or 0)
    notas = str(data.get('notas_devolucion') or '').strip()

    detalle = conn.execute("SELECT * FROM detalle_alquiler WHERE id_alquiler = ?", (id_alquiler,)).fetchall()
    subtotal_total = 0
    cursor = conn.cursor()

    for item in detalle:
        tarifa_tipo = str(item['tarifa_tipo'] or 'dia').strip()
        tarifa_valor = float(item['tarifa_valor'] or 0)
        cantidad = max(1, int(item['cantidad'] or 1))
        subtotal = calcular_total_alquiler(alquiler['fecha_salida'], fecha_real, tarifa_tipo, tarifa_valor, cantidad)
        subtotal_total += subtotal

        cursor.execute(
            "UPDATE detalle_alquiler SET subtotal = ?, estado_retorno = ?, observaciones = ? WHERE id = ?",
            (subtotal, str(data.get('estado_retorno') or 'bueno').strip() or 'bueno', notas, item['id'])
        )
        cursor.execute("UPDATE equipos_alquiler SET estado = 'Disponible' WHERE id = ?", (item['id_equipo'],))

    total_final = subtotal_total + cargos_extra - descuento
    cursor.execute("""
        UPDATE alquileres
        SET fecha_devolucion_real = ?, notas_devolucion = ?, cargos_extra = ?,
            descuento = ?, subtotal = ?, total_final = ?, estado = 'devuelto'
        WHERE id = ?
    """, (fecha_real, notas, cargos_extra, descuento, subtotal_total, total_final, id_alquiler))

    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Devolución registrada correctamente", "total_final": round(total_final, 2)})


init_db()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
