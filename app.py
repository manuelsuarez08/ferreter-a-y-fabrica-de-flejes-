from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'clave_secreta_ferreteria'
DB_NAME = 'ferreteria.db'

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

    # Tabla de clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cedula_nit TEXT UNIQUE,
            telefono TEXT
        )
    ''')

    # Tabla de productos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria TEXT,
            dimensiones TEXT,
            precio_costo REAL NOT NULL,
            precio_venta REAL NOT NULL,
            stock_actual INTEGER NOT NULL,
            stock_minimo INTEGER DEFAULT 5
        )
    ''')

    # Tabla de ventas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            fecha_dia TEXT NOT NULL,
            hora TEXT NOT NULL,
            total_venta REAL NOT NULL,
            saldo_pendiente REAL NOT NULL,
            tipo_pago TEXT NOT NULL,
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')

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

    # Usuario por defecto si la tabla está vacía
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO usuarios (usuario, clave, rol) VALUES ('admin', 'admin123', 'admin')")

    # Cliente general por defecto
    cursor.execute("SELECT COUNT(*) FROM clientes WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO clientes (id, nombre, cedula_nit, telefono) VALUES (1, 'Cliente Mostrador (General)', '222222222222', '0000000000')")

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
        cursor.execute("SELECT usuario, rol FROM usuarios WHERE usuario = ? AND clave = ?", (user, clave))
        row = cursor.fetchone()
        conn.close()

        if row:
            session['usuario'] = row[0]
            session['rol'] = row[1]
            return redirect(url_for('index'))
        return "<div style='text-align:center; padding: 50px; font-family: sans-serif;'><h3>❌ Credenciales incorrectas.</h3><a href='/login'>Volver a intentar</a></div>"

    return '''
        <div style="max-width: 450px; margin: 80px auto; font-family: sans-serif; padding: 30px; border: 1px solid #ddd; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); background: #ffffff;">
            <div style="text-align: center; margin-bottom: 25px;">
                <h1 style="color: #0d6efd; margin-bottom: 5px; font-size: 26px;">🛠️ FERRETERÍA Y FÁBRICA DE FLEJES</h1>
                <p style="color: #6c757d; margin: 0; font-size: 14px;">Sistema de Control e Inventario</p>
            </div>
            <form method="POST">
                <div style="margin-bottom: 15px;">
                    <label style="font-weight: bold; display: block; margin-bottom: 5px;">Usuario:</label>
                    <input type="text" name="usuario" style="width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box;" required autofocus>
                </div>
                <div style="margin-bottom: 20px;">
                    <label style="font-weight: bold; display: block; margin-bottom: 5px;">Contraseña:</label>
                    <input type="password" name="clave" style="width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box;" required>
                </div>
                <button type="submit" style="width: 100%; padding: 12px; background: #0d6efd; color: white; border: none; border-radius: 5px; font-weight: bold; font-size: 16px; cursor: pointer;">Ingresar al Sistema</button>
            </form>
        </div>
    '''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- API ADMINISTRACIÓN DE USUARIOS (RESTRINGIDO A ADMIN) ---
@app.route('/api/usuarios', methods=['GET', 'POST'])
def handle_usuarios():
    if session.get('rol') != 'admin':
        return jsonify({"error": "Acceso denegado. Solo el administrador puede realizar esta acción."}), 403

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, usuario, clave, rol FROM usuarios ORDER BY id ASC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{"id": r[0], "usuario": r[1], "clave": r[2], "rol": r[3]} for r in rows])

    elif request.method == 'POST':
        data = request.json
        try:
            cursor.execute("INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
                           (data['usuario'], data['clave'], data.get('rol', 'empleado')))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Usuario creado con éxito"}), 201
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"error": "El nombre de usuario ya existe"}), 400

@app.route('/api/usuarios/<int:id_usuario>', methods=['PUT', 'DELETE'])
def update_delete_usuario(id_usuario):
    if session.get('rol') != 'admin':
        return jsonify({"error": "Acceso denegado. Solo el administrador puede modificar usuarios."}), 403

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'PUT':
        data = request.json
        nueva_clave = data.get('clave')
        nuevo_rol = data.get('rol')

        if nueva_clave and nuevo_rol:
            cursor.execute("UPDATE usuarios SET clave = ?, rol = ? WHERE id = ?", (nueva_clave, nuevo_rol, id_usuario))
        elif nueva_clave:
            cursor.execute("UPDATE usuarios SET clave = ? WHERE id = ?", (nueva_clave, id_usuario))
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
def handle_clientes():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, nombre, cedula_nit, telefono FROM clientes ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{"id": r[0], "nombre": r[1], "cedula_nit": r[2], "telefono": r[3]} for r in rows])

    elif request.method == 'POST':
        data = request.json
        try:
            cursor.execute("INSERT INTO clientes (nombre, cedula_nit, telefono) VALUES (?, ?, ?)",
                           (data['nombre'], data.get('cedula_nit'), data.get('telefono')))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Cliente creado con éxito"}), 201
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"error": "La cédula o NIT ya está registrado"}), 400

# --- API PRODUCTOS (REABASTECIMIENTO Y REGISTRO NUEVO) ---
@app.route('/api/productos', methods=['GET', 'POST'])
def handle_productos():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, nombre, categoria, dimensiones, precio_costo, precio_venta, stock_actual, stock_minimo FROM productos ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([{
            "id": r[0], "nombre": r[1], "categoria": r[2], "dimensiones": r[3],
            "precio_costo": r[4], "precio_venta": r[5], "stock_actual": r[6], "stock_minimo": r[7]
        } for r in rows])

    elif request.method == 'POST':
        data = request.json
        nombre = data['nombre'].strip()
        categoria = data.get('categoria', '').strip()
        dimensiones = data.get('dimensiones', '').strip()
        precio_costo = float(data['precio_costo'])
        precio_venta = float(data['precio_venta'])
        stock_ingresado = int(data['stock_actual'])
        stock_minimo = int(data.get('stock_minimo', 5))

        # Verificar si existe coincidencia de nombre y dimensión
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
                SET categoria = ?, precio_costo = ?, precio_venta = ?, stock_actual = ?, stock_minimo = ?
                WHERE id = ?
            """, (categoria, precio_costo, precio_venta, nuevo_stock, stock_minimo, id_prod))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": f"Stock reabastecido con éxito. Nuevo stock total: {nuevo_stock}"}), 200
        else:
            cursor.execute("""
                INSERT INTO productos (nombre, categoria, dimensiones, precio_costo, precio_venta, stock_actual, stock_minimo)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nombre, categoria, dimensiones, precio_costo, precio_venta, stock_ingresado, stock_minimo))
            conn.commit()
            conn.close()
            return jsonify({"mensaje": "Producto agregado con éxito"}), 201

# --- API CREDITOS Y ABONOS ---
@app.route('/api/creditos', methods=['GET'])
def get_creditos():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    query = """
        SELECT c.id, c.nombre, c.telefono, SUM(v.saldo_pendiente) as deuda_total
        FROM clientes c
        JOIN ventas v ON c.id = v.id_cliente
        WHERE v.saldo_pendiente > 0
        GROUP BY c.id
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    return jsonify([{"id_cliente": r[0], "nombre": r[1], "telefono": r[2], "deuda_total": r[3]} for r in rows])

@app.route('/api/abonos', methods=['POST'])
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

    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Abono registrado con éxito"}), 201

# --- API VENTAS ---
@app.route('/api/ventas', methods=['GET', 'POST'])
def handle_ventas():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if request.method == 'GET':
        fecha = request.args.get('fecha')
        query = """
            SELECT v.id, v.fecha_dia, v.hora, c.nombre, v.total_venta, v.tipo_pago,
                   GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') as detalles,
                   c.cedula_nit, c.telefono, v.id_cliente
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
            "cedula_nit": r[7], "telefono": r[8], "id_cliente": r[9]
        } for r in rows])

    elif request.method == 'POST':
        data = request.json
        id_cliente = data['id_cliente']
        tipo_pago = data['tipo_pago']
        items = data['items']

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
                cantidad = item['cantidad']

                if cantidad > stock_actual:
                    conn.close()
                    return jsonify({"error": f"Stock insuficiente para el producto ID {item['id_producto']}"}), 400

                subtotal = cantidad * precio_venta
                total_venta += subtotal
                detalles.append((item['id_producto'], cantidad, precio_venta, subtotal))

            saldo_pendiente = total_venta if tipo_pago == 'credito' else 0

            cursor.execute("""
                INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente, tipo_pago)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente, tipo_pago))

            id_venta = cursor.lastrowid

            for d in detalles:
                cursor.execute("""
                    INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal)
                    VALUES (?, ?, ?, ?, ?)
                """, (id_venta, d[0], d[1], d[2], d[3]))

                cursor.execute("""
                    UPDATE productos SET stock_actual = stock_actual - ? WHERE id = ?
                """, (d[1], d[0]))

            conn.commit()
            conn.close()
            return jsonify({"mensaje": f"Venta #{id_venta} registrada con éxito", "id_venta": id_venta}), 201

        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({"error": str(e)}), 500

@app.route('/api/ventas/<int:id_venta>', methods=['GET'])
def get_factura_detalle(id_venta):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT v.id, v.fecha_dia, v.hora, c.nombre, c.cedula_nit, c.telefono, v.total_venta, v.tipo_pago, v.id_cliente
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
        "items": [{
            "nombre": d[0] + (f" ({d[1]})" if d[1] else ""),
            "cantidad": d[2],
            "precio_unitario": d[3],
            "subtotal": d[4]
        } for d in detalles]
    })

@app.route('/api/ventas/<int:id_venta>/cliente', methods=['PUT'])
def editar_cliente_factura(id_venta):
    data = request.json
    nombre = data.get('nombre')
    cedula_nit = data.get('cedula_nit')
    telefono = data.get('telefono')

    if not nombre:
        return jsonify({"error": "El nombre del cliente no puede estar vacío"}), 400

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT id_cliente FROM ventas WHERE id = ?", (id_venta,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "Factura no encontrada"}), 404

    id_cliente = row[0]

    try:
        if id_cliente == 1:
            cursor.execute("INSERT INTO clientes (nombre, cedula_nit, telefono) VALUES (?, ?, ?)",
                           (nombre, cedula_nit, telefono))
            nuevo_id_cliente = cursor.lastrowid
            cursor.execute("UPDATE ventas SET id_cliente = ? WHERE id = ?", (nuevo_id_cliente, id_venta))
        else:
            cursor.execute("UPDATE clientes SET nombre = ?, cedula_nit = ?, telefono = ? WHERE id = ?",
                           (nombre, cedula_nit, telefono, id_cliente))

        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Datos del cliente actualizados correctamente"}), 200

    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "La cédula/NIT ya pertenece a otro cliente registrado"}), 400
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)