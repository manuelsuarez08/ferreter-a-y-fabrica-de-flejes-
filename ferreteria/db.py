"""Capa de acceso a datos: conexión SQLite y esquema/migraciones.

Responsabilidad única: proveer conexiones y construir/actualizar el esquema.
Ninguna regla de negocio HTTP vive aquí, lo que permite reutilizar la capa de
datos desde scripts (cargar_datos.py, database.py) sin arrastrar dependencias
de Flask más allá de lo necesario.
"""
import os
import shutil
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from .config import (
    DB_NAME,
    DB_SEMILLA,
    DB_TIMEOUT,
    DB_BUSY_TIMEOUT_MS,
    DB_CACHE_SIZE_KB,
    INDICES,
    SEMILLA_VERSION,
)


def get_db():
    """Conexión SQLite optimizada para uso rápido y concurrente."""
    conn = sqlite3.connect(DB_NAME, timeout=DB_TIMEOUT, check_same_thread=False)
    try:
        # WAL mejora la concurrencia lectura/escritura. No aplica a :memory:.
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(f"PRAGMA cache_size={DB_CACHE_SIZE_KB}")
        conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    except sqlite3.Error:
        pass
    return conn


def migrar_columna(conn, nombre_tabla, nombre_columna, definicion):
    """Agrega una columna si no existe (idempotente)."""
    try:
        conn.execute(f"ALTER TABLE {nombre_tabla} ADD COLUMN {nombre_columna} {definicion}")
    except sqlite3.OperationalError:
        pass


def crear_indices(conn):
    """Crea los índices de búsqueda frecuente (idempotente)."""
    for sentencia in INDICES:
        try:
            conn.execute(sentencia)
        except sqlite3.Error:
            pass


def _crear_tablas_base(cursor):
    """Crea el conjunto de tablas del núcleo (usuarios, catálogo, ventas)."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            clave TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    ''')
    _crear_tablas_provisionamiento(cursor)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cedula_nit TEXT UNIQUE,
            telefono TEXT,
            direccion TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria TEXT,
            dimensiones TEXT,
            codigo_barras TEXT UNIQUE,
            precio_costo REAL DEFAULT 0,
            precio_venta REAL NOT NULL,
            stock_actual INTEGER NOT NULL DEFAULT 0,
            stock_minimo INTEGER DEFAULT 5,
            auditado INTEGER NOT NULL DEFAULT 0,
            stock_inicial INTEGER DEFAULT 0,
            fecha_auditoria TEXT DEFAULT NULL,
            activo INTEGER NOT NULL DEFAULT 1
        )
    ''')
    # NOTA: 'precio_costo' se conserva en la DB (opcional, por defecto 0) aunque
    # ya no se use en la interfaz ni en los cálculos del POS.
    # NOTA: 'stock_actual' ya NO tiene restricción CHECK, por lo que puede quedar
    # en saldo negativo sin bloquear la venta.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            fecha_dia TEXT NOT NULL,
            hora TEXT NOT NULL,
            total_venta REAL NOT NULL,
            saldo_pendiente REAL NOT NULL,
            tipo_pago TEXT NOT NULL,
            direccion_cliente TEXT,
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')


def _crear_tablas_operacion(cursor):
    """Crea las tablas de detalle, créditos, inventario, fábrica y caja."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta INTEGER NOT NULL,
            id_producto INTEGER NOT NULL,
            cantidad INTEGER NOT NULL,
            precio_unitario REAL NOT NULL,
            subtotal REAL NOT NULL,
            -- Acumulador de entregas por linea (para "para llevar" entregado por
            -- partes). OJO: tiene que estar AQUI y no solo en la lista de
            -- migraciones de abajo: una base nueva se creaba sin la columna y
            -- cada linea quedaba con cantidad_entregada NULL, asi que el pedido
            -- nacia con pendiente 0 y el motocarguero no podia registrar nada.
            cantidad_entregada INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (id_venta) REFERENCES ventas (id),
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS abonos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            monto REAL NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_cliente) REFERENCES clientes (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movimientos_inventario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_producto INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            motivo TEXT NOT NULL,
            usuario TEXT NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventario_hierro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            calibre TEXT NOT NULL,
            diametro_mm REAL NOT NULL DEFAULT 0,
            stock_kg REAL NOT NULL DEFAULT 0,
            ultimo_update TEXT,
            UNIQUE(calibre)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ordenes_figurado (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta INTEGER,
            id_cliente INTEGER NOT NULL,
            numero_orden TEXT NOT NULL UNIQUE,
            estado TEXT NOT NULL DEFAULT 'En Cola'
                CHECK (estado IN ('En Cola', 'En Figurado', 'Completado')),
            fecha_creacion TEXT NOT NULL,
            fecha_actualizacion TEXT,
            fecha_entrega TEXT,
            observaciones TEXT,
            kg_total REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (id_venta) REFERENCES ventas(id),
            FOREIGN KEY (id_cliente) REFERENCES clientes(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notificaciones_flejes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_orden INTEGER NOT NULL,
            numero_orden TEXT NOT NULL,
            cliente TEXT,
            mensaje TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            leida_pedidos INTEGER NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_orden) REFERENCES ordenes_figurado(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalles_fleje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_orden INTEGER NOT NULL,
            calibre TEXT NOT NULL,
            ancho_cm REAL NOT NULL,
            largo_cm REAL NOT NULL,
            largo_gancho_cm REAL NOT NULL DEFAULT 0,
            cantidad_piezas INTEGER NOT NULL DEFAULT 1,
            metros_lineales REAL NOT NULL DEFAULT 0,
            consumo_kg REAL NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'En Cola'
                CHECK (estado IN ('En Cola', 'En Figurado', 'Completado')),
            FOREIGN KEY (id_orden) REFERENCES ordenes_figurado(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            accion TEXT NOT NULL,
            entidad TEXT NOT NULL,
            entidad_id INTEGER,
            detalles TEXT,
            fecha TEXT NOT NULL
        )
    ''')
    # `nombre` es la RAZON SOCIAL DEL NEGOCIO, no el nombre del software. El
    # software se llama FerreControl (ver NOMBRE_PLATAFORMA en config.py) y este
    # campo lo llena el dueño de cada ferretería. El valor por defecto es
    # genérico a propósito: una base nueva no debe arrancar mostrando el nombre
    # de un cliente anterior.
    #
    # El comentario va FUERA del literal: SQLite no acepta `#` dentro de una
    # sentencia y falla con "unrecognized token".
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            nombre TEXT NOT NULL DEFAULT 'Mi Negocio',
            nit TEXT DEFAULT '',
            telefono TEXT DEFAULT '',
            direccion TEXT DEFAULT '',
            consecutivo INTEGER NOT NULL DEFAULT 1
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO configuracion (id) VALUES (1)")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            categoria TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            monto REAL NOT NULL,
            usuario TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cierres_caja (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT UNIQUE NOT NULL,
            base_inicial REAL NOT NULL DEFAULT 0,
            efectivo_esperado REAL NOT NULL,
            efectivo_contado REAL NOT NULL,
            diferencia REAL NOT NULL,
            observaciones TEXT,
            usuario TEXT NOT NULL,
            fecha_registro TEXT NOT NULL
        )
    ''')


def _crear_tablas_alquiler(cursor):
    """Crea las tablas del módulo de alquiler de maquinaria."""
    for tabla in ('equipos', 'equipos_alquiler'):
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {tabla} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_interno TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                categoria TEXT NOT NULL DEFAULT '',
                marca TEXT,
                modelo TEXT,
                numero_serie TEXT,
                estado TEXT NOT NULL DEFAULT 'Disponible'
                    CHECK (estado IN ('Disponible', 'En Alquiler', 'En Mantenimiento')),
                tipo_tarifa TEXT NOT NULL DEFAULT 'dia'
                    CHECK (tipo_tarifa IN ('dia', 'hora', 'turno', 'bulto')),
                tarifa REAL NOT NULL DEFAULT 0,
                tarifa_hora REAL DEFAULT 0,
                tarifa_turno REAL DEFAULT 0,
                tarifa_bulto REAL DEFAULT 0,
                medidas TEXT,
                especificaciones TEXT,
                cantidad_disponible INTEGER NOT NULL DEFAULT 1,
                cantidad_total INTEGER NOT NULL DEFAULT 1,
                fecha_compra TEXT,
                fecha_ultimo_mantenimiento TEXT,
                observaciones TEXT,
                activo INTEGER NOT NULL DEFAULT 1,
                fecha_registro TEXT,
                fecha_actualizacion TEXT
            )
        ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alquileres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente INTEGER NOT NULL,
            id_usuario INTEGER NOT NULL,
            fecha_salida TEXT NOT NULL,
            fecha_devolucion_pactada TEXT NOT NULL,
            fecha_devolucion_real TEXT,
            estado TEXT NOT NULL DEFAULT 'activo'
                CHECK (estado IN ('activo', 'devuelto', 'vencido', 'cancelado')),
            valor_deposito REAL NOT NULL DEFAULT 0,
            notas_salida TEXT,
            notas_devolucion TEXT,
            cargos_extra REAL NOT NULL DEFAULT 0,
            descuento REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            total_final REAL NOT NULL DEFAULT 0,
            fecha_registro TEXT NOT NULL,
            FOREIGN KEY (id_cliente) REFERENCES clientes(id),
            FOREIGN KEY (id_usuario) REFERENCES usuarios(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_alquiler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_alquiler INTEGER NOT NULL,
            id_equipo INTEGER NOT NULL,
            cantidad INTEGER NOT NULL DEFAULT 1,
            tarifa_tipo TEXT NOT NULL,
            tarifa_valor REAL NOT NULL,
            dias_usados INTEGER DEFAULT 0,
            horas_usadas INTEGER DEFAULT 0,
            turnos_usados INTEGER DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            estado_salida TEXT NOT NULL DEFAULT 'bueno'
                CHECK (estado_salida IN ('bueno', 'con_danos', 'faltante', 'otro')),
            estado_retorno TEXT,
            observaciones TEXT,
            FOREIGN KEY (id_alquiler) REFERENCES alquileres(id),
            FOREIGN KEY (id_equipo) REFERENCES equipos_alquiler(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mantenimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_equipo INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            costo REAL NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            responsable TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente'
                CHECK (estado IN ('pendiente', 'realizado', 'cancelado')),
            FOREIGN KEY (id_equipo) REFERENCES equipos_alquiler(id)
        )
    ''')


def _crear_tablas_pedidos(cursor):
    """Crea las tablas del módulo de pedidos."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pedidos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cliente        INTEGER NOT NULL,
            direccion_entrega TEXT,
            observaciones     TEXT,
            estado            TEXT NOT NULL DEFAULT 'pendiente',
            -- Estados: pendiente | alistando | listo | en_camino | entregado | cancelado
            usuario_vendedor  TEXT NOT NULL,
            usuario_bodega    TEXT,
            id_motocarguero   INTEGER,
            fecha_creacion    TEXT NOT NULL,
            fecha_actualizacion TEXT,
            FOREIGN KEY (id_cliente)      REFERENCES clientes (id),
            FOREIGN KEY (id_motocarguero) REFERENCES usuarios (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_pedidos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            id_pedido       INTEGER NOT NULL,
            id_producto     INTEGER NOT NULL,
            cantidad        INTEGER NOT NULL,
            precio_unitario REAL    NOT NULL,
            subtotal        REAL    NOT NULL,
            FOREIGN KEY (id_pedido)   REFERENCES pedidos (id),
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')
    # Entregas por partes de una venta "para llevar": el cliente puede pagar
    # todo y retirar la mercancia en varias salidas. Cada fila es una salida
    # (que producto, cuanto y quien/cuando) y queda como historial.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entregas_venta (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta     INTEGER NOT NULL,
            id_producto  INTEGER NOT NULL,
            cantidad     INTEGER NOT NULL,
            usuario      TEXT,
            fecha        TEXT NOT NULL,
            FOREIGN KEY (id_venta)    REFERENCES ventas (id),
            FOREIGN KEY (id_producto) REFERENCES productos (id)
        )
    ''')


def _aplicar_migraciones(cursor):
    """Migraciones idempotentes sobre bases de datos ya existentes."""
    for tabla, columna, definicion in (
        ('clientes', 'direccion', 'TEXT'),
        # Correo electrónico del cliente (contacto y envío de comprobantes).
        ('clientes', 'email', 'TEXT'),
        ('clientes', 'tipo_documento', "TEXT DEFAULT 'CC'"),
        ('ventas', 'direccion_cliente', 'TEXT'),
        ('productos', 'codigo_barras', 'TEXT'),
        ('productos', 'auditado', 'INTEGER NOT NULL DEFAULT 0'),
        ('productos', 'stock_inicial', 'INTEGER DEFAULT 0'),
        ('productos', 'fecha_auditoria', 'TEXT DEFAULT NULL'),
        ('productos', 'activo', 'INTEGER NOT NULL DEFAULT 1'),
        ('ventas', 'anulada', 'INTEGER NOT NULL DEFAULT 0'),
        ('ventas', 'motivo_anulacion', 'TEXT'),
        ('ventas', 'tipo_entrega', "TEXT NOT NULL DEFAULT 'entrega_inmediata'"),
        ('ventas', 'numero_pedido', 'INTEGER'),
        ('ventas', 'estado_despacho', "TEXT DEFAULT 'pendiente_preparar'"),
        # Trazabilidad del despacho: quién y cuándo preparó y entregó el pedido.
        ('ventas', 'despacho_preparado_por', 'TEXT'),
        ('ventas', 'despacho_preparado_fecha', 'TEXT'),
        ('ventas', 'despacho_entregado_por', 'TEXT'),
        ('ventas', 'despacho_entregado_fecha', 'TEXT'),
        ('usuarios', 'nombre_completo', 'TEXT'),
        # Acumulador de entregas por linea (para "para llevar" entregado por
        # partes). 0 = nada entregado todavia.
        ('detalle_ventas', 'cantidad_entregada', 'INTEGER NOT NULL DEFAULT 0'),
        # ── Datos fiscales DIAN del tercero (anexo técnico) ─────────────────
        # Tipo de persona: 'Natural' | 'Juridica'. Define si el NIT lleva DV.
        ('clientes', 'tipo_persona', "TEXT DEFAULT 'Natural'"),
        # Dígito de verificación del NIT (0-9), calculado con el algoritmo DIAN.
        ('clientes', 'digito_verificacion', "TEXT DEFAULT ''"),
        # Régimen fiscal: 'Responsable de IVA' | 'No Responsable de IVA'.
        ('clientes', 'regimen_fiscal', "TEXT DEFAULT 'No Responsable de IVA'"),
        # Responsabilidades DIAN separadas por coma: 'O-13,O-15,R-99-PN'.
        ('clientes', 'responsabilidades', "TEXT DEFAULT 'R-99-PN'"),
        # Códigos DANE oficiales: municipio (5 dígitos) y departamento (2).
        ('clientes', 'codigo_municipio', "TEXT DEFAULT '17665'"),
        ('clientes', 'codigo_departamento', "TEXT DEFAULT '17'"),
        # ── Unidad de medida y código DIAN en productos ─────────────────────
        # unidad_medida: código UN/ECE (ej. '94' unidad, 'KGM' kilo, 'MTR' metro).
        ('productos', 'unidad_medida', "TEXT NOT NULL DEFAULT '94'"),
        # codigo_dian: código homologado de producto/servicio que viaja al XML.
        ('productos', 'codigo_dian', 'TEXT'),
        # Naturaleza del IVA a tasa 0: 'exento' vs 'excluido' (se declaran distinto).
        # 'excluido' es la tasa 0 del artículo 424 ET (materiales de construcción de
        # extracción directa como arena y balastro); 'exento' es el artículo 422.
        ('productos', 'iva_naturaleza', "TEXT DEFAULT 'excluido'"),
        # El código de la tarifa a cero que la DIAN pide declarar (art. 424 ET):
        # '01' excluido, '02' exento, '03' no sujeto. '00' = tarifa normal
        # (gravada). Es ESTA columna la que decide si el producto lleva IVA, no
        # `iva_naturaleza`: ver la nota en `dian_emision._leer_items`.
        ('productos', 'iva_tipo_tarifa', "TEXT NOT NULL DEFAULT '00'"),
        # ── Retenciones, plazo y tipo de operación en ventas ────────────────
        ('ventas', 'retencion_fuente', 'REAL NOT NULL DEFAULT 0'),
        ('ventas', 'retencion_ica', 'REAL NOT NULL DEFAULT 0'),
        ('ventas', 'total_neto', 'REAL NOT NULL DEFAULT 0'),
        ('ventas', 'plazo_dias', 'INTEGER NOT NULL DEFAULT 0'),
        ('ventas', 'fecha_vencimiento', 'TEXT'),
        # Tipo de operación DIAN: '10' estándar, '11' AIU, '12' transporte, etc.
        ('ventas', 'tipo_operacion', "TEXT NOT NULL DEFAULT '10'"),
        # ── Documento Equivalente Electrónico POS (Res. 000165, Anexo 1.0) ──
        # `numero_dian` es el consecutivo fiscal del POS ("POS-1234"), que puede
        # no coincidir con el id interno de la venta y es el que va al XML.
        ('ventas', 'numero_dian', 'TEXT'),
        # Estado fiscal dentro de la venta, desnormalizado a propósito: la
        # pantalla de facturas lista cientos de ventas y no debe hacer JOIN con
        # la tabla de documentos solo para pintar un badge.
        ('ventas', 'dian_estado', "TEXT NOT NULL DEFAULT 'sin_emitir'"),
        # Tipo de documento que eligió el cajero al cobrar:
        #   'POS' → Documento Equivalente Electrónico (mostrador, consumidor final)
        #   'FV'  → Factura Electrónica de Venta (empresa / maestro de obra)
        # Decide la SERIE de numeración (prefijo, resolución y rango) y el
        # InvoiceTypeCode del XML. Las ventas antiguas no lo traían y son POS.
        ('ventas', 'tipo_documento_dian', "TEXT NOT NULL DEFAULT 'POS'"),
        ('ventas', 'dian_cuide', 'TEXT'),
        ('ventas', 'dian_descripcion', 'TEXT'),
        ('ventas', 'dian_fecha_emision', 'TEXT'),
        # Notas internas de la venta: estado de formaletas, entregas parciales,
        # acuerdos con el cliente, etc. No viaja al XML (allí van los conceptos
        # DIAN), es el respaldo interno del mostrador y de la facturación.
        ('ventas', 'observaciones', 'TEXT'),
    ):
        migrar_columna(cursor, tabla, columna, definicion)

    # Base inicial de caja para el módulo de cierre diario.
    migrar_columna(cursor, 'cierres_caja', 'base_inicial', 'REAL NOT NULL DEFAULT 0')

    # ── Datos fiscales del EMISOR y numeración/resolución DIAN ──────────────
    # Viven en `configuracion` (fila única) para poder cambiarlos sin tocar código.
    for columna, definicion in (
        ('digito_verificacion', "TEXT DEFAULT ''"),
        ('regimen_fiscal', "TEXT DEFAULT 'Responsable de IVA'"),
        ('responsabilidades', "TEXT DEFAULT 'O-13'"),
        # Ubicacion del emisor: la ferreteria esta en Samana, Caldas. Se deja
        # fijada para que no salga vacia en el XML (un municipio vacio lo
        # rechaza la DIAN). Ver CODIGO_MUNICIPIO_DEFECTO en blueprints/catalogo.
        ('codigo_municipio', "TEXT NOT NULL DEFAULT '17665'"),
        ('codigo_departamento', "TEXT NOT NULL DEFAULT '17'"),
        ('email_emisor', "TEXT DEFAULT ''"),
        ('numero_resolucion', "TEXT DEFAULT ''"),
        ('prefijo', "TEXT DEFAULT ''"),
        ('rango_desde', 'INTEGER NOT NULL DEFAULT 1'),
        ('rango_hasta', 'INTEGER NOT NULL DEFAULT 0'),
        ('fecha_vencimiento_resolucion', 'TEXT'),
        ('clave_tecnica', "TEXT DEFAULT ''"),
        ('software_id', "TEXT DEFAULT ''"),
        ('software_pin', "TEXT DEFAULT ''"),
        # Tolerancia de redondeo aceptada por el anexo técnico (en pesos).
        ('tolerancia_redondeo', 'REAL NOT NULL DEFAULT 1.0'),
        # ── Documento Equivalente Electrónico POS (DIAN, Anexo Técnico 1.0) ──
        # Ambiente de destino: 1 = Producción, 2 = Habilitación (set de pruebas).
        ('dian_ambiente', "TEXT NOT NULL DEFAULT '2'"),
        # Prefijo del consecutivo del POS. La resolución autoriza un rango; el
        # número que viaja al XML es prefijo + consecutivo (ej. 'POS-1042').
        ('dian_prefijo', "TEXT DEFAULT 'POS'"),
        ('dian_consecutivo', 'INTEGER NOT NULL DEFAULT 1'),
        # TestSetId que entrega la DIAN al solicitar el set de pruebas.
        ('dian_test_set_id', "TEXT DEFAULT ''"),
        # Ruta del certificado .p12/.pfx y su clave (se guardan en la fila única
        # de configuracion porque el POS es un equipo de mostrador, no un
        # servidor: no hay gestor de secretos disponible).
        ('certificado_ruta', "TEXT DEFAULT ''"),
        ('certificado_clave', "TEXT DEFAULT ''"),
        # SoftwareSecurityCode (PIN) que la DIAN entrega al registrar el software
        # en el catálogo del facturador.
        #
        # OJO: antes existía también la columna `software_pin`, que guardaba LO
        # MISMO (el PIN) en otro sitio. Dos columnas para un dato es una fuente
        # de inconsistencias: se escribía una y se leía la otra. El PIN real vive
        # en `dian_software_security_code`; `software_pin` se conserva por
        # compatibilidad con instalaciones antiguas y se normaliza en la
        # migración de abajo, pero NINGÚN código debe leerla.
        ('dian_software_security_code', "TEXT DEFAULT ''"),
        # ── Identidad del PROVEEDOR de software (el desarrollador, no la
        # ferretería). El anexo técnico exige identificarla en el nodo
        # SoftwareProvider/SoftwareProviderID del XML. Antes esos datos eran
        # constantes en el código y además llevaban el nombre de la ferretería,
        # no el del desarrollador. Ahora son datos de la instalación, editables
        # desde Configuración > Facturación DIAN sin tocar el fuente.
        ('software_proveedor_nit', "TEXT DEFAULT ''"),
        ('software_proveedor_nombre', "TEXT DEFAULT ''"),
        # ── Personalización de marca ──
        # El logo se guarda como ARCHIVO en disco (no en la base: son bytes, y
        # meterlos en SQLite engorda la base y no se sirve igual de rápido).
        # En la base queda solo la ruta, y una copia del anterior en
        # `negocio_logo_anterior` para poder deshacer: subir un logo equivocado
        # y quedarse sin ninguno sería un callejón sin salida.
        ('negocio_logo', "TEXT DEFAULT ''"),
        ('negocio_logo_anterior', "TEXT DEFAULT ''"),
        # Nombre comercial, que puede diferir de la razón social (que es lo que
        # va al XML y lo que exige la DIAN).
        ('negocio_nombre_comercial', "TEXT DEFAULT ''"),
        # Mensaje al pie del ticket: garantía, políticas, thanking. Es texto
        # libre y multilínea.
        ('negocio_mensaje_pie', "TEXT DEFAULT ''"),
        # Tamaño y posición del logo en el ticket térmico. Un logo de 500px en
        # una impresora de 58mm ocupa media página, así que el dueño necesita
        # poder ajustarlo sin que el programador toque el CSS.
        ('negocio_logo_tamano', 'INTEGER NOT NULL DEFAULT 96'),
        ('negocio_logo_mostrar', 'INTEGER NOT NULL DEFAULT 1'),
        # Color principal de la interfaz. Hex con almohadilla o sin ella.
        ('negocio_color_primario', "TEXT DEFAULT '#1F4E79'"),
        # Modo de emisión por defecto: 'habilitacion' o 'produccion'.
        ('dian_modo', "TEXT NOT NULL DEFAULT 'habilitacion'"),
        # Última verificación de conectividad con la DIAN (para la contingencia).
        ('dian_ultima_conexion', 'TEXT'),
        # Intentos máximos de la cola antes de dejar el trabajo en 'fallido'.
        ('dian_max_intentos', 'INTEGER NOT NULL DEFAULT 8'),
        # ¿Emitir el Documento Equivalente POS AUTOMÁTICAMENTE al registrar la
        # venta? Se activa cuando el negocio ya está en producción con la DIAN.
        # Apagado (0), la emisión es manual desde la factura, que es lo correcto
        # durante la habilitación y mientras se configura el software.
        ('dian_emision_automatica', 'INTEGER NOT NULL DEFAULT 0'),
    ):
        migrar_columna(cursor, 'configuracion', columna, definicion)

    # ═════════════════════════════════════════════════════
    # NORMALIZACIÓN DEL PIN (software_pin -> dian_software_security_code)
    # ═════════════════════════════════════════════════════
    # Antes había dos columnas para el mismo dato. Si una instalación antigua
    # cargó el PIN en `software_pin` y no en `dian_software_security_code`, el
    # PIN se perdía en silencio: `estado_dian` decía "listo" pero la firma salía
    # sin SoftwareSecurityCode y la DIAN rechazaba el documento. Se copia el valor
    # viejo al nuevo solo si el nuevo está vacío, y nunca al revés: el destino
    # es el que manda y `software_pin` queda como lectura DEPRECADA.
    columnas_config = {r[1] for r in cursor.execute(
        'PRAGMA table_info(configuracion)')}
    if ('software_pin' in columnas_config
            and 'dian_software_security_code' in columnas_config):
        cursor.execute('''
            UPDATE configuracion
               SET dian_software_security_code = software_pin
             WHERE id = 1
               AND TRIM(COALESCE(dian_software_security_code, '')) = ''
               AND TRIM(COALESCE(software_pin, '')) <> ''
        ''')

    # ═════════════════════════════════════════════════════
    # SERIES DE NUMERACIÓN DIAN (una por tipo de documento)
    # ═════════════════════════════════════════════════════
    # Antes la numeración vivía en columnas sueltas de `configuracion`
    # (dian_prefijo, dian_consecutivo, prefijo, rango_desde...). Eso obliga a
    # UN solo prefijo y UN solo consecutivo, así que no se puede tener a la vez
    # un Documento Equivalente POS y una Factura Electrónica de venta: cada una
    # necesita su propia resolución de la DIAN y su propio rango autorizado.
    #
    # `series_dian` es una fila por tipo de documento. Cuando llegue la
    # resolución real solo se editan estos datos desde el panel; el código no
    # se toca.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS series_dian (
            tipo_documento TEXT PRIMARY KEY,
            -- Prefijo autorizado, p. ej. 'POS', 'FV' o el de pruebas 'SETP990000000'.
            prefijo TEXT NOT NULL,
            -- Último consecutivo usado. El siguiente es este + 1.
            consecutivo INTEGER NOT NULL DEFAULT 1,
            -- Datos de la resolución que autoriza esa serie. Vacíos mientras se
            -- trabaja en habilitación.
            numero_resolucion TEXT DEFAULT '',
            rango_desde INTEGER NOT NULL DEFAULT 1,
            rango_hasta INTEGER NOT NULL DEFAULT 0,
            fecha_vencimiento TEXT,
            -- Clave técnica de la serie. En pruebas se usa la de habilitación.
            clave_tecnica TEXT DEFAULT '',
            -- 1 = la serie está lista para emitir; 0 = aún no se configura.
            activa INTEGER NOT NULL DEFAULT 0,
            descripcion TEXT DEFAULT '',
            actualizado TEXT
        )
    ''')

    # Series base, idempotentes. INSERT OR IGNORE no pisa lo que el usuario ya
    # haya configurado desde el panel.
    for tipo, prefijo, descripcion in (
        ('POS', 'POS', 'Documento Equivalente Electrónico POS (mostrador)'),
        ('FV', 'FV', 'Factura Electrónica de Venta (maestros de obra, empresas)'),
        ('NC', 'NC', 'Nota Crédito Electrónica'),
    ):
        cursor.execute(
            'INSERT OR IGNORE INTO series_dian (tipo_documento, prefijo, descripcion) '
            'VALUES (?, ?, ?)', (tipo, prefijo, descripcion))

    # IVA configurable desde la app (porcentaje sobre el subtotal). Se guarda en
    # configuracion para poder cambiarlo sin tocar codigo. Por defecto 19%.
    migrar_columna(cursor, 'configuracion', 'iva_porcentaje', 'REAL NOT NULL DEFAULT 19')
    migrar_columna(cursor, 'configuracion', 'iva_activo', 'INTEGER NOT NULL DEFAULT 1')

    # Desglose de cada venta: subtotal (sin IVA) e IVA aplicado. Quedan a 0 en
    # las ventas antiguas, que se hicieron sin IVA.
    migrar_columna(cursor, 'ventas', 'subtotal_venta', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'ventas', 'iva_valor', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'ventas', 'iva_porcentaje', 'REAL NOT NULL DEFAULT 0')

    # Desglose de IVA por producto. El precio_venta sigue siendo el PRECIO FINAL
    # (lo que paga el cliente, ya con IVA). precio_base es el valor sin IVA y
    # iva_valor el impuesto incluido. iv_tasa guarda el % aplicado a ese
    # producto (0 = exento). Productos antiguos: base = precio_venta, iva = 0.
    migrar_columna(cursor, 'productos', 'precio_base', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'productos', 'iva_valor', 'REAL NOT NULL DEFAULT 0')
    migrar_columna(cursor, 'productos', 'iva_tasa', 'REAL NOT NULL DEFAULT 0')

    # ═════════════════════════════════════════════════════
    #  IVA POR PRODUCTO: PENDIENTE DE CLASIFICAR
    # ═════════════════════════════════════════════════════
    # ESTE CAMPO ESTA EN 0 A PROPOSITO. Leerlo antes de entenderlo hace daño.
    #
    # Que la venta use la tasa de cada producto en vez de la tasa global del
    # negocio es lo correcto (un cemento de tasa cero no puede cobrar 19%), pero
    # en ESTE catálogo los 1461 productos tienen `iva_tasa = 0` y
    # `iva_tipo_tarifa = '01'`: la columna se creó con DEFAULT 0 y nunca se
    # migró, y el tipo '01' ("excluido", art. 424 del Estatuto Tributario) se
    # le puso a todo el catalogo por el mismo defecto.
    #
    # Con la tasa por producto, un ladrillo de $1.200 pasaria de $1.428 a
    # $1.200: la ferreteria dejaria de cobrar IVA en practicamente toda la
    # venta. Ese es el motivo de que el interruptor exista.
    #
    # Mientras el dueño NO clasifique el catalogo, este interruptor se queda en
    # 0 y todo se cobra con la tasa global, que es como funcionaba antes. En
    # cuanto se clasifique cada producto (tasa real en `iva_tasa` y
    # `iva_tipo_tarifa`), se pone en 1 desde Administración y las dos vias
    # quedan coherentes.
    #
    # NO se migra el catalogo automaticamente: decidir que productos son de tasa
    # cero es una decision fiscal del negocio, no del software. Se deja el
    # codigo listo y se espera el dato.
    migrar_columna(cursor, 'configuracion', 'iva_por_producto',
                   'INTEGER NOT NULL DEFAULT 0')

    # OJO: SQLite IGNORA el DEFAULT de ALTER TABLE ADD COLUMN. En una base que ya
    # existia cuando se agrego `cantidad_entregada`, las lineas viejas quedan en
    # NULL (no en 0). Con NULL, el pendiente se calcula como
    # max(0, cantidad - NULL) = 0, asi que el pedido nace "completo" y el
    # motocarguero no puede registrar ninguna entrega (el modal abre con los
    # inputs en max=0 y deshabilitados). Se normaliza aqui, de forma idempotente,
    # para que una base antigua quede igual que una recien creada.
    cursor.execute(
        "UPDATE detalle_ventas SET cantidad_entregada = 0 WHERE cantidad_entregada IS NULL"
    )
    if cursor.rowcount:
        print(f'[db] cantidad_entregada normalizada a 0 en {cursor.rowcount} linea(s) antigua(s)')

    for tabla in ('equipos_alquiler', 'equipos'):
        for columna, definicion in (
            ('medidas', 'TEXT'),
            ('especificaciones', 'TEXT'),
            ('cantidad_disponible', 'INTEGER NOT NULL DEFAULT 1'),
            ('cantidad_total', 'INTEGER NOT NULL DEFAULT 1'),
            ('fecha_registro', 'TEXT'),
            ('fecha_actualizacion', 'TEXT'),
        ):
            migrar_columna(cursor, tabla, columna, definicion)

    # Los productos existentes se consideran activos.
    try:
        cursor.execute("UPDATE productos SET activo = 1 WHERE activo IS NULL")
    except sqlite3.Error:
        pass
    # Productos creados ANTES del desglose de IVA: no lo traian, asi que su
    # precio_venta ES el valor base (iva 0). Se rellena una sola vez para que
    # las columnas nuevas no queden en 0 y las cuentas cuadren.
    try:
        cursor.execute("UPDATE productos SET precio_base = precio_venta "
                       "WHERE COALESCE(precio_base, 0) = 0 AND COALESCE(precio_venta, 0) > 0")
    except sqlite3.Error:
        pass

    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos (codigo_barras)")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ventas_numero_pedido ON ventas (numero_pedido) WHERE numero_pedido IS NOT NULL"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_entregas_venta ON entregas_venta (id_venta)"
    )


def _crear_tablas_provisionamiento(cursor):
    """Tablas del Panel SuperAdmin (solo en la base del desarrollador).

    Estas tablas NO se copian a las instancias de los clientes: viven en la base
    del desarrollador y describen a quién se le provisioningó el sistema. Por eso
    se crean aparte y no se siembran.

    `ferreterias` es el padrón de clientes. `estado` NO es decorativo: es lo que
    el panel usa para suspender el acceso, y se guarda con una restricción
    CHECK para que no aparezcan estados inventados por error de tipeo (un
    'suspendido ' con espacio no coincidiría con ninguna comparación y el
    cliente quedaría activo sin querer).
    """
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ferreterias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            nit TEXT DEFAULT '',
            -- Nombre del archivo .db de la instancia. Es la llave que conecta el
            -- registro con el archivo real en disco.
            archivo TEXT NOT NULL,
            -- Usuario dueño de ESA instancia (no el de este panel).
            usuario_dueno TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'activo'
                CHECK (estado IN ('activo', 'suspendido', 'cancelado')),
            -- Datos de contacto, para saber a quién escribirle.
            telefono TEXT DEFAULT '',
            email TEXT DEFAULT '',
            direccion TEXT DEFAULT '',
            -- Días que lleva suspendida, para el aviso de cobro. NULL si no.
            dias_suspendida INTEGER DEFAULT 0,
            -- Nota interna del desarrollador (pagos, pendientes, contrato).
            notas TEXT DEFAULT '',
            creado_en TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historial_provisionamiento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_ferreteria INTEGER NOT NULL,
            -- Qué pasó: creada, suspendida, reactivada, cancelada, eliminada.
            accion TEXT NOT NULL,
            detalle TEXT DEFAULT '',
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_ferreteria) REFERENCES ferreterias (id)
        )
    ''')
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ferreterias_estado "
        "ON ferreterias (estado)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_historial_ferreteria "
        "ON historial_provisionamiento (id_ferreteria, fecha)"
    )


def _sembrar_datos_por_defecto(cursor):
    """Garantiza el usuario admin y el cliente mostrador por defecto."""
    usuario_admin = cursor.execute(
        "SELECT id, clave FROM usuarios WHERE usuario = ?", ('admin',)
    ).fetchone()
    if usuario_admin is None:
        cursor.execute(
            "INSERT INTO usuarios (usuario, clave, rol) VALUES (?, ?, ?)",
            ('admin', generate_password_hash('admin123'), 'admin'),
        )
    else:
        cursor.execute(
            "UPDATE usuarios SET clave = ?, rol = 'admin' WHERE usuario = ?",
            (generate_password_hash('admin123'), 'admin'),
        )

    cursor.execute("SELECT COUNT(*) FROM clientes WHERE id = 1")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO clientes (id, nombre, cedula_nit, telefono, direccion) "
            "VALUES (1, 'Cliente Mostrador (General)', '222', '0000', 'Local')"
        )


def init_db():
    """Construye/actualiza el esquema completo de la base de datos."""
    conn = get_db()
    cursor = conn.cursor()

    _crear_tablas_base(cursor)
    _crear_tablas_operacion(cursor)
    _crear_tablas_alquiler(cursor)
    _crear_tablas_pedidos(cursor)
    _crear_tablas_cotizaciones(cursor)
    _crear_tablas_dian(cursor)
    _aplicar_migraciones(cursor)
    _sembrar_datos_por_defecto(cursor)

    crear_indices(cursor)

    # WAL: permite leer mientras se escribe una venta (mejor concurrencia).
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    try:
        cursor.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass

    conn.commit()
    conn.close()


# Cuántos respaldos de arranque se conservan. Antes no había tope: el código
# solo borraba el más reciente, así que en una máquina que reinicia seguido
# los .db se acumulaban (125 copias de la base en este repositorio). Con 5 se
# conserva un margen para recuperar un incidente sin llenar el disco.
MAX_RESPALDOS_ARRANQUE = 5


def respaldar_base_de_datos():
    '''Guarda una copia de seguridad de la base antes de tocarla.

    Se ejecuta en cada arranque, PERO solo si la base cambio desde el ultimo
    respaldo, y conserva solo el mas reciente. Antes creaba una copia nueva en
    CADA arranque sin borrar las anteriores: en un host con reinicios frecuentes
    (p. ej. Render, que duerme y despierta el servicio) los .db se acumulaban y
    terminaban llenando el disco, tras lo cual SQLite no podia escribir y todas
    las consultas devolvian 500 ("funciona un momento y deja de funcionar").

    Devuelve la ruta del respaldo, o None si no habia nada que respaldar.
    '''
    if not os.path.exists(DB_NAME):
        return None
    carpeta = os.path.dirname(DB_NAME) or '.'
    try:
        import glob
        previos = sorted(glob.glob(os.path.join(carpeta, 'ferreteria-respaldo-arranque-*.db')))
        # Antes solo se borraba el MÁS RECIENTE y se dejaba el resto. En una
        # máquina con reinicios(y cada uno con cambios) los .db viejos se
        # acumularon: 125 copias de la base en la carpeta. Ahora se conservan
        # los ultimos MAX_RESPALDOS_ARRANQUE y se borra el resto, de modo que
        # un incidente siempre tiene a donde volver.
        for viejo in previos[:-MAX_RESPALDOS_ARRANQUE]:
            try:
                os.remove(viejo)
            except OSError:
                pass
        if previos:
            ultimo = previos[-1]
            try:
                if os.path.getsize(ultimo) == os.path.getsize(DB_NAME) and \
                        os.path.getmtime(ultimo) >= os.path.getmtime(DB_NAME):
                    return None
                # La base si cambio: se reemplaza el respaldo anterior por el nuevo.
                os.remove(ultimo)
            except OSError:
                pass
        marca = datetime.now().strftime('%Y%m%d-%H%M%S')
        destino = os.path.join(carpeta, f'ferreteria-respaldo-arranque-{marca}.db')
        shutil.copy2(DB_NAME, destino)
        return destino
    except OSError:
        return None

def _contar_productos(ruta):
    """Numero de productos de una base, o None si no se puede leer."""
    try:
        conn = sqlite3.connect(ruta, timeout=10)
        try:
            return conn.execute('SELECT COUNT(*) FROM productos').fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None

def _ruta_version_disco(ruta_db):
    """Ruta del archivo que guarda la version de semilla aplicada al disco."""
    return ruta_db + '.version'


def _leer_version_disco(ruta_db):
    """Version de semilla ya aplicada en la base del disco (0 si no hay marca)."""
    try:
        with open(_ruta_version_disco(ruta_db), encoding='utf-8') as f:
            return int((f.read() or '0').strip())
    except (OSError, ValueError):
        return 0

def _escribir_version_disco(ruta_db, version):
    """Registra que la base del disco ya tiene aplicada la version dada."""
    try:
        with open(_ruta_version_disco(ruta_db), 'w', encoding='utf-8') as f:
            f.write(str(int(version)))
    except OSError:
        pass

def _semilla_es_mejor(actual, semilla):
    """True si la semilla tiene un catalogo claramente mas completo que actual.

    Se exige una diferencia amplia (1.5x) para no pisar el trabajo del usuario:
    si ya uso la base del disco y solo le falta un producto, se respeta. El caso
    tipico que SI reemplaza: el disco quedo con una base de 661 productos y la
    semilla trae 1637.
    """
    if not os.path.exists(semilla):
        return False
    n_actual = _contar_productos(actual)
    n_semilla = _contar_productos(semilla)
    # Si la semilla no se puede leer (o esta vacia), no hay nada mejor que ofrecer.
    if not n_semilla:
        return False
    # Base del disco vacia o ilegible: la semilla es claramente mejor. OJO: aqui
    # no se puede usar `if not n_actual: return False`, porque el caso 0 -> 1637
    # es justo el que hay que resolver (una base vacia dejo la app inservible).
    if not n_actual:
        return True
    return n_semilla > n_actual * 1.5

def _archivos_sqlite(ruta):
    """Devuelve las rutas de una base y sus companeros -wal / -shm."""
    return [ruta, ruta + '-wal', ruta + '-shm']


def _limpiar_archivos_sqlite(ruta):
    """Borra la base y sus -wal / -shm (se usan solo antes de re-sembrar)."""
    for objetivo in _archivos_sqlite(ruta):
        try:
            if os.path.exists(objetivo):
                os.remove(objetivo)
        except OSError:
            pass

def _base_legible(ruta):
    """True si la base SQLite abre, pasa integrity_check y trae las tablas minimas.

    Se usa como red de seguridad al arrancar: una base ilegible o a medio
    escribir hace fallar TODAS las consultas (500), asi que conviene detectarla
    aqui y regenerarla en vez de servir una app rota.
    """
    try:
        conn = sqlite3.connect(ruta, timeout=10)
        try:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                return False
            tablas = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            return {'productos', 'clientes', 'ventas', 'configuracion'} <= tablas
        finally:
            conn.close()
    except sqlite3.Error:
        return False

def asegurar_base_de_datos():
    """Prepara la base de datos efectiva, sembrándola si es necesario.

    Caso de uso (Render con disco persistente): la primera vez que arranca la
    app, el disco está vacío y `DB_NAME` apunta a él. Esta función copia la
    base "semilla" del repositorio (DB_SEMILLA) al disco para no perder el
    catálogo ya cargado. En arranques posteriores el disco ya tiene datos y no
    se sobrescribe nada.

    Devuelve True si sembró la base desde el repositorio, False si ya existía.
    """
    # Camino normal (misma ruta que la semilla): no hay nada que sembrar.
    if os.path.abspath(DB_NAME) == os.path.abspath(DB_SEMILLA):
        return False
    carpeta = os.path.dirname(DB_NAME)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)

    if os.path.exists(DB_NAME):
        # La base ya existe: se comprueba que sea LEGIBLE y consistente. Si esta
        # danada (p. ej. quedo un -wal viejo de otra base tras una restauracion
        # interrumpida), NO sirve dejarla: todas las consultas devolverian 500 y
        # la app quedaria inservible hasta limpiarla a mano. En ese caso se
        # reemplaza por la semilla del repositorio para que la app vuelva a
        # arrancar sola.
        if _base_legible(DB_NAME):
            # La base es legible, pero puede estar DESACTUALIZADA respecto al
            # repositorio. Se adopta la semilla en dos casos:
            #   1) La version de semilla del repo es MAYOR que la aplicada en el
            #      disco. Esto cubre cambios manuales del catalogo (p. ej. borrar
            #      productos que no son productos), que REDUCEN el numero de
            #      productos y por eso la heuristica de "1.5x" no detecta.
            #   2) La semilla tiene un catalogo claramente mayor (caso historico).
            version_repo = SEMILLA_VERSION
            version_disco = _leer_version_disco(DB_NAME)
            if version_repo > version_disco or _semilla_es_mejor(DB_NAME, DB_SEMILLA):
                # Se borran la base vieja y sus -wal/-shm ANTES de copiar. OJO:
                # no se puede volver a llamar a _limpiar_archivos_sqlite DESPUES
                # del copy2, porque borraria la semilla recien copiada y dejaria
                # la base en 0 bytes (era el bug: productos/clientes vacios y
                # 500 en /api/productos tras sembrar en Render).
                _limpiar_archivos_sqlite(DB_NAME)
                shutil.copy2(DB_SEMILLA, DB_NAME)
                _escribir_version_disco(DB_NAME, version_repo)
                return True
            return False
        _limpiar_archivos_sqlite(DB_NAME)
        if not os.path.exists(DB_SEMILLA):
            return False
        shutil.copy2(DB_SEMILLA, DB_NAME)
        _escribir_version_disco(DB_NAME, SEMILLA_VERSION)
        return True
    if os.path.exists(DB_SEMILLA):
        shutil.copy2(DB_SEMILLA, DB_NAME)
        # SQLite en modo WAL puede acompañarse de -wal/-shm; no se copian a
        # propósito para empezar con una base consistente.
        _escribir_version_disco(DB_NAME, SEMILLA_VERSION)
        return True

    return False
def _crear_tablas_cotizaciones(cursor):
    # Tablas del módulo de cotizaciones. Guardar una cotización NO toca stock,
    # cartera ni ventas: es solo una propuesta de precio al cliente.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            consecutivo_cotizacion TEXT NOT NULL UNIQUE,
            id_cliente INTEGER,
            nombre_cliente TEXT NOT NULL,
            cedula_nit TEXT,
            telefono TEXT,
            direccion TEXT,
            fecha TEXT NOT NULL,
            vigencia TEXT,
            observaciones TEXT,
            total REAL NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'Pendiente'
                CHECK (estado IN ('Pendiente', 'Aprobada', 'Vencida', 'Anulada')),
            usuario TEXT,
            FOREIGN KEY (id_cliente) REFERENCES clientes(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS detalle_cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cotizacion INTEGER NOT NULL,
            id_producto INTEGER,
            descripcion TEXT NOT NULL,
            cantidad REAL NOT NULL DEFAULT 1,
            precio_unitario REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (id_cotizacion) REFERENCES cotizaciones(id),
            FOREIGN KEY (id_producto) REFERENCES productos(id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cot_cliente ON cotizaciones (id_cliente)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cot_fecha ON cotizaciones (fecha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cot_consecutivo ON cotizaciones (consecutivo_cotizacion)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detcot_cotizacion ON detalle_cotizaciones (id_cotizacion)")
    # Notificaciones cuando un cliente aprueba una cotizacion desde el enlace.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notificaciones_cotizaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_cotizacion INTEGER NOT NULL,
            consecutivo TEXT NOT NULL,
            cliente TEXT,
            mensaje TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            FOREIGN KEY (id_cotizacion) REFERENCES cotizaciones(id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notcot_leida ON notificaciones_cotizaciones (leida)")


def _crear_tablas_dian(cursor):
    """Crea las tablas del Documento Equivalente Electronico POS (DIAN).

    Son dos tablas independientes y a propósito NO se toca `ventas`: si el
    sistema de facturación electrónica se daña, el POS debe seguir vendiendo. La
    emisión se cuelga de la venta por `id_venta` (1:1 en el caso normal) y todo
    el estado fiscal vive aquí, de modo que se puede reintentar o consultar sin
    tocar la venta original.

    `documentos_electronicos`
        Un registro por documento (CUIDE, número, XML, respuesta de la DIAN).
        `estado` sigue el ciclo: pendiente -> firmado -> aceptado | rechazado,
        y 'contingencia' cuando el documento se emitió sin conexión con la DIAN.
        `modo` distingue el set de pruebas ('habilitacion') de 'produccion'.

    `cola_dian`
        Cola de trabajos (patrón outbox) para todo lo que requiere red: enviar un
        documento, pedir el estado de un set de pruebas o reenviar tras una
        contingencia. El worker la procesa con backoff exponencial; así una caída
        de internet o de la DIAN no bloquea la venta en el mostrador.
    """
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documentos_electronicos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            id_venta          INTEGER NOT NULL,
            tipo_documento    TEXT NOT NULL DEFAULT 'POS',
            prefijo           TEXT NOT NULL DEFAULT '',
            numero            TEXT NOT NULL,
            cuide             TEXT NOT NULL DEFAULT '',
            fecha_generacion  TEXT NOT NULL,
            hora_generacion   TEXT NOT NULL,
            valor_total       REAL NOT NULL DEFAULT 0,
            valor_iva         REAL NOT NULL DEFAULT 0,
            valor_inc         REAL NOT NULL DEFAULT 0,
            xml               TEXT,
            xml_firmado       TEXT,
            qr_url            TEXT,
            modo              TEXT NOT NULL DEFAULT 'habilitacion',
            estado            TEXT NOT NULL DEFAULT 'pendiente',
            contingencia      INTEGER NOT NULL DEFAULT 0,
            tipo_evento       TEXT,
            descripcion_evento TEXT,
            respuesta_dian    TEXT,
            codigo_dian       TEXT,
            descripcion_dian  TEXT,
            track_id          TEXT,
            test_set_id       TEXT,
            -- ZipKey que devuelve SendTestSetAsync: es la llave para consultar
            -- el resultado del set de pruebas con GetStatusZip.
            zip_key           TEXT,
            intentos          INTEGER NOT NULL DEFAULT 0,
            ultimo_error      TEXT,
            fecha_envio       TEXT,
            fecha_respuesta   TEXT,
            UNIQUE (prefijo, numero),
            FOREIGN KEY (id_venta) REFERENCES ventas (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cola_dian (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            id_documento    INTEGER,
            operacion       TEXT NOT NULL,
            payload         TEXT,
            estado          TEXT NOT NULL DEFAULT 'pendiente',
            intentos        INTEGER NOT NULL DEFAULT 0,
            proximo_intento TEXT,
            ultimo_error    TEXT,
            creado          TEXT NOT NULL,
            actualizado     TEXT,
            FOREIGN KEY (id_documento) REFERENCES documentos_electronicos (id)
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_venta ON documentos_electronicos (id_venta)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_estado ON documentos_electronicos (estado)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_cuide ON documentos_electronicos (cuide)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_track ON documentos_electronicos (track_id)")
    # Red de seguridad para bases creadas por una versión anterior del módulo.
    migrar_columna(cursor, 'documentos_electronicos', 'zip_key', 'TEXT')
    # ── Notas crédito y documentos soporte ───────────────────────────────────
    # `documento_referido` guarda el CUIDE del documento que la nota corrige:
    # es el vínculo que exige la DIAN para que una corrección no sea una venta
    # negativa suelta. `motivo` guarda el texto libre del ajuste.
    migrar_columna(cursor, 'documentos_electronicos', 'documento_referido', 'TEXT')
    migrar_columna(cursor, 'documentos_electronicos', 'motivo', 'TEXT')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_ref ON documentos_electronicos (documento_referido)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cola_estado ON cola_dian (estado, proximo_intento)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cola_documento ON cola_dian (id_documento)")
    _asegurar_migracion_documento_soporte(cursor)


def _asegurar_migracion_documento_soporte(cursor):
    """Tablas del Documento Soporte a No Obligados a Facturar.

    `proveedores_informales`
        Persona natural que vende a la ferretería sin estar obligada a emitir
        factura electrónica (miningo del balastro, transportador, alerts de
        material). Se guardan aparte de `clientes` porque fiscalmente es otra
        cosa: el cliente COMPRA, el proveedor VENDE.

    `documentos_soporte`
        Un registro por documento soporte emitido: CUIDE, número, XML firmado,
        estado frente a la DIAN y la compra que respalda. Igual que
        `documentos_electronicos`, se guarda ANTES de enviar para poder
        reintentar sin volver a firmar.
    """
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proveedores_informales (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre            TEXT NOT NULL,
            tipo_documento    TEXT NOT NULL DEFAULT 'CC',
            numero_documento  TEXT,
            telefono          TEXT DEFAULT '',
            direccion         TEXT DEFAULT '',
            municipio         TEXT DEFAULT '',
            departamento      TEXT DEFAULT '',
            -- Qué se le compró: 'materiales' (arena, balastro), 'servicios'
            -- (transporte, maquinaria), 'alquiler' (equipos).
            tipo_suministro   TEXT NOT NULL DEFAULT 'materiales',
            activo            INTEGER NOT NULL DEFAULT 1,
            notas             TEXT DEFAULT '',
            creado            TEXT NOT NULL,
            actualizado       TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documentos_soporte (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            id_proveedor        INTEGER,
            id_compra           INTEGER,
            prefijo             TEXT NOT NULL DEFAULT 'DS',
            numero              TEXT NOT NULL,
            cuide               TEXT NOT NULL DEFAULT '',
            fecha_generacion    TEXT NOT NULL,
            hora_generacion     TEXT NOT NULL,
            -- Número de la factura de papel que trae el proveedor. No es un
            -- documento electrónico validado por la DIAN: es el soporte físico
            -- que justifica la compra.
            documento_proveedor TEXT,
            valor_total         REAL NOT NULL DEFAULT 0,
            valor_iva           REAL NOT NULL DEFAULT 0,
            valor_inc           REAL NOT NULL DEFAULT 0,
            xml                 TEXT,
            xml_firmado         TEXT,
            qr_url              TEXT,
            modo                TEXT NOT NULL DEFAULT 'habilitacion',
            estado              TEXT NOT NULL DEFAULT 'pendiente',
            contingencia        INTEGER NOT NULL DEFAULT 0,
            respuesta_dian      TEXT,
            codigo_dian         TEXT,
            descripcion_dian    TEXT,
            track_id            TEXT,
            intentos            INTEGER NOT NULL DEFAULT 0,
            ultimo_error        TEXT,
            fecha_envio         TEXT,
            fecha_respuesta     TEXT,
            UNIQUE (prefijo, numero),
            FOREIGN KEY (id_proveedor) REFERENCES proveedores_informales (id)
        )
    ''')
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_prov_activo ON proveedores_informales (activo)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_docsop_estado ON documentos_soporte (estado)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_docsop_prov ON documentos_soporte (id_proveedor)")
