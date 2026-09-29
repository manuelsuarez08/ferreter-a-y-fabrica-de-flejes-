"""Pruebas de la REVERSIÓN por Nota Crédito: el comportamiento más crítico.

La regla que estas pruebas fijan:

    La venta NO se revierte hasta que la DIAN ACEPTA la nota crédito.

Es lo contrario de lo que hacía el POS antes (marcaba `anulada` sin preguntar a
la DIAN). Consecuencias que se verifican:

  - Un rechazo de la DIAN deja la venta intacta y el stock sin devolver.
  - Una caída de red deja la venta intacta (queda en contingencia).
  - El stock se devuelve UNA sola vez, aunque se reintente.
  - Una venta nunca emitida no necesita nota crédito: se anula en el POS.

Se usa una base temporal para no tocar los datos reales.
"""
import os
import shutil
import sqlite3

import pytest

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.services import dian_nota_credito  # noqa: E402
from ferreteria.services.dian_emision import ErrorEmision  # noqa: E402

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db_path(tmp_path):
    """Cada test recibe una base LIMPIA propia.

    No se comparte una base entre tests a propósito: los números de documento
    son únicos (UNIQUE(prefijo, numero)) y dos tests que emitieran 'POS-1' en la
    misma base chocarían, además de dejar el stock de uno afectado por el otro.
    """
    destino = tmp_path / 'ferreteria.db'
    shutil.copy2(os.path.join(_RAIZ, 'ferreteria-semilla.db'), destino)
    os.environ['FERRETERIA_DB'] = str(destino)
    return str(destino)


@pytest.fixture
def cfg(db_path):
    """Re-resuelve la configuración contra la base temporal de este test.

    `ferreteria.config` resuelve DB_NAME AL IMPORTARSE. Los modulos ya estaban
    importados por otros tests, así que hay que recargarlos para que apunten a la
    base nueva: si no, el INSERT va a la base real y la migración tampoco.
    """
    import importlib

    import ferreteria.config as config
    importlib.reload(config)

    import ferreteria.db as db
    importlib.reload(db)

    return config


@pytest.fixture
def app(db_path, cfg):
    """Crea la app y corre el esquema (que es quien aplica las migraciones).

    `init_db` es imprescindible: la semilla se copió de un archivo que puede no
    tener las columnas nuevas (notas crédito, documento soporte).

    Se aplica sobre una conexión EXPLICITA a la base del test, no con init_db()
    a secas: `get_db()` usa el DB_NAME que quedó cacheado en el módulo, que
    puede apuntar a la base del test anterior (los módulos se reimportan al
    principio del test, pero cualquier conexión ya abierta sigue viva).
    """
    from ferreteria import db as db_mod

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._asegurar_migracion_documento_soporte(cursor)
    db_mod._aplicar_migraciones(cursor)
    conn.commit()
    conn.close()

    return create_app()


@pytest.fixture
def cfg(db_path):
    """Re-resuelve la configuración contra la base temporal de este test."""
    import importlib

    import ferreteria.config as config
    importlib.reload(config)
    return config


@pytest.fixture
def venta(app, cfg, db_path):
    """Venta con UNA línea, stock conocido, y un documento POS emitido."""
    conn = sqlite3.connect(cfg.DB_NAME)
    conn.execute(
        'INSERT INTO productos (nombre, precio_venta, precio_costo,'
        ' stock_actual, iva_tasa, unidad_medida, activo)'
        " VALUES ('Cemento', 50000, 40000, 10, 19, '94', 1)")
    id_prod = conn.execute('SELECT MAX(id) FROM productos').fetchone()[0]
    id_cli = conn.execute('SELECT id FROM clientes LIMIT 1').fetchone()[0]

    cur = conn.execute(
        "INSERT INTO ventas (fecha_dia, hora, total_venta, tipo_pago,"
        " id_cliente, subtotal_venta, iva_valor, iva_porcentaje, anulada,"
        " saldo_pendiente, direccion_cliente)"
        " VALUES ('2026-09-29','10:00:00',119000,'efectivo',?,100000,"
        "19000,19,0,0,'')",
        (id_cli,))
    id_venta = cur.lastrowid
    conn.execute(
        'INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,'
        ' precio_unitario, subtotal) VALUES (?,?,3,50000,150000)',
        (id_venta, id_prod))

    # El stock baja como en una venta real.
    conn.execute('UPDATE productos SET stock_actual = 7 WHERE id = ?', (id_prod,))

    # Documento POS emitido (lo que hace falta para poder corregirlo).
    conn.execute(
        'UPDATE ventas SET dian_estado=?, numero_dian=?, dian_cuide=? WHERE id=?',
        ('aceptado', 'POS-1', 'a' * 96, id_venta))
    conn.execute(
        'INSERT INTO documentos_electronicos'
        ' (id_venta, tipo_documento, prefijo, numero, cuide, fecha_generacion,'
        '  hora_generacion, valor_total, valor_iva, xml, xml_firmado,'
        '  estado, modo)'
        " VALUES (?, 'POS', 'POS', 'POS-1', ?, '2026-09-29', '10:00:00',"
        " 119000, 19000, '<x/>', '<x/>', 'aceptado', 'habilitacion')",
        (id_venta, 'a' * 96))
    conn.commit()
    conn.close()
    return {'id': id_venta, 'producto': id_prod, 'stock_antes': 7,
            'ruta': db_path}


def _stock(id_producto, cfg):
    conn = sqlite3.connect(cfg.DB_NAME)
    n = conn.execute('SELECT stock_actual FROM productos WHERE id = ?',
                     (id_producto,)).fetchone()[0]
    conn.close()
    return n


def _venta_estado(id_venta, cfg):
    conn = sqlite3.connect(cfg.DB_NAME)
    fila = conn.execute(
        'SELECT anulada, dian_estado FROM ventas WHERE id = ?', (id_venta,)).fetchone()
    conn.close()
    return fila


# ═════════════════════════════════════════════════════
# La venta NO emitida no necesita nota crédito
# ═════════════════════════════════════════════════════
def test_una_venta_no_emitida_no_pide_nota_credito(venta, cfg):
    """Si el documento nunca se emitió, no hay nada que corregir: se anula en
    el POS. El error debe decirlo, no pedir una nota imposible."""
    conn = sqlite3.connect(cfg.DB_NAME)
    conn.execute('DELETE FROM documentos_electronicos WHERE id_venta = ?',
                 (venta['id'],))
    conn.execute("UPDATE ventas SET dian_estado='sin_emitir' WHERE id=?",
                 (venta['id'],))
    conn.commit()
    conn.close()

    with pytest.raises(ErrorEmision) as exc:
        dian_nota_credito.emitir_nota_credito(
            sqlite3.connect(cfg.DB_NAME), venta['id'])
    assert 'no tiene documento' in str(exc.value).lower()


# ═════════════════════════════════════════════════════
# La venta se revierte SOLO tras aceptación
# ═════════════════════════════════════════════════════
def test_la_venta_se_revierte_solo_cuando_la_dian_acepta(venta, cfg):
    """El camino feliz: la DIAN acepta -> la venta se anula y vuelve el stock."""
    from ferreteria.services import dian_soap

    respuesta = dian_soap.RespuestaDian(
        exito=True, es_valido=True, codigo='00', descripcion='OK',
        xml_respuesta='<r/>', track_id='T1', zip_key='')
    dian_soap.send_bill_sync = lambda *a, **k: respuesta
    _configurar_certificado(venta, cfg)

    conn = sqlite3.connect(cfg.DB_NAME)
    r = dian_nota_credito.emitir_nota_credito(
        conn, venta['id'], motivo_codigo='1',
        motivo_descripcion='Devolución total')
    conn.close()

    assert r['estado'] == 'aceptado', r
    assert r['venta_revertida'] is True, 'la venta no se revirtió'
    assert _venta_estado(venta['id'], cfg)[0] == 1, 'la venta sigue sin anular'
    assert _stock(venta['producto'], cfg) == 10, (
        f"el stock quedo en {_stock(venta['producto'], cfg)}, esperaba 10"
    )


def test_un_rechazo_de_la_dian_no_revierte_la_venta(venta, cfg):
    """Si la DIAN rechaza la nota, el documento original sigue vigente: la
    venta NO se toca y el stock NO se devuelve."""
    from ferreteria.services import dian_soap

    respuesta = dian_soap.RespuestaDian(
        exito=True, es_valido=False, codigo='99', descripcion='XML inválido',
        xml_respuesta='<r/>', track_id='', zip_key='')
    dian_soap.send_bill_sync = lambda *a, **k: respuesta
    _configurar_certificado(venta, cfg)

    conn = sqlite3.connect(cfg.DB_NAME)
    r = dian_nota_credito.emitir_nota_credito(
        conn, venta['id'], motivo_codigo='1')
    conn.close()

    assert r['estado'] == 'rechazado'
    assert r['venta_revertida'] is False
    anulada = _venta_estado(venta['id'], cfg)[0]
    assert anulada == 0, 'una venta con nota rechazada NO debe anularse'
    assert _stock(venta['producto'], cfg) == 7, 'el stock se devolvió sin aceptación'


def test_sin_conexion_no_revierte_la_venta(venta, cfg):
    """Sin red la nota queda en contingencia y la venta NO se revierte: cuando
    vuelva la conexión se reintenta y, al aceptarse, se aplica la reversión."""
    from ferreteria.services import dian_soap

    def _falla(*a, **k):
        raise dian_soap.ErrorSOAP('sin conexión')
    dian_soap.send_bill_sync = _falla
    _configurar_certificado(venta, cfg)

    conn = sqlite3.connect(cfg.DB_NAME)
    r = dian_nota_credito.emitir_nota_credito(
        conn, venta['id'], motivo_codigo='1')
    conn.close()

    assert r['estado'] == 'contingencia'
    assert r['venta_revertida'] is False
    assert _venta_estado(venta['id'], cfg)[0] == 0
    assert _stock(venta['producto'], cfg) == 7, 'el stock volvió sin aceptación'


# ═════════════════════════════════════════════════════
# Idempotencia: el stock se devuelve UNA sola vez
# ═════════════════════════════════════════════════════
def test_revertir_dos_veces_no_devuelve_el_stock_doble(venta, cfg):
    """`revertir_venta` es idempotente: si la venta ya está revertida, no
    vuelve a sumar el stock (eso descuadraría el inventario)."""
    conn = sqlite3.connect(cfg.DB_NAME)
    primera = dian_nota_credito.revertir_venta(
        conn, venta['id'], 'admin', 'prueba')
    segunda = dian_nota_credito.revertir_venta(
        conn, venta['id'], 'admin', 'prueba')
    conn.commit()
    conn.close()

    assert primera is True, 'la primera reversión debe aplicar'
    assert segunda is False, 'la segunda debe ser no-op'
    assert _stock(venta['producto'], cfg) == 10, (
        'el stock se devolvió dos veces: inventario descuadrado'
    )


def test_no_se_emiten_dos_notas_para_la_misma_venta(venta, cfg):
    """La DIAN rechaza dos notas sobre el mismo documento."""
    conn = sqlite3.connect(cfg.DB_NAME)
    # Se simula que ya hay una nota en vuelo.
    conn.execute(
        'INSERT INTO documentos_electronicos'
        ' (id_venta, tipo_documento, prefijo, numero, cuide, fecha_generacion,'
        "  hora_generacion, valor_total, valor_iva, estado)"
        " VALUES (?, 'NC', 'NC', 'NC-1', ?, '2026-09-29', '10:00:00',"
        " 119000, 19000, 'contingencia')",
        (venta['id'], 'b' * 96))
    conn.commit()

    with pytest.raises(ErrorEmision) as exc:
        dian_nota_credito.emitir_nota_credito(conn, venta['id'], '1')
    conn.close()
    assert 'ya tiene una nota' in str(exc.value)


def test_el_motivo_debe_ser_del_catalogo(venta, cfg):
    with pytest.raises(ErrorEmision) as exc:
        dian_nota_credito.emitir_nota_credito(
            sqlite3.connect(cfg.DB_NAME), venta['id'], motivo_codigo='99')
    assert 'inválido' in str(exc.value)


# ═════════════════════════════════════════════════════
# La nota queda vinculada al documento original
# ═════════════════════════════════════════════════════
def test_la_nota_guarda_el_cuide_del_documento_original(venta, cfg):
    """La columna `documento_referido` es el vínculo con el documento que corrige."""
    from ferreteria.services import dian_soap

    respuesta = dian_soap.RespuestaDian(
        exito=True, es_valido=True, codigo='00', descripcion='OK',
        xml_respuesta='<r/>', track_id='T1', zip_key='')
    dian_soap.send_bill_sync = lambda *a, **k: respuesta
    _configurar_certificado(venta, cfg)

    conn = sqlite3.connect(cfg.DB_NAME)
    dian_nota_credito.emitir_nota_credito(conn, venta['id'], '1', 'devolución')
    fila = conn.execute(
        "SELECT documento_referido, tipo_documento, numero, motivo"
        " FROM documentos_electronicos WHERE id_venta = ?"
        " AND tipo_documento = 'NC'",
        (venta['id'],)).fetchone()
    conn.close()

    assert fila is not None, 'no se guardó la nota crédito'
    assert fila[0] == 'a' * 96, 'la nota no referencia el CUIDE original'
    assert fila[1] == 'NC'
    assert fila[2].startswith('NC-')
    assert fila[3] == 'devolución'


def test_la_nota_crea_movimiento_de_inventario(venta, cfg):
    """La devolución de mercancía queda registrada en el kardex."""
    from ferreteria.services import dian_soap

    respuesta = dian_soap.RespuestaDian(
        exito=True, es_valido=True, codigo='00', descripcion='OK',
        xml_respuesta='<r/>', track_id='T1', zip_key='')
    dian_soap.send_bill_sync = lambda *a, **k: respuesta
    _configurar_certificado(venta, cfg)

    conn = sqlite3.connect(cfg.DB_NAME)
    dian_nota_credito.emitir_nota_credito(conn, venta['id'], '1', 'devolución')
    n = conn.execute(
        "SELECT COUNT(*) FROM movimientos_inventario"
        " WHERE id_producto = ? AND tipo = 'entrada'",
        (venta['producto'],)).fetchone()[0]
    conn.close()
    assert n == 1, 'no se registró la entrada de inventario'


# ═════════════════════════════════════════════════════
def _configurar_certificado(venta, cfg):
    """Certificado autofirmado temporal para poder firmar sin red."""
    import datetime

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    ruta = os.path.join(os.path.dirname(venta['ruta']), 'prueba.p12')
    if not os.path.exists(ruta):
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
            serialization.BestAvailableEncryption(b'prueba123'))
        with open(ruta, 'wb') as f:
            f.write(p12)

    conn = sqlite3.connect(cfg.DB_NAME)
    conn.execute(
        "UPDATE configuracion SET nit='900187391', digito_verificacion='2',"
        " direccion='CALLE 1', dian_ambiente='2', dian_prefijo='POS',"
        " clave_tecnica='CT', software_id='SW1',"
        " dian_software_security_code='1', certificado_ruta=?,"
        " certificado_clave='prueba123' WHERE id=1",
        (ruta,))
    conn.commit()
    conn.close()
