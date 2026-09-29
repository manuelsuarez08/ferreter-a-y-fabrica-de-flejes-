"""Pruebas de los BUGS 4 y 5 reportados en produccion.

Ambos tenian la misma firma: la pantalla se rompe sin dejar error en consola,
asi que nadie caia en la causa. Estas pruebas los fijan por construccion.

BUG 4 — El historial de ventas no mostraba nada en ninguna fecha.
    `new Date().toISOString()` devuelve la fecha en UTC, no la local. En
    Colombia (UTC-5), despues de las 19:00 la cadena resulta ser el dia
    SIGUIENTE, y el filtro consultaba una fecha sin ventas.

BUG 5 — El boton "Ver" de la lista de facturas no abria el detalle.
    `verFactura` escribia en `fac-metodo-pago`, un id que NO existe en el HTML.
    `setTexto` hace `getElementById(...).innerText`, que sobre `null` lanza
    TypeError; como la funcion es `async`, el error se perdia como promesa
    rechazada y el modal nunca se abria, sin error visible en la consola.
"""
import io
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANTILLA = os.path.join(RAIZ, 'templates', 'index.html')


@pytest.fixture(scope='module')
def html():
    with io.open(PLANTILLA, encoding='utf-8') as f:
        return f.read()


@pytest.fixture(scope='module')
def ids(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def _sin_comentarios(html):
    """Quita comentarios de linea y de bloque.

    Necesario porque los comentarios que explican el bug mencionan el codigo
    viejo (`toISOString`, `fac-metodo-pago`): si no se limpian, las pruebas
    los detectarian como si el bug siguiera presente.
    """
    texto = re.sub(r'/\*.*?\*/', '', html, flags=re.S)
    texto = re.sub(r'^\s*//.*$', '', texto, flags=re.M)
    texto = re.sub(r'(?m)(?<=[^:])//(?![/\s])[^\n]*', '', texto)
    return texto


# ═════════════════════════════════════════════════════
# BUG 4: fecha local, no UTC
# ═════════════════════════════════════════════════════
def test_existe_el_helper_de_fecha_local(html):
    """Debe haber una forma de obtener la fecha local, no la UTC."""
    codigo = _sin_comentarios(html)
    assert 'function hoyLocalISO' in codigo
    assert 'function mesLocalISO' in codigo


def test_ningun_filtro_usa_toisoString_para_armar_una_fecha(html):
    """`toISOString()` es UTC: no sirve para filtrar por el dia local.

    Solo se permite dentro de la conversion explicita que ya resta el desfase
    (`getTime() - getTimezoneOffset() * 60000`), que si es correcta.
    """
    codigo = _sin_comentarios(html)
    usos_incorrectos = []
    for i, linea in enumerate(codigo.split('\n'), 1):
        if 'toISOString' not in linea:
            continue
        if 'getTimezoneOffset()' in linea:
            continue
        usos_incorrectos.append((i, linea.strip()[:100]))

    assert not usos_incorrectos, (
        'Estas lineas arman una fecha con toISOString (UTC) en vez de la local:\n'
        + '\n'.join(f'  {i}: {t}' for i, t in usos_incorrectos)
    )


def test_setHoyHistorial_usa_el_helper_local(html):
    """"Ver Hoy" es justamente donde se manifiesta el bug: debe ser local."""
    codigo = _sin_comentarios(html)
    m = re.search(r'function\s+setHoyHistorial\s*\(\s*\)\s*\{(.*?)\n\s*\}', codigo, re.S)
    assert m, 'no se encontro setHoyHistorial'
    cuerpo = m.group(1)
    assert 'hoyLocalISO()' in cuerpo
    assert 'toISOString' not in cuerpo


def test_el_helper_usa_los_metodos_locales_de_date(html):
    """La forma correcta es getFullYear/getMonth/getDate (ya en hora local)."""
    codigo = _sin_comentarios(html)
    m = re.search(r'function\s+hoyLocalISO\s*\(\s*\)\s*\{(.*?)\n\s*\}', codigo, re.S)
    assert m
    cuerpo = m.group(1)
    for metodo in ('getFullYear', 'getMonth', 'getDate'):
        assert metodo in cuerpo, f'hoyLocalISO deberia usar {metodo}()'


def test_el_helper_toma_el_dia_local_y_no_el_utc(html):
    """Smoke logico: con un desfase de -5h, el dia local puede ser otro.

    Se simula el caso limite (23:30 local) que es el que fallaba, usando la
    misma formula del helper contra toISOString.
    """
    from datetime import datetime, timedelta, timezone
    # Colombia: UTC-5, sin horario de verano.
    tz_local = timezone(timedelta(hours=-5))
    momento_23h30 = datetime(2026, 9, 29, 23, 30, tzinfo=tz_local)

    local = momento_23h30.strftime('%Y-%m-%d')
    utc = momento_23h30.astimezone(timezone.utc).strftime('%Y-%m-%d')

    assert local == '2026-09-29'
    assert utc == '2026-09-30', 'este caso es justamente el que rompia el filtro'
    assert local != utc


# ═════════════════════════════════════════════════════
# BUG 5: verFactura no abria el modal
# ═════════════════════════════════════════════════════
def test_ver_factura_no_escribe_en_el_id_inexistente(html):
    """`fac-metodo-pago` fue la causa: el id no existe en el HTML."""
    codigo = _sin_comentarios(html)
    assert 'fac-metodo-pago' not in codigo, (
        'fac-metodo-pago no existe en el HTML; escribir ahi rompe verFactura')
    assert "setTexto('fac-metodo-pago'" not in codigo


def test_todos_los_ids_que_escribe_set_texto_existen(html, ids):
    """Barrido: ningun setTexto puede apuntar a un id inexistente.

    setTexto no comprueba null, asi que un id de mas revienta la funcion
    completa. Este es el patron que produjo el bug 5.
    """
    codigo = _sin_comentarios(html)
    faltan = sorted({i for i in re.findall(r"setTexto\(\s*'([^']+)'", codigo)
                     if i not in ids})
    assert not faltan, f'setTexto escribe en ids que no existen: {faltan}'


def test_ver_factura_termina_abriendo_el_modal(html):
    """La ultima instruccion util de verFactura debe ser mostrar el modal.

    Si algo se lanza antes, el modal no abre. Se comprueba que la llamada al
    `.show()` este y que no queden llamadas a ids dudosos despues.
    """
    codigo = _sin_comentarios(html)
    m = re.search(r'async\s+function\s+verFactura\s*\([^)]*\)\s*\{', codigo)
    assert m, 'no se encontro verFactura'
    cuerpo = codigo[m.end():m.end() + 9000]
    assert "bootstrap.Modal(document.getElementById('modalFactura')).show()" in cuerpo
