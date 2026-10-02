"""Vuelca los XML reales que produce el sistema, para auditarlos.

Genera un documento de cada tipo contra una base temporal y los deja en disco.
No modifica nada real.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'herramientas'))

from ferreteria import db as db_mod  # noqa: E402
from ferreteria.services import (  # noqa: E402
    dian_emision,
    dian_notas,
    dian_xml,
)

SALIDA_XML = os.path.join(RAIZ, '_auditoria_xml')
NIT = '900187391'
DV = '2'
FECHA = '2026-10-01'
HORA = '10:30:00'


def _base():
    temporal = tempfile.mkdtemp(prefix='aud_')
    ruta = os.path.join(temporal, 'b.db')
    shutil.copy2(db_mod.DB_SEMILLA, ruta)
    conn = sqlite3.connect(ruta)
    cur = conn.cursor()
    for crear in (db_mod._crear_tablas_base, db_mod._crear_tablas_operacion,
                  db_mod._crear_tablas_alquiler, db_mod._crear_tablas_pedidos,
                  db_mod._crear_tablas_cotizaciones, db_mod._crear_tablas_dian):
        crear(cur)
    db_mod._aplicar_migraciones(cur)
    cur.execute("""
        UPDATE configuracion SET nombre='FERRETERIA AUDIT SA', nit=?, digito_verificacion=?,
        direccion='CALLE 1 # 2-3', codigo_municipio='11001', codigo_departamento='11',
        dian_ciiu='4665', dian_ciudad='Bogota D.C.',
        email_emisor='facturacion@audit.co', telefono='3001234567',
        regimen_fiscal='Responsable de IVA', responsabilidades='O-13',
        dian_ambiente='2', dian_modo='habilitacion', dian_prefijo='POS',
        dian_consecutivo=1, dian_emision_automatica=0,
        software_proveedor_nit='1054552590', software_proveedor_nombre='MANUEL STIBEN SUAREZ NARVAEZ'
        WHERE id=1
    """, (NIT, DV))
    # Resolucion de prueba con rango que cubre la fecha.
    cur.execute("""
        UPDATE series_dian SET prefijo='SETP', numero_resolucion='187640',
        rango_desde=1, rango_hasta=999999, fecha_vencimiento='2027-12-31'
        WHERE tipo_documento='POS'
    """)
    conn.commit()
    return ruta


def _ajustes():
    import ferreteria.config as config_mod
    import ferreteria.db as db_mod2
    ruta = _base()
    config_mod.DB_NAME = ruta
    db_mod2.DB_NAME = ruta
    os.environ['FERRETERIA_DB'] = ruta
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        UPDATE configuracion SET clave_tecnica='CT-PRUEBA', software_id='SW-PRUEBA',
        dian_software_security_code='PIN-PRUEBA', dian_test_set_id='TS-PRUEBA'
        WHERE id=1
    """)
    conn.commit()
    return conn, cur, ruta


def _guardar(nombre, texto, salida=None):
    destino = salida or SALIDA_XML
    os.makedirs(destino, exist_ok=True)
    ruta = os.path.join(destino, nombre)
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write(texto)
    print(f'  {nombre:34} {len(texto):6} bytes')
    return ruta


def generar(destino=None):
    """Genera los XML sin firmar y devuelve la carpeta donde quedaron.

    `destino` permite que la suite los genere en un temporal. Esa separacion es
    la que evita el falso verde: si el codigo cambia y nadie regenera, un
    verificador que lea el directorio del proyecto seguira comprobando el
    binario de la vez anterior y dira que todo esta bien.
    """
    salida = destino or SALIDA_XML
    _generar(salida)
    return salida


def _generar(salida):
    conn, cur, ruta = _ajustes()
    ajustes = dian_emision._leer_ajustes_dian(cur)
    emisor = {
        'nombre_comercial': 'Ferretería Audit', 'razon_social': 'FERRETERIA AUDIT SA',
        'nit': NIT, 'digito_verificacion': DV, 'direccion': 'CALLE 1 # 2-3',
        'municipio': '11001', 'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
        'telefono': '3001234567', 'email': 'facturacion@audit.co',
        'ciiu': '4665', 'ciudad': 'Bogotá D.C.',
        'prefijo': 'SETP',
    }
    cliente = {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'digito_verificacion': '9', 'nombre': 'CLIENTE EMPRESA SA',
        'direccion': 'AV 6 # 78-90', 'municipio': '11001', 'departamento': '11',
        'pais': 'CO', 'regimen_fiscal': 'Responsable de IVA',
        'responsabilidades': ['O-13'],
    }
    items = [
        {'descripcion': 'CEMENTO GRIS 50KG', 'cantidad': 2.0,
         'precio_unitario': 50000.0, 'unidad': '94', 'codigo': 'CE001',
         'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0, 'base': 100000.0},
        {'descripcion': 'TUBO PVC 1/2', 'cantidad': 3.0,
         'precio_unitario': 18000.0, 'unidad': '94', 'codigo': 'TU014',
         'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0, 'base': 54000.0},
    ]
    totales = {
        'line_extension_amount': 154000.0, 'tax_exclusive_amount': 154000.0,
        'tax_inclusive_amount': 183260.0, 'payable_amount': 183260.0,
        'iva_valor': 29260.0, 'inc_valor': 0,
        'impuestos_iva': [{'tasa': 19.0, 'base': 154000.0, 'valor': 29260.0}],
        'impuestos_inc': [],
    }
    extras = {'software_id': ajustes['software_id'],
              'software_security_code': ajustes['software_security_code']}

    def comun(numero):
        return {
            'numero': numero, 'fecha': FECHA, 'hora': HORA,
            'cuide': dian_xml.a_texto and 'PLACEHOLDER', 'tipo_ambiente': '2',
            'moneda': 'COP', 'tipo_operacion': '10', 'valor_total': 183260.0,
            'tipo_pago': 'efectivo', 'id_venta': 1,
            'tipo_documento': 'POS',
            'numero_resolucion': '187640', 'prefijo_resolucion': 'SETP',
            'rango_desde': 1, 'rango_hasta': 999999,
            'nombre_software': dian_emision.NOMBRE_SOFTWARE,
            'version_software': dian_emision.VERSION_SOFTWARE,
            'empresa_software': ajustes['software_proveedor_nombre'],
            'nit_proveedor_software': ajustes['software_proveedor_nit'],
        }

    from ferreteria.services.dian_pos import calcular_cuide

    def doc(numero, tipo_documento='POS'):
        d = comun(numero)
        d['tipo_documento'] = tipo_documento
        d['cuide'] = calcular_cuide(
            num_documento=numero, fecha=FECHA, hora=HORA, val_imp1=29260.0,
            val_imp2=0.0, val_total=183260.0, nit=NIT,
            tipo_documento=tipo_documento,
            clave_tecnica='CT-PRUEBA', tipo_ambiente='2')
        return d

    print('Generando XML de auditoría:')

    raiz = dian_xml.construir_invoice(doc('SETP-1'), emisor, cliente, items,
                                      totales, extras)
    _guardar('invoice.xml', dian_xml.a_texto(raiz), salida)

    raiz = dian_xml.construir_invoice(doc('SETP-2'), emisor, cliente,
                                      [items[0]], {
                                          'line_extension_amount': 100000.0,
                                          'tax_exclusive_amount': 100000.0,
                                          'tax_inclusive_amount': 119000.0,
                                          'payable_amount': 119000.0,
                                          'iva_valor': 19000.0, 'inc_valor': 0,
                                          'impuestos_iva': [{'tasa': 19.0,
                                                            'base': 100000.0,
                                                            'valor': 19000.0}],
                                          'impuestos_inc': []}, extras)
    _guardar('invoice_simple.xml', dian_xml.a_texto(raiz), salida)

    try:
        d = doc('SETP-NC-1', 'NC')
        # Los datos de la referencia van dentro del mismo dict `documento`:
        # es lo que lee `construir_nota_credito` (ver su docstring).
        d.update({'documento_referido': 1, 'numero_referido': 'SETP-1',
                  'cuide_referido': doc('SETP-1')['cuide'],
                  'fecha_referido': FECHA, 'motivo_codigo': '1',
                  'motivo_descripcion': 'Devolución total'})
        raiz = dian_notas.construir_nota_credito(
            d, emisor, cliente, items, totales, extras)
        _guardar('creditnote.xml', dian_xml.a_texto(raiz), salida)
    except Exception as e:
        print(f'  creditnote.xml  ERROR: {type(e).__name__}: {e}')

    try:
        raiz = dian_xml.construir_evento(doc('SETP-1')['cuide'], 'SETP-1', '030',
                                          'Acuse de recibo', emisor)
        _guardar('applicationresponse.xml', dian_xml.a_texto(raiz), salida)
    except Exception as e:
        print(f'  applicationresponse ERROR: {type(e).__name__}: {e}')

    print(f'\nEn: {salida}')
    conn.close()


def main():
    print(f'XML de auditoría en: {generar()}')


if __name__ == '__main__':
    main()

