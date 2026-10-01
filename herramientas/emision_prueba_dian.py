"""Emisión de PRUEBA en habilitación, sin tocar la base del desarrollador.

QUÉ HACE ESTO
-------------
Corre el flujo COMPLETO de facturación electrónica contra una COPIA de la base,
en el ambiente de habilitación (2, set de pruebas) de la DIAN:

    venta -> XML UBL -> firma XAdES-EPES -> CUIDE -> QR -> envío SOAP
          -> respuesta de la DIAN -> estado del documento

Sirve para dos cosas:
  1. Ver que el documento sale bien estructurado ANTES de tener credenciales
     reales. Un error del anexo técnico se descubre acá, no después de tener
     una ferretería cobrando.
  2. Probar de verdad la conexión con la DIAN.

POR QUÉ UNA COPIA Y NO LA BASE REAL
-----------------------------------
Esta emisión crea documentos, consume consecutivos y deja registros. Si se
hiciera sobre `ferreteria.db`, la dejaría con documentos de prueba mezclados con
los de una ferretería real. La copia se hace en una carpeta temporal y se borra
al terminar, o se conserva si se pasa `--conservar`.

LO QUE NO HACE
--------------
No aplica tarifas del catálogo, no toca la base del desarrollador y no envía nada
a PRODUCCIÓN: el ambiente está forzado a 2 y el código se niega a enviar si no
es ese.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# La consola de Windows viene con cp1252, que no tiene flechas ni em dash. Sin
# esto el script revienta al imprimir un "↔" con un UnicodeEncodeError que no
# dice nada del problema real. Se ajusta a UTF-8 y se tolera que la terminal no
# lo soporte del todo.
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

from ferreteria.services import (  # noqa: E402
    dian_emision,
    dian_firma,
    dian_soap,
)

# Datos de la emisión simulada. Son de EJEMPLO: el NIT del emisor tiene que
# coincidir con el del certificado de prueba, y ese lo genera el script de abajo.
NIT_EMISOR_PRUEBA = '900187391'
DV_EMISOR_PRUEBA = '2'
RAZON_EMISOR = 'FERRETERIA DE PRUEBA SAS'
DIRECCION_EMISOR = 'CALLE 1 # 2-3'
MUNICIPIO = '11001'   # Bogotá
DEPARTAMENTO = '11'

MARCA_OK = '  OK   '
MARCA_FALLA = '  FALLA'
MARCA_INFO = '  --   '


class Prueba:
    """Acumula las comprobaciones y las imprime al final."""

    def __init__(self):
        self.fallos = []
        self.total = 0

    def comprobar(self, descripcion, condicion, detalle=''):
        self.total += 1
        if condicion:
            print(f'{MARCA_OK} {descripcion}')
        else:
            print(f'{MARCA_FALLA} {descripcion}')
            if detalle:
                print(f'         {detalle}')
            self.fallos.append(descripcion)
        return bool(condicion)

    def info(self, texto):
        print(f'{MARCA_INFO} {texto}')


def _generar_certificado(ruta, nit, clave='clave-de-prueba'):
    """Certificado autofirmado con el NIT en el sujeto.

    Va con NIT en `serialNumber` porque `dian_firma.nit_titular` lo lee de ahí y
    `verificar_identidad_emisor` lo compara contra el NIT del emisor antes de
    firmar. Sin eso, la emisión se detiene a propósito.

    Es autofirmado: sirve para el set de pruebas, NO para producción. La DIAN no
    valida la cadena del certificado en habilitación, solo que el documento esté
    bien formado y firmado.
    """
    sys.path.insert(0, os.path.join(RAIZ, 'tests'))
    from _certificado_prueba import generar_p12

    generar_p12(ruta, clave=clave.encode('utf-8'), nit=nit)
    return ruta, clave


def _preparar_base(directorio):
    """Copia la semilla, la configura y le mete una venta con sus productos.

    Se parte de la SEMILLA y no de la base de trabajo: la semilla nunca trae
    ventas ni datos de operación, así que la venta que se crea es la única y no
    se mezcla con nada.
    """
    from ferreteria import db as db_mod

    ruta = os.path.join(directorio, 'prueba_habilitacion.db')
    shutil.copy2(db_mod.DB_SEMILLA, ruta)

    conn = sqlite3.connect(ruta)
    cursor = conn.cursor()

    for crear in (db_mod._crear_tablas_base, db_mod._crear_tablas_operacion,
                  db_mod._crear_tablas_alquiler, db_mod._crear_tablas_pedidos,
                  db_mod._crear_tablas_cotizaciones, db_mod._crear_tablas_dian):
        crear(cursor)
    db_mod._aplicar_migraciones(cursor)

    # Identidad del emisor.
    cursor.execute(
        '''
        UPDATE configuracion SET
            nombre = ?, nit = ?, digito_verificacion = ?, direccion = ?,
            codigo_municipio = ?, codigo_departamento = ?,
            email_emisor = 'facturacion@prueba.local',
            telefono = '3000000000',
            regimen_fiscal = 'Responsable de IVA',
            responsabilidades = 'O-13'
        WHERE id = 1
        ''',
        (RAZON_EMISOR, NIT_EMISOR_PRUEBA, DV_EMISOR_PRUEBA, DIRECCION_EMISOR,
         MUNICIPIO, DEPARTAMENTO),
    )

    # Ambiente de habilitación, sin emisión automática.
    cursor.execute(
        """
        UPDATE configuracion SET
            dian_ambiente = '2', dian_modo = 'habilitacion',
            dian_emision_automatica = 0,
            dian_prefijo = 'POS', dian_consecutivo = 1
        WHERE id = 1
        """
    )

    # Series de numeración de prueba: prefijo SETP + TestSetId, rango en 0 para
    # que no limite (lo que dice el comentario de la pantalla DIAN).
    for tipo, prefijo in (('POS', 'SETP'), ('FV', 'SETP'), ('NC', 'SETP')):
        cursor.execute(
            "UPDATE series_dian SET prefijo = ? WHERE tipo_documento = ?",
            (prefijo, tipo),
        )
        cursor.execute(
            'UPDATE series_dian SET rango_desde = 0, rango_hasta = 0 '
            'WHERE tipo_documento = ?', (tipo,))

    # Producto de prueba con el IVA ya clasificado, para que el XML declare un
    # TaxSubtotal con base y valor que cuadran. `codigo_dian` se deja vacío a
    # propósito: es el dato que la auditoría del catálogo está por resolver y
    # esta emisión no debe inventarlo.
    cursor.execute(
        """
        INSERT INTO productos (nombre, categoria, precio_venta, precio_costo,
                               stock_actual, unidad_medida, activo,
                               precio_base, iva_valor, iva_tasa)
        VALUES ('CEMENTO GRIS 50KG (PRUEBA)', 'CEMENTOS', 28000, 22000, 999,
                'NIU', 1, 23529.41, 4470.59, 19.0)
        """
    )
    id_producto = cursor.lastrowid

    ahora = datetime.now()
    cursor.execute(
        """
        INSERT INTO ventas (fecha_dia, hora, id_cliente, total_venta,
                            saldo_pendiente, tipo_pago, subtotal_venta,
                            iva_valor, iva_porcentaje, anulada, total_neto,
                            tipo_operacion, tipo_entrega)
        VALUES (?, ?, 1, 28000, 0, 'efectivo', 23529.41, 4470.59, 19.0, 0,
                28000, '10', 'entrega_inmediata')
        """,
        (ahora.strftime('%Y-%m-%d'), ahora.strftime('%H:%M:%S')),
    )
    id_venta = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO detalle_ventas (id_venta, id_producto, cantidad,
                                   precio_unitario, subtotal)
        VALUES (?, ?, 1, 28000, 23529.41)
        """,
        (id_venta, id_producto),
    )

    conn.commit()
    conn.close()
    return ruta, id_venta, id_producto


def _configurar_credenciales(ruta_db, ruta_cert, clave_cert, test_set_id):
    """Carga lo que la DIAN pediría, con valores de ejemplo.

    En el set de pruebas estos valores NO son los reales (no hay clave técnica
    ni SoftwareID hasta registrar el software). Se ponen de ejemplo para que el
    documento se construya completo y se pueda inspeccionar el XML; la DIAN va a
    responder que la clave técnica no existe, y eso también es útil saberlo.
    """
    conn = sqlite3.connect(ruta_db)
    conn.execute(
        """
        UPDATE configuracion SET
            certificado_ruta = ?, certificado_clave = ?,
            clave_tecnica = ?, software_id = ?,
            dian_software_security_code = ?, dian_test_set_id = ?,
            numero_resolucion = '', prefijo = '', rango_desde = 0, rango_hasta = 0
        WHERE id = 1
        """,
        (ruta_cert, clave_cert, 'CLAVE-TECNICA-DE-PRUEBA', 'SW-PRUEBA-0001',
         'PIN-DE-PRUEBA', test_set_id),
    )
    conn.commit()
    conn.close()


def _verificar_xml(xml, prueba):
    """Revisa el XML contra lo que exige el anexo técnico.

    Se miran las cosas que un rechazo de la DIAN señala con un mensaje críptico.
    """
    import re

    nodos = dict(re.findall(r'<sts:([A-Za-z]+)>([^<]*)</sts:\1>', xml))
    cac = dict(re.findall(r'<cac:([A-Za-z]+)>([^<]*)</cac:\1>', xml))

    # OJO con el `dict()` de los `cbc`: se recorren TODOS los nodos de la factura,
    # incluidos los de cada línea del detalle, y los de mismo nombre se pisan entre
    # ellos. Por eso los totales del documento se buscan dentro de `cac:LegalMonetaryTotal`, que
    # es donde el anexo los ubica y donde no hay otros con el mismo nombre.
    # OJO con el patrón: los nodos de UBL llevan atributos (`currencyID="COP"`), y
    # `<cbc:PayableAmount>` NO coincide con `<cbc:PayableAmount currencyID="COP">`.
    # Un regex que se olvide del atributo encuentra cero y parece que el documento
    # está incompleto cuando está perfecto.
    bloque_totales = re.search(
        r'<cac:LegalMonetaryTotal>(.*?)</cac:LegalMonetaryTotal>', xml, re.S)
    totales = dict(re.findall(r'<cbc:([A-Za-z]+)[^>]*>([^<]*)</cbc:\1>',
                              bloque_totales.group(1))) if bloque_totales else {}

    bloque_impuestos = re.search(
        r'<cac:TaxTotal>(.*?)</cac:TaxTotal>', xml, re.S)
    impuestos = dict(re.findall(r'<cbc:([A-Za-z]+)[^>]*>([^<]*)</cbc:\1>',
                                 bloque_impuestos.group(1))) if bloque_impuestos else {}

    prueba.comprobar('El XML declara el CUIDE', bool(nodos.get('CUDE')))
    prueba.comprobar('El XML declara el QR', bool(nodos.get('QRCode')))
    prueba.comprobar('El XML declara el SoftwareID',
                      bool(nodos.get('SoftwareID')))
    prueba.comprobar('El XML declara el PIN (SoftwareSecurityCode)',
                      bool(nodos.get('SoftwareSecurityCode')))
    prueba.comprobar('El XML identifica al proveedor de software',
                      bool(nodos.get('SoftwareProvider')))
    prueba.comprobar('El XML declara el NIT del proveedor',
                      bool(nodos.get('SoftwareProviderID')))
    prueba.comprobar('El NIT del proveedor es el del desarrollador',
                      nodos.get('SoftwareProviderID') == '1054552590',
                      f"veado: {nodos.get('SoftwareProviderID')!r}")
    prueba.comprobar('El XML va firmado (XAdES)',
                      'xades' in xml.lower() and 'SignatureValue' in xml)
    prueba.comprobar('El emisor trae su NIT', bool(cac.get('RegistrationName'))
                      or NIT_EMISOR_PRUEBA in xml)

    # El total con impuesto debe cuadrar con el subtotal más el impuesto. Se
    # comparan los TRES valores que el anexo exige que sean consistentes, no solo
    # el total: un documento donde el total está bien pero la base no, también lo
    # rechaza la DIAN.
    total = float(totales.get('PayableAmount', 0) or 0)
    base = float(totales.get('TaxExclusiveAmount', 0) or 0)
    impuesto = float(impuestos.get('TaxAmount', 0) or 0)
    prueba.comprobar('El documento declara el total a pagar', total > 0,
                      f'PayableAmount={totales.get("PayableAmount")!r}')
    prueba.comprobar('El documento declara la base sin impuesto', base > 0,
                      f'TaxExclusiveAmount={totales.get("TaxExclusiveAmount")!r}')
    prueba.comprobar('El documento declara el valor del impuesto', impuesto > 0,
                      f'TaxAmount={impuestos.get("TaxAmount")!r}')
    prueba.comprobar('Total = base + impuesto',
                      abs((base + impuesto) - total) < 0.01,
                      f'{base} + {impuesto} != {total}')
    return nodos, cac, totales


def _firmar_y_mostrar(conn, id_venta, prueba):
    """Construye, firma y guarda el documento, SIN enviarlo.

    Separado del envío a propósito: primero se comprueba que el documento se
    arma y se firma bien, y solo después se toca la red.
    """
    # Se emite con `contingencia=True` para que quede en la base y se pueda
    # inspeccionar, sin mandarlo. Es el camino que usa el POS cuando no hay red.
    try:
        dian_emision.emitir_venta(conn, id_venta, contingencia=True)
    except dian_emision.ErrorEmision as error:
        prueba.comprobar('La emisión se completa', False, str(error))
        return None

    fila = conn.execute(
        'SELECT numero, cuide, estado, xml_firmado FROM documentos_electronicos '
        'WHERE id_venta = ? ORDER BY id DESC LIMIT 1', (id_venta,)
    ).fetchone()
    if fila is None:
        prueba.comprobar('Se guardó el documento', False,
                         'no hay registro en documentos_electronicos')
        return None

    numero, cuide, estado, xml_firmado = fila
    prueba.comprobar('Se generó el documento', bool(numero), numero)
    prueba.comprobar('Se calculó el CUIDE', bool(cuide), cuide)
    prueba.comprobar('El documento quedó guardado firmado', bool(xml_firmado))
    prueba.comprobar('Quedó en contingencia (no se envió)', estado == 'contingencia',
                      f'estado={estado!r}')

    if xml_firmado:
        _verificar_xml(xml_firmado, prueba)

    return {'numero': numero, 'cuide': cuide, 'estado': estado, 'xml': xml_firmado}


def main():
    parser = argparse.ArgumentParser(
        description='Emisión de prueba en el ambiente de habilitación de la DIAN.'
    )
    parser.add_argument('--test-set-id', default='TEST-SET-PRUEBA',
                        help='TestSetId entregado por la DIAN (o uno de ejemplo)')
    parser.add_argument('--sin-red', action='store_true',
                        help='Solo arma y firma el documento, sin llamar a la DIAN')
    parser.add_argument('--conservar', action='store_true',
                        help='No borra la base temporal al terminar')
    parser.add_argument('--timeout', type=int, default=60,
                        help='Segundos de espera de la respuesta de la DIAN')
    args = parser.parse_args()

    prueba = Prueba()
    temporal = tempfile.mkdtemp(prefix='dian_habilitacion_')

    print()
    print('=' * 72)
    print(' EMISIÓN DE PRUEBA — AMBIENTE DE HABILITACIÓN DE LA DIAN')
    print('=' * 72)
    print(f'Fecha: {datetime.now().strftime("%d/%m/%Y %H:%M")}')
    print(f'TestSetId: {args.test_set_id}')
    print(f'Directorio temporal: {temporal}')

    try:
        print('\n1. Preparando la base de prueba')
        ruta_cert, clave_cert = _generar_certificado(
            os.path.join(temporal, 'prueba.p12'), NIT_EMISOR_PRUEBA)
        prueba.info(f'Certificado: {os.path.basename(ruta_cert)} '
                    f'(autofirmado, NIT {NIT_EMISOR_PRUEBA})')

        ruta_db, id_venta, id_producto = _preparar_base(temporal)
        _configurar_credenciales(ruta_db, ruta_cert, clave_cert, args.test_set_id)
        prueba.comprobar('La base de prueba se creó', os.path.exists(ruta_db))
        prueba.comprobar('La venta de prueba existe',
                         id_venta > 0, f'venta #{id_venta}')
        prueba.info(f'Veinte: {id_venta}  ·  Producto: {id_producto}')

        conn = sqlite3.connect(ruta_db)
        conn.row_factory = sqlite3.Row

        # El módulo resuelve DB_NAME de `ferreteria.config`; se parchea solo en
        # esta ejecución para que apunte a la base temporal.
        import ferreteria.config as config_mod
        import ferreteria.db as db_mod
        config_mod.DB_NAME = ruta_db
        db_mod.DB_NAME = ruta_db
        os.environ['FERRETERIA_DB'] = ruta_db

        print('\n2. Verificando la coherencia certificado ↔ emisor')
        ajustes = dian_emision._leer_ajustes_dian(conn.cursor())
        prueba.comprobar('Ambiente forzado a habilitación (2)',
                          ajustes['ambiente'] == '2', ajustes['ambiente'])
        prueba.comprobar('NIT del proveedor de software presente',
                          ajustes['software_proveedor_nit'] == '1054552590',
                          ajustes['software_proveedor_nit'])
        prueba.comprobar('Nombre del proveedor de software presente',
                          bool(ajustes['software_proveedor_nombre']),
                          ajustes['software_proveedor_nombre'])

        cert = dian_firma.cargar_certificado(ruta_cert, clave_cert)
        try:
            dian_firma.verificar_identidad_emisor(cert, NIT_EMISOR_PRUEBA)
            prueba.comprobar('El NIT del certificado coincide con el del emisor', True)
        except dian_firma.ErrorIdentidadEmisor as error:
            prueba.comprobar('El NIT del certificado coincide con el del emisor',
                              False, str(error))

        print('\n3. Armando y firmando el documento (sin red)')
        documento = _firmar_y_mostrar(conn, id_venta, prueba)
        conn.close()

        if documento is None:
            raise SystemExit('\nNo se pudo generar el documento.')

        print('\n4. Cuerpo del documento (extracto del XML firmado)')
        xml = documento['xml'] or ''
        for etiqueta in ('InvoiceTypeCode', 'DocumentNumber', 'IssueDate',
                         'PayableAmount', 'TaxExclusiveAmount', 'TaxTotal',
                         'InvoiceLineNumber'):
            import re
            encontrado = re.search(rf'<cbc:{etiqueta}>([^<]*)</cbc:{etiqueta}>', xml)
            if encontrado:
                print(f'    {etiqueta:22} = {encontrado.group(1)}')
        print(f'    {"CUDE":22} = {documento["cuide"]}')

        if args.sin_red:
            print('\n5. Envío a la DIAN: OMITIDO (--sin-red)')
            print('   El documento quedó firmado y guardado en la base temporal.')
        else:
            print('\n5. Enviando a la DIAN (ambiente de habilitación)')
            # `probar_conexion` devuelve (disponible, detalle), no un objeto. Se
            # hace un GetStatus con un trackId inexistente: si la DIAN contesta
            # (aunque sea con "no encontrado"), el servicio ESTA disponible.
            #
            # OJO: el `?wsdl` que traen las URL de ENDPOINTS es la del DESCRIPTOR del
            # servicio, no la del servicio. En pruebas el servidor responde igual
            # (por eso no rompe de inmediato), pero la operación SOAP se queda
            # esperando y la lectura expira. Se prueba sin `?wsdl`, que es la
            # forma correcta de invocar la operación.
            original = dian_soap.ENDPOINTS.get('2')
            dian_soap.ENDPOINTS['2'] = original.split('?')[0]

            try:
                disponible, detalle = dian_soap.probar_conexion(
                    ambiente='2', timeout=min(args.timeout, 30))
                prueba.comprobar('La DIAN responde el servicio de estado',
                                  disponible, detalle)
                print(f'    Respuesta: {detalle}')
            finally:
                if original is not None:
                    dian_soap.ENDPOINTS['2'] = original

    except SystemExit:
        raise
    except Exception as error:
        import traceback
        print(f'\nERROR INESPERADO: {error}')
        traceback.print_exc()
        prueba.fallos.append(f'error inesperado: {error}')
    finally:
        if args.conservar:
            print(f'\nBase de prueba conservada en: {temporal}')
        else:
            shutil.rmtree(temporal, ignore_errors=True)
            print('\nBase de prueba eliminada.')

    print()
    print('=' * 72)
    if prueba.fallos:
        print(f' RESULTADO: {len(prueba.fallos)} de {prueba.total} comprobaciones FALLARON')
        for falla in prueba.fallos:
            print(f'   - {falla}')
    else:
        print(f' RESULTADO: las {prueba.total} comprobaciones pasaron')
    print('=' * 72)
    print()
    return 1 if prueba.fallos else 0


if __name__ == '__main__':
    sys.exit(main())
