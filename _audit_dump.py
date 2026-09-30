import sqlite3, collections
c = sqlite3.connect('ferreteria.db')
c.text_factory = str
print('--- UMD ---')
for r in c.execute("SELECT COALESCE(NULLIF(TRIM(unidad_medida),''),'(vacio)'), COUNT(*) FROM productos GROUP BY 1 ORDER BY 2 DESC"):
    print(r)
print('--- iva_tasa ---')
for r in c.execute("SELECT iva_tasa, COUNT(*) FROM productos GROUP BY 1 ORDER BY 2 DESC"):
    print(r)
print('--- iva_tipo_tarifa ---')
for r in c.execute("SELECT iva_tipo_tarifa, COUNT(*) FROM productos GROUP BY 1"):
    print(r)
print('--- naturaleza ---')
for r in c.execute("SELECT iva_naturaleza, COUNT(*) FROM productos GROUP BY 1"):
    print(r)
print('--- precio_base vs precio_venta (muestra) ---')
for r in c.execute("SELECT id,nombre,categoria,precio_venta,precio_base,iva_tasa,iva_valor FROM productos ORDER BY id LIMIT 30"):
    print(r)
print('--- sin clasificar (muestra 80) ---')
for r in c.execute("SELECT id, nombre, precio_venta, IFNULL(unidad_medida,'') FROM productos WHERE categoria LIKE '%Sin clasificar%' OR categoria IN ('','Sin Categoría') ORDER BY nombre LIMIT 60"):
    print(r)
