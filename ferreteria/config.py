"""Configuración centralizada de la aplicación.

Aísla las decisiones de configuración (rutas, claves, parámetros de BD) del
resto del código. Antes estas constantes estaban dispersas en app.py; ahora
viven en un único punto, cumpliendo el principio de responsabilidad única
(SRP) y facilitando el testeo.
"""
import os
# Directorio raíz del proyecto (un nivel arriba de este paquete).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Carga opcional de un archivo .env en la raíz del proyecto. Se hace sin
# dependencias externas (no rompe si python-dotenv no está instalado) para que
# las credenciales de Siigo y el proxy estático vivan fuera del repositorio.
def _cargar_env():
    ruta = os.path.join(BASE_DIR, '.env')
    if not os.path.exists(ruta):
        return
    try:
        with open(ruta, encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith('#') or '=' not in linea:
                    continue
                clave, valor = linea.split('=', 1)
                clave, valor = clave.strip(), valor.strip().strip('"').strip("'")
                # No se sobreescribe lo que ya venga del entorno real.
                if clave and clave not in os.environ:
                    os.environ[clave] = valor
    except OSError:
        pass
_cargar_env()

# Ruta a la base SQLite "semilla": la que vive junto al código (y en el repo).
# Sirve como origen para poblar por primera vez un disco persistente vacío.
DB_SEMILLA = os.path.join(BASE_DIR, 'ferreteria.db')

# ── Versión de la semilla ───────────────────
# Un número entero en el archivo `ferreteria-semilla.version`. Cada vez que se
# cambia el catálogo de la semilla a mano (p. ej. borrar productos que no son
# productos), se sube este número. Al arrancar, si la versión del repositorio es
# MAYOR que la que quedó aplicada en el disco, la app adopta la semilla nueva.
# Sin esto, un disco que ya tenía datos NUNCA recibía los cambios del catálogo:
# la heurística de "1.5x productos" no se dispara con limpiezas (que reducen el
# número de productos).
SEMILLA_VERSION_FILE = os.path.join(BASE_DIR, 'ferreteria-semilla.version')

def _leer_version_semilla():
    try:
        with open(SEMILLA_VERSION_FILE, encoding='utf-8') as f:
            return int((f.read() or '0').strip())
    except (OSError, ValueError):
        return 0
SEMILLA_VERSION = _leer_version_semilla()

# Ruta efectiva de la base de datos. Si se define FERRETERIA_DB (p. ej. un
# disco persistente en Render: /var/data/ferreteria.db) se usa esa; si no, la
# semilla junto al código.
def _resolver_db_name():
    return os.environ.get('FERRETERIA_DB') or DB_SEMILLA
DB_NAME = _resolver_db_name()

# ¿Estamos en un servidor de producción (Render)? Render define RENDER=true.
EN_PRODUCCION = os.environ.get('RENDER', '').lower() == 'true'

# Si estamos en producción SIN disco persistente configurado, cada despliegue
# borraría los datos (la base vive dentro del contenedor efímero). Se avisa una
# sola vez al importar para que quede claro en los logs del servidor.
if EN_PRODUCCION and not os.environ.get('FERRETERIA_DB'):
    import warnings
    warnings.warn(
        'ATENCION: en produccion (Render) no esta configurada la variable '
        'FERRETERIA_DB. Sin ella, la base de datos vive dentro del contenedor y '
        'SE PERDERIA EN CADA DESPLIEGUE. Configure un disco persistente en '
        '/var/data y la variable FERRETERIA_DB=/var/data/ferreteria.db.',
        RuntimeWarning,
        stacklevel=2,
    )

# Clave secreta de Flask para firmar la sesión.
SECRET_KEY = os.environ.get('SECRET_KEY', 'clave_secreta_ferreteria')

# ── Facturación electrónica opcional con Siigo ──────────────────────────────
# La facturación electrónica NO se emite siempre: solo cuando el cajero marca
# la casilla en el POS. Estas credenciales se leen del entorno (.env).
SIIGO_USERNAME = os.environ.get('SIIGO_USERNAME', '')
SIIGO_ACCESS_KEY = os.environ.get('SIIGO_ACCESS_KEY', '')

# Siigo solo autoriza peticiones desde una IP fija. El tráfico sale por un proxy
# estático (Fixie o QuotaGuard); se acepta cualquiera de las dos variables.
FIXIE_URL = os.environ.get('FIXIE_URL') or os.environ.get('QUOTAGUARDSTATIC_URL') or ''

# Endpoints de la API de Siigo (v1). Se dejan configurables por si Siigo cambia
# el host o se quiere apuntar a un entorno de pruebas.
SIIGO_API_BASE = os.environ.get('SIIGO_API_BASE', 'https://api.siigo.com')
SIIGO_API_PARTNER_ID = os.environ.get('SIIGO_PARTNER_ID', 'FerreteriaPOS')

# Códigos por defecto que exige Siigo para construir el JSON de la factura.
# Deben existir en la configuración tributaria de la cuenta Siigo.
SIIGO_DEFAULT_DOCUMENT_ID = int(os.environ.get('SIIGO_DOCUMENT_ID', '1'))
SIIGO_DEFAULT_SELLER = int(os.environ.get('SIIGO_SELLER_ID', '0'))  # 0 -> se omite
SIIGO_DEFAULT_PAYMENT_ID = int(os.environ.get('SIIGO_PAYMENT_ID', '1'))
SIIGO_DEFAULT_TAX_ID = int(os.environ.get('SIIGO_TAX_ID', '0'))  # 0 -> producto sin IVA explícito
SIIGO_DEFAULT_TAX_PERCENTAGE = float(os.environ.get('SIIGO_TAX_PERCENTAGE', '0'))
# Tipo de comprobante: 'Invoice' = factura de venta electrónica.
SIIGO_DEFAULT_DOCUMENT_TYPE = os.environ.get('SIIGO_DOCUMENT_TYPE', 'Invoice')

# Timeout (segundos) de las peticiones a Siigo y máximo de intentos de reintento
# automático cuando la API responde con errores transitorios.
SIIGO_TIMEOUT = int(os.environ.get('SIIGO_TIMEOUT', '30'))
SIIGO_MAX_REINTENTOS = int(os.environ.get('SIIGO_MAX_REINTENTOS', '2'))

# Mapeo de los medios de pago del POS a los códigos de forma de pago de Siigo.
# El valor es el `payment.id` configurado en Siigo para cada medio.
SIIGO_MAPA_PAGOS = {
    'efectivo': int(os.environ.get('SIIGO_PAYMENT_EFECTIVO', os.environ.get('SIIGO_PAYMENT_ID', '1'))),
    'nequi_daviplata': int(os.environ.get('SIIGO_PAYMENT_TRANSFERENCIA', os.environ.get('SIIGO_PAYMENT_ID', '1'))),
    'tarjeta': int(os.environ.get('SIIGO_PAYMENT_TARJETA', os.environ.get('SIIGO_PAYMENT_ID', '1'))),
    'credito': int(os.environ.get('SIIGO_PAYMENT_CREDITO', os.environ.get('SIIGO_PAYMENT_ID', '1'))),
}

# Tipos de documento de identidad reconocidos por Siigo (campo `id_type`).
SIIGO_TIPOS_DOCUMENTO = {
    'CC': '13',   # Cédula de ciudadanía
    'NIT': '31',  # NIT
    'CE': '22',   # Cédula de extranjería
    'PP': '41',   # Pasaporte
    'TI': '12',   # Tarjeta de identidad
}

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
#   pendiente_preparar -> La ve BODEGA (badge naranja). Debe alistar el pedido.
#   listo              -> La ve el MOTOCARGUERO (badge azul). Debe entregar.
#   entregado          -> Historial (badge verde).
ESTADOS_DESPACHO = ('pendiente_preparar', 'listo', 'entregado')

# Transiciones permitidas por rol en el despacho de ventas "para llevar".
# Bodega prepara; el motocarguero entrega. Admin puede hacer todo (rescate).
TRANSICIONES_DESPACHO = {
    'pendiente_preparar': {'bodega': ['listo'], 'admin': ['listo', 'entregado']},
    'listo':              {'motocarguero': ['entregado'], 'bodega': ['entregado'], 'admin': ['entregado', 'pendiente_preparar']},
    'entregado':          {'admin': ['listo']},
}

# Etiqueta legible y color de badge para cada estado de despacho.
ETIQUETAS_DESPACHO = {
    'pendiente_preparar': ('\u23f3 Pendiente en Bodega', 'warning text-dark'),
    'listo':              ('\U0001f6f5 Listo para Entrega', 'primary'),
    'entregado':          ('\u2705 Entregado', 'success'),
}

# Estados del flujo de pedidos.
ESTADOS_PEDIDO = ('pendiente', 'alistando', 'listo', 'en_camino', 'entregado', 'cancelado')

# Estados válidos de una cotización.
ESTADOS_COTIZACION = ('Pendiente', 'Aprobada', 'Vencida', 'Anulada')

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
