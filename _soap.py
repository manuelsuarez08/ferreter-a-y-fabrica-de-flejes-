import time

from ferreteria.services import dian_soap

for segundos in (30, 60, 90):
    t = time.time()
    disponible, detalle = dian_soap.probar_conexion(ambiente='2', timeout=segundos)
    print(f'timeout={segundos:3}s  {time.time() - t:5.1f}s  disponible={disponible}')
    print(f'   {detalle[:160]}')
    if disponible:
        break
