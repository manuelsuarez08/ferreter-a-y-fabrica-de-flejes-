"""Configuración centralizada de la aplicación.

Aísla las decisiones de configuración (rutas, claves, parámetros de BD) del
resto del código. Antes estas constantes estaban dispersas en app.py; ahora
viven en un único punto, cumpliendo el principio de responsabilidad única
(SRP) y facilitando el testeo.
"""
import os

# Directorio raíz del proyecto (un nivel arriba de este paquete).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ruta a la base SQLite "semilla": la que vive junto al código (y en el repo).
# Sirve como origen para poblar por primera vez un disco persistente vacío.
DB_SEMILLA = os.path.join(BASE_DIR, 'ferreteria.db')

# Ruta efectiva de la base de datos. Si se define FERRETERIA_DB (p. ej. un
# disco persistente en Render: /var/data/ferreteria.db) se usa esa; si no, la
# semilla junto al código.
def _resolver_db_name():
    return os.environ.get('FERRETERIA_DB') or DB_SEMILLA
DB_NAME = _resolver_db_name()

# Clave secreta de Flask para firmar la sesión.
SECRET_KEY = os.environ.get('SECRET_KEY', 'clave_secreta_ferreteria')

# Parámetros de conexión SQLite.
DB_TIMEOUT = 15
DB_BUSY_TIMEOUT_MS = 15000
DB_CACHE_SIZE_KB = -16000

# ── Reglas de negocio reutilizables ────────────────────────
# Tipos de tarifa válidos para alquiler de maquinaria.
TIPOS_TARIFA = ('dia', 'hora', 'turno', 'bulto')

# Estados válidos de un equipo de alquiler.
ESTADOS_EQUIPO = ('Disponible', 'En Alquiler', 'En Mantenimiento')

# Estados del flujo de órdenes de fábrica (flejes).
ESTADOS_ORDEN_FLEJE = ('En Cola', 'En Figurado', 'Completado')

# Estados del flujo de despacho de ventas "para llevar".
ESTADOS_DESPACHO = ('pendiente_preparar', 'preparando', 'listo', 'entregado')

# Estados del flujo de pedidos.
ESTADOS_PEDIDO = ('pendiente', 'alistando', 'listo', 'en_camino', 'entregado', 'cancelado')

# Máquina de estados de pedidos validada por rol (patrón State).
TRANSICIONES_PEDIDO = {
    'pendiente': {'admin': ['alistando', 'cancelado'], 'empleado': ['cancelado'], 'bodega': ['alistando', 'cancelado']},
    'alistando': {'admin': ['listo', 'pendiente', 'cancelado'], 'bodega': ['listo', 'pendiente', 'cancelado']},
    'listo':     {'admin': ['en_camino', 'cancelado'], 'bodega': ['en_camino'], 'motocarguero': ['en_camino']},
    'en_camino': {'admin': ['entregado', 'listo'], 'motocarguero': ['entregado']},
    'entregado': {'admin': ['en_camino']},
    'cancelado': {'admin': ['pendiente']},
}

# Índices de búsqueda frecuente (idempotentes).
INDICES = (
    "CREATE INDEX IF NOT EXISTS idx_prod_nombre ON productos(nombre)",
    "CREATE INDEX IF NOT EXISTS idx_prod_codigo ON productos(codigo_barras)",
    "CREATE INDEX IF NOT EXISTS idx_prod_categoria ON productos(categoria)",
    "CREATE INDEX IF NOT EXISTS idx_prod_activo ON productos(activo)",
    "CREATE INDEX IF NOT EXISTS idx_detventa_producto ON detalle_ventas(id_producto)",
    "CREATE INDEX IF NOT EXISTS idx_movinv_producto ON movimientos_inventario(id_producto)",
    "CREATE INDEX IF NOT EXISTS idx_pedidos_estado ON pedidos (estado)",
    "CREATE INDEX IF NOT EXISTS idx_pedidos_moto  ON pedidos (id_motocarguero)",
    # Índice compuesto para el patrón real del POS: filtrar activos y ordenar por nombre.
    "CREATE INDEX IF NOT EXISTS idx_prod_activo_nombre ON productos(activo, nombre COLLATE NOCASE)",
    # Índice para acelerar el cálculo de cartera (ventas con saldo pendiente).
    "CREATE INDEX IF NOT EXISTS idx_ventas_cliente_saldo ON ventas(id_cliente, saldo_pendiente)",
)
