"""Resetea la venta de prueba a pendiente_preparar."""
from ferreteria.db import get_db

conn = get_db()
# OJO: se informa CUANTAS filas se resetearon. Antes se imprimia siempre
# 'reseteada' aunque no tocara ninguna, y un pedido de prueba ausente dejaba
# pasar el test de despacho en verde sin probar nada (la pantalla mostraba
# 'No hay pedidos para despachar' y el flujo quedaba sin ejercitar).
filas = conn.execute(
    "UPDATE ventas SET estado_despacho='pendiente_preparar', despacho_preparado_por=NULL, "
    "despacho_preparado_fecha=NULL, despacho_entregado_por=NULL, despacho_entregado_fecha=NULL "
    "WHERE tipo_entrega='para_llevar' AND anulada=0"
).rowcount
conn.commit()
conn.close()

if filas:
    print(f'{filas} venta(s) para llevar reseteadas a pendiente_preparar')
else:
    print('AVISO: no hay ninguna venta "para_llevar" activa que resetear; '
          'el flujo de despacho no tendra datos que ejercitar.')
