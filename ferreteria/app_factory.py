"""Composition root: crea y configura la aplicación Flask.

Patrón Application Factory: centraliza el cableado (config + blueprints) sin
que los módulos se conozcan entre sí. app.py solo delega aquí.
"""
from datetime import timedelta

from flask import Flask
from .config import SECRET_KEY, SESION_HORAS
from .db import asegurar_base_de_datos, init_db, respaldar_base_de_datos
from .blueprints import (alquiler, catalogo, core, cotizaciones, dian_notas,
                         dian_pos, fabrica, pedidos, ventas)


def create_app(inicializar_db=True, iniciar_hilo_dian=True):
    """Crea la app Flask, registra los blueprints y (opcionalmente) el esquema.

    Args:
        inicializar_db: si es True, garantiza el esquema de la base de datos.
            Se deja parametrizable para no crear/consultar la BD al importar en
            contextos donde no se desea (p. ej. herramientas o tests).
        iniciar_hilo_dian: si es True, arranca el hilo que reintenta solo los
            documentos en contingencia. Se apaga en los tests: un hilo con
            temporizador en segundo plano hace la suite intermitente.
    """
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key = SECRET_KEY

    # ── Sesión duradera ──────────────────────────────────────────────────────
    # Sin esto, la cookie de sesión es de navegador: al cerrarlo, o al reiniciar
    # el servidor, el usuario pierde la sesión y TODO fetch() se queda con el
    # HTML del login en vez de JSON. Como los datos del POS se piden por fetch,
    # la pantalla se ve "en blanco" o vacía sin que se note la causa: es el POS
    # entero, no una vista.
    # Con `PERMANENT_SESSION_LIFETIME` la sesión sobrevive al cierre del
    # navegador durante 12 horas, que es lo que dura un turno de mostrador.
    app.permanent_session_lifetime = timedelta(hours=SESION_HORAS)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
    )

    # Un blueprint por dominio de negocio.
    core.registrar(app)
    catalogo.registrar(app)
    ventas.registrar(app)
    fabrica.registrar(app)
    pedidos.registrar(app)
    alquiler.registrar(app)
    cotizaciones.registrar(app)
    dian_pos.registrar(app)
    # Notas crédito electrónicas y documentos soporte a no obligados.
    dian_notas.registrar(app)

    if inicializar_db:
        # Si DB_NAME apunta a un disco persistente vacío, se siembra desde el
        # repositorio antes de garantizar el esquema.
        asegurar_base_de_datos()
        # Red de seguridad: copia de la base ANTES de cualquier migracion, para
        # no perder datos si algo sale mal al actualizar.
        respaldo = respaldar_base_de_datos()
        if respaldo:
            app.logger.info('Respaldo de seguridad creado: %s', respaldo)
        init_db()

    # Reintento automático de los documentos en contingencia.
    #
    # Sin esto, un documento que quedó en contingencia porque se cayó el
    # internet se quedaba sin enviar PARA SIEMPRE: la venta estaba cobrada y
    # el inventario descontado, pero el documento nunca llegaba a la DIAN. Solo
    # había un botón manual que el admin tenía que acordarse de pulsar. El hilo
    # se encarga solo, en segundo plano, sin molestar al cajero.
    #
    # Va FUERA del `if inicializar_db`: dentro, `create_app(False)` —la forma
    # en que los tests y las herramientas abren la app— se quedaría sin
    # `return app` y devolvería None.
    #
    # Los tests lo apagan con `iniciar_hilo_dian=False`: un hilo con
    # temporizador hace la suite intermitente.
    if iniciar_hilo_dian:
        from .services import dian_reintento
        dian_reintento.iniciar(app)

    return app
