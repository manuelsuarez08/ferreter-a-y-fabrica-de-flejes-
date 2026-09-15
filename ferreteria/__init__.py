"""Paquete de la aplicación Ferretería.

Estructura por capas siguiendo SOLID:
    config.py     -> configuración centralizada (constantes y rutas).
    db.py         -> acceso a datos (conexión SQLite) y esquema/migraciones.
    security.py   -> decoradores de autenticación/autorización.
    services/     -> lógica de dominio pura (cálculos de fleje, alquiler, equipos).
    blueprints/   -> capa de presentación HTTP (rutas agrupadas por dominio).
    app_factory.py-> composition root: crea y configura la aplicación Flask.
"""
