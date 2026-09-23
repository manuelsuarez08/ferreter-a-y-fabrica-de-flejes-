"""Rutas de ventas, créditos y abonos.

Incluye el registro de la venta (POS), su anulación, el detalle de factura,
los despachos "para llevar" y la gestión de créditos/abonos por cliente.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from ..db import get_db
from ..security import admin_required, login_required
from ..services.auditoria import registrar_auditoria
from ..services.ordenes_fleje import registrar_orden_fleje_desde_venta
from ..services import siigo

bp = Blueprint('ventas', __name__)


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── Ventas ────────────────────
@bp.route('/api/ventas', methods=['GET', 'POST'])
@login_required
def handle_ventas():
    if request.method == 'GET':
        return _listar_ventas()
    return _registrar_venta()


def _listar_ventas():
    conn = get_db()
    fecha = request.args.get('fecha')
    query = """
        SELECT v.id, v.fecha_dia, v.hora, c.nombre, v.total_venta, v.tipo_pago,
               GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') as detalles,
               c.cedula_nit, c.telefono, v.id_cliente,
               COALESCE(v.direccion_cliente, c.direccion, ''), v.anulada, v.motivo_anulacion,
               v.tipo_entrega, v.numero_pedido,
               COALESCE(v.siigo_estado, 'no_solicitada'), v.siigo_numero,
               v.siigo_cufe, v.siigo_pdf_url, v.siigo_error
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

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([{
        "id": r[0], "fecha_dia": r[1], "hora": r[2], "cliente": r[3],
        "total_venta": r[4], "tipo_pago": r[5], "productos_detalle": r[6],
        "cedula_nit": r[7], "telefono": r[8], "id_cliente": r[9], "direccion": r[10] or "",
        "anulada": bool(r[11]), "motivo_anulacion": r[12] or "",
        "tipo_entrega": r[13] or "entrega_inmediata", "numero_pedido": r[14],
        "siigo_estado": r[15] or "no_solicitada", "siigo_numero": r[16] or "",
        "siigo_cufe": r[17] or "", "siigo_pdf_url": r[18] or "", "siigo_error": r[19] or "",
    } for r in rows])


def _es_producto_fleje(nombre, categoria, item):
    """Determina si un ítem vendido corresponde a un fleje a figurar."""
    return bool(
        (categoria or '').lower().find('fleje') >= 0
        or (nombre or '').lower().find('fleje') >= 0
        or item.get('calibre') or item.get('ancho_cm') or item.get('largo_cm')
    )


def _leer_config_iva(cursor):
    # Devuelve (porcentaje, activo) del IVA configurado para el negocio.
    fila = cursor.execute(
        "SELECT COALESCE(iva_porcentaje, 19), COALESCE(iva_activo, 1) FROM configuracion WHERE id = 1"
    ).fetchone()
    if not fila:
        return 19.0, True
    return float(fila[0] or 0), bool(fila[1])


def _construir_detalles(cursor, conn, items):
    """Valida los ítems y calcula el total. Devuelve (total, detalles) o un error."""
    subtotal_venta = 0
    detalles = []
    for item in items:
        item_id = item['id_producto']
        prod = cursor.execute(
            "SELECT nombre, categoria, precio_venta FROM productos WHERE id = ?", (item_id,)
        ).fetchone()
        if not prod:
            return None, (jsonify({"error": f"Producto ID {item_id} no encontrado"}), 400)

        nombre_producto, categoria_producto, precio_venta = prod
        precio_venta = float(precio_venta or 0)
        cantidad = int(item['cantidad'])
        if cantidad <= 0:
            return None, (jsonify({"error": "La cantidad debe ser mayor que cero"}), 400)

        # El cajero puede editar el precio mientras arma el pedido: si llega un
        # precio_unitario valido se usa ese; si no, se toma el precio del catalogo.
        precio_item = item.get('precio_unitario')
        if precio_item is not None and str(precio_item).strip() != '':
            try:
                precio_item = float(precio_item)
            except (TypeError, ValueError):
                return None, (jsonify({"error": "Precio inválido en una línea"}), 400)
            if precio_item < 0:
                return None, (jsonify({"error": "El precio no puede ser negativo"}), 400)
            precio_venta = precio_item

        # VENTAS PERMISIVAS: no se valida stock ni precio de costo.
        subtotal = cantidad * precio_venta
        subtotal_venta += subtotal
        detalles.append({
            'id_producto': item_id, 'cantidad': cantidad, 'precio': precio_venta,
            'nombre': nombre_producto, 'categoria': categoria_producto, 'item': item,
        })

    # IVA sobre el subtotal. Se redondea a pesos (sin centavos) para que la
    # tirilla cuadre exacto: total = subtotal + iva.
    iva_porcentaje, iva_activo = _leer_config_iva(cursor)
    if iva_activo and iva_porcentaje > 0:
        iva_valor = round(subtotal_venta * iva_porcentaje / 100)
    else:
        iva_porcentaje, iva_valor = 0.0, 0
    total_venta = subtotal_venta + iva_valor
    return (subtotal_venta, iva_valor, iva_porcentaje, total_venta, detalles), None


def _validar_datos_factura_electronica(cliente):
    """Valida que el cliente tenga los datos completos exigidos por Siigo/DIAN.

    Returns:
        Lista de campos faltantes (vacía si todo está completo).
    """
    faltantes = []
    if not str(cliente.get('tipo_documento') or '').strip():
        faltantes.append('Tipo de Documento')
    if not str(cliente.get('cedula_nit') or '').strip():
        faltantes.append('Cédula/NIT')
    if not str(cliente.get('nombre') or '').strip():
        faltantes.append('Nombre completo')
    if not str(cliente.get('email') or '').strip():
        faltantes.append('Correo electrónico')
    if not str(cliente.get('telefono') or '').strip():
        faltantes.append('Teléfono')
    return faltantes


def _datos_cliente_para_siigo(cursor, id_cliente):
    """Lee de la BD los datos del cliente necesarios para la factura electrónica."""
    fila = cursor.execute(
        "SELECT nombre, cedula_nit, telefono, COALESCE(email, ''), "
        "COALESCE(tipo_documento, 'CC') FROM clientes WHERE id = ?",
        (id_cliente,),
    ).fetchone()
    if not fila:
        return None
    return {'nombre': fila[0], 'cedula_nit': fila[1], 'telefono': fila[2],
            'email': fila[3], 'tipo_documento': fila[4]}

# Estados de Siigo válidos para una venta.
def _estado_siigo_inicial(solicitada):
    return 'pendiente' if solicitada else 'no_solicitada'

def _registrar_venta():
    data = request.json
    id_cliente = data['id_cliente']
    tipo_pago = data['tipo_pago']
    items = data['items']
    direccion_ingresada = str(data.get('direccion', '')).strip()
    # La factura electrónica es OPCIONAL: por defecto NO se solicita.
    factura_electronica = bool(data.get('factura_electronica', False))

    conn = get_db()
    cursor = conn.cursor()

    cliente = cursor.execute(
        "SELECT direccion, nombre, cedula_nit, telefono, COALESCE(email, ''), "
        "COALESCE(tipo_documento, 'CC') FROM clientes WHERE id = ?", (id_cliente,)
    ).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 400

    # Si se pidió factura electrónica, el cliente debe tener datos completos.
    if factura_electronica:
        datos_fiscales = {'nombre': cliente[1], 'cedula_nit': cliente[2], 'telefono': cliente[3],
                          'email': cliente[4], 'tipo_documento': cliente[5]}
        faltantes = _validar_datos_factura_electronica(datos_fiscales)
        if faltantes:
            conn.close()
            return jsonify({
                "error": "Para generar la Factura Electrónica el cliente debe tener registrados: "
                         + ", ".join(faltantes) + ". Edítelo y vuelva a intentarlo, "
                         "o desactive la casilla para una venta normal.",
                "campos_faltantes": faltantes,
            }), 400

    direccion_cliente = direccion_ingresada or (cliente[0] or '')
    if direccion_ingresada:
        cursor.execute("UPDATE clientes SET direccion = ? WHERE id = ?", (direccion_ingresada, id_cliente))

    if not items:
        conn.close()
        return jsonify({"error": "La venta debe contener al menos un producto"}), 400

    resultado, error = _construir_detalles(cursor, conn, items)
    if error:
        conn.close()
        return error
    subtotal_venta, iva_valor, iva_porcentaje, total_venta, detalles = resultado

    now = datetime.now()
    saldo_pendiente = total_venta if tipo_pago == 'credito' else 0

    tipo_entrega = data.get('tipo_entrega', 'entrega_inmediata')
    if tipo_entrega not in ('entrega_inmediata', 'para_llevar'):
        tipo_entrega = 'entrega_inmediata'

    numero_pedido = None
    if tipo_entrega == 'para_llevar':
        numero_pedido = cursor.execute(
            "SELECT COALESCE(MAX(numero_pedido), 0) + 1 FROM ventas WHERE numero_pedido IS NOT NULL"
        ).fetchone()[0]

    try:
        cursor.execute(
            """
            INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente,
                                tipo_pago, direccion_cliente, tipo_entrega, numero_pedido,
                                siigo_estado, subtotal_venta, iva_valor, iva_porcentaje)
            VALUES (:cliente, :dia, :hora, :total, :saldo, :pago, :direccion, :entrega, :pedido,
                    :siigo_estado, :subtotal, :iva, :iva_pct)
            """,
            {'cliente': id_cliente, 'dia': now.strftime('%Y-%m-%d'), 'hora': now.strftime('%H:%M:%S'),
             'total': total_venta, 'saldo': saldo_pendiente, 'pago': tipo_pago,
             'direccion': direccion_cliente, 'entrega': tipo_entrega, 'pedido': numero_pedido,
             'siigo_estado': _estado_siigo_inicial(factura_electronica),
             'subtotal': subtotal_venta, 'iva': iva_valor, 'iva_pct': iva_porcentaje},
        )
        id_venta = cursor.lastrowid

        for d in detalles:
            cursor.execute(
                "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal) "
                "VALUES (:venta, :producto, :cantidad, :precio, :subtotal)",
                {'venta': id_venta, 'producto': d['id_producto'], 'cantidad': d['cantidad'],
                 'precio': d['precio'], 'subtotal': d['cantidad'] * d['precio']},
            )
            cursor.execute(
                "UPDATE productos SET stock_actual = COALESCE(stock_actual, 0) - ? WHERE id = ?",
                (d['cantidad'], d['id_producto']),
            )
            cursor.execute(
                "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
                "VALUES (:id, 'salida', :cantidad, :motivo, :usuario, :fecha)",
                {'id': d['id_producto'], 'cantidad': d['cantidad'],
                 'motivo': f'Venta #{id_venta}', 'usuario': session['usuario'], 'fecha': _ahora()},
            )

            if _es_producto_fleje(d['nombre'], d['categoria'], d['item']):
                registrar_orden_fleje_desde_venta(conn, id_venta, id_cliente, d['item'], d['nombre'])

        etiqueta = f'Para llevar — Pedido #{numero_pedido}' if numero_pedido else 'Entrega en mostrador'
        if factura_electronica:
            etiqueta += ' — Factura electrónica solicitada'
        registrar_auditoria(conn, 'crear', 'venta', id_venta, f'Venta de {total_venta:.2f} — {etiqueta}')
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500

    # ── Transmisión OPCIONAL en segundo plano a Siigo ──────────────────────
    # La venta local ya está guardada: si Siigo falla, no se pierde y puede
    # reintentarse desde el panel de administración.
    resultado_siigo = None
    if factura_electronica:
        datos_cliente = _datos_cliente_para_siigo(cursor, id_cliente)
        resultado_siigo = _transmitir_a_siigo(conn, id_venta, datos_cliente, {
            'fecha': now.strftime('%Y-%m-%d'), 'tipo_pago': tipo_pago,
            'items': [{'codigo': d['id_producto'], 'nombre': d['nombre'],
                       'cantidad': d['cantidad'], 'precio_unitario': d['precio']} for d in detalles],
            'total': total_venta,
        }, direccion_cliente)
    conn.close()

    msg = f"Venta #{id_venta} registrada con éxito"
    if numero_pedido:
        msg += f" — Pedido #{numero_pedido} creado para despacho"
    return jsonify({"mensaje": msg, "id_venta": id_venta,
                    "tipo_entrega": tipo_entrega, "numero_pedido": numero_pedido,
                    "factura_electronica": factura_electronica,
                    "siigo": resultado_siigo}), 201


# ── Facturación electrónica opcional (Siigo) ──────────────────
def _transmitir_a_siigo(conn, id_venta, datos_cliente, venta_local, direccion=''):
    """Transmite la venta a Siigo y persiste el resultado en la tabla `ventas`.

    Pensada para ejecutarse sin interrumpir el flujo del POS: nunca lanza
    excepción hacia el llamador. Devuelve un dict con el resultado para que el
    frontend muestre el comprobante o la alerta de error.
    """
    venta_payload = {
        'id_venta': id_venta,
        'fecha': venta_local.get('fecha'),
        'tipo_pago': venta_local.get('tipo_pago'),
        'total': venta_local.get('total'),
        'items': venta_local.get('items') or [],
        'cliente': {**(datos_cliente or {}), 'direccion': direccion or ''},
    }
    ahora = _ahora()
    try:
        resultado = siigo.emitir_factura(venta_payload)
    except siigo.SiigoError as e:
        conn.execute(
            "UPDATE ventas SET siigo_estado = 'error', siigo_error = ?, "
            "siigo_intentos = COALESCE(siigo_intentos, 0) + 1, siigo_fecha_emision = ? WHERE id = ?",
            (e.motivo, ahora, id_venta),
        )
        registrar_auditoria(conn, 'siigo_error', 'venta', id_venta, e.motivo)
        conn.commit()
        return {'ok': False, 'estado': 'error', 'motivo': e.motivo}
    except Exception as e:  # red, proxy, respuesta inesperada…
        motivo = f"No se pudo transmitir a Siigo: {e}"
        conn.execute(
            "UPDATE ventas SET siigo_estado = 'error', siigo_error = ?, "
            "siigo_intentos = COALESCE(siigo_intentos, 0) + 1, siigo_fecha_emision = ? WHERE id = ?",
            (motivo, ahora, id_venta),
        )
        registrar_auditoria(conn, 'siigo_error', 'venta', id_venta, motivo)
        conn.commit()
        return {'ok': False, 'estado': 'error', 'motivo': motivo}

    conn.execute(
        "UPDATE ventas SET siigo_estado = 'aprobada', siigo_numero = ?, siigo_cufe = ?, "
        "siigo_pdf_url = ?, siigo_xml_url = ?, siigo_error = NULL, "
        "siigo_fecha_emision = ?, siigo_intentos = COALESCE(siigo_intentos, 0) + 1 "
        "WHERE id = ?",
        (resultado['numero'], resultado['cufe'], resultado['pdf_url'], resultado['xml_url'],
         ahora, id_venta),
    )
    registrar_auditoria(conn, 'siigo_factura', 'venta', id_venta,
                        f"Factura electrónica {resultado['numero']} emitida")
    conn.commit()
    return {'ok': True, 'estado': 'aprobada', 'numero': resultado['numero'],
            'cufe': resultado['cufe'], 'pdf_url': resultado['pdf_url'],
            'xml_url': resultado['xml_url'], 'email': (datos_cliente or {}).get('email', '')}


def _cargar_venta_para_siigo(conn, id_venta):
    """Recupera de la BD una venta y sus datos para reenviarla a Siigo."""
    venta = conn.execute(
        """SELECT v.fecha_dia, v.tipo_pago, v.total_venta, v.id_cliente,
                COALESCE(v.direccion_cliente, ''), v.anulada
         FROM ventas v WHERE v.id = ?""", (id_venta,)
    ).fetchone()
    if not venta:
        return None, jsonify({"error": "Venta no encontrada"}), 404
    fecha_dia, tipo_pago, total, id_cliente, direccion, anulada = venta
    if anulada:
        return None, jsonify({"error": "No se puede facturar electrónicamente una venta anulada"}), 400
    cliente = _datos_cliente_para_siigo(conn, id_cliente)
    if not cliente:
        return None, jsonify({"error": "Cliente no encontrado"}), 400
    faltantes = _validar_datos_factura_electronica(cliente)
    if faltantes:
        return None, jsonify({
            "error": "El cliente no tiene datos completos para facturación electrónica. "
                     "Faltan: " + ", ".join(faltantes),
            "campos_faltantes": faltantes,
        }), 400
    items = conn.execute(
        "SELECT dv.id_producto, p.nombre, dv.cantidad, dv.precio_unitario "
        "FROM detalle_ventas dv JOIN productos p ON p.id = dv.id_producto "
        "WHERE dv.id_venta = ?", (id_venta,)
    ).fetchall()
    return {
        'id_venta': id_venta, 'fecha': fecha_dia, 'tipo_pago': tipo_pago, 'total': total,
        'cliente': {**cliente, 'direccion': direccion or ''},
        'items': [{'codigo': r[0], 'nombre': r[1], 'cantidad': r[2], 'precio_unitario': r[3]}
                  for r in items],
    }, None, None
@bp.route('/api/ventas/<int:id_venta>/siigo', methods=['POST'])
@login_required
def transmitir_siigo(id_venta):
    """Transmite (o reintenta) la factura electrónica de una venta a Siigo.

    Sirve tanto para el reintento desde el panel de administración como para
    cuando un cliente pide la factura electrónica minutos después de comprar.
    """
    conn = get_db()
    datos, respuesta_error, status = _cargar_venta_para_siigo(conn, id_venta)
    if respuesta_error is not None:
        conn.close()
        # Se devuelve el cuerpo junto con su código HTTP real (404/400); sin el
        # status, Flask respondería 200 y el error parecería un éxito.
        return respuesta_error, status or 400
    resultado = _transmitir_a_siigo(
        conn, id_venta, datos['cliente'],
        {'fecha': datos['fecha'], 'tipo_pago': datos['tipo_pago'],
         'items': datos['items'], 'total': datos['total']},
        datos['cliente'].get('direccion', ''),
    )
    conn.close()

    if resultado.get('ok'):
        return jsonify({"mensaje": "Factura Electrónica emitida con éxito", **resultado}), 200
    return jsonify({
        "error": f"No se pudo transmitir a Siigo: {resultado.get('motivo', 'motivo desconocido')}",
        "motivo": resultado.get('motivo', ''),
        "venta_id": id_venta,
        "guardado_localmente": True,
    }), 502
@bp.route('/api/siigo/estado', methods=['GET'])
@login_required
def estado_siigo():
    """Indica si la integración con Siigo está configurada (para avisar en la UI)."""
    return jsonify({"configurado": siigo.configurado(),
                    "proxy_estatico": bool(siigo.FIXIE_URL)})

@bp.route('/api/ventas/despachos', methods=['GET'])
@login_required
def get_despachos():
    """Devuelve las ventas 'para_llevar' ordenadas por numero_pedido."""
    conn = get_db()
    rol = session.get('rol')
    estado_filtro = request.args.get('estado', '')
    query = """
        SELECT v.id, v.numero_pedido, v.fecha_dia, v.hora,
               c.nombre, c.telefono, c.cedula_nit,
               COALESCE(v.direccion_cliente, c.direccion, '') AS direccion,
               v.total_venta, v.tipo_pago, v.anulada,
               GROUP_CONCAT(p.nombre || ' (x' || dv.cantidad || ')', ', ') AS productos,
               COALESCE(v.estado_despacho, 'pendiente_preparar') AS estado_despacho,
               v.saldo_pendiente,
               COALESCE(v.despacho_preparado_por, ''),
               COALESCE(v.despacho_preparado_fecha, ''),
               COALESCE(v.despacho_entregado_por, ''),
               COALESCE(v.despacho_entregado_fecha, '')
        FROM ventas v
        JOIN clientes c ON c.id = v.id_cliente
        LEFT JOIN detalle_ventas dv ON dv.id_venta = v.id
        LEFT JOIN productos p ON p.id = dv.id_producto
        WHERE v.tipo_entrega = 'para_llevar' AND v.anulada = 0
    """
    params = []
    # El motocarguero solo ve lo que esta listo para entregar.
    if rol == 'motocarguero':
        query += " AND COALESCE(v.estado_despacho, 'pendiente_preparar') IN ('listo', 'entregado')"
    if estado_filtro:
        query += " AND COALESCE(v.estado_despacho, 'pendiente_preparar') = ?"
        params.append(estado_filtro)
    query += " GROUP BY v.id ORDER BY v.numero_pedido ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([{
        "id_venta": r[0], "numero_pedido": r[1], "fecha_dia": r[2], "hora": r[3],
        "cliente": r[4], "telefono": r[5], "cedula_nit": r[6], "direccion": r[7] or "",
        "total_venta": r[8], "tipo_pago": r[9], "anulada": bool(r[10]),
        "productos": r[11] or "", "estado_despacho": r[12],
        "saldo_pendiente": r[13] or 0,
        "preparado_por": r[14], "preparado_fecha": r[15],
        "entregado_por": r[16], "entregado_fecha": r[17],
        "cobrar_contra_entrega": round(float(r[13] or 0), 2),
    } for r in rows])


@bp.route('/api/ventas/<int:id_venta>/estado_despacho', methods=['PUT'])
@login_required
def cambiar_estado_despacho(id_venta):
    """Cambia el estado_despacho de una venta para_llevar."""
    from ..config import ESTADOS_DESPACHO, TRANSICIONES_DESPACHO
    data = request.json or {}
    nuevo = (data.get('estado_despacho') or '').strip()
    if nuevo not in ESTADOS_DESPACHO:
        return jsonify({"error": f"Estado inválido. Válidos: {', '.join(ESTADOS_DESPACHO)}"}), 400
    rol = session.get('rol')
    usuario = session.get('usuario')

    conn = get_db()
    venta = conn.execute(
        "SELECT tipo_entrega, COALESCE(estado_despacho, 'pendiente_preparar') FROM ventas WHERE id = ?",
        (id_venta,),
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[0] != 'para_llevar':
        conn.close()
        return jsonify({"error": "Esta venta no es de tipo para_llevar"}), 400
    # Validar que el rol pueda hacer esta transicion.
    estado_actual = venta[1]
    permitidos = TRANSICIONES_DESPACHO.get(estado_actual, {})
    if rol not in permitidos or nuevo not in permitidos[rol]:
        conn.close()
        return jsonify({
            "error": f"El rol '{rol}' no puede cambiar el despacho de '{estado_actual}' a '{nuevo}'"
        }), 403
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    updates = ["estado_despacho = :nuevo"]
    cambios = {'nuevo': nuevo, 'id': id_venta}
    if nuevo == 'listo':
        updates += ["despacho_preparado_por = :prep_por", "despacho_preparado_fecha = :prep_fecha"]
        cambios['prep_por'] = usuario
        cambios['prep_fecha'] = ahora
    elif nuevo == 'entregado':
        updates += ["despacho_entregado_por = :ent_por", "despacho_entregado_fecha = :ent_fecha"]
        cambios['ent_por'] = usuario
        cambios['ent_fecha'] = ahora
    conn.execute(f"UPDATE ventas SET {', '.join(updates)} WHERE id = :id", cambios)
    registrar_auditoria(conn, 'estado_despacho', 'venta', id_venta,
                        f'{estado_actual} -> {nuevo} por {usuario}')
    conn.commit()
    conn.close()
    return jsonify({"mensaje": f"Estado actualizado a '{nuevo}'", "estado_despacho": nuevo})


@bp.route('/api/ventas/<int:id_venta>', methods=['GET'])
@login_required
def get_factura_detalle(id_venta):
    conn = get_db()
    venta = conn.execute(
        """
        SELECT v.id, v.fecha_dia, v.hora, c.nombre, c.cedula_nit, c.telefono,
               v.total_venta, v.tipo_pago, v.id_cliente,
               COALESCE(v.direccion_cliente, c.direccion, ''),
               v.saldo_pendiente, v.anulada, v.motivo_anulacion, v.tipo_entrega,
               v.numero_pedido,
               COALESCE(v.siigo_estado, 'no_solicitada'), v.siigo_numero,
               v.siigo_cufe, v.siigo_pdf_url, v.siigo_error,
               COALESCE(c.email, ''), COALESCE(c.tipo_documento, 'CC'),
               COALESCE(v.subtotal_venta, 0), COALESCE(v.iva_valor, 0),
               COALESCE(v.iva_porcentaje, 0)
        FROM ventas v JOIN clientes c ON v.id_cliente = c.id WHERE v.id = ?
        """, (id_venta,)
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404

    detalles = conn.execute(
        """
        SELECT p.nombre, p.dimensiones, dv.cantidad, dv.precio_unitario, dv.subtotal,
               dv.id_producto
        FROM detalle_ventas dv JOIN productos p ON dv.id_producto = p.id
        WHERE dv.id_venta = ?
        """, (id_venta,)
    ).fetchall()
    conn.close()

    # Datos del negocio para el encabezado de la tirilla.
    conn2 = get_db()
    negocio = conn2.execute(
        "SELECT nombre, nit, telefono, direccion FROM configuracion WHERE id = 1"
    ).fetchone()
    conn2.close()

    return jsonify({
        "id": venta[0], "fecha_dia": venta[1], "hora": venta[2], "cliente": venta[3],
        "cedula_nit": venta[4], "telefono": venta[5], "total_venta": venta[6],
        "tipo_pago": venta[7], "id_cliente": venta[8], "direccion": venta[9] or "",
        "saldo_pendiente": venta[10] or 0,
        "anulada": bool(venta[11]),
        "motivo_anulacion": venta[12] or "",
        "tipo_entrega": venta[13] or "entrega_inmediata",
        "numero_pedido": venta[14],
        "siigo_estado": venta[15] or "no_solicitada",
        "siigo_numero": venta[16] or "",
        "siigo_cufe": venta[17] or "",
        "siigo_pdf_url": venta[18] or "",
        "siigo_error": venta[19] or "",
        "email": venta[20] or "",
        "tipo_documento": venta[21] or "CC",
        "subtotal_venta": venta[22] or 0,
        "iva_valor": venta[23] or 0,
        "iva_porcentaje": venta[24] or 0,
        "negocio": {
            "nombre": (negocio[0] if negocio else "Ferretería y Fábrica de Flejes"),
            "nit": (negocio[1] if negocio else "") or "",
            "telefono": (negocio[2] if negocio else "") or "",
            "direccion": (negocio[3] if negocio else "") or "",
        },
        "items": [{
            "nombre": d[0] + (f" ({d[1]})" if d[1] else ""), "cantidad": d[2],
            "precio_unitario": d[3], "subtotal": d[4], "id_producto": d[5],
        } for d in detalles],
    })


@bp.route('/api/ventas/<int:id_venta>/anular', methods=['PUT'])
@admin_required
def anular_venta(id_venta):
    data = request.json or {}
    motivo = str(data.get('motivo', '')).strip()
    if not motivo:
        return jsonify({"error": "Debe indicar el motivo de la anulación"}), 400

    conn = get_db()
    venta = conn.execute(
        "SELECT total_venta, saldo_pendiente, anulada FROM ventas WHERE id = ?", (id_venta,)
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[2]:
        conn.close()
        return jsonify({"error": "La venta ya está anulada"}), 400
    if venta[1] > 0 and venta[1] != venta[0]:
        conn.close()
        return jsonify({"error": "No se puede anular una venta de crédito que ya tiene abonos"}), 400

    detalles = conn.execute(
        "SELECT id_producto, cantidad FROM detalle_ventas WHERE id_venta = ?", (id_venta,)
    ).fetchall()
    for id_producto, cantidad in detalles:
        conn.execute("UPDATE productos SET stock_actual = stock_actual + ? WHERE id = ?",
                     (cantidad, id_producto))
        conn.execute(
            "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
            "VALUES (:id, 'entrada', :cantidad, :motivo, :usuario, :fecha)",
            {'id': id_producto, 'cantidad': cantidad, 'motivo': f'Anulación venta #{id_venta}',
             'usuario': session['usuario'], 'fecha': _ahora()},
        )
    conn.execute("UPDATE ventas SET anulada = 1, motivo_anulacion = ?, saldo_pendiente = 0 WHERE id = ?",
                 (motivo, id_venta))
    registrar_auditoria(conn, 'anular', 'venta', id_venta, motivo)
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Venta anulada y stock restaurado"})


@bp.route('/api/ventas/<int:id_venta>/cliente', methods=['PUT'])
@login_required
def editar_cliente_factura(id_venta):
    data = request.json
    nombre = data.get('nombre')
    if not nombre:
        return jsonify({"error": "El nombre es obligatorio"}), 400

    conn = get_db()
    res = conn.execute("SELECT id_cliente FROM ventas WHERE id = ?", (id_venta,)).fetchone()
    if not res:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    id_cliente = res[0]

    conn.execute(
        "UPDATE clientes SET nombre = ?, cedula_nit = ?, telefono = ?, direccion = ?, "
        "email = ?, tipo_documento = ? WHERE id = ?",
        (nombre, data.get('cedula_nit'), data.get('telefono'), data.get('direccion', ''),
         str(data.get('email', '')).strip(),
         (str(data.get('tipo_documento', '')).strip() or 'CC'), id_cliente),
    )
    conn.execute("UPDATE ventas SET direccion_cliente = ? WHERE id = ?",
                 (data.get('direccion', ''), id_venta))
    conn.commit()
    conn.close()
    return jsonify({"mensaje": "Datos del cliente y dirección actualizados correctamente"}), 200
@bp.route('/api/ventas/<int:id_venta>/detalle', methods=['PUT'])
@admin_required
def editar_detalle_factura(id_venta):
    """Edita los precios y cantidades de una venta ya registrada.

    Solo el administrador puede hacerlo. Recalcula el total con el IVA vigente y
    ajusta el inventario cuando cambia una cantidad (devuelve lo anterior y
    descuenta lo nuevo, para no descuadrar el stock). Deja rastro en auditoria.

    Cuerpo esperado:
        { "items": [ {"id_producto": 12, "cantidad": 3, "precio_unitario": 5000}, ... ] }
    """
    data = request.json or {}
    items = data.get('items') or []
    if not items:
        return jsonify({"error": "Debe enviar al menos un producto"}), 400
    conn = get_db()
    cursor = conn.cursor()
    venta = cursor.execute(
        "SELECT COALESCE(anulada, 0), COALESCE(total_venta, 0) FROM ventas WHERE id = ?", (id_venta,)
    ).fetchone()
    if not venta:
        conn.close()
        return jsonify({"error": "Venta no encontrada"}), 404
    if venta[0]:
        conn.close()
        return jsonify({"error": "No se puede editar una venta anulada"}), 400
    # Detalle anterior (para ajustar stock y detectar cambios).
    anteriores = {
        r[0]: r[1] for r in cursor.execute(
            "SELECT id_producto, cantidad FROM detalle_ventas WHERE id_venta = ?", (id_venta,)
        ).fetchall()
    }

    # Reemplaza el detalle por el enviado, validando cada linea.
    subtotal_venta = 0.0
    nuevas_cantidades = {}
    filas = []
    for item in items:
        try:
            id_prod = int(item.get('id_producto'))
            cantidad = int(item.get('cantidad'))
            precio = float(item.get('precio_unitario'))
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "Cantidad o precio inválidos en una línea"}), 400
        if cantidad <= 0 or precio < 0:
            conn.close()
            return jsonify({"error": "La cantidad debe ser mayor que cero y el precio no puede ser negativo"}), 400
        if not cursor.execute("SELECT 1 FROM productos WHERE id = ?", (id_prod,)).fetchone():
            conn.close()
            return jsonify({"error": f"Producto ID {id_prod} no encontrado"}), 400
        subtotal_venta += cantidad * precio
        nuevas_cantidades[id_prod] = nuevas_cantidades.get(id_prod, 0) + cantidad
        filas.append((id_prod, cantidad, precio))

    # Ajuste de inventario: se devuelve lo que tenia la venta antes y se
    # descuenta lo nuevo, por producto (evita descuadres si solo cambio el precio).
    for id_prod in set(list(anteriores.keys()) + list(nuevas_cantidades.keys())):
        delta = nuevas_cantidades.get(id_prod, 0) - anteriores.get(id_prod, 0)
        if delta:
            cursor.execute(
                "UPDATE productos SET stock_actual = COALESCE(stock_actual, 0) - ? WHERE id = ?",
                (delta, id_prod),
            )
            cursor.execute(
                "INSERT INTO movimientos_inventario (id_producto, tipo, cantidad, motivo, usuario, fecha) "
                "VALUES (:id, :tipo, :cantidad, :motivo, :usuario, :fecha)",
                {'id': id_prod, 'tipo': 'salida' if delta > 0 else 'entrada',
                 'cantidad': abs(delta), 'motivo': f'Ajuste por edición de venta #{id_venta}',
                 'usuario': session['usuario'], 'fecha': _ahora()},
            )

    cursor.execute("DELETE FROM detalle_ventas WHERE id_venta = ?", (id_venta,))
    for id_prod, cantidad, precio in filas:
        cursor.execute(
            "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal) "
            "VALUES (?, ?, ?, ?, ?)",
            (id_venta, id_prod, cantidad, precio, cantidad * precio),
        )

    # Recalcula total con el IVA vigente del negocio.
    iva_porcentaje, iva_activo = _leer_config_iva(cursor)
    iva_valor = round(subtotal_venta * iva_porcentaje / 100) if (iva_activo and iva_porcentaje > 0) else 0
    total_venta = subtotal_venta + iva_valor
    # Si era a credito, el saldo sigue al nuevo total (menos los abonos hechos).
    tipo_pago = cursor.execute("SELECT tipo_pago FROM ventas WHERE id = ?", (id_venta,)).fetchone()[0]
    if tipo_pago == 'credito':
        abonado = cursor.execute(
            "SELECT COALESCE(SUM(monto), 0) FROM abonos WHERE id_venta = ?", (id_venta,)
        ).fetchone()[0]
        saldo_pendiente = max(0.0, total_venta - abonado)
    else:
        saldo_pendiente = 0.0
    cursor.execute(
        "UPDATE ventas SET total_venta = ?, subtotal_venta = ?, iva_valor = ?, "
        "iva_porcentaje = ?, saldo_pendiente = ? WHERE id = ?",
        (total_venta, subtotal_venta, iva_valor, iva_porcentaje, saldo_pendiente, id_venta),
    )
    registrar_auditoria(conn, 'editar_detalle', 'venta', id_venta,
                        f'Factura editada. Nuevo total: {total_venta}')
    conn.commit()
    conn.close()
    return jsonify({
        "mensaje": "Factura actualizada correctamente",
        "subtotal_venta": subtotal_venta, "iva_valor": iva_valor,
        "iva_porcentaje": iva_porcentaje, "total_venta": total_venta,
        "saldo_pendiente": saldo_pendiente,
    }), 200


# ── Créditos y abonos ─────────────────────────
@bp.route('/api/creditos', methods=['GET'])
@login_required
def get_creditos():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT c.id, c.nombre, c.telefono, SUM(v.saldo_pendiente) as deuda_total, c.direccion,
               c.cedula_nit
        FROM clientes c
        LEFT JOIN ventas v ON c.id = v.id_cliente AND v.saldo_pendiente > 0
        GROUP BY c.id
        """
    ).fetchall()
    conn.close()
    return jsonify([{"id_cliente": r[0], "nombre": r[1], "telefono": r[2],
                     "deuda_total": r[3], "direccion": r[4] or "",
                     "cedula_nit": r[5] or ""} for r in rows])


@bp.route('/api/abonos', methods=['POST'])
@login_required
def registrar_abono():
    data = request.json
    id_cliente = data['id_cliente']
    monto_abono = float(data['monto'])
    if monto_abono <= 0:
        return jsonify({"error": "El monto del abono debe ser mayor a cero"}), 400

    conn = get_db()
    cursor = conn.cursor()
    ventas_pendientes = cursor.execute(
        "SELECT id, saldo_pendiente FROM ventas WHERE id_cliente = ? AND saldo_pendiente > 0 ORDER BY id ASC",
        (id_cliente,),
    ).fetchall()
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
            cursor.execute("UPDATE ventas SET saldo_pendiente = saldo_pendiente - ? WHERE id = ?",
                           (monto_restante, venta_id))
            monto_restante = 0

    fecha_hoy = _ahora()
    cursor.execute("INSERT INTO abonos (id_cliente, monto, fecha) VALUES (?, ?, ?)",
                   (id_cliente, monto_abono, fecha_hoy))
    id_abono = cursor.lastrowid
    registrar_auditoria(conn, 'registrar', 'abono', id_abono, f'Abono de {monto_abono:.2f}')

    cliente = cursor.execute(
        "SELECT nombre, cedula_nit, telefono, direccion FROM clientes WHERE id = ?", (id_cliente,)
    ).fetchone()
    saldo_despues = deuda_total - monto_abono
    conn.commit()
    conn.close()

    return jsonify({
        "mensaje": "Abono registrado con éxito",
        "abono": {
            "id": id_abono, "fecha": fecha_hoy, "cliente": cliente[0],
            "cedula_nit": cliente[1] or "", "telefono": cliente[2] or "",
            "direccion": cliente[3] or "", "deuda_anterior": deuda_total,
            "monto": monto_abono, "saldo_pendiente": saldo_despues,
        },
    }), 201


@bp.route('/api/abonos/<int:id_cliente>', methods=['GET'])
@login_required
def historial_abonos(id_cliente):
    conn = get_db()
    cursor = conn.cursor()
    cliente = cursor.execute(
        "SELECT nombre, cedula_nit, telefono, direccion FROM clientes WHERE id = ?", (id_cliente,)
    ).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"error": "Cliente no encontrado"}), 404

    deuda_original = cursor.execute(
        "SELECT COALESCE(SUM(total_venta), 0) FROM ventas WHERE id_cliente = ? AND tipo_pago = 'credito'",
        (id_cliente,),
    ).fetchone()[0] or 0
    abonos = cursor.execute(
        "SELECT id, monto, fecha FROM abonos WHERE id_cliente = ? ORDER BY id ASC", (id_cliente,)
    ).fetchall()
    conn.close()

    historial = []
    total_abonado = 0
    for id_abono, monto, fecha in abonos:
        deuda_antes = max(deuda_original - total_abonado, 0)
        total_abonado += monto
        historial.append({
            "id": id_abono, "fecha": fecha, "cliente": cliente[0],
            "cedula_nit": cliente[1] or "", "telefono": cliente[2] or "",
            "direccion": cliente[3] or "", "deuda_anterior": deuda_antes,
            "monto": monto, "saldo_pendiente": max(deuda_original - total_abonado, 0),
        })

    return jsonify({
        "cliente": {"id": id_cliente, "nombre": cliente[0], "cedula_nit": cliente[1] or "",
                    "telefono": cliente[2] or "", "direccion": cliente[3] or ""},
        "abonos": historial,
    })


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
