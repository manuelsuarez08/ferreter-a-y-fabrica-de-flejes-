"""Reportes contables construidos sobre `asiento_detalle` y `asientos_contables`.

Es la cara visible del motor de `services.contabilidad`: el motor escribe los
asientos; este módulo los convierte en información para el contador.

REGLA DE ORO DE ESTOS REPORTES
------------------------------
Ningún reporte incluye asientos en estado 'borrador'. Los 'contabilizado' Y los
'anulado' SÍ se incluyen, y esto último es deliberado:

Un asiento 'anulado' NO es un asiento borrado. La anulación de una venta emite
un comprobante espejo ('NA') que invierte sus líneas, pero deja el original en el
libro con su marca. Si el reporte ocultara el original y mostrara solo la
reversión, el signo de la operación se invertiría: una venta anulada restaría en
vez de cancelarse (se veía un ingreso NEGATIVO).

Mostrando ambos, el original y su espejo se cancelan línea a línea y la venta
anulada desaparece del neto, que es el comportamiento correcto: el libro mayor
sigue siendo auditable (se ve el asiento y su reversión) pero el total es limpio.

Lo único que se excluye es 'borrador', que por definición no ha sido
contabilizado y no debe afectar ningún saldo.

Reportes incluidos:
    GET /api/contabilidad/libro-diario          asientos del período, con líneas
    GET /api/contabilidad/balance-comprobacion  saldos por cuenta (débito/crédito)
    GET /api/contabilidad/libro-mayor           movimientos de UNA cuenta
    GET /api/contabilidad/estado-resultados     ingresos, costos y gastos
    GET /api/contabilidad/balance-general       activo, pasivo y patrimonio
    GET /api/contabilidad/informe-iva           IVA generado y descontable
    GET /api/contabilidad/informe-retenciones   retenciones por tercero (NIT)
    GET /api/contabilidad/libro-iva             IVA documento por documento
    POST /api/contabilidad/cerrar-ejercicio     traspaso del resultado a patrimonio
    GET  /api/contabilidad/exportar/<reporte>   descarga CSV (Excel) del reporte
    GET /api/contabilidad/plan-cuentas          catálogo PUC
    GET /api/contabilidad/centros-costo         centros de costo

Todos exigen sesión; el catálogo de cuentas exige admin (es parametrización).
"""
from flask import Blueprint, jsonify, render_template, request, session
from flask import send_file
from io import BytesIO
import csv
import io

from ..db import get_db
from ..security import admin_required, login_required
from ..services import contabilidad as motor_contable

bp = Blueprint('contabilidad', __name__)


# ═════════════════════════════════════════════════════
# Utilidad de exportación a CSV/Excel
# ═════════════════════════════════════════════════════
def _csv_descarga(nombre, encabezados, filas):
    """Devuelve una respuesta de descarga CSV lista para Excel.

    DOS DECISIONES QUE IMPORTAN PARA QUE EXCEL LO ABRA BIEN:

    1. BOM UTF-8 (\ufeff). Sin él, Excel en Windows lee el archivo como ANSI y
       los acentos salen como mojibake ("ComprobaciÃ³n"): el contador abre el
       archivo y ve basura. Con el BOM, Excel respeta el UTF-8.

    2. Separador PUNTO Y COMA. En Excel configurado en español, la coma es el
       separador DECIMAL, así que un CSV separado por comas mete toda la fila en
       una sola columna. El punto y coma es lo que espera en esa configuración.
       Se usa `;` como delimitador explícito.

    Los números se emiten con punto decimal y sin separador de miles
    (1234.56), que es lo que Excel reconoce como número: si se formatearan con
    separador de miles, Excel los tomaría como texto y no se podrían sumar.
    """
    buffer = BytesIO()
    buffer.write('\ufeff'.encode('utf-8'))  # BOM
    # `csv.writer` escribe TEXTO, no bytes: se envuelve el buffer binario con un
    # envoltorio de texto. Pasar el BytesIO directo revienta con
    # "a bytes-like object is required, not 'str'".
    texto = io.TextIOWrapper(buffer, encoding='utf-8', newline='')
    escritor = csv.writer(texto, delimiter=';', lineterminator='\r\n',
                          quoting=csv.QUOTE_MINIMAL)
    escritor.writerow(encabezados)
    for fila in filas:
        escritor.writerow(fila)
    # OJO: hay que 'soltar' el envoltorio (detach) antes de leer el buffer, o el
    # buffer se queda sin volcar los datos pendientes y el archivo sale vacio.
    texto.flush()
    texto.detach()
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='text/csv; charset=utf-8',
        as_attachment=True,
        download_name=nombre,
    )


# Clases del PUC que son de RESULTADO (ingresos, gastos, costos). Se usan para
# el estado de resultados: el balance general es de las clases 1-3.
CLASES_RESULTADO = ('4', '5', '6', '7')
CLASES_BALANCE = ('1', '2', '3')


def _normalizar_periodo(periodo):
    """Rango (desde, hasta) a partir de un período 'YYYY-MM' o 'YYYY'."""
    periodo = str(periodo or '').strip()
    if len(periodo) >= 7 and periodo[4] == '-':
        anio, mes = periodo[:4], periodo[5:7]
        return f'{anio}-{mes}-01', f'{anio}-{mes}-31'
    if len(periodo) == 4 and periodo.isdigit():
        return f'{periodo}-01-01', f'{periodo}-12-31'
    # Sin período: mes actual, que es lo que el contador revisa a diario.
    from datetime import datetime
    hoy = datetime.now()
    return hoy.strftime('%Y-%m-01'), hoy.strftime('%Y-%m-%d')


def _rango_peticion():
    """Rango de fechas del query string (acepta `periodo` o desde/hasta)."""
    desde = request.args.get('desde')
    hasta = request.args.get('hasta')
    if desde or hasta:
        from datetime import datetime
        hoy = datetime.now()
        desde = desde or hoy.strftime('%Y-%m-01')
        hasta = hasta or hoy.strftime('%Y-%m-%d')
        return desde, hasta
    return _normalizar_periodo(request.args.get('periodo'))


# ═════════════════════════════════════════════════════
# Libro diario
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/libro-diario', methods=['GET'])
@login_required
def libro_diario():
    """Asientos contabilizados del período, con sus líneas de detalle.

    Devuelve también las líneas para ahorrar una segunda consulta: el libro
    diario se lee como una lista de comprobantes con su detalle debajo.
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        asientos = conn.execute(
            """
            SELECT id, tipo_comprobante, numero, fecha, periodo, descripcion,
                   origen, origen_id, total_debito, total_credito, usuario
            FROM asientos_contables
            WHERE estado IN ('contabilizado', 'anulado')
              AND fecha BETWEEN ? AND ?
            ORDER BY fecha, tipo_comprobante, numero, id
            """,
            (desde, hasta),
        ).fetchall()
        columnas = ('id', 'tipo_comprobante', 'numero', 'fecha', 'periodo',
                    'descripcion', 'origen', 'origen_id', 'total_debito',
                    'total_credito', 'usuario')
        resultado = []
        for fila in asientos:
            asiento = dict(zip(columnas, fila))
            lineas = conn.execute(
                """
                SELECT cuenta, tercero_tipo, tercero_numero, tercero_nombre,
                       id_centro_costo, descripcion, debito, credito, base_gravable
                FROM asiento_detalle
                WHERE id_asiento = ?
                ORDER BY id
                """,
                (asiento['id'],),
            ).fetchall()
            asiento['lineas'] = [
                {
                    'cuenta': l[0], 'tercero_tipo': l[1], 'tercero_numero': l[2],
                    'tercero_nombre': l[3], 'id_centro_costo': l[4],
                    'descripcion': l[5], 'debito': l[6], 'credito': l[7],
                    'base_gravable': l[8],
                }
                for l in lineas
            ]
            resultado.append(asiento)
        return jsonify({'desde': desde, 'hasta': hasta, 'asientos': resultado})
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Balance de comprobación
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/balance-comprobacion', methods=['GET'])
@login_required
def balance_comprobacion():
    """Saldos por cuenta de movimiento, con suma de débitos y créditos.

    Es el reporte que prueba que la contabilidad cuadra: la columna de saldo
    debe sumar cero (o, mirándolo por naturaleza, activos+... = pasivos+...).
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT d.cuenta, p.nombre, p.clase, p.naturaleza,
                   ROUND(SUM(d.debito), 2)  AS debitos,
                   ROUND(SUM(d.credito), 2) AS creditos
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            LEFT JOIN plan_cuentas p ON p.codigo = d.cuenta
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
            GROUP BY d.cuenta
            ORDER BY d.cuenta
            """,
            (desde, hasta),
        ).fetchall()

        cuentas = []
        total_debito = total_credito = 0.0
        for cuenta, nombre, clase, naturaleza, debitos, creditos in filas:
            debitos = float(debitos or 0)
            creditos = float(creditos or 0)
            total_debito += debitos
            total_credito += creditos
            # El saldo se expone EN LA NATURALEZA de la cuenta: una cuenta de
            # crédito (pasivo/ingreso) con más créditos que débitos tiene saldo
            # positivo. Sin esto, el contador tendría que interpretar signos.
            if naturaleza == 'credito':
                saldo = round(creditos - debitos, 2)
            else:
                saldo = round(debitos - creditos, 2)
            cuentas.append({
                'cuenta': cuenta, 'nombre': nombre, 'clase': clase,
                'naturaleza': naturaleza, 'debitos': debitos,
                'creditos': creditos, 'saldo': saldo,
            })

        return jsonify({
            'desde': desde, 'hasta': hasta, 'cuentas': cuentas,
            'total_debito': round(total_debito, 2),
            'total_credito': round(total_credito, 2),
            'cuadra': abs(total_debito - total_credito) <= 0.01,
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Libro mayor (por cuenta)
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/libro-mayor', methods=['GET'])
@login_required
def libro_mayor():
    """Movimientos de UNA cuenta en el período, con saldo acumulado.

    `cuenta` es obligatoria: el libro mayor sin cuenta no existe como reporte.
    """
    cuenta = str(request.args.get('cuenta') or '').strip()
    if not cuenta:
        return jsonify({'error': 'Debe indicar la cuenta (parámetro `cuenta`)'}), 400

    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        info = conn.execute(
            'SELECT nombre, clase, naturaleza FROM plan_cuentas WHERE codigo = ?',
            (cuenta,),
        ).fetchone()

        filas = conn.execute(
            """
            SELECT a.fecha, a.tipo_comprobante, a.numero, a.descripcion,
                   a.origen, a.origen_id,
                   d.tercero_numero, d.tercero_nombre, d.descripcion,
                   d.debito, d.credito
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
              AND d.cuenta = ?
            ORDER BY a.fecha, a.id, d.id
            """,
            (desde, hasta, cuenta),
        ).fetchall()

        movimientos = []
        saldo = 0.0
        signo = 1.0 if (info and info[2] == 'debito') else -1.0
        for (fecha, tipo, numero, desc_asiento, origen, origen_id,
             tercero_num, tercero_nom, desc_linea, debito, credito) in filas:
            debito = float(debito or 0)
            credito = float(credito or 0)
            saldo += signo * (debito - credito)
            movimientos.append({
                'fecha': fecha, 'tipo_comprobante': tipo, 'numero': numero,
                'descripcion': desc_linea or desc_asiento,
                'origen': origen, 'origen_id': origen_id,
                'tercero_numero': tercero_num, 'tercero_nombre': tercero_nom,
                'debito': debito, 'credito': credito,
                'saldo': round(saldo, 2),
            })

        return jsonify({
            'cuenta': cuenta,
            'nombre': info[0] if info else None,
            'clase': info[1] if info else None,
            'naturaleza': info[2] if info else None,
            'desde': desde, 'hasta': hasta,
            'saldo_final': round(saldo, 2),
            'movimientos': movimientos,
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Estado de resultados
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/estado-resultados', methods=['GET'])
@login_required
def estado_resultados():
    """Ingresos, costos y gastos del período, y la utilidad resultante.

    Solo mira las clases 4 (ingresos), 5 (gastos) y 6/7 (costos): el estado de
    resultados no incluye activos, pasivos ni patrimonio.
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT p.clase, d.cuenta, p.nombre, p.naturaleza,
                   ROUND(SUM(d.debito), 2), ROUND(SUM(d.credito), 2)
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            JOIN plan_cuentas p ON p.codigo = d.cuenta
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
              AND p.clase IN ('4', '5', '6', '7')
            GROUP BY d.cuenta
            ORDER BY d.cuenta
            """,
            (desde, hasta),
        ).fetchall()

        ingresos = []
        costos_gastos = []
        total_ingresos = total_costos = 0.0
        for clase, cuenta, nombre, naturaleza, debitos, creditos in filas:
            debitos = float(debitos or 0)
            creditos = float(creditos or 0)
            if naturaleza == 'credito':
                saldo = round(creditos - debitos, 2)
            else:
                saldo = round(debitos - creditos, 2)
            fila = {'cuenta': cuenta, 'nombre': nombre, 'clase': clase,
                    'saldo': saldo}
            if clase == '4':
                ingresos.append(fila)
                total_ingresos += saldo
            else:
                costos_gastos.append(fila)
                total_costos += saldo

        return jsonify({
            'desde': desde, 'hasta': hasta,
            'ingresos': ingresos,
            'costos_gastos': costos_gastos,
            'total_ingresos': round(total_ingresos, 2),
            'total_costos_gastos': round(total_costos, 2),
            'utilidad': round(total_ingresos - total_costos, 2),
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Balance general
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/balance-general', methods=['GET'])
@login_required
def balance_general():
    """Activo, pasivo y patrimonio a una fecha de corte (clases 1, 2 y 3).

    Es el complemento del estado de resultados: aquí viven las cuentas de BALANCE
    (lo que el negocio TIENE y DEBE), no las de resultado.

    Sobre la ecuación contable: si el ejercicio NO se ha cerrado, la utilidad del
    período todavía vive en las cuentas de resultado y hay que traerla al
    patrimonio (campo `resultado_ejercicio`) para que la ecuación cuadre. Si el
    ejercicio YA se cerró, el resultado está en las cuentas de patrimonio
    (3605/3606) y no debe volver a calcularse: por eso se EXCLUYEN del cálculo las
    líneas del asiento de cierre, que son precisamente las que dejan en cero las
    cuentas de resultado. Incluirlas invertiría el signo de la utilidad.
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT p.clase, d.cuenta, p.nombre, p.naturaleza,
                   ROUND(SUM(d.debito), 2), ROUND(SUM(d.credito), 2),
                   -- Marca si la cuenta SOLO recibió movimientos del asiento de
                   -- cierre. Sirve para no volver a contar como resultado lo que
                   -- el cierre ya traslado a patrimonio.
                   MAX(CASE WHEN a.origen = 'cierre_ejercicio' THEN 1 ELSE 0 END)
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            JOIN plan_cuentas p ON p.codigo = d.cuenta
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
            GROUP BY d.cuenta
            ORDER BY d.cuenta
            """,
            (desde, hasta),
        ).fetchall()

        activo, pasivo, patrimonio = [], [], []
        total_activo = total_pasivo = total_patrimonio = 0.0
        # Resultado del período (clases 4/5/6/7) que AÚN no está en patrimonio.
        # Solo se calcula si el ejercicio no se ha cerrado: una vez cerrado, el
        # resultado vive en las cuentas 3605/3606 y volver a sumarlo lo duplicaría.
        resultado = 0.0
        cerrado = False

        for clase, cuenta, nombre, naturaleza, debitos, creditos, del_cierre in filas:
            debitos = float(debitos or 0)
            creditos = float(creditos or 0)
            if naturaleza == 'credito':
                saldo = round(creditos - debitos, 2)
            else:
                saldo = round(debitos - creditos, 2)

            fila = {'cuenta': cuenta, 'nombre': nombre, 'clase': clase,
                    'saldo': saldo}
            if clase == '1':
                activo.append(fila)
                total_activo += saldo
            elif clase == '2':
                pasivo.append(fila)
                total_pasivo += saldo
            elif clase == '3':
                patrimonio.append(fila)
                total_patrimonio += saldo
            # Clases 4/5/6/7: resultado del ejercicio. PERO si la cuenta solo
            # tiene movimientos del asiento de cierre (del_cierre = 1), su saldo
            # es parte del traspaso y NO debe contar como resultado del período.
            elif del_cierre:
                continue
            elif clase == '4':
                resultado += saldo
            elif clase in ('5', '6', '7'):
                resultado -= saldo

        # ¿Hubo cierre? Se detecta por la cuenta 3605/3606 con movimientos.
        cerrado = any(c['cuenta'] in ('3605', '3606') for c in patrimonio)

        resultado = round(resultado, 2)
        # Si el ejercicio YA está cerrado, el resultado no se suma otra vez: las
        # cuentas de resultado quedaron en cero y el saldo ya está en 3605/3606.
        if not cerrado:
            total_patrimonio = round(total_patrimonio + resultado, 2)

        return jsonify({
            'desde': desde, 'hasta': hasta,
            'activo': activo, 'pasivo': pasivo, 'patrimonio': patrimonio,
            'total_activo': round(total_activo, 2),
            'total_pasivo': round(total_pasivo, 2),
            'total_patrimonio': total_patrimonio,
            'resultado_ejercicio': resultado,
            'cerrado': cerrado,
            # La ecuacion contable debe cumplirse; se expone para que el
            # contador lo verifique de un vistazo en la pantalla.
            'cuadra': abs(total_activo - (total_pasivo + total_patrimonio)) <= 0.01,
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Catálogo (parametrización)
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/plan-cuentas', methods=['GET'])
@login_required
def plan_cuentas():
    """Catálogo PUC completo (activas e inactivas), para poblar selects."""
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT codigo, nombre, clase, naturaleza, nivel, padre,
                   maneja_movimiento, requiere_tercero, requiere_centro, activo
            FROM plan_cuentas
            ORDER BY codigo
            """
        ).fetchall()
        return jsonify({'cuentas': [
            {
                'codigo': f[0], 'nombre': f[1], 'clase': f[2],
                'naturaleza': f[3], 'nivel': f[4], 'padre': f[5],
                'maneja_movimiento': bool(f[6]), 'requiere_tercero': bool(f[7]),
                'requiere_centro': bool(f[8]), 'activo': bool(f[9]),
            }
            for f in filas
        ]})
    finally:
        conn.close()


@bp.route('/api/contabilidad/centros-costo', methods=['GET'])
@login_required
def centros_costo():
    """Centros de costo (dimensión de costeo de las líneas del asiento)."""
    conn = get_db()
    try:
        filas = conn.execute(
            'SELECT id, codigo, nombre, descripcion, activo FROM centros_costo '
            'ORDER BY codigo'
        ).fetchall()
        return jsonify({'centros_costo': [
            {'id': f[0], 'codigo': f[1], 'nombre': f[2], 'descripcion': f[3],
             'activo': bool(f[4])}
            for f in filas
        ]})
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Informe de IVA
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/informe-iva', methods=['GET'])
@login_required
def informe_iva():
    """IVA generado (ventas) e IVA descontable (compras), por tarifa y período.

    Es el insumo del formulario 300 (declaración de IVA). Se construye desde
    `asiento_detalle`, no desde las ventas: así el informe respeta exactamente lo
    que se contabilizó (y no lo que se cobró, que puede diferir si algo se anuló
    después).

    Las líneas de IVA son las que se marcaron con `componente = 'iva'` en la
    parametrización: llevan la cuenta de impuesto (2404 generado / 240405
    descontable) Y la base gravable en `base_gravable`.

    Cómo se distingue generado de descontable:
      - Cuenta de PASIVO (clase 2) -> IVA GENERADO (ventas: se debe a la DIAN).
      - Cuenta de ACTIVO (clase 1)  -> IVA DESCONTABLE (compras: se puede restar).
    Es la naturaleza de la cuenta la que decide, no el nombre del evento.
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT p.clase, d.cuenta, p.nombre,
                   ROUND(SUM(d.base_gravable), 2) AS base,
                   ROUND(SUM(d.debito), 2)        AS debitos,
                   ROUND(SUM(d.credito), 2)       AS creditos
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            JOIN plan_cuentas p ON p.codigo = d.cuenta
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
              -- La cuenta debe participar en ALGUNA regla de impuesto. Se usa
              -- EXISTS y NO un JOIN: la misma cuenta de IVA aparece en varias
              -- reglas (venta efectivo, credito y la generica '*'), y un JOIN
              -- multiplicaria cada linea por el numero de reglas (el IVA salia
              -- triplicado). EXISTS solo pregunta ' participa?', sin duplicar.
              AND EXISTS (
                  SELECT 1 FROM parametrizacion_contable pc
                  WHERE pc.componente = 'iva'
                    AND (pc.cuenta_debito = d.cuenta OR pc.cuenta_credito = d.cuenta)
              )
            GROUP BY d.cuenta
            ORDER BY d.cuenta
            """,
            (desde, hasta),
        ).fetchall()

        generado, descontable = [], []
        total_generado = total_descontable = 0.0
        base_generada = base_descontable = 0.0

        for clase, cuenta, nombre, base, debitos, creditos in filas:
            base = float(base or 0)
            debitos = float(debitos or 0)
            creditos = float(creditos or 0)
            # El impuesto se reconoce en el lado que aumentó la cuenta:
            # generado (pasivo) crece al crédito; descontable (activo) al débito.
            if clase == '1':
                impuesto = round(debitos - creditos, 2)
                descontable.append({'cuenta': cuenta, 'nombre': nombre,
                                    'base': base, 'impuesto': impuesto})
                total_descontable += impuesto
                base_descontable += base
            else:
                impuesto = round(creditos - debitos, 2)
                generado.append({'cuenta': cuenta, 'nombre': nombre,
                                 'base': base, 'impuesto': impuesto})
                total_generado += impuesto
                base_generada += base

        return jsonify({
            'desde': desde, 'hasta': hasta,
            'generado': generado, 'descontable': descontable,
            'total_generado': round(total_generado, 2),
            'total_descontable': round(total_descontable, 2),
            'base_generada': round(base_generada, 2),
            'base_descontable': round(base_descontable, 2),
            # Saldo a pagar (positivo) o saldo a favor (negativo): es la cifra
            # que va al formulario y la primera que mira el contador.
            'saldo_a_pagar': round(total_generado - total_descontable, 2),
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════
# Informe de retenciones (por tercero/NIT)
# ═════════════════════════════════════════════════════════
@bp.route('/api/contabilidad/informe-retenciones', methods=['GET'])
@login_required
def informe_retenciones():
    """Retenciones practicadas A LA ferretería, agrupadas por tercero (NIT).

    Es el insumo de los certificados de retención y de la información exógena:
    el contador necesita el total por cada NIT que retuvo, no el detalle suelto.

    Se toman las líneas de las cuentas de anticipo (135515 retefuente, 135517
    reteIVA, 135518 reteICA). Que esas reglas lleven `requiere_tercero = 1` es lo
    que hace posible este informe: sin el documento del tercero, la línea no se
    podría atribuir a nadie.
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        # Totales por tipo de retención.
        por_concepto = conn.execute(
            """
            SELECT d.cuenta, p.nombre,
                   ROUND(SUM(d.debito - d.credito), 2) AS valor,
                   COUNT(*) AS lineas
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            JOIN plan_cuentas p ON p.codigo = d.cuenta
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
              AND d.cuenta IN ('135515', '135517', '135518')
            GROUP BY d.cuenta
            ORDER BY d.cuenta
            """,
            (desde, hasta),
        ).fetchall()

        # Detalle por tercero: es lo que se certifica a fin de año.
        por_tercero = conn.execute(
            """
            SELECT COALESCE(d.tercero_numero, '(sin documento)') AS documento,
                   COALESCE(d.tercero_nombre, '') AS nombre,
                   ROUND(SUM(CASE WHEN d.cuenta = '135515'
                                  THEN d.debito - d.credito ELSE 0 END), 2) AS retefuente,
                   ROUND(SUM(CASE WHEN d.cuenta = '135517'
                                  THEN d.debito - d.credito ELSE 0 END), 2) AS reteiva,
                   ROUND(SUM(CASE WHEN d.cuenta = '135518'
                                  THEN d.debito - d.credito ELSE 0 END), 2) AS reteica,
                   ROUND(SUM(d.debito - d.credito), 2) AS total
            FROM asiento_detalle d
            JOIN asientos_contables a ON a.id = d.id_asiento
            WHERE a.estado IN ('contabilizado', 'anulado')
              AND a.fecha BETWEEN ? AND ?
              AND d.cuenta IN ('135515', '135517', '135518')
            GROUP BY documento
            ORDER BY total DESC
            """,
            (desde, hasta),
        ).fetchall()

        # Se marca explícitamente si hay líneas SIN tercero: son las que
        # impedirían armar el certificado, y es mejor avisarlo que esconderlo.
        sin_documento = sum(1 for f in por_tercero if f[0] == '(sin documento)')

        return jsonify({
            'desde': desde, 'hasta': hasta,
            'por_concepto': [
                {'cuenta': f[0], 'nombre': f[1], 'valor': float(f[2]),
                 'lineas': f[3]}
                for f in por_concepto
            ],
            'por_tercero': [
                {'documento': f[0], 'nombre': f[1], 'retefuente': float(f[2]),
                 'reteiva': float(f[3]), 'reteica': float(f[4]),
                 'total': float(f[5])}
                for f in por_tercero
            ],
            'total_retenido': round(sum(float(f[5]) for f in por_tercero), 2),
            'terceros_sin_documento': sin_documento,
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Cierre de ejercicio
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/cerrar-ejercicio', methods=['POST'])
@admin_required
def cerrar_ejercicio():
    """Cierra un ejercicio: traspasa el resultado del período a patrimonio.

    Exige admin: es una operación que afecta el resultado del negocio y no debe
    poder dispararla un usuario de mostrador.

    Body (opcional): {"desde": "YYYY-01-01", "hasta": "YYYY-12-31"}. Si no se
    envía, se cierra el año de la fecha actual.
    """
    from datetime import datetime
    anio = datetime.now().year
    data = request.json or {}
    desde = str(data.get('desde') or f'{anio}-01-01')
    hasta = str(data.get('hasta') or f'{anio}-12-31')

    conn = get_db()
    try:
        try:
            resultado = motor_contable.cerrar_ejercicio(
                conn, desde=desde, hasta=hasta, usuario=session.get('usuario'))
        except motor_contable.ErrorContabilidad as e:
            conn.rollback()
            return jsonify({'error': str(e)}), 409
        if resultado is None:
            conn.rollback()
            return jsonify({
                'mensaje': 'No hay movimientos de resultado en el período: '
                           'no hay nada que cerrar.',
                'cerrado': False,
            }), 200
        conn.commit()
        return jsonify({
            'cerrado': True, 'id_asiento': resultado['id_asiento'],
            'numero': resultado['numero'], 'utilidad': resultado['utilidad'],
            'mensaje': (f"Cierre de ejercicio {hasta[:4]} registrado "
                        f"(asiento #{resultado['numero']}). Resultado: "
                        f"{'utilidad' if resultado['utilidad'] >= 0 else 'pérdida'} "
                        f"de {abs(resultado['utilidad']):.2f}."),
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Exportación a Excel/CSV
# ═════════════════════════════════════════════════════
# El contador trabaja en Excel: entrega los reportes en ese formato es lo que
# convierte esta pantalla en algo que realmente usa. Se expone un endpoint por
# reporte, con el mismo filtro de período que la versión JSON.
@bp.route('/api/contabilidad/exportar/<reporte>', methods=['GET'])
@login_required
def exportar(reporte):
    """Descarga un reporte en CSV (Excel): diario, comprobacion, mayor, iva, retenciones.

    La lógica de cada reporte se reutiliza llamando a su función y tomando
    `get_json()`: así el CSV y la pantalla muestran SIEMPRE las mismas cifras.
    Si se duplicara la consulta, tarde o temprano una cambiaría y no coincidiría.
    """
    desde, hasta = _rango_peticion()
    sufijo = f'{desde}_{hasta}'

    if reporte == 'diario':
        datos = libro_diario().get_json()
        filas = []
        for a in datos['asientos']:
            for l in a['lineas']:
                filas.append([
                    a['fecha'], a['tipo_comprobante'], a['numero'],
                    a['descripcion'], l['cuenta'],
                    l.get('tercero_numero') or '',
                    l.get('descripcion') or '',
                    f"{float(l['debito'] or 0):.2f}",
                    f"{float(l['credito'] or 0):.2f}",
                ])
        return _csv_descarga(
            f'libro_diario_{sufijo}.csv',
            ['Fecha', 'Comprobante', 'Numero', 'Descripcion', 'Cuenta',
             'Tercero', 'Detalle', 'Debito', 'Credito'], filas)

    if reporte == 'comprobacion':
        datos = balance_comprobacion().get_json()
        filas = [[c['cuenta'], c['nombre'] or '', c['clase'], c['naturaleza'] or '',
                  f"{c['debitos']:.2f}", f"{c['creditos']:.2f}", f"{c['saldo']:.2f}"]
                 for c in datos['cuentas']]
        filas.append(['', 'TOTALES', '', '', f"{datos['total_debito']:.2f}",
                      f"{datos['total_credito']:.2f}", ''])
        return _csv_descarga(
            f'balance_comprobacion_{sufijo}.csv',
            ['Cuenta', 'Nombre', 'Clase', 'Naturaleza',
             'Debitos', 'Creditos', 'Saldo'], filas)

    if reporte == 'mayor':
        cuenta = str(request.args.get('cuenta') or '').strip()
        if not cuenta:
            return jsonify({'error': 'Debe indicar la cuenta (parámetro `cuenta`)'}), 400
        datos = libro_mayor().get_json()
        filas = [[m['fecha'], m['tipo_comprobante'], m['numero'],
                  m['descripcion'] or '', m['tercero_numero'] or '',
                  f"{m['debito']:.2f}", f"{m['credito']:.2f}", f"{m['saldo']:.2f}"]
                 for m in datos['movimientos']]
        return _csv_descarga(
            f'libro_mayor_{cuenta}_{sufijo}.csv',
            ['Fecha', 'Comprobante', 'Numero', 'Descripcion', 'Tercero',
             'Debito', 'Credito', 'Saldo'], filas)

    if reporte == 'iva':
        datos = informe_iva().get_json()
        filas = []
        for c in datos['generado']:
            filas.append(['Generado', c['cuenta'], c['nombre'] or '',
                          f"{c['base']:.2f}", f"{c['impuesto']:.2f}"])
        for c in datos['descontable']:
            filas.append(['Descontable', c['cuenta'], c['nombre'] or '',
                          f"{c['base']:.2f}", f"{c['impuesto']:.2f}"])
        filas.append(['', '', 'TOTAL GENERADO', f"{datos['base_generada']:.2f}",
                      f"{datos['total_generado']:.2f}"])
        filas.append(['', '', 'TOTAL DESCONTABLE', f"{datos['base_descontable']:.2f}",
                      f"{datos['total_descontable']:.2f}"])
        filas.append(['', '', 'SALDO A PAGAR', '', f"{datos['saldo_a_pagar']:.2f}"])
        return _csv_descarga(
            f'informe_iva_{sufijo}.csv',
            ['Tipo', 'Cuenta', 'Nombre', 'Base', 'Impuesto'], filas)

    if reporte == 'libro_iva':
        datos = libro_iva().get_json()
        filas = [[d['fecha'], d['tipo_documento'], d['numero_dian'],
                  d['tipo_identificacion'], d['documento'], d['nombre'],
                  f"{d['tarifa']:.2f}", f"{d['base']:.2f}", f"{d['iva']:.2f}",
                  f"{d['retefuente']:.2f}", f"{d['reteica']:.2f}",
                  f"{d['total']:.2f}"]
                 for d in datos['documentos']]
        t = datos['totales']
        filas.append(['', '', '', '', '', 'TOTALES', '', f"{t['base']:.2f}",
                      f"{t['iva']:.2f}", f"{t['retefuente']:.2f}",
                      f"{t['reteica']:.2f}", f"{t['total']:.2f}"])
        return _csv_descarga(
            f'libro_iva_{sufijo}.csv',
            ['Fecha', 'TipoDoc', 'Numero', 'TipoID', 'Documento', 'Nombre',
             'Tarifa', 'Base', 'IVA', 'Retefuente', 'ReteICA', 'Total'],
            filas)

    if reporte == 'retenciones':
        datos = informe_retenciones().get_json()
        filas = [[t['documento'], t['nombre'], f"{t['retefuente']:.2f}",
                  f"{t['reteiva']:.2f}", f"{t['reteica']:.2f}", f"{t['total']:.2f}"]
                 for t in datos['por_tercero']]
        filas.append(['', 'TOTAL', '', '', '', f"{datos['total_retenido']:.2f}"])
        return _csv_descarga(
            f'retenciones_{sufijo}.csv',
            ['Documento', 'Nombre', 'Retefuente', 'ReteIVA', 'ReteICA', 'Total'],
            filas)

    return jsonify({'error': f'Reporte desconocido: {reporte}'}), 404


# ═════════════════════════════════════════════════════
# Libro de IVA (base para el formato DIAN)
# ═════════════════════════════════════════════════════
@bp.route('/api/contabilidad/libro-iva', methods=['GET'])
@login_required
def libro_iva():
    """IVA documento por documento (ventas), con base, impuesto y retenciones.

    IMPORTANTE — QUÉ ES Y QUÉ NO ES ESTE REPORTE
    --------------------------------------------
    NO es el archivo oficial del formato DIAN (1005/1006/1007). El formato exacto
    depende de la resolución vigente de cada año y de la lista de conceptos que la
    DIAN publica aparte; emitir un archivo con esa estructura SIN verificar contra
    la resolución del año sería entregar a la DIAN un documento potencialmente
    equivocado, que es peor que no entregarlo.

    LO QUE SÍ ES: el detalle documento por documento que EXIGE el formato, con los
    datos que la contabilidad de este sistema sustenta con certeza. El contador (o
    su software de exógena) lo mapea a los conceptos del año correspondiente.

    Solo incluye ventas VIGENTES: las anuladas quedan fuera, porque no se declaran.
    """
    desde, hasta = _rango_peticion()
    conn = get_db()
    try:
        filas = conn.execute(
            """
            SELECT v.id, v.fecha_dia, COALESCE(v.numero_dian, '') AS numero_dian,
                   COALESCE(v.tipo_documento_dian, 'POS') AS tipo_doc,
                   COALESCE(c.cedula_nit, '') AS documento,
                   COALESCE(c.nombre, '') AS nombre,
                   COALESCE(c.tipo_documento, 'CC') AS tipo_id,
                   COALESCE(v.subtotal_venta, 0) AS base,
                   COALESCE(v.iva_valor, 0) AS iva,
                   COALESCE(v.iva_porcentaje, 0) AS tarifa,
                   COALESCE(v.retencion_fuente, 0) AS retefuente,
                   COALESCE(v.retencion_ica, 0) AS reteica,
                   COALESCE(v.total_venta, 0) AS total
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.id_cliente
            WHERE v.fecha_dia BETWEEN ? AND ?
              AND COALESCE(v.anulada, 0) = 0
            ORDER BY v.fecha_dia, v.id
            """,
            (desde, hasta),
        ).fetchall()

        documentos = []
        totales = {'base': 0.0, 'iva': 0.0, 'retefuente': 0.0, 'reteica': 0.0,
                   'total': 0.0}
        for f in filas:
            documento = {
                'id_venta': f[0], 'fecha': f[1], 'numero_dian': f[2],
                'tipo_documento': f[3], 'documento': f[4], 'nombre': f[5],
                'tipo_identificacion': f[6],
                'base': float(f[7]), 'iva': float(f[8]), 'tarifa': float(f[9]),
                'retefuente': float(f[10]), 'reteica': float(f[11]),
                'total': float(f[12]),
            }
            for clave in ('base', 'iva', 'retefuente', 'reteica', 'total'):
                totales[clave] = round(totales[clave] + documento[clave], 2)
            documentos.append(documento)

        return jsonify({
            'desde': desde, 'hasta': hasta,
            'documentos': documentos,
            'totales': totales,
            'advertencia': (
                'Detalle para el formato DIAN de información exógena. No es el '
                'archivo oficial: hágalo coincidir con los conceptos de la '
                'resolución del año antes de presentarlo.'
            ),
        })
    finally:
        conn.close()


# ═════════════════════════════════════════════════════
# Vista amigable para el contador
# ═════════════════════════════════════════════════════
@bp.route('/contabilidad', methods=['GET'])
@login_required
def pagina():
    """Página de reportes contables que consume los endpoints JSON.

    Los endpoints por sí solos no son usables por un contador: esta vista arma
    las pestañas (balance de comprobación, resultados, balance general, diario y
    mayor) y el filtro de período en un solo lugar.
    """
    return render_template('contabilidad.html',
                           usuario=session.get('usuario'),
                           rol=session.get('rol', 'empleado'))


def registrar(app):
    app.register_blueprint(bp)
