"""Personalización de marca: el logo y los datos que salen en el ticket.

Este módulo es lo que hace que dos ferreterías que usan el mismo software se
vean distintas: nombre comercial, logo, color y el mensaje de garantía que el
dueño escribe. Todo sale de la tabla `configuracion` de SU instancia, así que
cada ferretería es Dueña de lo que ve y no hay que recompilar nada.

DECISIÓN: EL LOGO VIVE EN DISCO, NO EN LA BASE
---------------------------------------------
Guardar la imagen como BLOB en SQLite funciona, pero tiene dos problemas reales:
la base engorda con cada cambio de logo (el archivo viejo se queda para siempre)
y la imagen no se sirve con la eficiencia de un archivo estático. Aquí el logo
es un archivo en `static/logos/` y en la base queda solo su ruta.

Por seguridad no se guarda el nombre que envía el usuario: el archivo se llama
`logo.<extensión>` siempre. Si se usara el nombre original, un cliente podría
subir `../../../app.py` y sobrescribir código de la aplicación.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from ..config import BASE_DIR

# Carpeta donde viven los logos. Vive dentro de `static` a propósito: así la
# plantilla puede pedirlo como `/static/logos/logo.png` y el servidor lo sirve
# sin una ruta propia.
#
# Con UNA ferretería no había problema: la carpeta es del software. Con VARIAS,
# este `BASE_DIR` es el del DESARROLLADOR, y todas las instancias comparten el
# mismo archivo `logo.png`: la última en subirlo le pisaba el logo a las demás,
# y una ferretería veía el logo de otra en su ticket y en su login. Un logo
# ajeno es un problema de marca, no un detalle.
#
# Por eso el destino se resuelve en runtime (ver `directorio_logos()`): si la
# base que se está usando es la del desarrollador, los logos van a la carpeta
# compartida; si es una instancia de cliente, van a la carpeta de ESA instancia
# y quedan aisladas como el resto de sus datos.
DIRECTORIO_LOGOS_COMPARTIDO = os.path.join(BASE_DIR, 'static', 'logos')

# Prefijo de la carpeta de la instancia. Es el mismo nombre con el que se crea
# el archivo .db, sin extensión, para que sea obvio a qué ferretería pertenece.
PREFIJO_INSTANCIA = 'ferreteria_'


def directorio_logos():
    """Carpeta de logos de la base con la que se está trabajando ahora.

    Se resuelve en cada llamada y no al importar, porque `FERRETERIA_DB` cambia
    en runtime: el panel corre sobre la base del desarrollador y cada cliente
    sobre la suya. Fijarlo al importar haría que todas escribieran en la misma.
    """
    from ..config import DB_NAME

    nombre = os.path.splitext(os.path.basename(DB_NAME))[0]
    if PREFIJO_INSTANCIA not in nombre.lower():
        # Es la base del desarrollador: los logos van a la carpeta compartida.
        return DIRECTORIO_LOGOS_COMPARTIDO

    # Es una instancia: los logos van a su propia carpeta, junto a su base.
    base = os.path.dirname(os.path.abspath(DB_NAME))
    directorio = os.path.join(base, 'static', 'logos')
    os.makedirs(directorio, exist_ok=True)
    return directorio


# Se conserva el nombre viejo porque lo usan las pruebas y las herramientas.
# Apunta a la carpeta del desarrollador: es el caso de una instancia solo.
DIRECTORIO_LOGOS = DIRECTORIO_LOGOS_COMPARTIDO

# Extensiones aceptadas. Solo formatos de imagen que un navegador y una
# impresora térmica saben mostrar. El SVG se excluye a propósito: es un archivo
# que puede ejecutar JavaScript al abrirse, y sirve para injectar código en la
# página de la ferretería.
EXTENSIONES_PERMITIDAS = ('png', 'jpg', 'jpeg', 'gif', 'webp')

# Tope del archivo. El logo se imprime en un ticket de 58mm: 2 MB es más que
# suficiente para un PNG de alta resolución y evita que alguien suba una foto de
# 20 MB y ralentice cada carga del POS.
TAMANO_MAXIMO = 2 * 1024 * 1024

# Firma binaria de cada formato. Se comprueba el CONTENIDO, no la extensión: si
# solo se mirara el nombre, un archivo ejecutable renombrado a `.png` pasaría
# el control y se serviría desde `static`.
_FIRMAS = {
    'png': (b'\x89PNG\r\n\x1a\n',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    'gif': (b'GIF87a', b'GIF89a'),
    'webp': (b'RIFF',),
}

_COLOR_VALIDO = re.compile(r'^#?[0-9a-fA-F]{6}$')

# Tamaño del logo en el ticket. Se acota porque un logo de 500px en una
# impresora de 58mm tapa media página y el total deja de verse.
TAMANO_LOGO_MIN = 48
TAMANO_LOGO_MAX = 200


class ErrorPersonalizacion(Exception):
    """La personalización no se pudo aplicar (archivo inválido, etc.)."""


def _columnas(conn):
    return {r[1] for r in conn.execute('PRAGMA table_info(configuracion)')}


def _validar_tamano(valor):
    """Acota el tamaño del logo al rango que se ve bien en un ticket."""
    try:
        tamano = int(valor)
    except (TypeError, ValueError):
        return 96
    return max(TAMANO_LOGO_MIN, min(TAMANO_LOGO_MAX, tamano))


def _validar_color(valor):
    """Un color mal formado rompe el CSS y la interfaz queda sin estilo."""
    texto = str(valor or '').strip()
    if not texto:
        return '#1F4E79'
    if not texto.startswith('#'):
        texto = '#' + texto
    return texto if _COLOR_VALIDO.match(texto) else '#1F4E79'


def leer(conn):
    """La personalización actual del negocio.

    Se lee con un `SELECT` explícito de columnas COALESCE y NO con `SELECT *`:
    el orden de las columnas de `configuracion` cambia con cada migración, y un
    `SELECT *` seguido de `row[7]` se rompe en silencio el día que se agregue una
    columna en medio. Los nombres son el contrato.
    """
    disponibles = _columnas(conn)
    if 'negocio_logo' not in disponibles:
        raise ErrorPersonalizacion(
            'La base no tiene las columnas de personalización. Reinicie la '
            'aplicación para aplicar las migraciones.'
        )

    fila = conn.execute('''
        SELECT nombre, COALESCE(negocio_nombre_comercial, ''),
               COALESCE(negocio_logo, ''),
               COALESCE(negocio_mensaje_pie, ''),
               COALESCE(negocio_logo_tamano, 96),
               COALESCE(negocio_logo_mostrar, 1),
               COALESCE(negocio_color_primario, '#1F4E79')
        FROM configuracion WHERE id = 1
    ''').fetchone()

    if fila is None:
        raise ErrorPersonalizacion('No hay configuración del negocio.')

    # El nombre comercial cae al nombre de la razón social si no se configuró:
    # es lo natural, porque casi siempre coinciden.
    nombre_comercial = fila[1] or fila[0] or ''
    logo = fila[2] or ''

    return {
        'nombre': fila[0] or '',
        'nombre_comercial': nombre_comercial,
        'logo_url': _url_publica(logo),
        'logo_archivo': logo,
        'logo_tiene': bool(logo) and os.path.exists(_ruta_absoluta(logo)),
        'mensaje_pie': fila[3] or '',
        'logo_tamano': fila[4],
        'logo_mostrar': bool(fila[5]),
        'color_primario': fila[6] or '#1F4E79',
    }


def _ruta_absoluta(logo):
    """Ruta en disco del logo guardado."""
    if not logo:
        return ''
    if os.path.isabs(logo):
        return logo
    return os.path.join(directorio_logos(), os.path.basename(logo))


def _url_publica(logo):
    """URL con la que el navegador pide el logo.

    Se expone solo la parte relativa a `static/`. Una ruta absoluta del servidor
    (/home/usuario/app/static/logos/logo.png) no sirve en el navegador y además
    filtra la estructura del sistema.
    """
    if not logo:
        return ''
    return '/static/' + logo.replace('\\', '/').split('static/', 1)[-1]


def _extension_real(contenido):
    """Deduce el formato por la FIRMA del archivo, no por su nombre."""
    for extension, firmas in _FIRMAS.items():
        if any(contenido.startswith(firma) for firma in firmas):
            if extension == 'webp':
                # El RIFF es genérico: el WebP de verdad tiene 'WEBP' en los
                # bytes 8-12. Sin esta comprobación, un WAV o un AVI pasarían
                # por un logo y el navegador no lo mostraría.
                if contenido[8:12] != b'WEBP':
                    continue
            return extension
    return None


def guardar_logo(conn, contenido, nombre_original=''):
    """Valida y guarda un logo nuevo.

    Args:
        conn: conexión a la base del negocio.
        contenido: bytes del archivo.
        nombre_original: el nombre que envió el dueño. NO se usa para construir
            la ruta del archivo (eso haría que un cliente pudiera escribir fuera
            de la carpeta de logos). Se conserva en la firma para que el mensaje
            de error pueda nombrar el archivo que se rechazó.

    Returns:
        La personalización completa tras el cambio.

    Raises:
        ErrorPersonalizacion: si el archivo no es una imagen válida, excede el
            tamaño o no se pudo escribir.
    """
    if not contenido:
        raise ErrorPersonalizacion(
            f'No se recibió ningún archivo{f" ({nombre_original})" if nombre_original else ""}.'
        )

    if len(contenido) > TAMANO_MAXIMO:
        raise ErrorPersonalizacion(
            f'El logo pesa {len(contenido) // 1024} KB y el máximo es '
            f'{TAMANO_MAXIMO // 1024} KB. Para un ticket de 58mm no hace falta '
            'más: una imagen muy grande se ve peor, no mejor.'
        )

    extension = _extension_real(contenido)
    if extension is None:
        raise ErrorPersonalizacion(
            'El archivo no es una imagen válida. Solo se aceptan PNG, JPG, '
            'GIF o WebP. (Se revisa el contenido del archivo, no su extensión.)'
        )

    carpeta = directorio_logos()
    os.makedirs(carpeta, exist_ok=True)
    destino_relativo = os.path.join('logos', f'logo.{extension}')
    destino_absoluto = os.path.join(carpeta, f'logo.{extension}')

    anterior = conn.execute(
        'SELECT COALESCE(negocio_logo, "") FROM configuracion WHERE id = 1'
    ).fetchone()[0]

    with open(destino_absoluto, 'wb') as archivo:
        archivo.write(contenido)

    conn.execute(
        'UPDATE configuracion SET negocio_logo = ?, negocio_logo_anterior = ? '
        'WHERE id = 1',
        (destino_relativo, anterior or ''),
    )
    conn.commit()

    # Se limpian los logos viejos DESPUÉS de actualizar la base: si se hiciera
    # antes, el "anterior" podría ser el que se acaba de borrar.
    _limpiar_logos_huerfanos(conn)

    return leer(conn)


def _borrar_logo(logo):
    """Borra un logo del disco. No falla si ya no está: es una limpieza."""
    ruta = _ruta_absoluta(logo)
    # Solo se borra DENTRO de la carpeta de logos. Una ruta manipulada en la
    # base no debe poder borrar un archivo del sistema.
    dentro = os.path.abspath(ruta).startswith(os.path.abspath(directorio_logos()))
    if dentro and os.path.exists(ruta):
        try:
            os.remove(ruta)
        except OSError:
            pass


def restaurar_logo_anterior(conn):
    """Vuelve al logo anterior, si lo hay.

    Existe para el caso real: el dueño sube el logo al revés y no quiere que la
    ferretería quede sin ninguno.

    El logo anterior NO se borra al subir uno nuevo, incluso si cambia el
    formato. Borrarlo lo dejaba sin salida justo en el caso más probable de
    querer deshacer: subir un JPG donde había un PNG. Por eso hay dos archivos
    vivos en la carpeta (el actual y el anterior) y se limpian al cambiar de
    logo por tercera vez.
    """
    disponible = 'negocio_logo_anterior' in _columnas(conn)
    if not disponible:
        raise ErrorPersonalizacion('No hay logo anterior para restaurar.')

    fila = conn.execute(
        "SELECT COALESCE(negocio_logo, ''), COALESCE(negocio_logo_anterior, '') "
        'FROM configuracion WHERE id = 1'
    ).fetchone()

    actual, anterior = fila[0], fila[1]
    if not anterior:
        raise ErrorPersonalizacion('No hay ningún logo anterior.')

    conn.execute(
        'UPDATE configuracion SET negocio_logo = ?, negocio_logo_anterior = ? '
        'WHERE id = 1',
        (anterior, actual),
    )
    conn.commit()

    # Se materializa el que ahora está activo: los dos archivos existen en
    # disco, pero quien sirve la URL necesita el bytes del logo activo bajo el
    # nombre de la ruta activa.
    origen = _ruta_absoluta(anterior)
    if actual and os.path.exists(origen):
        destino = _ruta_absoluta(actual)
        try:
            with open(origen, 'rb') as origen_archivo:
                contenido = origen_archivo.read()
            with open(destino, 'wb') as destino_archivo:
                destino_archivo.write(contenido)
        except OSError:
            pass

    return leer(conn)


def _limpiar_logos_huerfanos(conn):
    """Borra de la carpeta los logos que ni el actual ni el anterior.

    Sin esto, cada cambio de formato deja un archivo atrás y la carpeta crece sin
    límite. Se conservan los dos últimos a propósito: son los que hacen posible
    "deshacer".
    """
    fila = conn.execute(
        "SELECT COALESCE(negocio_logo, ''), COALESCE(negocio_logo_anterior, '') "
        'FROM configuracion WHERE id = 1'
    ).fetchone()
    conservar = {os.path.basename(fila[0]), os.path.basename(fila[1])} - {''}

    carpeta = directorio_logos()
    if not os.path.isdir(carpeta):
        return
    for nombre in os.listdir(carpeta):
        if nombre.startswith('logo.') and nombre not in conservar:
            try:
                os.remove(os.path.join(carpeta, nombre))
            except OSError:
                pass


def quitar_logo(conn):
    """Deja el negocio sin logo (vuelve al genérico del sistema)."""
    conn.execute(
        "UPDATE configuracion SET negocio_logo = '', "
        "negocio_logo_anterior = '' WHERE id = 1"
    )
    conn.commit()
    return leer(conn)


def guardar_datos(conn, datos):
    """Guarda nombre comercial, mensaje del pie, tamaño y color.

    Solo se escribe lo que viene en `datos`. Se listan las columnas en el mismo
    orden que en el `UPDATE` a propósito, para que una columna nueva no se
    pueda colar entre el `SET` y los valores sin que se note.
    """
    disponibles = _columnas(conn)

    valores = {}
    if 'negocio_nombre_comercial' in disponibles and 'negocio_nombre_comercial' in datos:
        valores['negocio_nombre_comercial'] = str(
            datos['negocio_nombre_comercial'] or '').strip()
    if 'negocio_mensaje_pie' in disponibles and 'negocio_mensaje_pie' in datos:
        valores['negocio_mensaje_pie'] = str(datos['negocio_mensaje_pie'] or '').strip()
    if 'negocio_logo_tamano' in disponibles and 'negocio_logo_tamano' in datos:
        valores['negocio_logo_tamano'] = _validar_tamano(datos['negocio_logo_tamano'])
    if 'negocio_logo_mostrar' in disponibles and 'negocio_logo_mostrar' in datos:
        activo = datos['negocio_logo_mostrar']
        valores['negocio_logo_mostrar'] = 1 if activo in (True, 1, '1', 'true', 'on') else 0
    if 'negocio_color_primario' in disponibles and 'negocio_color_primario' in datos:
        valores['negocio_color_primario'] = _validar_color(datos['negocio_color_primario'])

    if not valores:
        raise ErrorPersonalizacion('No se recibió ningún campo para guardar.')

    asignaciones = ', '.join(f'{c} = ?' for c in valores)
    conn.execute(
        f'UPDATE configuracion SET {asignaciones} WHERE id = 1',
        list(valores.values()),
    )
    conn.commit()
    return leer(conn)


def para_documento(conn):
    """Datos listos para armar un ticket o una factura.

    Se separa de `leer()` a propósito: la plantilla y el PDF necesitan campos con
    nombres estables y valores ya saneados, no el diccionario de la interfaz.
    """
    datos = leer(conn)
    return {
        'nombre_comercial': datos['nombre_comercial'],
        'nombre': datos['nombre'],
        'logo_url': datos['logo_url'],
        'logo_tamano': datos['logo_tamano'],
        'logo_mostrar': datos['logo_mostrar'] and datos['logo_tiene'],
        'mensaje_pie': datos['mensaje_pie'],
        'color_primario': datos['color_primario'],
        'actualizado': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }