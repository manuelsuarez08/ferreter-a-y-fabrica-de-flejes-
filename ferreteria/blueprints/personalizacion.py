"""API de personalización: lo que el dueño de la ferretería configura sin ayuda.

Todo lo que se expone aquí ES de la ferretería, no del desarrollador: su logo, su
nombre comercial, el mensaje de su ticket. El panel SuperAdmin queda aparte.

Punto sensible: la subida del logo. Se valida por la FIRMA binaria del archivo, no
por su extensión ni por su nombre. Un logo se sirve desde `static/`, así que un
archivo con código dentro y extensión `.png` sería una vía para ejecutar
JavaScript en la página del POS. El nombre con el que llega el archivo no se usa
para nada.
"""
from flask import Blueprint, jsonify, render_template, request, session

from ..db import get_db
from ..security import login_required
from ..services import personalizacion as marca

bp = Blueprint('personalizacion', __name__)


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)


def _es_admin():
    return session.get('rol') == 'admin'


# ═══════════════════════════════════════════
# Lectura
# ═══════════════════════════════════════════

@bp.route('/api/personalizacion', methods=['GET'])
@login_required
def leer():
    """La personalización actual.

    Es de lectura para todos los roles: el cajero también necesita el nombre
    comercial y el mensaje del ticket para mostrarlos.
    """
    conn = get_db()
    try:
        return jsonify(marca.leer(conn))
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()


# ═══════════════════════════════════════════
# Escritura
# ═══════════════════════════════════════════

@bp.route('/api/personalizacion', methods=['PUT'])
@login_required
def guardar():
    """Guarda nombre comercial, mensaje del pie, tamaño del logo y color."""
    if not _es_admin():
        return jsonify({
            'error': 'Solo el administrador puede cambiar la personalización.',
        }), 403

    conn = get_db()
    try:
        datos = marca.guardar_datos(conn, request.json or {})
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify({'mensaje': 'Personalización guardada.', **datos})


@bp.route('/api/personalizacion/logo', methods=['POST'])
@login_required
def subir_logo():
    """Sube el logo del negocio.

    Se acepta `multipart/form-data` con el campo `logo`. Se usa
    `request.files`, no el cuerpo crudo, porque es la única forma de que Flask
    controle el tamaño de la subida en vez de confiar en un Content-Length.
    """
    if not _es_admin():
        return jsonify({
            'error': 'Solo el administrador puede cambiar el logo.',
        }), 403

    archivo = request.files.get('logo')
    if archivo is None or not archivo.filename:
        return jsonify({'error': 'No se recibió ningún archivo.'}), 400

    # Límite de la petición: el doble del tope del archivo. Si se pasa, Flask
    # corta la subida con un 413 antes de que la aplicación la lea entera, que es
    # lo que evita que alguien mande 500 MB para que se descarten.
    contenido = archivo.read()

    conn = get_db()
    try:
        datos = marca.guardar_logo(conn, contenido, archivo.filename)
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    except OSError as error:
        return jsonify({
            'error': f'No se pudo escribir el logo en el disco: {error}',
        }), 500
    finally:
        conn.close()
    return jsonify({'mensaje': 'Logo actualizado.', **datos})


@bp.route('/api/personalizacion/logo/restaurar', methods=['POST'])
@login_required
def restaurar_logo():
    """Vuelve al logo anterior, si lo hubo."""
    if not _es_admin():
        return jsonify({'error': 'Solo el administrador puede hacer esto.'}), 403

    conn = get_db()
    try:
        datos = marca.restaurar_logo_anterior(conn)
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify({'mensaje': 'Se restauró el logo anterior.', **datos})


@bp.route('/api/personalizacion/logo', methods=['DELETE'])
@login_required
def quitar_logo():
    """Quita el logo y vuelve al del sistema."""
    if not _es_admin():
        return jsonify({'error': 'Solo el administrador puede hacer esto.'}), 403

    conn = get_db()
    try:
        datos = marca.quitar_logo(conn)
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify({'mensaje': 'Logo quitado.', **datos})


# ═══════════════════════════════════════════
# Pantalla
# ═══════════════════════════════════════════

@bp.route('/personalizacion')
@login_required
def pantalla():
    """La pantalla de Personalización del Negocio.

    Se abre a cualquier usuario con sesión, pero solo el administrador puede
    GUARDAR. Un cajero que llegue a mirar el formulario no puede cambiar el logo
    de la ferretería; la interfaz le oculta los botones de escritura.
    """
    conn = get_db()
    try:
        datos = marca.leer(conn)
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return render_template(
        'personalizacion.html',
        usuario=session['usuario'],
        rol=session.get('rol', 'empleado'),
        negocio=datos,
        puede_editar=_es_admin(),
    )


# ═══════════════════════════════════════════
# Datos para tickets y facturas
# ═══════════════════════════════════════════

@bp.route('/api/personalizacion/documento')
@login_required
def datos_documento():
    """Los mismos datos, con los nombres que espera el generador de comprobantes.

    Vive separada de `leer()` porque quien arma el ticket necesita campos con
    nombres estables y valores ya resueltos (por ejemplo `logo_mostrar` ya
    combina "el dueño lo activó" y "el archivo existe"), no el diccionario de la
    pantalla de ajustes.
    """
    conn = get_db()
    try:
        return jsonify(marca.para_documento(conn))
    except marca.ErrorPersonalizacion as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()