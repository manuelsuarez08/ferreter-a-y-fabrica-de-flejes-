"""Blueprints HTTP, agrupados por dominio de negocio.

Cada módulo expone una función `registrar(app)` (patrón registro) para que el
composition root (app_factory) los conecte sin acoplar la app a los módulos.
"""
