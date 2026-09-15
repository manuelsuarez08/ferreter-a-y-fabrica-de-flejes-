"""Decoradores de autenticación y autorización.

Encapsulan las reglas de acceso (¿hay sesión?, ¿qué rol?) lejos de los
handlers. Antes estaban en app.py mezclados con las rutas; ahora son una
preocupación aislada (SRP) y reutilizables por cualquier blueprint.
"""
from functools import wraps

from flask import jsonify, session


def login_required(view):
    """Exige que exista una sesión iniciada."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'usuario' not in session:
            return jsonify({'error': 'Debe iniciar sesión'}), 401
        return view(*args, **kwargs)
    return wrapped_view


def rol_required(*roles_permitidos):
    """Permite el acceso solo a los roles indicados."""
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if 'usuario' not in session:
                return jsonify({'error': 'Debe iniciar sesión'}), 401
            if session.get('rol') not in roles_permitidos:
                mensaje = f'Acceso denegado. Se requiere uno de estos roles: {", ".join(roles_permitidos)}'
                return jsonify({'error': mensaje}), 403
            return view(*args, **kwargs)
        return wrapped_view
    return decorator


def admin_required(view):
    """Permite el acceso solo al rol administrador."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get('rol') != 'admin':
            return jsonify({'error': 'Acceso denegado. Solo el administrador puede realizar esta acción.'}), 403
        return view(*args, **kwargs)
    return wrapped_view
