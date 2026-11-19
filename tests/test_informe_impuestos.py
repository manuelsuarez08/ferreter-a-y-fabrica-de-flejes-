import unittest
from decimal import Decimal
from io import BytesIO
import os
import sqlite3
import tempfile
from unittest.mock import patch

from flask import Flask
from openpyxl import load_workbook

from ferreteria.blueprints.contabilidad import bp
from ferreteria.services.informe_impuestos import agrupar_documentos, consultar_impuestos


class InformeImpuestosTest(unittest.TestCase):
    def test_agrupa_y_suma_sin_error_binario(self):
        informe = agrupar_documentos([
            {'tarifa': '19', 'base_ventas': '0.10', 'impuesto_ventas': '0.02'},
            {'tarifa': '19', 'base_ventas': '0.20', 'impuesto_ventas': '0.04'},
            {'tarifa': '5', 'base_compras': '10.00', 'impuesto_compras': '0.50'},
        ])
        self.assertEqual(informe['totales']['base_ventas'], Decimal('0.30'))
        self.assertEqual(informe['grupos'][0]['totales']['impuesto_ventas'], Decimal('0.06'))
        self.assertEqual(informe['totales']['impuesto_compras'], Decimal('0.50'))


class ConsultaImpuestosTest(unittest.TestCase):
    def setUp(self):
        self.directorio = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self.directorio.name, 'prueba.db')
        self.conn = sqlite3.connect(self.ruta)
        self.conn.executescript('''
            CREATE TABLE clientes (id INTEGER, cedula_nit TEXT, nombre TEXT);
            CREATE TABLE ventas (id INTEGER, id_cliente INTEGER, numero_dian TEXT,
                                 iva_porcentaje REAL, iva_valor REAL, subtotal_venta REAL,
                                 fecha_dia TEXT, anulada INTEGER DEFAULT 0, total_venta REAL);
            CREATE TABLE asientos_contables (id INTEGER, tipo_comprobante TEXT, numero INTEGER,
                fecha TEXT, origen TEXT, origen_id INTEGER, descripcion TEXT, estado TEXT);
            CREATE TABLE plan_cuentas (codigo TEXT, clase TEXT);
            CREATE TABLE asiento_detalle (id INTEGER, id_asiento INTEGER, cuenta TEXT,
                tercero_numero TEXT, tercero_nombre TEXT, debito REAL, credito REAL, base_gravable REAL);
            CREATE TABLE parametrizacion_contable (componente TEXT, cuenta_debito TEXT, cuenta_credito TEXT);
            INSERT INTO clientes VALUES (1, '901513285', 'Cliente Prueba');
            INSERT INTO ventas VALUES (1, 1, 'FV-4-597', 19, 190, 1000, '2026-11-03', 1, 1190),
                                      (2, 1, 'FV-4-598', 0, 0, 250, '2026-11-05', 0, 250),
                                      (3, 1, 'FV-4-599', 19, 125, 1000, '2026-11-07', 0, 1125);
            INSERT INTO plan_cuentas VALUES ('2404', '2'), ('1355', '1');
            INSERT INTO parametrizacion_contable VALUES ('iva', NULL, '2404'),
                ('iva', NULL, '2404'), ('iva', '1355', NULL);
            INSERT INTO asientos_contables VALUES
                (1, 'CI', 1, '2026-11-03', 'venta', 1, 'Venta #1', 'anulado'),
                (2, 'NA', 1, '2026-11-04', 'nota_credito', 1, 'Reversión venta #1', 'contabilizado'),
                (3, 'CI', 2, '2026-11-05', 'venta', 2, 'Venta #2', 'contabilizado'),
                (4, 'CE', 1, '2026-11-06', 'compra', 1, 'Compra', 'contabilizado'),
                (5, 'CI', 3, '2026-11-07', 'venta', 3, 'Venta #3', 'contabilizado'),
                (6, 'CT', 1, '2026-11-08', 'cierre_ejercicio', NULL, 'Cierre', 'contabilizado'),
                (7, 'CT', 2, '2026-11-09', 'manual', NULL, 'Borrador', 'borrador');
            INSERT INTO asiento_detalle VALUES
                (1, 1, '2404', NULL, NULL, 0, 95, 500),
                (2, 2, '2404', NULL, NULL, 190, 0, 1000),
                (3, 4, '1355', '123', 'Proveedor Prueba', 50, 0, 1000),
                (4, 5, '2404', NULL, NULL, 0, 125, 1000),
                (5, 6, '2404', NULL, NULL, 0, 19, 100),
                (6, 7, '2404', NULL, NULL, 0, 19, 100),
                (7, 1, '2404', NULL, NULL, 0, 95, 500);
        ''')
        self.conn.commit()
        self.app = Flask(__name__, template_folder='../templates')
        self.app.secret_key = 'test'
        self.app.register_blueprint(bp)
        self.cliente = self.app.test_client()
        with self.cliente.session_transaction() as sesion:
            sesion['usuario'] = 'prueba'
        self.parche = patch('ferreteria.blueprints.contabilidad.get_db',
                            side_effect=lambda: sqlite3.connect(self.ruta))
        self.parche.start()

    def tearDown(self):
        self.parche.stop()
        self.conn.close()
        self.directorio.cleanup()

    def test_movimientos_reversion_filtros_y_cierre(self):
        informe = consultar_impuestos(self.conn, '2026-11-01', '2026-11-30')
        self.assertEqual(informe['totales']['base_ventas'], Decimal('1250.00'))
        self.assertEqual(informe['totales']['impuesto_ventas'], Decimal('125.00'))
        self.assertEqual(informe['totales']['base_compras'], Decimal('1000.00'))
        self.assertEqual(informe['totales']['impuesto_compras'], Decimal('50.00'))
        grupos = {grupo['tarifa']: grupo for grupo in informe['grupos']}
        self.assertEqual(grupos['19']['totales']['base_ventas'], Decimal('0.00'))
        self.assertEqual(grupos['0']['totales']['base_ventas'], Decimal('250.00'))
        self.assertEqual(grupos['sin-dato']['totales']['impuesto_ventas'], Decimal('125.00'))
        tercero = consultar_impuestos(self.conn, '2026-11-01', '2026-11-30', 'proveedor')
        self.assertEqual(tercero['totales']['impuesto_compras'], Decimal('50.00'))
        filtrado = consultar_impuestos(self.conn, '2026-11-01', '2026-11-30', tarifas=['19'])
        self.assertEqual(len(filtrado['grupos'][0]['documentos']), 2)
        cierre = consultar_impuestos(self.conn, '2026-11-01', '2026-11-30', incluye_cierre=True)
        self.assertEqual(cierre['totales']['impuesto_ventas'], Decimal('144.00'))

    def test_venta_historica_sin_asiento_aparece_sin_duplicar_ni_inventar_iva(self):
        self.conn.executemany('INSERT INTO ventas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            (4, 1, 'FV-4-600', 19, 190, 1000, '2026-11-10', 0, 1190),
            (5, 1, 'FV-4-601', 19, 190, 1000, '2026-11-11', 1, 1190),
            (6, 1, 'FV-4-602', 0, 0, 0, '2026-11-12', 0, 1190),
        ])
        self.conn.commit()
        informe = consultar_impuestos(self.conn, '2026-11-01', '2026-11-30')
        self.assertEqual(informe['totales']['base_ventas'], Decimal('2250.00'))
        self.assertEqual(informe['totales']['impuesto_ventas'], Decimal('315.00'))
        documentos = [documento for grupo in informe['grupos'] for documento in grupo['documentos']]
        historico = next(documento for documento in documentos if documento['comprobante'] == 'FV-4-600')
        self.assertEqual(historico['fuente'], 'venta_sin_asiento')
        self.assertNotIn('FV-4-601', [documento['comprobante'] for documento in documentos])
        incompleto = next(documento for documento in documentos if documento['comprobante'] == 'FV-4-602')
        self.assertTrue(incompleto['sin_desglose'])
        self.assertIsNone(incompleto['base_ventas'])
        self.assertIsNone(incompleto['impuesto_ventas'])
        self.assertEqual(informe['ventas_sin_desglose'], 1)

    def test_api_y_excel_aplican_los_mismos_filtros(self):
        query = '?desde=2026-11-01&hasta=2026-11-30&tercero=Proveedor'
        respuesta = self.cliente.get('/api/contabilidad/impuestos-detallados' + query)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json['totales']['impuesto_compras'], '50.00')
        excel = self.cliente.get('/api/contabilidad/impuestos-detallados.xlsx' + query)
        self.assertEqual(excel.status_code, 200)
        hoja = load_workbook(BytesIO(excel.data)).active
        self.assertEqual(hoja['I7'].value, 50)
        self.assertEqual(hoja['D9'].value, 'Proveedor Prueba')
        self.assertEqual(hoja['I9'].number_format, '#,##0.00;[Red]-#,##0.00')
        self.assertEqual(hoja.row_dimensions[9].outlineLevel, 2)
        self.assertEqual(self.cliente.get('/api/contabilidad/impuestos-detallados?desde=2026-02-31').status_code, 400)
        self.assertEqual(self.cliente.get('/contabilidad/impuestos-detallados').status_code, 200)
        terceros = self.cliente.get('/api/contabilidad/impuestos-detallados/terceros?q=proveedor')
        self.assertEqual(terceros.json, [{'nombre': 'Proveedor Prueba', 'identificacion': '123'}])

    def test_api_y_excel_muestran_ventas_historicas_incompletas(self):
        self.conn.executemany('INSERT INTO ventas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', [
            (4, 1, 'FV-4-600', 19, 190, 1000, '2026-11-10', 0, 1190),
            (6, 1, 'FV-4-602', 0, 0, 0, '2026-11-12', 0, 1190),
        ])
        self.conn.commit()
        query = '?desde=2026-11-01&hasta=2026-11-30&tercero=901513285'
        respuesta = self.cliente.get('/api/contabilidad/impuestos-detallados' + query)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json['ventas_sin_desglose'], 1)
        self.assertEqual(respuesta.json['totales']['impuesto_ventas'], '315.00')
        excel = self.cliente.get('/api/contabilidad/impuestos-detallados.xlsx' + query)
        hoja = load_workbook(BytesIO(excel.data)).active
        self.assertIn('excluidas de los totales: 1', hoja['A5'].value)
        filas = {fila[0].value: fila for fila in hoja.iter_rows(min_row=7)}
        self.assertEqual(filas['FV-4-600 (sin asiento)'][6].value, 190)
        self.assertIsNone(filas['FV-4-602 (sin asiento)'][5].value)
        self.assertIsNone(filas['FV-4-602 (sin asiento)'][6].value)
        filtrado = self.cliente.get('/api/contabilidad/impuestos-detallados' + query + '&tarifa=19')
        self.assertEqual(filtrado.json['ventas_sin_desglose'], 0)
        self.assertEqual(filtrado.json['totales']['impuesto_ventas'], '190.00')

    def test_sin_sesion(self):
        cliente = self.app.test_client()
        self.assertEqual(cliente.get('/api/contabilidad/impuestos-detallados').status_code, 401)
        self.assertEqual(cliente.get('/api/contabilidad/impuestos-detallados.xlsx').status_code, 401)


if __name__ == '__main__':
    unittest.main()