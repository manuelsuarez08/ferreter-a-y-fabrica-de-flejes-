"""Servicio de normalización de datos de equipos de alquiler.

Traduce el payload flexible que envía el front-end (con múltiples alias de
campo) a un diccionario canónico con tipos y valores válidos. Función pura,
sin acceso a base de datos ni a la petición HTTP.
"""
from ..config import ESTADOS_EQUIPO, TIPOS_TARIFA


def _texto(payload, *claves, default=''):
    for clave in claves:
        valor = payload.get(clave)
        if valor is not None and str(valor).strip():
            return str(valor).strip()
    return default


def _numero(payload, *claves, default=0.0):
    for clave in claves:
        valor = payload.get(clave)
        if valor is not None and str(valor).strip():
            try:
                return float(valor)
            except (ValueError, TypeError):
                continue
    return default


def _entero(payload, *claves, default=1):
    for clave in claves:
        valor = payload.get(clave)
        if valor is not None and str(valor).strip():
            try:
                return int(float(valor))
            except (ValueError, TypeError):
                continue
    return default


def normalizar_datos_equipo(data):
    """Devuelve un dict canónico con los datos de un equipo ya saneados."""
    payload = data or {}

    nombre = _texto(payload, 'nombre', 'equipo')
    codigo = _texto(payload, 'codigo_interno', 'codigo')
    categoria = _texto(payload, 'categoria')

    tipo_tarifa = _texto(payload, 'tipo_tarifa', 'tarifa_tipo', default='dia') or 'dia'
    if tipo_tarifa not in TIPOS_TARIFA:
        tipo_tarifa = 'dia'

    estado = _texto(payload, 'estado', default='Disponible') or 'Disponible'
    if estado not in ESTADOS_EQUIPO:
        estado = 'Disponible'

    cantidad_disponible = _entero(payload, 'cantidad_disponible', 'cantidad_total', default=1)
    cantidad_total = _entero(payload, 'cantidad_total', default=cantidad_disponible or 1)

    return {
        'nombre': nombre,
        'codigo_interno': codigo,
        'categoria': categoria,
        'marca': _texto(payload, 'marca'),
        'modelo': _texto(payload, 'modelo'),
        'numero_serie': _texto(payload, 'numero_serie'),
        'estado': estado,
        'tipo_tarifa': tipo_tarifa,
        'tarifa': _numero(payload, 'tarifa', 'tarifa_dia', 'tarifa_valor'),
        'tarifa_hora': _numero(payload, 'tarifa_hora'),
        'tarifa_turno': _numero(payload, 'tarifa_turno'),
        'tarifa_bulto': _numero(payload, 'tarifa_bulto'),
        'medidas': _texto(payload, 'medidas'),
        'especificaciones': _texto(payload, 'especificaciones', 'especificacion'),
        'cantidad_disponible': max(0, cantidad_disponible),
        'cantidad_total': max(0, cantidad_total),
        'fecha_compra': _texto(payload, 'fecha_compra') or None,
        'fecha_ultimo_mantenimiento': _texto(payload, 'fecha_ultimo_mantenimiento') or None,
        'observaciones': _texto(payload, 'observaciones'),
        'activo': 1,
    }
