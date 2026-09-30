"""Prueba de regresión del fallo REAL que se reportó en produccion.

Síntoma: el historial de ventas salía vacío y el modal de factura no abría,
sin ningún error en la consola. Tres sintomas distintos, una sola causa.

Causa: `badgeFacturacion()` se USA dentro de la plantilla que arma cada fila
del historial, pero la FUNCION NO EXISTIA. El commit c6d034a (facturación DIAN
propia) borro la version antigua —que leia campos de Siigo— y dejo la llamada
intacta. Un `ReferenceError` ahi aborta el `forEach` completo, y como
`cargarHistorialVentas` es `async`, la excepcion se pierde como promesa
rechazada: no llega ni a la consola.

Este modulo fija:
  1. Que toda funcion llamada desde un onclick exista.
  2. Que badgeFacturacion exista y pinte algo.
  3. Que GET /api/ventas devuelva las ventas (el backend nunca estuvo roto).
"""
import io
import os
import re
import shutil
import tempfile

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLA = os.path.join(RAIZ, 'templates', 'index.html')
DIAN_JS = os.path.join(RAIZ, 'static', 'js', 'dian-pos.js')

_dir = tempfile.mkdtemp()
_db = os.path.join(_dir, 'ferreteria.db')
shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), _db)
os.environ['FERRETERIA_DB'] = _db

from ferreteria.app_factory import create_app  # noqa: E402


@pytest.fixture(scope='module')
def codigo():
    """El script del POS, sin comentarios (los comentarios nombran funciones
    viejas y darian falsos positivos)."""
    with io.open(PLANTILLA, encoding='utf-8') as f:
        html = f.read()
    bloques = re.findall(r'<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>', html, re.S)
    pos = next(b for b in bloques if 'function verFactura' in b)
    t = re.sub(r'/\*.*?\*/', '', pos, flags=re.S)
    return re.sub(r'(?m)//[^\n]*$', '', t)


@pytest.fixture(scope='module')
def html_completo():
    with io.open(PLANTILLA, encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def codigo_html():
    """La plantilla entera sin comentarios."""
    with io.open(PLANTILLA, encoding='utf-8') as f:
        html = f.read()
    t = re.sub(r'/\*.*?\*/', '', html, flags=re.S)
    return re.sub(r'(?m)//[^\n]*$', '', t)


@pytest.fixture(scope='module')
def definidas(codigo):
    d = set(re.findall(r'function\s+(\w+)\s*\(', codigo))
    # `const x = debounce(function...)` y `const x = async () =>` NO matchean
    # el patron de arriba. Sin esto, `filtrarCartera` y `debounce` saldrían
    # como inexistentes y el test perdería valor.
    d |= set(re.findall(r'(?:const|let|var)\s+(\w+)\s*=', codigo))
    if os.path.exists(DIAN_JS):
        with io.open(DIAN_JS, encoding='utf-8') as f:
            t = f.read()
        d |= set(re.findall(r'function\s+(\w+)\s*\(', t))
        # dian-pos.js se publica como window.DianPos = { metodo() {...} }
        d |= set(re.findall(r'^\s{2}(\w+)\s*[:(]', t, re.M))
    return d


# ═════════════════════════════════════════════════════
# La causa raiz
# ═════════════════════════════════════════════════════
def test_badge_facturacion_existe(codigo):
    """LA FUNCION QUE FALTA. Sin ella el historial sale vacio y la factura
    no abre."""
    assert 'function badgeFacturacion' in codigo, (
        'badgeFacturacion se usa al pintar el historial pero no esta definida: '
        'eso deja la tabla sin filas y verFactura revienta a media carga')


def test_badge_facturacion_se_usa_en_el_historial(codigo):
    """Y tiene que seguir usandose: es lo que muestra el estado de la DIAN."""
    usos = re.findall(r'badgeFacturacion\s*\(', codigo)
    # 1 definicion + al menos 1 llamada
    assert len(usos) >= 2, 'badgeFacturacion quedo definida pero sin usarse'


def test_badge_facturacion_no_depende_de_campos_de_siigo(codigo):
    """La version vieja leia `numero_factura` / `dian_estado` de Siigo.

    Ese modulo ya no existe. Si alguien restaura la version antigua, estas
    columnasarian a `undefined` y el badge saldria mudo.
    """
    m = re.search(r'function\s+badgeFacturacion\s*\([^)]*\)\s*\{', codigo)
    cuerpo = codigo[m.end():m.end() + 1800]
    assert 'numero_factura' not in cuerpo
    assert 'prefijo' not in cuerpo
    # Debe leerse del estado DIAN propio.
    assert 'dian_estado' in cuerpo


def test_badge_facturacion_maneja_los_estados_que_devuelve_la_api(codigo):
    m = re.search(r'function\s+badgeFacturacion\s*\([^)]*\)\s*\{', codigo)
    cuerpo = codigo[m.end():m.end() + 1800]
    for estado in ('aceptado', 'rechazado', 'contingencia', 'error', 'sin_emitir'):
        assert estado in cuerpo, f'falta el estado {estado}'


# ═════════════════════════════════════════════════════
# Barrido general: ninguna funcion huerfana
# ═════════════════════════════════════════════════════
def test_ninguna_funcion_de_onclick_esta_sin_definir(html_completo, definidas):
    """El modo de falla real: una llamada huerfana dentro de un onclick.

    Un boton que no responde delata el ReferenceError; una llamada dentro de
    una funcion async (como el pintado de una tabla) no delata nada.
    """
    externas = {'bootstrap', 'TomSelect', 'html2pdf', 'Html5Qrcode', 'Swal',
                '$', 'jQuery', 'getOrCreateInstance', 'getInstance'}
    huerfanas = set()
    for m in re.finditer(r'on(?:click|change|input|submit)="([^"]*)"', html_completo):
        for n in re.findall(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(', m.group(1)):
            if n in externas or n in definidas:
                continue
            huerfanas.add(n)
    assert not huerfanas, f'funciones de onclick sin definir: {sorted(huerfanas)}'


def test_el_historial_se_pinta_tras_la_venta(codigo):
    """La cadena del historial debe seguir completa de punta a punta."""
    for pieza in ('async function cargarHistorialVentas', 'function pintarHistorialVentas',
                  'pintarHistorialVentas(historialVentasLista)',
                  "getElementById('tabla-historial-body')"):
        assert pieza in codigo, f'falta {pieza} en la cadena del historial'


def test_ver_factura_llega_al_modal(codigo):
    """La cadena de la factura debe llegar hasta bootstrap .show().

    OJO: verFactura NO usa el badge (ese es solo del historial). Lo que se fija
    aqui es que la funcion no se interrumpa antes de abrir el modal.
    """
    m = re.search(r'async\s+function\s+verFactura', codigo)
    assert m
    cuerpo = codigo[m.start():m.start() + 9000]
    assert "bootstrap.Modal(document.getElementById('modalFactura')).show()" in cuerpo
    # El pintado del bloque DIAN es lo ultimo antes de abrir: si falla, no abre.
    assert 'DianPos.pintarBloque' in cuerpo


# ═════════════════════════════════════════════════════
# El backend: nunca estuvo roto, pero se fija
# ═════════════════════════════════════════════════════
@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def admin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['usuario'] = 'admin'
        s['rol'] = 'admin'
        s['id_usuario'] = 1
    return c


def test_una_venta_registrada_aparece_en_el_historial(admin):
    """Guardar y luego leer: si esto pasa, el backend de ventas esta bien."""
    prod = admin.get('/api/productos?limite=1').get_json()[0]
    res = admin.post('/api/ventas', json={
        'id_cliente': 1, 'tipo_pago': 'efectivo',
        'items': [{'id_producto': prod['id'], 'cantidad': 1,
                   'precio_unitario': prod['precio_venta']}]})
    assert res.status_code == 201
    id_venta = res.get_json()['id_venta']

    listado = admin.get('/api/ventas').get_json()
    assert any(v['id'] == id_venta for v in listado), 'la venta no salio en el historial'

    # Y el filtro por su fecha la trae.
    venta = next(v for v in listado if v['id'] == id_venta)
    filtrado = admin.get(f"/api/ventas?fecha={venta['fecha_dia']}").get_json()
    assert any(v['id'] == id_venta for v in filtrado)


def test_el_historial_trae_los_campos_que_usa_el_badge(admin):
    """El badge necesita `dian_estado` y `numero_dian`; si faltan, sale mudo."""
    listado = admin.get('/api/ventas').get_json()
    if not listado:
        pytest.skip('no hay ventas en esta base')
    v = listado[0]
    assert 'dian_estado' in v
    assert 'numero_dian' in v
    assert 'observaciones' in v


def test_el_detalle_de_factura_trae_lo_que_pinta_el_modal(admin):
    listado = admin.get('/api/ventas').get_json()
    if not listado:
        pytest.skip('no hay ventas en esta base')
    d = admin.get(f"/api/ventas/{listado[0]['id']}").get_json()
    for clave in ('cliente', 'items', 'total_venta', 'subtotal_venta',
                  'iva_valor', 'observaciones', 'negocio', 'dian'):
        assert clave in d, f'el modal pinta {clave} y el endpoint no lo devuelve'


# ═════════════════════════════════════════════════════
# 1. Créditos: `deuda_total` NULO dejaba la sección en blanco
# ═════════════════════════════════════════════════════
def test_deuda_total_siempre_es_numero(admin):
    """La causa del "pantallazo en blanco" en créditos.

    `SUM(v.saldo_pendiente)` sobre un LEFT JOIN sin coincidencias devuelve NULL,
    no 0. El JS hacía `c.deuda_total > 0`; con null eso da false, el deudor se
    caía del desplegable y la sección se veía vacía sin error.
    """
    filas = admin.get('/api/creditos').get_json()
    assert isinstance(filas, list)
    for f in filas:
        assert f['deuda_total'] is not None, (
            f'cliente {f["id_cliente"]} devolvio deuda_total=null')
        assert isinstance(f['deuda_total'], (int, float))


def test_un_cliente_sin_deuda_no_rompe_la_cartera(admin):
    """El caso borde: cliente sin ninguna venta a credito."""
    filas = admin.get('/api/creditos').get_json()
    sin_deuda = [f for f in filas if f['deuda_total'] == 0]
    assert sin_deuda, 'la base no tiene clientes sin deuda para probar el borde'
    for f in sin_deuda:
        # 0 es falsy en JS, pero `0 > 0` es false, que es lo correcto: un
        # cliente sin deuda no va al desplegable de deudores.
        assert f['deuda_total'] >= 0


def test_el_consolidado_responde_para_un_cliente_sin_compras(admin):
    """Debe devolver 200 con listas vacias, no romperse.

    Se usa un cliente NUEVO, sin ventas: el id 1 lo comparten otras pruebas
    de este modulo y ya puede tener ventas.
    """
    admin.post('/api/clientes', json={
        'nombre': 'Cliente Sin Compras Para Consolidado', 'cedula_nit': '609990001'})
    filas = admin.get('/api/clientes').get_json()
    nuevo = next(c for c in filas if c['cedula_nit'] == '609990001')

    res = admin.get(f"/api/creditos/{nuevo['id']}/consolidado")
    assert res.status_code == 200
    d = res.get_json()
    assert d['ventas'] == [], 'un cliente sin compras debe devolver lista vacia'
    assert d['abonos'] == []
    assert d['total_mes'] == 0
    assert d['abonos_mes'] == 0
    assert d['saldo_global'] == 0


# ═════════════════════════════════════════════════════
# 4. Tirilla de cierre termica y legible
# ═════════════════════════════════════════════════════
def test_la_tirilla_de_cierre_es_termica_y_legible(codigo_html):
    """Ya no puede salir en 12px monospace con width:300px."""
    idx = codigo_html.find("title>Cierre ' + d.fecha")
    assert idx != -1, 'no se encontro la tirilla de cierre'
    # Ventana amplia: el CSS empieza unas lineas antes del <title> y el
    # contenido termina despues del ultimo <div>.
    bloque = codigo_html[max(0, idx - 1200):idx + 4000]

    assert '@page' in bloque, 'debe declarar el ancho de la hoja'
    assert '80mm' in bloque, 'debe adaptarse al ancho de una termica de 80 mm'
    assert 'monospace' not in bloque, 'la tipografia de palo seco no es legible'
    assert 'font-weight:bold' in bloque, 'debe ir todo en negrita'
    m2 = re.search(r'body\{[^}]*font-size:(\d+)px', bloque)
    assert m2, 'no se encontro el font-size del body'
    assert int(m2.group(1)) >= 14, f'el cuerpo quedo en {m2.group(1)}px, debe ser >= 14'


def test_la_tirilla_de_cierre_escapa_el_nombre_del_negocio(codigo_html):
    """Va directo a document.write: un caracter en el nombre rompe la pagina."""
    idx = codigo_html.find("title>Cierre ' + d.fecha")
    bloque = codigo_html[max(0, idx - 1200):idx + 4000]
    assert "escapeHtml(negocio)" in bloque, (
        'el nombre del negocio debe ir escapado: rompe el HTML de la tirilla')
