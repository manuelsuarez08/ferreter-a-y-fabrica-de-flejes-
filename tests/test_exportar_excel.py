"""Pruebas de la protección del Excel de auditoría del catálogo.

El daño que estas pruebas evitan: el dueño marca durante horas, alguien corre
el exportador "para ver el dato actualizado", y el archivo se regenera vacío.
Las decisiones no están en ningún respaldo: se pierden sin dejar rastro.

Cubren:
  1. Detectar marcas en la columna APLICAR.
  2. Que NO se sobrescriba un archivo marcado.
  3. Que sí se sobrescriba uno vacío (el caso normal de uso).
  4. Que --forzar lo permita, avisando de lo que se pierde.
  5. La columna APLICAR se busca por NOMBRE, no por posición.
  6. Aviso cuando hay marcas en una hoja que --aplicar no lee.
"""
from __future__ import annotations

import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'herramientas'))

import openpyxl  # noqa: E402

import exportar_excel_catalogo as exportador  # noqa: E402

VERDADEROS = ('SI', 'SÍ', 'S', 'YES', '1', 'X')


def _excel_con_marcas(ruta, marcas, hoja='TODOS', mover_columna=False):
    """Crea un Excel mínimo con la columna APLICAR y las marcas indicadas.

    `marcas` es una lista de (numero_de_fila, valor). Las filas van desde la 2.
    """
    libro = openpyxl.Workbook()
    hoja_excel = libro.active
    hoja_excel.title = hoja
    hoja_excel.append(['Id BD', 'Nombre del producto', 'APLICAR'])
    for indice in range(2, 12):
        hoja_excel.append([indice, f'PRODUCTO {indice}', ''])
    for fila, valor in marcas:
        hoja_excel.cell(row=fila, column=3).value = valor

    if mover_columna:
        # Simula que alguien insertó una columna antes de APLICAR. El código la
        # tiene que encontrar por nombre, no asumir que es la tercera.
        hoja_excel.insert_cols(2)
        hoja_excel.cell(row=1, column=2).value = 'Columna insertada'
        for fila in range(2, 12):
            hoja_excel.cell(row=fila, column=2).value = 'x'
    libro.save(ruta)
    return ruta


# ═══════════════════════════════════════════
# 1. Detección
# ═══════════════════════════════════════════

def test_un_archivo_inexistente_no_tiene_que_ver(tmp_path):
    info = exportador.revisar_existente(str(tmp_path / 'no_existe.xlsx'))

    assert info['existe'] is False
    assert info['con_marcas'] is False


def test_un_excel_sin_marcas_no_dispara_la_proteccion(tmp_path):
    """El caso normal: se regenera sin problema."""
    ruta = _excel_con_marcas(str(tmp_path / 'a.xlsx'), [])

    info = exportador.revisar_existente(ruta)

    assert info['con_marcas'] is False
    assert info['con_si'] == 0


def test_detecta_las_filas_marcadas_como_si(tmp_path):
    ruta = _excel_con_marcas(str(tmp_path / 'b.xlsx'),
                             [(2, 'SI'), (3, 'SI'), (4, 'NO')])

    info = exportador.revisar_existente(ruta)

    assert info['con_marcas'] is True
    assert info['con_si'] == 2, 'Solo las marcadas SI cuentan como decisiones'


@pytest.mark.parametrize('valor', VERDADEROS)
def test_acepta_todas_las_formas_de_escribir_si(tmp_path, valor):
    """La gente escribe SI, Sí, S, X... y todo vale igual."""
    ruta = _excel_con_marcas(str(tmp_path / f'c_{valor}.xlsx'),
                             [(2, valor)])

    assert exportador.revisar_existente(ruta)['con_si'] == 1


def test_una_hoja_sin_columna_aplicar_no_rompe(tmp_path):
    """Un Excel raro no debe hacer que la herramienta lance una excepción."""
    ruta = str(tmp_path / 'd.xlsx')
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = 'TODOS'
    hoja.append(['Id BD', 'Nombre del producto'])
    hoja.append([1, 'PRODUCTO'])
    libro.save(ruta)

    info = exportador.revisar_existente(ruta)

    assert info['con_marcas'] is False


def test_un_archivo_corrupto_no_hace_explotar(tmp_path):
    """Peor caso: un .xlsx que no se puede abrir. Debe avisar, no morir."""
    ruta = str(tmp_path / 'roto.xlsx')
    with open(ruta, 'wb') as f:
        f.write(b'esto no es un Excel')

    info = exportador.revisar_existente(ruta)

    assert info['existe'] is True
    assert info['advertencias'], 'Debe avisar que no pudo leerlo'


# ═══════════════════════════════════════════
# 2. La columna se busca por nombre
# ═══════════════════════════════════════════

def test_encuentra_aplicar_tras_insertar_una_columna(tmp_path):
    """El dueño puede insertar columnas en Excel. La posición no es fija.

    Si el código leyera 'columna 3' sin más, con una columna insertada leería el
    valor equivocado y creería que no hay ninguna decisión marcada.
    """
    ruta = _excel_con_marcas(str(tmp_path / 'e.xlsx'),
                             [(2, 'SI'), (3, 'SI')], mover_columna=True)

    info = exportador.revisar_existente(ruta)

    assert info['con_marcas'] is True
    assert info['con_si'] == 2


# ═══════════════════════════════════════════
# 3. La trampa de las hojas que --aplicar no lee
# ═══════════════════════════════════════════

def test_no_avisa_sobre_todos_porque_ya_se_consolida(tmp_path):
    """Antes avisaba que --aplicar solo leía TODOS. Ahora consolida, así que no.

    Este test fija el CAMBIO de comportamiento: el aviso era el síntoma de un
    problema que se resolvió en el lector. Si vuelve a aparecer, significa que
    alguien revirtió la consolidación.
    """
    ruta = _excel_con_marcas(str(tmp_path / 'f.xlsx'), [(2, 'SI')],
                             hoja='POR REVISAR')

    info = exportador.revisar_existente(ruta)

    assert not any('TODOS' in a for a in info['advertencias']), \
        'Ya no aplica el aviso: --aplicar lee todas las hojas'
    # Pero SÍ debe seguir detectando que hay trabajo que no se puede pisar.
    assert info['con_marcas'] is True


def test_no_avisa_si_las_marcas_estan_en_todos(tmp_path):
    """Si marqué donde toca, no hay nada que avisar."""
    ruta = _excel_con_marcas(str(tmp_path / 'g.xlsx'), [(2, 'SI')],
                             hoja='TODOS')

    info = exportador.revisar_existente(ruta)

    assert not info['advertencias']


# ═══════════════════════════════════════════
# 4. El comportamiento del main
# ═══════════════════════════════════════════

@pytest.fixture
def catalogo_real(tmp_path, monkeypatch):
    """Catálogo de verdad, con productos activos, y SALIDA en un temporal.

    Se parte de la SEMILLA y no de una tabla armada a mano: el auditor consulta
    muchas columnas (dimensiones, unidad_medida_descripcion, iva_tasa...) y una
    tabla minima de productos se rompe al momento. Además asi la prueba corre
    contra el esquema real, que es lo que importa.
    """
    import shutil
    import sqlite3

    ruta = str(tmp_path / 'catalogo.db')
    shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), ruta)

    # Se dejan activos algunos productos para que el reporte tenga contenido.
    conn = sqlite3.connect(ruta)
    conn.execute('UPDATE productos SET activo = 1 WHERE id <= 20')
    conn.commit()
    conn.close()

    monkeypatch.setattr(exportador.auditoria, 'DB', ruta)
    monkeypatch.setattr(exportador, 'SALIDA', str(tmp_path / 'auditoria_catalogo.xlsx'))
    return tmp_path


def test_no_sobreescribe_un_archivo_con_marcas(catalogo_real, capsys):
    """Este es EL comportamiento que importa: no perder trabajo de nadie."""
    ruta = str(catalogo_real / 'auditoria_catalogo.xlsx')
    _excel_con_marcas(ruta, [(2, 'SI'), (3, 'SI'), (4, 'SI')])

    with pytest.raises(SystemExit) as salida:
        exportador.main()

    assert salida.value.code == 1
    texto = capsys.readouterr().out
    assert 'NO SE GENERO EL ARCHIVO' in texto
    assert '3' in texto, 'Debe decir cuántas marcas se perderían'

    # Y, sobre todo: el archivo sigue ahí con sus marcas.
    info = exportador.revisar_existente(ruta)
    assert info['con_si'] == 3


def test_si_sobreescribe_un_archivo_sin_marcas(catalogo_real):
    """Regenerar un Excel vacío es el uso normal y debe funcionar."""
    ruta = str(catalogo_real / 'auditoria_catalogo.xlsx')
    _excel_con_marcas(ruta, [])

    exportador.main()

    assert os.path.exists(ruta)
    libro = openpyxl.load_workbook(ruta)
    assert 'TODOS' in libro.sheetnames


def test_forzar_permite_sobreescribir_aviso(catalogo_real, capsys):
    """--forzar existe, pero deja constancia de lo que se perdió."""
    ruta = str(catalogo_real / 'auditoria_catalogo.xlsx')
    _excel_con_marcas(ruta, [(2, 'SI'), (3, 'SI')])

    exportador.main(forzar=True)

    texto = capsys.readouterr().out
    assert 'sobrescrito' in texto
    assert '2' in texto


def test_salida_alternativa_no_toca_el_original(catalogo_real):
    """Se puede generar el Excel en otro lado sin arriesgar el que tiene trabajo."""
    original = str(catalogo_real / 'auditoria_catalogo.xlsx')
    _excel_con_marcas(original, [(2, 'SI')])
    otro = str(catalogo_real / 'otro.xlsx')

    exportador.main(salida=otro)

    assert os.path.exists(otro)
    # El original sigue intacto.
    assert exportador.revisar_existente(original)['con_si'] == 1


def test_el_excel_generado_no_trae_marcas_de_ejemplo(catalogo_real):
    """El archivo nuevo arranca sin decisiones: la revisión empieza en cero.

    Si el exportador pusiera 'SI' de ejemplo, el dueño podría aplicar sin
    haber mirado nada.
    """
    exportador.main()

    libro = openpyxl.load_workbook(catalogo_real / 'auditoria_catalogo.xlsx')
    hoja = libro['TODOS']
    columna = exportador._columna_aplicar(hoja)

    for indice in range(2, hoja.max_row + 1):
        valor = hoja.cell(row=indice, column=columna + 1).value
        assert valor in (None, ''), f'Fila {indice} trae {valor!r}'


def test_el_nombre_alternativo_es_libre(tmp_path):
    existente = str(tmp_path / 'auditoria_catalogo.xlsx')
    _excel_con_marcas(existente, [])
    primero = exportador._nombre_alternativo(existente)
    _excel_con_marcas(primero, [])
    segundo = exportador._nombre_alternativo(existente)

    assert primero != existente
    assert segundo != existente
    assert segundo.endswith('.xlsx')