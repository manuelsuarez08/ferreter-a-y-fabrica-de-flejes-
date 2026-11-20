"""Prueba del generador del XML simplificado de Facturatech (tiquete POS).

Contrato fijado:
  1. Los nodos salen en el orden que exige el manual.
  2. ENC_6 = prefijo + folio, y el prefijo no puede terminar en número.
  3. Los totales cuadran: TOT_7 = base + impuestos.
  4. El descuento va en IDE y los impuestos en TII/IIM.
  5. Faltan datos mandatorios -> ValueError claro.
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ferreteria.services import facturatech_xml as fx  # noqa: E402

DOC = {'prefijo': 'SETT', 'folio': '1000001', 'tipo_ambiente': '2',
       'pos': {'placa': 'PLC1', 'ubicacion': 'Caja 1', 'cajero': 'Ana',
               'tipo_caja': 'TC01', 'codigo_venta': 'V1'}}
EMISOR = {'nit': '901.143.311', 'digito_verificacion': '8',
          'razon_social': 'Ftech Solutions SAS', 'direccion': 'Cll 32E',
          'municipio': '05001', 'departamento': '05', 'ciudad': 'Medellin',
          'nombre_departamento': 'Antioquia', 'prefijo': 'SETT',
          'responsabilidades': ['O-13']}
RESOLUCION = {'numero': '18760000001', 'fecha_inicio': '2019-01-19',
              'fecha_fin': '2030-01-19', 'desde': 1, 'hasta': 5000000}
ITEMS = [{'descripcion': 'Martillo', 'cantidad': 2, 'precio_unitario': 10000,
          'iva_tasa': 19, 'codigo': 'MAR1'}]
TOTALES = {'line_extension_amount': 20000, 'payable_amount': 23800}


def _construir(**cambios):
    args = dict(documento=DOC, emisor=EMISOR, adquirente={}, items=ITEMS,
                totales=TOTALES, resolucion=RESOLUCION)
    args.update(cambios)
    return fx.construir_factura(**args)


def test_orden_de_nodos():
    raiz = _construir()
    assert [n.tag for n in raiz] == [
        'ENC', 'IPV', 'EMI', 'ADQ', 'TOT', 'TIM', 'DRF', 'MEP', 'ITE']


def test_encabezado_y_emisor():
    raiz = _construir()
    assert raiz.findtext('ENC/ENC_6') == 'SETT1000001'
    assert raiz.findtext('ENC/ENC_2') == '901143311'
    assert raiz.findtext('ENC/ENC_3') == '222222222222'
    assert raiz.findtext('ENC/ENC_20') == '2'
    assert raiz.findtext('EMI/EMI_22') == '8'
    assert raiz.findtext('EMI/TAC/TAC_1') == 'O-13'
    assert raiz.findtext('DRF/DRF_4') == 'SETT'


def test_campos_de_comprador_y_pago_compatibles_con_dian():
    raiz = _construir(documento=dict(DOC, forma_pago='1', medio_pago='10'),
                      adquirente={'nombre': 'Juan Perez',
                                  'numero_documento': '123456789',
                                  'tipo_documento': 'CC'})
    assert raiz.find('ADQ/ADQ_8') is None
    assert raiz.find('ADQ/ADQ_9') is None
    assert raiz.find('ADQ/GTA') is None
    assert raiz.findtext('MEP/MEP_1') == '10'
    assert raiz.findtext('MEP/MEP_2') == '1'


def test_omite_correo_invalido_y_conserva_correo_valido():
    emisor = dict(EMISOR, email='correo-sin-dominio', telefono='6041234567')
    raiz = _construir(emisor=emisor,
                      adquirente={'nombre': 'Juan Perez', 'telefono': '3001234567'})
    assert raiz.find('EMI/CDE') is None
    assert raiz.find('ADQ/CDA') is None

    raiz = _construir(emisor=dict(EMISOR, email='facturas@example.com'))
    assert raiz.findtext('EMI/CDE/CDE_4') == 'facturas@example.com'


def test_direccion_fiscal_emisor_municipio_samana():
    emisor = dict(EMISOR, municipio='17665', departamento='17', ciudad='',
                  nombre_departamento='')
    municipio, departamento, ciudad, nombre_departamento = fx._direccion_fiscal(emisor)
    assert (municipio, departamento, ciudad, nombre_departamento) == (
        '17665', '17', 'Samaná', 'Caldas')
    raiz = _construir(emisor=emisor)
    assert raiz.findtext('EMI/EMI_13') == 'Samaná'
    assert raiz.findtext('EMI/EMI_19') == 'Caldas'
    assert raiz.findtext('EMI/DFE/DFE_6') == 'Caldas'


def test_totales_cuadran():
    raiz = _construir()
    assert raiz.findtext('TOT/TOT_1') == '20000.00'
    assert raiz.findtext('TOT/TOT_3') == '20000.00'
    assert raiz.findtext('TOT/TOT_7') == '23800.00'
    assert raiz.findtext('TIM/TIM_2') == '3800.00'
    assert raiz.findtext('TIM/IMP/IMP_4') == '3800.00'
    assert raiz.findtext('ITE/TII/IIM/IIM_2') == '3800.00'


def test_descuento_en_ide_y_base_neta():
    items = [dict(ITEMS[0], descuento=2000)]
    raiz = _construir(items=items)
    assert raiz.findtext('ITE/ITE_5') == '18000.00'
    assert raiz.findtext('TOT/TOT_1') == '18000.00'
    assert raiz.findtext('ITE/IDE/IDE_2') == '2000.00'
    assert raiz.findtext('ITE/IDE/IDE_6') == '10.00'
    assert raiz.findtext('ITE/IDE/IDE_7') == '20000.00'
    assert raiz.findtext('ITE/TII/IIM/IIM_4') == '18000.00'
    assert raiz.findtext('ITE/TII/IIM/IIM_2') == '3420.00'
    ite = raiz.find('ITE')
    orden = [n.tag for n in ite if n.tag in ('IAE', 'IDE', 'TII')]
    assert orden == ['IAE', 'IDE', 'TII']


def test_nota_credito_que_anula():
    doc = dict(DOC, prefijo='NCPS', folio='5', tipo='NC', pos=None,
               referencia={'numero': 'SETT1000001', 'fecha_firma': '2026-11-04',
                           'cufe': 'abc123'},
               concepto={'codigo': '2', 'descripcion': 'Anulación'})
    del doc['pos']
    raiz = _construir(documento=doc)
    assert raiz.tag == 'NOTA'
    assert raiz.findtext('ENC/ENC_1') == 'NC'
    assert raiz.findtext('ENC/ENC_9') == '91'
    assert raiz.findtext('ENC/ENC_21') == '20'
    assert raiz.findtext('REF/REF_1') == 'IV'
    assert raiz.findtext('REF/REF_2') == 'SETT1000001'
    assert raiz.findtext('REF/REF_5') == 'CUFE-SHA384'
    assert raiz.findtext('CDN/CDN_1') == '2'
    tags = [n.tag for n in raiz]
    assert tags.index('DRF') < tags.index('REF') < tags.index('MEP') \
        < tags.index('CDN') < tags.index('ITE')


def test_nota_sin_referencia_ni_concepto():
    for cambios in ({'concepto': {}}, {'referencia': {}}):
        doc = dict(DOC, tipo='NC', referencia={'numero': 'X1'},
                   concepto={'codigo': '2'})
        doc.update(cambios)
        try:
            _construir(documento=doc)
        except ValueError:
            continue
        raise AssertionError(f'{cambios} debio fallar')


def test_prefijo_obligatorio_y_sin_numero_final():
    for prefijo in ('', 'SETT1'):
        try:
            _construir(documento=dict(DOC, prefijo=prefijo))
        except ValueError:
            continue
        raise AssertionError(f'prefijo {prefijo!r} debio fallar')


def test_datos_faltantes():
    for cambios in ({'items': []}, {'emisor': dict(EMISOR, nit='')},
                    {'resolucion': {}}):
        try:
            _construir(**cambios)
        except ValueError:
            continue
        raise AssertionError(f'{cambios} debio fallar')


def test_xml_bien_formado():
    datos = fx.a_bytes(_construir(items=[dict(ITEMS[0], descripcion='A & B <x>')]))
    assert ET.fromstring(datos).findtext('ITE/ITE_11') == 'A & B <x>'


if __name__ == '__main__':
    pruebas = [f for n, f in sorted(globals().items()) if n.startswith('test_')]
    for prueba in pruebas:
        prueba()
        print('ok', prueba.__name__)
    print(f'{len(pruebas)}/{len(pruebas)} pasaron')
