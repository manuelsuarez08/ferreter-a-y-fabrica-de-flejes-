"""Servicio de cálculo de alquiler de maquinaria.

Convierte un rango de fechas y una tarifa en un importe. Función pura para
poder probarla y reutilizarla desde las rutas de alquileres.
"""
from datetime import datetime

from ..config import TIPOS_TARIFA  # noqa: F401  (re-exportado en __all__)


def calcular_total_alquiler(fecha_inicio, fecha_fin, tarifa_tipo, tarifa_valor, cantidad=1):
    """Devuelve el importe de alquiler según el tipo de tarifa (día/hora/turno/bulto)."""
    try:
        inicio = datetime.fromisoformat(str(fecha_inicio).replace('Z', '+00:00'))
        fin = datetime.fromisoformat(str(fecha_fin).replace('Z', '+00:00'))
    except ValueError:
        return 0

    delta = fin - inicio
    cantidad = max(1, int(cantidad or 1))
    tarifa_valor = float(tarifa_valor or 0)

    if tarifa_tipo == 'dia':
        dias = max(1, delta.days + (1 if delta.seconds > 0 else 0))
        return round(dias * tarifa_valor * cantidad, 2)
    if tarifa_tipo == 'hora':
        horas = max(1, int(delta.total_seconds() / 3600))
        return round(horas * tarifa_valor * cantidad, 2)
    if tarifa_tipo == 'turno':
        horas = max(1, int(delta.total_seconds() / 3600))
        turnos = max(1, int(horas / 8))
        return round(turnos * tarifa_valor * cantidad, 2)
    # 'bulto' y cualquier otro tipo caen aquí (precio por unidad).
    return round(tarifa_valor * cantidad, 2)


__all__ = ['calcular_total_alquiler', 'TIPOS_TARIFA']
