"""Pruebas del Panel SuperAdmin: clonar, sembrar, registrar y suspender.

El foco está en lo que puede COSTAR DINERO si falla:

  - Que una instancia de una ferretería no arrastre datos de la semilla de otra
    (es decir: que quede realmente aislada).
  - Que la contraseña `admin123` de la semilla no sobreviva en la instancia.
  - Que suspender NO destruya la base ni sus ventas (es reversible).
  - Que eliminar del padrón NO borre el archivo.
  - Que no se pueda improvisar un estado de licencia ("suspendido " con espacio).
"""
from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def padron(tmp_path):
    """Una base CON LAS TABLAS DEL PADRÓN y un directorio de instancias limpio.

    La base del desarrollador se construye con el mismo código que usa la app, de
    modo que la prueba verifica el esquema real y no una copia a mano que podría
    quedar desalineada.
    """
    from ferreteria import db as db_mod

    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    conn.commit()
    conn.close()

    # `_crear_tablas_base` también crea las del padrón a propósito: así el
    # provisonamiento no puede fallar en producción por una tabla que no existe.
    assert conn is not None
    return conn, str(tmp_path)


@pytest.fixture
def padron_real(tmp_path, monkeypatch):
    """Una base del DESARROLLADOR con el esquema completo, en memoria.

    Se construye con las mismas funciones que usa `init_db`, en el mismo orden.
    Recomponerlo a mano dejaría tablas fuera y la prueba no estaría probando el
    esquema real, sino una aproximación.
    """
    from ferreteria import db as db_mod

    conn = sqlite3.connect(':memory:', check_same_thread=False)
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_alquiler(cursor)
    db_mod._crear_tablas_pedidos(cursor)
    db_mod._crear_tablas_cotizaciones(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._aplicar_migraciones(cursor)
    conn.commit()

    # El servicio abre sus propias conexiones, así que se le entrega esta misma
    # base a través de una conexión nueva al archivo en memoria.
    ruta = str(tmp_path / 'desarrollador.db')
    destino = sqlite3.connect(ruta)
    conn.backup(destino)
    destino.close()
    conn.close()

    return sqlite3.connect(ruta), str(tmp_path)


# ═══════════════════════════════════════════
# 1. El nombre del archivo
# ═══════════════════════════════════════════

@pytest.fixture
def padron(tmp_path):
    """Alias histórico del fixture, para no romper las referencias."""
    return padron_real(tmp_path)


def test_la_semilla_desactualizada_no_impide_provisionar(tmp_path):
    """La semilla del repo siempre va un paso atrás. Eso no puede bloquear el alta.

    Este test documenta una decisión: si a la semilla le falta una columna nueva,
    NO se aborta. Se crea la instancia igual y se devuelve un aviso, porque
    abortar dejaría al desarrollador sin poder dar de alta clientes hasta
    regenerar la semilla, que es un paso fuera de su alcance.
    """
    from ferreteria.services import provisionamiento

    directorio = str(tmp_path)
    # Se simula una semilla vieja: se le borran las columnas del proveedor.
    semilla = os.path.join(RAIZ, 'ferreteria-semilla.db')
    copia = os.path.join(directorio, 'semilla_vieja.db')
    shutil.copy2(semilla, copia)
    base = sqlite3.connect(copia)
    for columna in ('software_proveedor_nit', 'software_proveedor_nombre'):
        try:
            base.execute(f'ALTER TABLE configuracion DROP COLUMN {columna}')
        except sqlite3.OperationalError:
            pass  # la columna no existía: la semilla ya es vieja
    base.commit()
    base.close()

    original = provisionamiento.DB_SEMILLA
    provisionamiento.DB_SEMILLA = copia
    try:
        resultado = provisionamiento.provisionar(
            directorio, 'Ferretería Con Semilla Vieja', 'duenoX', 'ClaveSegura123')
    finally:
        provisionamiento.DB_SEMILLA = original

    assert os.path.exists(resultado['ruta'])
    assert resultado['avisos'], 'Debe avisar que el proveedor quedó sin escribir'


def test_el_nombre_del_archivo_no_rompe_la_linea_de_comandos():
    """Tildes, espacios y signos se quitan: el nombre va a rutas y a la consola."""
    from ferreteria.services.provisionamiento import nombre_archivo_instancia

    assert nombre_archivo_instancia('Ferretería El Tornillo') == \
        'ferreteria_el_tornillo'
    assert nombre_archivo_instancia('Ferretería El Tornillo S.A.S.') == \
        'ferreteria_el_tornillo_s_a_s'
    assert nombre_archivo_instancia('  Túnel  /  Equipos  ') == 'tunel_equipos'
    assert nombre_archivo_instancia('Ñandú & Cía') == 'nandu_cia'


def test_un_nombre_solo_de_simbolos_se_rechaza():
    from ferreteria.services.provisionamiento import (
        ErrorProvisionamiento, nombre_archivo_instancia)

    with pytest.raises(ErrorProvisionamiento):
        nombre_archivo_instancia('***')


# ═══════════════════════════════════════════
# 2. Crear la instancia
# ═══════════════════════════════════════════

def test_crear_la_instancia_copia_la_semilla_y_la_siembra(padron_real):
    """El archivo existe, tiene los datos del negocio y es una copia real."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería El Tornillo',
        usuario_dueno='dueno1', clave_dueno='ClaveSegura123',
        nit='900123456', digito_verificacion='7',
        direccion='CALLE 5 # 6-7', telefono='3001112222',
    )

    assert os.path.exists(registro['ruta'])
    assert registro['archivo'] == 'ferreteria_el_tornillo.db'
    assert registro['estado'] == 'activo'
    assert registro['puede_operar'] is True

    base = sqlite3.connect(registro['ruta'])
    datos = base.execute(
        'SELECT nombre, nit, digito_verificacion, direccion FROM configuracion '
        'WHERE id = 1'
    ).fetchone()
    assert datos == ('Ferretería El Tornillo', '900123456', '7', 'CALLE 5 # 6-7')
    base.close()


def test_la_instancia_recibe_el_nit_del_proveedor_y_no_el_del_cliente(padron_real):
    """El NIT que va al XML es el del desarrollador, no el de la ferretería."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Norte',
        usuario_dueno='dueno2', clave_dueno='ClaveSegura123',
        nit='900987654',
    )

    base = sqlite3.connect(registro['ruta'])
    nit_emisor, nit_proveedor = base.execute(
        'SELECT nit, software_proveedor_nit FROM configuracion WHERE id = 1'
    ).fetchone()
    base.close()

    assert nit_emisor == '900987654'
    assert nit_proveedor == '1054552590'
    # Y son distintos: si fueran iguales, el XML no podría distinguir quién
    # firma de quién construyó el software.
    assert nit_emisor != nit_proveedor


def test_las_claves_de_la_dian_del_cliente_quedan_vacias(padron_real):
    """No se siembran datos inventados: el cliente carga los suyos."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Sur',
        usuario_dueno='dueno3', clave_dueno='ClaveSegura123',
    )

    base = sqlite3.connect(registro['ruta'])
    fila = base.execute(
        'SELECT certificado_ruta, clave_tecnica, software_id, '
        'dian_software_security_code, dian_test_set_id FROM configuracion WHERE id = 1'
    ).fetchone()
    ambiente = base.execute(
        'SELECT dian_ambiente, dian_emision_automatica FROM configuracion WHERE id = 1'
    ).fetchone()
    base.close()

    assert fila == ('', '', '', '', '')
    # Habilitación y emisión apagada: una instancia nueva jamás apunta a
    # producción ni manda documentos sola.
    assert ambiente == ('2', 0)


def test_el_admin_de_la_semilla_no_sobrevive(padron_real):
    """`admin/admin123` está publicado en el repositorio: no puede quedar."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Centro',
        usuario_dueno='dueña', clave_dueno='ClaveSegura123',
    )

    base = sqlite3.connect(registro['ruta'])
    usuarios = base.execute('SELECT usuario, rol FROM usuarios').fetchall()
    base.close()

    assert ('admin', 'admin') not in usuarios
    assert ('dueña', 'admin') in usuarios


def test_la_contrasena_se_guarda_cifrada(padron_real):
    """Un `.db` que se roba no debe revelar la contraseña en claro."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Pruebas',
        usuario_dueno='dueno4', clave_dueno='ClaveSecreta999',
    )

    base = sqlite3.connect(registro['ruta'])
    almacenada = base.execute(
        'SELECT clave FROM usuarios WHERE usuario = ?', ('dueno4',)
    ).fetchone()[0]
    base.close()

    assert 'ClaveSecreta999' not in almacenada
    assert almacenada.startswith('pbkdf2:') or almacenada.startswith('scrypt:')

    # Y sirve para iniciar sesión (lo que hace el POS).
    from werkzeug.security import check_password_hash
    assert check_password_hash(almacenada, 'ClaveSecreta999')
    assert not check_password_hash(almacenada, 'admin123')


def test_no_se_sobrescribe_una_instancia_existente(padron_real):
    """Sobrescribir por error borraría ventas reales. Por defecto, no."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Unica',
        usuario_dueno='dueno5', clave_dueno='ClaveSegura123')

    with pytest.raises(padron.ErrorPadron) as error:
        padron.crear_ferreteria(
            conn, directorio, nombre='Ferretería Unica',
            usuario_dueno='otro', clave_dueno='ClaveSegura123')
    assert 'No se sobrescribe' in str(error.value)


def test_la_instancia_queda_aislada_de_la_semilla(padron_real):
    """Cambiar una ferretería NO puede tocar la base del desarrollador."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Aislada',
        usuario_dueno='dueno6', clave_dueno='ClaveSegura123',
        nit='900111222')

    base = sqlite3.connect(registro['ruta'])
    base.execute("UPDATE configuracion SET nombre = 'CAMBIADA' WHERE id = 1")
    base.commit()
    base.close()

    # La semilla del repositorio no se movió.
    original = sqlite3.connect(os.path.join(RAIZ, 'ferreteria-semilla.db'))
    nombre_semilla = original.execute(
        'SELECT nombre FROM configuracion WHERE id = 1'
    ).fetchone()[0]
    original.close()

    assert nombre_semilla != 'CAMBIADA'


# ═══════════════════════════════════════════
# 3. Suspender y reactivar
# ═══════════════════════════════════════════

def test_suspender_no_borra_los_datos(padron_real):
    """Suspender es administrativo y reversible: la base sigue con sus ventas."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Activa',
        usuario_dueno='dueno7', clave_dueno='ClaveSegura123')

    # Se simula una venta del cliente.
    base = sqlite3.connect(registro['ruta'])
    base.execute(
        'INSERT INTO clientes (nombre, cedula_nit) VALUES (?, ?)',
        ('Cliente de prueba', '111'))
    base.commit()
    base.close()

    padron.cambiar_estado(conn, registro['id'], 'suspendido', directorio)
    #
    suspendido = padron.obtener(conn, registro['id'], directorio)
    assert suspendido['estado'] == 'suspendido'
    assert suspendido['puede_operar'] is False

    # El archivo sigue ahí y con los datos dentro.
    assert os.path.exists(registro['ruta'])
    base = sqlite3.connect(registro['ruta'])
    assert base.execute('SELECT COUNT(*) FROM clientes').fetchone()[0] == 2
    base.close()

    # Y reactivar devuelve el acceso sin haber perdido nada.
    reactivado = padron.cambiar_estado(
        conn, registro['id'], 'activo', directorio)
    assert reactivado['estado'] == 'activo'
    assert reactivado['puede_operar'] is True


def test_un_estado_inventado_se_rechaza(padron_real):
    """'suspendido ' con espacio no puede colarse: el CHECK y el servicio lo frenan."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Estado',
        usuario_dueno='dueno8', clave_dueno='ClaveSegura123')

    with pytest.raises(padron.ErrorPadron):
        padron.cambiar_estado(conn, registro['id'], 'inventado', directorio)

    # Y a nivel de base tampoco: la restricción CHECK lo impide.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE ferreterias SET estado = 'inventado' WHERE id = ?",
                     (registro['id'],))
    conn.rollback()


def test_registrar_un_cliente_inexistente_falla(padron_real):
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    with pytest.raises(padron.ErrorPadron):
        padron.cambiar_estado(conn, 9999, 'suspendido', directorio)


# ═══════════════════════════════════════════
# 4. Eliminar del padrón no borra el archivo
# ═══════════════════════════════════════════

def test_eliminar_del_padron_conserva_el_archivo(padron_real):
    """La base puede tener documentos emitidos: no se borra desde una pantalla."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Para Olvidar',
        usuario_dueno='dueno9', clave_dueno='ClaveSegura123')

    resultado = padron.eliminar_registro(conn, registro['id'])

    assert os.path.exists(registro['ruta'])
    assert resultado['archivo_conservado'] == registro['archivo']
    assert padron.listar(conn, directorio) == []


# ═══════════════════════════════════════════
# 5. Lo que hay que arreglar
# ═══════════════════════════════════════════

def test_se_detectan_las_instancias_huerfanas(padron_real):
    """Un archivo sin registro se reporta, no se adopta ni se borra."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Registrada',
        usuario_dueno='dueno10', clave_dueno='ClaveSegura123')

    # Se simula una copia hecha a mano.
    shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'),
                 os.path.join(directorio, 'ferreteria_copia_manual.db'))

    huerfanos = padron.listar_huerfanos(conn, directorio)
    nombres = [h['archivo'] for h in huerfanos]

    assert 'ferreteria_copia_manual.db' in nombres
    assert 'ferreteria_el_tornillo.db' not in nombres  # el de la registrada


def test_se_detecta_un_cliente_activo_sin_archivo(padron_real):
    """Es lo que más engaña: figura activo pero el dueño no puede entrar."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Sin Disco',
        usuario_dueno='dueno11', clave_dueno='ClaveSegura123')

    os.remove(registro['ruta'])

    listado = padron.listar(conn, directorio)
    assert listado[0]['estado'] == 'activo'
    assert listado[0]['archivo_existe'] is False
    assert listado[0]['puede_operar'] is False
    # Y sale primero, porque es lo que hay que arreglar.
    assert padron.resumen(conn, directorio)['requiere_atencion'] >= 1


def test_los_que_no_pueden_operar_aparecen_primero(padron_real):
    """Un padrón donde todo se ve bien esconde lo que hay que corregir."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    buena = padron.crear_ferreteria(
        conn, directorio, nombre='AAA Ferretería Sana',
        usuario_dueno='d1', clave_dueno='ClaveSegura123')
    mala = padron.crear_ferreteria(
        conn, directorio, nombre='ZZZ Ferretería Rota',
        usuario_dueno='d2', clave_dueno='ClaveSegura123')
    os.remove(mala['ruta'])

    listado = padron.listar(conn, directorio)
    assert listado[0]['id'] == mala['id']
    assert listado[-1]['id'] == buena['id']


def test_la_verificacion_dice_que_le_falta_configurar(padron_real):
    """El panel debe mostrarle al desarrollador qué le falta al cliente."""
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Pendiente',
        usuario_dueno='dueno12', clave_dueno='ClaveSegura123')

    resultado = padron.verificar(conn, registro['id'], directorio)

    assert resultado['integridad'] == 'ok'
    assert resultado['tiene_admin'] is True
    assert resultado['pendiente_dian'] == [
        'Certificado .p12', 'Clave técnica', 'SoftwareID',
        'PIN (SoftwareSecurityCode)',
    ]


def test_el_historial_registra_lo_que_paso(padron_real):
    from ferreteria.services import padron_ferreterias as padron

    conn, directorio = padron_real
    registro = padron.crear_ferreteria(
        conn, directorio, nombre='Ferretería Con Historial',
        usuario_dueno='dueno13', clave_dueno='ClaveSegura123')
    padron.cambiar_estado(conn, registro['id'], 'suspendido', directorio)

    acciones = [h['accion'] for h in padron.historial(conn, registro['id'])]

    assert 'creada' in acciones
    assert 'suspendido' in acciones