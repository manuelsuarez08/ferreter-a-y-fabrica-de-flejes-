"""Configuración centralizada de la aplicación.

Aísla las decisiones de configuración (rutas, claves, parámetros de BD) del
resto del código. Antes estas constantes estaban dispersas en app.py; ahora
viven en un único punto, cumpliendo el principio de responsabilidad única
(SRP) y facilitando el testeo.
"""
import os
import posixpath
# Directorio raíz del proyecto (un nivel arriba de este paquete).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Carga opcional de un archivo .env en la raíz del proyecto. Se hace sin
# dependencias externas (no rompe si python-dotenv no está instalado) para que
# los secretos de configuración vivan fuera del repositorio.
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
#
# IMPORTANTE: la semilla y la base de trabajo son archivos DISTINTOS.
#   - `ferreteria-semilla.db`  -> en el repo. Solo esquema + catalogo (productos,
#     equipos, usuarios). NUNCA datos de operacion (ventas, clientes con cedula,
#     auditoria) ni secretos (NIT, clave tecnica, certificado).
#   - `ferreteria.db`         -> la base de trabajo local. NUNCA se versiona
#     con datos reales: cada quien tiene la suya.
# Antes `ferreteria.db` cumplia los dos papeles, asi que un despliegue podia
# pisar los datos de quien estaba probando la app.
DB_SEMILLA = os.path.join(BASE_DIR, 'ferreteria-semilla.db')

# La base de trabajo local. Es la que se usa si no hay FERRETERIA_DB en el
# entorno. En desarrollo se separa de la semilla para que una regeneracion de
# la semilla no borre el historico de quien esta probando.
DB_TRABAJO = os.path.join(BASE_DIR, 'ferreteria.db')

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
# base de trabajo local.
def _resolver_db_name():
    return os.environ.get('FERRETERIA_DB') or DB_TRABAJO
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

# ── Directorio de las instancias de los clientes ───────────────────
# Con la arquitectura de una base por ferretería (Opción A), el panel del
# desarrollador crea un archivo .db por cliente. Esos archivos son DATOS, y en
# Render cualquier ruta fuera del disco montado es efímera: se pierden en cada
# despliegue.
#
# Por eso, en producción, el directorio se pone bajo /var/data (el disco
# persistente) y NO junto a la base de trabajo: si FERRETERIA_DB ya está
# apuntando al disco, se reutiliza ese mismo directorio, que es lo coherente
# (la instancia tiene que sobrevivir exactamente donde vive el resto).
#
# El orden de decisión es:
#   1. FERRETERIA_INSTANCIAS, si está definida (permite cambiar de infraestructura).
#   2. El directorio de la base de trabajo, si NO es una ruta efímera típica.
#   3. /var/data/instancias.
#
# Importante: la resolución es una función, no una constante, porque las variables
# de entorno se fijan antes de importar el módulo en el arranque pero en los
# tests se cambian en caliente. `DIRECTORIO_INSTANCIAS` se fija al importar y es
# lo que consume el panel.
DISCO_PERSISTENTE_RENDER = '/var/data'


def _normalizar(ruta):
    """Deja una ruta comparable sin depender del sistema operativo.

    OJO: no se usa `os.path.abspath`. En Windows convierte `/var/data` en
    `C:\\var\\data`, que deja de coincidir con la ruta persistente y hace que el
    panel crea que está en un directorio efímero cuando en realidad no lo está.
    `normpath` normaliza separadores y `..` sin inventar una unidad.
    """
    return os.path.normpath(str(ruta or '')).replace('\\', '/').rstrip('/').lower()


def _es_ruta_efimera(ruta):
    """True si la ruta está dentro del contenedor de Render (y se pierde al desplegar)."""
    if not EN_PRODUCCION:
        return False
    return not _normalizar(ruta).startswith(_normalizar(DISCO_PERSISTENTE_RENDER))


def resolver_directorio_instancias():
    """Directorio donde se crean las bases de datos de los clientes.

    Se evalúa en el arranque y en cada provisionamiento, no solo al importar:
    así una corrección de configuración no exige reiniciar.
    """
    explicita = os.environ.get('FERRETERIA_INSTANCIAS')
    if explicita:
        return explicita

    # Junto a la base de trabajo, que es lo natural en local. Se devuelve con su
    # forma NATIVA (separadores y mayúsculas originales): es la ruta que se le
    # muestra al usuario y la que se le pasa a `os.path`. La normalizada solo se
    # usa para COMPARAR, nunca para devolver.
    #
    # OJO: comparar contra la ruta NORMALIZADA y no con `os.path.dirname`. En
    # Windows `dirname('/var/data/ferreteria.db')` devuelve '/var/data\\', con
    # separador mixto, y la comparación con /var/data falla: el panel creería que
    # está en un directorio efímero cuando en realidad es el disco persistente.
    if _es_ruta_efimera(DB_NAME):
        # Estamos en Render y la base de trabajo NO está en el disco persistente:
        # poner aquí las instancias garantiza perderlas. Se va al disco.
        return f'{DISCO_PERSISTENTE_RENDER}/instancias'

    # La ruta se devuelve tal como el sistema la entiende, pero sin perder el
    # estilo POSIX. `posixpath` entra en juego cuando la ruta empieza por '/': en
    # Linux es lo mismo que `os.path`, y evita que al probar en Windows (donde
    # `os.path.dirname('/var/data/x.db')` devuelve '\var\data') la ruta del disco
    # persistente se convierta en una de Windows que no existe en el servidor.
    if str(DB_NAME).startswith('/'):
        return posixpath.dirname(str(DB_NAME)) or '/'

    return os.path.dirname(os.path.abspath(DB_NAME))


DIRECTORIO_INSTANCIAS = resolver_directorio_instancias()


def aviso_persistencia_instancias(directorio=None):
    """Texto de advertencia si el directorio de instancias se pierde al desplegar.

    No lanza nada: la app debe arrancar igual para que el desarrollador pueda entrar y
    corregir la configuración. Solo devuelve el texto (o None si todo está bien)
    para que quien llama lo muestre en los logs y en el panel.

    Returns:
        str o None. El texto explica dónde quedó el directorio y qué se pierde.
    """
    ruta = directorio or resolver_directorio_instancias()

    if _es_ruta_efimera(ruta):
        return (
            f'ATENCION: el directorio de instancias es "{ruta}", que en Render '
            f'es efimero. Las bases de datos de los clientes se perderian en '
            f'cada despliegue. Configure un disco persistente en '
            f'{DISCO_PERSISTENTE_RENDER} y apunte '
            'FERRETERIA_INSTANCIAS (o FERRETERIA_DB) ahi.'
        )

    # Fuera de Render puede seguir siendo un contenedor o una ruta temporal.
    # Solo se avisa si además parece efímero por estar en un temporal conocido.
    if not EN_PRODUCCION and any(
        ruta.startswith(prefijo) for prefijo in ('/tmp', '/var/folders', 'C:\\Temp')
    ):
        return (
            f'ATENCION: el directorio de instancias es "{ruta}", que parece '
            'temporal. Las bases de los clientes se perderian al limpiar el '
            'sistema. Configure FERRETERIA_INSTANCIAS con una ruta permanente.'
        )

    return None

# Clave secreta de Flask para firmar la sesión.
SECRET_KEY = os.environ.get('SECRET_KEY', 'clave_secreta_ferreteria')

# Horas que dura una sesión iniciada. El POS trabaja por turnos: si la sesión
# muere a la media hora, el cajero ve el POS "vacío" (los fetch se quedan con
# el HTML del login) sin entender por qué. 12 horas cubre el turno completo.
SESION_HORAS = int(os.environ.get('SESION_HORAS', 12))

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
ESTADOS_DESPACHO = ('pendiente_preparar', 'listo', 'entrega_parcial', 'entregado')

# Transiciones permitidas por rol en el despacho de ventas "para llevar".
# Bodega prepara; el motocarguero entrega. Admin puede hacer todo (rescate).
# 'entrega_parcial' cubre al cliente que paga todo y retira en varias salidas.
TRANSICIONES_DESPACHO = {
    'pendiente_preparar': {'bodega': ['listo'], 'admin': ['listo', 'entregado', 'entrega_parcial']},
    'listo':              {'motocarguero': ['entregado', 'entrega_parcial'],
                           'bodega': ['entregado', 'entrega_parcial'],
                           'admin': ['entregado', 'entrega_parcial', 'pendiente_preparar']},
    'entrega_parcial':    {'motocarguero': ['entregado', 'entrega_parcial'],
                           'bodega': ['entregado', 'entrega_parcial', 'listo'],
                           'admin': ['entregado', 'entrega_parcial', 'listo', 'pendiente_preparar']},
    'entregado':          {'admin': ['listo', 'entrega_parcial']},
}

# Etiqueta legible y color de badge para cada estado de despacho.
ETIQUETAS_DESPACHO = {
    'pendiente_preparar': ('\u23f3 Pendiente en Bodega', 'warning text-dark'),
    'listo':              ('\U0001f6f5 Listo para Entrega', 'primary'),
    'entrega_parcial':    ('\U0001f4e6 Entrega Parcial', 'info text-dark'),
    'entregado':          ('\u2705 Entregado', 'success'),
}

# Estados del flujo de pedidos.
ESTADOS_PEDIDO = ('pendiente', 'alistando', 'listo', 'en_camino', 'entregado', 'cancelado')

# Estados válidos de una cotización.
ESTADOS_COTIZACION = ('Pendiente', 'Aprobada', 'Vencida', 'Anulada')

# Cliente por defecto del POS: el "consumidor final" genérico de mostrador. Es el
# id 1 que siembra `db._sembrar_datos_por_defecto`. El formulario de venta arranca
# siempre aquí para que el cajero no tenga que buscar a alguien por cada venta
# de mostrador, y lo cambia solo cuando el cliente sí está registrado.
CLIENTE_MOSTRADOR_ID = 1
NOMBRE_CLIENTE_MOSTRADOR = 'Cliente Mostrador (General)'

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
