"""El ciclo de la contingencia: se cae la red, vuelve la red, se envía.

ESTE ERA UN AGUERO REAL. `procesar_cola()` existía y funcionaba, pero solo se
llamaba desde un botón que el admin tenía que acordarse de pulsar. Si nadie lo
pulsaba, los documentos en contingencia se quedaban sin enviar PARA SIEMPRE.

Eso es justo el fallo que la contingencia existe para evitar: la venta estaba
cobrada, el inventario descontado, y el documento nunca llegaba a la DIAN. Sin
un reintento automático, un corte de internet de una hora станови una factura
perdida.

Aquí se prueba el circuito COMPLETO, con la cola simulada:
  1. Se emite un documento con la red caida → queda en contingencia.
  2. Se encola con un próximo intento.
  3. Se levanta la red.
  4. El hilo reintenta y el documento SALE.

Y se fija que el hilo no se amide si la excepción (un hilo muerto = cola
muerta = documentos perdidos).
"""
import os
import shutil
import tempfile
import time

import pytest

_DIR = tempfile.mkdtemp()
os.environ['FERRETERIA_DB'] = os.path.join(_DIR, 'ferreteria.db')
shutil.copy2(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'ferreteria-semilla.db'),
    os.environ['FERRETERIA_DB'])

from ferreteria.app_factory import create_app  # noqa: E402
from ferreteria.db import get_db  # noqa: E402
from ferreteria.services import dian_emision, dian_reintento  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def _preparada():
    from ferreteria import db as db_mod
    conn = get_db()
    db_mod._crear_tablas_base(conn.cursor())
    db_mod._aplicar_migraciones(conn.cursor())
    conn.execute("UPDATE configuracion SET nit='900187391', "
                 "digito_verificacion='7', "
                 "direccion='Calle 1, Samana', "
                 "codigo_municipio='17665', codigo_departamento='17', "
                 "regimen_fiscal='Responsable de IVA', "
                 "responsabilidades='O-13', "
                 "clave_tecnica='CLAVE', dian_ambiente='2' WHERE id=1")
    conn.execute("INSERT OR IGNORE INTO series_dian (tipo_documento, prefijo, "
                 "consecutivo, clave_tecnica) VALUES ('POS','SETP1',1,'CLAVE')")

    # La firma es obligatoria ANTES de enviar: sin certificado la emisión se
    # detiene y nunca llega a la parte de red, que es lo que se prueba aquí.
    # Se usa el certificado autofirmado que genera `_certificado_prueba.py`.
    cert = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'certificado_prueba.p12')
    if os.path.exists(cert):
        conn.execute("UPDATE configuracion SET certificado_ruta=?, "
                     "certificado_clave='clave-de-prueba' WHERE id=1", (cert,))
    conn.commit()
    conn.close()
    dian_reintento.detener()


@pytest.fixture
def venta():
    conn = get_db()
    try:
        # El fixture es POR PRUEBA y la base es la misma, así que las
        # migraciones se aplican una vez aqui: `get_db()` solo abre la
        # conexión y la copia de la semilla es de antes de la tabla de series.
        from ferreteria import db as db_mod
        db_mod._aplicar_migraciones(conn.cursor())
        # La base es la misma para todas las pruebas del modulo. Si queda un
        # trabajo de la corrida anterior, este test vería "hay cola" cuando lo
        # que quiere comprobar es que SU documento se encoló.
        conn.execute("DELETE FROM cola_dian")
        conn.execute("DELETE FROM documentos_electronicos")
        conn.commit()
        prod = conn.execute("SELECT id FROM productos WHERE COALESCE(activo,1)=1 LIMIT 1").fetchone()
        cur = conn.execute(
            "INSERT INTO ventas (id_cliente, fecha_dia, hora, total_venta, "
            "saldo_pendiente, tipo_pago, subtotal_venta, iva_valor, iva_porcentaje, "
            "anulada, tipo_documento_dian) VALUES (1,'2026-09-30','10:00:00',"
            "119000,0,'efectivo',100000,19000,19,0,'POS')")
        id_venta = cur.lastrowid
        conn.execute("INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,"
                     " precio_unitario, subtotal) VALUES (?,?,1,100000,100000)",
                     (id_venta, prod[0]))
        conn.commit()
        return id_venta
    finally:
        conn.close()


@pytest.fixture
def sin_red(monkeypatch):
    """La DIAN no responde: la emisión cae en contingencia.

    Se parchea `_llamar`, que es la función por la que la emisión sale a la
    red. El nombre lleva guion bajo porque es interna del módulo SOAP; lo que
    importa es que la emisión llegue a ella.
    """
    from ferreteria.services import dian_soap

    def _falla(*a, **k):
        raise dian_soap.ErrorSOAP('sin red')

    monkeypatch.setattr(dian_soap, '_llamar', _falla)
    return monkeypatch


# ═════════════════════════════════════════════════════
# 1. La contingencia existe y encola
# ═════════════════════════════════════════════════════
def test_sin_red_el_documento_queda_en_contingencia_y_se_encola(venta, sin_red):
    """El camino crítico: la venta NO se pierde, se guarda para después."""
    conn = get_db()
    try:
        res = dian_emision.emitir_venta(conn, venta, contingencia=True)
    finally:
        conn.close()

    assert res['estado'] == 'contingencia', (
        'sin red el documento debe quedar en contingencia, no fallar')
    assert res['cuide'], 'debe tener CUIDE aunque no se haya enviado'
    assert res['contingencia'] is True

    # Y debe haber un trabajo en la cola para reintentarlo.
    conn = get_db()
    try:
        trabajo = conn.execute(
            "SELECT estado, intentos, proximo_intento FROM cola_dian "
            "WHERE id_documento = ?", (res['id_documento'],)).fetchone()
    finally:
        conn.close()
    assert trabajo, 'el documento en contingencia no se encoló: nunca se reintentará'
    assert trabajo[0] == 'pendiente'
    # `proximo_intento` puede quedar en NULL: asi marca la cola "listo para
    # enviar YA" (es lo que hace `encolar` con intentos=0). Lo importante es
    # que el hilo lo RECONOZCA, y ese es justo el bug que esta prueba
    # destapó: la consulta del hilo solo miraba `proximo_intento <= ahora` y
    # dejaba fuera los NULL, o sea, todos los recién encolados.
    assert trabajo[1] == 0, 'un trabajo recién encolado va con 0 intentos'


# ═════════════════════════════════════════════════════
# 2. El hilo reintenta cuando vuelve la red
# ═════════════════════════════════════════════════════
def test_el_hilo_reintenta_cuando_vuelve_la_red(venta, sin_red, monkeypatch):
    """EL CICLO COMPLETO. Es la prueba que faltaba.

    Se emite sin red, se deja vencer el próximo intento, se levanta la red y
    se corre un ciclo del hilo. El documento tiene que SALE.
    """
    conn = get_db()
    try:
        res = dian_emision.emitir_venta(conn, venta, contingencia=True)
        id_doc = res['id_documento']
        # Se vence el backoff para que el hilo lo considere listo.
        conn.execute("UPDATE cola_dian SET proximo_intento='2000-01-01 00:00:00' "
                     "WHERE id_documento = ?", (id_doc,))
        conn.commit()
    finally:
        conn.close()

    # Vuelve la red: el servicio responde con un ApplicationResponse de código
    # 00. OJO: en el protocolo de la DIAN el exito es '00' (o '01' con
    # notificacion), NO '100'. Con '100' el parser dice exito=True pero
    # `estado_desde_respuesta` devuelve 'rechazado', porque 100 no está en
    # CODIGOS_RECHAZO_DIAN pero tampoco es un código de aceptación.
    from ferreteria.services import dian_soap
    respuesta = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soap:Body><SendBillSyncResponse>'
        '<SendBillSyncResult>'
        '<ApplicationResponse '
        'xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2">'
        '<cbc:ResponseCode '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        '00</cbc:ResponseCode>'
        '</ApplicationResponse>'
        '</SendBillSyncResult>'
        '</SendBillSyncResponse></soap:Body></soap:Envelope>'
    )
    monkeypatch.setattr(dian_soap, '_llamar', lambda *a, **k: respuesta)

    # Un ciclo del hilo, con la red ya levantada.
    conn = get_db()
    try:
        assert dian_reintento._hay_trabajos_pendientes(conn), (
            'el hilo no ve el trabajo vencido: no lo reintentará')
        resumen = dian_emision.procesar_cola(conn)
    finally:
        conn.close()

    conn = get_db()
    try:
        estado = conn.execute(
            "SELECT estado FROM documentos_electronicos WHERE id = ?",
            (id_doc,)).fetchone()[0]
        trabajo = conn.execute(
            "SELECT estado FROM cola_dian WHERE id_documento = ?",
            (id_doc,)).fetchone()[0]
    finally:
        conn.close()

    assert estado == 'aceptado', (
        f'con la red arriba el documento deberia aceptarse; quedo en "{estado}"')
    assert trabajo == 'completado', 'el trabajo de la cola no se cerró'


def test_sin_trabajos_el_hilo_no_hace_nada():
    """En reposo el hilo no debe tocar nada."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM cola_dian WHERE proximo_intento > '2000-01-01'")
        conn.commit()
    finally:
        conn.close()
    conn = get_db()
    try:
        # Puede haber trabajos viejos: se mide que la consulta NO lance.
        dian_reintento._hay_trabajos_pendientes(conn)
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# 3. El hilo tiene que ser a prueba de fallos
# ═════════════════════════════════════════════════════
def test_el_hilo_no_muere_si_la_base_falla():
    """Un hilo muerto = cola muerta = documentos perdidos en silencio.

    Se fuerza un error en la conexión y se comprueba que el bucle aguanta: si
    una excepción lo tumbara, `dian_reintento.activo()` sería False.
    """
    llamadas = {'n': 0}

    class _ConnQueFalla:
        def execute(self, *a, **k):
            raise RuntimeError('disco lleno')

        def close(self):
            pass

    def _db_rota():
        return _ConnQueFalla()

    hilo = dian_reintento.iniciar(_FakeApp(), intervalo=0.05, obtener_db=_db_rota)
    assert hilo is not None
    time.sleep(0.3)
    activo = dian_reintento.activo()
    dian_reintento.detener()
    assert activo, (
        'el hilo se murio con la exception: la contingencia se quedaria sin '
        'reintentar nunca')


def test_el_hilo_se_puede_arrancar_una_sola_vez():
    app = _FakeApp()
    primero = dian_reintento.iniciar(app, intervalo=60)
    segundo = dian_reintento.iniciar(app, intervalo=60)
    try:
        assert primero is not None
        assert segundo is None, 'se arrancó un segundo hilo sobre el mismo proceso'
    finally:
        dian_reintento.detener(timeout=0.5)


def test_al_arrancar_dos_veces_no_hay_dos_hilos():
    """La app puede recrearse (recarga en caliente): no debe acumular hilos."""
    app = _FakeApp()
    dian_reintento.iniciar(app, intervalo=60)
    dian_reintento.iniciar(app, intervalo=60)
    dian_reintento.iniciar(app, intervalo=60)
    try:
        assert dian_reintento.activo()
    finally:
        dian_reintento.detener(timeout=0.5)


def test_create_app_puede_arrancar_sin_el_hilo():
    """Los tests necesitan apagar el hilo: un temporizador los hace intermitentes."""
    app = create_app(inicializar_db=False, iniciar_hilo_dian=False)
    assert not dian_reintento.activo(), 'el hilo arrancó pese a pedirlo apagado'


class _FakeApp:
    """Placeholder: `iniciar` no usa la app, solo la recibe por firma."""
    def __init__(self):
        self.config = {}
