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
from datetime import datetime

from .provisionamiento import (
    ErrorProvisionamiento,
    provisionar,
    verificar_instancia,
)

# Estados en los que el cliente PUEDE operar.
ESTADOS_OPERATIVOS = ('activo',)
# Estados en los que NO puede.
ESTADOS_BLOQUEADOS = ('suspendido', 'cancelado')

_ESTADOS_VALIDOS = ESTADOS_OPERATIVOS + ESTADOS_BLOQUEADOS


class ErrorPadron(Exception):
    """Operación inválida sobre el padrón de ferreterías."""


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
    """Nombres de las columnas de `ferreterias`, en orden de definición."""
    global _COLUMNAS
    if _COLUMNAS is None:
        _COLUMNAS = (
            'id', 'nombre', 'nit', 'archivo', 'usuario_dueno', 'estado',
            'telefono', 'email', 'direccion', 'dias_suspendida', 'notas',
            'creado_en', 'actualizado_en',
        )
    return _COLUMNAS


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
    registro['puede_operar'] = (
        registro['estado'] in ESTADOS_OPERATIVOS and registro['archivo_existe']
    )
    return registro


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
        registro['puede_operar'] = (
            registro['estado'] in ESTADOS_OPERATIVOS
            and registro['archivo_existe']
        )
        registros.append(registro)

    # Los que no pueden operar, primero.
    registros.sort(key=lambda r: (r['puede_operar'], r['nombre'] or ''))
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

    return {
        'total': total,
        'por_estado': por_estado,
        'operativos': sum(1 for r in listar(conn, directorio) if r['puede_operar']),
        'huerfanos': huerfanos,
        'sin_archivo': sin_archivo,
        # Si esto no está vacío hay que revisarlo a mano: son clientes activos
        # que no pueden entrar porque su archivo no está.
        'requiere_atencion': len(sin_archivo) + len(huerfanos),
    }