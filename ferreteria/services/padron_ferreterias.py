"""Padrón de ferreterías: quién tiene el sistema y en qué estado.

Este módulo conecta DOS cosas que viven separadas:

  - La fila en `ferreterias` (esta base, la del desarrollador).
  - El archivo `.db` de la instancia (el disco de la ferretería).

Regla que atraviesa todo el archivo: el registro es la FUENTE sobre el estado
(activo/suspendido), pero el archivo es la fuente sobre los datos. Si uno de los
dos falta, no se inventa nada:

  - Hay registro pero no archivo: el cliente quedó a medio provisionar. Se dice.
  - Hay archivo pero no registro: hay una instancia huérfana. Se reporta, pero NO
    se adopta automáticamente, porque no se sabe de quién es ni si ya está
    facturando.

Nunca se borra un archivo de instancia desde acá sin confirmación explícita:
puede tener ventas reales y documentos ya emitidos.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

from .provisionamiento import (
    ErrorProvisionamiento,
    provisionar,
    verificar_instancia,
)

# Estados en los que el cliente PUEDE operar.
ESTADOS_OPERATIVOS = ('activo', 'prorrogado')
# Estados en los que NO puede.
ESTADOS_BLOQUEADOS = ('suspendido', 'cancelado')

_ESTADOS_VALIDOS = ESTADOS_OPERATIVOS + ESTADOS_BLOQUEADOS

# Plan de cobro. El nombre dice cada cuánto vence la licencia.
PLANES = ('mensual', 'anual')
DIAS_POR_PLAN = {'mensual': 30, 'anual': 365}

# Prórroga por defecto tras el vencimiento, en días.
DIAS_PRORROGA_DEFECTO = 15

# Formato de fecha de vencimiento: solo `YYYY-MM-DD`. Aceptar otros formatos
# haría que dos registros con la misma fecha se compararan distinto.
FORMATO_FECHA = '%Y-%m-%d'


class ErrorPadron(Exception):
    """Operación inválida sobre el padrón de ferreterías."""


def _hoy():
    return datetime.now().date()


def _parsear_fecha(texto):
    """Fecha a `date`, o None si viene vacía o mal formada.

    Devuelve None en vez de lanzar porque un vencimiento mal escrito no puede
    impedir que se abra el panel del desarrollador: se muestra como "sin fecha"
    y es él quien lo corrige.
    """
    if not texto:
        return None
    try:
        return datetime.strptime(str(texto).strip()[:10], FORMATO_FECHA).date()
    except ValueError:
        return None


def _formatear_fecha(fecha):
    return fecha.strftime(FORMATO_FECHA) if fecha else ''


def dias_para_vencer(fecha_vencimiento):
    """Días que faltan. Negativo si ya venció.

    Returns:
        int o None si no hay fecha configurada.
    """
    fecha = _parsear_fecha(fecha_vencimiento)
    if fecha is None:
        return None
    return (fecha - _hoy()).days


def estado_por_fecha(fecha_vencimiento, dias_prorroga=DIAS_PRORROGA_DEFECTO,
                     estado_actual='activo'):
    """Qué estado LE TOCARÍA por la fecha, sin tocar la base.

    Es una función PURA a propósito: el panel la usa para mostrar "va a vencer
    en 5 días" sin que eso cambie nada por sí solo. El cambio de estado solo
    ocurre cuando el desarrollador lo pide o cuando se ejecuta
    `revisar_vencimientos`.

    Regla:
        - sin fecha, o vigente          -> el estado que ya tenía
        - vencida y dentro de prórroga  -> 'prorrogado'
        - vencida y prórroga agotada    -> 'suspendido'

    Un cliente 'suspendido' o 'cancelado' NO vuelve a activo por el paso del
    tiempo: la suspensión es una decisión del desarrollador, no un efecto del
    reloj. Si solo se tratara de una fecha vencida, `suspension_automatica`
    avisaría y listo.
    """
    if estado_actual in ESTADOS_BLOQUEADOS:
        return estado_actual

    dias = dias_para_vencer(fecha_vencimiento)
    if dias is None or dias >= 0:
        return estado_actual

    try:
        gracia = int(dias_prorroga)
    except (TypeError, ValueError):
        gracia = DIAS_PRORROGA_DEFECTO
    gracia = max(gracia, 0)

    # Negativo: ya venció. `dias` es -1 el día después del vencimiento.
    if (-dias) <= gracia:
        return 'prorrogado'
    return 'suspendido'


def renovacion_sugerida(plan, desde=None):
    """Fecha de vencimiento para un plan nuevo o una renovación.

    Args:
        plan: 'mensual' o 'anual'.
        desde: fecha base. Por defecto hoy.
    """
    if plan not in PLANES:
        raise ErrorPadron(
            f'Plan desconocido: "{plan}". Use uno de: {", ".join(PLANES)}.'
        )
    base = _parsear_fecha(desde) if desde else _hoy()
    if base is None:
        base = _hoy()
    return _formatear_fecha(base + timedelta(days=DIAS_POR_PLAN[plan]))


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _registrar_historial(cursor, id_ferreteria, accion, detalle=''):
    cursor.execute(
        'INSERT INTO historial_provisionamiento '
        '(id_ferreteria, accion, detalle, fecha) VALUES (?, ?, ?, ?)',
        (id_ferreteria, accion, detalle, _ahora()),
    )


def _existe(conn, id_ferreteria):
    fila = conn.execute(
        'SELECT id, nombre, archivo, usuario_dueno, estado FROM ferreterias '
        'WHERE id = ?', (id_ferreteria,)
    ).fetchone()
    if fila is None:
        raise ErrorPadron(
            f'No existe una ferretería con el id {id_ferreteria}.'
        )
    return fila


def crear_ferreteria(conn, directorio, nombre, usuario_dueno, clave_dueno,
                     nit='', digito_verificacion='', direccion='', telefono='',
                     email='', notas=''):
    """Provisiona la instancia y la registra en el padrón.

    El orden importa: primero se crea el archivo y después se registra. Si el
    registro fallara, queda una base huérfana que `listar_huerfanos` reporta;
    al revés, quedaría un registro apuntando a un archivo inexistente, que es
    peor, porque el panel mostraría un cliente "activo" al que no se puede
    entrar.

    Returns:
        El registro creado (mismas claves que devuelve `listar`).
    """
    try:
        resultado = provisionar(
            directorio=directorio,
            nombre_negocio=nombre,
            usuario_dueno=usuario_dueno,
            clave_dueno=clave_dueno,
            nit=nit,
            digito_verificacion=digito_verificacion,
            direccion=direccion,
            telefono=telefono,
            email=email,
        )
    except ErrorProvisionamiento as error:
        raise ErrorPadron(str(error)) from error

    ahora = _ahora()
    cursor = conn.cursor()
    try:
        cursor.execute(
            '''
            INSERT INTO ferreterias
                (nombre, nit, archivo, usuario_dueno, estado, telefono, email,
                 direccion, notas, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, 'activo', ?, ?, ?, ?, ?, ?)
            ''',
            (nombre, nit, resultado['archivo'], usuario_dueno.strip(),
             telefono, email, direccion, notas, ahora, ahora),
        )
        id_ferreteria = cursor.lastrowid
        _registrar_historial(cursor, id_ferreteria, 'creada',
                             f'Archivo: {resultado["archivo"]}')
        conn.commit()
    except Exception as error:
        conn.rollback()
        raise ErrorPadron(
            f'Se creó el archivo {resultado["archivo"]} pero no se pudo '
            f'registrar la ferretería: {error}. La instancia existe en disco; '
            'elimínala o regístrala a mano para no dejarla huérfana.'
        ) from error

    registro = obtener(conn, id_ferreteria, directorio)
    registro['avisos'] = resultado.get('avisos', [])
    return registro


def _como_dict(fila):
    """Convierte una fila en diccionario venga como venga.

    `conn.row_factory = sqlite3.Row` es lo normal en esta app, pero no está
    garantizado: si quien llama abre la conexión sin configurarlo, `dict(fila)`
    falla con "object is not iterable" y el error no dice nada del padrón.
    Aquí se cubre el caso plano (tupla) sin depender del row_factory de la
    conexión.
    """
    if fila is None:
        return None
    if isinstance(fila, sqlite3.Row):
        return dict(fila)
    return dict(zip(_columnas_padron(), fila))


_COLUMNAS = None


def _columnas_padron():
    """Nombres de las columnas de `ferreterias`, en orden de definición.

    Tiene que coincidir con el `CREATE TABLE` de `db._crear_tablas_provisionamiento`.
    Si se agrega una columna ahí y no se agrega acá, `dict(zip(...))` desalinea
    TODO lo que venga después: el `estado` de un cliente acabaría siendo su
    `telefono`, y el panel mentiría sin dar ningún error.
    """
    global _COLUMNAS
    if _COLUMNAS is None:
        _COLUMNAS = (
            'id', 'nombre', 'nit', 'archivo', 'usuario_dueno', 'estado',
            'telefono', 'email', 'direccion', 'dias_suspendida', 'notas',
            'creado_en', 'actualizado_en',
            'plan', 'fecha_vencimiento', 'dias_prorroga', 'desde_prorroga',
        )
    return _COLUMNAS


def revisar_vencimientos(conn, aplicar=True):
    """Revisa los vencimientos y cambia los estados que correspondan.

    QUÉ HACE
    --------
    Para cada ferretería compara su `fecha_vencimiento` con hoy y aplica la
    regla: vencida y dentro de prórroga -> 'prorrogado'; vencida y prórroga
    agotada -> 'suspendido'.

    POR QUÉ NO SE CORRE EN CADA ARRANQUE NI EN CADA VENTA
    ---------------------------------------------------
    - En el arranque sería inútil: la fecha solo cambia cuando pasa el día, y el
      servidor puede estar apagado ese día.
    - En medio de una venta sería peligroso: una venta a medio registrar que
      cambia de estado por una fecha es una venta perdida en una ferretería que
      estaba pagando. Por eso NO se toca el POS.

    Por eso `aplicar` es un parámetro explícito: el panel puede llamar esta
    función para mostrar qué pasaría (`aplicar=False`, solo simulación) sin tocar
    nada, y aplicar el cambio cuando el desarrollador lo decide.

    EXCEPCIÓN INTENCIONAL
    ---------------------
    Un cliente 'suspendido' por decisión MANUAL no vuelve a activo porque su
    fecha se renueve. La suspensión es una decisión; el reloj solo agrava.
    Ver `estado_por_fecha`.

    Returns:
        Lista de cambios: [{id, nombre, de, a, dias}].
    """
    filas = conn.execute(
        'SELECT id, nombre, estado, fecha_vencimiento, dias_prorroga '
        'FROM ferreterias'
    ).fetchall()

    cambios = []
    cursor = conn.cursor()
    for fila in filas:
        # Se leen por posición: esta consulta trae 5 columnas y la lista
        # completa (17) haría que `_como_dict` alineara mal.
        id_ = fila[0]
        nombre = fila[1]
        estado = fila[2]
        vencimiento = fila[3]
        try:
            dias_prorroga = int(fila[4]) if fila[4] is not None \
                else DIAS_PRORROGA_DEFECTO
        except (TypeError, ValueError):
            dias_prorroga = DIAS_PRORROGA_DEFECTO

        nuevo = estado_por_fecha(vencimiento, dias_prorroga, estado)
        if nuevo == estado:
            continue

        dias = dias_para_vencer(vencimiento)
        cambios.append({
            'id': id_, 'nombre': nombre, 'de': estado, 'a': nuevo,
            'dias': dias,
        })

        if aplicar:
            ahora = _ahora()
            if nuevo == 'prorrogado':
                cursor.execute(
                    'UPDATE ferreterias SET estado = ?, desde_prorroga = ?, '
                    'actualizado_en = ? WHERE id = ?',
                    (nuevo, ahora, ahora, id_),
                )
            else:
                cursor.execute(
                    'UPDATE ferreterias SET estado = ?, desde_prorroga = NULL, '
                    'actualizado_en = ? WHERE id = ?',
                    (nuevo, ahora, id_),
                )
            _registrar_historial(
                cursor, id_, nuevo,
                f'Automatico por vencimiento (vencio hace '
                f'{abs(dias or 0)} dia(s))',
            )

    if aplicar and cambios:
        conn.commit()
    return cambios


def suspendidos_pendientes(conn):
    """Clientes cuyo estado NO coincide con lo que dice su fecha.

    No escribe nada. Sirve para que el panel diga "3 clientes deberian estar
    suspendidos" sin que eso ocurra solo por abrir la pantalla.
    """
    pendientes = []
    for fila in conn.execute(
        'SELECT id, nombre, estado, fecha_vencimiento, dias_prorroga '
        'FROM ferreterias'
    ).fetchall():
        try:
            dias_prorroga = int(fila[4]) if fila[4] is not None \
                else DIAS_PRORROGA_DEFECTO
        except (TypeError, ValueError):
            dias_prorroga = DIAS_PRORROGA_DEFECTO
        esperado = estado_por_fecha(fila[3], dias_prorroga, fila[2])
        if esperado != fila[2]:
            pendientes.append({
                'id': fila[0], 'nombre': fila[1], 'estado': fila[2],
                'deberia_ser': esperado,
            })
    return pendientes


def configurar_pago(conn, id_ferreteria, plan=None, fecha_vencimiento=None,
                    dias_prorroga=None, aplicar_estado=True):
    """Actualiza plan, vencimiento y días de prórroga de una ferretería.

    Con `fecha_vencimiento=None` se deja la fecha COMO ESTÁ. Poner '' la borra.
    Esa distinción importa: un cliente al que nunca se le puso fecha no debe
    aparecer como "sin fecha" solo porque alguien guardó el plan.

    `aplicar_estado` recalcula el estado según la fecha nueva. Con False, solo
    guarda los datos y deja el estado como está (útil para dar más días de
    prórroga sin tocar el estado manualmente).

    Returns:
        El registro de pago actualizado.
    """
    _existe(conn, id_ferreteria)
    cursor = conn.cursor()

    valores = {}
    if plan is not None:
        if plan not in PLANES:
            raise ErrorPadron(
                f'Plan desconocido: "{plan}". Use uno de: {", ".join(PLANES)}.'
            )
        valores['plan'] = plan
    if fecha_vencimiento is not None:
        if str(fecha_vencimiento).strip():
            # Se valida el formato antes de guardar: una fecha mal escrita
            # hace que el cliente nunca aparezca como vencido.
            if _parsear_fecha(fecha_vencimiento) is None:
                raise ErrorPadron(
                    f'Fecha de vencimiento inválida: "{fecha_vencimiento}". '
                    'Use el formato AAAA-MM-DD.'
                )
            valores['fecha_vencimiento'] = str(fecha_vencimiento).strip()[:10]
        else:
            valores['fecha_vencimiento'] = ''
    if dias_prorroga is not None:
        try:
            dias = int(dias_prorroga)
        except (TypeError, ValueError):
            raise ErrorPadron('Los días de prórroga deben ser un número.')
        if dias < 0:
            raise ErrorPadron('Los días de prórroga no pueden ser negativos.')
        valores['dias_prorroga'] = dias

    if not valores:
        raise ErrorPadron('No se recibió ningún dato de pago para guardar.')

    asignaciones = ', '.join(f'{c} = ?' for c in valores)
    cursor.execute(
        f'UPDATE ferreterias SET {asignaciones}, actualizado_en = ? WHERE id = ?',
        list(valores.values()) + [_ahora(), id_ferreteria],
    )
    _registrar_historial(
        cursor, id_ferreteria, 'pago',
        ', '.join(f'{k}={v}' for k, v in valores.items()),
    )
    conn.commit()

    if aplicar_estado:
        revisar_vencimientos(conn, aplicar=True)

    return obtener_pago(conn, id_ferreteria)


def obtener_pago(conn, id_ferreteria):
    """Solo los datos de pago de una ferretería, sin tocar el disco."""
    fila = conn.execute(
        'SELECT id, nombre, estado, plan, fecha_vencimiento, dias_prorroga, '
        'desde_prorroga FROM ferreterias WHERE id = ?', (id_ferreteria,)
    ).fetchone()
    if fila is None:
        raise ErrorPadron(f'No existe una ferretería con el id {id_ferreteria}.')

    registro = {
        'id': fila[0], 'nombre': fila[1], 'estado': fila[2],
        'plan': fila[3] or 'mensual', 'fecha_vencimiento': fila[4] or '',
        'dias_prorroga': fila[5] if fila[5] is not None else DIAS_PRORROGA_DEFECTO,
        'desde_prorroga': fila[6] or '',
    }
    registro['dias_para_vencer'] = dias_para_vencer(registro['fecha_vencimiento'])
    registro['suspension_automatica'] = estado_por_fecha(
        registro['fecha_vencimiento'], registro['dias_prorroga'],
        registro['estado'],
    )
    return registro


def _marcar_pago(registro):
    """Agrega al registro los datos de pago ya calculados.

    Se separa del `SELECT` porque los cálculos (días restantes, estado que
    tocaría) no son una consulta: son lógica. Mezclarlos haría que el listado
    devuelva lo que la base tiene y lo que el calendario dice, sin que se
    pueda distinguir uno de lo otro.
    """
    registro = dict(registro)
    dias = dias_para_vencer(registro.get('fecha_vencimiento'))
    registro['dias_para_vencer'] = dias
    registro['plan'] = registro.get('plan') or 'mensual'
    try:
        registro['dias_prorroga'] = int(
            registro.get('dias_prorroga') or DIAS_PRORROGA_DEFECTO)
    except (TypeError, ValueError):
        registro['dias_prorroga'] = DIAS_PRORROGA_DEFECTO

    # Cuánto le queda de prórroga, si ya venció.
    if dias is not None and dias < 0:
        registro['dias_restantes_prorroga'] = (
            registro['dias_prorroga'] - (-dias))
    else:
        registro['dias_restantes_prorroga'] = None

    registro['suspension_automatica'] = estado_por_fecha(
        registro.get('fecha_vencimiento'),
        registro['dias_prorroga'],
        registro.get('estado', 'activo'),
    )
    # `puede_operar` tiene en cuenta lo que DICHA la base, no lo que la fecha
    # sugiere: el panel muestra la diferencia, y el cambio real solo ocurre
    # cuando el desarrollador lo acepta o se ejecuta `revisar_vencimientos`.
    registro['puede_operar'] = (
        registro.get('estado') in ESTADOS_OPERATIVOS
        and registro.get('archivo_existe')
    )
    return registro


def obtener(conn, id_ferreteria, directorio):
    """Un registro del padrón, con el estado REAL del archivo en disco."""
    fila = conn.execute(
        'SELECT * FROM ferreterias WHERE id = ?', (id_ferreteria,)
    ).fetchone()
    if fila is None:
        raise ErrorPadron(
            f'No existe una ferretería con el id {id_ferreteria}.'
        )

    registro = _como_dict(fila)
    ruta = os.path.join(directorio, registro['archivo'])
    registro['ruta'] = ruta
    registro['archivo_existe'] = os.path.exists(ruta)
    return _marcar_pago(registro)


def listar(conn, directorio, estado=None):
    """Todos los clientes, con el estado real de cada archivo.

    Ordena primero los que están mal: los que no pueden operar aparecen arriba,
    porque son los que requieren una acción del desarrollador. Un padrón donde
    todo se ve bien esconde justo lo que hay que arreglar.
    """
    if estado is not None and estado not in _ESTADOS_VALIDOS:
        raise ErrorPadron(
            f'Estado desconocido: "{estado}". Use uno de: {", ".join(_ESTADOS_VALIDOS)}.'
        )

    if estado:
        filas = conn.execute(
            'SELECT * FROM ferreterias WHERE estado = ? ORDER BY nombre',
            (estado,)
        ).fetchall()
    else:
        filas = conn.execute('SELECT * FROM ferreterias ORDER BY nombre').fetchall()

    registros = []
    for fila in filas:
        registro = _como_dict(fila)
        ruta = os.path.join(directorio, registro['archivo'])
        registro['ruta'] = ruta
        registro['archivo_existe'] = os.path.exists(ruta)
        registros.append(_marcar_pago(registro))

    # Primero lo que requiere atención: los que no pueden operar y los que
    # están por vencerse. Un listado donde todo parece bien esconde lo que hay
    # que hacer hoy.
    def _prioridad(r):
        if not r['puede_operar']:
            return (0, '')
        dias = r['dias_para_vencer']
        if dias is not None and dias <= 7:
            return (1, f'{r["nombre"]}')
        return (2, r['nombre'] or '')

    registros.sort(key=_prioridad)
    return registros


def cambiar_estado(conn, id_ferreteria, estado, directorio, notas=''):
    """Activa, suspende o cancela el acceso de una ferretería.

    Notes:
        NO toca el archivo de la instancia ni sus datos. Suspender es un acto
        administrativo: la base sigue ahí, intacta, con sus ventas. Eso permite
        reactivar sin pérdida, y es la razón de que suspender sea reversible.
    """
    if estado not in _ESTADOS_VALIDOS:
        raise ErrorPadron(
            f'Estado desconocido: "{estado}". Use uno de: {", ".join(_ESTADOS_VALIDOS)}.'
        )

    fila = _existe(conn, id_ferreteria)
    if fila[4] == estado:
        raise ErrorPadron(f'La ferretería "{fila[1]}" ya está en estado "{estado}".')

    ahora = _ahora()
    # Al suspender se cuentan los días desde la última actualización. Al
    # reactivar el contador se reinicia a 0 (ver la línea siguiente), de modo que
    # `dias_suspendida` es "días de este periodo", no un acumulado.
    dias = _dias_desde(fila) if estado == 'suspendido' else 0

    cursor = conn.cursor()
    cursor.execute(
        'UPDATE ferreterias SET estado = ?, dias_suspendida = ?, notas = ?, '
        'actualizado_en = ? WHERE id = ?',
        (estado, dias if estado == 'suspendido' else 0, notas, ahora,
         id_ferreteria),
    )
    _registrar_historial(cursor, id_ferreteria, estado, notas)
    conn.commit()

    return obtener(conn, id_ferreteria, directorio)


def _dias_desde(fila):
    """Días transcurridos desde la última actualización."""
    try:
        desde = datetime.strptime(fila['actualizado_en'], '%Y-%m-%d %H:%M:%S')
        return max((datetime.now() - desde).days, 0)
    except (KeyError, TypeError, ValueError):
        return 0


def editar(conn, id_ferreteria, **campos):
    """Edita los datos de contacto y las notas del registro.

    Solo los campos de contacto. NO se permite cambiar `archivo` ni
    `usuario_dueno` desde acá: el archivo lo define el provisionamiento y
    cambiarlo a mano dejaría el registro apuntando a otra base.
    """
    _existe(conn, id_ferreteria)

    permitidos = {
        'nombre', 'nit', 'telefono', 'email', 'direccion', 'notas',
    }
    recibidos = {k: v for k, v in campos.items() if k in permitidos}
    if not recibidos:
        raise ErrorPadron(
            f'No hay nada editable. Campos permitidos: {", ".join(sorted(permitidos))}.'
        )

    asignaciones = ', '.join(f'{campo} = ?' for campo in recibidos)
    cursor = conn.cursor()
    cursor.execute(
        f'UPDATE ferreterias SET {asignaciones}, actualizado_en = ? WHERE id = ?',
        list(recibidos.values()) + [_ahora(), id_ferreteria],
    )
    _registrar_historial(
        cursor, id_ferreteria, 'editada',
        ', '.join(sorted(recibidos)),
    )
    conn.commit()


def eliminar_registro(conn, id_ferreteria):
    """Quita el REGISTRO del padrón. NO borra el archivo de la instancia.

    Es a propósito. La base de la ferretería puede tener ventas reales y
    documentos ya emitidos ante la DIAN: borrarla desde un panel es una
    operación irreversible que no corresponde a una pantalla web. Si de verdad se quiere eliminar el archivo, se borra en el sistema de archivos,
        a mano y con respaldo.
    """
    fila = _existe(conn, id_ferreteria)
    conn.execute('DELETE FROM historial_provisionamiento WHERE id_ferreteria = ?',
                 (id_ferreteria,))
    conn.execute('DELETE FROM ferreterias WHERE id = ?', (id_ferreteria,))
    conn.commit()
    return {
        'eliminado': fila[1],
        'archivo_conservado': fila[2],
        'mensaje': (
            f'Se quitó "{fila[1]}" del padrón. El archivo {fila[2]} se dejó '
            'intacto en disco, a propósito.'
        ),
    }


def listar_huerfanos(conn, directorio):
    """Archivos .db de instancia que NO están registrados en el padrón.

    Sirve para encontrar lo que se provisionó a medias o lo que se copió a
    mano. Solo se REPORTAN: no se adoptan ni se borran, porque no hay forma
    automática de saber a quién pertenecen ni si ya están facturando.
    """
    registrados = {
        r[0] for r in conn.execute('SELECT archivo FROM ferreterias').fetchall()
    }
    huerfanos = []
    if os.path.isdir(directorio):
        for nombre in sorted(os.listdir(directorio)):
            if not nombre.endswith('.db') or nombre in registrados:
                continue
            ruta = os.path.join(directorio, nombre)
            if not os.path.isfile(ruta):
                continue
            # La base de trabajo del desarrollador no es una instancia.
            if nombre in ('ferreteria.db', 'ferreteria-semilla.db'):
                continue
            huerfanos.append({
                'archivo': nombre,
                'ruta': ruta,
                'tamano_kb': round(os.path.getsize(ruta) / 1024, 1),
            })
    return huerfanos


def verificar(conn, id_ferreteria, directorio):
    """Comprueba que la instancia esté sana y devuelve qué le falta configurar."""
    registro = obtener(conn, id_ferreteria, directorio)
    if not registro['archivo_existe']:
        raise ErrorPadron(
            f'El archivo {registro["archivo"]} no está en {directorio}. La '
            'ferretería figura en el padrón pero su base no está.'
        )
    try:
        resultado = verificar_instancia(registro['ruta'])
    except ErrorProvisionamiento as error:
        raise ErrorPadron(str(error)) from error

    resultado['ferreteria'] = registro['nombre']
    resultado['estado_licencia'] = registro['estado']
    resultado['puede_operar'] = registro['puede_operar']
    return resultado


def historial(conn, id_ferreteria):
    _existe(conn, id_ferreteria)
    filas = conn.execute(
        'SELECT accion, detalle, fecha FROM historial_provisionamiento '
        'WHERE id_ferreteria = ? ORDER BY fecha DESC, id DESC',
        (id_ferreteria,),
    ).fetchall()
    return [{'accion': f[0], 'detalle': f[1] or '', 'fecha': f[2]} for f in filas]


def resumen(conn, directorio):
    """Cifras del padrón para la cabecera del panel."""
    total = conn.execute('SELECT COUNT(*) FROM ferreterias').fetchone()[0]
    por_estado = {
        estado: 0 for estado in _ESTADOS_VALIDOS
    }
    for estado, cantidad in conn.execute(
        'SELECT estado, COUNT(*) FROM ferreterias GROUP BY estado'
    ).fetchall():
        por_estado[estado] = cantidad

    huerfanos = listar_huerfanos(conn, directorio)
    sin_archivo = [
        r['archivo'] for r in listar(conn, directorio)
        if not r['archivo_existe']
    ]
    pendientes = suspendidos_pendientes(conn)

    return {
        'total': total,
        'por_estado': por_estado,
        'operativos': sum(1 for r in listar(conn, directorio) if r['puede_operar']),
        'huerfanos': huerfanos,
        'sin_archivo': sin_archivo,
        # Clientes cuyo estado no coincide con su fecha. NO se corrigen solos:
        # se listan y el desarrollador decide.
        'vencimientos_pendientes': pendientes,
        # Si esto no está vacío hay que revisarlo a mano: son clientes activos
        # que no pueden entrar porque su archivo no está, o que deberían estar
        # suspendidos y siguen operando.
        'requiere_atencion': len(sin_archivo) + len(huerfanos) + len(pendientes),
    }