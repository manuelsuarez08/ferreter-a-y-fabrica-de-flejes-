"""Rutas del módulo de alquiler de maquinaria: equipos, alquileres y devoluciones."""
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..db import get_db
from ..security import login_required
from ..services.alquiler import calcular_total_alquiler
from ..services.equipos import normalizar_datos_equipo

bp = Blueprint('alquiler', __name__)

# Columnas del equipo en el orden usado al construir el dict manualmente.
_COLUMNAS_EQUIPO = (
    'id', 'codigo_interno', 'nombre', 'categoria', 'marca', 'modelo', 'numero_serie',
    'estado', 'tipo_tarifa', 'tarifa', 'tarifa_hora', 'tarifa_turno', 'tarifa_bulto',
    'medidas', 'especificaciones', 'cantidad_disponible', 'cantidad_total',
    'fecha_compra', 'fecha_ultimo_mantenimiento', 'observaciones', 'activo',
    'fecha_registro', 'fecha_actualizacion',
)


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _filas_equipo_a_dicts(rows):
    """Convierte filas (Row o tupla) a lista de dicts de equipo."""
    resultado = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            resultado.append(dict(r))
        else:
            resultado.append(dict(zip(_COLUMNAS_EQUIPO, r)))
    return resultado


def _listar_equipos_alquiler(conn):
    """Lista equipos activos resolviendo de forma tolerante la tabla disponible."""
    try:
        conn.execute("SELECT 1 FROM equipos LIMIT 1")
        tabla = 'equipos'
    except sqlite3.Error:
        tabla = 'equipos_alquiler'
    rows = conn.execute(f"SELECT * FROM {tabla} WHERE activo = 1 ORDER BY nombre ASC").fetchall()
    return _filas_equipo_a_dicts(rows)


@bp.route('/api/equipos', methods=['GET', 'POST'])
@login_required
def api_equipos():
    conn = get_db()
    conn.row_factory = sqlite3.Row

    if request.method == 'GET':
        rows = _listar_equipos_alquiler(conn)
        conn.close()
        return jsonify(rows)

    data = request.get_json(force=True) or {}
    equipo = normalizar_datos_equipo(data)
    if not equipo['nombre'] or not equipo['codigo_interno']:
        conn.close()
        return jsonify({"error": "Nombre y código interno del equipo son obligatorios"}), 400

    try:
        _upsert_equipo(conn, equipo, id_equipo=None)
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Equipo registrado correctamente", "equipo": equipo}), 201
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Ya existe un equipo con ese código interno"}), 400


def _upsert_equipo(conn, equipo, id_equipo=None):
    """Inserta o actualiza un equipo en las dos tablas espejo (equipos/equipos_alquiler).

    Se mantiene la doble escritura del diseño original por compatibilidad.
    """
    ahora = _ahora()
    columnas = (
        'nombre', 'categoria', 'marca', 'modelo', 'numero_serie', 'estado', 'tipo_tarifa',
        'tarifa', 'tarifa_hora', 'tarifa_turno', 'tarifa_bulto', 'medidas', 'especificaciones',
        'cantidad_disponible', 'cantidad_total', 'fecha_compra', 'fecha_ultimo_mantenimiento',
        'observaciones', 'activo',
    )
    valores = tuple(equipo[c] for c in columnas)

    # --- equipos_alquiler (por codigo_interno) ---
    existente = conn.execute(
        "SELECT id FROM equipos_alquiler WHERE codigo_interno = ?", (equipo['codigo_interno'],)
    ).fetchone()
    if existente:
        sets = ', '.join(f"{c} = ?" for c in columnas)
        conn.execute(
            f"UPDATE equipos_alquiler SET {sets}, fecha_actualizacion = ? WHERE id = ?",
            valores + (ahora, existente[0]),
        )
        id_equipo = existente[0]
    else:
        cols = ', '.join(('codigo_interno',) + columnas + ('fecha_registro', 'fecha_actualizacion'))
        marcas = ', '.join(['?'] * (len(columnas) + 3))
        conn.execute(
            f"INSERT INTO equipos_alquiler ({cols}) VALUES ({marcas})",
            (equipo['codigo_interno'],) + valores + (ahora, ahora),
        )
        id_equipo = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # --- equipos (espejo, por codigo_interno) ---
    existente2 = conn.execute(
        "SELECT id FROM equipos WHERE codigo_interno = ?", (equipo['codigo_interno'],)
    ).fetchone()
    if existente2:
        sets = ', '.join(f"{c} = ?" for c in columnas)
        conn.execute(
            f"UPDATE equipos SET {sets}, fecha_actualizacion = ? WHERE id = ?",
            valores + (ahora, existente2[0]),
        )
    else:
        cols = ', '.join(('id', 'codigo_interno') + columnas + ('fecha_registro', 'fecha_actualizacion'))
        marcas = ', '.join(['?'] * (len(columnas) + 4))
        conn.execute(
            f"INSERT INTO equipos ({cols}) VALUES ({marcas})",
            (id_equipo, equipo['codigo_interno']) + valores + (ahora, ahora),
        )
    return id_equipo


@bp.route('/api/equipos/<int:id_equipo>', methods=['PUT', 'DELETE'])
@login_required
def api_equipo_por_id(id_equipo):
    conn = get_db()
    conn.row_factory = sqlite3.Row

    if request.method == 'PUT':
        data = request.get_json(force=True) or {}
        equipo = normalizar_datos_equipo(data)
        if not equipo['nombre'] or not equipo['codigo_interno']:
            conn.close()
            return jsonify({"error": "Nombre y código interno del equipo son obligatorios"}), 400
        try:
            conn.execute(
                """
                UPDATE equipos
                SET codigo_interno = ?, nombre = ?, categoria = ?, marca = ?, modelo = ?,
                    numero_serie = ?, estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?,
                    tarifa_turno = ?, tarifa_bulto = ?, medidas = ?, especificaciones = ?,
                    cantidad_disponible = ?, cantidad_total = ?, fecha_compra = ?,
                    fecha_ultimo_mantenimiento = ?, observaciones = ?, fecha_actualizacion = ?
                WHERE id = ?
                """,
                (
                    equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'],
                    equipo['modelo'], equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'],
                    equipo['tarifa'], equipo['tarifa_hora'], equipo['tarifa_turno'],
                    equipo['tarifa_bulto'], equipo['medidas'], equipo['especificaciones'],
                    equipo['cantidad_disponible'], equipo['cantidad_total'], equipo['fecha_compra'],
                    equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'], _ahora(), id_equipo,
                ),
            )
            _actualizar_equipo_espejo(conn, equipo, id_equipo)
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


def _actualizar_equipo_espejo(conn, equipo, id_equipo):
    """Actualiza la fila espejo en equipos_alquiler aceptando dos estrategias de match."""
    try:
        conn.execute(
            """
            UPDATE equipos_alquiler
            SET codigo_interno = ?, nombre = ?, categoria = ?, marca = ?, modelo = ?,
                numero_serie = ?, estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?,
                tarifa_turno = ?, tarifa_bulto = ?, medidas = ?, especificaciones = ?,
                cantidad_disponible = ?, cantidad_total = ?, fecha_compra = ?,
                fecha_ultimo_mantenimiento = ?, observaciones = ?, fecha_actualizacion = ?
            WHERE id = ?
            """,
            (
                equipo['codigo_interno'], equipo['nombre'], equipo['categoria'], equipo['marca'],
                equipo['modelo'], equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'],
                equipo['tarifa'], equipo['tarifa_hora'], equipo['tarifa_turno'],
                equipo['tarifa_bulto'], equipo['medidas'], equipo['especificaciones'],
                equipo['cantidad_disponible'], equipo['cantidad_total'], equipo['fecha_compra'],
                equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'], _ahora(), id_equipo,
            ),
        )
    except sqlite3.Error:
        # Fallback: la fila espejo puede estar indexada por codigo_interno.
        conn.execute(
            """
            UPDATE equipos_alquiler
            SET nombre = ?, categoria = ?, marca = ?, modelo = ?, numero_serie = ?,
                estado = ?, tipo_tarifa = ?, tarifa = ?, tarifa_hora = ?, tarifa_turno = ?,
                tarifa_bulto = ?, medidas = ?, especificaciones = ?, cantidad_disponible = ?,
                cantidad_total = ?, fecha_compra = ?, fecha_ultimo_mantenimiento = ?,
                observaciones = ?, fecha_actualizacion = ?
            WHERE codigo_interno = ?
            """,
            (
                equipo['nombre'], equipo['categoria'], equipo['marca'], equipo['modelo'],
                equipo['numero_serie'], equipo['estado'], equipo['tipo_tarifa'], equipo['tarifa'],
                equipo['tarifa_hora'], equipo['tarifa_turno'], equipo['tarifa_bulto'],
                equipo['medidas'], equipo['especificaciones'], equipo['cantidad_disponible'],
                equipo['cantidad_total'], equipo['fecha_compra'],
                equipo['fecha_ultimo_mantenimiento'], equipo['observaciones'], _ahora(),
                equipo['codigo_interno'],
            ),
        )


@bp.route('/api/alquiler/equipos', methods=['GET', 'POST'])
@login_required
def api_alquiler_equipos():
    conn = get_db()
    conn.row_factory = sqlite3.Row

    if request.method == 'GET':
        rows = _listar_equipos_alquiler(conn)
        conn.close()
        return jsonify(rows)

    data = request.get_json(force=True) or {}
    equipo = normalizar_datos_equipo(data)
    if not equipo['codigo_interno'] or not equipo['nombre']:
        conn.close()
        return jsonify({"error": "Código interno y nombre son obligatorios"}), 400

    columnas = (
        'codigo_interno', 'nombre', 'categoria', 'marca', 'modelo', 'numero_serie', 'estado',
        'tipo_tarifa', 'tarifa', 'tarifa_hora', 'tarifa_turno', 'tarifa_bulto', 'medidas',
        'especificaciones', 'cantidad_disponible', 'cantidad_total', 'fecha_compra',
        'fecha_ultimo_mantenimiento', 'observaciones', 'activo',
    )
    ahora = _ahora()
    cols_sql = ', '.join(columnas + ('fecha_registro', 'fecha_actualizacion'))
    marcas = ', '.join(['?'] * (len(columnas) + 2))
    valores = tuple(equipo[c] for c in columnas) + (ahora, ahora)

    try:
        conn.execute(f"INSERT INTO equipos_alquiler ({cols_sql}) VALUES ({marcas})", valores)
        conn.commit()
        conn.close()
        return jsonify({"mensaje": "Equipo registrado correctamente"}), 201
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Ya existe un equipo con ese código interno"}), 400


def _resolver_tarifa(equipo, item):
    """Elige el valor de tarifa según el tipo solicitado, con fallback a la tarifa base."""
    tarifa_tipo = str(item.get('tarifa_tipo') or equipo['tipo_tarifa'] or 'dia').strip()
    por_tipo = {
        'hora': equipo['tarifa_hora'],
        'turno': equipo['tarifa_turno'],
        'bulto': equipo['tarifa_bulto'],
    }
    defecto = por_tipo.get(tarifa_tipo, equipo['tarifa'])
    tarifa_valor = float(item.get('tarifa_valor') or defecto or 0)
    return tarifa_tipo, tarifa_valor


@bp.route('/api/alquileres', methods=['GET', 'POST'])
@login_required
def api_alquileres():
    conn = get_db()
    conn.row_factory = sqlite3.Row

    if request.method == 'GET':
        rows = conn.execute(
            "SELECT a.*, c.nombre AS cliente_nombre FROM alquileres a "
            "JOIN clientes c ON c.id = a.id_cliente ORDER BY a.id DESC"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])

    data = request.get_json(force=True) or {}
    id_cliente = data.get('id_cliente')
    equipos = data.get('equipos') or []
    if not id_cliente:
        conn.close()
        return jsonify({"error": "Debe seleccionar un cliente"}), 400
    if not conn.execute("SELECT id FROM clientes WHERE id = ?", (int(id_cliente),)).fetchone():
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
        return _crear_alquiler(conn, data, int(id_cliente), equipos, fecha_salida, fecha_pactada)
    except ValueError:
        conn.close()
        return jsonify({"error": "Los valores enviados no son válidos"}), 400


def _crear_alquiler(conn, data, id_cliente, equipos, fecha_salida, fecha_pactada):
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO alquileres (id_cliente, id_usuario, fecha_salida, fecha_devolucion_pactada,
                                estado, valor_deposito, notas_salida, fecha_registro)
        VALUES (:cliente, :usuario, :salida, :pactada, 'activo', :deposito, :notas, :fecha)
        """,
        {'cliente': id_cliente, 'usuario': int(session.get('id_usuario') or 1),
         'salida': fecha_salida, 'pactada': fecha_pactada,
         'deposito': float(data.get('valor_deposito') or 0),
         'notas': str(data.get('notas_salida') or '').strip(), 'fecha': _ahora()},
    )
    id_alquiler = cursor.lastrowid

    subtotal_total = 0
    for item in equipos:
        id_equipo = int(item.get('id_equipo'))
        cantidad = max(1, int(item.get('cantidad') or 1))
        equipo = conn.execute("SELECT * FROM equipos_alquiler WHERE id = ?", (id_equipo,)).fetchone()
        if not equipo:
            conn.close()
            return jsonify({"error": f"Equipo con id {id_equipo} no existe"}), 400

        tarifa_tipo, tarifa_valor = _resolver_tarifa(equipo, item)
        subtotal = calcular_total_alquiler(fecha_salida, fecha_pactada, tarifa_tipo, tarifa_valor, cantidad)
        subtotal_total += subtotal

        cursor.execute(
            """
            INSERT INTO detalle_alquiler (id_alquiler, id_equipo, cantidad, tarifa_tipo, tarifa_valor,
                                          subtotal, estado_salida, observaciones)
            VALUES (:alquiler, :equipo, :cantidad, :tipo, :valor, :subtotal, :estado, :obs)
            """,
            {'alquiler': id_alquiler, 'equipo': id_equipo, 'cantidad': cantidad,
             'tipo': tarifa_tipo, 'valor': tarifa_valor, 'subtotal': subtotal,
             'estado': str(item.get('estado_salida') or 'bueno').strip() or 'bueno',
             'obs': str(item.get('observaciones') or '').strip()},
        )
        cursor.execute("UPDATE equipos_alquiler SET estado = 'En Alquiler' WHERE id = ?", (id_equipo,))

    cursor.execute(
        "UPDATE alquileres SET subtotal = ?, total_final = ? WHERE id = ?",
        (subtotal_total, subtotal_total, id_alquiler),
    )
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Alquiler registrado correctamente", "id_alquiler": id_alquiler}), 201


@bp.route('/api/alquileres/activos', methods=['GET'])
@login_required
def api_alquileres_activos():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
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
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/alquiler/alertas', methods=['GET'])
@login_required
def api_alquiler_alertas():
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
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
        """
    ).fetchall()
    conn.close()

    hoy = datetime.now()
    resultado = []
    for row in rows:
        fecha_limite = datetime.fromisoformat(str(row['fecha_devolucion_pactada']).replace('Z', '+00:00'))
        resultado.append({
            'id': row['id'], 'cliente': row['cliente'], 'equipo': row['equipo'],
            'codigo_interno': row['codigo_interno'], 'fecha_salida': row['fecha_salida'],
            'fecha_devolucion_pactada': row['fecha_devolucion_pactada'],
            'vencido': fecha_limite < hoy,
        })
    return jsonify(resultado)


@bp.route('/api/alquileres/<int:id_alquiler>/devolver', methods=['POST'])
@login_required
def api_devolver_alquiler(id_alquiler):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    data = request.get_json(force=True) or {}

    alquiler = conn.execute("SELECT * FROM alquileres WHERE id = ?", (id_alquiler,)).fetchone()
    if not alquiler:
        conn.close()
        return jsonify({"error": "Alquiler no encontrado"}), 404

    fecha_real = str(data.get('fecha_devolucion_real') or _ahora()).strip()
    cargos_extra = float(data.get('cargos_extra') or 0)
    descuento = float(data.get('descuento') or 0)
    notas = str(data.get('notas_devolucion') or '').strip()

    detalle = conn.execute(
        "SELECT * FROM detalle_alquiler WHERE id_alquiler = ?", (id_alquiler,)
    ).fetchall()
    subtotal_total = 0
    cursor = conn.cursor()

    for item in detalle:
        tarifa_tipo = str(item['tarifa_tipo'] or 'dia').strip()
        tarifa_valor = float(item['tarifa_valor'] or 0)
        cantidad = max(1, int(item['cantidad'] or 1))
        subtotal = calcular_total_alquiler(alquiler['fecha_salida'], fecha_real, tarifa_tipo,
                                           tarifa_valor, cantidad)
        subtotal_total += subtotal

        cursor.execute(
            "UPDATE detalle_alquiler SET subtotal = ?, estado_retorno = ?, observaciones = ? WHERE id = ?",
            (subtotal, str(data.get('estado_retorno') or 'bueno').strip() or 'bueno', notas, item['id']),
        )
        cursor.execute("UPDATE equipos_alquiler SET estado = 'Disponible' WHERE id = ?", (item['id_equipo'],))

    total_final = subtotal_total + cargos_extra - descuento
    cursor.execute(
        """
        UPDATE alquileres
        SET fecha_devolucion_real = ?, notas_devolucion = ?, cargos_extra = ?,
            descuento = ?, subtotal = ?, total_final = ?, estado = 'devuelto'
        WHERE id = ?
        """,
        (fecha_real, notas, cargos_extra, descuento, subtotal_total, total_final, id_alquiler),
    )
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Devolución registrada correctamente", "total_final": round(total_final, 2)})


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
