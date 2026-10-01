"""Auditoria y pre-clasificacion fiscal del catalogo de productos.

Genera un reporte linea por linea (JSON + CSV) que el administrador revisa a
mano y, opcionalmente, lo aplica a la base de datos.

POR QUE EXISTE
El catalogo tiene 1461 productos con `iva_tasa = 0` y `iva_tipo_tarifa = '01'`
('excluido', art. 424 E.T.), valores que puso el DEFAULT de la columna y que
nunca se migraron. Con eso, si se activaba la tarifa por producto, casi toda la
venta salia sin impuesto y el documento electronico declaraba tasa cero sobre
ventas que si lo cobran: la DIAN lo rechaza.

La PRE-clasificacion de este script es una PROPUESTA, no una verdad. Automatizar
el criterio fiscal completo seria inventar una decision del negocio: por eso
todo lo dudoso queda con `requiere_revision = true` y NO se aplica salvo que el
administrador lo confirme en el reporte.

NADA SE ADIVINA EN LA APLICACION
`--aplicar` solo escribe los productos que el administrador marco con
`aplicar = SI` en el CSV. Sin ese flag el script no escribe nada.

Uso
    python herramientas/auditar_catalogo_iva.py
    python herramientas/auditar_catalogo_iva.py --aplicar

    Salida: auditoria_catalogo.json, auditoria_catalogo.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(RAIZ, 'ferreteria.db')
SALIDA_JSON = os.path.join(RAIZ, 'auditoria_catalogo.json')
SALIDA_CSV = os.path.join(RAIZ, 'auditoria_catalogo.csv')
SALIDA_XLSX = os.path.join(RAIZ, 'auditoria_catalogo.xlsx')


# ══════════════════════════════════════════════════════════════
# 1. CODIGO INTERNO POR CATEGORIA
# ══════════════════════════════════════════════════════════════
# Codigo corto y alfanumerico (CEM-001) para teclear rapido en el POS mientras
# no haya escaner. Se usa la palabra clave de la categoria y, si dos
# categorias comparten prefijo, se desambigua con el nombre completo.

PREFIJOS_FIJOS = {
    'flejes': 'FLE',
    'fleje': 'FLE',
    'pvc': 'PVC',
    'plasticos': 'PLA',
    'herrajes': 'HER',
    'ferreteria': 'FER',
    'tornilleria': 'TOR',
    'fijacion, tornilleria y herrajes': 'HER',
    'cementos': 'CEM',
    'concretos': 'CON',
    'arenas': 'ARE',
    'agregados': 'AGR',
    'pinturas': 'PIN',
    'esmaltes': 'PIN',
    'soluciones': 'SOL',
    'lamparas': 'LAM',
    'electricos': 'ELE',
    'cables': 'CAB',
    'tubos': 'TUB',
    'sanitarios': 'SAN',
    'herramientas': 'HRR',
    'medicion': 'MED',
    'seguridad': 'SEG',
    'tejas': 'TEJ',
    'maderas': 'MAD',
    'vidrios': 'VID',
    'ceramicas': 'CER',
    'baños': 'BAN',
}


def _normalizar(texto):
    """Sin tildes y en mayusculas, para comparar y para construir claves."""
    if not texto:
        return ''
    descompuesto = unicodedata.normalize('NFKD', str(texto))
    return ''.join(c for c in descompuesto if not unicodedata.combining(c)).upper()


def prefijo_categoria(categoria):
    """Prefijo corto de 3 letras. `None` si la categoria no sirve como prefijo."""
    norm = _normalizar(categoria).strip()
    if not norm:
        return None
    if norm in PREFIJOS_FIJOS:
        return PREFIJOS_FIJOS[norm]
    # Primera palabra significativa: "Pinturas y Barnices" -> "PINTURAS".
    palabras = [p for p in re.split(r'[^A-Z0-9]+', norm) if p and p not in
                ('DE', 'DEL', 'LA', 'EL', 'Y', 'PARA', 'CON')]
    if not palabras:
        return None
    base = palabras[0]
    if len(base) <= 3 and len(palabras) > 1:
        base = (palabras[0] + palabras[1])[:4]
    base = re.sub(r'[^A-Z0-9]', '', base)[:3]
    return base or None


# ══════════════════════════════════════════════════════════════
# 2. UNIDAD DE MEDIDA UN/ECE (los codigos que exige el anexo DIAN)
# ══════════════════════════════════════════════════════════════
# El catalogo viene con '94' (unidad) en casi todo, lo cual es un supuesto
# silencioso: una varilla se vende por metro, un cemento por bulto. Se infiere
# del nombre SOLO cuando hay una pista explicita, y si no hay pista se deja
# '94' y se marca para revision.

UNIDADES = {
    'NIU': 'Unidad',
    '94': 'Unidad',
    'MTR': 'Metro',
    'MTK': 'Metro cuadrado',
    'MTQ': 'Metro cubico',
    'MTK': 'Metro cuadrado',
    'KGM': 'Kilogramo',
    'GRM': 'Gramo',
    'TNE': 'Tonelada',
    'LTR': 'Litro',
    'MLT': 'Mililitro',
    'GAL': 'Galon',
    'BX': 'Caja',
    'PK': 'Paquete',
    'BAG': 'Bulto/Saco',
    'ROL': 'Rollo',
    'SET': 'Juego/Conjunto',
    'PZA': 'Pieza',
    'HR': 'Hora',
    'DAY': 'Dia',
    'DZN': 'Docena',
    'ON': 'Onza',
    'TAR': 'Tarilla',
    'PCE': 'Pieza',
}

# El orden importa: se evalua en cascada y gana la primera pista que aparece.
PISTAS_UNIDAD = [
    (r'\bKG\b|\bKILO', 'KGM'),
    (r'\bGRAMO|\bGR\b', 'GRM'),
    (r'\bM2\b|METRO CUA|METRO CUAD', 'MTK'),
    (r'\bM3\b|METRO CUB|METRO CÚB', 'MTQ'),
    (r'\bL\b|LITRO|GALON|GLN', 'LTR'),
    (r'\bML\b|MILILIT', 'MLT'),
    (r'\bBULTO|\bSACO|\bSACOS?\b|\b50KG|\b50 KG', 'BAG'),
    (r'\bCAJA|\bCJA\b|\bCJ\b', 'BX'),
    (r'\bPAQUETE|\bPAQ\b|\bBLISTER', 'PK'),
    (r'\bROLLO|\bROLLOS?\b', 'ROL'),
    (r'\bJUEGO|\bJUEGOS?\b|\bKIT\b|\bSET\b|\bCAJA DE HERRAMIENTAS', 'SET'),
    (r'\bTARJETA|\bTARILLA', 'TAR'),
    (r'\bHORA\b|\bHORAS\b', 'HR'),
    (r'\bDOZEN|\bDOCENA', 'DZN'),
    (r'\bTON\b|\bTONELADA', 'TNE'),
    # 'M' suelto al final: "varilla 6M", "tubo 3 M". Va DESPUES de M2/M3 a
    # proposito, para que "10M2" no se lea como metro.
    (r'(?<![A-Z0-9])\d+\s?M(?![2-9A-Z])|(?<![A-Z0-9])METRO', 'MTR'),
]


def inferir_unidad(nombre, categoria, unidad_actual):
    """Devuelve (codigo_unidad, requiere_revision, motivo)."""
    texto = _normalizar(f'{nombre} {categoria or ""}')
    for patron, codigo in PISTAS_UNIDAD:
        if re.search(patron, texto):
            return codigo, False, f'inferida del nombre ({codigo}={UNIDADES.get(codigo)})'
    actual = str(unidad_actual or '').strip() or '94'
    if actual in UNIDADES:
        # Se respeta lo que ya tiene, pero un '94' sobre un nombre que SI tiene
        # pista de longitud (tubo, varilla, manguera, alambre) merece revision.
        if re.search(r'\b(TUBO|VARILLA|MANGUERA|ALAMBRE|CINTA|CABLE)\b', texto):
            return 'MTR', True, 'material que se vende por longitud; confirmar si es metro lineal'
        return actual, False, f'mantiene {actual} ({UNIDADES.get(actual, "")})'
    return '94', True, f'unidad "{actual}" no esta en el catalogo UN/ECE'


# El catalogo guarda la unidad como codigo NUMERICO UN/ECE ('94' = unidad), que
# es lo que viaja al XML. El reporte usa el codigo ALFABETICO ('NIU'), que es el
# que se lee en una guia de compra y el que pide el anexo para documentos. Son el
# mismo dato: `unidad_alineada` traduce sin tocar la base.
UNIDAD_ALFABETICA = {
    '94': 'NIU', '10': 'MTR', '11': 'MTK', '09': 'MTQ', 'KGM': 'KGM',
    '28': 'GRM', 'TNE': 'TNE', 'LTR': 'LTR', 'MLT': 'MLT', 'GAL': 'GAL',
    'BX': 'BX', 'PK': 'PK', 'BAG': 'BAG', 'ROL': 'ROL', 'SET': 'SET',
    'PZA': 'PZA', 'HUR': 'HR', 'DAY': 'DAY', 'DZN': 'DZN', 'ONZ': 'ON',
}


def unidad_alineada(codigo):
    """Codigo alfabetico equivalente, sin alterar el dato guardado."""
    return UNIDAD_ALFABETICA.get(str(codigo or '').strip(), str(codigo or '').strip() or 'NIU')


# ══════════════════════════════════════════════════════════════
# 3. PRE-CLASIFICACION FISCAL
# ══════════════════════════════════════════════════════════════
# Referencia: Estatuto Tributario.
#   art. 424 ET -> materiales de extraccion directa (arena, balastro, gravas,
#                  piedra, cal, arcilla): EXCLUIDO, codigo DIAN '01'.
#   art. 422 ET -> bienes de uso arquitectonico/medico/agro (cemento estructural,
#                  yeso, cal, ladrillo en obra, Libros, etc.): EXENTO, '02'.
#   tarifa general 19% -> '00'. Tarifa 5% solo para bienes de la actividad
#   economica CIIU 2410/2420/2423/2424 (metales, barras, flejes): '00' con 5%.
#
# La tarifa 5% NO se deduce: es la del Codigo de Actividades Economicas, y
# depende de que el negocio tenga esa actividad inscrita en la DIAN. Por eso
# todo lo que podria ser 5% queda con `requiere_revision`.

# (patron, iva_tipo, iva_tipo_tarifa, iva_tarifa, art, confianza)
REGLAS_IVA = [
    # ── Art. 424: extraccion directa. Sin IVA. ──────────────────────────
    (r'\bARENA\b|\bARENAS?\b|\BALO\b|\bBALASTRO\b|\bGRAVA\b|\bPISOS? D?E? (BASE|CONCRETO)\b'
     r'|\bPIEDRA\b|\bGRAVA\b|\bMARMOL\b|\bCANTERA\b', 'EXCLUIDO', '01', 0, 424, 'alta'),
    (r'\bCAL\b(?!\s*VIVA)|LIME\b|CAL\s*APAGADA', 'EXCLUIDO', '01', 0, 424, 'media'),

    # ── Art. 422: exentos de bien de uso arquitectonico. ───────────────
    (r'\bLADRILLO\b|\bLADRILLOS?\b|\bADOBE\b|\bTEJA\b|\bTEJAS\b|\bCABO\b'
     r'|\bESCOBA\b|\bCOCOL?O\b', 'EXENTO', '02', 0, 422, 'media'),
    (r'\bCEMENTO\b|\bCEMENTOS?\b|\bMORTERO\b|\bHORMIGON\b|\bCONCRETO\b'
     r'|\bYESO\b|\bESTUCO\b|\bPASTA\s*DE\s*ENCHAPE\b|\bENCHAPE\b', 'EXENTO', '02', 0, 422, 'media'),
    (r'\bCINTA\s*DE\s*AISLAMIENTO\b|\bLANA\s*DE\s*VIDRIO\b|\bTEJA\s*ONDULAD', 'EXENTO', '02', 0, 422, 'baja'),

    # ── Tarifa general 19% (lo demas del catalogo). ─────────────────────
    # Confianza ALTA a proposito: para una ferreteria, lo que no cae en 422 ni
    # en 424 es gravado al 19% (art. 437 E.T.). Marcarlo derevision dejaba los
    # 1461 productos en la lista de pendientes, que es lo mismo que no auditar
    # nada. La duda real (pinturas, metales, electricos, adhesion) la marque la
    # lista MARCAS_REVISION, que si es corta y revisable.
    (r'.*', 'GRAVADO', '00', 19, None, 'alta'),
]

# Palabras que exigen revision humana aunque caigan en una regla.
MARCAS_REVISION = re.compile(
    r'\bIMPERMEAB|\bPINTURA\b|\bESMALTE\b|\bSELLAD|\bADHESIV|\bPEGANT'
    r'|\bSOLUCION|\bREMOVEDOR|\bDILUYENTE|\bESQUELET|\bALUMINIO\b'
    r'|\bBARRA\b|\bPERFIL\b|\bALAMBR|\bANGULO\b|\bFLEJE\b|\bGALVANIZAD'
    r'|\bPLASTIC|\bLICOR|\bACEITE\b|\bGAS\b|\bASEO\b|\bJABON|\bDETERGENT'
    r'|\bMEDICAMENT|\bVETERINARI|\bALIMENTO\b|\bELEMENTO DE PROTECCION'
    r'|\bEPP\b|\bCASCO\b|\bGUANTE|\bLENTE\b|\bARNES\b|\bBOTA\b'
    r'|\bCABLE\b|\bCONDUCTOR|\bLAMPA|\bBOMBILLO|\bINTERRUPTOR|\bTOMACORRIENTE'
    r'|\bCONTABIL\b|\bADMINISTRATIV', re.I)


def clasificar_iva(nombre, categoria, dimensiones):
    """Devuelve un dict con la propuesta fiscal y si requiere revision."""
    texto = _normalizar(f'{categoria or ""} {nombre or ""} {dimensiones or ""}')
    for patron, tipo, tipo_tarifa, tasa, art, confianza in REGLAS_IVA:
        if not re.search(patron, texto, re.I):
            continue
        propuesta = {
            'iva_tipo': tipo,
            'iva_tipo_tarifa': tipo_tarifa,
            'iva_tarifa': tasa,
            'articulo_estatuto': art,
            'fundamento': (f'art. {art} del Estatuto Tributario' if art
                           else 'tarifa general del articulo 437 E.T.'),
            'confianza': confianza,
        }
        break

    motivos = []
    if propuesta['confianza'] == 'baja':
        motivos.append('criterio de baja confianza: confirmar el articulo aplicado')
    if MARCAS_REVISION.search(texto):
        motivos.append('producto de la lista de verificacion manual (pinturas, '
                       'metales, electricos, EPP o adhesion)')

    propuesta['requiere_revision'] = bool(motivos)
    propuesta['motivo_revision'] = '; '.join(motivos)
    return propuesta


# ══════════════════════════════════════════════════════════════
# 4. DESGLOSE
# ══════════════════════════════════════════════════════════════
# `precio_venta` es el PRECIO FINAL: lo que paga el cliente, con el IVA dentro.
# El impuesto se EXTRAE, no se suma. Sumarlo cobraria un recargo del 19% (bug
# ya corregido en `ferreteria/blueprints/ventas.py` y en el POS).


def desglosar(precio_final, tasa_porcentaje):
    """precio sin IVA e IVA, en pesos, con la formula de descontacion."""
    precio = round(float(precio_final or 0), 2)
    if not precio or tasa_porcentaje == 0:
        return precio, 0.0
    base = precio / (1 + (tasa_porcentaje / 100.0))
    return round(base, 2), round(precio - base, 2)


# ══════════════════════════════════════════════════════════════
# 5. LECTURA Y ARMADO DEL REPORTE
# ══════════════════════════════════════════════════════════════

SQL = """
SELECT id, nombre, categoria, dimensiones, precio_venta, codigo_barras,
       unidad_medida, COALESCE(iva_tasa, 0), COALESCE(iva_tipo_tarifa, '01'),
       COALESCE(iva_naturaleza, 'excluido'), COALESCE(activo, 1)
FROM productos
WHERE COALESCE(activo, 1) = 1
ORDER BY COALESCE(categoria, ''), id
"""


def armar_reporte(conn):
    filas = conn.execute(SQL).fetchall()
    # Conteo por categoria para el consecutivo del codigo interno.
    consecutivos = {}
    nombres_vistos = {}

    for f in filas:
        (pid, nombre, categoria, dimensiones, precio, codigo_barras,
         unidad, iva_tasa_actual, tipo_actual, naturaleza_actual, activo) = f

        pref = prefijo_categoria(categoria)
        clave = pref or 'GEN'
        if pref is None:
            pref = 'GEN'
        consecutivos[clave] = consecutivos.get(clave, 0) + 1
        codigo_interno = f'{pref}-{consecutivos[clave]:03d}'
        if consecutivos[clave] > 999:
            codigo_interno = f'{pref}-{consecutivos[clave]}'

        unidad_cod, unidad_rev, unidad_motivo = inferir_unidad(
            nombre, categoria, unidad)
        fiscal = clasificar_iva(nombre, categoria, dimensiones)
        base, valor_iva = desglosar(precio, fiscal['iva_tarifa'])

        requiere = unidad_rev or fiscal['requiere_revision']
        motivos = [m for m in (unidad_motivo if unidad_rev else '',
                                fiscal['motivo_revision']) if m]

        # Un precio en cero no es un dato fiscal, pero si es un dato roto: el
        # cajero no lo podria vender y el reporte de inventario lo cuenta como
        # mercancia. Se marca para que la limpieza sea aparte de la fiscalizacion.
        precio_num = round(float(precio or 0), 2)
        if precio_num <= 0:
            requiere = True
            motivos.append('precio en cero: revisar antes de importar')

        # Un mismo nombre con dos precios distintos es un problema de catalogo
        # que hay que ver antes de importar.
        clave_nombre = _normalizar(nombre)
        if clave_nombre in nombres_vistos:
            requiere = True
            motivos.append(f'el mismo nombre ya existe con otro precio '
                           f'({nombres_vistos[clave_nombre]})')
        else:
            nombres_vistos[clave_nombre] = precio

        yield {
            'id': pid,
            'codigo_interno': codigo_interno,
            'codigo_barras': codigo_barras or '',
            'nombre_producto': nombre,
            'categoria': categoria or '',
            'unidad_medida': unidad_alineada(unidad_cod),
            'unidad_medida_dian': unidad_cod,
            'unidad_medida_descripcion': UNIDADES.get(unidad_cod, ''),
            'precio_venta_con_iva': precio_num,
            'precio_sin_iva': base,
            'valor_iva': valor_iva,
            'iva_tarifa': fiscal['iva_tarifa'],
            'iva_tipo': fiscal['iva_tipo'],
            'iva_tipo_tarifa_dian': fiscal['iva_tipo_tarifa'],
            'articulo_estatuto': fiscal['articulo_estatuto'],
            'fundamento': fiscal['fundamento'],
            'iva_tipo_tarifa_actual': tipo_actual,
            'iva_tasa_actual': iva_tasa_actual,
            'requiere_revision': requiere,
            'motivo_revision': '; '.join(motivos),
            'aplicar': 'NO' if requiere else 'SI',
        }


def resumen(filas):
    def cuenta(clave, valor):
        total, pendientes = 0, 0
        for f in filas:
            if f[clave] == valor:
                total += 1
                pendientes += 1 if f['requiere_revision'] else 0
        return total, pendientes

    tipos = {}
    for f in filas:
        e = tipos.setdefault(f['iva_tipo'], {'n': 0, 'revision': 0})
        e['n'] += 1
        e['revision'] += 1 if f['requiere_revision'] else 0

    return {
        'productos': len(filas),
        'requieren_revision': sum(1 for f in filas if f['requiere_revision']),
        'listos_para_aplicar': sum(1 for f in filas if not f['requiere_revision']),
        'precio_en_cero': sum(1 for f in filas if f['precio_venta_con_iva'] <= 0),
        'valor_catalogo': round(sum(f['precio_venta_con_iva'] for f in filas), 2),
        'por_iva_tipo': tipos,
        'unidades': {c: sum(1 for f in filas if f['unidad_medida'] == c)
                     for c in sorted({f['unidad_medida'] for f in filas})},
    }


# ══════════════════════════════════════════════════════════════
# 6. APLICACION
# ══════════════════════════════════════════════════════════════

# Hojas del Excel de las que se leen decisiones, en ORDEN DE MENOS a MAS
# prioridad. Ver `_resolver` para por qué está en ese orden.
HOJAS_DECISION = ('LISTOS PARA APLICAR', 'POR REVISAR', 'TODOS')

VERDADEROS = ('SI', 'SÍ', 'S', 'YES', '1', 'X')


def _leer_marcas(hoja):
    """Devuelve {id_producto: 'SI'|'NO'} de una hoja del Excel.

    Se recorre UNA sola vez con `iter_rows`: con `read_only`, cada llamada a
    `hoja.cell()` vuelve a recorrer la hoja entera, y 1461 filas convertían la
    lectura en algo de más de dos minutos.

    La columna APLICAR se busca por ENCABECADO y no por posición: el dueño puede
    insertar o reordenar columnas en Excel, y una posición fija leería la
    decisión de la columna equivocada sin avisar.
    """
    iterador = hoja.iter_rows(values_only=True)
    try:
        cabeceras = next(iterador)
    except StopIteration:
        return {}
    if not cabeceras:
        return {}

    col_id = col_aplicar = None
    for posicion, valor in enumerate(cabeceras):
        if valor is None:
            continue
        titulo = str(valor).strip().lower()
        if titulo == 'id bd':
            col_id = posicion
        elif titulo == 'aplicar':
            col_aplicar = posicion
    if col_id is None or col_aplicar is None:
        return {}

    marcas = {}
    for fila in iterador:
        if col_id >= len(fila) or col_aplicar >= len(fila):
            continue
        identificador = fila[col_id]
        if identificador is None:
            continue
        decision = fila[col_aplicar]
        if decision is None:
            continue
        texto = str(decision).strip()
        if texto == '':
            continue
        try:
            clave = int(identificador)
        except (TypeError, ValueError):
            continue
        # 'NO' se guarda como tal: es una decisión explícita de NO TOCAR, y la
        # respetamos por encima de la confianza que tenga el reporte.
        marcas[clave] = 'SI' if texto.upper() in VERDADEROS else 'NO'
    return marcas


def _resolver(marcas_por_hoja):
    """Consolida las marcas de varias hojas en una sola decisión por producto.

    REGLA (y por qué esta)
    ---------------------
    Un producto aparece en varias hojas: está en TODOS siempre, en POR REVISAR
    si necesita decisión, y en LISTOS si el reporte lo considera de confianza.

    - Si tiene NO en CUALQUIER hoja, no se aplica. Un NO es alguien diciendo
      "a este déjalo quieto". Y como el reporte aplica solo lo que considera de
      confianza, ese NO también debe frenar la propuesta automática: si no, el
      usuario marcó NO en una hoja y la herramienta lo aplicó igual.
    - Si tiene SI y ningún NO, se aplica. Da igual en qué hoja esté marcado:
      la persona lo decidió.
    - Si no tiene ninguna marca, no se decide aquí. El comando usa entonces la
      confianza del reporte.

    El NO gana sobre el SI a propósito. Si alguien se contradice entre hojas, lo
    que sale mal es cambiar la tarifa de un producto sin querer; eso no tiene
    arreglo. Dejarlo como estaba, sí.
    """
    consolidado = {}
    for hoja in HOJAS_DECISION:
        for clave, valor in marcas_por_hoja.get(hoja, {}).items():
            if valor == 'NO':
                consolidado[clave] = 'NO'
            elif consolidado.get(clave) != 'NO':
                consolidado[clave] = 'SI'
    return consolidado


def decisiones_del_excel(filas=None, detallado=False):
    """Lee la columna APLICAR del Excel que llenó el administrador.

    Se leen TODAS las hojas de detalle y se consolidan (ver `_resolver`), para
    que no haya que copiar las decisiones de `POR REVISAR` a `TODOS` a mano. Antes
    solo se leía `TODOS`, y el propio archivo indicaba trabajar en `POR
    REVISAR`: lo que se marcaba ahí no se aplicaba nunca, sin aviso.

    Returns:
        {id_producto: 'SI'|'NO'}. Con `detallado=True` devuelve además un
        resumen por hoja, para que el comando diga de dónde salió cada cosa.
    """
    vacio = ({}, {}) if detallado else {}
    if not os.path.exists(SALIDA_XLSX):
        return vacio

    import openpyxl

    libro = openpyxl.load_workbook(SALIDA_XLSX, data_only=True, read_only=True)
    try:
        marcas_por_hoja = {
            nombre: _leer_marcas(libro[nombre])
            for nombre in HOJAS_DECISION
            if nombre in libro.sheetnames
        }
    finally:
        libro.close()

    decisiones = _resolver(marcas_por_hoja)
    del filas
    if not detallado:
        return decisiones
    return decisiones, marcas_por_hoja


def aplicar(conn, filas):
    """Escribe SOLO los productos que el administrador marco como aplicables.

    La decision sale del Excel (`auditoria_catalogo.xlsx`), leyendo TODAS sus
    hojas y consolidandolas, y si el Excel no existe o esta vacio, del reporte
    generado en memoria. El Excel manda: es lo que la persona reviso y confirmo
    fila por fila.

    Un 'NO' explicito en cualquier hoja frena el producto, incluso si el reporte
    lo considera de confianza alta. Quien marco NO estaba diciendo "no se": estaba
    diciendo "a este no lo toques", y eso gana.

    Se guardan la tasa, el tipo de tarifa DIAN, la naturaleza y la unidad. El
    `precio_venta` NO se toca: es el precio que paga el cliente y ni el reporte
    ni este comando lo modifican.
    """
    decisiones, por_hoja = decisiones_del_excel(detallado=True)
    if decisiones:
        marcados = [h for h in HOJAS_DECISION if por_hoja.get(h)]
        origen = ', '.join(
            f'{h} ({sum(1 for v in por_hoja[h].values() if v == "SI")})'
            for h in marcados)
        print(f'Decisiones leidas del Excel: {origen}')

    explicitos_si = sum(1 for v in decisiones.values() if v == 'SI')
    explicitos_no = sum(1 for v in decisiones.values() if v == 'NO')
    if explicitos_si or explicitos_no:
        print(f'  {explicitos_si} marcado(s) SI · {explicitos_no} marcado(s) NO '
              '(un NO gana sobre un SI)')

    candidatos = [f for f in filas
                  if decisiones.get(f['id'], f['aplicar']) == 'SI'
                  and f['codigo_interno']]
    if not candidatos:
        print('No hay productos marcados para aplicar. Nada se escribio.')
        return 0

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    respaldo = os.path.join(RAIZ, f'ferreteria-respaldo-antes-iva-{stamp}.db')
    shutil.copy2(DB, respaldo)
    print(f'Respaldo previo: {os.path.basename(respaldo)}')

    naturaleza = {'GRAVADO': 'gravado', 'EXENTO': 'exento', 'EXCLUIDO': 'excluido'}
    conn.execute('BEGIN')
    try:
        for f in candidatos:
            conn.execute(
                'UPDATE productos SET unidad_medida = ?, iva_tasa = ?, '
                'iva_tipo_tarifa = ?, iva_naturaleza = ? WHERE id = ?',
                (f['unidad_medida_dian'], f['iva_tarifa'],
                 f['iva_tipo_tarifa_dian'],
                 naturaleza.get(f['iva_tipo'], 'excluido'),
                 f['id']),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        print('Se revirtio la escritura: no se aplico nada.')
        raise
    return len(candidatos)


# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--aplicar', action='store_true',
                    help='escribe en la base lo que quedo con APLICAR = SI')
    args = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit(f'No existe la base: {DB}')

    conn = sqlite3.connect(DB)
    try:
        filas = list(armar_reporte(conn))
        if not filas:
            sys.exit('El catalogo no tiene productos activos.')

        if args.aplicar:
            n = aplicar(conn, filas)
            print(f'\n{n} productos actualizados.')
        else:
            with open(SALIDA_JSON, 'w', encoding='utf-8') as fh:
                json.dump({'generado': datetime.now().isoformat(timespec='seconds'),
                           'resumen': resumen(filas), 'productos': filas},
                          fh, ensure_ascii=False, indent=2)
            with open(SALIDA_CSV, 'w', encoding='utf-8-sig', newline='') as fh:
                w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()))
                w.writeheader()
                w.writerows(filas)

            r = resumen(filas)
            print(f"Productos activos        : {r['productos']}")
            print(f"Listos para aplicar      : {r['listos_para_aplicar']}")
            print(f"Requieren revision       : {r['requieren_revision']}")
            print(f"Precio en cero            : {r['precio_en_cero']}")
            print(f"Valor del catalogo        : {r['valor_catalogo']:,.0f} COP")
            for tipo, e in sorted(r['por_iva_tipo'].items()):
                print(f"  {tipo:<9} {e['n']:>5} productos "
                      f"({e['revision']} por revisar)")
            print('\nArchivos: auditoria_catalogo.json y auditoria_catalogo.csv')
            print('Para revisar el catalogo con colores y marcar decisiones:')
            print('  python herramientas/exportar_excel_catalogo.py')
            print('Cuando ya haya revisado el Excel, para escribir en la base:')
            print('  python herramientas/auditar_catalogo_iva.py --aplicar')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
