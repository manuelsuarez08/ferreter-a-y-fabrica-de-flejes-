"""Pruebas de productos EXCLUIDOS de IVA y del Documento Soporte.

PRODUCTOS EXCLUIDOS (tarifa 0%)
    Los materiales de construcción de extracción directa (arena, balastro,
    grava) están EXCLUIDOS del IVA por el artículo 424 del Estatuto Tributario:
    su tarifa es 0%, pero NO son "exentos" (que sería no gravado). La distinción
    importa porque la DIAN los declara con códigos distintos y una operación
    exenta con impuesto declarado es un rechazo.

DOCUMENTO SOPORTE
    Respaldar compras a proveedores informales: el receptor va marcado como
    "No Obligado a Facturar" y las partes están invertidas respecto al POS.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_productos_soporte.py -q
"""
import os
import shutil
import sqlite3
import tempfile

import pytest

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db_path(tmp_path):
    destino = tmp_path / 'ferreteria.db'
    shutil.copy2(os.path.join(_RAIZ, 'ferreteria-semilla.db'), destino)
    os.environ['FERRETERIA_DB'] = str(destino)
    return str(destino)


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    yield c
    c.close()


# ═════════════════════════════════════════════════════
# Productos excluidos de IVA
# ═════════════════════════════════════════════════════
NATURALEZAS_EXENTAS = ('exento', 'excluido_iva', 'no_sujeto')


def test_la_columna_iva_tipo_tarifa_existe(conn):
    """El código de tarifa a cero que la DIAN pide declarar (art. 424 ET)."""
    from ferreteria import db as db_mod
    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.commit()
    cols = [c[1] for c in conn.execute('PRAGMA table_info(productos)')]
    assert 'iva_naturaleza' in cols
    assert 'iva_tipo_tarifa' in cols, 'falta el código de tarifa del IVA'


def test_los_codigos_de_tarifa_son_los_de_la_dian(conn):
    from ferreteria import db as db_mod
    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.commit()

    # '01' excluido, '02' exento, '03' no sujeto, '00' tarifa normal.
    cur = conn.cursor()
    for naturaleza, tipo in (('excluido', '01'), ('exento', '02'),
                             ('no_sujeto', '03'), ('excluido', '00')):
        cur.execute(
            'INSERT INTO productos (nombre, precio_venta, precio_costo,'
            ' stock_actual, iva_tasa, iva_naturaleza, iva_tipo_tarifa,'
            ' unidad_medida, activo)'
            ' VALUES (?,?,?,0,0,?,?,?,1)',
            (f'P-{naturaleza}-{tipo}', 1000, 800, naturaleza, tipo, '94'))
    conn.commit()

    filas = conn.execute(
        'SELECT nombre, iva_tipo_tarifa FROM productos'
        " WHERE nombre LIKE 'P-%'").fetchall()
    por_nombre = dict(filas)
    assert por_nombre['P-excluido-01'] == '01', por_nombre
    assert por_nombre['P-exento-02'] == '02', por_nombre
    assert por_nombre['P-no_sujeto-03'] == '03', por_nombre
    # El default '00' es la tarifa NORMAL: un producto gravado 19% conserva
    # su IVA. Este es el caso que se rompía si se usara el texto 'excluido'
    # como criterio (todos los productos vienen con ese texto por defecto).
    assert por_nombre['P-excluido-00'] == '00', por_nombre


def test_el_material_de_extraccion_directa_queda_excluido(conn):
    """Arena y balastro se declaran EXCLUIDOS (tasa 0%), no exentos.

    Es el caso real que motiva el módulo: en una ferretería estos materiales se
    compran y se venden al público, y se equivocarse de tarifa cambia el IVA que
    la DIAN valida.
    """
    from ferreteria import db as db_mod
    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, iva_naturaleza, iva_tipo_tarifa,'
        " unidad_medida, activo)"
        " VALUES ('ARENA', 80000, 60000, 0, 0, 'excluido', '01', 'MTQ', 1)")
    conn.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, iva_naturaleza, iva_tipo_tarifa,'
        " unidad_medida, activo)"
        " VALUES ('BALASTRO', 95000, 70000, 0, 0, 'excluido', '01', 'MTQ', 1)")
    conn.commit()

    filas = conn.execute(
        "SELECT nombre, iva_tasa, iva_naturaleza, iva_tipo_tarifa"
        " FROM productos WHERE nombre IN ('ARENA', 'BALASTRO')").fetchall()
    assert len(filas) == 2
    for nombre, tasa, naturaleza, tipo in filas:
        assert tasa == 0, f'{nombre} tiene tasa {tasa}, debe ser 0'
        assert naturaleza == 'excluido', f'{nombre} quedo {naturaleza}'
        assert tipo == '01', f'{nombre} tiene código {tipo}, debe ser 01'


def test_leer_items_no_grava_un_excluido(conn):
    """La pieza crítica: un producto excluido NO puede recibir el IVA global.

    Si el POS tiene el IVA al 19% activo y se vende arena (excluida al 0%), la
    línea del documento debe salir con tasa 0 y sin desagregar el precio.
    """
    from ferreteria import db as db_mod
    from ferreteria.services import dian_emision

    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, iva_naturaleza, iva_tipo_tarifa,'
        " unidad_medida, activo)"
        " VALUES ('ARENA', 80000, 60000, 10, 0, 'excluido', '01', 'MTQ', 1)")
    conn.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, iva_naturaleza, iva_tipo_tarifa,'
        " unidad_medida, activo)"
        " VALUES ('CEMENTO', 59500, 47600, 10, 19, 'excluido', '00', 'BAG', 1)")
    conn.execute('INSERT INTO clientes (nombre, cedula_nit) VALUES (?, ?)',
                 ('T', '1'))
    id_cli = conn.execute('SELECT MAX(id) FROM clientes').fetchone()[0]

    cur = conn.execute(
        "INSERT INTO ventas (fecha_dia, hora, total_venta, tipo_pago,"
        " id_cliente, subtotal_venta, iva_valor, iva_porcentaje, anulada,"
        " saldo_pendiente, direccion_cliente)"
        " VALUES ('2026-09-29','10:00:00',0,'efectivo',?,0,0,19,0,0,'')",
        (id_cli,))
    id_venta = cur.lastrowid
    for nombre in ('ARENA', 'CEMENTO'):
        conn.execute(
            'INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,'
            ' precio_unitario, subtotal)'
            ' SELECT ?, id, 1, precio_venta, precio_venta FROM productos'
            ' WHERE nombre = ?', (id_venta, nombre))
    conn.commit()

    items = dian_emision._leer_items(cur, id_venta, 19.0)

    arena = [i for i in items if 'ARENA' in i['descripcion']]
    cemento = [i for i in items if 'CEMENTO' in i['descripcion']]

    assert arena[0]['iva_tasa'] == 0.0, (
        f"la arena (excluida) quedó gravada al {arena[0]['iva_tasa']}%"
    )
    # Sin desagregar: el precio final ES la base.
    assert arena[0]['precio_unitario'] == 80000.0

    # El gravado sí mantiene su 19% y desagrega.
    assert cemento[0]['iva_tasa'] == 19.0
    assert cemento[0]['precio_unitario'] == 50000.0


# ═════════════════════════════════════════════════════
# Documento Soporte a No Obligados a Facturar
# ═════════════════════════════════════════════════════
def test_se_pueden_registrar_proveedores_informales(db_path):
    """La tabla de proveedores informales existe tras la migración."""
    from ferreteria import db as db_mod
    conn = sqlite3.connect(db_path)
    db_mod._asegurar_migracion_documento_soporte(conn.cursor())
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        'INSERT INTO proveedores_informales (nombre, tipo_documento,'
        ' numero_documento, direccion, tipo_suministro, creado)'
        " VALUES ('DON PEDRO', 'CC', '12345678', 'VEREDA', 'materiales',"
        " '2026-09-29 10:00:00')")
    conn.commit()
    fila = conn.execute(
        'SELECT nombre, tipo_suministro FROM proveedores_informales').fetchone()
    conn.close()

    assert fila[0] == 'DON PEDRO'
    assert fila[1] == 'materiales'


def test_el_documento_soporte_se_emite_como_bloque(conn, db_path):
    """Emisión completa del soporte: XML, firma y registro del proveedor."""
    import datetime

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    from ferreteria import db as db_mod
    from ferreteria.services import dian_documento_soporte, dian_soap

    cursor = conn.cursor()
    db_mod._crear_tablas_dian(cursor)
    db_mod._asegurar_migracion_documento_soporte(cursor)
    db_mod._aplicar_migraciones(cursor)

    # Certificado autofirmado.
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'PRUEBA')])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(nombre).issuer_name(nombre)
            .public_key(clave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora - datetime.timedelta(days=1))
            .not_valid_after(ahora + datetime.timedelta(days=1))
            .sign(clave, hashes.SHA256()))
    p12 = pkcs12.serialize_key_and_certificates(
        b'p', clave, cert, None,
        serialization.BestAvailableEncryption(b'clave123'))
    ruta = os.path.join(os.path.dirname(db_path), 'p.p12')
    with open(ruta, 'wb') as f:
        f.write(p12)

    conn.execute(
        "UPDATE configuracion SET nit='900187391', digito_verificacion='2',"
        " direccion='CALLE 1', dian_ambiente='2', dian_prefijo='POS',"
        " clave_tecnica='CT', software_id='SW1',"
        " dian_software_security_code='1', certificado_ruta=?,"
        " certificado_clave='clave123' WHERE id=1", (ruta,))
    conn.execute(
        'INSERT INTO proveedores_informales (nombre, tipo_documento,'
        ' numero_documento, direccion, municipio, departamento,'
        " tipo_suministro, creado)"
        " VALUES ('DON PEDRO (BALASTRO)', 'CC', '12345678', 'VEREDA',"
        " '11001', '11', 'materiales', '2026-09-29 10:00:00')")
    conn.commit()
    id_prov = conn.execute(
        'SELECT id FROM proveedores_informales').fetchone()[0]

    # Se simula la aceptación de la DIAN.
    dian_soap.send_bill_sync = lambda *a, **k: dian_soap.RespuestaDian(
        exito=True, es_valido=True, codigo='00', descripcion='OK',
        xml_respuesta='<r/>', track_id='T1', zip_key='')

    r = dian_documento_soporte.emitir_documento_soporte(
        conn, id_prov,
        [{'descripcion': 'Balastro 10 m3', 'cantidad': 1,
          'precio_unitario': 800000, 'iva_naturaleza': 'excluido'}],
        documento_proveedor='001-045')

    assert r['estado'] == 'aceptado', r['mensaje']
    assert r['numero'].startswith('DS-'), r['numero']
    assert len(r['cuide']) == 96

    fila = conn.execute(
        'SELECT estado, xml_firmado, documento_proveedor, valor_total'
        ' FROM documentos_soporte WHERE id = ?', (r['id_documento'],)).fetchone()
    conn.close()

    assert fila[0] == 'aceptado'
    assert '<ds:Signature' in fila[1], 'el soporte no se firmó'
    assert fila[2] == '001-045'
    # Excluido: el total es el precio completo, sin IVA.
    assert fila[3] == 800000.0


def test_el_soporte_rechaza_lineas_invalidas(conn, db_path):
    """Cantidad cero o precio negativo no pueden generar un documento."""
    from ferreteria.services import dian_documento_soporte
    from ferreteria.services.dian_emision import ErrorEmision

    with pytest.raises(ErrorEmision):
        dian_documento_soporte._leer_items(
            conn.cursor(), [{'descripcion': 'X', 'cantidad': 0,
                             'precio_unitario': 100}], 19.0)
    with pytest.raises(ErrorEmision):
        dian_documento_soporte._leer_items(
            conn.cursor(), [{'descripcion': 'X', 'cantidad': 1,
                             'precio_unitario': -5}], 19.0)
    with pytest.raises(ErrorEmision):
        dian_documento_soporte._leer_items(conn.cursor(), [], 19.0)
