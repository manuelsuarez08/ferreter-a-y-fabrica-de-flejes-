"""Rutas del núcleo: autenticación, configuración, reportes y administración.

Se agrupan en un único blueprint de propósito general por ser rutas
transversales de baja cohesión entre sí pero alto acoplamiento al usuario.
"""
import os
import shutil
import sqlite3
from datetime import datetime
from flask import (
    Blueprint, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from ..config import DB_NAME
from ..db import get_db
from ..security import admin_required, login_required
from ..services.auditoria import registrar_auditoria

bp = Blueprint('core', __name__)


# ── Autenticación ─────────────────────────────
@bp.route('/')
def index():
    if 'usuario' not in session:
        return redirect(url_for('core.login'))
    return render_template('index.html', usuario=session['usuario'], rol=session.get('rol', 'empleado'))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = (request.form.get('usuario') or '').strip()
        clave = request.form.get('clave') or ''

        conn = get_db()
        row = conn.execute(
            "SELECT id, usuario, clave, rol FROM usuarios WHERE usuario = ?", (user,)
        ).fetchone()
        conn.close()

        if row and _credenciales_validas(row, clave):
            session['usuario'] = row[1]
            session['rol'] = row[3]
            session['id_usuario'] = row[0]
            return redirect(url_for('core.index'))
        return render_template('login.html', error='Credenciales incorrectas.')

    return render_template('login.html')


def _credenciales_validas(row, clave):
    """Valida la contraseña y migra al vuelo los hashes heredados en texto plano."""
    stored_hash = row[2]
    if stored_hash is None or stored_hash == '':
        if row[1] == 'admin' and clave == 'admin123':
            _guardar_hash(row[0], clave)
            return True
        return False

    if isinstance(stored_hash, str) and stored_hash.startswith(('pbkdf2:', 'scrypt:')):
        return check_password_hash(stored_hash, clave)

    if stored_hash == clave:
        _guardar_hash(row[0], clave)
        return True
    return False


def _guardar_hash(id_usuario, clave):
    conn = get_db()
    conn.execute("UPDATE usuarios SET clave = ? WHERE id = ?", (generate_password_hash(clave), id_usuario))
    conn.commit()
    conn.close()


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('core.login'))


# ── Respaldo y configuración ──────────────────
@bp.route('/api/backup', methods=['GET'])
@admin_required
def descargar_respaldo():
    nombre = f"ferreteria-respaldo-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return send_file(DB_NAME, as_attachment=True, download_name=nombre)

@bp.route('/api/restaurar', methods=['POST'])
@admin_required
def restaurar_respaldo():
    """Reemplaza la base de datos actual por el archivo SQLite que se suba.

    Pensado para entornos con disco persistente (Render /var/data), donde el
    código nuevo NO reemplaza la base existente. Así se puede actualizar desde
    el navegador sin acceso al Shell ni a Git.

    Seguridad:
      - Solo administradores.
      - Valida que el archivo sea una base SQLite real y tenga las tablas
        mínimas, antes de tocar la actual.
      - Guarda una copia de la base actual junto a ella (mismo directorio) por
        si hay que volver atrás.
    """
    archivo = request.files.get('archivo')
    if not archivo or not archivo.filename:
        return jsonify({"error": "No se recibió ningún archivo"}), 400
    # 1) Se escribe el archivo subido en un temporal dentro del mismo directorio
    #    de la base (así el os.replace final es atómico en el mismo volumen).
    carpeta = os.path.dirname(os.path.abspath(DB_NAME)) or '.'
    tmp_nuevo = os.path.join(carpeta, '_restaurar_nuevo.db')
    archivo.save(tmp_nuevo)

    # 2) Validación: debe ser SQLite y tener las tablas clave.
    try:
        with open(tmp_nuevo, 'rb') as f:
            cabecera = f.read(16)
        if cabecera != b'SQLite format 3\x00':
            raise ValueError('El archivo no es una base de datos SQLite válida.')

        prueba = sqlite3.connect(tmp_nuevo)
        tablas = {r[0] for r in prueba.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        prueba.close()
        faltantes = {'ventas', 'clientes', 'productos'} - tablas
        if faltantes:
            raise ValueError('La base no tiene las tablas esperadas: ' + ', '.join(sorted(faltantes)))
    except Exception as e:
        try:
            os.remove(tmp_nuevo)
        except OSError:
            pass
        return jsonify({"error": f"Archivo inválido: {e}"}), 400
    # 3) Respaldo de la base actual (no se sobreescribe un respaldo anterior).
    respaldo = None
    if os.path.exists(DB_NAME):
        respaldo = os.path.join(
            carpeta, f"ferreteria-respaldo-antes-restaurar-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
        try:
            shutil.copy2(DB_NAME, respaldo)
        except Exception:
            respaldo = None
    # 4) Reemplazo atómico.
    try:
        os.replace(tmp_nuevo, DB_NAME)
        # Limpia los archivos WAL/SHM viejos para que no queden datos colgados.
        for sufijo in ('-wal', '-shm'):
            ruta = DB_NAME + sufijo
            if os.path.exists(ruta):
                try:
                    os.remove(ruta)
                except OSError:
                    pass
    except Exception as e:
        return jsonify({"error": f"No se pudo reemplazar la base: {e}"}), 500
    # 5) Cuenta final para confirmar al usuario que quedó bien.
    try:
        conn = get_db()
        ventas = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
        clientes = conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
        productos = conn.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
        conn.close()
    except Exception as e:
        return jsonify({"error": f"La base se reemplazó pero no se pudo leer: {e}"}), 500
    return jsonify({
        "mensaje": "Base de datos restaurada correctamente",
        "ventas": ventas, "clientes": clientes, "productos": productos,
        "respaldo_anterior": os.path.basename(respaldo) if respaldo else None,
    })


@bp.route('/api/configuracion', methods=['GET', 'PUT'])
@login_required
def configuracion_negocio():
    conn = get_db()
    if request.method == 'GET':
        row = conn.execute(
            "SELECT nombre, nit, telefono, direccion, consecutivo FROM configuracion WHERE id = 1"
        ).fetchone()
        conn.close()
        return jsonify({"nombre": row[0], "nit": row[1], "telefono": row[2],
                        "direccion": row[3], "consecutivo": row[4]})

    if session.get('rol') != 'admin':
        conn.close()
        return jsonify({"error": "Solo el administrador puede cambiar la configuración"}), 403

    data = request.json or {}
    conn.execute(
        "UPDATE configuracion SET nombre = ?, nit = ?, telefono = ?, direccion = ? WHERE id = 1",
        (str(data.get('nombre', '')).strip(), str(data.get('nit', '')).strip(),
         str(data.get('telefono', '')).strip(), str(data.get('direccion', '')).strip()),
    )
    registrar_auditoria(conn, 'actualizar', 'configuracion', 1, 'Datos del negocio actualizados')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Configuración guardada"})


# ── Reportes ──────────────────
def _rango_periodo(periodo):
    """Traduce el período pedido a un par (fecha_inicio, fecha_fin) en texto."""
    hoy = datetime.now().date()
    if periodo == 'semana':
        inicio = hoy.fromordinal(hoy.toordinal() - hoy.weekday())
    elif periodo == 'mes':
        inicio = hoy.replace(day=1)
    else:
        periodo = 'hoy'
        inicio = hoy
    return periodo, inicio.strftime('%Y-%m-%d'), hoy.strftime('%Y-%m-%d')


@bp.route('/api/reportes', methods=['GET'])
@login_required
def reportes():
    conn = get_db()
    periodo, fecha_inicio, fecha_fin = _rango_periodo(request.args.get('periodo', 'hoy'))
    rango = (fecha_inicio, fecha_fin)

    ventas = conn.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(total_venta), 0),
               COALESCE(SUM(total_venta - (SELECT COALESCE(SUM(dv.cantidad * COALESCE(p.precio_costo, 0)), 0)
               FROM detalle_ventas dv JOIN productos p ON p.id = dv.id_producto
               WHERE dv.id_venta = v.id)), 0)
        FROM ventas v
        WHERE fecha_dia BETWEEN ? AND ? AND anulada = 0
        """, rango
    ).fetchone()

    abonos = conn.execute(
        "SELECT COALESCE(SUM(monto), 0) FROM abonos WHERE substr(fecha, 1, 10) BETWEEN ? AND ?",
        rango,
    ).fetchone()[0]
    gastos = conn.execute(
        "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE fecha BETWEEN ? AND ?", rango
    ).fetchone()[0]
    cartera = conn.execute(
        "SELECT COALESCE(SUM(saldo_pendiente), 0) FROM ventas WHERE saldo_pendiente > 0 AND anulada = 0"
    ).fetchone()[0]
    stock_bajo = conn.execute(
        "SELECT COUNT(*) FROM productos WHERE stock_actual <= stock_minimo"
    ).fetchone()[0]
    top = conn.execute(
        """
        SELECT p.nombre, SUM(dv.cantidad) cantidad
        FROM detalle_ventas dv JOIN productos p ON p.id = dv.id_producto JOIN ventas v ON v.id = dv.id_venta
        WHERE v.anulada = 0 AND v.fecha_dia BETWEEN ? AND ?
        GROUP BY p.id ORDER BY cantidad DESC LIMIT 5
        """, rango
    ).fetchall()
    conn.close()

    return jsonify({"periodo": periodo, "fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin,
                    "ventas": ventas[0], "ingresos": ventas[1], "ganancia": ventas[2],
                    "gastos": gastos, "utilidad_neta": ventas[2] - gastos,
                    "abonos": abonos, "cartera": cartera, "stock_bajo": stock_bajo,
                    "mas_vendidos": [{"nombre": r[0], "cantidad": r[1]} for r in top]})


# ── Gastos y cierres de caja ──────────────────
@bp.route('/api/gastos', methods=['GET', 'POST'])
@login_required
def gastos_negocio():
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute(
            "SELECT id, fecha, categoria, descripcion, monto, usuario FROM gastos ORDER BY id DESC LIMIT 200"
        ).fetchall()
        conn.close()
        return jsonify([{"id": r[0], "fecha": r[1], "categoria": r[2],
                         "descripcion": r[3], "monto": r[4], "usuario": r[5]} for r in rows])

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
        cursor.execute(
            "INSERT INTO gastos (fecha, categoria, descripcion, monto, usuario) "
            "VALUES (:fecha, :categoria, :descripcion, :monto, :usuario)",
            {'fecha': fecha, 'categoria': categoria, 'descripcion': descripcion,
             'monto': monto, 'usuario': session['usuario']},
        )
        id_gasto = cursor.lastrowid
        registrar_auditoria(conn, 'registrar', 'gasto', id_gasto, f'Gasto de {monto:.2f}')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Gasto registrado", "id": id_gasto}), 201
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "Datos del gasto inválidos"}), 400


@bp.route('/api/cierres-caja', methods=['GET', 'POST'])
@login_required
def cierres_caja():
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute(
            "SELECT id, fecha, efectivo_esperado, efectivo_contado, diferencia, observaciones, "
            "usuario, fecha_registro FROM cierres_caja ORDER BY id DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return jsonify([{"id": r[0], "fecha": r[1], "efectivo_esperado": r[2],
                         "efectivo_contado": r[3], "diferencia": r[4], "observaciones": r[5] or "",
                         "usuario": r[6], "fecha_registro": r[7]} for r in rows])

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
        ventas_efectivo = cursor.execute(
            "SELECT COALESCE(SUM(total_venta), 0) FROM ventas "
            "WHERE fecha_dia = ? AND tipo_pago = 'efectivo' AND anulada = 0", (fecha,)
        ).fetchone()[0]
        abonos = cursor.execute(
            "SELECT COALESCE(SUM(monto), 0) FROM abonos WHERE substr(fecha, 1, 10) = ?", (fecha,)
        ).fetchone()[0]
        gastos = cursor.execute(
            "SELECT COALESCE(SUM(monto), 0) FROM gastos WHERE fecha = ?", (fecha,)
        ).fetchone()[0]
        efectivo_esperado = ventas_efectivo + abonos - gastos
        diferencia = contado - efectivo_esperado
        cursor.execute(
            """
            INSERT INTO cierres_caja (fecha, efectivo_esperado, efectivo_contado, diferencia,
                                      observaciones, usuario, fecha_registro)
            VALUES (:fecha, :esperado, :contado, :diferencia, :obs, :usuario, :registro)
            ON CONFLICT(fecha) DO UPDATE SET efectivo_esperado = excluded.efectivo_esperado,
            efectivo_contado = excluded.efectivo_contado, diferencia = excluded.diferencia,
            observaciones = excluded.observaciones, usuario = excluded.usuario,
            fecha_registro = excluded.fecha_registro
            """,
            {'fecha': fecha, 'esperado': efectivo_esperado, 'contado': contado,
             'diferencia': diferencia, 'obs': observaciones, 'usuario': session['usuario'],
             'registro': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
        )
        registrar_auditoria(conn, 'cerrar', 'caja', None, f'Cierre {fecha}: diferencia {diferencia:.2f}')
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Cierre de caja guardado", "fecha": fecha,
                        "efectivo_esperado": efectivo_esperado, "diferencia": diferencia}), 201
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"error": "Datos del cierre inválidos"}), 400


# ── Auditoría ─────────────────
@bp.route('/api/auditoria', methods=['GET'])
@admin_required
def consultar_auditoria():
    conn = get_db()
    rows = conn.execute(
        "SELECT usuario, accion, entidad, entidad_id, detalles, fecha FROM auditoria ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return jsonify([{"usuario": r[0], "accion": r[1], "entidad": r[2],
                     "entidad_id": r[3], "detalles": r[4], "fecha": r[5]} for r in rows])


# ── Administración de usuarios ────────────────────────────────
@bp.route('/api/usuarios', methods=['GET', 'POST'])
@admin_required
def handle_usuarios():
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'GET':
        rows = cursor.execute("SELECT id, usuario, clave, rol FROM usuarios ORDER BY id ASC").fetchall()
        conn.close()
        return jsonify([{"id": r[0], "usuario": r[1], "rol": r[3]} for r in rows])

    data = request.json
    try:
        cursor.execute(
            "INSERT INTO usuarios (usuario, clave, rol) "
            "VALUES (:usuario, :clave, :rol)",
            {'usuario': data['usuario'], 'clave': generate_password_hash(data['clave']),
             'rol': data.get('rol', 'empleado')},
        )
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Usuario creado con éxito"}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "El nombre de usuario ya existe"}), 400


@bp.route('/api/usuarios/<int:id_usuario>', methods=['PUT', 'DELETE'])
@admin_required
def update_delete_usuario(id_usuario):
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'PUT':
        data = request.json
        nueva_clave = data.get('clave')
        nuevo_rol = data.get('rol')

        if nueva_clave and nuevo_rol:
            cursor.execute("UPDATE usuarios SET clave = ?, rol = ? WHERE id = ?",
                           (generate_password_hash(nueva_clave), nuevo_rol, id_usuario))
        elif nueva_clave:
            cursor.execute("UPDATE usuarios SET clave = ? WHERE id = ?",
                           (generate_password_hash(nueva_clave), id_usuario))
        elif nuevo_rol:
            cursor.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, id_usuario))

        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Usuario actualizado correctamente"}), 200

    if id_usuario == 1:
        conn.close()
        return jsonify({"error": "No se puede eliminar el usuario administrador principal"}), 400

    cursor.execute("DELETE FROM usuarios WHERE id = ?", (id_usuario,))
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Usuario eliminado con éxito"}), 200


# ── Notificaciones de stock ───────────────────
@bp.route('/api/notificaciones', methods=['GET'])
@login_required
def get_notificaciones():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, nombre, categoria, dimensiones, stock_actual, stock_minimo
        FROM productos
        WHERE COALESCE(activo, 1) = 1 AND stock_actual <= stock_minimo
        ORDER BY stock_actual ASC
        """
    ).fetchall()
    conn.close()

    notificaciones = [{
        "id": r[0],
        "nombre": r[1],
        "categoria": r[2] or "Sin Categoría",
        "dimensiones": r[3] or "",
        "stock_actual": r[4],
        "stock_minimo": r[5],
        "urgencia": "critico" if (r[4] or 0) <= 0 else "alto",
    } for r in rows]

    return jsonify({"total": len(notificaciones), "alertas": notificaciones})


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
