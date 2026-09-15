"""Servicio de auditoría.

Centraliza el registro de acciones en la tabla `auditoria`. Usa la sesión de
Flask para resolver el usuario, de modo que ningún handler tenga que repetir
el `session.get('usuario', 'sistema')`.
"""
from datetime import datetime

from flask import session


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def registrar_auditoria(conn, accion, entidad, entidad_id=None, detalles=''):
    """Inserta una fila de auditoría en la conexión dada (sin commit)."""
    conn.execute(
        """
        INSERT INTO auditoria (usuario, accion, entidad, entidad_id, detalles, fecha)
        VALUES (:usuario, :accion, :entidad, :entidad_id, :detalles, :fecha)
        """,
        {
            'usuario': session.get('usuario', 'sistema'),
            'accion': accion,
            'entidad': entidad,
            'entidad_id': entidad_id,
            'detalles': detalles,
            'fecha': _ahora(),
        },
    )
