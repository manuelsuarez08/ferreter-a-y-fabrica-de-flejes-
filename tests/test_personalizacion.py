"""Pruebas de la personalización de marca: logo, nombre comercial y ticket.

El bloque de seguridad es el que más importa aquí. Un logo se guarda dentro de
`static/`, que es el mismo directorio desde donde el navegador descarga el
JavaScript de la aplicación. Si el nombre del archivo tuviera alguna libertad, un
cliente podría subir `../../app.py` y sobrescribir código. Estas pruebas fijan
que eso NO pase.

Cubren:
  1. Validación del logo por firma binaria (no por extensión).
  2. Que un archivo con ruta relativa no escape de la carpeta.
  3. Tope de tamaño.
  4. Guardar / deshacer / quitar.
  5. Que un logo "activo" sin archivo en disco no rompa el ticket.
  6. Acotado del tamaño y del color.
  7. Permisos: cualquier rol lee, solo admin escribe.
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# Imágenes mínimas y REALES (con su firma binaria). No son marcadores de
# posición: `_extension_real` mira los primeros bytes, así que un PNG de verdad
# es lo único que puede pasar por un PNG.
PNG_MINIMO = (
    b'\x89PNG\r\n\x1a\n'
    b'\x00\x00\x00\rIHDR'
    b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
    b'\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4'
    b'\x00\x00\x00\x00IEND\xaeB`\x82'
)
JPEG_MINIMO = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Base del DEVELOPER con el esquema completo, apuntada por FERRETERIA_DB.

    `ferreteria.config` resuelve `DB_NAME` al importarse y es estado GLOBAL
    compartido por toda la suite. Recargarlo aquí (y "restaurarlo" al terminar)
    deja el módulo apuntando a otra base de la que otros test configuraron al
    importarse, y rompía sus pruebas.

    La alternativa es parchear SOLO el atributo que nos interesa, con
    `monkeypatch`, que pytest revierte solo al terminar este test y sin tocar nada
    más del módulo.
    """
    from ferreteria import config as config_mod
    from ferreteria import db as db_mod

    ruta = str(tmp_path / 'ferreteria.db')
    monkeypatch.setattr(config_mod, 'DB_NAME', ruta)
    monkeypatch.setattr(db_mod, 'DB_NAME', ruta, raising=False)
    monkeypatch.setenv('FERRETERIA_DB', ruta)

    base = sqlite3.connect(ruta)
    cursor = base.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_alquiler(cursor)
    db_mod._crear_tablas_pedidos(cursor)
    db_mod._crear_tablas_cotizaciones(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._aplicar_migraciones(cursor)
    base.commit()
    base.close()

    return ruta


@pytest.fixture
def conn_logo(tmp_path, monkeypatch):
    """Base del negocio con las columnas de personalización y logos en un temp.

    Los logos se escriben en un directorio temporal, no en `static/logos`: una
    prueba que dejara archivos ahí ensuciaría el repositorio y, peor, haría que
    la siguiente prueba encontrara un logo que ella no subió.
    """
    from ferreteria import db as db_mod
    from ferreteria.services import personalizacion as marca

    directorio_logos = tmp_path / 'logos'
    directorio_logos.mkdir()

    monkeypatch.setattr(marca, 'DIRECTORIO_LOGOS', str(directorio_logos))
    monkeypatch.setattr(marca, '_ruta_absoluta', lambda logo: (
        os.path.join(str(directorio_logos), os.path.basename(logo))
        if logo else ''
    ))

    base = sqlite3.connect(':memory:')
    cursor = base.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_alquiler(cursor)
    db_mod._crear_tablas_pedidos(cursor)
    db_mod._crear_tablas_cotizaciones(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._aplicar_migraciones(cursor)
    base.commit()

    yield base, directorio_logos

    base.close()


# ═══════════════════════════════════════════
# 1. El archivo se valida por contenido, no por nombre
# ═══════════════════════════════════════════

def test_acepta_un_png_real(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    datos = marca.guardar_logo(conn, PNG_MINIMO, 'logo.png')

    assert datos['logo_tiene'] is True
    assert datos['logo_archivo'] == os.path.join('logos', 'logo.png')


def test_acepta_un_jpg_real(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    datos = marca.guardar_logo(conn, JPEG_MINIMO, 'foto.jpg')

    assert datos['logo_tiene'] is True
    assert (directorio / 'logo.jpg').exists()


def test_rechaza_un_script_renombrado_a_png(conn_logo):
    """El caso de seguridad: HTML con JavaScript disfrazado de imagen.

    El logo se sirve desde `static/`. Si se aceptara solo por extensión, este
    archivo se ejecutaría en el navegador de la ferretería.
    """
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    malicioso = b'<html><script>alert("x")</script></html>'

    with pytest.raises(marca.ErrorPersonalizacion) as error:
        marca.guardar_logo(conn, malicioso, 'logo.png')

    assert 'imagen válida' in str(error.value)
    assert not any(directorio.iterdir()), 'No debe quedar ningún archivo en disco'


def test_rechaza_un_ejecutable_renombrado_a_png(conn_logo):
    """Un .exe que se guarda como logo.png y se sirve como contenido estático."""
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    ejecutable = b'MZ\x90\x00\x03\x00\x00\x00' + b'\x00' * 100

    with pytest.raises(marca.ErrorPersonalizacion):
        marca.guardar_logo(conn, ejecutable, 'logo.png')

    assert not any(directorio.iterdir())


def test_rechaza_un_webp_falso(conn_logo):
    """RIFF es genérico: un WAV empieza igual. Sin verificar 'WEBP', pasaría."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    wav = b'RIFF\x24\x00\x00\x00WAVEfmt ' + b'\x00' * 20

    with pytest.raises(marca.ErrorPersonalizacion):
        marca.guardar_logo(conn, wav, 'logo.webp')


# ═══════════════════════════════════════════
# 2. El nombre del archivo no se usa
# ═══════════════════════════════════════════

def test_un_nombre_con_ruta_relativa_no_escapa(conn_logo):
    """El nombre enviado no decide dónde se escribe el archivo.

    Si se usara, un cliente podría mandar `../../../app.py` y sobrescribir el
    código de la aplicación.
    """
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, '../../../../app.py')

    # El archivo quedó DENTRO de la carpeta de logos, con nombre propio.
    assert (directorio / 'logo.png').exists()
    assert len(list(directorio.iterdir())) == 1
    assert not os.path.exists(os.path.join(RAIZ, 'app.png'))


def test_no_se_escribe_fuera_de_la_carpeta_de_logos(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, 'mi logo.png')

    dentro = os.path.abspath(str(directorio))
    for nombre in os.listdir(directorio):
        assert os.path.abspath(os.path.join(directorio, nombre)).startswith(dentro)


# ═══════════════════════════════════════════
# 3. El tamaño
# ═══════════════════════════════════════════

def test_rechaza_un_logo_demasiado_grande(conn_logo):
    """Un ticket de 58mm no gana nada con una imagen de 20 MB."""
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    enorme = PNG_MINIMO + b'\x00' * (marca.TAMANO_MAXIMO + 1000)

    with pytest.raises(marca.ErrorPersonalizacion) as error:
        marca.guardar_logo(conn, enorme, 'enorme.png')

    assert 'KB' in str(error.value)
    assert not any(directorio.iterdir())


def test_rechaza_un_archivo_vacio(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    with pytest.raises(marca.ErrorPersonalizacion):
        marca.guardar_logo(conn, b'', 'vacio.png')


# ═══════════════════════════════════════════
# 4. Guardar, deshacer y quitar
# ═══════════════════════════════════════════

def test_se_puede_deshacer_el_cambio_de_logo(conn_logo):
    """Subir un logo al revés no puede dejar a la ferretería sin ninguno."""
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, 'primero.png')
    marca.guardar_logo(conn, JPEG_MINIMO, 'segundo.jpg')

    restaurado = marca.restaurar_logo_anterior(conn)

    assert restaurado['logo_archivo'] == os.path.join('logos', 'logo.png')
    assert (directorio / 'logo.png').exists()


def test_sin_anterior_no_se_puede_deshacer(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, 'unico.png')

    # Se sube y se quita el anterior: ya no hay a dónde volver.
    conn.execute("UPDATE configuracion SET negocio_logo_anterior = '' WHERE id = 1")
    conn.commit()

    with pytest.raises(marca.ErrorPersonalizacion) as error:
        marca.restaurar_logo_anterior(conn)
    assert 'No hay ningún logo anterior' in str(error.value)


def test_cambiar_de_formato_conserva_el_anterior_para_deshacer(conn_logo):
    """No se borra el logo viejo: es lo que hace posible el botón deshacer.

    El caso más probable de querer deshacer es subir un JPG donde había un PNG.
    Si al cambiar de formato se borrara el anterior, ese sería justo el momento en
    que el botón deja de servir.
    """
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, 'a.png')
    marca.guardar_logo(conn, JPEG_MINIMO, 'b.jpg')

    assert (directorio / 'logo.png').exists(), 'El anterior debe seguir en disco'
    assert (directorio / 'logo.jpg').exists()

    restaurado = marca.restaurar_logo_anterior(conn)
    assert restaurado['logo_archivo'] == os.path.join('logos', 'logo.png')


def test_los_logos_mas_antiguos_se_limpian(conn_logo):
    """La carpeta no crece sin límite, pero los dos últimos sobreviven."""
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, '1.png')
    marca.guardar_logo(conn, JPEG_MINIMO, '2.jpg')
    marca.guardar_logo(conn, PNG_MINIMO, '3.png')
    marca.guardar_logo(conn, JPEG_MINIMO, '4.jpg')

    archivos = sorted(p.name for p in directorio.iterdir())

    # Tras cuatro cambios quedan el ACTUAL (jpg) y el ANTERIOR (png), que es lo
    # que hace posible deshacer. Los dos más viejos ya se borraron.
    assert archivos == ['logo.jpg', 'logo.png']

    # Y el siguiente cambio sí limpia el que ya no sirve para deshacer.
    marca.guardar_logo(conn, PNG_MINIMO, '5.png')
    assert sorted(p.name for p in directorio.iterdir()) == ['logo.jpg', 'logo.png']


def test_quitar_el_logo_vuelve_al_del_sistema(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, 'logo.png')

    datos = marca.quitar_logo(conn)

    assert datos['logo_archivo'] == ''
    assert datos['logo_tiene'] is False
    assert datos['logo_url'] == ''


# ═══════════════════════════════════════════
# 5. El logo roto no rompe el ticket
# ═══════════════════════════════════════════

def test_un_logo_activado_sin_archivo_no_rompe_el_documento(conn_logo):
    """El caso real: borraron el archivo de la carpeta a mano.

    La base sigue diciendo que hay logo. Si el ticket lo tomara sin verificar,
    saldría una imagen rota en el comprobante de cada cliente.
    """
    from ferreteria.services import personalizacion as marca

    conn, directorio = conn_logo
    marca.guardar_logo(conn, PNG_MINIMO, 'logo.png')
    conn.execute(
        'UPDATE configuracion SET negocio_logo_mostrar = 1 WHERE id = 1')
    conn.commit()

    # Se borra el archivo por fuera de la aplicación.
    for nombre in os.listdir(directorio):
        os.remove(os.path.join(str(directorio), nombre))

    datos = marca.para_documento(conn)

    assert datos['logo_mostrar'] is False, 'No debe intentar imprimir un logo roto'


def test_sin_logo_la_personalizacion_sigue_siendo_usable(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    datos = marca.para_documento(conn)

    assert datos['logo_mostrar'] is False
    assert datos['logo_url'] == ''
    # Y los demás datos están: el ticket sale igual.
    assert 'nombre_comercial' in datos
    assert 'mensaje_pie' in datos


# ═══════════════════════════════════════════
# 6. Tamaño y color acotados
# ═══════════════════════════════════════════

@pytest.mark.parametrize('entrada,esperado', [
    (10, 48),          # se sube al mínimo
    (500, 200),        # se baja al máximo
    (96, 96),
    ('96', 96),
    ('basura', 96),    # si no es número, el valor por defecto
])
def test_el_tamano_del_logo_se_acota(conn_logo, entrada, esperado):
    """Un logo de 500px tapa media página en una impresora de 58mm."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    marca.guardar_datos(conn, {'negocio_logo_tamano': entrada})
    assert marca.leer(conn)['logo_tamano'] == esperado


@pytest.mark.parametrize('entrada,esperado', [
    ('#ff0000', '#ff0000'),
    ('ff0000', '#ff0000'),      # sin almohadilla se la ponemos
    ('', '#1F4E79'),             # vacío: el color por defecto
    ('rojo', '#1F4E79'),         # inválido: el color por defecto
    ('#ff00', '#1F4E79'),        # corto: inválido
])
def test_un_color_mal_formado_no_rompe_el_estilo(conn_logo, entrada, esperado):
    """Un color inválido deja la interfaz sin estilo si va directo al CSS."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    marca.guardar_datos(conn, {'negocio_color_primario': entrada})
    assert marca.leer(conn)['color_primario'] == esperado


# ═══════════════════════════════════════════
# 7. Los datos que el dueño escribe
# ═══════════════════════════════════════════

def test_el_nombre_comercial_cae_a_la_razon_social(conn_logo):
    """Casi siempre coinciden, así que no se obliga a escribirlo dos veces."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    conn.execute("UPDATE configuracion SET nombre = 'FERRETERIA SAS' WHERE id = 1")
    conn.commit()

    assert marca.leer(conn)['nombre_comercial'] == 'FERRETERIA SAS'


def test_el_nombre_comercial_propio_manda(conn_logo):
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    conn.execute("UPDATE configuracion SET nombre = 'FERRETERIA SAS' WHERE id = 1")
    marca.guardar_datos(conn, {'negocio_nombre_comercial': 'El Tornillo'})
    conn.commit()

    assert marca.leer(conn)['nombre_comercial'] == 'El Tornillo'


def test_el_mensaje_del_pie_guarda_saltos_de_linea(conn_logo):
    """La garantía se escribe en varios renglones y se imprime tal cual."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    texto = 'Garantía de 6 meses\nNo se aceptan devoluciones sin recibo'
    marca.guardar_datos(conn, {'negocio_mensaje_pie': texto})

    assert marca.leer(conn)['mensaje_pie'] == texto


def test_guardar_solo_toca_los_campos_que_vienen(conn_logo):
    """Un formulario que solo manda el nombre no debe borrar el mensaje."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    marca.guardar_datos(conn, {'negocio_mensaje_pie': 'Garantía 12 meses'})

    marca.guardar_datos(conn, {'negocio_nombre_comercial': 'El Tornillo'})

    datos = marca.leer(conn)
    assert datos['nombre_comercial'] == 'El Tornillo'
    assert datos['mensaje_pie'] == 'Garantía 12 meses'


def test_guardar_nada_es_un_error_explicito(conn_logo):
    """Preferible a un "guardado" que no cambió nada."""
    from ferreteria.services import personalizacion as marca

    conn, _ = conn_logo
    with pytest.raises(marca.ErrorPersonalizacion) as error:
        marca.guardar_datos(conn, {})
    assert 'ningún campo' in str(error.value)# ═══════════════════════════════════════════
# 8. La API y los permisos
# ═══════════════════════════════════════════

@pytest.fixture
def app_logo(db_path, conn_logo):
    """App con la pantalla de personalización sobre la base del fixture.

    Se piden `db_path` y `conn_logo` aunque no se usen aquí: son los que
    parchearon la base y el directorio de logos, y sin ellos el `create_app`
    apuntaría a otra parte.
    """
    from ferreteria.app_factory import create_app

    return create_app(inicializar_db=False, iniciar_hilo_dian=False)


def _sesion(cliente, rol):
    with cliente.session_transaction() as s:
        s['usuario'] = 'usuario_prueba'
        s['rol'] = rol


def test_la_pantalla_se_abre_para_cualquier_rol(app_logo):
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'empleado')
        r = cliente.get('/personalizacion')

    assert r.status_code == 200
    assert b'Personalizaci' in r.data


def test_un_empleado_no_puede_guardar(app_logo):
    """Ver no es lo mismo que cambiar: un cajero no cambia el logo."""
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'empleado')
        r = cliente.put('/api/personalizacion',
                        json={'negocio_nombre_comercial': 'Hackeado'})

    assert r.status_code == 403


def test_un_empleado_no_puede_subir_logo(app_logo):
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'empleado')
        r = cliente.post(
            '/api/personalizacion/logo',
            data={'logo': (io.BytesIO(PNG_MINIMO), 'logo.png')},
            content_type='multipart/form-data')

    assert r.status_code == 403


def test_sin_sesion_no_se_ve_nada(app_logo):
    with app_logo.test_client() as cliente:
        assert cliente.get('/api/personalizacion').status_code == 401
        assert cliente.get('/personalizacion').status_code == 401


def test_el_admin_si_puede_guardar(app_logo):
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'admin')
        r = cliente.put('/api/personalizacion',
                        json={'negocio_nombre_comercial': 'El Tornillo'})

        assert r.status_code == 200
        assert r.get_json()['nombre_comercial'] == 'El Tornillo'


def test_el_admin_puede_subir_un_logo_valido(app_logo):
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'admin')
        r = cliente.post(
            '/api/personalizacion/logo',
            data={'logo': (io.BytesIO(PNG_MINIMO), 'cualquier.png')},
            content_type='multipart/form-data')

        assert r.status_code == 200
        assert r.get_json()['logo_tiene'] is True


def test_la_api_rechaza_un_archivo_malicioso(app_logo):
    """El HTML con JavaScript debe rechazarse también por HTTP, no solo en la función."""
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'admin')
        malicioso = b'<html><script>alert("x")</script></html>'
        r = cliente.post(
            '/api/personalizacion/logo',
            data={'logo': (io.BytesIO(malicioso), 'logo.png')},
            content_type='multipart/form-data')

    assert r.status_code == 400
    assert 'imagen válida' in r.get_json()['error']


def test_el_admin_puede_deshacer(app_logo):
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'admin')
        cliente.post('/api/personalizacion/logo',
                     data={'logo': (io.BytesIO(PNG_MINIMO), 'a.png')},
                     content_type='multipart/form-data')
        cliente.post('/api/personalizacion/logo',
                     data={'logo': (io.BytesIO(JPEG_MINIMO), 'b.jpg')},
                     content_type='multipart/form-data')

        r = cliente.post('/api/personalizacion/logo/restaurar')

        assert r.status_code == 200
        assert r.get_json()['logo_archivo'].endswith('logo.png')


def test_el_endpoint_de_documento_ama_logo_roto(app_logo, conn_logo):
    """El POS debe poder generar un ticket aunque el logo ya no esté en disco."""
    _, directorio = conn_logo
    with app_logo.test_client() as cliente:
        _sesion(cliente, 'admin')
        cliente.post('/api/personalizacion/logo',
                     data={'logo': (io.BytesIO(PNG_MINIMO), 'logo.png')},
                     content_type='multipart/form-data')

    for nombre in os.listdir(directorio):
        os.remove(os.path.join(str(directorio), nombre))

    with app_logo.test_client() as cliente:
        _sesion(cliente, 'admin')
        r = cliente.get('/api/personalizacion/documento')

    assert r.status_code == 200
    assert r.get_json()['logo_mostrar'] is False