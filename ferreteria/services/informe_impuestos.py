from decimal import Decimal, ROUND_HALF_UP


CAMPOS_VALOR = ('base_ventas', 'impuesto_ventas', 'base_compras', 'impuesto_compras')
CENTAVO = Decimal('0.01')


def dinero(valor):
    return Decimal(str(valor or 0)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def tarifa_estimada(tarifa, base, impuesto):
    if tarifa is not None and dinero(tarifa) > 0 and base:
        estimado = abs(base) * dinero(tarifa) / 100
        if abs(estimado - abs(impuesto)) <= Decimal('1.00'):
            return format(Decimal(str(tarifa)).normalize(), 'f')
    return 'sin-dato'


def agrupar_documentos(documentos):
    grupos = {}
    total = {campo: Decimal('0.00') for campo in CAMPOS_VALOR}
    for documento in documentos:
        tarifa = documento['tarifa']
        grupo = grupos.setdefault(tarifa, {
            'tarifa': tarifa,
            'documentos': [],
            'totales': {campo: Decimal('0.00') for campo in CAMPOS_VALOR},
        })
        for campo in CAMPOS_VALOR:
            if documento.get('sin_desglose') and campo in ('base_ventas', 'impuesto_ventas'):
                documento[campo] = None
                continue
            valor = dinero(documento.get(campo))
            documento[campo] = valor
            grupo['totales'][campo] += valor
            total[campo] += valor
        grupo['documentos'].append(documento)
    return {'grupos': list(grupos.values()), 'totales': total}


def consultar_impuestos(conn, desde, hasta, tercero='', tarifas=(), incluye_cierre=False):
    parametros = (desde, hasta, int(incluye_cierre))
    filas = conn.execute('''
        SELECT a.id, a.tipo_comprobante, a.numero, a.fecha, a.origen,
               v.numero_dian, v.iva_porcentaje, d.tercero_numero,
               d.tercero_nombre, c.cedula_nit, c.nombre, p.clase,
               d.debito, d.credito, d.base_gravable
        FROM asiento_detalle d
        JOIN asientos_contables a ON a.id = d.id_asiento
        JOIN plan_cuentas p ON p.codigo = d.cuenta
        LEFT JOIN ventas v ON v.id = a.origen_id AND
            (a.origen = 'venta' OR (a.origen = 'nota_credito'
             AND a.descripcion LIKE 'Reversión venta #%'))
        LEFT JOIN clientes c ON c.id = v.id_cliente
        WHERE a.estado IN ('contabilizado', 'anulado')
          AND a.fecha BETWEEN ? AND ?
          AND (? = 1 OR a.origen != 'cierre_ejercicio')
          AND EXISTS (SELECT 1 FROM parametrizacion_contable pc
                      WHERE pc.componente = 'iva' AND
                      (pc.cuenta_debito = d.cuenta OR pc.cuenta_credito = d.cuenta))
        ORDER BY a.fecha, a.id, d.id
    ''', parametros).fetchall()
    documentos = []
    for (asiento, tipo, numero, fecha, origen, numero_dian, tarifa_venta,
         tercero_numero, tercero_nombre, cliente_numero, cliente_nombre, clase,
         debito, credito, base) in filas:
        impuesto = dinero(debito) - dinero(credito) if clase == '1' else dinero(credito) - dinero(debito)
        base = dinero(base) * (-1 if impuesto < 0 else 1)
        tarifa = tarifa_estimada(tarifa_venta, base, impuesto)
        documentos.append({
            'asiento': asiento, 'comprobante': numero_dian if origen == 'venta' and numero_dian else f'{tipo}-{numero}',
            'identificacion': tercero_numero or cliente_numero or '',
            'nombre': tercero_nombre or cliente_nombre or '', 'sucursal': '',
            'fecha': fecha, 'tarifa': tarifa,
            'base_ventas': base if clase != '1' else 0,
            'impuesto_ventas': impuesto if clase != '1' else 0,
            'base_compras': base if clase == '1' else 0,
            'impuesto_compras': impuesto if clase == '1' else 0,
        })

    cero = conn.execute('''
        SELECT a.id, a.tipo_comprobante, a.numero, a.fecha, a.origen,
               v.numero_dian, c.cedula_nit, c.nombre, v.subtotal_venta
        FROM asientos_contables a
        JOIN ventas v ON v.id = a.origen_id AND
            (a.origen = 'venta' OR (a.origen = 'nota_credito'
             AND a.descripcion LIKE 'Reversión venta #%'))
        LEFT JOIN clientes c ON c.id = v.id_cliente
        WHERE a.estado IN ('contabilizado', 'anulado') AND a.fecha BETWEEN ? AND ?
          AND (? = 1 OR a.origen != 'cierre_ejercicio')
          AND COALESCE(v.iva_valor, 0) = 0
          AND NOT EXISTS (SELECT 1 FROM asiento_detalle d
                          JOIN parametrizacion_contable pc ON pc.componente = 'iva'
                           AND (pc.cuenta_debito = d.cuenta OR pc.cuenta_credito = d.cuenta)
                          WHERE d.id_asiento = a.id)
        ORDER BY a.fecha, a.id
    ''', parametros).fetchall()
    for asiento, tipo, numero, fecha, origen, numero_dian, identificacion, nombre, base in cero:
        documentos.append({
            'asiento': asiento, 'comprobante': numero_dian if origen == 'venta' and numero_dian else f'{tipo}-{numero}',
            'identificacion': identificacion or '', 'nombre': nombre or '', 'sucursal': '',
            'fecha': fecha, 'tarifa': '0',
            'base_ventas': dinero(base) * (-1 if origen == 'nota_credito' else 1),
        })

    busqueda = tercero.strip().casefold()
    historicas = conn.execute('''
        SELECT v.id, v.fecha_dia, v.numero_dian, c.cedula_nit, c.nombre,
               v.subtotal_venta, v.iva_valor, v.iva_porcentaje, v.total_venta
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.id_cliente
        WHERE v.fecha_dia BETWEEN ? AND ? AND COALESCE(v.anulada, 0) = 0
          AND NOT EXISTS (SELECT 1 FROM asientos_contables a
                          WHERE a.origen = 'venta' AND a.origen_id = v.id
                            AND a.estado IN ('contabilizado', 'anulado'))
        ORDER BY v.fecha_dia, v.id
    ''', (desde, hasta)).fetchall()
    sin_desglose = 0
    for (id_venta, fecha, numero, identificacion, nombre, base, impuesto,
         tarifa_venta, total) in historicas:
        identificacion, nombre = identificacion or '', nombre or ''
        if busqueda and busqueda not in identificacion.casefold() and busqueda not in nombre.casefold():
            continue
        base, impuesto, total = dinero(base), dinero(impuesto), dinero(total)
        if base < 0 or impuesto < 0 or (total > 0 and base == 0) or abs(base + impuesto - total) > Decimal('1.00'):
            if not tarifas or 'sin-dato' in tarifas:
                sin_desglose += 1
            documentos.append({
                'asiento': -id_venta, 'fuente': 'venta_sin_asiento', 'sin_desglose': True,
                'comprobante': numero or f'Venta #{id_venta}',
                'identificacion': identificacion, 'nombre': nombre, 'sucursal': '',
                'fecha': fecha, 'tarifa': 'sin-dato',
            })
            continue
        tarifa = '0' if impuesto == 0 else tarifa_estimada(tarifa_venta, base, impuesto)
        documentos.append({
            'asiento': -id_venta, 'fuente': 'venta_sin_asiento',
            'comprobante': numero or f'Venta #{id_venta}',
            'identificacion': identificacion, 'nombre': nombre, 'sucursal': '',
            'fecha': fecha, 'tarifa': tarifa, 'base_ventas': base, 'impuesto_ventas': impuesto,
        })

    documentos = [documento for documento in documentos
                  if (not busqueda or busqueda in documento['identificacion'].casefold()
                      or busqueda in documento['nombre'].casefold())
                  and (not tarifas or documento['tarifa'] in tarifas)]
    documentos.sort(key=lambda documento: (documento['fecha'], documento['asiento']))
    consolidados = {}
    for documento in documentos:
        clave = (documento['asiento'], documento['tarifa'], documento['identificacion'], documento['nombre'])
        if clave not in consolidados:
            consolidados[clave] = documento
        else:
            anterior = consolidados[clave]
            for campo in CAMPOS_VALOR:
                anterior[campo] = dinero(anterior.get(campo)) + dinero(documento.get(campo))
    informe = agrupar_documentos(consolidados.values())
    informe['ventas_sin_desglose'] = sin_desglose
    return informe


def etiqueta_tarifa(tarifa):
    return 'Tarifa no disponible' if tarifa == 'sin-dato' else f'IVA {tarifa}%'


def crear_excel(datos, filtros):
    from io import BytesIO
    from datetime import date
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    libro = Workbook()
    hoja = libro.active
    hoja.title = 'Impuestos detallados'
    hoja.append(['Informe impuestos detallados'])
    hoja.append([f"Período: {filtros['desde']} a {filtros['hasta']}"])
    hoja.append([f"Tipo: IVA | Tarifas: {', '.join(etiqueta_tarifa(t) for t in filtros['tarifas']) or 'Todas'}"])
    hoja.append([f"Tercero: {filtros['tercero'] or 'Todos'} | Incluye cierre: {'Sí' if filtros['incluye_cierre'] else 'No'}"])
    hoja.append([f"Tarifas estimadas. Ventas sin desglose excluidas de los totales: {datos.get('ventas_sin_desglose', 0)}."])
    hoja.append(['Comprobante', 'Identificación', 'Sucursal', 'Nombre tercero', 'Fecha elaboración',
                 'Base ventas', 'Valor impuesto ventas', 'Base compras', 'Valor impuesto compras'])
    for celda in hoja[6]:
        celda.fill = PatternFill('solid', fgColor='0B4F8A')
        celda.font = Font(color='FFFFFF', bold=True)
    def agregar_resumen(etiqueta, totales, color, nivel):
        hoja.append([etiqueta, '', '', '', ''] + [float(totales[campo]) for campo in CAMPOS_VALOR])
        hoja.row_dimensions[hoja.max_row].outlineLevel = nivel
        for celda in hoja[hoja.max_row]:
            celda.fill = PatternFill('solid', fgColor=color)
            celda.font = Font(bold=True)

    agregar_resumen('Tipo: IVA', datos['totales'], 'DCEEFB', 0)
    for grupo in datos['grupos']:
        agregar_resumen('Impuesto: ' + etiqueta_tarifa(grupo['tarifa']), grupo['totales'], 'EBF5FC', 1)
        for documento in grupo['documentos']:
            comprobante = documento['comprobante']
            if documento.get('fuente') == 'venta_sin_asiento':
                comprobante += ' (sin asiento)'
            hoja.append([comprobante, documento['identificacion'], documento['sucursal'],
                         documento['nombre'], date.fromisoformat(documento['fecha'])] +
                        [None if documento[campo] is None else float(documento[campo]) for campo in CAMPOS_VALOR])
            hoja.row_dimensions[hoja.max_row].outlineLevel = 2
            for celda in hoja[hoja.max_row][:4]:
                if celda.value is not None:
                    celda.data_type = 's'
            hoja.cell(hoja.max_row, 5).number_format = 'DD/MM/YYYY'
    for fila in hoja.iter_rows(min_row=7, min_col=6, max_col=9):
        for celda in fila:
            celda.number_format = '#,##0.00;[Red]-#,##0.00'
            celda.alignment = Alignment(horizontal='right')
    for columna in range(1, 10):
        ancho = max(len(str(hoja.cell(fila, columna).value or ''))
                    for fila in range(6, hoja.max_row + 1))
        hoja.column_dimensions[get_column_letter(columna)].width = min(max(ancho + 2, 15), 48)
    hoja.freeze_panes = 'F7'
    buffer = BytesIO()
    libro.save(buffer)
    buffer.seek(0)
    return buffer