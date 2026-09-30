"""Panel del desarrollador (SuperAdmin): quién tiene el sistema y en qué estado.

Este blueprint es la pantalla del DESARROLLOADOR del software, no la de una
ferretería. Distinguir ambas cosas no es un detalle de permisos: aquí se ven los
archivos de base de datos de todos los clientes y se puede suspender el acceso de
cualquiera. Por eso exige el rol `superadmin`, un rol que la instanciación de
cada ferretería nunca tiene.

Lo que hace, en concreto:
  - Lista los clientes provisionados y el estado real de cada instancia.
  - Crea una instancia nueva: copia la semilla, siembra los datos y da de alta al
    dueño.
  - Activa, suspende o cancela el acceso (sin tocar sus datos).
  - Verifica que la instancia esté sana y muestra qué le falta configurar.
  - Reporta instancias huérfanas: archivos sin registro.

Límite importante: NO borra archivos de instancia. La base de una ferretería
puede tener ventas reales y documentos ya emitidos; eso se borra en el sistema de
archivos, a mano y con respaldo.
"""
from flask import Blueprint, jsonify, render_template, request, session

from ..config import aviso_persistencia_instancias, resolver_directorio_instancias
from ..db import get_db
from ..security import superadmin_required
from ..services import padron_ferreterias as padron

bp = Blueprint('superadmin', __name__)

# Se resuelve en cada petición, no al importar. Motivo: si el despliegue cambia
# (o el test cambia el entorno) la ruta debe seguir siendo la correcta sin
# reiniciar el proceso, y porque resolverla al importar ya fijó en tests
# anteriores un valor que luego no coincidía con la base real.
def _directorio():
    return resolver_directorio_instancias()


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)
    # Un solo aviso al arrancar, en los logs del servidor. No es una excepción:
    # la app debe seguir levantándose para que eldeveloper pueda entrar a
    # corregir la configuración.
    aviso = aviso_persistencia_instancias()
    if aviso:
        app.logger.warning(aviso)


def _datos(request_data, campo, por_defecto=''):
    """Lee un campo del JSON tolerando que no venga."""
    return (request_data.get(campo) or por_defecto)


# ═════════════════════════════
# Pantalla
# ═════════════════════════════

@bp.route('/superadmin')
@superadmin_required
def panel():
    """La página del panel."""
    return render_template('superadmin.html', usuario=session['usuario'])


# ═════════════════════════════
# API
# ═════════════════════════════

@bp.route('/api/superadmin/ferreterias', methods=['GET'])
@superadmin_required
def api_listar():
    conn = get_db()
    try:
        registros = padron.listar(conn, _directorio(),
                                  estado=request.args.get('estado') or None)
        cifras = _resumen_con_aviso(conn)
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify({'ferreterias': registros, 'resumen': cifras})


@bp.route('/api/superadmin/ferreterias', methods=['POST'])
@superadmin_required
def api_crear():
    """Provisiona una instancia nueva y la registra en el padrón."""
    datos = request.json or {}
    faltantes = [
        campo for campo in ('nombre', 'usuario_dueno', 'clave_dueno')
        if not str(datos.get(campo) or '').strip()
    ]
    if faltantes:
        return jsonify({
            'error': 'Faltan datos obligatorios: ' + ', '.join(faltantes),
        }), 400

    conn = get_db()
    try:
        registro = padron.crear_ferreteria(
            conn=conn,
            directorio=_directorio(),
            nombre=str(datos['nombre']).strip(),
            usuario_dueno=str(datos['usuario_dueno']).strip(),
            clave_dueno=str(datos['clave_dueno']),
            nit=_datos(datos, 'nit'),
            digito_verificacion=_datos(datos, 'digito_verificacion'),
            direccion=_datos(datos, 'direccion'),
            telefono=_datos(datos, 'telefono'),
            email=_datos(datos, 'email'),
            notas=_datos(datos, 'notas'),
        )
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()

    return jsonify({
        'mensaje': (
            f'Instancia creada: {registro["archivo"]}. '
            f'El dueño entra con el usuario "{registro["usuario_dueno"]}".'
        ),
        'ferreteria': registro,
        # Avisos de una siembra incompleta (p. ej. semilla sin columnas del
        # proveedor). No impiden operar, pero hay que verlos.
        'avisos': registro.get('avisos', []),
    }), 201


@bp.route('/api/superadmin/ferreterias/<int:id_ferreteria>', methods=['PUT'])
@superadmin_required
def api_editar(id_ferreteria):
    """Edita los datos de contacto o cambia el estado del acceso."""
    datos = request.json or {}
    conn = get_db()
    try:
        if datos.get('estado'):
            registro = padron.cambiar_estado(
                conn, id_ferreteria, datos['estado'],
                _directorio(),
                notas=_datos(datos, 'notas'),
            )
            mensaje = f'Estado cambiado a "{datos["estado"]}".'
        else:
            campos = {k: datos[k] for k in
                      ('nombre', 'nit', 'telefono', 'email', 'direccion', 'notas')
                      if k in datos}
            padron.editar(conn, id_ferreteria, **campos)
            registro = padron.obtener(conn, id_ferreteria, _directorio())
            mensaje = 'Datos actualizados.'
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify({'mensaje': mensaje, 'ferreteria': registro})


@bp.route('/api/superadmin/ferreterias/<int:id_ferreteria>', methods=['DELETE'])
@superadmin_required
def api_eliminar(id_ferreteria):
    """Quita el registro del padrón. El archivo de la instancia NO se borra."""
    conn = get_db()
    try:
        resultado = padron.eliminar_registro(conn, id_ferreteria)
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify(resultado)


@bp.route('/api/superadmin/ferreterias/<int:id_ferreteria>/verificar')
@superadmin_required
def api_verificar(id_ferreteria):
    """Comprueba la instancia y devuelve qué le falta configurar."""
    conn = get_db()
    try:
        resultado = padron.verificar(conn, id_ferreteria, _directorio())
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify(resultado)


@bp.route('/api/superadmin/ferreterias/<int:id_ferreteria>/historial')
@superadmin_required
def api_historial(id_ferreteria):
    conn = get_db()
    try:
        return jsonify({'historial': padron.historial(conn, id_ferreteria)})
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()


@bp.route('/api/superadmin/huerfanos')
@superadmin_required
def api_huerfanos():
    """Archivos .db de instancia que no están registrados en el padrón."""
    conn = get_db()
    try:
        return jsonify({'huerfanos': padron.listar_huerfanos(
            conn, _directorio())})
    finally:
        conn.close()


@bp.route('/api/superadmin/resumen')
@superadmin_required
def api_resumen():
    conn = get_db()
    try:
        return jsonify(_resumen_con_aviso(conn))
    finally:
        conn.close()


def _resumen_con_aviso(conn):
    """El resumen del padrón, más el aviso de persistencia si lo hay.

    El aviso viaja en la respuesta y no solo en los logs: el desarrollador que
    abre el panel es quien puede arreglarlo, y un aviso que solo existe en un
    log de un servidor es un aviso que nadie lee hasta que ya se perdió una base.
    """
    resultado = padron.resumen(conn, _directorio())
    resultado['directorio_instancias'] = _directorio()
    resultado['aviso_persistencia'] = aviso_persistencia_instancias()
    return resultado