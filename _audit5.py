import sqlite3
c=sqlite3.connect("ferreteria.db")
for r in c.execute("select coalesce(categoria,''), nombre, precio_venta, coalesce(unidad_medida,'') from productos where coalesce(precio_venta,0)>0 order by coalesce(categoria,''), id limit 60"):
    print(r)
