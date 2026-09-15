"""Servicios de dominio (lógica de negocio pura, sin dependencias de Flask).

Todo el cálculo y normalización que no depende del ciclo de petición HTTP
vive aquí: así es testeable de forma aislada y los blueprints quedan finos.
"""
