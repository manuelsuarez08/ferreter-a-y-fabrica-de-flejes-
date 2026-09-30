"""Rutas HTTP del módulo de Facturación Electrónica DIAN (software propio).

Expone la emisión y el seguimiento del Documento Equivalente Electrónico POS:

    GET    /api/dian/estado                 panel: configuración, cola y resumen
    GET    /api/dian/configuracion          ajustes del emisor y del software
    PUT    /api/dian/configuracion          guarda los ajustes (solo admin)
    POST   /api/dian/certificado            sube el .p12/.pfx (solo admin)
    POST   /api/dian/emitir/<id_venta>      emite el documento de una venta
    POST   /api/dian/documentos/<id>/reenviar   reintenta un envío fallido
    GET    /api/dian/documentos/<id>/consultar  consulta el estado en la DIAN
    GET    /api/dian/documentos             lista los documentos emitidos
    GET    /api/dian/documentos/<id>/xml    descarga el XML firmado
    POST   /api/dian/probar-conexion        comprueba si la DIAN responde
    POST   /api/dian/procesar-cola          reintenta los envíos pendientes
    GET    /api/dian/series                 series de numeración (POS/FV/NC)
    PUT    /api/dian/series/<tipo>          guarda una serie (solo admin)

Solo el administrador puede EMITIR y cambiar configuración; cualquier usuario
con sesión puede consultar el estado y descargar un XML (es información que el
cajero necesita para atender al cliente).
"""
import os

from flask import (Blueprint, jsonify, render_template, request, send_file,
                   session)

from ..config import BASE_DIR
from ..db import get_db
from ..security import admin_required, login_required
from ..services import dian_emision, dian_series, dian_soap
from ..services.auditoria import registrar_auditoria
from ..services.dian_firma import ErrorCertificado, cargar_certificado
from ..services.dian_emision import ErrorEmision
from ..services.dian_pos import ambiente_por_numero
from ..services.qr import qr_png

bp = Blueprint('dian_pos', __name__)

# Carpeta donde se guardan los certificados subidos. Vive FUERA del repositorio
# (junto a la base) para que no se suba a git: un .p12 es la llave de firma del
# negocio. El nombre del archivo se normaliza para no permitir rutas relativas.
CARPETA_CERTIFICADOS = os.path.join(BASE_DIR, 'certificados')

EXTENSIONES_CERTIFICADO = ('.p12', '.pfx')


# ═════════════════════════════
# Consulta
# ═════════════════════════════
@bp.route('/api/dian/estado', methods=['GET'])
@login_required
def estado():
    """Panel del módulo: ambiente, certificado, cola y documentos por estado."""
    conn = get_db()
    try:
        return jsonify(dian_emision.estado_dian(conn))
    finally:
        conn.close()


@bp.route('/api/dian/documentos', methods=['GET'])
@login_required
def listar_documentos():
    """Lista los documentos electrónicos emitidos (filtrable por estado)."""
    conn = get_db()
    try:
        estado_filtro = (request.args.get('estado') or '').strip() or None
        limite = min(max(int(request.args.get('limite', 100) or 100), 1), 500)
        return jsonify(dian_emision.listar_documentos(
            conn, limite=limite, estado=estado_filtro))
    finally:
        conn.close()


@bp.route('/api/dian/documentos/<int:id_documento>/xml', methods=['GET'])
@login_required
def descargar_xml(id_documento):
    """Devuelve el XML FIRMADO del documento (lo que exige el cliente final)."""
    conn = get_db()
    fila = conn.execute(
        'SELECT numero, xml_firmado, xml FROM documentos_electronicos WHERE id = ?',
        (id_documento,),
    ).fetchone()
    conn.close()
    if not fila:
        return jsonify({'error': 'Documento no encontrado'}), 404

    contenido = fila[1] or fila[2]
    if not contenido:
        return jsonify({'error': 'El documento no tiene XML generado'}), 404

    # Se sirve desde memoria: el XML puede ser grande y no debe quedar en disco
    # del servidor (además el POS puede correr en un contenedor de solo lectura).
    from io import BytesIO
    return send_file(
        BytesIO(contenido.encode('utf-8')),
        mimetype='application/xml',
        as_attachment=True,
        download_name=f'{fila[0]}.xml',
    )


# ═════════════════════════════
# Código QR de la tirilla
# ═════════════════════════════
@bp.route('/api/dian/qr', methods=['GET'])
@login_required
def qr():
    """Genera el PNG del código QR de la tirilla a partir de la URL de consulta.

    El QR se dibuja en el SERVIDOR (el navegador solo lo pinta en el canvas del
    recibo): así el POS no carga otra librería de generación y el contenido del
    QR queda en un único lugar auditable.

    Query:
        url: URL de consulta del documento en el catálogo de la DIAN.
        escala (opcional): píxeles por módulo.

    Returns:
        El PNG, o 400 si falta la URL o si es demasiado larga para un QR.
    """
    url = (request.args.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'Falta el parámetro url'}), 400
    try:
        escala = int(request.args.get('escala') or 0) or None
    except (TypeError, ValueError):
        escala = None
    try:
        png = qr_png(url, escala=escala) if escala else qr_png(url)
    except Exception:
        png = None
    if png is None:
        # URL ilegible o demasiado larga: la tirilla se imprime sin el QR.
        return jsonify({'error': 'No se pudo generar el código QR para esa URL'}), 400
    from io import BytesIO
    return send_file(BytesIO(png), mimetype='image/png')

# ═════════════════════════════
# Configuración
# ═════════════════════════════
@bp.route('/api/dian/configuracion', methods=['GET'])
@login_required
def configuracion():
    """Ajustes del emisor y del software propio (sin exponer secretos)."""
    conn = get_db()
    fila = conn.execute(
        """
        SELECT COALESCE(dian_ambiente, '2'), COALESCE(dian_prefijo, 'POS'),
               COALESCE(dian_consecutivo, 1), COALESCE(clave_tecnica, ''),
               COALESCE(software_id, ''), COALESCE(dian_test_set_id, ''),
               COALESCE(numero_resolucion, ''), COALESCE(prefijo, ''),
               COALESCE(rango_desde, 1), COALESCE(rango_hasta, 0),
               COALESCE(certificado_ruta, ''),
               CASE WHEN COALESCE(certificado_clave, '') != '' THEN 1 ELSE 0 END,
               COALESCE(dian_modo, 'habilitacion'),
               CASE WHEN COALESCE(software_pin, '') != '' THEN 1 ELSE 0 END,
               COALESCE(dian_software_security_code, ''),
               COALESCE(fecha_vencimiento_resolucion, ''), nit, nombre, telefono,
               direccion, COALESCE(codigo_municipio, ''),
               COALESCE(codigo_departamento, ''), COALESCE(regimen_fiscal, '')
        FROM configuracion WHERE id = 1
        """
    ).fetchone()

    # Estado del certificado para avisar en la interfaz si venció o está por vencer.
    certificado = {'configurado': bool(fila[10]), 'valido': False,
                   'titular': '', 'vence': '', 'dias': None, 'mensaje': ''}
    if fila[10] and os.path.exists(fila[10]):
        try:
            cargado = cargar_certificado(
                fila[10],
                _leer_clave_certificado(conn),
                verificar_vigencia=False,
            )
            certificado.update({
                'valido': cargado.vigente(),
                'titular': cargado.titular,
                'dias': cargado.dias_para_vencer(),
                'vence': cargado.fecha_vencimiento().strftime('%Y-%m-%d'),
            })
        except ErrorCertificado as error:
            certificado['mensaje'] = str(error)
    conn.close()

    return jsonify({
        'ambiente': fila[0], 'ambiente_nombre': ambiente_por_numero(fila[0]),
        'prefijo': fila[1], 'consecutivo': fila[2],
        'clave_tecnica': fila[3], 'software_id': fila[4],
        'test_set_id': fila[5], 'numero_resolucion': fila[6],
        'prefijo_resolucion': fila[7], 'rango_desde': fila[8],
        'rango_hasta': fila[9], 'modo': fila[12],
        'certificado': certificado,
        'tiene_clave_certificado': bool(fila[11]),
        'tiene_pin': bool(fila[13]),
        'software_security_code': fila[14],
        'fecha_vencimiento_resolucion': fila[15],
        # Datos del emisor (los mismos de Configuración > Negocio).
        'emisor': {'nit': fila[16], 'nombre': fila[17], 'telefono': fila[18],
                   'direccion': fila[19], 'municipio': fila[20],
                   'departamento': fila[21], 'regimen_fiscal': fila[22]},
    })


def _leer_clave_certificado(conn):
    """Contraseña del .p12 guardada en la configuración.

    Se lee aparte y no se devuelve nunca por HTTP: la interfaz solo sabe si hay
    una clave configurada o no.
    """
    fila = conn.execute(
        "SELECT COALESCE(certificado_clave, '') FROM configuracion WHERE id = 1"
    ).fetchone()
    return fila[0] if fila else ''


@bp.route('/api/dian/configuracion', methods=['PUT'])
@admin_required
def guardar_configuracion():
    """Guarda los ajustes del módulo DIAN.

    Solo actualiza los campos que llegan en el cuerpo: así el formulario de
    "datos del software" no borra el certificado, ni al revés.
    """
    data = request.json or {}
    conn = get_db()

    campos = {
        'dian_ambiente': ('ambiente', str),
        'dian_prefijo': ('prefijo', str),
        'clave_tecnica': ('clave_tecnica', str),
        'software_id': ('software_id', str),
        'software_pin': ('software_pin', str),
        'dian_software_security_code': ('software_security_code', str),
        'dian_test_set_id': ('test_set_id', str),
        'dian_modo': ('modo', str),
        'numero_resolucion': ('numero_resolucion', str),
        'prefijo': ('prefijo_resolucion', str),
        'fecha_vencimiento_resolucion': ('fecha_vencimiento_resolucion', str),
    }

    asignaciones, valores, cambiados = [], [], []
    for columna, (llave, tipo) in campos.items():
        if llave not in data:
            continue
        valor = tipo(data.get(llave) or '').strip()
        if llave == 'ambiente' and valor not in ('1', '2'):
            return jsonify({'error': "El ambiente debe ser '1' (producción) "
                                     "o '2' (habilitación)"}), 400
        if llave == 'modo' and valor not in ('habilitacion', 'produccion'):
            return jsonify({'error': "El modo debe ser 'habilitacion' o 'produccion'"}), 400
        asignaciones.append(f'{columna} = ?')
        valores.append(valor)
        cambiados.append(llave)

    # Numéricos: consecutivo y rango de la resolución.
    for columna, llave in (('dian_consecutivo', 'consecutivo'),
                           ('rango_desde', 'rango_desde'),
                           ('rango_hasta', 'rango_hasta')):
        if llave in data:
            try:
                numero = int(data.get(llave) or 0)
            except (TypeError, ValueError):
                return jsonify({'error': f'El campo {llave} debe ser numérico'}), 400
            asignaciones.append(f'{columna} = ?')
            valores.append(max(0, numero))
            cambiados.append(llave)

    if not asignaciones:
        conn.close()
        return jsonify({'error': 'No se envió ningún campo para actualizar'}), 400

    conn.execute(f"UPDATE configuracion SET {', '.join(asignaciones)} WHERE id = 1",
                 valores)
    registrar_auditoria(conn, 'actualizar', 'dian_configuracion', 1,
                        f'Configuración DIAN actualizada: {", ".join(cambiados)}')
    conn.commit()
    conn.close()
    return jsonify({'mensaje': 'Configuración DIAN guardada'})


@bp.route('/api/dian/certificado', methods=['POST'])
@admin_required
def subir_certificado():
    """Recibe el certificado .p12/.pfx y su contraseña.

    El archivo se guarda en `certificados/` (fuera del repositorio). Antes de
    aceptarlo se INTENTA ABRIR con la contraseña recibida: es mejor rechazarlo
    aquí, con un mensaje claro, que descubrir en la caja que el .p12 estaba mal
    cuando la DIAN rechace la primera venta.

    Form (multipart): archivo, clave
    """
    archivo = request.files.get('archivo')
    clave = request.form.get('clave', '')

    if not archivo or not archivo.filename:
        return jsonify({'error': 'Debe adjuntar el archivo del certificado (.p12/.pfx)'}), 400

    nombre = os.path.basename(archivo.filename)
    if not nombre.lower().endswith(EXTENSIONES_CERTIFICADO):
        return jsonify({'error': 'El certificado debe ser un archivo .p12 o .pfx'}), 400

    os.makedirs(CARPETA_CERTIFICADOS, exist_ok=True)
    destino = os.path.join(CARPETA_CERTIFICADOS, nombre)
    archivo.save(destino)

    # Validación inmediata: si la clave no abre el archivo, no se guarda nada.
    try:
        certificado = cargar_certificado(destino, clave)
    except ErrorCertificado as error:
        try:
            os.remove(destino)
        except OSError:
            pass
        return jsonify({'error': str(error)}), 400

    conn = get_db()
    conn.execute(
        'UPDATE configuracion SET certificado_ruta = ?, certificado_clave = ? '
        'WHERE id = 1',
        (destino, clave),
    )
    registrar_auditoria(conn, 'actualizar', 'dian_certificado', 1,
                        f'Certificado cargado. Titular: {certificado.titular}')
    conn.commit()
    conn.close()

    return jsonify({
        'mensaje': 'Certificado cargado y validado',
        'titular': certificado.titular,
        'vence': certificado.fecha_vencimiento().strftime('%Y-%m-%d'),
        'dias_para_vencer': certificado.dias_para_vencer(),
    })


# ═════════════════════════════
# Emisión
# ═════════════════════════════
@bp.route('/api/dian/emitir/<int:id_venta>', methods=['POST'])
@admin_required
def emitir(id_venta):
    """Emite (o reemite) el Documento Equivalente POS de una venta.

    Body opcional:
        {"contingencia": true}  -> genera y firma sin intentar enviar (cuando el
        POS ya sabe que no hay conexión y no quiere esperar el timeout)
        {"forzar": true}        -> reemite aunque ya exista documento
    """
    data = request.json or {}
    conn = get_db()
    try:
        resultado = dian_emision.emitir_venta(
            conn, id_venta,
            forzar=bool(data.get('forzar')),
            contingencia=bool(data.get('contingencia')),
        )
    except ErrorEmision as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()

    registrar_auditoria_accion(resultado, id_venta)
    return jsonify(resultado), 201


def registrar_auditoria_accion(resultado, id_venta):
    """Deja rastro en auditoría de la emisión (no debe tumbar la respuesta)."""
    try:
        conn = get_db()
        registrar_auditoria(
            conn, 'emitir', 'documento_dian', resultado.get('id_documento'),
            f"Venta #{id_venta} -> {resultado.get('numero')} "
            f"({resultado.get('estado')})",
        )
        conn.commit()
        conn.close()
    except Exception:
        # La auditoría es un extra: si falla, la emisión ya está hecha y se
        # devuelve igual (no se pierde la venta por un log).
        pass


@bp.route('/api/dian/documentos/<int:id_documento>/reenviar', methods=['POST'])
@admin_required
def reenviar(id_documento):
    """Reintenta el envío de un documento en contingencia o error."""
    conn = get_db()
    try:
        resultado = dian_emision.reenviar_documento(conn, id_documento)
    except ErrorEmision as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify(resultado)


@bp.route('/api/dian/documentos/<int:id_documento>/consultar', methods=['GET'])
@login_required
def consultar(id_documento):
    """Consulta en la DIAN el estado del documento (trackId / ZipKey)."""
    conn = get_db()
    try:
        resultado = dian_emision.consultar_estado(conn, id_documento)
    except ErrorEmision as error:
        return jsonify({'error': str(error)}), 400
    finally:
        conn.close()
    return jsonify(resultado)


@bp.route('/api/dian/procesar-cola', methods=['POST'])
@admin_required
def procesar_cola():
    """Reintenta los envíos pendientes de la cola (botón "Reintentar envíos")."""
    conn = get_db()
    try:
        resumen = dian_emision.procesar_cola(conn)
    finally:
        conn.close()
    return jsonify(resumen)


@bp.route('/api/dian/probar-conexion', methods=['POST'])
@login_required
def probar_conexion():
    """Comprueba si el servicio de la DIAN responde en el ambiente configurado.

    Sirve para decidir si se emite normalmente o en contingencia: si el POS sabe
    que la DIAN está caída, puede marcar la venta como contingencia sin esperar
    el timeout de 30 s con el cliente delante.
    """
    conn = get_db()
    ambiente = conn.execute(
        "SELECT COALESCE(dian_ambiente, '2') FROM configuracion WHERE id = 1"
    ).fetchone()[0]
    conn.close()

    disponible, detalle = dian_soap.probar_conexion(ambiente)
    # Se guarda la última verificación para mostrarla en el panel.
    if disponible:
        conn = get_db()
        from datetime import datetime
        conn.execute(
            'UPDATE configuracion SET dian_ultima_conexion = ? WHERE id = 1',
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),),
        )
        conn.commit()
        conn.close()

    return jsonify({
        'disponible': disponible,
        'ambiente': ambiente,
        'ambiente_nombre': ambiente_por_numero(ambiente),
        'detalle': detalle,
    })


# ═════════════════════════════
# Vistas
# ═════════════════════════════
@bp.route('/dian', methods=['GET'])
@login_required
def panel():
    """Página de administración del módulo de facturación electrónica."""
    return render_template('dian.html',
                           usuario=session.get('usuario'),
                           rol=session.get('rol', 'empleado'))


@bp.route('/api/dian/series', methods=['GET'])
@login_required
def api_series():
    """Series de numeración: una por tipo de documento (POS, FV, NC).

    Cada una tiene su prefijo, consecutivo y resolución. Es lo que permite
    emitir a la vez el Documento Equivalente del mostrador y la Factura
    Electrónica que pide un maestro de obra, que son numeraciones distintas.
    """
    conn = get_db()
    try:
        series = dian_series.leer_series(conn)
    finally:
        conn.close()
    return jsonify({
        'series': dian_series.estado_para_panel(series),
        # Prefijos sugeridos para empezar a trabajar en habilitación sin
        # resolución real. La DIAN exige SETP + dígitos del TestSetId.
        'sugerencia_pruebas': 'SETP990000000',
        'nota': ('Mientras no haya resolución real, configure un prefijo de '
                'pruebas (SETP + su TestSetId) y deje el rango en 0 para que '
                'no limite la numeración.'),
    })


@bp.route('/api/dian/series/<tipo_documento>', methods=['PUT'])
@admin_required
def api_actualizar_serie(tipo_documento):
    """Guarda los datos de una serie: prefijo, resolución, rango y clave técnica.

    Es el punto donde se ingresan los datos que entrega la DIAN por el portal
    (MUISCA). No hace falta tocar el código cuando llegue la resolución real.
    """
    data = request.json or {}
    tipo = (tipo_documento or '').strip().upper()

    # El prefijo del anexo técnico para el set de pruebas empieza por SETP.
    prefijo = str(data.get('prefijo') or '').strip().upper()
    if prefijo and not (prefijo.startswith('SETP') or prefijo.startswith('FV')
                        or prefijo.startswith('POS') or prefijo.startswith('NC')):
        return jsonify({
            'error': ('El prefijo debe empezar por SETP (pruebas), POS, FV o NC. '
                      f'Recibido: {prefijo}')
        }), 400

    conn = get_db()
    try:
        try:
            dian_series.actualizar_serie(conn, tipo, data)
        except dian_series.ErrorSerie as e:
            return jsonify({'error': str(e)}), 400
        registrar_auditoria(conn, 'configurar', 'serie_dian', None,
                            f'Serie {tipo}: prefijo={prefijo}, '
                            f'resolución={data.get("numero_resolucion", "")}')
        conn.commit()
        series = dian_series.leer_series(conn)
    finally:
        conn.close()
    return jsonify({
        'mensaje': f'Serie {tipo} actualizada',
        'series': dian_series.estado_para_panel(series),
    })


def registrar(app):
    """Conecta este blueprint a la aplicación."""
    app.register_blueprint(bp)