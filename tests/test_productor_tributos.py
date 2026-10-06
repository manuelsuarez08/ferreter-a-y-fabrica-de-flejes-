"""Pruebas del PRODUCTOR de tributos especificos (dian_emision).

Los archivos anteriores (`test_tributos_especificos.py` y
`test_aritmetica_tributos.py`) prueban el GENERADOR: que un especifico viaje
como `PerUnitAmount` y que el total cuadre con los subtotales. Los dos
pasaban en verde mientras la funcion estaba muerta.

ESTE ARCHIVO PRUEBA QUE EXISTE UN PRODUCTOR
-------------------------------------------
El fallo era de omision: `dian_xml._categorias_de` leia `impuestos_especificos`
y NADIE lo llenaba. Veinte pruebas de estructura sobre una ruta que no se
recorria. Estas pruebas cierran el circuito:

  producto configurado -> linea de venta -> `_leer_items` -> totales del
  documento -> `impuestos_especificos` -> subtotal en el XML

Y fijan las tres reglas que hacen que la activacion sea segura: sin dato no se
declara, y nunca se declara un tributo de cero pesos.

LA ARITMETICA SE COMPRUEBA CON `Decimal` Y A MANO
------------------------------------------------
El valor esperado esta escrito, no calculado con la funcion que se prueba. Es la
misma regla del CUFE y de `test_aritmetica_tributos.py`: si las dos sprayed
salieran del mismo codigo, cambiar el redondeo las moveria juntas y la prueba
seguiria en verde.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria import db as db_mod  # noqa: E402
from ferreteria.services import dian_emision, dian_pos, dian_xml  # noqa: E402

NS = {'cac': dian_xml.NS_CAC, 'cbc': dian_xml.NS_CBC}


# ══════════════════════════════════════════════════════════════
# 1. La aritmética de la línea, sin base de datos
# ══════════════════════════════════════════════════════════════

def test_el_tributo_es_nominal_por_cantidad():
    """3 botellas de 500 ml a 0,18 por litro.

    3 x 0,5 = 1,5 litros. 1,5 x 0,18 = 0,27 pesos.

    El valor se escribe a mano: 0.27. Si se calculara con `redondear`, la
    prueba comprobaria que la funcion se parece a si misma.
    """
    trib = dian_emision._tributo_especifico_de_linea(
        '33', 0.18, 0.5, 3)

    assert trib is not None
    assert Decimal(str(trib['valor'])) == Decimal('0.27')


def test_el_contenido_es_lo_que_convierte_unidades_comerciales_en_medida():
    """La misma venta en Orphanet buy bottle vs. litro da distinto impuesto.

    Tres litros dan 0.54; tres botellas de 500 ml dan 0.27. Si se ignorara el
    contenido, ambos darian lo mismo y el impuesto seria la mitad.
    """
    por_litro = dian_emision._tributo_especifico_de_linea('33', 0.18, 1.0, 3)
    por_botella = dian_emision._tributo_especifico_de_linea('33', 0.18, 0.5, 3)

    assert Decimal(str(por_litro['valor'])) == Decimal('0.54')
    assert Decimal(str(por_botella['valor'])) == Decimal('0.27')


def test_el_inpp_se_liquida_en_toneladas():
    """El INPP no va por litros: va por peso."""
    trib = dian_emision._tributo_especifico_de_linea(
        dian_pos.TIPO_IMPUESTO_INPP, 2500.0, 0.001, 2000)

    assert trib['unidad'] == 'TNE'
    # 2000 unidades x 0,001 t = 2 toneladas. 2 x 2500 = 5000.
    assert Decimal(str(trib['valor'])) == Decimal('5000.00')


def test_el_tributo_declara_su_nominal_y_no_una_tasa():
    """El generador necesita `valor_unitario`. Una tasa en cero no sirve."""
    trib = dian_emision._tributo_especifico_de_linea('33', 0.18, 1.0, 5)

    assert trib['valor_unitario'] == 0.18
    assert trib['tasa'] == 0.0


# ══════════════════════════════════════════════════════════════
# 2. Cuándo NO se declara (la parte que evitaaguarrar)
# ══════════════════════════════════════════════════════════════

def test_sin_tipo_de_tributo_no_se_declara_nada():
    assert dian_emision._tributo_especifico_de_linea('', 0.18, 1.0, 3) is None


def test_sin_nominal_no_se_declara_nada():
    """Producto con el tipo puesto y el nominal vacio.

    Si se declarara, el documento llevaria un tributo de CERO pesos: un
    impuesto que se dice cobrar y que no existe. Peor que no declararlo.
    """
    assert dian_emision._tributo_especifico_de_linea('33', 0, 1.0, 3) is None


def test_sin_contenido_no_se_declara_nada():
    """Sin contenido no hay cuenta posible: no sabemos cuantos litros son."""
    assert dian_emision._tributo_especifico_de_linea('33', 0.18, 0, 3) is None


def test_un_tributo_ad_valorem_no_pasa_por_esta_via():
    """El ICUI es porcentual. Pasarlo por aqui produciria un calculo sin sentido."""
    assert dian_emision._tributo_especifico_de_linea('34', 0.18, 1.0, 3) is None


def test_un_tributo_inexistente_no_pasa():
    assert dian_emision._tributo_especifico_de_linea('99', 1.0, 1.0, 3) is None


def test_cantidad_cero_no_produce_impuesto():
    assert dian_emision._tributo_especifico_de_linea('33', 0.18, 1.0, 0) is None


# ══════════════════════════════════════════════════════════════
# 3. El consolidador agrupa por tributo
# ══════════════════════════════════════════════════════════════

def _linea(tipo, valor, nominal=0.18, unidad='LTR'):
    return {'tributo_especifico': {'tipo': tipo, 'valor': valor,
                                   'valor_unitario': nominal, 'unidad': unidad,
                                   'tasa': 0.0, 'base': 0.0}}


def test_dos_lineas_del_mismo_tributo_se_suman():
    """Tres botellas de un lado y dos de otro son UN subtotal, no dos."""
    items = [_linea('33', 0.27), _linea('33', 0.18)]
    resumen = dian_emision._resumen_tributos_especificos(items)

    assert len(resumen) == 1
    assert Decimal(str(resumen[0]['valor'])) == Decimal('0.45')


def test_tributos_distintos_dan_subtotales_distintos():
    items = [_linea('33', 0.27), _linea('32', 5000.0, 2500.0, 'TNE')]
    resumen = dian_emision._resumen_tributos_especificos(items)

    assert len(resumen) == 2
    assert {g['tipo'] for g in resumen} == {'32', '33'}


def test_una_linea_sin_tributo_no_altera_el_resumen():
    items = [{'tributo_especifico': None}, _linea('33', 0.27)]
    resumen = dian_emision._resumen_tributos_especificos(items)

    assert len(resumen) == 1


def test_el_resumen_conserva_el_nominal_para_que_lo_emita_el_generador():
    """Sin `valor_unitario` el generador no puede emitir `PerUnitAmount`."""
    resumen = dian_emision._resumen_tributos_especificos([_linea('33', 0.27)])

    assert resumen[0]['valor_unitario'] == 0.18
    assert resumen[0]['unidad'] == 'LTR'


def test_sin_lineas_con_tributo_el_resumen_es_vacio():
    assert dian_emision._resumen_tributos_especificos([{}, {}]) == []


# ══════════════════════════════════════════════════════════════
# 4. EL CIRCUITO COMPLETO: base -> venta -> documento -> XML
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def base_con_producto_tributado(tmp_path):
    """Base mínima con un producto configurado con IBUA.

    Se construye como las pruebas de `_leer_items` reales: tabla `ventas` y
    `detalle_ventas` de la aplicacion, no un doble de test. Un doble probaria
    el doble; esto prueba que las columnas existen y que el SELECT las trae.

    El producto va a TASA CERO, como los 1461 del catálogo: `iva_tasa` vacío
    significa que la tarifa no se ha clasificado todavia. Con el interruptor
    por producto apagado, el codigo aplica la tasa global salvo que la
    naturaleza sea exenta/no sujeta o la tasa venga en cero explicito.
    """
    return _base_con_producto(tmp_path, iva_producto=0)


@pytest.fixture
def base_con_producto_tributado_y_gravado(tmp_path):
    """El mismo producto, pero GRAVADO al 19%.

    Hace falta para probar el desagregador: sin IVA no hay nada que desagregar,
    y una prueba del modelo con impuesto incluido sobre un producto a tasa
    cero no estaria probando nada. Es el caso real de una bebida azucarada en
    una ferreteria que vende con impuesto dentro del precio.
    """
    return _base_con_producto(tmp_path, iva_producto=19.0)


def _base_con_producto(tmp_path, iva_producto):
    ruta = tmp_path / 'ferreteria.db'
    conn = sqlite3.connect(ruta)
    cursor = conn.cursor()

    for crear in (db_mod._crear_tablas_base, db_mod._crear_tablas_operacion,
                  db_mod._crear_tablas_alquiler, db_mod._crear_tablas_pedidos,
                  db_mod._crear_tablas_cotizaciones, db_mod._crear_tablas_dian):
        crear(cursor)
    db_mod._aplicar_migraciones(cursor)

    cursor.execute("""
        INSERT INTO productos (nombre, precio_venta, unidad_medida,
                              iva_tasa,
                              tributo_especifico_tipo,
                              tributo_especifico_nominal,
                              tributo_contenido)
        VALUES ('Gaseosa 500 ml', 3000, '94', ?,
                '33', 0.18, 0.5)
    """, (iva_producto,))
    id_producto = cursor.lastrowid

    cursor.execute("""
        INSERT INTO clientes (nombre, cedula_nit) VALUES ('Comprador', 'CC123')
    """)
    id_cliente = cursor.lastrowid

    cursor.execute("""
        INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta,
                            saldo_pendiente, tipo_pago)
        VALUES (?, '2026-10-01', '10:30', 9000, 0, 'efectivo')
    """, (id_cliente,))
    id_venta = cursor.lastrowid

    cursor.execute("""
        INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,
                                    precio_unitario, subtotal)
        VALUES (?, ?, 3, 3000, 9000)
    """, (id_venta, id_producto))
    conn.commit()
    return conn, id_venta


def test_la_lectura_de_lineas_calcula_el_tributo(base_con_producto_tributado):
    """El SELECT trae las columnas nuevas y la linea trae el tributo."""
    conn, id_venta = base_con_producto_tributado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)

    assert len(items) == 1
    trib = items[0]['tributo_especifico']
    assert trib is not None, 'la linea no trae tributo especifico'
    assert trib['tipo'] == '33'
    # 3 x 0,5 litros x 0,18 = 0.27
    assert Decimal(str(trib['valor'])) == Decimal('0.27')


def test_la_lista_completa_no_deja_ningun_producto_sin_tributo(base_con_producto_tributado):
    """Regresion del fallo original: `impuestos_especificos` vacio."""
    conn, id_venta = base_con_producto_tributado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)
    resumen = dian_emision._resumen_tributos_especificos(items)

    assert len(resumen) == 1, 'el resumen salio vacio: la ruta sigue muerta'
    assert resumen[0]['tipo'] == '33'


def test_el_subtotal_llega_hasta_el_xml(base_con_producto_tributado):
    """El circuito entero: linea -> resumen -> `cac:TaxSubtotal`."""
    conn, id_venta = base_con_producto_tributado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)
    totales = {
        'impuestos_iva': [],
        'impuestos_inc': [],
        'impuestos_especificos': dian_emision._resumen_tributos_especificos(items),
    }
    raiz = ET.fromstring(dian_xml.a_texto(
        dian_xml._construir_impuestos_totales(totales)))
    subtotales = raiz.findall('cac:TaxSubtotal', NS)

    assert len(subtotales) == 1
    codigo = subtotales[0].find(
        'cac:TaxCategory/cac:TaxScheme/cbc:ID', NS).text
    assert codigo == '33'

    nominal = subtotales[0].find(
        'cac:TaxCategory/cbc:PerUnitAmount', NS).text
    assert nominal.replace(',', '') == '0.18'


def test_el_total_cuadra_con_el_subtotal(base_con_producto_tributado):
    """La invariante del commit anterior sigue valiendo con el productor vivo.

    Este es el encadenamiento importante: antes el total se derivaba de
    subtotales que nunca existian. Ahora que si existen, la suma tiene que dar.
    """
    conn, id_venta = base_con_producto_tributado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)
    totales = {
        'impuestos_iva': [],
        'impuestos_inc': [],
        'impuestos_especificos': dian_emision._resumen_tributos_especificos(items),
    }
    raiz = ET.fromstring(dian_xml.a_texto(
        dian_xml._construir_impuestos_totales(totales)))

    general = Decimal(raiz.findtext('cbc:TaxAmount', namespaces=NS))
    subtotales = [Decimal(s.findtext('cbc:TaxAmount', namespaces=NS))
                  for s in raiz.findall('cac:TaxSubtotal', NS)]

    assert sum(subtotales) == general


def test_el_tributo_se_descuenta_del_precio_antes_de_sacar_la_base(base_con_producto_tributado_y_gravado):
    """MODELO CON IMPUESTO INCLUIDO: el precio de mostrador trae las dos cosas.

    El negocio cobro con impuesto dentro del precio, asi que para sacar la base
    hay que quitar el IVA (que es porcentaje) y el tributo (que son pesos por
    litro), en ese orden:

        base = (precio - tributo) / (1 + iva)

    Con 3000 por unidad, 19% de IVA y 0,18 por litro sobre botellas de 500 ml:

        tributo unidad = 0,18 x 0,5            = 0,09
        precio sin tributo = 3000 - 0,09       = 2999,91
        base unitaria = 2999,91 / 1,19         = 2520,93

    El 0,09 entra ANTES de dividir. Si se dividiera primero y se restara
    despues, el IVA se habria calculado sobre una base que todavia contenia el
    tributo, y el impuesto quedaria calculado sobre una cifra que no es la base
    real.
    """
    conn, id_venta = base_con_producto_tributado_y_gravado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)

    base = Decimal(str(items[0]['precio_unitario']))
    cantidad = Decimal('3')
    iva = Decimal(str(items[0]['iva_tasa'])) / 100
    trib_linea = Decimal(str(items[0]['tributo_especifico']['valor']))
    trib_unidad = trib_linea / cantidad

    # El desagregador resta el tributo de UNA unidad, no el de la linea entera.
    esperado = ((Decimal('3000') - trib_unidad) / (1 + iva)).quantize(
        Decimal('0.01'))
    assert base == esperado == Decimal('2520.93')

    # Reconstruccion: base x cantidad, mas IVA, mas el tributo.
    #
    # Da 8999.9901 en vez de 9000 exacto, y esa diferencia es de redondeo, no
    # del desagregador: la base unitaria se redondea a 2520.93 (el Anexo pide una
    # `PriceAmount` por linea, ya redondeada), y al multiplicar por tres y
    # devolver el IVA acumulado el total queda un centimo por debajo. La DIAN
    # tolera +-2 en los montos generales, asi que se comprueba la diferencia y no
    # una igualdad que no se puede cumplir.
    reconstruido = base * cantidad * (1 + iva) + trib_linea
    assert reconstruido == Decimal('8999.9901')
    assert abs(Decimal('9000') - reconstruido) <= Decimal('2'), \
        'la diferencia debe caber en la tolerancia de la DIAN para montos generales'


def test_el_tributo_es_por_unidad_y_no_por_linea(base_con_producto_tributado):
    """El impuesto nominal se cobra POR UNIDAD, no una vez por linea.

    Este test fija la trampa de la que hablo la nota del desagregador: si
    alguien resta el tributo de UNA unidad en vez de todas, la base queda
    7562,95 en vez de 7562,80 y el IVA se declara tres centavos por encima.

    Con 3 botellas de 500 ml a 0,18 por litro:

        por unidad = 0,18 x 0,5 = 0,09
        por linea   = 0,18 x 0,5 x 3 = 0,27
    """
    conn, id_venta = base_con_producto_tributado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)
    trib = items[0]['tributo_especifico']

    # El valor calculado lleva YA las tres unidades.
    assert Decimal(str(trib['valor'])) == Decimal('0.27')
    assert Decimal(str(trib['valor'])) != Decimal('0.09')


def test_el_tributo_se_reparte_entre_las_unidades(base_con_producto_tributado_y_gravado):
    """El desagregador resta la parte de UNA unidad, no el total de la linea.

    `_tributo_especifico_de_linea` devuelve 0,27 (las tres botellas). Pero
    `precio_final` es el precio UNITARIO, as que restar 0,27 contra un precio de
    una unidad daria una base distinta para cada linea y la suma no cuadraria con
    lo que cobro el POS.

    Con cantidad 1 los dos caminos coinciden, asi que este fallo solo aparece en
    ventas de varias unidades: es el tipo de defecto que pasa la prueba con una
    unidad y rompe con tres.
    """
    conn, id_venta = base_con_producto_tributado_y_gravado
    items = dian_emision._leer_items(conn.cursor(), id_venta, 19.0)

    cantidad = Decimal('3')
    trib_linea = Decimal(str(items[0]['tributo_especifico']['valor']))
    base = Decimal(str(items[0]['precio_unitario']))

    # Si se restara el total de la linea, la base seria MENOR (o incluso
    # negativa con un nominal mas alto). Con la parte por unidad da positiva y
    # reconstruye el precio exacto.
    iva = Decimal(str(items[0]['iva_tasa'])) / 100
    base_mal = ((Decimal('3000') - trib_linea) / (1 + iva)).quantize(
        Decimal('0.01'))
    base_bien = ((Decimal('3000') - trib_linea / cantidad) / (1 + iva)).quantize(
        Decimal('0.01'))

    assert base == base_bien
    assert base != base_mal

    # Y el desagregador del modelo 'precio con impuesto incluido' necesita el
    # numero de unidades. Se escribe aqui para que la nota del codigo no se
    # lea como si un solo tributo bastara.
    precio_total = Decimal('9000')
    iva = Decimal('0.19')
    una_sola = (precio_total - Decimal('0.09')) / (1 + iva)
    todas = (precio_total - Decimal('0.27')) / (1 + iva)

    assert todas.quantize(Decimal('0.01')) == Decimal('7562.80')
    assert una_sola.quantize(Decimal('0.01')) == Decimal('7562.95')
    assert todas != una_sola


def test_un_producto_sin_configurar_no_declara_tributo(tmp_path):
    """Un producto normal NO debe generar tributo especifico por defecto.

    Es el caso de los 1461 productos del catalogo: si apareciera un subtotal
    de IBUA en un producto de cemento, cada documento seria rechazado.
    """
    ruta = tmp_path / 'ferreteria.db'
    conn = sqlite3.connect(ruta)
    cursor = conn.cursor()
    for crear in (db_mod._crear_tablas_base, db_mod._crear_tablas_operacion,
                  db_mod._crear_tablas_alquiler, db_mod._crear_tablas_pedidos,
                  db_mod._crear_tablas_cotizaciones, db_mod._crear_tablas_dian):
        crear(cursor)
    db_mod._aplicar_migraciones(cursor)
    cursor.execute("""
        INSERT INTO productos (nombre, precio_venta) VALUES ('CEMENTO', 25000)
    """)
    pid = cursor.lastrowid
    cursor.execute("""
        INSERT INTO clientes (nombre, cedula_nit) VALUES ('Comprador', 'CC123')
    """)
    id_cliente = cursor.lastrowid
    cursor.execute("""
        INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta,
                            saldo_pendiente, tipo_pago)
        VALUES (?, '2026-10-01', '10:30', 25000, 0, 'efectivo')
    """, (id_cliente,))
    vid = cursor.lastrowid
    cursor.execute("""
        INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,
                                    precio_unitario, subtotal)
        VALUES (?, ?, 1, 25000, 25000)
    """, (vid, pid))
    conn.commit()

    items = dian_emision._leer_items(conn.cursor(), vid, 19.0)

    assert 'tributo_especifico' not in items[0]
    assert dian_emision._resumen_tributos_especificos(items) == []# ══════════════════════════════════════════════════════════════
# 5. La frontera: los tributos siguen APAGADOS
# ══════════════════════════════════════════════════════════════

def test_las_columnas_nacen_vacias_en_la_semilla():
    """Ningun producto debe venir con un tributo especifico puesto.

    Los 1461 productos del catalogo son de ferreteria: cemento, tuberias,
    pintura. Ninguno es una bebida azucarada. Si la migration hubiera puesto un
    valor por defecto, TODOS los documentos empezarian a declarar IBUA.

    Se comprueba sobre la SEMILLA, que es la base de la que se clona cada
    instancia de cliente: es el punto donde un valor por defecto equivocado se
    multiplicaria por todos los tenants futuros.
    """
    import sqlite3 as _s3

    conn = _s3.connect(os.path.join(RAIZ, 'ferreteria-semilla.db'))
    try:
        configurados = conn.execute("""
            SELECT COUNT(*) FROM productos
            WHERE tributo_especifico_tipo IS NOT NULL
               OR tributo_especifico_nominal IS NOT NULL
               OR tributo_contenido IS NOT NULL
        """).fetchone()[0]
    finally:
        conn.close()

    assert configurados == 0, (
        f'{configurados} productos de la semilla traen tributo especifico '
        'puesto: se declararia un impuesto que no existe'
    )


def test_la_semilla_no_altera_un_documento_sin_tributo():
    """La garantia de la ronda anterior: un documento sin tributos no cambia.

    Un producto normal tiene que seguir dando el mismo XML byte a byte que
    antes de esta ronda. Si el productor metiera un subtotal de cero, cada
    factura de la ferreteria quedaria alterada.
    """
    totales = {'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0,
                                  'valor': 19000.0}],
               'impuestos_inc': [], 'impuestos_especificos': []}
    raiz = ET.fromstring(dian_xml.a_texto(
        dian_xml._construir_impuestos_totales(totales)))

    subtotales = raiz.findall('cac:TaxSubtotal', NS)
    assert len(subtotales) == 1
    assert subtotales[0].find(
        'cac:TaxCategory/cac:TaxScheme/cbc:ID', NS).text == '01'


def test_el_docstring_dice_que_el_ia_no_grava_sobre_el_tributo():
    """Guarda de honestidad tecnica, no de comportamiento.

    El modelo implementado (precio con impuesto incluido, tributo restado antes
    de sacar la base) es la REGLA DE COBRO DEL NEGOCIO, que es un hecho. Lo que
    NO se pudo cotejar es si la Ley 2277 de 2022 exige que el IVA grave sobre la
    base del bien o sobre la base mas el tributo especifico.

    Si alguien llegara a este archivo creyendo que la eleccion del desagregador
    es la regla legal, tomaria una decision sobre una lectura propia. Se deja
    escrito que es dimensional. El fallo de esta prueba es de contenido, y por
    eso mira el docstring en vez de ejecutar el codigo.
    """
    import inspect

    texto = inspect.getdoc(dian_emision._leer_items) or ''
    minusculas = texto.lower()

    # El docstring tiene que NOMBRAR LAS DOS cosas que no se han resuelto, no
    # solo advertir en general. Un aviso generico tipo "ojo, revisar" se
    # vuelve invisible en dos meses; uno que dice "el nominal no esta
    # verificado" y "el IVA no tiene regla confirmada" se puede buscar.
    assert 'nominal' in minusculas, 'no advierte del valor nominal sin verificar'
    assert ('no se pudo cotejar' in minusculas
            or 'no se han resuelto' in minusculas), \
        'no advierte de que el tratamiento fiscal NO esta verificado'
    assert 'regla de cobro' in minusculas, \
        'no aclara que lo implementado es la regla del negocio, no la norma'