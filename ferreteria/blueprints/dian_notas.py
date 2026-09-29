"""Rutas de las Notas Crédito Electrónicas y los Documentos Soporte.

Completa el módulo de facturación DIAN con los dos documentos que permiten
operar de forma legal:

  - NOTA CRÉDITO: corrige una venta YA EMITIDA. Es la única forma de revertirla
    (marcarla como anulada dejaría un documento válido en el catálogo de la
    DIAN). La venta no se revierte hasta que la DIAN ACEPTA la nota.
  - DOCUMENTO SOPORTE: respalda compras a proveedores informales que no
    entregan factura electrónica.

Endpoints:
    GET    /api/dian/nota-credito/motivos            catálogo de motivos
    POST   /api/dian/nota-credito/<id_venta>         emite la nota y revierte
    GET    /api/dian/notas-credito                   lista las notas emitidas
    GET    /api/dian/notas-credito/<id>/xml          descarga el XML firmado
    GET    /api/proveedores-informales               lista proveedores
    POST   /api/proveedores-informales               crea un proveedor
    PUT    /api/proveedores-informales/<id>          actualiza un proveedor
    POST   /api/dian/documento-soporte               emite el soporte
    GET    /api/dian/documentos-soporte              lista los soportes

Emitir documentos exige ser admin; consultarlos, con sesión.
"""
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from ..db import get_db
from ..security import admin_required, login_required
from ..services import dian_documento_soporte
from ..services import dian_nota_credito
from ..services import dian_notas
from ..services.auditoria import registrar_auditoria
from ..services.dian_emision import ErrorEmision

bp = Blueprint('dian_notas', __name__)


# ═════════════════════════════════════════════════════
# Notas crédito electrónicas
# ═════════════════════════════════════════════════════
@bp.route('/api/dian/nota-credito/motivos', methods=['GET'])
@login_required
def motivos_nota_credito():
    """Catálogo de motivos que acepta la DIAN para una nota crédito."""
    return jsonify({
        'motivos': [{'codigo': c, 'descripcion': d}
                    for c, d in dian_notas.MOTIVOS_NOTA_CREDITO],
    })


@bp.route('/api/dian/nota-credito/<int:id_venta>', methods=['POST'])
@admin_required
def emitir_nota_credito(id_venta):
    """Emite la Nota Crédito Electrónica de una venta ya emitida.

    Body:
        {"motivo_codigo": "1", "motivo_descripcion": "Devolución total",
         "contingencia": false}

    Si la DIAN ACEPTA la nota, la venta se revierte y el stock vuelve al
    inventario. Si la rechaza o no hay red, la venta NO se toca: el documento
    original sigue vigente.
    """
    data = request.json or {}
    resultado = None
    conn = get_db()
    try:
        resultado = dian_nota_credito.emitir_nota_credito(
            conn, id_venta,
            motivo_codigo=str(data.get('motivo_codigo') or '1'),
            motivo_descripcion=str(data.get('motivo_descripcion') or '').strip(),
            contingencia=bool(data.get('contingencia')),
        )
        registrar_auditoria(
            conn, 'emitir', 'nota_credito', resultado.get('id_documento'),
            f"Venta #{id_venta} -> nota crédito {resultado.get('numero')} "
            f"({resultado.get('estado')})")
        conn.commit()
    except ErrorEmision as error:
        conn.close()
        return jsonify({'error': str(error)}), 400
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return jsonify(resultado), 201


@bp.route('/api/dian/notas-credito', methods=['GET'])
@login_required
def listar_notas_credito():
    """Lista las notas crédito emitidas, con la venta a la que pertenecen."""
    limite = min(max(int(request.args.get('limite', 100) or 100), 1), 500)
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT d.id, d.id_venta, d.numero, d.cuide, d.estado,
                   d.codigo_dian, d.descripcion_dian, d.motivo,
                   d.documento_referido, d.valor_total, d.valor_iva,
                   d.fecha_generacion, v.numero_dian AS numero_venta
            FROM documentos_electronicos d
            JOIN ventas v ON v.id = d.id_venta
            WHERE d.tipo_documento = 'NC'
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify({'notas': [dict(f) for f in filas]})


@bp.route('/api/dian/notas-credito/<int:id_documento>/xml', methods=['GET'])
@login_required
def xml_nota_credito(id_documento):
    """Descarga el XML FIRMADO de la nota crédito."""
    conn = get_db()
    fila = conn.execute(
        "SELECT numero, xml_firmado, xml FROM documentos_electronicos"
        " WHERE id = ? AND tipo_documento = 'NC'",
        (id_documento,),
    ).fetchone()
    conn.close()
    if not fila:
        return jsonify({'error': 'Nota crédito no encontrada'}), 404
    contenido = fila[1] or fila[2]
    if not contenido:
        return jsonify({'error': 'La nota crédito no tiene XML'}), 404
    return send_file(
        BytesIO(contenido.encode('utf-8')),
        mimetype='application/xml',
        as_attachment=True,
        download_name=f'{fila[0]}.xml',
    )


# ═════════════════════════════════════════════════════
# Proveedores informales
# ═════════════════════════════════════════════════════
@bp.route('/api/proveedores-informales', methods=['GET'])
@login_required
def listar_proveedores():
    conn = get_db()
    try:
        filas = conn.execute(
            "SELECT * FROM proveedores_informales WHERE activo = 1 ORDER BY nombre"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({'proveedores': [dict(f) for f in filas]})


@bp.route('/api/proveedores-informales', methods=['POST'])
@admin_required
def crear_proveedor():
    """Registra un proveedor que no está obligado a facturar electrónicamente."""
    data = request.json or {}
    nombre = str(data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'error': 'El nombre del proveedor es obligatorio'}), 400

    conn = get_db()
    from datetime import datetime
    cur = conn.execute(
        """
        INSERT INTO proveedores_informales
            (nombre, tipo_documento, numero_documento, telefono, direccion,
             municipio, departamento, tipo_suministro, notas, creado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (nombre,
         str(data.get('tipo_documento') or 'CC'),
         str(data.get('numero_documento') or ''),
         str(data.get('telefono') or ''),
         str(data.get('direccion') or ''),
         str(data.get('municipio') or ''),
         str(data.get('departamento') or ''),
         str(data.get('tipo_suministro') or 'materiales'),
         str(data.get('notas') or ''),
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
    )
    registrar_auditoria(conn, 'crear', 'proveedor_informal', cur.lastrowid,
                        f'Proveedor informal: {nombre}')
    conn.commit()
    conn.close()
    return jsonify({'id': cur.lastrowid, 'mensaje': 'Proveedor registrado'}), 201


@bp.route('/api/proveedores-informales/<int:id_proveedor>', methods=['PUT'])
@admin_required
def actualizar_proveedor(id_proveedor):
    data = request.json or {}
    conn = get_db()
    cur = conn.execute(
        """
        UPDATE proveedores_informales
        SET nombre = COALESCE(?, nombre),
            tipo_documento = COALESCE(?, tipo_documento),
            numero_documento = COALESCE(?, numero_documento),
            telefono = COALESCE(?, telefono),
            direccion = COALESCE(?, direccion),
            municipio = COALESCE(?, municipio),
            departamento = COALESCE(?, departamento),
            tipo_suministro = COALESCE(?, tipo_suministro),
            notas = COALESCE(?, notas),
            actualizado = ?
        WHERE id = ?
        """,
        (str(data.get('nombre') or '') or None,
         str(data.get('tipo_documento') or '') or None,
         str(data.get('numero_documento') or '') or None,
         str(data.get('telefono') or '') or None,
         str(data.get('direccion') or '') or None,
         str(data.get('municipio') or '') or None,
         str(data.get('departamento') or '') or None,
         str(data.get('tipo_suministro') or '') or None,
         str(data.get('notas') or '') or None,
         __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         id_proveedor),
    )
    if not cur.rowcount:
        conn.close()
        return jsonify({'error': 'Proveedor no encontrado'}), 404
    conn.commit()
    conn.close()
    return jsonify({'mensaje': 'Proveedor actualizado'})


# ═════════════════════════════════════════════════════
# Documento Soporte a No Obligados a Facturar
# ═════════════════════════════════════════════════════
@bp.route('/api/dian/documento-soporte', methods=['POST'])
@admin_required
def emitir_documento_soporte():
    """Emite un Documento Soporte a No Obligados a Facturar.

    Body:
        {"id_proveedor": 1,
         "documento_proveedor": "001-045",
         "items": [{"id_producto": 12, "cantidad": 2, "precio_unitario": 80000}],
         "contingencia": false}
    """
    data = request.json or {}
    id_proveedor = data.get('id_proveedor')
    items = data.get('items') or []
    if not id_proveedor:
        return jsonify({'error': 'Debe indicar el proveedor (id_proveedor)'}), 400
    if not items:
        return jsonify({'error': 'Debe indicar al menos una línea de compra'}), 400

    conn = get_db()
    try:
        resultado = dian_documento_soporte.emitir_documento_soporte(
            conn, int(id_proveedor), items,
            documento_proveedor=str(data.get('documento_proveedor') or ''),
            contingencia=bool(data.get('contingencia')),
        )
        registrar_auditoria(
            conn, 'emitir', 'documento_soporte', resultado.get('id_documento'),
            f"Proveedor #{id_proveedor} -> soporte {resultado.get('numero')} "
            f"({resultado.get('estado')})")
        conn.commit()
    except ErrorEmision as error:
        conn.close()
        return jsonify({'error': str(error)}), 400
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return jsonify(resultado), 201


@bp.route('/api/dian/documentos-soporte', methods=['GET'])
@login_required
def listar_documentos_soporte():
    """Lista los documentos soporte emitidos."""
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT d.id, d.id_proveedor, p.nombre AS proveedor, d.numero,
                   d.cuide, d.estado, d.codigo_dian, d.descripcion_dian,
                   d.documento_proveedor, d.valor_total, d.valor_iva,
                   d.fecha_generacion
            FROM documentos_soporte d
            LEFT JOIN proveedores_informales p ON p.id = d.id_proveedor
            ORDER BY d.id DESC
            LIMIT 200
            """
        ).fetchall()
    finally:
        conn.close()
    return jsonify({'documentos': [dict(f) for f in filas]})


def registrar(app):
    app.register_blueprint(bp)
