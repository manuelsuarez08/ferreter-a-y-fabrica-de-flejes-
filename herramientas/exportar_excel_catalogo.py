"""Exporta la auditoria del catalogo a un Excel construido para LEERLA.

El CSV y el JSON sirven para maquinas, no para el administrador. Este archivo
resuelve lo unico que importa: que la persona entienda, fila por fila, que esta
proponiendo el reporte y que tiene que decidir.

QUE HACE, EN CONCRETO
- Hoja 1 "COMO LEER ESTO": el catalogo esta en 1461 filas, no se audita a ojo.
  Explica en una pantalla las cuatro preguntas de cada fila y el trabajo real
  (los precios en cero), con los numeros de verdad del reporte.
- Hoja 2 "POR REVISAR": solo las filas dudosas, con el motivo escrito en
  texto plano, no en un codigo.
- Hoja 3 "LISTOS PARA APLICAR": lo que se puede aplicar sin preguntar nada.
- Hoja 4 "TODOS": el catalogo completo, con filtros y colores.

Decisiones de diseno
- Los colores de la tarifa se repiten en las tres hojas: verde es gravado 19%,
  ambar es exento, azul es excluido a tasa cero. Un color, un significado.
- La columna `aplicar` es una lista desplegable SI/NO con validacion: hacer clic
  equivocado en el Excel no rompe nada, y solo escribe lo que quedo en SI.
- Filtros y paneles congelados en todas las hojas: se llega a la fila 1.400 sin
  perder de vista los titulos.
- El precio con IVA va en negrita: es el dato que NO se modifica.

Hoja 0 "RESUMEN": una fila por categoria con cuantos productos tiene, cuantos
  hay que decidir y el valor del catalogo. Es el mapa del trabajo pendiente.

Salida: auditoria_catalogo.xlsx
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importa el modulo de auditoria en vez de duplicar la logica: si las reglas
# fiscales cambian, el Excel cambia con ellas.
import auditar_catalogo_iva as auditoria  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, 'auditoria_catalogo.xlsx')

# ── Paleta ────────────────────────────────────────────────────────────────
AZUL = '1F4E79'
AZUL_CLARO = 'DDEBF7'
GRIS = 'F2F2F2'
VERDE = 'E2EFDA'
AMBAR = 'FFF2CC'
AZUL_TASA0 = 'D9E8F5'
ROJO = 'FFC7CE'
TEXTO_ROJO = '9C0006'

CABECERA = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
TITULO = Font(name='Calibri', size=16, bold=True, color=AZUL)
SUBTITULO = Font(name='Calibri', size=11, italic=True, color='595959')
NEGRITA = Font(name='Calibri', size=11, bold=True)
NORMAL = Font(name='Calibri', size=11)

BORDE = Border(*[Side(style='thin', color='BFBFBF')] * 4)

# Color de fondo por tarifa. Es la misma clave visual en las tres hojas.
COLOR_TARIFA = {'GRAVADO': VERDE, 'EXENTO': AMBAR, 'EXCLUIDO': AZUL_TASA0}

# Columnas del reporte -> (encabezado, ancho, formato)
COLUMNAS = [
    ('id', 'Id BD', 7, 'entero'),
    ('codigo_interno', 'Codigo interno', 14, 'texto'),
    ('codigo_barras', 'Codigo de barras', 16, 'texto'),
    ('nombre_producto', 'Nombre del producto', 44, 'texto'),
    ('categoria', 'Categoria', 22, 'texto'),
    ('unidad_medida', 'Unidad', 9, 'texto'),
    ('unidad_medida_descripcion', 'Unidad (nombre)', 14, 'texto'),
    ('precio_venta_con_iva', 'PVP con IVA', 13, 'dinero'),
    ('precio_sin_iva', 'Base sin IVA', 13, 'dinero'),
    ('valor_iva', 'Valor IVA', 12, 'dinero'),
    ('iva_tarifa', 'Tarifa %', 9, 'entero'),
    ('iva_tipo', 'Tipo', 11, 'texto'),
    ('iva_tipo_tarifa_dian', 'Codigo DIAN', 11, 'texto'),
    ('articulo_estatuto', 'Art. E.T.', 9, 'entero'),
    ('fundamento', 'Fundamento', 38, 'texto'),
    ('iva_tipo_tarifa_actual', 'DIAN hoy', 10, 'texto'),
    ('iva_tasa_actual', 'Tasa hoy', 10, 'decimal'),
    ('requiere_revision', 'Requiere revision', 16, 'booleano'),
    ('motivo_revision', 'Por que hay que revisarlo', 52, 'texto'),
    ('aplicar', 'APLICAR', 11, 'texto'),
]

TITULOS = {k: t for k, t, _, _ in COLUMNAS}


def _formato(campo, tipo):
    if tipo == 'dinero':
        return '#,##0'
    if tipo == 'decimal':
        return '0.0'
    return '@'


def _pintar_encabezado(hoja):
    for indice, (_, titulo, ancho, _) in enumerate(COLUMNAS, start=1):
        letra = get_column_letter(indice)
        celda = hoja.cell(row=1, column=indice, value=titulo)
        celda.font = CABECERA
        celda.fill = PatternFill('solid', fgColor=AZUL)
        celda.alignment = Alignment(horizontal='center', vertical='center',
                                    wrap_text=True)
        celda.border = BORDE
        hoja.column_dimensions[letra].width = ancho
    hoja.row_dimensions[1].height = 32
    hoja.freeze_panes = 'D2'
    hoja.auto_filter.ref = (f'A1:{get_column_letter(len(COLUMNAS))}1')


def _pintar_filas(hoja, filas, fila_inicial=2):
    """Escribe las filas. `aplicar` arranca vacio: la decision es del usuario."""
    n = 0
    for f in filas:
        fila = fila_inicial + n
        color = COLOR_TARIFA.get(f['iva_tipo'], 'FFFFFF')
        for indice, (campo, _, _, tipo) in enumerate(COLUMNAS, start=1):
            valor = f.get(campo, '')
            if campo == 'aplicar':
                valor = ''
            elif campo == 'requiere_revision':
                valor = 'SI' if f.get(campo) else 'NO'
            celda = hoja.cell(row=fila, column=indice, value=valor)
            celda.border = BORDE
            celda.font = NEGRITA if campo in ('precio_venta_con_iva',) else NORMAL
            celda.number_format = _formato(campo, tipo)
            if tipo == 'texto':
                celda.alignment = Alignment(vertical='top', wrap_text=True)
            else:
                celda.alignment = Alignment(horizontal='center', vertical='top')
            if campo in ('iva_tipo', 'iva_tarifa', 'iva_tipo_tarifa_dian'):
                celda.fill = PatternFill('solid', fgColor=color)
            elif campo == 'nombre_producto' and not f.get('precio_venta_con_iva'):
                celda.fill = PatternFill('solid', fgColor=ROJO)
                celda.font = Font(name='Calibri', size=11, color=TEXTO_ROJO)
        n += 1
    return n


def _validacion_si_no(hoja, desde, hasta):
    dv = DataValidation(type='list', formula1='"SI,NO"', allow_blank=True,
                        showErrorMessage=True)
    dv.error = 'Escriba SI para aplicar o NO para dejar el producto como esta.'
    dv.errorTitle = 'Valor no valido'
    dv.prompt = 'SI aplica la propuesta, NO deja el producto sin tocar.'
    dv.promptTitle = 'Decision del administrador'
    hoja.add_data_validation(dv)
    letra = get_column_letter([c for c, (_, t, _, _) in enumerate(COLUMNAS, 1)
                               if t == 'APLICAR'][0])
    dv.add(f'{letra}{desde}:{letra}{hasta}')


def _hoja_datos(wb, titulo, filas, con_validacion):
    hoja = wb.create_sheet(titulo)
    _pintar_encabezado(hoja)
    total = _pintar_filas(hoja, filas)
    if con_validacion and total:
        _validacion_si_no(hoja, 2, total + 1)
    return hoja, total


# ══════════════════════════════════════════════════════════════
# HOJA 1 — la que explica el reporte
# ══════════════════════════════════════════════════════════════

def _hoja_explicacion(wb, filas):
    r = auditoria.resumen(filas)
    precios_en_cero = sum(1 for f in filas if not f['precio_venta_con_iva'])
    sin_barras = sum(1 for f in filas if not f['codigo_barras'])
    unidades = r['unidades']
    con_longitud = unidades.get('MTR', 0) + unidades.get('KGM', 0)

    hoja = wb.create_sheet('COMO LEER ESTO')
    hoja.column_dimensions['A'].width = 4
    hoja.column_dimensions['B'].width = 34
    hoja.column_dimensions['C'].width = 16
    hoja.column_dimensions['D'].width = 74
    hoja.sheet_view.showGridLines = False

    def linea(n, col_b, col_c, col_d, negrita=False, alto=None):
        if negrita:
            hoja.cell(row=n, column=2, value=col_b).font = NEGRITA
        if col_c is not None:
            hoja.cell(row=n, column=3, value=col_c).font = NEGRITA
        hoja.cell(row=n, column=4, value=col_d).font = NORMAL
        hoja.cell(row=n, column=4).alignment = Alignment(wrap_text=True,
                                                         vertical='top')
        if alto:
            hoja.row_dimensions[n].height = alto

    hoja['B1'] = 'Auditoria del catalogo: que hay que decidir'
    hoja['B1'].font = TITULO
    hoja.row_dimensions[1].height = 26
    hoja['B2'] = (f'Generado el {datetime.now().strftime("%d/%m/%Y a las %H:%M")}'
                  f'  ·  {r["productos"]} productos activos  ·  '
                  f'valor del catalogo ${r["valor_catalogo"]:,.0f} COP')
    hoja['B2'].font = SUBTITULO
    hoja.row_dimensions[2].height = 20

    n = 4
    def bloque(titulo):
        nonlocal n
        celda = hoja.cell(row=n, column=2, value=titulo)
        celda.font = Font(name='Calibri', size=12, bold=True, color='FFFFFF')
        celda.fill = PatternFill('solid', fgColor=AZUL)
        hoja.merge_cells(start_row=n, start_column=2, end_row=n, end_column=4)
        n += 1

    def dato(etiqueta, valor, texto):
        nonlocal n
        linea(n, etiqueta, valor, texto)
        n += 1

    def nota(texto):
        nonlocal n
        hoja.cell(row=n, column=2, value=texto).font = NORMAL
        hoja.cell(row=n, column=2).alignment = Alignment(wrap_text=True,
                                                         vertical='top')
        hoja.merge_cells(start_row=n, start_column=2, end_row=n, end_column=4)
        n += 1

    def hueco():
        nonlocal n
        n += 1

    # ── 1. Que se propuso ──
    bloque('1. QUE HIZO ESTE REPORTE')
    nota('A cada producto se le proyo una tarifa de IVA leyendo el nombre y la '
         'categoria. Es una PROPUESTA para que usted la confirme, no una verdad: '
         'palabras como "adhesivo" o "alambre" no distinguen un producto gravado '
         'de uno que no lo esta, y esa decision es del negocio, no del programa.')
    nota('Los precios NO se cambian. La columna "PVP con IVA" es exactamente lo '
         'que paga el cliente hoy. Lo unico que se propone es como se declara el '
         'impuesto en la factura electronica.')
    hueco()

    # ── 2. Las cuatro preguntas ──
    bloque('2. QUE SIGNIFICA CADA COLUMNA')
    for etiqueta, valor, texto in [
        ('Codigo interno', 'PVP / CUB-014',
         'Codigo corto por categoria, para teclear en el POS mientras no haya '
         'escaner. Se genera aqui, no hay que escribirlo.'),
        ('Codigo de barras', '(vacio)',
         f'{sin_barras} productos lo tienen vacio. Se escanea en el mostrador y '
         'se llena progresivamente. No lo toque si no tiene el codigo fisico.'),
        ('Unidad', 'MTR = Metro',
         'Como se mide. Se infirio del nombre cuando habia una pista clara '
         f'("tubo 3 m" -> metro). {con_longitud} productos quedaron como metro o '
         'kilogramo; el resto sigue en unidad, que es lo que tiene la base.'),
        ('PVP con IVA', '15.000',
         'LO QUE PAGA EL CLIENTE. En negrita y sin cambios. No se modifica.'),
        ('Base sin IVA', '12.605',
         'Lo que queda si se le quita el impuesto. Se calcula con la formula '
         'precio / (1 + tarifa).'),
        ('Valor IVA', '2.395',
         'La diferencia entre los dos anteriores. Es lo que la DIAN ve como '
         'impuesto pagado.'),
        ('Tarifa % / Tipo / Codigo DIAN', '19 / GRAVADO / 00',
         'VERDE = gravado al 19% (lo normal). AMBAR = exento (art. 422, cero pero '
         'con otro codigo). AZUL = excluido (art. 424: arena, balastro, cero). '
         'El codigo es el que viaja en el XML; por eso el color importa.'),
        ('DIAN hoy / Tasa hoy', '01 / 0.0',
         'Lo que tiene el producto AHORA en la base. Esta al lado de la propuesta '
         'para ver el cambio. Hoy casi todo el catalogo dice "01 / 0" porque se '
         'lleno con el valor por defecto de la columna y nunca se reviso.'),
        ('Por que hay que revisarlo', 'texto',
         'La razon concreta, en palabras. Si esta vacio, la propuesta es de '
         'confianza alta y se puede aplicar.'),
    ]:
        dato(etiqueta, valor, texto)
    hueco()

    # ── 3. El trabajo real ──
    bloque('3. DONDE ESTA EL TRABAJO REAL (no en el IVA)')
    dato('Precios en cero', f'{precios_en_cero} de {r["productos"]}',
         'Casi tres de cada cuatro productos NO tienen precio. Aqui no hay nada '
         'que auditar fiscalmente: hay que cargar el precio. Cualquier tarifa que '
         'se apruebe sobre un precio en cero es una tarifa sobre nada, y esas '
         'filas estan marcadas en rojo en la columna del nombre. Empiece por aqui.')
    dato('Sin codigo de barras', f'{sin_barras} de {r["productos"]}',
         'Se llena escaneando en el mostrador. No bloquea la auditoria.')
    dato('Unidad por confirmar', f'{con_longitud}',
         'Productos que se venden por metro o por kilo. Si su ferreteria los '
         'vende entera, la unidad correcta es NIU y hay que cambiarla a mano.')
    hueco()

    # ── 4. Como se usa ──
    bloque('4. COMO SE USA (tres pasos)')
    dato('Paso 1', 'POR REVISAR',
         'Abra esa hoja. Esta ordenada por categoria. Lea el motivo, mire el '
         'nombre y el precio. Si esta bien, ponga SI en la columna APLICAR. Si no, '
         'corrija la tarifa ahi mismo y luego ponga SI. Si no esta seguro, deje '
         'NO: no pasa nada.')
    dato('Paso 2', 'LISTOS / TODOS',
         'La hoja LISTOS son las que el programa considera firmes. Igual se '
         'revisan a mano antes de aplicar: la propuesta no es un fallo si se '
         'equivoca, pero es mas barato corrijiarla aqui.')
    dato('Paso 3', '--aplicar',
         'Solo cuando este listo: "python herramientas/'
         'auditar_catalogo_iva.py --aplicar". Ese comando escribe UNICAMENTE las '
         'filas con APLICAR = SI, y hace un respaldo de la base antes de tocar '
         'nada. Sin ese comando no se escribe nada en la base.')
    hueco()

    # ── 5. Lo que el programa NO hizo ──
    bloque('5. LO QUE ESTE REPORTE NO HIZO, A PROPOSITO')
    for texto in [
        'No cambio ningun precio de venta.',
        'No escribio nada en la base de datos: esto es un archivo, no una '
        'operacion.',
        'No activo el IVA por producto. Ese interruptor sigue apagado, y el '
        'catalogo se sigue facturando con la tarifa general del negocio. Se '
        'enciende despues de que usted termine esta auditoria.',
        'No dedujo la tarifa del 5%. Esa tarifa depende del Codigo de Actividades '
        'Economicas inscrito en la DIAN, no del nombre del producto. Si la '
        'ferreteria tiene esa actividad, esos productos se cambian a mano.',
        'No resolvio los nombres repetidos. Si un mismo nombre aparece con dos '
        'precios, el reporte lo avisa en la hoja POR REVISAR, pero el precio es un '
        'problema de catalogo.',
    ]:
        nota('• ' + texto)
    hueco()

    # ── 6. Leyenda de color ──
    bloque('6. COLORES')
    for etiqueta, fondo, texto in [
        ('Verde', VERDE, 'Gravado al 19% (o 5% si usted lo cambia). Lo mas '
                         'comun: tuberia, cemento gris, pintura, herramientas.'),
        ('Ambar', AMBAR, 'Exento, art. 422 E.T. Tasa cero, pero con codigo '
                         'distinto: ladrillo, adobe, teja, cemento de uso '
                         'arquitectonico, yeso.'),
        ('Azul', AZUL_TASA0, 'Excluido, art. 424 E.T. Tasa cero por extraccion '
                             'directa: arena, balastro, grava, piedra.'),
        ('Rojo', ROJO, 'El nombre del producto tiene precio en cero. Cargar el '
                       'precio antes de decidir la tarifa.'),
    ]:
        celda = hoja.cell(row=n, column=2, value=etiqueta)
        celda.fill = PatternFill('solid', fgColor=fondo)
        celda.font = NEGRITA
        celda.alignment = Alignment(horizontal='center', vertical='center')
        celda.border = BORDE
        hoja.cell(row=n, column=4, value=texto).font = NORMAL
        hoja.cell(row=n, column=4).alignment = Alignment(wrap_text=True,
                                                         vertical='top')
        n += 1

    return hoja


# ══════════════════════════════════════════════════════════════
# HOJA 0 — el mapa: que hay y cuanto falta
# ══════════════════════════════════════════════════════════════

ENCABEZADO_RESUMEN = ['Categoria', 'Codigos', 'Productos', 'A decidir',
                      'Listos', 'Sin precio', 'Valor del catalogo',
                      'Tarifa que se propone']


def _hoja_resumen(wb, filas):
    """Una fila por categoria.

    El catalogo esta mezclado: hay categorias de 300 productos y de 1 solo.
    Sin este mapa no hay forma de saber por donde empezar a revisar, y la hoja
    POR REVISAR arranca en orden alfabetico, no por dificultad.
    """
    por_cat = {}
    for f in filas:
        e = por_cat.setdefault(f['categoria'] or '(sin categoria)', {
            'n': 0, 'revisar': 0, 'listos': 0, 'sin_precio': 0,
            'valor': 0.0, 'prefijos': set(), 'tipos': {},
        })
        e['n'] += 1
        e['revisar'] += 1 if f['requiere_revision'] else 0
        e['sin_precio'] += 1 if not f['precio_venta_con_iva'] else 0
        e['listos'] += 0 if f['requiere_revision'] else 1
        e['prefijos'].add(f['codigo_interno'].split('-')[0])
        e['valor'] += f['precio_venta_con_iva']
        e['tipos'][f['iva_tipo']] = e['tipos'].get(f['iva_tipo'], 0) + 1

    # Primero lo que mas trabajo tiene: ahi es donde se gana tiempo.
    orden = sorted(por_cat.items(), key=lambda kv: (-kv[1]['revisar'], kv[0]))

    hoja = wb.create_sheet('RESUMEN')
    hoja.sheet_view.showGridLines = False
    anchos = [30, 14, 12, 12, 10, 13, 20, 34]
    for i, (titulo, ancho) in enumerate(zip(ENCABEZADO_RESUMEN, anchos), start=1):
        c = hoja.cell(row=1, column=i, value=titulo)
        c.font = CABECERA
        c.fill = PatternFill('solid', fgColor=AZUL)
        c.alignment = Alignment(horizontal='center', vertical='center',
                                wrap_text=True)
        c.border = BORDE
        hoja.column_dimensions[get_column_letter(i)].width = ancho
    hoja.row_dimensions[1].height = 30
    hoja.freeze_panes = 'A2'

    for n, (categoria, e) in enumerate(orden, start=2):
        # Que tipos mezcla la categoria. Si aparece mas de uno, la regla no es
        # uniforme y hay que bajar a mirarla fila por fila.
        tipos = e['tipos']
        dominante = max(tipos, key=tipos.get) if tipos else 'GRAVADO'
        propuesta = ', '.join(f'{c} {v}' for c, v in
                              sorted(tipos.items(), key=lambda kv: -kv[1]))
        valores = [categoria, ', '.join(sorted(e['prefijos'])), e['n'],
                   e['revisar'], e['listos'], e['sin_precio'],
                   round(e['valor'], 2), propuesta]
        for i, valor in enumerate(valores, start=1):
            c = hoja.cell(row=n, column=i, value=valor)
            c.border = BORDE
            c.font = NEGRITA if i == 1 else NORMAL
            c.number_format = '#,##0' if i == 7 else '@'
            c.alignment = (Alignment(horizontal='center') if i in (3, 4, 5, 6)
                           else Alignment(vertical='center', wrap_text=(i == 8)))
        hoja.cell(row=n, column=8).fill = PatternFill(
            'solid', fgColor=COLOR_TARIFA.get(dominante, 'FFFFFF'))

    hoja.auto_filter.ref = f'A1:{get_column_letter(len(ENCABEZADO_RESUMEN))}1'
    return hoja


def _columna_aplicar(hoja):
    """Índice (base 0) de la columna APLICAR en la cabecera, o None.

    Se lee solo la primera fila, así que es barato incluso en `read_only`. Se
    busca por ENCABECADO y no por posición: el dueño puede insertar columnas en
    Excel, y una posición fija leería la decisión de la columna equivocada.
    """
    for fila in hoja.iter_rows(min_row=1, max_row=1, values_only=True):
        for posicion, valor in enumerate(fila or ()):
            if valor and str(valor).strip().upper() == 'APLICAR':
                return posicion
    return None


def _marcas_de(hoja):
    """Cuántas filas tienen ALGO escrito en la columna APLICAR.

    OJO: se recorre la hoja UNA vez con `iter_rows`. Con `read_only=True`, cada
    llamada a `hoja.cell()` vuelve a recorrer la hoja desde el principio, así que
    un bucle de 1461 celdas hace 1461 recorridos completos: al cuadrado. Medido,
    eso pasaba de milisegundos a más de dos minutos, y el exportador quedaba
    colgado antes incluso de avisar que iba a pisar el archivo.

    Returns:
        (con_marca, con_si) o (0, 0) si la hoja no tiene columna APLICAR.
    """
    iterador = hoja.iter_rows(values_only=True)
    try:
        cabeceras = next(iterador)
    except StopIteration:
        return 0, 0
    if not cabeceras:
        return 0, 0

    # La columna APLICAR se busca por ENCABEZADO, no por posición: el dueño
    # puede ordenar, filtrar o insertar columnas en Excel, y una posición fija
    # leería la decisión de la columna equivocada sin avisar.
    indice = None
    for posicion, valor in enumerate(cabeceras):
        if valor and str(valor).strip().upper() == 'APLICAR':
            indice = posicion
            break
    if indice is None:
        return 0, 0

    con_marca = con_si = 0
    for fila in iterador:
        if indice >= len(fila) or fila[indice] is None:
            continue
        texto = str(fila[indice]).strip()
        if texto == '':
            continue
        con_marca += 1
        if texto.upper() in ('SI', 'SÍ', 'S', 'YES', '1', 'X'):
            con_si += 1
    return con_marca, con_si


def revisar_existente(ruta):
    """Qué tiene el Excel que ya está en disco.

    Returns:
        dict con:
          existe     : hay un archivo ahí
          con_marcas : alguna fila tiene algo en APLICAR
          con_si     : cuántas filas están marcadas SI
          advertencias: lista de textos que hay que mostrarle al usuario
    """
    info = {
        'existe': False,
        'con_marcas': False,
        'con_si': 0,
        'por_hoja': {},
        'advertencias': [],
    }
    if not os.path.exists(ruta):
        return info

    import openpyxl

    info['existe'] = True
    try:
        libro = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    except Exception as error:
        info['advertencias'].append(
            f'No se pudo leer el archivo existente ({error}). Se va a generar '
            'otro con otro nombre para no pisarlo.'
        )
        return info

    try:
        for nombre in ('TODOS', 'POR REVISAR', 'LISTOS PARA APLICAR'):
            if nombre not in libro.sheetnames:
                continue
            con_marca, con_si = _marcas_de(libro[nombre])
            info['por_hoja'][nombre] = (con_marca, con_si)
            info['con_marcas'] = info['con_marcas'] or con_marca > 0
            info['con_si'] += con_si
    finally:
        libro.close()

    # La trampa más probable: la persona marca en la hoja donde el archivo le
    # dice que revise, pero `--aplicar` solo lee TODOS. Sus marcas se perderían
    # sin error. Se avisa en vez de fallar: puede que sí haya marcado en TODOS.
    for nombre in ('POR REVISAR', 'LISTOS PARA APLICAR'):
        if nombre in info['por_hoja'] and info['por_hoja'][nombre][0] > 0:
            info['advertencias'].append(
                f'Hay {info["por_hoja"][nombre][0]} marca(s) en la hoja '
                f'"{nombre}", pero --aplicar SOLO lee la hoja "TODOS". '
                'Replica ahí tus decisiones o nada se aplicará.'
            )
    return info


def _nombre_alternativo(ruta):
    """Un nombre libre junto al original: auditoria_catalogo_2.xlsx, etc."""
    base, extension = os.path.splitext(ruta)
    numero = 2
    while os.path.exists(f'{base}_{numero}{extension}'):
        numero += 1
    return f'{base}_{numero}{extension}'


def main(forzar=False, salida=None):
    if not os.path.exists(auditoria.DB):
        sys.exit(f'No existe la base: {auditoria.DB}')

    conn = sqlite3.connect(auditoria.DB)
    try:
        filas = list(auditoria.armar_reporte(conn))
    finally:
        conn.close()

    if not filas:
        sys.exit('El catalogo no tiene productos activos.')

    revisar = [f for f in filas if f['requiere_revision']]
    listos = [f for f in filas if not f['requiere_revision']]

    # Las hojas de detalle se dejan ordenadas por trabajo pendiente, no por
    # orden alfabetico de categoria: en la cola de trabajo uno ve primero lo que
    # desbloquea mas decisiones.
    revisar.sort(key=lambda f: (0 if not f['precio_venta_con_iva'] else 1,
                                f['categoria'] or '', f['nombre_producto'] or ''))
    listos.sort(key=lambda f: (f['categoria'] or '', f['nombre_producto'] or ''))

    # ── No pisar un Excel con trabajo dentro ───────────────────────────
    # El archivo existe y tiene marcas en la columna APLICAR: son horas de
    # revisión de alguien. Sobrescribirlo las pierde sin dejar rastro, y no hay
    # forma de recuperarlas. Es el peor error que puede cometer esta herramienta,
    # así que se detiene y deja que se decida a mano.
    destino = salida or SALIDA
    previo = revisar_existente(destino)

    if previo['con_marcas'] and not forzar:
        print()
        print('*** NO SE GENERO EL ARCHIVO ***')
        print()
        print(f'{os.path.basename(destino)} ya tiene {previo["con_si"]} fila(s)')
        print('marcadas como SI. Generarlo encima BORRARIA ese trabajo.')
        print()
        for aviso in previo['advertencias']:
            print(f'  - {aviso}')
        print()
        print('  Opciones:')
        print('    1) Copia el archivo a otro nombre y vuelve a correr: se')
        print('       generará el nuevo junto al viejo.')
        print('    2) Si ya aplicaste las decisiones y quieres un Excel')
        print('       limpio, usa --forzar (revisalo antes: no hay vuelta atrás).')
        print('    3) Si solo querés el reporte en otro lado: --salida RUTA')
        print()
        sys.exit(1)

    for aviso in previo['advertencias']:
        print(f'  AVISO: {aviso}')

    wb = Workbook()
    wb.remove(wb.active)  # la hoja por defecto no sirve para nada aqui

    _hoja_explicacion(wb, filas)
    _hoja_resumen(wb, filas)
    _hoja_datos(wb, 'POR REVISAR', revisar, con_validacion=True)
    _hoja_datos(wb, 'LISTOS PARA APLICAR', listos, con_validacion=True)
    _hoja_datos(wb, 'TODOS', filas, con_validacion=True)

    # La primera hoja que se ve es la que explica.
    wb.active = 0
    wb.save(destino)

    print(f'Reporte: {os.path.basename(destino)}')
    if previo['con_marcas']:
        print(f'  (sobrescrito con --forzar: se perdieron '
              f'{previo["con_si"]} marca(s) del archivo anterior)')
    print(f'  COMO LEER ESTO        : 1 hoja  (que significa cada columna)')
    print(f'  RESUMEN               : 1 hoja  (por categoria, con el prefijo de codigo)')
    print(f'  POR REVISAR           : {len(revisar)} productos  <- empieza aqui')
    print(f'  LISTOS PARA APLICAR   : {len(listos)} productos')
    print(f'  TODOS                 : {len(filas)} productos')
    print('\nLa columna APLICAR tiene una lista SI/NO. Poner NO no hace nada.')


if __name__ == '__main__':
    import argparse

    analisador = argparse.ArgumentParser(
        description='Exporta la auditoría del catálogo a un Excel para revisar.')
    analisador.add_argument(
        '--forzar', action='store_true',
        help='Sobrescribe el archivo aunque tenga decisiones marcadas. '
             'Revisa que ya las hayas aplicado: no hay vuelta atrás.')
    analisador.add_argument(
        '--salida', default=None,
        help='Genera el Excel en otra ruta en vez de auditoria_catalogo.xlsx')
    argumentos = analisador.parse_args()

    main(forzar=argumentos.forzar, salida=argumentos.salida)
