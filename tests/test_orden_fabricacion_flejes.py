"""Una venta con productos de categoría Fleje genera UNA orden de fabricación."""
import pytest

import app


@pytest.fixture()
def cliente_http():
    c = app.server.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'admin'
        s['rol'] = 'admin'
    return c


def _crear_producto(nombre, categoria, dimensiones=''):
    from ferreteria.db import get_db
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO productos (nombre, categoria, dimensiones, precio_costo, precio_venta, "
            "stock_actual, stock_minimo) VALUES (?, ?, ?, 500, 1000, 100, 1)",
            (nombre, categoria, dimensiones))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_venta_con_flejes_crea_una_orden_con_todos_los_flejes(cliente_http):
    f1 = _crear_producto('Fleje 10x10 cal 8', 'Flejes', '10x10 cm')
    f2 = _crear_producto('Fleje 15x15 cal 8', 'Fleje', '15x15 cm')
    otro = _crear_producto('Tornillo', 'Tornilleria')

    r = cliente_http.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo', 'direccion': 'Obra Calle 5',
        'observaciones': 'Entregar antes del viernes',
        'items': [{'id_producto': f1, 'cantidad': 200},
                  {'id_producto': f2, 'cantidad': 50},
                  {'id_producto': otro, 'cantidad': 3}],
    })
    assert r.status_code == 201, r.json
    id_venta = r.json['id_venta']
    assert 'fábrica' in r.json['mensaje']

    ordenes = [o for o in cliente_http.get('/api/fabrica/ordenes').json
               if o['id_venta'] == id_venta]
    assert len(ordenes) == 1
    o = ordenes[0]
    assert o['estado'] == 'En Cola'
    assert o['direccion_entrega'] == 'Obra Calle 5'
    assert o['nota_cliente'] == 'Entregar antes del viernes'
    assert [(i['producto'], i['cantidad'], i['dimensiones']) for i in o['items']] == [
        ('Fleje 10x10 cal 8', 200, '10x10 cm'), ('Fleje 15x15 cal 8', 50, '15x15 cm')]

    for estado in ('En Figurado', 'Completado'):
        r = cliente_http.put(f"/api/fabrica/ordenes/{o['id']}/estado", json={'estado': estado})
        assert r.status_code == 200


def test_venta_sin_flejes_no_crea_orden(cliente_http):
    otro = _crear_producto('Clavo', 'Tornilleria')
    r = cliente_http.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': otro, 'cantidad': 1}]})
    assert r.status_code == 201
    assert all(o['id_venta'] != r.json['id_venta']
               for o in cliente_http.get('/api/fabrica/ordenes').json)
