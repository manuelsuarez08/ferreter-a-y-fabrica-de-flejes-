from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3
import os
import shutil
from datetime import datetime

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


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get('rol') != 'admin':
            return jsonify({'error': 'Acceso denegado. Solo el administrador puede realizar esta acción.'}), 403
        return view(*args, **kwargs)
    return wrapped_view


def registrar_auditoria(conn, accion, entidad, entidad_id=None, detalles=''):
    conn.execute("""
        INSERT INTO auditoria (usuario, accion, entidad, entidad_id, detalles, fecha)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session.get('usuario', 'sistema'), accion, entidad, entidad_id,
          detalles, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

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
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
                       ('admin', generate_password_hash('admin123'), 'admin'))

    # Cliente general por defecto
    cursor.execute("SELECT COUNT(*) FROM clientes WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO clientes (id, nombre, cedula_nit, telefono, direccion) VALUES (1, 'Cliente Mostrador (General)', '222222222222', '0000000000', 'Local')")

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
        user = request.form.get('usuario')
        clave = request.form.get('clave')

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, usuario, clave, rol FROM usuarios WHERE usuario = ?", (user,))
        row = cursor.fetchone()
        conn.close()

        password_valid = False
        if row:
            if row[2].startswith(('pbkdf2:', 'scrypt:')):
                password_valid = check_password_hash(row[2], clave)
            else:
                password_valid = row[2] == clave

        if row and password_valid:
            if row[2] == clave:
                conn = sqlite3.connect(DB_NAME)
                conn.execute("UPDATE usuarios SET clave = ? WHERE id = ?", (generate_password_hash(clave), row[0]))
                conn.commit()
                conn.close()
            session['usuario'] = row[1]
            session['rol'] = row[3]
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
                   COALESCE(v.direccion_cliente, c.direccion, ''), v.anulada, v.motivo_anulacion
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
            "anulada": bool(r[11]), "motivo_anulacion": r[12] or ""
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
                cursor.execute("SELECT precio_venta, stock_actual FROM productos WHERE id = ?", (item['id_producto'],))
                prod = cursor.fetchone()
                if not prod:
                    conn.close()
                    return jsonify({"error": f"Producto ID {item['id_producto']} no encontrado"}), 400

                precio_venta, stock_actual = prod
                cantidad = int(item['cantidad'])

                if cantidad <= 0:
                    conn.close()
                    return jsonify({"error": "La cantidad debe ser mayor que cero"}), 400

                if cantidad > stock_actual:
                    conn.close()
                    return jsonify({"error": f"Stock insuficiente para el producto ID {item['id_producto']}"}), 400

                subtotal = cantidad * precio_venta
                total_venta += subtotal
                detalles.append((item['id_producto'], cantidad, precio_venta, subtotal))

            saldo_pendiente = total_venta if tipo_pago == 'credito' else 0

            cursor.execute("""
                INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente, tipo_pago, direccion_cliente)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente, tipo_pago, direccion_cliente))

            id_venta = cursor.lastrowid

            for d in detalles:
                cursor.execute("""
                    INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal)
                    VALUES (?, ?, ?, ?, ?)
                """, (id_venta, d[0], d[1], d[2], d[3]))

                cursor.execute("""
                    UPDATE productos SET stock_actual = stock_actual - ? WHERE id = ?
                """, (d[1], d[0]))
                cursor.execute("INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) VALUES (?, 'salida', ?, ?, ?, ?)", (d[0], d[1], f'Venta #{id_venta}', session['usuario'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

            registrar_auditoria(conn, 'crear', 'venta', id_venta, f'Venta de {total_venta:.2f}')
            conn.commit()
            conn.close()
            return jsonify({"mensaje": f"Venta #{id_venta} registrada con éxito", "id_venta": id_venta}), 201

        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({"error": str(e)}), 500

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)