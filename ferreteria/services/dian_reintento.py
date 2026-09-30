"""Reintento automático de la cola DIAN: un hilo en segundo plano.

EL PROBLEMA
`procesar_cola()` existe y funciona, pero solo se llamaba desde un botón que
el admin tiene que acordarse de pulsar. Si nadie lo pulsaba, los documentos
en contingencia se quedaban sin enviar PARA SIEMPRE: la venta estaba cobrada,
el inventario descontado, y el documento nunca llegaba a la DIAN.

Eso es exactamente el fallo que la contingencia existe para evitar. Un
documento en contingencia es un documento a medio enviar; sin reintento
automático es un documento perdido.

QUÉ HACE ESTE MÓDULO
Un hilo demonio, en segundo plano, que:
  - Espera el intervalo configurado.
  - Mira si hay trabajos listos para reintentar (`proximo_intento` vencido).
  - Si los hay, llama a `procesar_cola()`.
  - Si no hay nada, vuelve a dormir.

No toca la base si no hay nada pendiente, así que el costo en reposo es
despreciable. Y no bloquea al cajero: la venta no espera a este hilo.

Por qué un hilo y no una tarea programada: la aplicación es de escritorio, se
arranca con doble clic y no hay cron ni celery. Un `threading.Thread` con
`daemon=True` muere con el proceso y no impide cerrarlo.
"""
import logging
import threading
import time

# Intervalo entre revisiones de la cola. 5 minutos es un punto medio: si la
# red vuelve, el documento sale en minutos; si sigue caida, no se martillea el
# servicio de la DIAN con peticiones inútiles.
INTERVALO_DEFECTO = 300

# Espera inicial antes de la primera revisión: da tiempo a que la aplicación
# termine de arrancar y a que el admin abra la pantalla, sin que el primer
# barrido compita con el inicio.
ESPERA_INICIAL = 30

log = logging.getLogger(__name__)

_hilo = None
_cerrar = threading.Event()


def _hay_trabajos_pendientes(conn) -> bool:
    """¿Hay algo listo para reintentar ahora?

    OJO con el `IS NULL`: la cola usa `proximo_intento = NULL` para marcar
    "listo para enviar YA" (es lo que hace `encolar` con `intentos=0`). Si esta
    consulta solo mirara `proximo_intento <= ahora`, los recién encolados
    —los NULOS— quedarían fuera y el hilo no reintentaría NADA.

    La misma condición usa `procesar_cola`, y tiene que ser idéntica: si el
    hilo ve trabajo que la cola no considera listo, o al revés, se Asking
    documentos que nunca salen.
    """
    from datetime import datetime

    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fila = conn.execute(
        "SELECT 1 FROM cola_dian WHERE estado = 'pendiente' "
        "AND (proximo_intento IS NULL OR proximo_intento <= ?) LIMIT 1",
        (ahora,),
    ).fetchone()
    return fila is not None


def _bucle(intervalo, obtener_db):
    """Ciclo del hilo. Separado para poder probarlo sin esperar 5 minutos."""
    log.info('Hilo de reintento DIAN iniciado (cada %s s)', intervalo)
    while not _cerrar.is_set():
        # `wait` devuelve True si alguien puso el evento: es la forma de
        # despertar de inmediato al cerrar, en vez de esperar el intervalo.
        if _cerrar.wait(intervalo):
            break
        try:
            conn = obtener_db()
            try:
                if _hay_trabajos_pendientes(conn):
                    from . import dian_emision
                    resumen = dian_emision.procesar_cola(conn)
                    enviados = resumen.get('enviados', 0)
                    if enviados:
                        log.info(
                            'Cola DIAN: %s documento(s) reenviado(s) '
                            'tras recuperar la conexión', enviados)
            finally:
                conn.close()
        except Exception as error:  # pragma: no cover - red o disco
            # El hilo nunca debe morir: si una excepción lo tumba, la
            # contingencia se queda sin reintentar para siempre, que es
            # justo lo que este módulo existe para evitar.
            log.warning('Hilo de reintento DIAN: %s', error)
    log.info('Hilo de reintento DIAN detenido')


def iniciar(app, intervalo=None, obtener_db=None):
    """Arranca el hilo. Devuelve el hilo, o None si ya estaba corriendo.

    Se llama desde `create_app` una sola vez. Los tests la llaman con un
    intervalo largo o directamente sin arrancar el hilo, para que la suite
    no dependa de un temporizador en segundo plano.
    """
    global _hilo
    if _hilo is not None and _hilo.is_alive():
        return None

    if intervalo is None:
        intervalo = INTERVALO_DEFECTO
    if obtener_db is None:
        # `..db` y no `.db`: este módulo vive en ferreteria/services/, así que
        # la base está un nivel arriba. Con un punto importaba un submódulo
        # inexistente y reventaba al arrancar el hilo.
        from ..db import get_db
        obtener_db = get_db

    _cerrar.clear()
    _hilo = threading.Thread(
        target=_bucle, args=(intervalo, obtener_db),
        name='cola-dian-reintento', daemon=True)
    _hilo.start()
    return _hilo


def detener(timeout=2.0):
    """Pide al hilo que termine. Se usa al cerrar la app y en los tests."""
    _cerrar.set()
    hilo = _hilo
    if hilo is not None and hilo.is_alive():
        hilo.join(timeout=timeout)
    return True


def activo() -> bool:
    return _hilo is not None and _hilo.is_alive()
