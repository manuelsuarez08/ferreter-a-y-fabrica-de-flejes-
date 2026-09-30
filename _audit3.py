import sqlite3
c=sqlite3.connect("ferreteria.db")
q=lambda s: c.execute(s).fetchone()[0]
print("total", q("select count(*) from productos"))
print("con codigo_barras", q("select count(*) from productos where coalesce(codigo_barras,'')<>''"))
print("con unidad_medida", q("select count(*) from productos where coalesce(unidad_medida,'')<>''"))
print("categorias distintas", q("select count(distinct coalesce(categoria,'')) from productos"))
print("con precio>0", q("select count(*) from productos where coalesce(precio_venta,0)>0"))
print("inactivos", q("select count(*) from productos where activo=0"))
for r in c.execute("select coalesce(unidad_medida,'(vacia)'), count(*) from productos group by 1 order by 2 desc limit 20"): print("UMD", r)
for r in c.execute("select coalesce(precio_venta,0) p, count(*) from productos group by 1 order by 2 desc limit 5"): print("PRECIO", r)
