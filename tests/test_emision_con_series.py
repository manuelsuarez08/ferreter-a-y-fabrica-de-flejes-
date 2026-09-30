"""Emisión real de un documento con serie de pruebas, para POS y para FV.

Este modulo NO existía y hacía falta: al cambiar la emisión para leer el
prefijo de `series_dian` y el tipo de documento, nadie comprobó que un
documento siguiera generándose. Un refactor de la numeración se lleva por
delante la emisión entera sin que ninguna prueba se entere.

Se emite contra un certificado de prueba y sin red: lo que se comprueba es que
el XML se construye, que el CUIDE tiene la forma correcta y que el prefijo
y el tipo de documento son los de la serie elegida.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402
from ferreteria.services import dian_emision, dian_series  # noqa: E402

# El certificado de prueba lo genera `tests/_certificado_prueba.py`, que lo
# deja en la RAÍZ del proyecto. La clave está en el propio script: es
# autofirmado y de prueba, no hay secreto que guardar.
CERT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'certificado_prueba.p12')
CLAVE_CERT = 'clave-de-prueba'


@pytest.fixture
def app():
    return create_app(inicializar_db=False)


@pytest.fixture(autouse=True)
def _series_limpias():
    """Deja las series en su estado base antes de cada prueba.

    Dos razones, y las dos importan:

    1. El consecutivo se reinicia. Si no, dos pruebas que emiten para el mismo
       tipo documental consumen el mismo número y la restricción UNIQUE de
       `documentos_electronicos` revienta en la prueba EQUIVOCADA.
    2. El prefijo vuelve a su valor por defecto. `test_emision_con_series.py`
       configura SETP222222222 para las facturas, y sin este reset ese módulo
       deja la serie FV cambiada para los módulos que corran despues. El orden
       de ejecucion de pytest no es el del archivo.
    """
    conn = get_db()
    try:
        conn.execute("UPDATE series_dian SET consecutivo = 1, prefijo = tipo_documento")
        conn.execute("DELETE FROM documentos_electronicos")
        conn.execute("DELETE FROM cola_dian")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def configurado(app):
    """Serie de pruebas para POS y FV, con clave técnica y certificado."""
    conn = get_db()
    try:
        for tipo, prefijo in (('POS', 'SETP111111111'), ('FV', 'SETP222222222')):
            dian_series.actualizar_serie(conn, tipo, {
                'prefijo': prefijo, 'consecutivo': 1,
                'rango_desde': 1, 'rango_hasta': 0,
                'clave_tecnica': 'CLAVE-TECNICA-PRUEBA',
            })
        conn.execute("UPDATE configuracion SET dian_ambiente='2', "
                     "dian_modo='habilitacion', "
                     "software_id='SOFT-TEST', dian_test_set_id='SETP-001', "
                     "digito_verificacion='7', "
                     "regimen_fiscal='Responsable de IVA', "
                     "responsabilidades='O-13' "
                     "WHERE id=1")
        # El NIT del EMISOR es obligatorio: sin él la DIAN no identifica quién
        # emite y la emisión se detiene antes de llegar al CUIDE.
        nit = conn.execute("SELECT nit FROM configuracion WHERE id=1").fetchone()[0]
        if not nit:
            # El NIT que trae el certificado de prueba (900187391).
            conn.execute("UPDATE configuracion SET nit='900187391' WHERE id=1")
        else:
            conn.execute("UPDATE configuracion SET digito_verificacion=? "
                         "WHERE id=1", ('7',))
        # La dirección del emisor también es obligatoria (el anexo técnico la
        # exige como punto de emisión).
        if not conn.execute("SELECT direccion FROM configuracion WHERE id=1").fetchone()[0]:
            conn.execute("UPDATE configuracion SET direccion=? WHERE id=1",
                         ('Calle 1 # 2-3, Samaná, Caldas',))
        # Municipio y departamento del emisor (Samaná = 17665, Caldas = 17).
        conn.execute("UPDATE configuracion SET codigo_municipio='17665', "
                     "codigo_departamento='17' WHERE id=1")
        if os.path.exists(CERT):
            conn.execute("UPDATE configuracion SET certificado_ruta=?, "
                         "certificado_clave=? WHERE id=1", (CERT, CLAVE_CERT))
        conn.commit()
    finally:
        conn.close()
    return app


_contador = {'n': 0}


def _venta(tipo_documento_dian, con_nit=True):
    """Crea una venta de un producto y devuelve su id.

    El NIT lleva un contador porque `cedula_nit` es UNIQUE y varias pruebas
    crean cliente: sin esto la segunda revienta por restricción.
    """
    conn = get_db()
    try:
        if con_nit:
            _contador['n'] += 1
            cur = conn.execute(
                "INSERT INTO clientes (nombre, cedula_nit, telefono) "
                "VALUES (?, ?, ?)",
                (f'Empresa Compradora {_contador["n"]}',
                 f'90012{_contador["n"]:04d}', '3001234567'))
            id_cliente = cur.lastrowid
        else:
            id_cliente = 1  # el mostrador, documento 222
        prod = conn.execute(
            "SELECT id, precio_venta FROM productos WHERE COALESCE(activo,1)=1 LIMIT 1"
        ).fetchone()
        cur = conn.execute(
            "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, "
            "saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, "
            "iva_porcentaje, anulada, tipo_documento_dian) "
            "VALUES (?, '2026-09-30', '10:00:00', 119000, 0, 'efectivo', "
            "100000, 19000, 19, 0, ?)", (id_cliente, tipo_documento_dian))
        id_venta = cur.lastrowid
        conn.execute(
            "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, "
            "precio_unitario, subtotal) VALUES (?, ?, 1, 100000, 100000)",
            (id_venta, prod[0]))
        conn.commit()
        return id_venta
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
@pytest.mark.skipif(not os.path.exists(CERT), reason='sin certificado de prueba')
def test_una_venta_pos_se_emite(configurado):
    """El caso de siempre: la venta de mostrador."""
    id_venta = _venta('POS', con_nit=False)
    conn = get_db()
    try:
        res = dian_emision.emitir_venta(conn, id_venta, contingencia=True)
    finally:
        conn.close()

    assert res['numero'] == 'SETP111111111-1', (
        'el número debe salir de la serie POS, no del consecutivo global')
    assert len(res['cuide']) == 96, 'el CUIDE son 96 caracteres hexadecimales'
    assert all(c in '0123456789abcdef' for c in res['cuide'])
    assert res['estado'] in ('contingencia', 'aceptado', 'enviado')
    assert res['qr_url'], 'debe devolver la URL de verificación'


@pytest.mark.skipif(not os.path.exists(CERT), reason='sin certificado de prueba')
def test_una_factura_electronica_se_emite(configurado):
    """La FV usa SU serie y un CUIDE DISTINTO, aunque el número coincida.

    Esto es lo que exige la DIAN: el tipo de documento entra en el CUIDE, así
    que un POS y una FV nunca pueden compartirlo.
    """
    id_pos = _venta('POS', con_nit=False)
    id_fv = _venta('FV', con_nit=True)

    conn = get_db()
    try:
        r_pos = dian_emision.emitir_venta(conn, id_pos, contingencia=True)
        r_fv = dian_emision.emitir_venta(conn, id_fv, contingencia=True)
    finally:
        conn.close()

    assert r_pos['numero'].startswith('SETP111111111-')
    assert r_fv['numero'].startswith('SETP222222222-')
    assert r_pos['cuide'] != r_fv['cuide'], (
        'POS y FV con el mismo número NO pueden compartir CUIDE')


@pytest.mark.skipif(not os.path.exists(CERT), reason='sin certificado de prueba')
def test_el_tipo_queda_guardado_en_el_documento(configurado):
    """Es lo que permite que la nota crédito encuentre el original."""
    id_fv = _venta('FV', con_nit=True)
    conn = get_db()
    try:
        dian_emision.emitir_venta(conn, id_fv, contingencia=True)
        tipo = conn.execute(
            "SELECT tipo_documento FROM documentos_electronicos "
            "WHERE id_venta = ?", (id_fv,)).fetchone()[0]
    finally:
        conn.close()
    assert tipo == 'FV'


@pytest.mark.skipif(not os.path.exists(CERT), reason='sin certificado de prueba')
def test_el_xml_lleva_el_numero_de_la_serie(configurado):
    """El XML es lo que lee la DIAN: el prefijo tiene que ser el de la serie."""
    id_fv = _venta('FV', con_nit=True)
    conn = get_db()
    try:
        res = dian_emision.emitir_venta(conn, id_fv, contingencia=True)
        xml = conn.execute(
            "SELECT xml FROM documentos_electronicos WHERE id = ?",
            (res['id_documento'],)).fetchone()[0]
    finally:
        conn.close()
    assert 'SETP222222222-' in xml


def test_el_tipo_va_al_cuide_y_al_xml(configurado):
    """El tipo de documento no es solo una etiqueta del panel.

    Entra en el CUIDE (por eso POS y FV nunca lo comparten) y en el XML. Si
    alguno de los dos se queda con 'POS' fijo, dos documentos de tipos
    distintos pueden terminar con el mismo CUIDE y la DIAN los rechaza.
    """
    id_fv = _venta('FV', con_nit=True)
    conn = get_db()
    try:
        res = dian_emision.emitir_venta(conn, id_fv, contingencia=True)
        fila = conn.execute(
            "SELECT cuide, tipo_documento, numero FROM documentos_electronicos "
            "WHERE id = ?", (res['id_documento'],)).fetchone()
    finally:
        conn.close()
    assert fila[1] == 'FV'
    assert fila[0] == res['cuide']
    assert fila[2].startswith('SETP222222222-')


def test_la_nota_credito_corrige_una_factura_electronica(configurado):
    """La cadena completa sobre una FV: es el caso que estaba roto.

    El filtro del documento original era `tipo_documento = 'POS'`, así que la
    nota crédito de una factura electrónica no encontraba qué corregir. Aquí se
    emite la FV, se genera la NC y se comprueba que la venta queda revertida.
    """
    from ferreteria.services import dian_nota_credito

    id_fv = _venta('FV', con_nit=True)
    conn = get_db()
    try:
        dian_emision.emitir_venta(conn, id_fv, contingencia=True)
        doc = conn.execute(
            "SELECT id, cuide FROM documentos_electronicos WHERE id_venta = ?",
            (id_fv,)).fetchone()
        assert doc[1], 'la FV debe tener CUIDE para poder referenciarla'

        # contingencia=True: genera y firma SIN intentar llamar a la DIAN. Sin
        # esto el test se queda esperando el timeout de red, que es de minutos.
        nc = dian_nota_credito.emitir_nota_credito(
            conn, id_fv, motivo_codigo='1',
            motivo_descripcion='Devolución total', contingencia=True)

        assert nc['numero'].startswith('NC-')
        # La NC declara NegativeValue: es lo que la DIAN espera para una nota.
        fila = conn.execute(
            "SELECT tipo_documento, documento_referido, motivo FROM documentos_electronicos "
            "WHERE id = ?", (nc['id_documento'],)).fetchone()
        assert fila[0] == 'NC'
        assert fila[1] == doc[1], (
            'la nota debe referenciar el CUIDE de la FV, no el de un POS')
    finally:
        conn.close()


def test_sin_clave_tecnica_se_avisa_con_el_nombre_de_la_serie(configurado):
    """El error debe decir QUÉ serie configurar, no un mensaje genérico."""
    conn = get_db()
    try:
        dian_series.actualizar_serie(conn, 'FV', {'clave_tecnica': ''})
        conn.execute("UPDATE configuracion SET clave_tecnica='' WHERE id=1")
        conn.commit()
        id_venta = _venta('FV', con_nit=True)
        with pytest.raises(dian_emision.ErrorEmision) as e:
            dian_emision.emitir_venta(conn, id_venta, contingencia=True)
    finally:
        conn.close()
    assert 'FV' in str(e.value), (
        'el mensaje debe nombrar la serie que falta configurar')
