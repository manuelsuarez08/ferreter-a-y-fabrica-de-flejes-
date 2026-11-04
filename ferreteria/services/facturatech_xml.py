"""Generador del XML simplificado de Facturatech (tiquete POS / factura).

Facturatech no recibe UBL: recibe su propio XML (`<FACTURA>` con nodos ENC,
IPV, EMI, ADQ, TOT, DRF, MEP, ITE...) y ellos construyen, firman y envían el
UBL a la DIAN. Este módulo solo arma ese insumo a partir de los mismos dicts
que usa `dian_xml.construir_invoice`; NO hace ninguna llamada de red.

Referencia: Pos.pdf (23/04/2024) y 'Factura POS -BASE.xml' de Facturatech.
El ORDEN de los nodos importa: un nodo mal colocado devuelve error de
estructura.

Supuestos no confirmados por el manual (verificar contra el demo):
- Los impuestos del ítem (TII/IIM) van al final del ITE, después de IAE.
- Tipo de impuesto (Tabla 44): '01' IVA y '04' INC.
- La numeración y estructura de factura/nota sigue las guías de Facturatech de
  sep-2025 (ITE_5 neto de descuentos; orden IAE -> IDE -> TII).
"""
import xml.etree.ElementTree as ET
from ferreteria.services.dian import calcular_digito_verificacion

from ferreteria.services.dian_pos import (
    formatear_cantidad, formatear_monto, normalizar_fecha, solo_digitos)
from ferreteria.services.dian_xml import (
    _nombre_departamento, _nombre_municipio, _nombre_normalizado)

TIPO_IMPUESTO_IVA = '01'
TIPO_IMPUESTO_INC = '04'
NIT_CONSUMIDOR_FINAL = '222222222222'
OBLIGACIONES_VALIDAS = ('O-13', 'O-15', 'O-23', 'O-47', 'R-99-PN')
TIPOS_DOCUMENTO = {'INVOIC', 'NC', 'ND'}


def _nodo(tag, texto=None):
    elemento = ET.Element(tag)
    if texto is not None:
        elemento.text = str(texto)
    return elemento


def _hijos(padre, pares):
    """Agrega `(tag, valor)` omitiendo los valores None (nodos opcionales)."""
    for tag, valor in pares:
        if valor is not None:
            padre.append(_nodo(tag, valor))
    return padre


def _monto(valor):
    return formatear_monto(valor)


def _prefijo_y_folio(documento):
    prefijo = str(documento.get('prefijo') or '').strip()
    folio = str(documento.get('folio') or '').strip()
    if not prefijo:
        raise ValueError('El prefijo es obligatorio para el servicio web')
    if prefijo[-1].isdigit():
        # Si el prefijo terminara en número, prefijo+folio sería ambiguo.
        raise ValueError('El prefijo no puede terminar en número')
    if not folio.isdigit():
        raise ValueError('El folio debe ser numérico')
    return prefijo, folio


def _encabezado(documento, adquirente, items, prefijo, folio, nit_emisor):
    tipo = documento.get('tipo') or 'INVOIC'
    if tipo not in TIPOS_DOCUMENTO:
        raise ValueError(f'Tipo de documento no válido: {tipo}')
    nit_adq = solo_digitos(adquirente.get('numero_documento')) or NIT_CONSUMIDOR_FINAL
    ambiente = '1' if str(documento.get('tipo_ambiente')) == '1' else '2'
    return _hijos(_nodo('ENC'), [
        ('ENC_1', tipo),
        ('ENC_2', nit_emisor),
        ('ENC_3', nit_adq),
        ('ENC_4', 'UBL 2.1'),
        ('ENC_5', 'DIAN 2.1'),
        ('ENC_6', f'{prefijo}{folio}'),
        ('ENC_9', documento.get('tipo_factura') or '01'),
        ('ENC_10', documento.get('moneda') or 'COP'),
        ('ENC_15', len(items)),
        ('ENC_16', normalizar_fecha(documento['fecha_vencimiento'])
         if documento.get('fecha_vencimiento') else None),
        ('ENC_20', ambiente),
        ('ENC_21', documento.get('tipo_operacion') or '10'),
    ])


def _punto_de_venta(documento, totales):
    pos = documento.get('pos') or {}
    return _hijos(_nodo('IPV'), [
        ('IPV_1', pos.get('placa') or 'CAJA01'),
        ('IPV_2', pos.get('ubicacion') or 'ALMACEN'),
        ('IPV_3', pos.get('cajero') or 'CAJERO'),
        ('IPV_4', pos.get('tipo_caja') or 'TC01'),
        ('IPV_5', pos.get('codigo_venta') or documento.get('numero') or ''),
        ('IPV_6', _monto(totales.get('line_extension_amount'))),
    ])


def _obligacion(datos):
    codigos = datos.get('responsabilidades') or []
    for codigo in codigos:
        if codigo in OBLIGACIONES_VALIDAS:
            return codigo
    return 'R-99-PN'


def _direccion_fiscal(datos):
    municipio = str(datos.get('municipio') or '').strip()
    departamento = str(datos.get('departamento') or '').strip()
    ciudad = _nombre_normalizado(
        str(datos.get('ciudad') or '').strip() or _nombre_municipio(municipio))
    nombre_dep = _nombre_normalizado(
        str(datos.get('nombre_departamento') or '').strip()
        or _nombre_departamento(departamento))
    return municipio, departamento, ciudad, nombre_dep


def _emisor(emisor, nit_emisor):
    razon = emisor.get('razon_social') or ''
    municipio, departamento, ciudad, nombre_dep = _direccion_fiscal(emisor)
    pais = emisor.get('pais') or 'CO'
    postal = emisor.get('codigo_postal') or municipio
    nodo = _hijos(_nodo('EMI'), [
        ('EMI_1', emisor.get('tipo_persona') or '1'),
        ('EMI_2', nit_emisor),
        ('EMI_3', '31'),
        ('EMI_6', razon),
        ('EMI_7', emisor.get('nombre_comercial') or razon),
        ('EMI_10', emisor.get('direccion')),
        ('EMI_11', departamento),
        ('EMI_13', ciudad),
        ('EMI_14', postal),
        ('EMI_15', pais),
        ('EMI_19', nombre_dep),
        ('EMI_22', emisor.get('digito_verificacion')),
        ('EMI_23', municipio),
        ('EMI_24', razon),
    ])
    nodo.append(_hijos(_nodo('TAC'), [('TAC_1', _obligacion(emisor))]))
    nodo.append(_hijos(_nodo('DFE'), [
        ('DFE_1', municipio), ('DFE_2', departamento), ('DFE_3', pais),
        ('DFE_4', postal), ('DFE_5', 'Colombia'), ('DFE_6', nombre_dep),
        ('DFE_7', ciudad), ('DFE_8', emisor.get('direccion')),
    ]))
    # ICC_9 (prefijo de la numeración) es obligatorio solo para persona jurídica.
    if (emisor.get('tipo_persona') or '1') == '1' and emisor.get('prefijo'):
        nodo.append(_hijos(_nodo('ICC'), [('ICC_9', emisor['prefijo'])]))
    if emisor.get('email') or emisor.get('telefono'):
        nodo.append(_hijos(_nodo('CDE'), [
            ('CDE_2', emisor.get('nombre_comercial') or razon),
            ('CDE_3', emisor.get('telefono')),
            ('CDE_4', emisor.get('email')),
        ]))
    nodo.append(_hijos(_nodo('GTE'), [('GTE_1', '01'), ('GTE_2', 'IVA')]))
    return nodo


def _adquirente(adquirente):
    numero = solo_digitos(adquirente.get('numero_documento')) or NIT_CONSUMIDOR_FINAL
    consumidor = numero == NIT_CONSUMIDOR_FINAL
    tipo_doc = '22' if consumidor else str(adquirente.get('tipo_documento_codigo') or '13')
    nombre = adquirente.get('nombre') or 'Consumidor final'
    municipio, departamento, _, _ = _direccion_fiscal(adquirente)
    nodo = _hijos(_nodo('ADQ'), [
        ('ADQ_1', '2' if tipo_doc != '31' else '1'),
        ('ADQ_2', numero),
        ('ADQ_3', tipo_doc),
        ('ADQ_6', nombre),
        ('ADQ_11', departamento or None),
        ('ADQ_14', adquirente.get('codigo_postal') or municipio or None),
        ('ADQ_15', adquirente.get('pais') or 'CO'),
        ('ADQ_22', str(calcular_digito_verificacion(numero)) if tipo_doc == '31' else None),
    ])
    nodo.append(_hijos(_nodo('TCR'), [('TCR_1', _obligacion(adquirente))]))
    if adquirente.get('email') or adquirente.get('telefono'):
        nodo.append(_hijos(_nodo('CDA'), [
            ('CDA_2', nombre),
            ('CDA_3', adquirente.get('telefono')),
            ('CDA_4', adquirente.get('email')),
        ]))
    nodo.append(_hijos(_nodo('GTA'), [('GTA_1', '01'), ('GTA_2', 'IVA')]))
    return nodo


def _calcular_linea(item):
    cantidad = float(item.get('cantidad') or 0)
    precio = float(item.get('precio_unitario') or 0)
    descuento = float(item.get('descuento') or 0)
    bruto = round(cantidad * precio, 2)
    base = round(bruto - descuento, 2)
    iva_tasa = float(item.get('iva_tasa') or 0)
    inc_tasa = float(item.get('inc_tasa') or 0)
    return {
        'cantidad': cantidad, 'precio': precio, 'descuento': descuento,
        'bruto': bruto, 'base': base,
        'iva_tasa': iva_tasa, 'iva': round(base * iva_tasa / 100, 2),
        'inc_tasa': inc_tasa, 'inc': round(base * inc_tasa / 100, 2),
    }


def _impuesto_item(codigo, valor, base, tasa, moneda):
    return _hijos(_nodo('IIM'), [
        ('IIM_1', codigo), ('IIM_2', _monto(valor)), ('IIM_3', moneda),
        ('IIM_4', _monto(base)), ('IIM_5', moneda),
        ('IIM_6', _monto(tasa)),
    ])


def _linea(indice, item, calculo, moneda):
    unidad = str(item.get('unidad') or '94')
    # ITE_5 = (ITE_27*ITE_7) - descuentos: ya viene neto (guía 4DESTM_CARITM).
    nodo = _hijos(_nodo('ITE'), [
        ('ITE_1', indice),
        ('ITE_3', formatear_cantidad(calculo['cantidad'])),
        ('ITE_4', unidad),
        ('ITE_5', _monto(calculo['base'])),
        ('ITE_6', moneda),
        ('ITE_7', _monto(calculo['precio'])),
        ('ITE_8', moneda),
        ('ITE_11', item.get('descripcion') or 'Producto'),
        ('ITE_19', _monto(calculo['base'])),
        ('ITE_20', moneda),
        ('ITE_27', formatear_cantidad(calculo['cantidad'])),
        ('ITE_28', unidad),
    ])
    nodo.append(_hijos(_nodo('IAE'), [
        ('IAE_1', item.get('codigo') or str(indice)),
        ('IAE_2', item.get('codigo_estandar') or '999'),
    ]))
    # Orden del manual: IAE -> IDE -> TII.
    if calculo['descuento']:
        porcentaje = (calculo['descuento'] * 100 / calculo['bruto']
                      if calculo['bruto'] else 0)
        nodo.append(_hijos(_nodo('IDE'), [
            ('IDE_1', 'false'), ('IDE_2', _monto(calculo['descuento'])),
            ('IDE_3', moneda), ('IDE_6', _monto(porcentaje)),
            ('IDE_7', _monto(calculo['bruto'])), ('IDE_8', moneda),
        ]))
    total_impuestos = calculo['iva'] + calculo['inc']
    tii = _hijos(_nodo('TII'), [
        ('TII_1', _monto(total_impuestos)), ('TII_2', moneda),
        ('TII_3', 'false'),
    ])
    tii.append(_impuesto_item(TIPO_IMPUESTO_IVA, calculo['iva'], calculo['base'],
                              calculo['iva_tasa'], moneda))
    if calculo['inc_tasa']:
        tii.append(_impuesto_item(TIPO_IMPUESTO_INC, calculo['inc'],
                                  calculo['base'], calculo['inc_tasa'], moneda))
    nodo.append(tii)
    return nodo


def _impuestos_globales(calculos, moneda):
    """Nodos TIM/IMP: un TIM con un IMP por cada tipo y tarifa."""
    grupos = {}
    for c in calculos:
        grupos.setdefault((TIPO_IMPUESTO_IVA, c['iva_tasa']), [0.0, 0.0])
        grupos[(TIPO_IMPUESTO_IVA, c['iva_tasa'])][0] += c['base']
        grupos[(TIPO_IMPUESTO_IVA, c['iva_tasa'])][1] += c['iva']
        if c['inc_tasa']:
            grupos.setdefault((TIPO_IMPUESTO_INC, c['inc_tasa']), [0.0, 0.0])
            grupos[(TIPO_IMPUESTO_INC, c['inc_tasa'])][0] += c['base']
            grupos[(TIPO_IMPUESTO_INC, c['inc_tasa'])][1] += c['inc']
    total = round(sum(valor for _, valor in grupos.values()), 2)
    tim = _hijos(_nodo('TIM'), [
        ('TIM_1', 'false'), ('TIM_2', _monto(total)), ('TIM_3', moneda)])
    for (codigo, tasa), (base, valor) in sorted(grupos.items()):
        tim.append(_hijos(_nodo('IMP'), [
            ('IMP_1', codigo), ('IMP_2', _monto(base)), ('IMP_3', moneda),
            ('IMP_4', _monto(valor)), ('IMP_5', moneda),
            ('IMP_6', _monto(tasa)),
        ]))
    return tim


def _totales(calculos, totales, moneda):
    base = round(sum(c['base'] for c in calculos), 2)
    impuestos = round(sum(c['iva'] + c['inc'] for c in calculos), 2)
    con_impuestos = round(base + impuestos, 2)
    a_pagar = totales.get('payable_amount')
    a_pagar = con_impuestos if a_pagar is None else float(a_pagar)
    # TOT_1 = suma de ITE_5, que ya es neto de descuentos.
    return _hijos(_nodo('TOT'), [
        ('TOT_1', _monto(base)), ('TOT_2', moneda),
        ('TOT_3', _monto(base)), ('TOT_4', moneda),
        ('TOT_5', _monto(a_pagar)), ('TOT_6', moneda),
        ('TOT_7', _monto(con_impuestos)), ('TOT_8', moneda),
    ])


def _resolucion(resolucion, prefijo):
    faltan = [k for k in ('numero', 'fecha_inicio', 'fecha_fin', 'desde', 'hasta')
              if not resolucion.get(k)]
    if faltan:
        raise ValueError('Faltan datos de la resolución: ' + ', '.join(faltan))
    return _hijos(_nodo('DRF'), [
        ('DRF_1', resolucion['numero']),
        ('DRF_2', normalizar_fecha(resolucion['fecha_inicio'])),
        ('DRF_3', normalizar_fecha(resolucion['fecha_fin'])),
        ('DRF_4', prefijo),
        ('DRF_5', resolucion['desde']),
        ('DRF_6', resolucion['hasta']),
    ])


def construir_factura(documento, emisor, adquirente, items, totales,
                      resolucion, beneficio_comprador=None):
    """Arma el árbol `<FACTURA>` en el formato de Facturatech.

    `documento` necesita: prefijo, folio, fecha (opcional, no se envía),
    tipo ('INVOIC' por defecto), tipo_ambiente ('1' prod / '2' demo),
    forma_pago ('1' contado, '2' crédito), medio_pago (Tabla 26, '10' efectivo)
    y, para POS, `pos` con placa, ubicacion, cajero, tipo_caja, codigo_venta.
    `resolucion`: numero, fecha_inicio, fecha_fin, desde, hasta.
    Para notas (`tipo` 'NC' o 'ND') la raíz es `<NOTA>`, ENC_9 es 91/92 y se
    exige `documento['referencia']` (numero, fecha_firma, cufe) y
    `documento['concepto']` (codigo CDN_1, descripcion CDN_2). Una NC que anula
    la factura (CDN_1 = 2) lleva ENC_21 = 20 y los mismos datos que la factura.

    Raises ValueError si falta un dato mandatorio (mejor que un rechazo
    genérico de Facturatech).
    """
    nit_emisor = solo_digitos(emisor.get('nit'))
    if not nit_emisor:
        raise ValueError('El emisor necesita un NIT configurado')
    if not items:
        raise ValueError('El documento necesita al menos una línea')
    prefijo, folio = _prefijo_y_folio(documento)
    adquirente = adquirente or {}
    moneda = documento.get('moneda') or 'COP'
    calculos = [_calcular_linea(item) for item in items]
    tipo = documento.get('tipo') or 'INVOIC'
    es_nota = tipo in ('NC', 'ND')
    if es_nota:
        documento = dict(documento)
        documento.setdefault('tipo_factura', '91' if tipo == 'NC' else '92')
        concepto = documento.get('concepto') or {}
        if not concepto.get('codigo'):
            raise ValueError('La nota necesita el concepto (CDN_1)')
        if not (documento.get('referencia') or {}).get('numero'):
            raise ValueError('La nota necesita la factura que referencia')
        anula = str(concepto['codigo']) == '2'
        documento.setdefault('tipo_operacion', '20' if anula else '22')

    raiz = ET.Element('NOTA' if es_nota else 'FACTURA')
    raiz.append(_encabezado(documento, adquirente, items, prefijo, folio, nit_emisor))
    if documento.get('pos') is not None:
        raiz.append(_punto_de_venta(documento, totales))
        if beneficio_comprador:
            raiz.append(_hijos(_nodo('BAC'), [
                ('BAC_1', beneficio_comprador.get('documento')),
                ('BAC_2', beneficio_comprador.get('nombre')),
                ('BAC_3', beneficio_comprador.get('puntos', 0)),
            ]))
    raiz.append(_emisor(emisor, nit_emisor))
    raiz.append(_adquirente(adquirente))
    raiz.append(_totales(calculos, totales, moneda))
    raiz.append(_impuestos_globales(calculos, moneda))
    raiz.append(_resolucion(resolucion, prefijo))
    if es_nota:
        ref = documento['referencia']
        raiz.append(_hijos(_nodo('REF'), [
            ('REF_1', ref.get('tipo') or 'IV'),
            ('REF_2', ref['numero']),
            ('REF_3', normalizar_fecha(ref['fecha_firma'])
             if ref.get('fecha_firma') else None),
            ('REF_4', ref.get('cufe')),
            ('REF_5', ref.get('algoritmo') or 'CUFE-SHA384'),
        ]))
    forma = documento.get('forma_pago') or '1'
    medio = documento.get('medio_pago') or '10'
    # Guía de factura (nov-2025): MEP_1 = medio (Tabla 5), MEP_2 = método (Tabla 26).
    # La guía POS conserva el orden anterior.
    mep = ([('MEP_1', forma), ('MEP_2', medio)] if documento.get('pos') is not None
           else [('MEP_1', medio), ('MEP_2', forma)])
    raiz.append(_hijos(_nodo('MEP'), mep))
    if es_nota:
        raiz.append(_hijos(_nodo('CDN'), [
            ('CDN_1', concepto['codigo']),
            ('CDN_2', concepto.get('descripcion')),
        ]))
    for indice, (item, calculo) in enumerate(zip(items, calculos), start=1):
        raiz.append(_linea(indice, item, calculo, moneda))
    return raiz


def a_bytes(raiz):
    return ET.tostring(raiz, encoding='utf-8', xml_declaration=True)
