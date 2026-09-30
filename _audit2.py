import sqlite3
c=sqlite3.connect("ferreteria.db")
print([r[0] for r in c.execute("select name from sqlite_master where type='table' order by 1")])
print("prod cols:", [r[1] for r in c.execute("PRAGMA table_info(productos)")])
print("con precio:", c.execute("select count(*) from productos where coalesce(precio_venta,0)>0").fetchone())
print("con codigo:", c.execute("select count(*) from productos where coalesce(codigo,'')<>''").fetchone())
print("con unidad:", c.execute("select count(*) from productos where coalesce(unidad,'')<>''").fetchone())
for r in c.execute("select * from productos where coalesce(precio_venta,0)>0 limit 10"):
    print(r)
