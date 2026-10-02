"""Pruebas de la identidad del emisor y de la del proveedor de software.

Son las dos cosas que la DIAN usa para saber QUIÉN firma y QUIÉN construyó el
software. Antes no se comprobaban, y una de ellas ni siquiera se enviaba.

Este archivo cubre:
  1. `verificar_identidad_emisor`: el NIT del .p12 tiene que ser el del emisor.
  2. El bloque SoftwareInfo del XML: nombre, versión, proveedor y su NIT.
  3. Que el PIN (SoftwareSecurityCode) se lea de UNA sola columna.
  4. Que leer los ajustes no barra los valores entre columnas.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'tests'))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone

from ferreteria.services import dian_emision, dian_firma, dian_xml


@pytest.fixture
def db_path(tmp_path):
    """Cada test recibe una copia LIMPIA de la semilla."""
    import shutil
    destino = tmp_path / 'ferreteria.db'
    shutil.copy2(os.path.join(RAIZ, 'ferreteria-semilla.db'), destino)
    os.environ['FERRETERIA_DB'] = str(destino)
    return str(destino)


@pytest.fixture
def cfg(db_path):
    """Recarga `config` para que `DB_NAME` apunte a la base de este test.

    `ferreteria.config` resuelve DB_NAME al importarse, así que hay que recargarlo
    o la escritura va a la base real.
    """
    import importlib
    import ferreteria.config as config
    importlib.reload(config)
    import ferreteria.db as db
    importlib.reload(db)
    return config

NIT_EMISOR = '900187391'
NIT_OTRO = '800111222'


def _certificado(tmp_path, nombre_archivo, nit, comun='FERRETERIA SAS'):
    """Genera un .p12 autofirmado cuyo sujeto lleva el NIT en serialNumber."""
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(timezone.utc)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'CO'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, comun),
        x509.NameAttribute(NameOID.COMMON_NAME, comun),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, nit),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto).issuer_name(sujeto)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1))
        .not_valid_after(ahora + timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    ruta = str(tmp_path / nombre_archivo)
    with open(ruta, 'wb') as f:
        f.write(pkcs12.serialize_key_and_certificates(
            b'prueba', llave, cert, None,
            serialization.BestAvailableEncryption(b'clave123')))
    return ruta


# ═══════════════════════════════════════════
# 1. Coherencia entre el certificado y el emisor
# ═══════════════════════════════════════════

def test_el_nit_del_certificado_se_contrasta_con_el_del_emisor(tmp_path):
    """Si los NIT no coinciden, la emisión se detiene ANTES de firmar."""
    ruta = _certificado(tmp_path, 'otro.p12', NIT_OTRO)
    cert = dian_firma.cargar_certificado(ruta, 'clave123')

    with pytest.raises(dian_firma.ErrorIdentidadEmisor) as error:
        dian_firma.verificar_identidad_emisor(cert, NIT_EMISOR)

    # El mensaje dice CUALES son los dos NIT: sin eso el usuario no sabe si
    # sube otro certificado o si debe corregir el NIT del negocio.
    mensaje = str(error.value)
    assert NIT_OTRO in mensaje
    assert NIT_EMISOR in mensaje


def test_si_los_nit_coinciden_no_falla(tmp_path):
    ruta = _certificado(tmp_path, 'ok.p12', NIT_EMISOR)
    cert = dian_firma.cargar_certificado(ruta, 'clave123')

    dian_firma.verificar_identidad_emisor(cert, NIT_EMISOR)


def test_el_nit_se_compara_sin_guiones_ni_digito_de_verificacion(tmp_path):
    """En la práctica el NIT viene con guiones: 900.187.391 se compara bien."""
    ruta = _certificado(tmp_path, 'formato.p12', NIT_EMISOR)
    cert = dian_firma.cargar_certificado(ruta, 'clave123')

    dian_firma.verificar_identidad_emisor(cert, '900.187.391')


def test_un_certificado_sin_nit_no_pasa_en_produccion(tmp_path):
    """Sin NIT legible no hay garantía de nada, así que se bloquea."""
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(timezone.utc)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'SIN NIT')])
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto).issuer_name(sujeto)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1))
        .not_valid_after(ahora + timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    ruta = str(tmp_path / 'sin_nit.p12')
    with open(ruta, 'wb') as f:
        f.write(pkcs12.serialize_key_and_certificates(
            b'p', llave, cert, None,
            serialization.BestAvailableEncryption(b'clave123')))
    cargado = dian_firma.cargar_certificado(ruta, 'clave123')

    with pytest.raises(dian_firma.ErrorIdentidadEmisor):
        dian_firma.verificar_identidad_emisor(cargado, NIT_EMISOR, exigido=True)


def test_el_error_de_identidad_es_un_error_de_certificado():
    """Para que un solo `except ErrorCertificado` lo cubra en los 3 módulos."""
    assert issubclass(dian_firma.ErrorIdentidadEmisor,
                      dian_firma.ErrorCertificado)


# ═══════════════════════════════════════════
# 2. Identidad del proveedor de software en el XML
# ═══════════════════════════════════════════

def _documento_minimo(**extra):
    """Documento con los campos que consume el bloque de extensiones."""
    documento = {
        'numero': 'POS-1', 'fecha': '2026-01-15', 'hora': '10:00:00',
        'cuide': 'CUDE-DE-PRUEBA', 'tipo_ambiente': '2', 'moneda': 'COP',
        'tipo_operacion': '10', 'valor_total': 119000.0,
        'tipo_pago': 'efectivo', 'id_venta': 34,
        'nombre_software': 'POS Ferreteria DIAN',
        'version_software': '1.0',
        'empresa_software': 'DESARROLLO SAS',
        'nit_proveedor_software': '1054552590',
    }
    documento.update(extra)
    return documento


def _texto_documento(documento, extras=None):
    raiz = dian_xml.construir_invoice(
        documento,
        {'nombre_comercial': 'FERRETERIA SAS', 'razon_social': 'FERRETERIA SAS',
         'nit': NIT_EMISOR, 'digito_verificacion': '2',
         'direccion': 'CALLE 1 # 2-3', 'municipio': '11001',
         'departamento': '11', 'pais': 'CO',
         'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13']},
        {'tipo_documento': 'NIT', 'numero_documento': '830114978',
         'digito_verificacion': '9', 'nombre': 'CLIENTE SA',
         'direccion': 'AV 6 # 78-90', 'municipio': '11001',
         'departamento': '11', 'pais': 'CO',
         'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13']},
        [{'descripcion': 'Cemento gris 50kg', 'cantidad': 2.0,
          'precio_unitario': 50000.0, 'unidad': '94', 'codigo': 'CE001',
          'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0, 'base': 100000.0}],
        {'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
         'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
         'iva_valor': 19000.0, 'inc_valor': 0,
         'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
         'impuestos_inc': []},
        extras or {},
    )
    return dian_xml.a_texto(raiz)


def test_el_xml_declara_el_nit_del_proveedor_de_software():
    """El anexo lo exige: sin el NIT la DIAN no traza el documento."""
    xml = _texto_documento(_documento_minimo())
    assert '<sts:SoftwareProviderID>1054552590</sts:SoftwareProviderID>' in xml


def test_el_xml_declara_nombre_version_y_proveedor():
    xml = _texto_documento(_documento_minimo())
    assert '<sts:SoftwareName>POS Ferreteria DIAN</sts:SoftwareName>' in xml
    assert '<sts:SoftwareVersion>1.0</sts:SoftwareVersion>' in xml
    assert '<sts:SoftwareProvider>DESARROLLO SAS</sts:SoftwareProvider>' in xml


def test_sin_nit_de_proveedor_no_se_manda_el_nodo_vacio():
    """Un nodo vacío es peor que un nodo ausente: parece configurado."""
    xml = _texto_documento(_documento_minimo(nit_proveedor_software=''))
    assert 'SoftwareProviderID' not in xml


def test_el_proveedor_no_se_confunde_con_el_emisor():
    """El nombre del proveedor NO es el de la ferretería: son dos actores."""
    documento = _documento_minimo(
        empresa_software='DESARROLLO SAS',
        nit_proveedor_software='1054552590',
    )
    xml = _texto_documento(documento)
    assert '<sts:SoftwareProvider>DESARROLLO SAS</sts:SoftwareProvider>' in xml
    # El emisor sigue siendo la ferretería, en su propio bloque. Su NIT va en
    # `cbc:CompanyID` dentro de `cac:PartyLegalEntity`, con el juego de
    # atributos que exige el Anexo Tecnico V1.9, pagina 44:
    #   FAJ45 @schemeAgencyID   = "195"
    #   FAJ46 @schemeAgencyName = "CO, DIAN (...)"
    #   FAJ47 @schemeID         = digito de verificacion
    #   FAJ48 @schemeName       = "31"
    # Ojo: el NIT del proveedor va en SoftwareProviderID, no aquí.
    #
    # Se comprueba la forma por ATRIBUTOS y no por texto exacto: el orden de los
    # atributos no lo fija la norma, y una comparacion de string completa
    # fallaria en cuanto el serializador cambiara el orden, sin que el
    # documento fuera incorrecto.
    m = re.search(r'<cbc:CompanyID([^>]*)>([^<]*)</cbc:CompanyID>', xml)
    assert m, 'FALTA cbc:CompanyID'
    atributos = m.group(1)
    assert m.group(2) == NIT_EMISOR
    assert 'schemeAgencyID="195"' in atributos
    assert 'schemeAgencyName="CO, DIAN' in atributos
    assert 'schemeName="31"' in atributos
    assert 'CorporateScheme' not in atributos, \
        '"CorporateScheme" no aparece ni una vez en las 753 paginas del Anexo'
    assert NIT_EMISOR not in documento['empresa_software']
    # Y el NIT del proveedor NO aparece como identificación del emisor.
    assert xml.count(NIT_EMISOR) == 1


# ═══════════════════════════════════════════
# 3. El PIN se lee de una sola columna
# ═══════════════════════════════════════════

def _conn_con_config(**columnas):
    """Base en memoria con la fila única de `configuracion` ya sembrada.

    Se crean TODAS las columnas que `_leer_ajustes_dian` consulta, aunque la
    prueba solo use tres o cuatro: si falta una, el COALESCE explota y el
    error dice "no such column", no "el dato llegó a la clave equivocada".
    """
    esquema = {
        'dian_ambiente': "TEXT DEFAULT '2'", 'dian_prefijo': "TEXT DEFAULT 'POS'",
        'dian_consecutivo': 'INTEGER DEFAULT 1',
        'clave_tecnica': "TEXT DEFAULT ''", 'software_id': "TEXT DEFAULT ''",
        'software_pin': "TEXT DEFAULT ''",
        'dian_software_security_code': "TEXT DEFAULT ''",
        'certificado_ruta': "TEXT DEFAULT ''", 'certificado_clave': "TEXT DEFAULT ''",
        'dian_test_set_id': "TEXT DEFAULT ''", 'dian_modo': "TEXT DEFAULT 'habilitacion'",
        'numero_resolucion': "TEXT DEFAULT ''", 'prefijo': "TEXT DEFAULT ''",
        'rango_desde': 'INTEGER DEFAULT 1', 'rango_hasta': 'INTEGER DEFAULT 0',
        'dian_max_intentos': 'INTEGER DEFAULT 8',
        'software_proveedor_nit': "TEXT DEFAULT ''",
        'software_proveedor_nombre': "TEXT DEFAULT ''",
    }
    esquema.update({k: f'TEXT DEFAULT {v!r}' for k, v in columnas.items()})
    columnas_sql = ', '.join(f'{k} {v}' for k, v in esquema.items())
    conn = sqlite3.connect(':memory:')
    conn.execute(f'CREATE TABLE configuracion (id INTEGER PRIMARY KEY, {columnas_sql})')
    conn.execute('INSERT INTO configuracion (id) VALUES (1)')
    conn.commit()
    return conn


def test_el_pin_se_toma_de_dian_software_security_code():
    """La columna de destino es la que manda, no la antigua `software_pin`."""
    conn = _conn_con_config(
        dian_ambiente='2', software_pin='PIN_VIEJO',
        dian_software_security_code='PIN_NUEVO')
    ajustes = dian_emision._leer_ajustes_dian(conn.cursor())

    assert ajustes['software_security_code'] == 'PIN_NUEVO'
    conn.close()


def test_leer_los_ajustes_no_barre_certificado_y_modo():
    """Cada columna llega a su clave.

    Este test existe por una razón concreta: al quitar la columna `software_pin`
    del SELECT, TODOS los índices posteriores se corren una posición. El síntoma
    sería un certificado donde va el modo, o un rango donde va el prefijo, y
    aparecería mucho después, en un rechazo de la DIAN. Aquí se atrapa al
    momento.
    """
    conn = _conn_con_config(
        dian_ambiente='2',
        dian_prefijo='POS',
        dian_consecutivo=7,
        clave_tecnica='CT-UNICA',
        software_id='SW-123',
        dian_software_security_code='PIN-456',
        certificado_ruta='C:/certs/firma.p12',
        certificado_clave='secreto',
        dian_test_set_id='TS-9',
        dian_modo='produccion',
        numero_resolucion='187640',
        prefijo='FV',
        rango_desde=10,
        rango_hasta=500,
        dian_max_intentos=5,
        software_proveedor_nit='1054552590',
        software_proveedor_nombre='DESARROLLO SAS',
    )
    ajustes = dian_emision._leer_ajustes_dian(conn.cursor())

    assert ajustes['ambiente'] == '2'
    assert ajustes['prefijo'] == 'POS'
    assert ajustes['consecutivo'] == 7
    assert ajustes['clave_tecnica'] == 'CT-UNICA'
    assert ajustes['software_id'] == 'SW-123'
    assert ajustes['software_security_code'] == 'PIN-456'
    assert ajustes['certificado_ruta'] == 'C:/certs/firma.p12'
    assert ajustes['certificado_clave'] == 'secreto'
    assert ajustes['test_set_id'] == 'TS-9'
    assert ajustes['modo'] == 'produccion'
    assert ajustes['numero_resolucion'] == '187640'
    assert ajustes['prefijo_resolucion'] == 'FV'
    assert ajustes['rango_desde'] == 10
    assert ajustes['rango_hasta'] == 500
    assert ajustes['max_intentos'] == 5
    assert ajustes['software_proveedor_nit'] == '1054552590'
    assert ajustes['software_proveedor_nombre'] == 'DESARROLLO SAS'
    conn.close()


def test_el_nit_del_proveedor_se_normaliza_a_digitos():
    conn = _conn_con_config(dian_ambiente='2',
                            software_proveedor_nit='1.054.552.590-1')
    ajustes = dian_emision._leer_ajustes_dian(conn.cursor())

    assert ajustes['software_proveedor_nit'] == '10545525901'
    conn.close()# ═══════════════════════════════════════════
# 4. El panel expone los datos sin cruzarlos
# ═══════════════════════════════════════════

def test_el_panel_devuelve_cada_campo_en_su_clave(db_path, cfg):
    """El endpoint de configuración debe mapear bien las columnas.

    Igual que el test de `_leer_ajustes_dian`, este existe porque añadir dos
    columnas al final de un SELECT con 23 posiciones desplaza con facilidad todo
    lo que le sigue por una posición. Aquí se comprueba con valores DIFERENTES en
    cada campo, que es la única forma de detectar un desajuste: si todos
    valieran lo mismo, el test pasaría con el código roto.
    """
    import importlib

    from ferreteria.app_factory import create_app
    from ferreteria.blueprints import dian_pos as blueprint_pos
    from ferreteria import db as db_mod
    importlib.reload(blueprint_pos)

    # La semilla puede no traer las columnas nuevas: se corre el esquema, que es
    # quien aplica las migraciones. Se hace sobre una conexión explícita a la
    # base de este test, no con init_db() a secas.
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._asegurar_migracion_documento_soporte(cursor)
    db_mod._aplicar_migraciones(cursor)
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE configuracion SET dian_ambiente='1', "
                 "dian_prefijo='FV', dian_consecutivo=42, "
                 "clave_tecnica='CT-42', software_id='SW-42', "
                 "dian_test_set_id='TS-42', numero_resolucion='187640', "
                 "prefijo='FV', rango_desde=11, rango_hasta=999, "
                 "certificado_ruta='', dian_modo='produccion', "
                 "dian_software_security_code='PIN-42', "
                 "fecha_vencimiento_resolucion='2027-01-01', "
                 "nit='900187391', nombre='NOMBRE-DEL-NEGOCIO', "
                 "telefono='3001234567', direccion='DIRECCION-DEL-NEGOCIO', "
                 "codigo_municipio='11001', codigo_departamento='11', "
                 "regimen_fiscal='REGIMEN-DEL-NEGOCIO', "
                 "software_proveedor_nit='1054552590', "
                 "software_proveedor_nombre='PROVEEDOR-DEL-SOFTWARE' WHERE id=1")
    conn.commit()
    conn.close()

    aplicacion = create_app(inicializar_db=False, iniciar_hilo_dian=False)
    # La vista exige sesión: se entra como administrador en lugar de saltarse el
    # decorador, para que la prueba recorra el mismo camino que la interfaz real.
    with aplicacion.test_request_context():
        from flask import session
        session['usuario'] = 'admin'
        session['rol'] = 'admin'
        respuesta = blueprint_pos.configuracion()

    assert respuesta.status_code == 200
    datos = respuesta.get_json()

    assert datos['ambiente'] == '1'
    assert datos['prefijo'] == 'FV'
    assert datos['consecutivo'] == 42
    assert datos['clave_tecnica'] == 'CT-42'
    assert datos['software_id'] == 'SW-42'
    assert datos['test_set_id'] == 'TS-42'
    assert datos['numero_resolucion'] == '187640'
    assert datos['prefijo_resolucion'] == 'FV'
    assert datos['rango_desde'] == 11
    assert datos['rango_hasta'] == 999
    assert datos['modo'] == 'produccion'
    assert datos['software_security_code'] == 'PIN-42'
    assert datos['tiene_pin'] is True
    assert datos['fecha_vencimiento_resolucion'] == '2027-01-01'
    # Datos del negocio: aquí es donde un desplazamiento se notaría.
    assert datos['emisor']['nit'] == '900187391'
    assert datos['emisor']['nombre'] == 'NOMBRE-DEL-NEGOCIO'
    assert datos['emisor']['telefono'] == '3001234567'
    assert datos['emisor']['direccion'] == 'DIRECCION-DEL-NEGOCIO'
    assert datos['emisor']['municipio'] == '11001'
    assert datos['emisor']['departamento'] == '11'
    assert datos['emisor']['regimen_fiscal'] == 'REGIMEN-DEL-NEGOCIO'
    # Datos del desarrollador: NO deben aparecer dentro del emisor.
    assert datos['software_proveedor_nit'] == '1054552590'
    assert datos['software_proveedor_nombre'] == 'PROVEEDOR-DEL-SOFTWARE'