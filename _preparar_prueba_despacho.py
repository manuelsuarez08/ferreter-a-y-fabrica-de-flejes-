"""Prepara datos de prueba para el flujo de despachos."""
from werkzeug.security import generate_password_hash
from ferreteria.db import get_db

conn = get_db()
c = conn.cursor()
# Usuarios de prueba
for user, rol in (('bodega_test', 'bodega'), ('moto_test', 'motocarguero')):
    fila = c.execute("SELECT id FROM usuarios WHERE usuario = ?", (user,)).fetchone()
    if fila:
        c.execute("UPDATE usuarios SET clave = ?, rol = ? WHERE id = ?",
                  (generate_password_hash('test123'), rol, fila[0]))
    else:
        c.execute("INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
                  (user, generate_password_hash('test123'), rol))

rows = c.execute(
    "SELECT id, numero_pedido, COALESCE(estado_despacho,'?'), total_venta, saldo_pendiente "
    "FROM ventas WHERE tipo_entrega = 'para_llevar' AND anulada = 0 ORDER BY id DESC LIMIT 10"
).fetchall()
print('Ventas para_llevar:', rows)

if not [r for r in rows if r[2] == 'pendiente_preparar']:
    num = c.execute("SELECT COALESCE(MAX(numero_pedido), 0) + 1 FROM ventas").fetchone()[0]
    total = 50000.0
    sql = (
        "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, saldo_pendiente, "
        "tipo_pago, direccion_cliente, tipo_entrega, numero_pedido, estado_despacho) "
        "VALUES (:cli, '2026-01-01', '10:00', :tot, :saldo, 'efectivo', "
        "'Calle Prueba #123', 'para_llevar', :num, 'pendiente_preparar')"
    )
    c.execute(sql, {'cli': 1, 'tot': total, 'saldo': total, 'num': num})
    id_venta = c.lastrowid
    prod = c.execute("SELECT id FROM productos WHERE COALESCE(activo,1)=1 ORDER BY id LIMIT 1").fetchone()
    c.execute(
        "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, precio_unitario, subtotal) "
        "VALUES (?, ?, 2, ?, ?)",
        (id_venta, prod[0], total / 2, total),
    )
    print('Venta creada id={} #ped={}'.format(id_venta, num))

conn.commit()
print('Usuarios:', c.execute("SELECT usuario, rol FROM usuarios").fetchall())
conn.close()
