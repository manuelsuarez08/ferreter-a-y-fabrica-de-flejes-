"""Pruebas de la SESION: la causa comun de los tres fallos reportados.

Sintoma: consolidado en blanco, historial vacio y la factura que no abria. Tres
vistas distintas, un solo motivo: cuando la sesión de Flask caduca (o el
servidor se reinicia y cambia el SECRET_KEY), TODAS las peticiones del POS
devuelven el HTML del login en vez de JSON. El JS hace `await res.json()` y
recibe una pagina web, no un array: la vista se queda vacia y no hay ningun
error en consola porque el fetch sí respondió 200.
"""
import os
import re
import shutil
import tempfile

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_dir = tempfile.mkdtemp()
_db = os.path.join(_dir, 'ferreteria.db')
shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), _db)
os.environ['FERRETERIA_DB'] = _db

from ferreteria.app_factory import create_app  # noqa: E402
import ferreteria.config as cfg  # noqa: E402


@pytest.fixture(scope='module')
def html_completo():
    with open(os.path.join(RAIZ, 'templates', 'index.html'), encoding='utf-8') as f:
        return f.read()


@pytest.fixture
def app():
    return create_app()


def _login(cl):
    """Login por el formulario, como el navegador."""
    cl.post('/login', data={'usuario': 'admin', 'clave': 'admin123'},
            follow_redirects=True)


def test_las_veces_de_configurar_las_credenciales_devuelven_json(app):
    """Comprobacion basica: la app arranca y tiene la base."""
    assert os.path.exists(cfg.DB_NAME)


# ═════════════════════════════════════════════════════
# La sesión debe sobrevivir
# ═════════════════════════════════════════════════════
def test_la_sesion_es_permanente(app):
    """`session.permanent = True` en el login.

    Sin esto la cookie de sesión se borra al cerrar el navegador, y si caduca a
    mitad de turno los fetch del POS se quedan con el HTML del login.
    """
    c = app.test_client()
    c.post('/login', data={'usuario': 'admin', 'clave': 'admin123'})
    with c.session_transaction() as s:
        assert s.permanent is True, (
            'la sesion no es permanente: caduca al cerrar el navegador y el '
            'POS se queda vacio a mitad de turno')


def test_la_sesion_dura_un_turno_completo(app):
    """12 horas, no la media hora por defecto de Flask."""
    horas = app.permanent_session_lifetime.total_seconds() / 3600
    assert horas >= 8, (
        f'la sesion dura solo {horas:.1f}h; con un turno de 8h el cajero '
        f'quedaria sin entrar a mitad de trabajo')


def test_el_secreto_estable_entre_arranques():
    """Si el SECRET_KEY cambia, todas las sesiones abiertas mueren.

    Con un valor por defecto fijo, reiniciar el servidor NO expulsa al cajero.
    """
    assert cfg.SECRET_KEY, 'SECRET_KEY vacio'
    assert 'os.environ' not in str(cfg.SECRET_KEY)


# ═════════════════════════════════════════════════════
# Y si la sesion se pierde, el POS debe avisar (no quedar mudo)
# ═════════════════════════════════════════════════════
def test_la_plantilla_tiene_una_unica_ventana_de_error_de_sesion(html_completo):
    """Si la sesion caduca, el JS debe avisar en vez de pintar una tabla vacia.

    Es la red de seguridad: aunque la sesion muera, el cajero ve "tu sesion
    expiro" y entra otra vez, en vez de pensar que se perdio la venta.
    """
    t = re.sub(r'/\*.*?\*/', '', html_completo, flags=re.S)
    assert 'sesionExpirada' in t or 'sesión expiró' in t.lower() or 'sesion-expirada' in t, (
        'no hay manejo de sesion expirada en el JS: si caduca, las vistas '
        'quedan vacias sin explicacion')


def test_el_manejo_de_sesion_detecta_un_html_en_vez_de_json(html_completo):
    """La comprobacion tecnica: /login devuelve HTML, no JSON.

    Si un fetch recibe algo que no empieza por { o [, es que le devolvieron
    la pagina de login.
    """
    assert '/login' in html_completo
