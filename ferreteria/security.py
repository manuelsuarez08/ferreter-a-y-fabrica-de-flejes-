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


# El rol del desarrollador. Deliberadamente NO es 'admin': un admin de ferretería
# administra SU negocio, no el padrón de clientes de los demás. Mezclarlos haría
# que un cliente pudiera suspender o leer los datos de otro cliente, que es
# justamente lo que la arquitectura de una base por ferretería evita.
ROL_SUPERADMIN = 'superadmin'


def superadmin_required(view):
    """Exige rol de desarrollador (SuperAdmin).

    A diferencia de `login_required` y `admin_required`, no basta con tener
    sesión: un usuario sin sesión recibe 401 y un rol incorrecto recibe 403. La
    diferencia importa en la auditoría: "no estaba conectado" y "estaba
    conectado pero no le correspondía" son hechos distintos.
    """
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if 'usuario' not in session:
            return jsonify({'error': 'Debe iniciar sesión'}), 401
        if session.get('rol') != ROL_SUPERADMIN:
            return jsonify({
                'error': 'Acceso denegado. Esta pantalla es del desarrollador del software.',
            }), 403
        return view(*args, **kwargs)
    return wrapped_view
