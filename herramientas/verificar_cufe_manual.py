"""Compara el CUFE/CUDE de los XML reales contra el hash calculado a mano.

No usa ninguna función del proyecto: arma la cadena a mano y la hashea con
hashlib. Si los dos valores coinciden, el generador está correcto de extremo a
extremo, desde la cadena hasta el nodo cbc:UUID del XML.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / '_auditoria_xml'

NS = {
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
}

# Datos de la auditoría. NO se importan del proyecto a propósito: este
# comprobador tiene que ser independiente, si no termina creyendo al código
# que está comprobando.
AMBIENTE = '2'
CLAVE_TECNICA = 'CT-PRUEBA'
NIT = '900187391'
TIPO_DOC = {'SETP-1': 'POS', 'SETP-2': 'POS', 'SETP-NC-1': 'NC'}


def _texto(raiz, xpath):
    nodo = raiz.find(xpath, NS)
    return nodo.text if nodo is not None else None


def _impuestos(raiz):
    """IVA e INC leídos del propio documento, por su código de esquema.

    El esquema del tributo cuelga de `cac:TaxSubtotal/cac:TaxCategory`, NO de
    `cac:TaxTotal`: en `TaxTotal` solo está el RESUMEN y no lleva TaxScheme.
    Confundir los dos deja el valor en 0.00 y hace que el comprobador reporte un
    CUFE inválido que en realidad es correcto: el fallo del comprobador se ve
    como fallo del código, que es peor.
    """
    iva = inc = 0.0
    # Solo el TaxTotal DEL DOCUMENTO, que es hijo directo de la raíz. Cada
    # `cac:InvoiceLine` trae el suyo, y sumarlos todos duplica el IVA (aquí daba
    # 29260 + 29260 = 58520), lo que hacía que el comprobadoryb此外，
    # `cac:InvoiceLine` también trae su propio `TaxTotal`.
    for total in raiz.findall('cac:TaxTotal', NS):
        for subtotal in total.findall('cac:TaxSubtotal', NS):
            esquema = _texto(subtotal, 'cac:TaxCategory/cac:TaxScheme/cbc:ID')
            importe = _texto(subtotal, 'cbc:TaxAmount')
            if not importe:
                continue
            if esquema == '01':
                iva += float(importe)
            elif esquema == '04':
                inc += float(importe)
    return iva, inc


def cadena_de(raiz):
    """Arma la cadena del CUFE/CUDE con los datos del PROPIO documento.

    Cada documento tiene su número, su fecha y su total, así que el valor
    esperado no puede estar fijo: si lo estuviera, el comprobador solo
    validaría un documento y daría falsa confianza sobre los demás. Lo que se
    pone a prueba es el paso de los datos del documento al hash.

    Lo que NO se comprueba aquí: que el orden de los campos sea el que exige el
    Suplemento. Eso es una regla externa; este archivo solo verifica que el
    código aplique lo que dice hacer.
    """
    numero = _texto(raiz, './/cbc:ID')
    fecha = _texto(raiz, './/cbc:IssueDate')
    # Se toma solo la hora: el -05:00 NO va en la cadena que se hashea.
    hora = (_texto(raiz, './/cbc:IssueTime') or '')[:8]
    iva, inc = _impuestos(raiz)
    total = float(_texto(raiz, './/cac:LegalMonetaryTotal/cbc:PayableAmount')
                  or '0')
    return ''.join((
        numero or '',
        fecha or '',
        hora,
        f'{iva:.2f}',
        f'{inc:.2f}',
        f'{total:.2f}',
        NIT,
        TIPO_DOC.get(numero, 'POS'),
        CLAVE_TECNICA,
        AMBIENTE,
    ))


def uuid_de(raiz):
    nodo = raiz.find('.//cbc:UUID', NS)
    if nodo is None:
        return (None, None)
    return (nodo.get('schemeName'), nodo.text)


def main():
    fallos = 0

    for nombre, es_evento in (('invoice.xml', False),
                              ('creditnote.xml', False),
                              ('applicationresponse.xml', True)):
        ruta = SALIDA / nombre
        if not ruta.exists():
            print(f'{nombre}: NO EXISTE')
            fallos += 1
            continue

        raiz = ET.parse(ruta).getroot()
        esquema, valor = uuid_de(raiz)
        issue = _texto(raiz, './/cbc:IssueTime')

        # En un ApplicationResponse el `cbc:UUID` ES el CUIDE del documento al
        # que se refiere el evento, no un hash propio: no se puede verificar
        # contra una cadena y no se intenta. El CUDE del evento vive en la nota
        # de referencia, que se comprueba aparte.
        if es_evento:
            print(nombre)
            print('   UUID (CUIDE del documento referido):', valor)
            print('   IssueTime :', issue)
            print('   COINCIDE  : no aplica (el UUID ES el CUIDE del POS)')
            if not valor:
                print('   >>> el evento no trae el UUID/CUIDE del documento')
                fallos += 1
            if issue and not re.search(r'\d{2}:\d{2}:\d{2}-05:00$', issue):
                print('   >>> IssueTime sin el desfase -05:00')
                fallos += 1
            print()
            continue

        cadena = cadena_de(raiz)
        esperado = hashlib.sha384(cadena.encode('utf-8')).hexdigest()
        coincide = valor == esperado

        print(nombre)
        print(f'   numero     : {_texto(raiz, ".//cbc:ID")}')
        print(f'   cadena     : {cadena}   ({len(cadena)} caracteres)')
        print(f'   SHA-384    : {esperado}')
        print(f'   UUID       : {valor}')
        print(f'   schemeName : {esquema}')
        print(f'   IssueTime  : {issue}')
        print(f'   COINCIDE   : {"SI" if coincide else "NO"}')

        if not coincide:
            print('   >>> el CUFE del XML no corresponde a su propia cadena')
            fallos += 1
        if esquema not in ('CUFE-SHA384', 'CUDE-SHA384'):
            print('   >>> schemeName no es CUFE/CUDE-SHA384')
            fallos += 1
        if issue and not re.search(r'\d{2}:\d{2}:\d{2}-05:00$', issue):
            print('   >>> IssueTime sin el desfase -05:00')
            fallos += 1

        # Lo específico de la nota crédito: sin esto el documento es rechazado.
        if 'creditnote' in nombre:
            problemas = []

            if raiz.find('.//cac:AdditionalDocumentReference', NS) is None:
                problemas.append('no referencia al documento que corrige')
            if raiz.find('.//cac:DiscrepancyResponse/cbc:ResponseCode',
                         NS) is None:
                problemas.append('no declara el concepto de corrección')
            montos = raiz.findall('.//cac:LegalMonetaryTotal/*', NS)
            # UBL 2.1: los montos son MAGNITUDES. El unico que admite signo es
            # PayableRoundingAmount. La nota resta por su TIPO (InvoiceTypeCode
            # '01'), su concepto de correccion y su referencia, no por un signo.
            if any(m.get('NegativeValue') for m in montos):
                problemas.append('un monto lleva NegativeValue; UBL 2.1 solo '
                                 'lo permite en PayableRoundingAmount')
            elif any(float(m.text) < 0 for m in montos):
                problemas.append('un monto es negativo; UBL 2.1 los declara '
                                 'positivos')

            print('   nota credito: '
                  + ('referencia + concepto + importes positivos OK'
                     if not problemas else 'FALTA -> ' + '; '.join(problemas)))
            fallos += len(problemas)

        print()

    print('Todo coincide.' if not fallos else f'{fallos} problemas.')
    return 1 if fallos else 0


if __name__ == '__main__':
    raise SystemExit(main())