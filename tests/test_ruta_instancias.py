"""Pruebas de dónde se crean las bases de datos de los clientes.

Este es el punto donde un error cuesta datos reales: una instancia creada fuera
del disco persistente de Render desaparece en el siguiente despliegue, sin error
y sin aviso de la DIAN. Las pruebas fijan la resolución de la ruta y, sobre todo,
que el aviso aparezca cuando corresponde.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Una base LIMPIA de desarrollador, apuntada por FERRETERIA_DB."""
    destino = tmp_path / 'ferreteria.db'
    shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), destino)
    monkeypatch.setenv('FERRETERIA_DB', str(destino))

    # `config` resuelve DB_NAME al importarse: hay que recargarlo para que apunte
    # a esta base y no a la real.
    import ferreteria.config as config
    importlib.reload(config)
    import ferreteria.db as db_mod
    importlib.reload(db_mod)
    return str(destino)


@pytest.fixture
def entorno(monkeypatch):
    """Recarga `ferreteria.config` con un entorno limpio.

    `config` lee las variables al importarse, así que hay que recargarlo para
    probar cada combinación. Se limpian las tres que influyen:
    `RENDER`, `FERRETERIA_DB` y `FERRETERIA_INSTANCIAS`.
    """
    import ferreteria.config as config

    for variable in ('RENDER', 'FERRETERIA_DB', 'FERRETERIA_INSTANCIAS'):
        monkeypatch.delenv(variable, raising=False)

    def recargar():
        importlib.reload(config)
        return config

    yield recargar

    # Se deja el módulo como estaba, para no arrastrar el entorno de prueba.
    for variable in ('RENDER', 'FERRETERIA_DB', 'FERRETERIA_INSTANCIAS'):
        monkeypatch.delenv(variable, raising=False)
    importlib.reload(config)


# ═══════════════════════════════════════════
# En local: junto a la base de trabajo
# ═══════════════════════════════════════════

def test_en_local_va_junto_a_la_base_de_trabajo(entorno, monkeypatch, tmp_path):
    """Es lo natural: la instancia y la base del desarrollador, en el mismo sitio."""
    monkeypatch.setenv('FERRETERIA_DB', str(tmp_path / 'ferreteria.db'))
    config = entorno()

    assert config.DIRECTORIO_INSTANCIAS == str(tmp_path)


# ═══════════════════════════════════════════
# En Render: al disco persistente
# ═══════════════════════════════════════════

def test_en_render_sin_db_en_el_disco_va_a_var_data(entorno, monkeypatch):
    """La base de trabajo está en el contenedor: las instancias tampoco pueden estarlo."""
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/app/ferreteria.db')
    config = entorno()

    assert config.DIRECTORIO_INSTANCIAS == '/var/data/instancias'


def test_en_render_con_la_base_en_el_disco_reutiliza_ese_disco(entorno, monkeypatch):
    """Si FERRETERIA_DB ya apunta al disco, las instancias van al mismo sitio.

    Es lo coherente: la instancia tiene que sobrevivir donde vive el resto.
    """
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/var/data/ferreteria.db')
    config = entorno()

    assert config.DIRECTORIO_INSTANCIAS == '/var/data'


def test_un_subdirectorio_del_disco_tambien_serve(entorno, monkeypatch):
    """La comparación es por prefijo: /var/data/clientes también es persistente."""
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/var/data/clientes/ferreteria.db')
    config = entorno()

    assert config.DIRECTORIO_INSTANCIAS == '/var/data/clientes'


# ═══════════════════════════════════════════
# La variable de entorno manda
# ═══════════════════════════════════════════

def test_ferreteria_instancias_tiene_prioridad(entorno, monkeypatch):
    """Permite cambiar de infraestructura sin tocar el código."""
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/var/data/ferreteria.db')
    monkeypatch.setenv('FERRETERIA_INSTANCIAS', '/mnt/disco-externo/ventas')
    config = entorno()

    assert config.DIRECTORIO_INSTANCIAS == '/mnt/disco-externo/ventas'


def test_la_variable_manda_tambien_en_local(entorno, monkeypatch, tmp_path):
    monkeypatch.setenv('FERRETERIA_INSTANCIAS', str(tmp_path / 'propio'))
    config = entorno()

    assert config.DIRECTORIO_INSTANCIAS == str(tmp_path / 'propio')


# ═══════════════════════════════════════════
# El aviso
# ═══════════════════════════════════════════

def test_avisa_en_render_si_la_ruta_es_efimera(entorno, monkeypatch):
    """Este es el aviso que evita perder una base de cliente."""
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/app/ferreteria.db')
    config = entorno()

    aviso = config.aviso_persistencia_instancias('/app/instancias')

    assert aviso is not None
    assert '/var/data' in aviso
    assert 'FERRETERIA_INSTANCIAS' in aviso


def test_no_avisa_en_render_si_ya_esta_en_el_disco(entorno, monkeypatch):
    """Sin ruido: si ya está bien, el aviso solo cansa."""
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/var/data/ferreteria.db')
    config = entorno()

    assert config.aviso_persistencia_instancias('/var/data') is None


def test_no_avisa_en_local(entorno, monkeypatch, tmp_path):
    """En local no hay despliegues que borren el disco."""
    monkeypatch.setenv('FERRETERIA_DB', str(tmp_path / 'ferreteria.db'))
    config = entorno()

    assert config.aviso_persistencia_instancias(str(tmp_path)) is None


def test_avisa_si_la_ruta_pareece_temporal_aunque_no_sea_render(entorno, monkeypatch):
    """Un contenedor o un temp de desarrollo también borra al reiniciar."""
    monkeypatch.setenv('FERRETERIA_DB', '/tmp/ferreteria.db')
    config = entorno()

    aviso = config.aviso_persistencia_instancias('/tmp/instancias')
    assert aviso is not None


def test_el_aviso_explica_donde_esta_el_directorio(entorno, monkeypatch):
    """Un aviso sin la ruta concreta no sirve: hay que saber qué corregir."""
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setenv('FERRETERIA_DB', '/app/ferreteria.db')
    config = entorno()

    aviso = config.aviso_persistencia_instancias('/opt/datos/instancias')
    assert '/opt/datos/instancias' in aviso


# ═══════════════════════════════════════════
# El panel lo expone
# ═══════════════════════════════════════════

def test_el_panel_expone_el_directorio_y_el_aviso(db_path, monkeypatch):
    """El aviso viaja en la respuesta del panel, no solo en los logs.

    Un aviso que solo existe en el log de un servidor es un aviso que nadie lee
    hasta que ya se perdió una base.
    """
    import importlib

    import ferreteria.config as config_mod
    importlib.reload(config_mod)
    monkeypatch.setattr(
        config_mod, 'resolver_directorio_instancias',
        lambda: '/ruta/de/prueba/instancias')
    monkeypatch.setattr(
        config_mod, 'aviso_persistencia_instancias',
        lambda directorio=None: 'AVISO DE PRUEBA')

    from ferreteria import db as db_mod
    conn = __import__('sqlite3').connect(db_path)
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_alquiler(cursor)
    db_mod._crear_tablas_pedidos(cursor)
    db_mod._crear_tablas_cotizaciones(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._aplicar_migraciones(cursor)
    conn.commit()
    conn.close()

    from ferreteria.app_factory import create_app
    from ferreteria.blueprints import superadmin as panel
    importlib.reload(panel)

    app = create_app(inicializar_db=False, iniciar_hilo_dian=False)
    with app.test_client() as cliente:
        with cliente.session_transaction() as sesion:
            sesion['usuario'] = 'programador'
            sesion['rol'] = 'superadmin'

        datos = cliente.get('/api/superadmin/resumen').get_json()

    assert datos['directorio_instancias'] == '/ruta/de/prueba/instancias'
    assert datos['aviso_persistencia'] == 'AVISO DE PRUEBA'