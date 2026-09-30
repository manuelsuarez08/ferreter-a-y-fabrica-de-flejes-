import sqlite3
c=sqlite3.connect("ferreteria.db")
print([r[1] for r in c.execute("PRAGMA table_info(productos)")])
