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
import os

from flask import (
    Blueprint, jsonify, render_template, request, send_from_directory, session,
)

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


@bp.route('/api/superadmin/ferreterias/<int:id_ferreteria>/pago', methods=['PUT'])
@superadmin_required
def api_pago(id_ferreteria):
    """Actualiza plan, fecha de vencimiento y días de prórroga.

    Es un endpoint aparte (no el PUT general) porque estos campos tienen reglas
    propias: el plan se valida contra la lista, la fecha contra el formato, y
    tocar el pago puede cambiar el estado del cliente. Mezclarlos con la
    edición de contacto haría que un cambio de teléfono suspendiera a alguien.
    """
    datos = request.json or {}
    conn = get_db()
    try:
        pago = padron.configurar_pago(
            conn, id_ferreteria,
            plan=datos.get('plan'),
            fecha_vencimiento=datos.get('fecha_vencimiento'),
            dias_prorroga=datos.get('dias_prorroga'),
            aplicar_estado=datos.get('aplicar_estado', True),
        )
    except padron.ErrorPadron as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify({'mensaje': 'Datos de pago actualizados.', 'pago': pago})


@bp.route('/api/superadmin/vencimientos', methods=['GET'])
@superadmin_required
def api_vencimientos():
    """Qué cambiaría si se aplicaran los vencimientos. NO escribe nada.

    Se separa del listado a propósito: el desarrollador tiene que ver el efecto
    antes de aceptarlo, sobre todo porque suspender es una medida real contra un
    cliente que puede estar pagando.
    """
    conn = get_db()
    try:
        simulacion = padron.revisar_vencimientos(conn, aplicar=False)
    finally:
        conn.close()
    return jsonify({
        'simulacion': simulacion,
        'planes': list(padron.PLANES),
        'dias_prorroga_defecto': padron.DIAS_PRORROGA_DEFECTO,
    })


@bp.route('/api/superadmin/vencimientos/aplicar', methods=['POST'])
@superadmin_required
def api_aplicar_vencimientos():
    """Aplica los cambios de estado por vencimiento. Requiere confirmación.

    El panel pide confirmación antes de llamar esto.
    """
    conn = get_db()
    try:
        cambios = padron.revisar_vencimientos(conn, aplicar=True)
    finally:
        conn.close()
    return jsonify({
        'mensaje': (
            f'Se aplicaron {len(cambios)} cambio(s).'
            if cambios else 'No había vencimientos que aplicar.'),
        'cambios': cambios,
    })


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
    log de un servidor es un aviso que nadie lee hasta que ya se perdio una base.
    """
    resultado = padron.resumen(conn, _directorio())
    resultado['directorio_instancias'] = _directorio()
    resultado['aviso_persistencia'] = aviso_persistencia_instancias()
    return resultado


@bp.route('/api/superadmin/perfiles')
@superadmin_required
def api_perfiles():
    """Los perfiles en formato de tarjetas, con el logo de cada instancia.

    Cada tarjeta necesita el logo de SU ferretería. Se lee del archivo de la
    instancia, no de la base del desarrollador: el logo lo sube el dueño desde
    su panel y vive en el directorio de logos de su instancia.

    Si la instancia no está en disco, se entrega el logo genérico de la
    plataforma. Una tarjeta sin imagen se distingue de una con logo: la primera
    significa "todavía no subió", que es información útil.
    """
    conn = get_db()
    directorio = _directorio()
    try:
        registros = padron.listar(conn, directorio)
    finally:
        conn.close()

    perfiles = []
    for registro in registros:
        logo = _logo_de_instancia(directorio, registro)
        perfiles.append({
            'id': registro['id'],
            'nombre': registro['nombre'],
            'nit': registro['nit'],
            'contacto': registro['telefono'] or registro['email'],
            'archivo': registro['archivo'],
            'archivo_existe': registro['archivo_existe'],
            'estado': registro['estado'],
            'plan': registro['plan'],
            'fecha_vencimiento': registro['fecha_vencimiento'],
            'dias_para_vencer': registro['dias_para_vencer'],
            'dias_prorroga': registro['dias_prorroga'],
            'dias_restantes_prorroga': registro['dias_restantes_prorroga'],
            'suspension_automatica': registro['suspension_automatica'],
            'puede_operar': registro['puede_operar'],
            'logo_url': logo,
            'tiene_logo': bool(logo),
            # El color de la franja de la tarjeta. Lo calcula el servidor para
            # que el JavaScript no tenga que decidir qué estado es urgente.
            'franja': _franja(registro),
        })
    return jsonify({'perfiles': perfiles})


def _franja(registro):
    """Color de la tarjeta, decidido por el servidor.

    Centralizarlo acá evita que la regla de urgencia se desincronice entre el
    backend y el frontend: si cambia, cambia en un solo lugar.
    """
    if not registro['puede_operar']:
        return 'bloqueado'
    dias = registro['dias_para_vencer']
    if dias is not None and dias < 0:
        return 'vencido'
    if dias is not None and dias <= 7:
        return 'por_vencer'
    if registro['estado'] == 'prorrogado':
        return 'prorroga'
    return 'ok'


def _logo_de_instancia(directorio, registro):
    """URL del logo de una ferretería, leído de SU instancia.

    El logo se guarda como `static/logos/logo.<ext>` dentro de la base de cada
    instancia, así que la ruta es siempre la misma y solo cambia el contenido.
    Se busca entre las extensiones aceptadas.
    """
    if not registro['archivo_existe']:
        return ''
    base_instancia = os.path.join(directorio, os.path.splitext(registro['archivo'])[0])
    for extension in ('png', 'jpg', 'jpeg', 'webp', 'gif'):
        if os.path.exists(os.path.join(base_instancia, 'static', 'logos',
                                       f'logo.{extension}')):
            return f'/instancia/{registro["archivo"]}/static/logos/logo.{extension}'
    return ''


@bp.route('/instancia/<archivo>/static/logos/<path:nombre>')
@superadmin_required
def servir_logo_instancia(archivo, nombre):
    """Sirve el logo de UNA instancia.

    Existe para las tarjetas del panel. Va por aquí y no por `static` porque
    el logo de cada ferretería vive en SU carpeta de instancia, no en el
    `static` del desarrollador.

    Seguridad: el archivo se busca en el padrón, nunca se construye una ruta a
    partir de lo que llega. `send_from_directory` además impide salir del
    directorio indicado, así que un `../../` en `nombre` no llega al disco. Y
    solo se sirven imágenes de la carpeta `logos`, que es lo único que el dueño
    sube desde su panel.
    """
    conn = get_db()
    directorio = _directorio()
    try:
        registrado = conn.execute(
            'SELECT archivo FROM ferreterias WHERE archivo = ?', (archivo,)
        ).fetchone()
    finally:
        conn.close()

    if registrado is None:
        return jsonify({'error': 'Instancia no encontrada.'}), 404

    # Solo la carpeta de logos. Cualquier otra ruta (la base de datos de otro
    # cliente, un .py, el .p12) queda fuera.
    base = os.path.join(
        directorio,
        os.path.splitext(archivo)[0],
        'static', 'logos',
    )
    return send_from_directory(base, nombre)
