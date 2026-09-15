"""Punto de entrada de la aplicación Ferretería (composition root).

Este archivo era un monolito de ~2.967 líneas con configuración, acceso a
datos, lógica de negocio y ~50 rutas mezcladas. Tras el refactor solo cumple
una responsabilidad: exponer la aplicación y las utilidades usadas por
scripts externos, delegando en el paquete `ferreteria`.

Estructura resultante:
    ferreteria/config.py              configuración
    ferreteria/db.py                  conexión SQLite + esquema/migraciones
    ferreteria/security.py            decoradores de auth
    ferreteria/services/              lógica de dominio (fleje, alquiler, equipos...)
    ferreteria/blueprints/            rutas HTTP por dominio
    ferreteria/app_factory.py         cableado (Application Factory)

Se mantienen aquí los nombres históricos (`app`, `server`, `get_db`,
`init_db`, ...) como re-exportaciones para no romper imports existentes ni el
arranque de Gunicorn/Render (`app:server`).
"""
import os

# --- Re-exportaciones de compatibilidad hacia atrás -------------------------
# Scripts como database.py y cargar_datos.py hacen `from app import init_db`.
from ferreteria.config import BASE_DIR, DB_NAME, SECRET_KEY  # noqa: F401
from ferreteria.db import get_db, init_db  # noqa: F401
from ferreteria.security import admin_required, login_required, rol_required  # noqa: F401
from ferreteria.app_factory import create_app

# Construcción de la aplicación (registra blueprints y garantiza el esquema).
app = create_app()

# Alias que servidores como Render/Gunicorn detectan automáticamente (app:server).
server = app


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
