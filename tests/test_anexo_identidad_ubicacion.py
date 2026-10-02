"""Pruebas de identidad y ubicacion del emisor y del adquiriente.

Cada prueba de este archivo cita la PAGINA del Anexo Tecnico de Factura
Electronica de Venta V1.9 (Resolucion 000165 del 01/NOV/2023) de la que sale la
regla. Eso es lo que las hace utiles: dentro de seis meses el codigo puede haber
cambiado, pero "Anexo Tecnico V1.9, pagina 15" se puede volver a buscar.

La ronda anteriorKilometro por kilometro dejo el documento sintacticamente bien
puesto y aun asi habia puntos del Anexo que el generador no cumplia. Estos son
algunos de ellos, y los que se corrigieron aqui:

  1. `cbc:CityName` llevaba el CODIGO DANE del municipio (11001) en vez del
     NOMBRE (Bogota D.C.). El Anexo es explicito: la ficha CAJ-13 dice
     "Identificacion de la ciudad o pueblo en donde se encuentra el
     adquiriente" y el ejemplo de la pagina 26 muestra el texto.

  2. `cac:CountrySubentity` llevaba el codigo del departamento en vez del
     nombre. El orden correcto es NAME (FAJ11) antes que CODE (FAJ12), y el
     ejemplo de la pagina 16 lleva "Bogota D.C." primero y "11" despues.

  3. `cac:CountrySubentityCode` no existia. Sin el, el departamento no queda
     identificado de forma inequivoca: hay dos "Bogota" y el codigo es lo que
     los distingue.

  4. No existia `cac:AddressLine`, que es donde va la direccion (FAJ13). Se
     emitia el nombre de la calle repartido en `cbc:Line` sin el grupo.

  5. No existia `cac:CorporateRegistrationScheme`, que el Anexo marca como
     obligatorio (FAJ49, pagina 15) y cuyo `cbc:ID` "debe ser igual al campo
     sts:prefix informado en el encabezado de la factura".

  6. No existia `cac:RegistrationDate` (FAJ54-55, pagina 16).
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_xml  # noqa: E402

NS = {
    'cac': dian_xml.NS_CAC,
    'cbc': dian_xml.NS_CBC,
    'sts': dian_xml.NS_STS,
    'ext': dian_xml.NS_EXT,
}


def _xml(**extra):
    """Construye el XML completo y lo devuelve como texto.

    Se construye ENTERO, no un nodo suelto: varios de los puntos de este
    archivo son de orden y de ubicacion dentro del arbol (el nombre del
    departamento antes que su codigo), y eso solo se mira en el documento
    montado.
    """
    emisor = {
        'razon_social': 'FERRETERIA AUDIT SA',
        'nombre_comercial': 'Ferretería Audit',
        'nit': '900187391',
        'digito_verificacion': '2',
        'direccion': 'CALLE 1 # 2-3',
        'ciiu': '4665',
        'ciudad': 'Bogotá D.C.',
        'municipio': '11001',
        'departamento': '11',
        'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA',
        'responsabilidades': ['O-13'],
        'telefono': '3001234567',
        'email': 'facturacion@audit.co',
        'prefijo': 'SETP',
        'fecha_registro_vencimiento': '2028-06-30',
    }
    emisor.update(extra.get('emisor', {}))
    cliente = extra.get('cliente', {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'digito_verificacion': '9', 'nombre': 'CLIENTE EMPRESA SA',
        'direccion': 'AV 6 # 78-90', 'municipio': '11001',
        'departamento': '11', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
    })

    documento = {
        'numero': 'SETP-1', 'fecha': '2026-10-01', 'hora': '10:30:00',
        'cuide': 'CUDE-DE-PRUEBA', 'tipo_ambiente': '2', 'moneda': 'COP',
        'tipo_operacion': '10', 'valor_total': 119000.0, 'tipo_pago': 'efectivo',
        'id_venta': 1, 'tipo_documento': 'POS', 'numero_resolucion': '187640',
        'prefijo_resolucion': 'SETP', 'rango_desde': 1, 'rango_hasta': 999999,
        'nombre_software': 'POS Ferreteria DIAN', 'version_software': '1.0',
        'empresa_software': 'DESARROLLO SAS',
        'nit_proveedor_software': '1054552590',
    }
    items = [{'descripcion': 'TUBO PVC 1/2', 'cantidad': 1.0,
              'precio_unitario': 100000.0, 'unidad': '94', 'codigo': 'TU014',
              'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
              'base': 100000.0}]
    totales = {
        'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
        'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
        'iva_valor': 19000.0, 'inc_valor': 0,
        'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
        'impuestos_inc': [],
    }
    extras = {'software_id': 'SW-PRUEBA', 'software_security_code': 'PIN'}

    raiz = dian_xml.construir_invoice(documento, emisor, cliente, items,
                                      totales, extras)
    return dian_xml.a_texto(raiz)


def _raiz(**extra):
    return ET.fromstring(_xml(**extra))


def _emisor(r):
    return r.find('cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity',
                  NS)


def _direccion_emisor(r):
    """La direccion del emisor cuelga de `cac:Party/cac:PhysicalLocation`.

    NO va dentro de `cac:PartyLegalEntity`: `PartyLegalEntity` agrupa la
    identificacion de la empresa (razon social, NIT, registro), y la ubicacion
    es un grupo hermano. Este archivo lo fija porque es el error de jerarquia
    tipico en UBL: los dos son `cac:` y los dos llevan un `Address` en algun
    sitio, asi que confundirlos no rompe nada visible.
    """
    return r.find('cac:AccountingSupplierParty/cac:Party/'
                  'cac:PhysicalLocation/cac:Address', NS)


def _direccion_cliente(r):
    return r.find('cac:AccountingCustomerParty/cac:Party/'
                  'cac:PhysicalLocation/cac:Address', NS)


# ══════════════════════════════════════════════════════════════
# 1. El codigo DANE NO es el nombre de la ciudad
# ══════════════════════════════════════════════════════════════

def test_city_name_lleva_el_nombre_del_municipio():
    """Anexo V1.9, pagina 26 (CAJ-13) y pagina 15 (FAJ10): el codigo DANE no es
    el nombre de la ciudad."

    "Identificacion de la ciudad o pueblo en donde se encuentra el
    adquiriente" — el ejemplo de la pagina 26 trae `Bogota D.C.`, no `11001`.
    """
    texto = _xml()
    assert '<cbc:CityName>Bogotá D.C.</cbc:CityName>' in texto
    assert '<cbc:CityName>11001</cbc:CityName>' not in texto, \
        'el codigo DANE en CityName es el defecto que se corrigio'


def test_el_codigo_dane_va_en_location_id():
    """FAJ09: "Codigo de la ciudad o pueblo en donde se encuentra el
    adquiriente". Es el UNICO lugar donde va el codigo, y no se sustituye al
    nombre: conviven los dos."""
    direccion = _direccion_cliente(_raiz())
    assert direccion.findtext('cbc:ID', namespaces=NS) == '11001'
    assert direccion.findtext('cbc:CityName', namespaces=NS) == 'Bogotá D.C.'


def test_el_municipio_con_diacritico_no_se_altera():
    """`Bogotá` con tilde y `Bogotá D.C.` con punto.

    El municipio DANE 11001 se llama así. Un nombre sin la tilde no coincide
    con el registro y es un dato distinto, aunque sea 'lo mismo' para quien lo
    escribe a mano. Ademas `Bogotá D.C.` lleva punto, que es parte del nombre
    oficial.
    """
    texto = _xml()
    assert 'Bogotá D.C.' in texto
    assert 'Bogota D.C.' not in texto, 'el nombre del municipio perdio el acento'


# ══════════════════════════════════════════════════════════════
# 2. Departamento: nombre antes que codigo, y los dos
# ══════════════════════════════════════════════════════════════

def test_country_subentity_lleva_el_nombre_no_el_codigo():
    """FAJ11, pagina 16: `cbc:CountrySubentity` = "Nombre del departamento o
    distrito". El ejemplo de la pagina 16 lleva `Bogota D.C.` y despues `11`."""
    direccion = _direccion_emisor(_raiz())
    assert direccion.findtext('cbc:CountrySubentity', namespaces=NS) == 'Bogotá D.C.'


def test_country_subentity_code_lleva_el_codigo_dane():
    """FAJ12, pagina 16: "Codigo de identificacion del departamento o distrito,
    segun la divisional politica del pais`.

    El proyecto tiene el codigo (`configuracion.codigo_departamento`) y no lo
    emitia. Sin el, dos municipios llamados igual en departamentos distintos son
    indistinguibles.
    """
    direccion = _direccion_emisor(_raiz())
    assert direccion.findtext('cbc:CountrySubentityCode', namespaces=NS) == '11'


def test_el_nombre_del_departamento_viene_antes_que_su_codigo():
    """`cac:Address` es una `xsd:sequence`, y el orden importa igual que el
    contenido.

    UBL declara el nombre del departamento antes que su codigo. Este es el mismo
    tipo de fallo que rompío la firma: todo correcto y documento inválido.
    """
    direccion = _direccion_emisor(_raiz())
    hijos = [h.tag.split('}')[-1] for h in direccion]

    assert 'CountrySubentity' in hijos and 'CountrySubentityCode' in hijos
    assert hijos.index('CountrySubentity') < hijos.index('CountrySubentityCode'), \
        f'el codigo del departamento va despues del nombre: {hijos}'


# ══════════════════════════════════════════════════════════════
# 3. La calle va en AddressLine
# ══════════════════════════════════════════════════════════════

def test_la_direccion_va_en_address_line():
    """FAJ13, pagina 16: `cac:AddressLine` es "Nodo que agrupa los elementos que
    identifican el lugar fisico de la direccion"."""
    direccion = _direccion_emisor(_raiz())
    linea = direccion.find('cac:AddressLine', NS)
    assert linea is not None, 'falta el grupo AddressLine'
    assert linea.findtext('cbc:Line', namespaces=NS) == 'CALLE 1 # 2-3'


def test_la_direccion_del_cliente_tambien_va_en_address_line():
    """La misma regla para el adquiriente (CAJ-15, pagina 26)."""
    direccion = _direccion_cliente(_raiz())
    linea = direccion.find('cac:AddressLine', NS)
    assert linea is not None
    assert linea.findtext('cbc:Line', namespaces=NS) == 'AV 6 # 78-90'


def test_address_line_viene_antes_del_pais():
    """UBL declara `cbc:Country` al final de `cac:Address`."""
    direccion = _direccion_emisor(_raiz())
    hijos = [h.tag.split('}')[-1] for h in direccion]
    assert hijos.index('AddressLine') < hijos.index('Country'), \
        f'AddressLine antes de Country: {hijos}'


def test_el_pais_declara_colombia():
    """FAJ15 / CAJ-16: `cbc:IdentificationCode` = codigo del pais. CO."""
    direccion = _direccion_emisor(_raiz())
    pais = direccion.find('cac:Country', NS)
    assert pais.findtext('cbc:IdentificationCode', namespaces=NS) == 'CO'


# ══════════════════════════════════════════════════════════════
# 4. La ubicacion fisica va en cac:PhysicalLocation
# ══════════════════════════════════════════════════════════════

def test_la_direccion_esta_bajo_physical_location():
    """CAJ-12: `cac:PhysicalLocation` agrupa el lugar fisico.

    Un `cac:Address` como hijo directo de `cac:Party` no valida: el nodo de la
    ubicacion es `PhysicalLocation`.
    """
    party = _raiz().find('cac:AccountingCustomerParty/cac:Party', NS)
    assert party.find('cac:Address', NS) is None, \
        'el Address cuelga directo de Party en vez de PhysicalLocation'
    assert party.find('cac:PhysicalLocation/cac:Address', NS) is not None


# ══════════════════════════════════════════════════════════════
# 5. El prefijo autorizado por la resolucion
# ══════════════════════════════════════════════════════════════

def test_el_emisor_declara_el_prefijo_de_su_resolucion():
    """FAJ49/FAJ50, pagina 15: grupo obligatorio, y su `cbc:ID` "debe ser igual al
    campo sts:prefix informado en el encabezado de la factura"."""
    entidad = _emisor(_raiz())
    esquema = entidad.find('cac:CorporateRegistrationScheme', NS)
    assert esquema is not None, 'falta CorporateRegistrationScheme (FAJ49, obligatorio)'
    assert esquema.findtext('cbc:ID', namespaces=NS) == 'SETP'


def test_el_prefijo_del_emisor_coincide_con_el_de_la_resolucion():
    """El Anexo lo dice dos veces: FAJ50 remite a `sts:Prefix` del encabezado.

    Si el emisor declara 'SETP' y la autorizacion autoriza 'FV', el documento se
    contradice: la DIAN no puede saber a que rango pertenece el consecutivo.
    """
    raiz = _raiz()
    prefijo_emisor = _emisor(raiz).find(
        'cac:CorporateRegistrationScheme/cbc:ID', NS).text
    prefijo_resolucion = raiz.findtext(
        'ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/'
        'sts:DianExtensions/sts:InvoiceControl/'
        'sts:AuthorizedInvoices/sts:Prefix', namespaces=NS)

    assert prefijo_emisor == prefijo_resolucion == 'SETP'


def test_sin_prefijo_no_se_emite_el_nodo_vacio():
    """Un nodo con `cbc:ID` vacio es peor que ninguno: aparenta estar
    configurado. El Anexo no admite un prefijo vacio."""
    raiz = _raiz(emisor={'prefijo': ''})
    entidad = _emisor(raiz)
    esquema = entidad.find('cac:CorporateRegistrationScheme', NS)
    assert esquema is None or esquema.findtext('cbc:ID', namespaces=NS)


def test_corporate_registration_scheme_viene_despues_del_company_id():
    """`cac:PartyLegalEntity` es `ID, Name, ..., CompanyID, ..., CorporateID,
    ..., CorporateRegistrationScheme, ..., RegistrationDate, ...`."""
    hijos = [h.tag.split('}')[-1] for h in _emisor(_raiz())]
    assert 'CompanyID' in hijos and 'CorporateRegistrationScheme' in hijos
    assert hijos.index('CompanyID') < hijos.index('CorporateRegistrationScheme')


# ══════════════════════════════════════════════════════════════
# 6. El registro en camara de comercio
# ══════════════════════════════════════════════════════════════

def test_el_emisor_declara_el_vencimiento_de_su_registro():
    """FAJ55, pagina 16: `cbc:EndDate` = "Fecha de vencimiento del registro de la
    camara de comercio"."""
    fechas = _emisor(_raiz()).find('cac:RegistrationDate', NS)
    assert fechas is not None, 'falta RegistrationDate (FAJ54/FAJ55, pagina 16)'
    assert fechas.findtext('cbc:EndDate', namespaces=NS) == '2028-06-30'


def test_sin_dato_de_registro_no_se_inventa_una_fecha():
    """Este es el punto de criterio, y por eso esta prueba.

    La fecha de REGISTRO (FAJ54) no existe en la base y no se puede deducir de
    nada. Emitirla con la fecha de emision equivaldria a declarar ante la DIAN
    que la empresa se matriculo el dia que se hizo la primera venta. Se deja
    fuera, y la prueba falla si alguien la pone.

    `EndDate` es distinto: ese dato sí lo declara el negocio, asi que sale
    cuando esta. Sin dato, no hay nada que emitir.
    """
    raiz = _raiz(emisor={'fecha_registro_vencimiento': ''})
    fechas = _emisor(raiz).find('cac:RegistrationDate', NS)
    if fechas is not None:
        assert not fechas.findtext('cbc:StartDate', namespaces=NS), \
            'la fecha de REGISTRO no se puede deducir: no la declares'


def test_registration_date_va_al_final_del_party_legal_entity():
    """En la secuencia de `cac:PartyLegalEntity`, `RegistrationDate` es el
    ultimo grupo."""
    hijos = [h.tag.split('}')[-1] for h in _emisor(_raiz())]
    if 'RegistrationDate' in hijos:
        assert hijos.index('RegistrationDate') == len(hijos) - 1, \
            f'RegistrationDate deberia cerrar el grupo: {hijos}'


# ══════════════════════════════════════════════════════════════
# 7. La direccion en el NODE de tipo Address, no suelta
# ══════════════════════════════════════════════════════════════

def test_no_hay_line_suelto_fuera_de_address_line():
    """Regresion: se emitia `cbc:Line` sin el grupo `cac:AddressLine` que lo
    contiene. El Anexo lo declara dentro del AddressLine, no como hijo suelto
    del Address."""
    direccion = _direccion_emisor(_raiz())
    assert direccion.find('cbc:Line', NS) is None, \
        'cbc:Line suelto en Address: debe ir dentro de cac:AddressLine'