"""Motor contable: generación automática de asientos por partida doble.

Este servicio es el ÚNICO punto donde se construyen asientos. Los blueprints
(`ventas.py`, `core.py`) lo llaman con un evento y un diccionario de montos; el
servicio lee `parametrizacion_contable`, resuelve las cuentas y escribe el
asiento. Así ningún handler necesita conocer el PUC.

Principios que se respetan aquí y conviene no romper:

  * PARTIDA DOBLE OBLIGATORIA. Si la suma de débitos no es igual a la de
    créditos, se lanza `ErrorContabilidad` y NO se escribe nada. Una venta con
    el asiento descuadrado se revierte completa: nunca queda un hecho económico
    registrado a medias.
  * SIN COMMIT PROPIO. `generar_asiento` trabaja sobre la conexión que le dan y
    deja el `commit` al llamador. Es lo que permite que venta + asiento sean una
    sola transacción atómica.
  * CONSECUTIVO DENTRO DE LA MISMA TRANSACCIÓN. El número se calcula con
    `MAX(numero) + 1` en la conexión del llamador; la restricción
    `UNIQUE (tipo_comprobante, numero)` es la red de seguridad frente a una
    carrera entre dos cajas.
  * SOLO CUENTAS DE MOVIMIENTO. Asentar en una cuenta agrupadora descuadra el
    balance de comprobación; se valida antes de insertar.
  * FIDELIDAD FISCAL DEL IVA. El IVA se toma de `ventas.iva_valor` (el valor
    realmente cobrado), no se recalcula desde el catálogo. El interruptor
    `iva_por_producto` está deliberadamente en 0 (ver `db.py`), así que
    recalcular daría un IVA distinto al cobrado.
"""
from datetime import datetime

from ..db import get_db


class ErrorContabilidad(Exception):
    """Error de negocio del motor contable.

    El llamador lo trata como un error de la transacción en curso: hace
    `rollback` y devuelve el mensaje al POS. No se silencia nunca, porque un
    asiento descuadrado es un dato fiscal incorrecto.
    """


# Tipos de comprobante válidos (deben coincidir con el CHECK de la tabla).
TIPOS_COMPROBANTE = ('CE', 'CI', 'CD', 'CT', 'NA')

# Etiquetas legibles, para el nombre por defecto cuando no se pasa descripción.
_NOMBRE_COMPROBANTE = {
    'CE': 'Comprobante de egreso',
    'CI': 'Comprobante de ingreso',
    'CD': 'Comprobante de diario',
    'CT': 'Comprobante de contabilidad',
    'NA': 'Nota de ajuste',
}


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _siguiente_numero(conn, tipo_comprobante):
    """Siguiente consecutivo libre para el tipo de comprobante.

    Se calcula DENTRO de la transacción del llamador. La restricción UNIQUE
    (tipo_comprobante, numero) impide una colisión si dos procesos entran a la
    vez: el segundo recibe IntegrityError y reintenta o revienta la transacción,
    que es preferible a dos asientos con el mismo número.
    """
    fila = conn.execute(
        "SELECT COALESCE(MAX(numero), 0) FROM asientos_contables "
        "WHERE tipo_comprobante = ?",
        (tipo_comprobante,),
    ).fetchone()
    return int((fila[0] if fila else 0) or 0) + 1


def _leer_reglas(conn, evento, condicion):
    """Reglas activas del evento, ordenadas de la más específica a la más general.

    El comodín '*' es una REGLA DE RESPALDO, no una regla adicional: si el
    evento tiene reglas propias para la condición pedida, el comodín no se
    mezcla (si lo hiciera, una venta en efectivo generaría la línea de caja DOS
    veces: una por 'efectivo' y otra por '*'). Solo se devuelven las reglas '*'
    cuando no existe ninguna coincidencia exacta.
    """
    # La conexión no usa row_factory, así que las filas llegan como tuplas: se
    # mapean por posición a un dict con los nombres de columna.
    columnas = ('id', 'condicion', 'cuenta_debito', 'cuenta_credito',
                'campo_monto', 'componente', 'porcentaje', 'requiere_tercero',
                'id_centro_costo', 'orden')
    select = (
        "SELECT id, condicion, cuenta_debito, cuenta_credito, campo_monto, "
        "       componente, porcentaje, requiere_tercero, id_centro_costo, orden "
        "FROM parametrizacion_contable "
        "WHERE evento = ? AND activo = 1 AND condicion = ? "
        "ORDER BY prioridad ASC, orden ASC"
    )
    filas = conn.execute(select, (evento, condicion)).fetchall()
    if not filas:
        # Sin regla exacta: se aplica la genérica del evento, si existe.
        filas = conn.execute(select, (evento, '*')).fetchall()
    return [dict(zip(columnas, f)) for f in filas]


def _resolver_monto(regla, montos):
    """Extrae el valor de la línea a partir del diccionario `montos`.

    `campo_monto` dice qué campo usar ('subtotal', 'iva', 'costo', 'monto'...).
    Si el cálculo depende del desglose de impuestos (`componente`), se resuelve
    contra ese componente. Un campo ausente vale 0: la línea no se emite (lo
    decide `generar_asiento`), que es más seguro que inventar un valor.
    """
    campo = (regla.get('campo_monto') or 'total').strip()
    componente = (regla.get('componente') or '').strip()

    if componente:
        # p. ej. componente='iva' -> montos['iva']; 'inc' -> montos['inc'].
        valor = montos.get(componente)
        if valor is None:
            # Algunos llamadores guardan el desglose como montos['impuestos'][componente].
            impuestos = montos.get('impuestos') or {}
            valor = impuestos.get(componente)
    else:
        valor = montos.get(campo)
        if valor is None and campo == 'total':
            # Compatibilidad: si no viene 'total' explícito, se intenta 'monto'.
            valor = montos.get('monto')

    try:
        valor = float(valor or 0)
    except (TypeError, ValueError):
        valor = 0.0
    return round(valor, 2)


def _validar_cuenta(conn, codigo):
    """Comprueba que la cuenta exista, esté activa y sea de movimiento."""
    fila = conn.execute(
        "SELECT maneja_movimiento, activo FROM plan_cuentas WHERE codigo = ?",
        (codigo,),
    ).fetchone()
    if not fila:
        raise ErrorContabilidad(f'La cuenta {codigo} no existe en el plan de cuentas.')
    if not fila[1]:
        raise ErrorContabilidad(f'La cuenta {codigo} está inactiva.')
    if not fila[0]:
        raise ErrorContabilidad(
            f'La cuenta {codigo} es agrupadora: no admite movimientos. '
            'Use una subcuenta o auxiliar.'
        )


def generar_asiento(conn, evento, *, origen_id=None, fecha=None, descripcion=None,
                    montos=None, condicion='*', tercero=None, id_centro_costo=None,
                    tipo_comprobante=None, usuario=None, origen=None):
    """Genera y persiste el asiento de partida doble de un evento del negocio.

    Args:
        conn: conexión SQLite abierta. NO se hace commit aquí.
        evento: 'venta', 'costo_venta', 'gasto', 'retencion_fuente', 'abono'...
        origen_id: id del hecho que lo genera (id_venta, id_gasto...).
        fecha: 'YYYY-MM-DD'. Por defecto, hoy.
        descripcion: texto del asiento.
        montos: dict con los valores disponibles. Claves habituales:
            {'total', 'subtotal', 'iva', 'inc', 'costo', 'monto', 'neto',
             'retencion_fuente', 'retencion_ica'}.
        condicion: discriminador de reglas. En ventas, la forma de pago;
            en gastos, la categoría. '*' cuando no aplica.
        tercero: dict {'tipo', 'numero', 'nombre'} de a quién afecta la línea.
        id_centro_costo: centro de costo por defecto de las líneas.
        tipo_comprobante: 'CE' | 'CI' | 'CD' | 'CT' | 'NA'. Si no se indica, se
            elige por evento (venta -> CI, gasto -> CE, resto -> CD).
        origen: etiqueta de origen ('venta', 'gasto'...). Por defecto, `evento`.
        usuario: usuario que genera el asiento.

    Returns:
        El id del asiento creado, o None si no hay ninguna regla aplicable
        (el módulo contable no está parametrizado para ese evento: no es un
        error, simplemente no se contabiliza).

    Raises:
        ErrorContabilidad: si el asiento no cuadra o una cuenta no es válida.
    """
    montos = montos or {}
    tercero = tercero or {}
    fecha = fecha or datetime.now().strftime('%Y-%m-%d')
    periodo = fecha[:7]

    if tipo_comprobante is None:
        tipo_comprobante = _comprobante_por_evento(evento)
    else:
        tipo_comprobante = str(tipo_comprobante).upper()
    if tipo_comprobante not in TIPOS_COMPROBANTE:
        raise ErrorContabilidad(f'Tipo de comprobante inválido: {tipo_comprobante}')

    # Una regla por evento Y condición puede generar varias líneas. Se recorren
    # en el orden devuelto (prioridad, especificidad, orden).
    reglas = _leer_reglas(conn, evento, condicion)
    if not reglas:
        return None

    lineas = []
    for regla in reglas:
        monto = _resolver_monto(regla, montos)
        if monto <= 0:
            # Regla sin valor: no se emite línea. Así una venta sin IVA no
            # genera un renglón de IVA por cero.
            continue

        cargo_tercero = bool(tercero) and bool(regla.get('requiere_tercero'))
        centro = id_centro_costo or regla.get('id_centro_costo')
        descripcion_linea = descripcion or _NOMBRE_COMPROBANTE.get(
            tipo_comprobante, '')

        base_gravable = 0.0
        if (regla.get('componente') or '').strip() in ('iva', 'inc'):
            # La base del impuesto acompaña a la línea del impuesto: sirve para
            # el reporte de IVA descontable/generado sin volver a calcularlo.
            try:
                base_gravable = float(montos.get('base_gravable') or 0)
            except (TypeError, ValueError):
                base_gravable = 0.0

        if regla.get('cuenta_debito'):
            lineas.append({
                'cuenta': regla['cuenta_debito'],
                'debito': monto, 'credito': 0.0,
                'base_gravable': base_gravable,
                'descripcion': descripcion_linea,
                'tercero': tercero if cargo_tercero else {},
                'id_centro_costo': centro,
            })
        if regla.get('cuenta_credito'):
            lineas.append({
                'cuenta': regla['cuenta_credito'],
                'debito': 0.0, 'credito': monto,
                'base_gravable': base_gravable,
                'descripcion': descripcion_linea,
                'tercero': tercero if cargo_tercero else {},
                'id_centro_costo': centro,
            })

    if not lineas:
        return None

    # ── Validación estricta de partida doble ────────────────────────────────
    # Se redondea a pesos ANTES de comparar (el anexo técnico usa pesos enteros;
    # comparar floats crudos produciría falsos descuadres por decimales).
    total_debito = round(sum(l['debito'] for l in lineas), 2)
    total_credito = round(sum(l['credito'] for l in lineas), 2)
    if abs(total_debito - total_credito) > 0.01:
        raise ErrorContabilidad(
            f'El asiento del evento "{evento}" no cuadra: '
            f'débitos {total_debito:.2f} vs créditos {total_credito:.2f}.'
        )

    # Todas las cuentas se validan ANTES de insertar nada.
    for linea in lineas:
        _validar_cuenta(conn, linea['cuenta'])

    numero = _siguiente_numero(conn, tipo_comprobante)
    descripcion = descripcion or _NOMBRE_COMPROBANTE.get(tipo_comprobante, 'Asiento')
    origen = origen or evento

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO asientos_contables
            (tipo_comprobante, numero, fecha, periodo, descripcion, origen,
             origen_id, estado, total_debito, total_credito, usuario, creado)
        VALUES (:tipo, :numero, :fecha, :periodo, :descripcion, :origen,
                :origen_id, 'contabilizado', :debito, :credito, :usuario, :creado)
        """,
        {
            'tipo': tipo_comprobante, 'numero': numero, 'fecha': fecha,
            'periodo': periodo, 'descripcion': descripcion, 'origen': origen,
            'origen_id': origen_id, 'debito': total_debito,
            'credito': total_credito, 'usuario': usuario or 'sistema',
            'creado': _ahora(),
        },
    )
    id_asiento = cursor.lastrowid

    for linea in lineas:
        tercero_linea = linea['tercero'] or {}
        cursor.execute(
            """
            INSERT INTO asiento_detalle
                (id_asiento, cuenta, tercero_tipo, tercero_numero, tercero_nombre,
                 id_centro_costo, descripcion, debito, credito, base_gravable)
            VALUES (:asiento, :cuenta, :tipo, :numero, :nombre,
                    :centro, :descripcion, :debito, :credito, :base)
            """,
            {
                'asiento': id_asiento, 'cuenta': linea['cuenta'],
                'tipo': tercero_linea.get('tipo'),
                'numero': tercero_linea.get('numero'),
                'nombre': tercero_linea.get('nombre'),
                'centro': linea['id_centro_costo'],
                'descripcion': linea['descripcion'],
                'debito': linea['debito'], 'credito': linea['credito'],
                'base': linea['base_gravable'],
            },
        )

    return id_asiento


def generar_asiento_gasto(conn, *, id_gasto, fecha, categoria, descripcion, monto,
                          usuario=None):
    """Atajo para el asiento de un gasto.

    La categoría se normaliza (minúsculas, sin tildes ni espacios sobrantes)
    porque `parametrizacion_contable.condicion` guarda textos simples como
    'arriendo' o 'servicios publicos'. Si la categoría no tiene regla propia,
    cae en la regla genérica '*' del evento `gasto`.

    POLÍTICA ESTRICTA: si NO existe ninguna regla aplicable (ni específica ni
    genérica), se lanza `ErrorContabilidad`. Antes devolvía None y el gasto se
    registraba SIN asiento contable en silencio: el módulo de gastos y el libro
    mayor quedaban desalineados sin que nadie se enterara (un descuadre que
    solo aparece al cerrar el mes, cuando ya es caro reconstruir qué pasó).

    Es preferible rechazar el gasto a contabilizarlo mal o a no contabilizarlo.
    """
    id_asiento = generar_asiento(
        conn, 'gasto', origen_id=id_gasto, fecha=fecha,
        descripcion=f'Gasto {categoria}: {descripcion}',
        montos={'monto': monto},
        condicion=_normalizar_condicion(categoria),
        usuario=usuario,
    )
    if id_asiento is None:
        raise ErrorContabilidad(
            f'No existe parametrización contable aplicable para la categoría de '
            f'gasto "{categoria}". Configure una regla para esa categoría o una '
            f'regla genérica (*) del evento "gasto" antes de registrar el gasto.'
        )
    return id_asiento


def _comprobante_por_evento(evento):
    """Tipo de comprobante por defecto según la naturaleza del hecho.

    Ingresos -> CI; egresos -> CE; el resto (ajustes, costos, abonos) -> CD.
    """
    if evento in ('venta', 'abono'):
        return 'CI'
    if evento in ('gasto', 'compra'):
        return 'CE'
    return 'CD'


def _normalizar_condicion(texto):
    """Normaliza una categoría para compararla con la parametrización.

    'Servicios Públicos' -> 'servicios publicos'. Se quitan tildes con un mapa
    explícito (no se depende de unicodedata para que el resultado sea estable).
    """
    texto = str(texto or '').strip().lower()
    reemplazos = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ü': 'u', 'ñ': 'n',
    }
    for origen, destino in reemplazos.items():
        texto = texto.replace(origen, destino)
    return texto or '*'


def asiento_de_venta(conn, *, id_venta, fecha, total_venta, subtotal_venta,
                     iva_valor, costo_venta=0, retencion_fuente=0, retencion_ica=0,
                     tipo_pago='efectivo', tercero=None, usuario=None):
    """Genera los asientos de una venta: ingreso/IVA y, si hay, el costo de venta.

    El IVA se toma de `iva_valor` (el cobrado en la venta), NUNCA se recalcula
    desde el catálogo: el interruptor `iva_por_producto` está en 0 y recalcular
    daría un impuesto distinto al que realmente pagó el cliente.

    Devuelve (id_asiento_venta, id_asiento_costo) — cualquiera puede ser None.
    """
    condicion = 'efectivo' if tipo_pago == 'efectivo' else 'credito'
    montos = {
        'total': total_venta,
        'subtotal': subtotal_venta,
        'iva': iva_valor,
        'inc': 0,
        'costo': costo_venta,
        'retencion_fuente': retencion_fuente,
        'retencion_ica': retencion_ica,
        'base_gravable': subtotal_venta,
        'monto': total_venta,
    }

    id_venta_asiento = generar_asiento(
        conn, 'venta', origen_id=id_venta, fecha=fecha,
        descripcion=f'Venta #{id_venta}',
        montos=montos, condicion=condicion, tercero=tercero, usuario=usuario,
    )

    id_costo_asiento = None
    if costo_venta and costo_venta > 0:
        id_costo_asiento = generar_asiento(
            conn, 'costo_venta', origen_id=id_venta, fecha=fecha,
            descripcion=f'Costo de venta #{id_venta}',
            montos=montos, usuario=usuario,
        )

    # La retención que sufre la ferretería se registra aparte: reduce lo que
    # entra de caja y deja un anticipo de impuestos a favor.
    if retencion_fuente and retencion_fuente > 0:
        generar_asiento(
            conn, 'retencion_fuente', origen_id=id_venta, fecha=fecha,
            descripcion=f'Retencion en la fuente venta #{id_venta}',
            montos=montos, tercero=tercero, usuario=usuario,
        )
    if retencion_ica and retencion_ica > 0:
        generar_asiento(
            conn, 'retencion_ica', origen_id=id_venta, fecha=fecha,
            descripcion=f'Retención de ICA venta #{id_venta}',
            montos=montos, tercero=tercero, usuario=usuario,
        )

    return id_venta_asiento, id_costo_asiento


def revertir_asientos(conn, *, origen_id, motivo, usuario=None, fecha=None):
    """Revierte los asientos de un hecho económico (patrón "asiento espejo").

    Se usa al ANULAR una venta o al emitir una Nota Crédito: en vez de borrar
    los asientos originales (borrar contabilidad es una mala práctica y deja la
    numeración con huecos), se hace lo que exige la técnica contable:

      1. Se marca cada asiento original como 'anulado' y se guarda el motivo.
         Un asiento anulado NO debe sumar a los reportes.
      2. Se emite un comprobante de reversión ('NA', Nota de Ajuste) que invierte
         cada línea: donde había un débito va un crédito y viceversa.

    Se revierte por DOCUMENTO original (venta, costo de venta, retenciones) y no
    solo el evento 'venta': si se anulara únicamente el ingreso, quedarían vivos
    el IVA, el costo de venta y las retenciones, y el libro mayor mentiría.

    Args:
        conn: conexión SQLite abierta. NO se hace commit aquí.
        origen_id: id del hecho (id_venta).
        motivo: texto de por qué se anula (queda en `anulado_motivo`).
        usuario: quién revierte.
        fecha: 'YYYY-MM-DD'. Por defecto, hoy.

    Returns:
        Lista de ids de los asientos de reversión creados (vacía si no había
        nada que revertir: anular una venta sin asiento no es un error).
    """
    fecha = fecha or datetime.now().strftime('%Y-%m-%d')
    motivo = str(motivo or '').strip() or 'Anulación'

    # Solo se revierten asientos VIVOS: uno ya anulado no se revierte dos veces
    # (una doble reversión crearía ingreso falso).
    originales = conn.execute(
        "SELECT id, origen FROM asientos_contables "
        "WHERE origen_id = ? AND estado = 'contabilizado' "
        "ORDER BY id",
        (origen_id,),
    ).fetchall()
    if not originales:
        return []

    cursor = conn.cursor()
    ids_reversion = []
    for id_original, origen_original in originales:
        # 1. Se marca el asiento original como anulado.
        cursor.execute(
            "UPDATE asientos_contables "
            "SET estado = 'anulado', anulado_motivo = ?, actualizado = ? "
            "WHERE id = ?",
            (motivo, _ahora(), id_original),
        )

        # 2. Se arma la reversión leyendo las líneas originales. El signo se
        #    invierte: débito -> crédito y crédito -> débito.
        lineas = cursor.execute(
            "SELECT cuenta, tercero_tipo, tercero_numero, tercero_nombre, "
            "       id_centro_costo, descripcion, debito, credito, base_gravable "
            "FROM asiento_detalle WHERE id_asiento = ? ORDER BY id",
            (id_original,),
        ).fetchall()
        if not lineas:
            continue

        numero = _siguiente_numero(conn, 'NA')
        total_debito = round(sum(float(l[7] or 0) for l in lineas), 2)  # crédito original
        total_credito = round(sum(float(l[6] or 0) for l in lineas), 2)  # débito original

        cursor.execute(
            "INSERT INTO asientos_contables "
            "(tipo_comprobante, numero, fecha, periodo, descripcion, origen, "
            " origen_id, estado, total_debito, total_credito, usuario, creado) "
            "VALUES ('NA', :numero, :fecha, :periodo, :descripcion, "
            "        'nota_credito', :origen_id, 'contabilizado', "
            "        :debito, :credito, :usuario, :creado)",
            {
                'numero': numero, 'fecha': fecha, 'periodo': fecha[:7],
                'descripcion': f'Reversión {origen_original} #{origen_id} — {motivo}',
                'origen_id': origen_id, 'debito': total_debito,
                'credito': total_credito, 'usuario': usuario or 'sistema',
                'creado': _ahora(),
            },
        )
        id_reversion = cursor.lastrowid
        ids_reversion.append(id_reversion)

        for (cuenta, tipo_tercero, num_tercero, nom_tercero, centro,
             descripcion, debito, credito, base_gravable) in lineas:
            cursor.execute(
                "INSERT INTO asiento_detalle "
                "(id_asiento, cuenta, tercero_tipo, tercero_numero, "
                " tercero_nombre, id_centro_costo, descripcion, debito, credito, "
                " base_gravable) "
                "VALUES (:asiento, :cuenta, :tipo, :numero, :nombre, :centro, "
                "        :descripcion, :debito, :credito, :base)",
                {
                    'asiento': id_reversion, 'cuenta': cuenta,
                    'tipo': tipo_tercero, 'numero': num_tercero,
                    'nombre': nom_tercero, 'centro': centro,
                    'descripcion': f'Reversión: {descripcion or ""}'.strip(),
                    # Inversión: el débito original pasa a crédito y viceversa.
                    'debito': float(credito or 0),
                    'credito': float(debito or 0),
                    'base': base_gravable,
                },
            )

    return ids_reversion


def cerrar_ejercicio(conn, *, desde, hasta, usuario=None, fecha=None):
    """Cierra el ejercicio: traspasa el resultado del período a patrimonio.

    QUÉ HACE Y POR QUÉ HACE FALTA
    -----------------------------
    Durante el año, los ingresos (clase 4) se acumulan en el crédito y los costos
    y gastos (clases 5, 6, 7) en el débito. Ese resultado vive en las cuentas de
    resultado, no en patrimonio. Al cerrar el ejercicio hay que dejarlas en cero y
    llevar la diferencia a `3605 Utilidad del ejercicio` (si ganó) o
    `3606 Pérdida del ejercicio` (si perdió).

    Sin este asiento, la ecuación Activo = Pasivo + Patrimonio solo cuadra si el
    reporte calcula la utilidad al vuelo (lo que hace `balance-general`). Con el
    cierre, queda formalizada en las cuentas y el balance general ya no necesita
    ese ajuste.

    CÓMO SE HACE (asiento espejo de las cuentas de resultado)
    ---------------------------------------------------------
    Cada cuenta de resultado se lleva a cero invirtiendo su saldo:
      - Ingresos (saldo crédito) -> se DEBITAN por su saldo acumulado.
      - Costos y gastos (saldo débito) -> se ACREDITAN por su saldo acumulado.
    La diferencia (ingresos - costos y gastos) va al patrimonio: crédito en 3605
    si es utilidad, débito en 3606 si es pérdida. Con eso el asiento cuadra
    siempre, y el resultado queda limpio.

    IDEMPOTENCIA: se niega a cerrar dos veces el mismo rango. Cerrar dos veces
    dejaría las cuentas de resultado en negativo y duplicaría la utilidad en
    patrimonio.

    Args:
        conn: conexión SQLite abierta. NO se hace commit aquí.
        desde, hasta: rango 'YYYY-MM-DD' del ejercicio a cerrar.
        usuario: quién cierra.
        fecha: fecha del asiento de cierre. Por defecto, `hasta`.

    Returns:
        dict con id_asiento, utilidad y el total traspasado, o None si no hay
        nada que cerrar (no hubo movimientos de resultado en el período).

    Raises:
        ErrorContabilidad: si el ejercicio ya fue cerrado, o si falta alguna de
        las cuentas de resultado/cierre en el plan de cuentas.
    """
    fecha = fecha or hasta

    # ── Idempotencia ────────────────────────────────────────────────────────
    # El cierre se identifica por su origen propio ('cierre_ejercicio'), no por
    # la descripción: apoyarse en un texto es frágil (basta retocarlo para que
    # el sistema crea que el ejercicio no se cerró y lo cierre dos veces).
    ya = conn.execute(
        "SELECT id FROM asientos_contables "
        "WHERE origen = 'cierre_ejercicio' AND origen_id = ? "
        "  AND estado <> 'anulado' LIMIT 1",
        (int(hasta[:4]),),
    ).fetchone()
    if ya:
        raise ErrorContabilidad(
            f'El ejercicio de {hasta[:4]} ya fue cerrado (asiento #{ya[0]}). '
            'Para volver a cerrarlo hay que anular el asiento de cierre primero.'
        )

    # ── Saldos de las cuentas de resultado ─────────────────────────────────
    filas = conn.execute(
        """
        SELECT d.cuenta, p.nombre, p.naturaleza,
               ROUND(SUM(d.debito), 2)  AS debitos,
               ROUND(SUM(d.credito), 2) AS creditos
        FROM asiento_detalle d
        JOIN asientos_contables a ON a.id = d.id_asiento
        JOIN plan_cuentas p ON p.codigo = d.cuenta
        WHERE a.estado IN ('contabilizado', 'anulado')
          AND a.fecha BETWEEN ? AND ?
          AND p.clase IN ('4', '5', '6', '7')
        GROUP BY d.cuenta
        HAVING ABS(SUM(d.debito) - SUM(d.credito)) > 0.009
        ORDER BY d.cuenta
        """,
        (desde, hasta),
    ).fetchall()
    if not filas:
        return None

    lineas = []
    utilidad = 0.0
    for cuenta, nombre, naturaleza, debitos, creditos in filas:
        debitos = float(debitos or 0)
        creditos = float(creditos or 0)
        if naturaleza == 'credito':
            saldo = round(creditos - debitos, 2)   # ingresos
            utilidad += saldo
            # Se cierra DEBITANDO la cuenta de ingreso (su saldo es crédito).
            lineas.append({'cuenta': cuenta, 'debito': saldo, 'credito': 0.0,
                           'descripcion': f'Cierre {nombre}'})
        else:
            saldo = round(debitos - creditos, 2)   # costos y gastos
            utilidad -= saldo
            # Se cierra ACREDITANDO la cuenta de costo/gasto (su saldo es débito).
            lineas.append({'cuenta': cuenta, 'debito': 0.0, 'credito': saldo,
                           'descripcion': f'Cierre {nombre}'})

    utilidad = round(utilidad, 2)
    if abs(utilidad) < 0.01:
        # Ingresos == costos y gastos: se cierran las cuentas pero no hay nada
        # que llevar a patrimonio. El asiento cuadra igual.
        pass
    elif utilidad > 0:
        lineas.append({'cuenta': '3605', 'debito': 0.0, 'credito': utilidad,
                       'descripcion': 'Utilidad del ejercicio'})
    else:
        lineas.append({'cuenta': '3606', 'debito': abs(utilidad), 'credito': 0.0,
                       'descripcion': 'Pérdida del ejercicio'})

    total_debito = round(sum(l['debito'] for l in lineas), 2)
    total_credito = round(sum(l['credito'] for l in lineas), 2)
    if abs(total_debito - total_credito) > 0.01:
        raise ErrorContabilidad(
            f'El asiento de cierre no cuadra: débitos {total_debito:.2f} vs '
            f'créditos {total_credito:.2f}.'
        )

    # Todas las cuentas se validan ANTES de insertar (incluida 3605/3606).
    for linea in lineas:
        _validar_cuenta(conn, linea['cuenta'])

    numero = _siguiente_numero(conn, 'CT')
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO asientos_contables
            (tipo_comprobante, numero, fecha, periodo, descripcion, origen,
             origen_id, estado, total_debito, total_credito, usuario, creado)
        VALUES ('CT', :numero, :fecha, :periodo, :descripcion, 'cierre_ejercicio',
                :origen_id, 'contabilizado', :debito, :credito, :usuario, :creado)
        """,
        {
            'numero': numero, 'fecha': fecha, 'periodo': fecha[:7],
            'descripcion': f'Cierre de ejercicio {hasta[:4]}',
            'origen_id': int(hasta[:4]),
            'debito': total_debito, 'credito': total_credito,
            'usuario': usuario or 'sistema', 'creado': _ahora(),
        },
    )
    id_asiento = cursor.lastrowid

    for linea in lineas:
        cursor.execute(
            "INSERT INTO asiento_detalle "
            "(id_asiento, cuenta, descripcion, debito, credito) "
            "VALUES (?, ?, ?, ?, ?)",
            (id_asiento, linea['cuenta'], linea['descripcion'],
             linea['debito'], linea['credito']),
        )

    return {'id_asiento': id_asiento, 'numero': numero,
            'utilidad': utilidad, 'total': total_debito}


def modulo_activo(conn=None):
    """True si hay plan de cuentas y al menos una regla activa.

    Sirve para que los hooks puedan verificar rápido si el módulo está
    parametrizado antes de intentar contabilizar (y no fallar en instalaciones
    donde el dueño aún no lo configuró).
    """
    propio = conn is None
    if propio:
        conn = get_db()
    try:
        cuentas = conn.execute(
            "SELECT COUNT(*) FROM plan_cuentas WHERE activo = 1").fetchone()[0]
        reglas = conn.execute(
            "SELECT COUNT(*) FROM parametrizacion_contable WHERE activo = 1").fetchone()[0]
        return bool(cuentas) and bool(reglas)
    except Exception:
        return False
    finally:
        if propio:
            conn.close()
