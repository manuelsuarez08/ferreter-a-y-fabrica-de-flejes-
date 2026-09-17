"""Resetea la venta de prueba a pendiente_preparar."""
from ferreteria.db import get_db

conn = get_db()
conn.execute(
    "UPDATE ventas SET estado_despacho='pendiente_preparar', despacho_preparado_por=NULL, "
    "despacho_preparado_fecha=NULL, despacho_entregado_por=NULL, despacho_entregado_fecha=NULL "
    "WHERE tipo_entrega='para_llevar' AND anulada=0"
)
conn.commit()
print('Venta reseteada a pendiente_preparar')
conn.close()
