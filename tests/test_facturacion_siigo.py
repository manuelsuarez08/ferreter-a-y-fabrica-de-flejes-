"""Prueba de integración del flujo de venta + facturación electrónica Siigo.

Ejercita la app real con el cliente de test de Flask (sin necesidad de un
servidor ni de credenciales reales de Siigo):

  1. Login.
  2. GET /api/siigo/estado        -> configurado=False sin .env.
  3. POST /api/ventas (FE = no)   -> venta normal, estado 'no_solicitada'.
  4. POST /api/ventas (FE = sí) con cliente incompleto -> 400 y NO se vende.
  5. POST /api/ventas (FE = sí) con cliente completo   -> la venta se guarda
     localmente y queda con siigo_estado='error' (reintentable) porque no hay
     credenciales; NO se pierde la venta.
  6. POST /api/ventas/<id>/siigo  -> reintento responde 502 y marca
     `guardado_localmente: true`.
  7. Venta anulada                -> 400, no se puede facturar electrónicamente.

No toca la base real: trabaja sobre una copia temporal en %TEMP%.

Uso:
    python tests/test_facturacion_siigo.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# Base de datos temporal para no ensuciar la real (debe fijarse ANTES de crear la app).
tmpdir = tempfile.mkdtemp(prefix="fe_test_")
db_tmp = os.path.join(tmpdir, "ferreteria.db")
shutil.copy(os.path.join(RAIZ, "ferreteria.db"), db_tmp)
os.environ["FERRETERIA_DB"] = db_tmp

from ferreteria.app_factory import create_app  # noqa: E402

app = create_app()


def _preparar_datos():
    """Asegura un cliente con datos fiscales y un producto activo para vender."""
    conn = sqlite3.connect(db_tmp)
    cliente = conn.execute("SELECT id FROM clientes ORDER BY id LIMIT 1").fetchone()[0]
    cedula_original = conn.execute(
        "SELECT cedula_nit FROM clientes WHERE id = ?", (cliente,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE clientes SET email = 'cliente@test.com', tipo_documento = 'CC', "
        "telefono = COALESCE(NULLIF(telefono, ''), '3001234567') WHERE id = ?",
        (cliente,),
    )
    producto = conn.execute("SELECT id FROM productos WHERE activo = 1 LIMIT 1").fetchone()[0]
    conn.commit()
    conn.close()
    return cliente, producto, cedula_original


def main():
    fallos = []

    def check(nombre, cond, extra=""):
        print(("  OK  " if cond else " FALLA ") + nombre + (f"  [{extra}]" if extra else ""))
        if not cond:
            fallos.append(nombre)

    cliente_id, producto_id, cedula_original = _preparar_datos()
    c = app.test_client()

    print("\n[1] Login")
    r = c.post("/login", data={"usuario": "admin", "clave": "admin123"}, follow_redirects=False)
    check("login devuelve redireccion (302)", r.status_code == 302, f"status={r.status_code}")

    print("\n[2] GET /api/siigo/estado (sin .env)")
    r = c.get("/api/siigo/estado")
    data = r.get_json()
    id_fe = None
    check("responde 200", r.status_code == 200)
    check("configurado=False sin credenciales", data.get("configurado") is False, str(data))

    print("\n[3] POST /api/ventas SIN factura electronica")
    r = c.post("/api/ventas", json={
        "id_cliente": cliente_id, "tipo_pago": "efectivo", "direccion": "",
        "tipo_entrega": "entrega_inmediata", "factura_electronica": False,
        "items": [{"id_producto": producto_id, "cantidad": 1}],
    })
    d3 = r.get_json()
    check("responde 201", r.status_code == 201, f"status={r.status_code} body={d3}")
    if r.status_code == 201:
        id_normal = d3["id_venta"]
        check("no transmite a Siigo (siigo=None)", d3.get("siigo") is None)
        conn = sqlite3.connect(db_tmp)
        est = conn.execute("SELECT siigo_estado FROM ventas WHERE id=?", (id_normal,)).fetchone()[0]
        conn.close()
        check("estado local = no_solicitada", est == "no_solicitada", est)

    print("\n[4] POST /api/ventas CON FE pero cliente sin cedula (debe BLOQUEAR)")
    conn = sqlite3.connect(db_tmp)
    conn.execute("UPDATE clientes SET cedula_nit='' WHERE id=?", (cliente_id,))
    conn.commit()
    conn.close()
    r = c.post("/api/ventas", json={
        "id_cliente": cliente_id, "tipo_pago": "efectivo", "direccion": "",
        "tipo_entrega": "entrega_inmediata", "factura_electronica": True,
        "items": [{"id_producto": producto_id, "cantidad": 1}],
    })
    d4 = r.get_json()
    check("responde 400 (bloquea por datos incompletos)", r.status_code == 400, f"status={r.status_code}")
    check("indica el campo faltante", "C\u00e9dula/NIT" in (d4.get("campos_faltantes") or []), str(d4))
    conn = sqlite3.connect(db_tmp)
    conn.execute("UPDATE clientes SET cedula_nit=? WHERE id=?", (cedula_original, cliente_id))
    conn.commit()
    conn.close()

    print("\n[5] POST /api/ventas CON factura electronica (se guarda y marca error)")
    r = c.post("/api/ventas", json={
        "id_cliente": cliente_id, "tipo_pago": "efectivo", "direccion": "",
        "tipo_entrega": "entrega_inmediata", "factura_electronica": True,
        "items": [{"id_producto": producto_id, "cantidad": 1}],
    })
    d5 = r.get_json()
    check("responde 201 (la venta se guarda igual)", r.status_code == 201, f"status={r.status_code} body={d5}")
    if r.status_code == 201:
        id_fe = d5["id_venta"]
        check("siigo reporta ok=False", (d5.get("siigo") or {}).get("ok") is False, str(d5.get("siigo")))
        conn = sqlite3.connect(db_tmp)
        fila = conn.execute(
            "SELECT siigo_estado, siigo_error FROM ventas WHERE id=?", (id_fe,)
        ).fetchone()
        conn.close()
        check("estado local = error", fila[0] == "error", fila[0])
        check("guarda motivo del fallo", bool(fila[1]), str(fila[1])[:60])

        print("\n[6] POST /api/ventas/<id>/siigo (reintento)")
        r = c.post(f"/api/ventas/{id_fe}/siigo")
        d6 = r.get_json()
        check("responde 502 (sin credenciales)", r.status_code == 502, f"status={r.status_code}")
        check("indica que se guardo localmente", d6.get("guardado_localmente") is True, str(d6))

    if id_fe is None:
        print("\n[7] OMITIDA: no se creo venta con FE para probar la anulacion")
        return 1

    print("\n[7] Venta anulada no se puede facturar")
    conn = sqlite3.connect(db_tmp)
    conn.execute("UPDATE ventas SET anulada = 1 WHERE id = ?", (id_fe,))
    conn.commit()
    conn.close()
    r = c.post(f"/api/ventas/{id_fe}/siigo")
    check("responde 400 para venta anulada", r.status_code == 400,
          f"status={r.status_code} body={r.get_json()}")

    print("\n[8] Venta inexistente devuelve 404")
    r = c.post("/api/ventas/999/siigo")
    check("responde 404", r.status_code == 404, f"status={r.status_code}")

    print("\n[9] El historial expone los campos de Siigo")
    r = c.get("/api/ventas")
    check("responde 200", r.status_code == 200, f"status={r.status_code}")
    fila_fe = next((v for v in (r.get_json() or []) if v["id"] == id_fe), None)
    check("la venta con FE aparece en el historial", fila_fe is not None)
    if fila_fe:
        check("historial trae siigo_estado='error'", fila_fe.get("siigo_estado") == "error",
              str(fila_fe.get("siigo_estado")))
        check("historial trae el motivo del fallo", bool(fila_fe.get("siigo_error")))

    print("\n" + "=" * 60)
    if fallos:
        print(f"RESULTADO: {len(fallos)} FALLO(S): " + "; ".join(fallos))
        return 1
    print("RESULTADO: TODAS LAS PRUEBAS PASARON")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
