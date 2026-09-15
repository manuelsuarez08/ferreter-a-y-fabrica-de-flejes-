"""Composition root: crea y configura la aplicación Flask.

Patrón Application Factory: centraliza el cableado (config + blueprints) sin
que los módulos se conozcan entre sí. app.py solo delega aquí.
"""
from flask import Flask

from .config import SECRET_KEY
from .db import asegurar_base_de_datos, init_db
from .blueprints import alquiler, catalogo, core, fabrica, pedidos, ventas


def create_app(inicializar_db=True):
    """Crea la app Flask, registra los blueprints y (opcionalmente) el esquema.

    Args:
        inicializar_db: si es True, garantiza el esquema de la base de datos.
            Se deja parametrizable para no crear/consultar la BD al importar en
            contextos donde no se desea (p. ej. herramientas o tests).
    """
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key = SECRET_KEY

    # Un blueprint por dominio de negocio.
    core.registrar(app)
    catalogo.registrar(app)
    ventas.registrar(app)
    fabrica.registrar(app)
    pedidos.registrar(app)
    alquiler.registrar(app)

    if inicializar_db:
        # Si DB_NAME apunta a un disco persistente vacío, se siembra desde el
        # repositorio antes de garantizar el esquema.
        asegurar_base_de_datos()
        init_db()

    return app
