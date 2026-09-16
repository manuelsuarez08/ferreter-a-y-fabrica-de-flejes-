"""Crea (o actualiza) un usuario con rol 'pedidos'.

Se ejecuta una sola vez. Usa el mismo hashing de la app (werkzeug) para que la
contraseña quede segura, igual que si se creara desde la pestaña Administración.

Uso:
    python crear_usuario_pedidos.py                 # usuario 'pedidos', clave 'pedidos123'
    python crear_usuario_pedidos.py <usuario> <clave>
"""
import sys

from werkzeug.security import generate_password_hash

from ferreteria.db import get_db

USUARIO = sys.argv[1] if len(sys.argv) > 1 else 'pedidos'
CLAVE = sys.argv[2] if len(sys.argv) > 2 else 'pedidos123'
ROL = 'pedidos'

conn = get_db()
cursor = conn.cursor()
fila = cursor.execute("SELECT id FROM usuarios WHERE usuario = ?", (USUARIO,)).fetchone()

if fila:
    cursor.execute(
        "UPDATE usuarios SET clave = ?, rol = ? WHERE id = ?",
        (generate_password_hash(CLAVE), ROL, fila[0]),
    )
    print(f"Usuario '{USUARIO}' actualizado a rol '{ROL}'.")
else:
    cursor.execute(
        "INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
        (USUARIO, generate_password_hash(CLAVE), ROL),
    )
    print(f"Usuario '{USUARIO}' creado con rol '{ROL}'.")

conn.commit()
conn.close()
print(f"  Usuario: {USUARIO}")
print(f"  Clave:   {CLAVE}")
print("  (puede cambiarla luego desde Administración)")
