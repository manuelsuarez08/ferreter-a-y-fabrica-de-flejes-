"""Provisionamiento de instancias: una base de datos por ferretería.

POR QUÉ ESTO EXISTE
-------------------
La aplicación es de UNA ferretería por instalación, a propósito. La tabla
`configuracion` tiene `id INTEGER PRIMARY KEY CHECK (id = 1)`, así que solo
puede haber una fila, y `ventas` no tiene columna de negocio: no existe forma de
que un XML vaya con el NIT de una ferretería y las ventas de otra. Ese es el
criterio con el que se decidió no hacer multi-tenant: cero cruce de datos
fiscales.

Lo que este servicio hace es el paso siguiente: crear la instancia de una
ferretería nueva copiando la semilla, sembrando sus datos y creando al usuario
dueño. Cada ferretería queda completamente aislada en su propio archivo.

QUÉ SE COPIA Y QUÉ NO
---------------------
Se copia `ferreteria-semilla.db`, que por diseño NUNCA trae datos de operación
(ventas, clientes con cédula, auditoría) ni secretos. Lo que sí se escribe
después, en la base ya copiada:

  - Identidad del negocio: nombre, NIT, dirección, régimen.
  - Identidad del PROVEEDOR de software (el desarrollador), que es igual para
    todas las ferreterías: el NIT que va al XML no es el del cliente.
  - La fila `configuracion` de la fila única, lista para que el dueño cargue su
    `.p12` y sus claves de la DIAN desde su propio panel.

Lo que NUNCA se hace aquí: emitir documentos, tocar precios o escribir en la
base del desarrollador.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

from ..config import DB_SEMILLA

# Identidad del proveedor de software. Se escribe en CADA base nueva porque el
# anexo técnico exige que el XML declare quién construyó el software, y ese dato
# NO es el de la ferretería. Va aquí y no como constante en `dian_emision` para
# que se pueda corregir sin desplegar código.
NIT_PROVEEDOR_SOFTWARE = '1054552590'
NOMBRE_PROVEEDOR_SOFTWARE = ''

# Rol del dueño de la ferretería en SU instancia. Es `admin` de SU base, no un
# superusuario: no puede tocar la de otro. El superusuario vive en la base del
# desarrollador.
ROL_DUENO = 'admin'

# Separador entre palabras al generar el nombre del archivo de la instancia.
_NO_ALFANUMERICO = re.compile(r'[^a-z0-9]+')

# Avisos de la última siembra. Se acumulan en una lista y se devuelven con el
# resultado, para que el panel pueda mostrarlos. No son excepciones: describen
# algo que quedó a medias pero no impide operar.
_AVISOS_SEMILLA = []


class ErrorProvisionamiento(Exception):
    """No se pudo crear la instancia de la ferretería."""


def _sin_accentos(texto):
    """Quita tildes y pasa a minúsculas, para armar un nombre de archivo seguro."""
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ü': 'u',
        'Á': 'a', 'É': 'e', 'Í': 'i', 'Ó': 'o', 'Ú': 'u', 'Ü': 'u',
        'ñ': 'n', 'Ñ': 'n',
    }
    for original, replacement in replacements.items():
        texto = texto.replace(original, replacement)
    return texto.lower()


def nombre_archivo_instancia(nombre_negocio):
    """Convierte el nombre del negocio en un nombre de archivo seguro.

    'Ferretería El Tornillo S.A.S.' -> 'ferreteria_el_tornillo_sas'

    Se quitan tildes y todo lo que no sea alfanumérico, porque el nombre va a
    terminar en la línea de comandos del despliegue y en una URL. Un espacio o
    una barra ahí rompen cosas en el momento menos visible.
    """
    limpio = _sin_accentos(str(nombre_negocio or '')).strip()
    partes = [p for p in _NO_ALFANUMERICO.split(limpio) if p]
    if not partes:
        raise ErrorProvisionamiento(
            'El nombre del negocio no tiene letras ni números: no se puede '
            'generar el nombre de la base de datos.'
        )
    return '_'.join(partes)


def ruta_instancia(directorio, nombre_negocio, sufijo='.db'):
    """Ruta completa del archivo de la instancia."""
    return os.path.join(directorio, nombre_archivo_instancia(nombre_negocio) + sufijo)


def _columnas_existentes(conn):
    return {r[1] for r in conn.execute('PRAGMA table_info(configuracion)')}


def _sembrar_configuracion(conn, datos):
    """Escribe los datos iniciales en la fila ÚNICA de `configuracion`.

    Se listan las columnas a mano en vez de armar el UPDATE con un diccionario,
    por dos razones: los nombres se leen en el mismo orden en que se declaran
    abajo (un `dict` perdería ese orden y una clave faltante pasaría inadvertida),
    y así es obvio qué campos de la DIAN quedan VACÍOS a propósito, que es
    justamente lo que el dueño tiene que completar después.
    """
    valores = {
        # Identidad del negocio (lo escribe el desarrollador al provisionar).
        'nombre': datos['nombre'],
        'nit': datos.get('nit', ''),
        'digito_verificacion': datos.get('digito_verificacion', ''),
        'direccion': datos.get('direccion', ''),
        'telefono': datos.get('telefono', ''),
        'email_emisor': datos.get('email', ''),
        'regimen_fiscal': datos.get('regimen_fiscal', 'Responsable de IVA'),
        'responsabilidades': datos.get('responsabilidades', 'O-13'),
        # Identidad del PROVEEDOR de software: la misma para todos los clientes.
        'software_proveedor_nit': datos.get(
            'software_proveedor_nit', NIT_PROVEEDOR_SOFTWARE),
        'software_proveedor_nombre': datos.get(
            'software_proveedor_nombre', NOMBRE_PROVEEDOR_SOFTWARE),
        # ── Todo lo de la DIAN del cliente queda VACÍO a propósito ──
        # El .p12, la clave técnica, el SoftwareID y el PIN los entrega la DIAN
        # al cliente cuando registra SU software, y cada ferretería tiene los
        # suyos. Sembrarlos aquí con datos inventados sería peor que dejarlos
        # vacíos: `estado_dian` los marcaría como configurados.
        'certificado_ruta': '',
        'certificado_clave': '',
        'clave_tecnica': '',
        'software_id': '',
        'dian_software_security_code': '',
        'software_pin': '',
        'dian_test_set_id': '',
        'numero_resolucion': '',
        # Ambiente de habilitación: una instancia nueva jamás debe apuntar a
        # producción. El dueño cambia esto cuando ya tiene su resolución real.
        'dian_ambiente': '2',
        'dian_modo': 'habilitacion',
        'dian_prefijo': 'POS',
        'dian_consecutivo': 1,
        # La emisión automática arranca APAGADA: hasta que el dueño configure
        # sus credenciales y pruebe en el set de pruebas, no se manda nada a la
        # DIAN por su cuenta.
        'dian_emision_automatica': 0,
    }

    disponibles = _columnas_existentes(conn)
    # Se filtra contra el esquema REAL de la base. Una semilla vieja puede no
    # tener alguna columna nueva; escribirla produciría "no such column" y
    # dejaría la instancia a medio crear.
    usables = {k: v for k, v in valores.items() if k in disponibles}

    if 'software_proveedor_nit' not in disponibles:
        # Esto NO es un error, es lo esperable: la semilla del repositorio se
        # actualiza solo cuando alguien regenera ese archivo, y el despliegue de
        # una ferretería nueva no espera a eso. El código funciona igual sin la
        # columna; lo que se pierde es el NIT del proveedor en el XML, que se
        # rellena después desde el panel. Abortar acá dejaría al desarrollador sin
        # poder dar de alta clientes hasta regenerar la semilla, que es un paso
        # fuera de su alcance.
        _AVISOS_SEMILLA.append(
            'La base semilla no tiene las columnas del proveedor de software. '
            'La instancia se creó, pero el NIT del desarrollador NO quedó escrito: '
            'complete "software_proveedor_nit" en la configuración de la '
            'ferretería antes de emitir documentos.'
        )

    asignaciones = ', '.join(f'{columna} = ?' for columna in usables)
    conn.execute(
        f'UPDATE configuracion SET {asignaciones} WHERE id = 1',
        list(usables.values()),
    )


def _crear_usuario_dueno(conn, usuario, clave):
    """Crea el usuario administrador de la instancia.

    No se deja el `admin/admin123` que trae la semilla: esa clave es pública,
    está en el repositorio y sirve para entrar a cualquier ferretería. Se
    borra y se crea el usuario real del dueño.

    Returns:
        El id del usuario creado.
    """
    # El admin genérico de la semilla se elimina: es una puerta conocida y su clave
    # (`admin123`) está publicada en el repositorio.
    conn.execute("DELETE FROM usuarios WHERE usuario = ?", ('admin',))

    existe = conn.execute(
        'SELECT id FROM usuarios WHERE usuario = ?', (usuario,)
    ).fetchone()
    if existe:
        raise ErrorProvisionamiento(
            f'El usuario "{usuario}" ya existe en esta instancia.'
        )

    cursor = conn.execute(
        'INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)',
        (usuario, generate_password_hash(clave), ROL_DUENO),
    )
    return cursor.lastrowid


def provisionar(directorio, nombre_negocio, usuario_dueno, clave_dueno,
                nit='', digito_verificacion='', direccion='', telefono='',
                email='', regimen_fiscal='Responsable de IVA',
                responsabilidades='O-13',
                software_proveedor_nit=NIT_PROVEEDOR_SOFTWARE,
                software_proveedor_nombre=NOMBRE_PROVEEDOR_SOFTWARE,
                sobrescribir=False):
    """Crea la instancia completa de una ferretería.

    Args:
        directorio: dónde se crea el archivo .db (normalmente el directorio de
            datos del despliegue).
        nombre_negocio: nombre comercial. Define el nombre del archivo.
        usuario_dueno / clave_dueno: credenciales iniciales del dueño.
        nit y demás: datos fiscales del negocio. Se pueden dejar vacíos y
            completarlos después desde su panel.
        sobrescribir: si True, reemplaza una instancia existente. Por defecto
            NO se sobrescribe, porque esa base puede tener ventas y issued
            documentos de una ferretería real; borrarla por error es un
            desastre que no se puede deshacer.

    Returns:
        Un diccionario con la ruta creada y lo que se sembró, para confirmar.

    Raises:
        ErrorProvisionamiento: si la semilla no existe, el nombre no sirve, ya
            hay una instancia con ese nombre, o la escritura falla.
    """
    if not os.path.exists(DB_SEMILLA):
        raise ErrorProvisionamiento(
            f'No se encontró la base semilla en {DB_SEMILLA}. Sin ella no se '
            'puede crear una instancia nueva.'
        )
    if not str(nombre_negocio or '').strip():
        raise ErrorProvisionamiento('Falta el nombre del negocio.')
    if not str(usuario_dueno or '').strip():
        raise ErrorProvisionamiento('Falta el nombre de usuario del dueño.')
    if not str(clave_dueno or ''):
        raise ErrorProvisionamiento('Falta la contraseña del dueño.')

    destino = ruta_instancia(directorio, nombre_negocio)
    os.makedirs(directorio, exist_ok=True)

    if os.path.exists(destino) and not sobrescribir:
        raise ErrorProvisionamiento(
            f'Ya existe una instancia en {os.path.basename(destino)}. No se '
            'sobrescribe a propósito: esa base puede tener ventas reales. Use '
            'sobrescribir=True solo si sabe qué está haciendo.'
        )

    # Copia primero, se escribe después: si la siembra falla, no queda un
    # archivo a medio crear que después alguien intente usar.
    temporal = destino + '.creando'
    if os.path.exists(temporal):
        os.remove(temporal)

    # Se vacían los avisos de una siembra anterior: son de ESTA operación.
    _AVISOS_SEMILLA.clear()

    try:
        shutil.copy2(DB_SEMILLA, temporal)

        conn = sqlite3.connect(temporal)
        try:
            _sembrar_configuracion(conn, {
                'nombre': nombre_negocio,
                'nit': nit,
                'digito_verificacion': digito_verificacion,
                'direccion': direccion,
                'telefono': telefono,
                'email': email,
                'regimen_fiscal': regimen_fiscal,
                'responsabilidades': responsabilidades,
                'software_proveedor_nit': software_proveedor_nit,
                'software_proveedor_nombre': software_proveedor_nombre,
            })
            id_usuario = _crear_usuario_dueno(conn, usuario_dueno.strip(), clave_dueno)
            conn.commit()
        finally:
            conn.close()

        os.replace(temporal, destino)
    except Exception as error:
        if os.path.exists(temporal):
            os.remove(temporal)
        if isinstance(error, ErrorProvisionamiento):
            raise
        raise ErrorProvisionamiento(
            f'No se pudo crear la instancia: {error}'
        ) from error

    return {
        'ruta': destino,
        'archivo': os.path.basename(destino),
        'nombre_negocio': nombre_negocio,
        'usuario_dueno': usuario_dueno.strip(),
        'id_usuario': id_usuario,
        'nit': nit,
        'proveedor_software': software_proveedor_nit,
        'creado': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'avisos': list(_AVISOS_SEMILLA),
    }


def verificar_instancia(ruta):
    """Comprueba que una base recién creada esté sana.

    Existe porque `shutil.copy2` termina bien aunque el archivo quede corrupto
    (disco lleno a medio copiar, por ejemplo). Verificar ANTES de entregarle
    la instancia al cliente evita que descubra en su primer arranque que el
    archivo no abre.

    Returns:
        Un diccionario con el resultado de cada comprobación.

    Raises:
        ErrorProvisionamiento: si la base no abre o le falta lo esencial.
    """
    if not os.path.exists(ruta):
        raise ErrorProvisionamiento(f'No existe el archivo: {ruta}')

    try:
        conn = sqlite3.connect(ruta)
    except sqlite3.Error as error:
        raise ErrorProvisionamiento(f'No se pudo abrir la base: {error}') from error

    try:
        integridad = conn.execute('PRAGMA integrity_check').fetchone()[0]
        if integridad != 'ok':
            raise ErrorProvisionamiento(
                f'La base está dañada (integrity_check: {integridad}).'
            )

        # La semilla puede no tener las columnas del proveedor: se comprueba
        # antes de consultarlas en vez de asumir el esquema. Un "no such column"
        # aquí diría "la base no pasó la verificación", que manda a mirar una
        # corrupción que no existe.
        disponibles = _columnas_existentes(conn)
        nit_proveedor = (
            conn.execute(
                'SELECT software_proveedor_nit FROM configuracion WHERE id = 1'
            ).fetchone()[0]
            if 'software_proveedor_nit' in disponibles else ''
        )

        # Un LATERAL JOIN con sqlite_master: si la tabla no existe, no hay fila y
        # el LEFT JOIN devuelve NULLs en vez de lanzar "no such table".
        fila = conn.execute(
            '''
            SELECT c.nit, c.dian_ambiente,
                   c.certificado_ruta, c.clave_tecnica, c.software_id,
                   c.dian_software_security_code,
                   (SELECT COUNT(*) FROM usuarios WHERE rol = 'admin') AS admins
            FROM configuracion c
            LEFT JOIN sqlite_master m ON m.type = 'table' AND m.name = 'usuarios'
            WHERE c.id = 1
            '''
        ).fetchone()

        if fila is None:
            raise ErrorProvisionamiento(
                'La base creada no tiene la fila de configuración (id=1).'
            )

        usuarios = conn.execute('SELECT usuario, rol FROM usuarios').fetchall()
    except sqlite3.Error as error:
        raise ErrorProvisionamiento(f'La base no pasó la verificación: {error}') from error
    finally:
        conn.close()

    return {
        'archivo': os.path.basename(ruta),
        'integridad': integridad,
        'usuarios': [{'usuario': u, 'rol': r} for u, r in usuarios],
        'tiene_admin': bool(fila[6]),
        'proveedor_software': nit_proveedor,
        # Lo que falta es lo que el dueño debe completar. Se devuelve para
        # mostrarlo como lista de pendientes, no para rellenar.
        'pendiente_dian': [
            etiqueta for etiqueta, valor in (
                ('Certificado .p12', fila[2]),
                ('Clave técnica', fila[3]),
                ('SoftwareID', fila[4]),
                ('PIN (SoftwareSecurityCode)', fila[5]),
            ) if not valor
        ],
        'ambiente': fila[1],
    }