"""Series de numeración DIAN: una por tipo de documento.

La DIAN autoriza cada tipo de documento con su propia resolución y su propio
rango. Un POS de ferretería necesita en el mismo mostrador:

  - `POS` — Documento Equivalente Electrónico, para la venta de mostrador.
  - `FV`  — Factura Electrónica de Venta, cuando el comprador es un maestro de
            obra o una empresa y necesita soporte para descontar impuestos.
  - `NC`  — Nota Crédito Electrónica, que corrige cualquiera de los dos.

Antes la numeración vivía en columnas sueltas de `configuracion` y eso
permitía un solo prefijo y un solo consecutivo, con lo cual era imposible
emitir los dos tipos a la vez. Este módulo centraliza la lectura y la reserva
del consecutivo por tipo.

Todo es dato, no código: el prefijo, la resolución, el rango y la clave
técnica se editan desde el panel. Al llegar la resolución real de la DIAN solo
se cambian esos datos.
"""
from datetime import datetime

# Tipos de documento que el POS sabe emitir. La clave es la que viaja en el
# XML como InvoiceTypeCode (branch/extension del anexo técnico).
TIPOS_DOCUMENTO = ('POS', 'FV', 'NC')

# Descripciones para el panel de configuración.
DESCRIPCIONES = {
    'POS': 'Documento Equivalente Electrónico POS (mostrador)',
    'FV': 'Factura Electrónica de Venta (empresas, maestros de obra)',
    'NC': 'Nota Crédito Electrónica',
}


class ErrorSerie(Exception):
    """La serie no permite emitir: falta configurarla o se agotó el rango."""


def _ahora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def leer_series(conn):
    """Devuelve las series configuradas, indexadas por tipo de documento."""
    filas = conn.execute(
        """
        SELECT tipo_documento, prefijo, consecutivo, numero_resolucion,
               rango_desde, rango_hasta, fecha_vencimiento, clave_tecnica,
               activa, descripcion
        FROM series_dian
        ORDER BY tipo_documento
        """
    ).fetchall()
    return {
        f[0]: {
            'tipo_documento': f[0],
            'prefijo': f[1] or '',
            'consecutivo': int(f[2] or 1),
            'numero_resolucion': f[3] or '',
            'rango_desde': int(f[4] or 1),
            'rango_hasta': int(f[5] or 0),
            'fecha_vencimiento': f[6] or '',
            'clave_tecnica': f[7] or '',
            'activa': bool(f[8]),
            'descripcion': f[9] or DESCRIPCIONES.get(f[0], ''),
        }
        for f in filas
    }


def leer_serie(conn, tipo_documento):
    """Serie de un tipo, o None si no existe."""
    return leer_series(conn).get(tipo_documento)


def serie_operativa(serie):
    """¿Esta serie puede emitir ahora mismo?

    No exige resolución: en habilitación la DIAN acepta documentos sin ella
    (de ahí la clave técnica de pruebas). Lo que sí exige es un prefijo y no
    tener el rango agotado.
    """
    if not serie:
        return False, 'La serie no está configurada.'
    if not serie['prefijo']:
        return False, 'La serie no tiene prefijo.'
    hasta = serie['rango_hasta']
    if hasta and serie['consecutivo'] > hasta:
        return False, (
            f'El consecutivo {serie["consecutivo"]} superó el rango autorizado '
            f'(hasta {hasta}). Solicite una nueva resolución a la DIAN.'
        )
    return True, ''


def reservar_numero(conn, tipo_documento, estricto=False):
    """Reserva y devuelve el siguiente número fiscal de la serie.

    Returns:
        dict con `prefijo`, `numero` ('FV-15') y `consecutivo`.

    El incremento ocurre dentro de la transacción de la venta: si la venta
    falla, el número se libera; si se registra, queda consumido. La DIAN
    rechaza dos documentos con el mismo número, así que nunca se reutiliza.
    """
    serie = leer_serie(conn, tipo_documento)
    ok, motivo = serie_operativa(serie)
    if not ok:
        if estricto:
            raise ErrorSerie(motivo)
        # Sin la serie se cae al valor por defecto para no paralizar el POS en
        # un equipo que todavía no ha configurado nada.
        return {'prefijo': serie['prefijo'] if serie else tipo_documento,
                'numero': f'{tipo_documento}-{serie["consecutivo"] if serie else 1}',
                'consecutivo': serie['consecutivo'] if serie else 1}

    siguiente = serie['consecutivo']
    conn.execute(
        'UPDATE series_dian SET consecutivo = ? WHERE tipo_documento = ?',
        (siguiente + 1, tipo_documento),
    )
    return {
        'prefijo': serie['prefijo'],
        'numero': f'{serie["prefijo"]}-{siguiente}',
        'consecutivo': siguiente,
    }


def actualizar_serie(conn, tipo_documento, datos):
    """Guarda los datos de una serie desde el panel de configuración.

    Solo se escribe lo que viene informado: así el panel puede enviar el
    formulario completo sin riesgo de pisar el consecutivo.
    """
    if tipo_documento not in TIPOS_DOCUMENTO:
        raise ErrorSerie(f'Tipo de documento desconocido: {tipo_documento}')

    campos, valores = [], []
    mapeo = {
        'prefijo': ('prefijo', str),
        'numero_resolucion': ('numero_resolucion', str),
        'rango_desde': ('rango_desde', int),
        'rango_hasta': ('rango_hasta', int),
        'fecha_vencimiento': ('fecha_vencimiento', str),
        'clave_tecnica': ('clave_tecnica', str),
        'activa': ('activa', bool),
        'descripcion': ('descripcion', str),
    }
    for clave, (columna, tipo) in mapeo.items():
        if clave in datos and datos[clave] is not None:
            valor = datos[clave]
            if tipo is bool:
                valor = 1 if valor in (True, 1, '1', 'on', 'true') else 0
            elif tipo is int:
                valor = int(valor or 0)
            else:
                valor = str(valor).strip()
            campos.append(f'{columna} = ?')
            valores.append(valor)

    if 'consecutivo' in datos and datos['consecutivo'] is not None:
        campos.append('consecutivo = ?')
        valores.append(int(datos['consecutivo']))

    if not campos:
        return False

    campos.append('actualizado = ?')
    valores.append(_ahora())
    valores.append(tipo_documento)
    conn.execute(
        f'UPDATE series_dian SET {", ".join(campos)} WHERE tipo_documento = ?',
        valores,
    )
    return True


def estado_para_panel(series):
    """Resumen para pintar el panel: qué series están listas y cuál es el
    siguiente número, sin dejar que el frontend haga la aritmética."""
    filas = []
    for tipo in TIPOS_DOCUMENTO:
        s = series.get(tipo) or {
            'tipo_documento': tipo, 'prefijo': tipo, 'consecutivo': 1,
            'numero_resolucion': '', 'rango_desde': 1, 'rango_hasta': 0,
            'fecha_vencimiento': '', 'clave_tecnica': '', 'activa': False,
            'descripcion': DESCRIPCIONES.get(tipo, ''),
        }
        ok, motivo = serie_operativa(s)
        restante = 0
        if s['rango_hasta'] and s['rango_hasta'] >= s['consecutivo']:
            restante = s['rango_hasta'] - s['consecutivo'] + 1
        filas.append({
            **s,
            'operativa': ok,
            'motivo_bloqueo': motivo,
            'siguiente_numero': f'{s["prefijo"]}-{s["consecutivo"]}',
            'numeros_restantes': restante,
        })
    return filas
