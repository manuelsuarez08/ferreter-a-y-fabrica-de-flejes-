import sqlite3
c=sqlite3.connect("ferreteria.db")
for r in c.execute("select coalesce(categoria,'(sin categoria)'), count(*), sum(case when coalesce(precio_venta,0)>0 then 1 else 0 end) from productos group by 1 order by 2 desc"): print(r)
