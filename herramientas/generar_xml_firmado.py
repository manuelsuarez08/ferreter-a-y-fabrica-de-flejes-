"""Genera un XML FIRMADO real, para auditar la firma con un validador externo.

Usa un .p12 de prueba generado al vuelo: el objetivo es examinar la ESTRUCTURA
de la firma (c14n, digests, KeyInfo, XAdES), no validar contra la DIAN.
"""
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / 'herramientas'))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from ferreteria import db as db_mod
from ferreteria.services import dian_emision, dian_firma, dian_notas, dian_xml

SALIDA = RAIZ / '_auditoria_xml'
NIT = '900187391'


def certificado_de_prueba(destino):
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.datetime.now(datetime.timezone.utc)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'CO'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'PRUEBA FIRMA SAS'),
        x509.NameAttribute(NameOID.COMMON_NAME, 'PRUEBA FIRMA SAS'),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, NIT),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto).issuer_name(sujeto)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    with open(destino, 'wb') as f:
        f.write(pkcs12.serialize_key_and_certificates(
            b'prueba', llave, cert, None,
            serialization.BestAvailableEncryption(b'clave123')))
    return destino


def main():
    temporal = tempfile.mkdtemp(prefix='firma_')
    ruta_p12 = certificado_de_prueba(os.path.join(temporal, 'firma.p12'))
    cert = dian_firma.cargar_certificado(ruta_p12, 'clave123')

    emisor = {
        'nombre_comercial': 'Ferretería Prueba', 'razon_social': 'PRUEBA SAS',
        'nit': NIT, 'digito_verificacion': '2', 'direccion': 'CALLE 1 # 2-3',
        'municipio': '11001', 'ciudad': 'Bogotá D.C.', 'departamento': '11',
        'nombre_departamento': 'Bogotá D.C.', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
        'ciiu': '4665',
    }
    adquirente = {
        'tipo_documento': 'NIT', 'numero_documento': '830114978',
        'digito_verificacion': '9', 'nombre': 'CLIENTE SA',
        'direccion': 'AV 6 # 78-90', 'municipio': '11001',
        'ciudad': 'Bogotá D.C.', 'departamento': '11',
        'nombre_departamento': 'Bogotá D.C.', 'pais': 'CO',
        'regimen_fiscal': 'Responsable de IVA', 'responsabilidades': ['O-13'],
    }
    items = [{'descripcion': 'CEMENTO GRIS 50KG', 'cantidad': 2.0,
              'precio_unitario': 50000.0, 'unidad': '94', 'codigo': 'CE001',
              'descuento': 0, 'iva_tasa': 19.0, 'inc_tasa': 0,
              'base': 100000.0}]
    totales = {
        'line_extension_amount': 100000.0, 'tax_exclusive_amount': 100000.0,
        'tax_inclusive_amount': 119000.0, 'payable_amount': 119000.0,
        'iva_valor': 19000.0, 'inc_valor': 0,
        'impuestos_iva': [{'tasa': 19.0, 'base': 100000.0, 'valor': 19000.0}],
        'impuestos_inc': [],
    }
    extras = {'software_id': 'SW-PRUEBA',
              'software_security_code': 'PIN-PRUEBA'}
    documento = {
        'numero': 'SETP-1', 'fecha': '2026-10-01', 'hora': '10:30:00',
        'cuide': 'a' * 96, 'tipo_documento': 'POS', 'tipo_ambiente': '2',
        'moneda': 'COP', 'tipo_operacion': '10', 'valor_total': 119000.0,
        'numero_resolucion': '187640', 'prefijo_resolucion': 'SETP',
        'rango_desde': 1, 'rango_hasta': 999999,
        'nombre_software': 'FerreControl', 'version_software': '1.0',
        'empresa_software': 'PRUEBA SAS', 'nit_proveedor_software': '1054552590',
    }

    momento = datetime.datetime(2026, 10, 1, 15, 30, 5,
                                tzinfo=datetime.timezone.utc)
    SALIDA.mkdir(exist_ok=True)

    raiz_doc = dian_xml.construir_invoice(documento, emisor, adquirente,
                                          items, totales, extras)
    firmado = dian_firma.firmar_documento(raiz_doc, cert, momento=momento)
    (SALIDA / 'invoice_firmado.xml').write_bytes(firmado)
    print(f'  invoice_firmado.xml   {len(firmado):6} bytes')

    documento_nc = dict(documento, numero='SETP-NC-1', tipo_documento='NC',
                        cuide='b' * 96,
                        cuide_referido='a' * 96, fecha_referido='2026-09-30',
                        motivo_codigo='1', motivo_descripcion='Devolución total')
    raiz_nc = dian_notas.construir_nota_credito(
        documento_nc, emisor, adquirente, items, totales, extras)
    firmado_nc = dian_firma.firmar_documento(raiz_nc, cert, momento=momento)
    (SALIDA / 'creditnote_firmado.xml').write_bytes(firmado_nc)
    print(f'  creditnote_firmado.xml {len(firmado_nc):6} bytes')

    raiz_ev = dian_xml.construir_evento(
        'a' * 96, 'SETP-1', '030', 'Acuse de recibo', emisor)
    # El evento lleva su PROPIO ID, corto: el `cbc:ID` de un evento es el
    # CUIDE del documento referido (96 caracteres), y un XML ID no puede
    # empezar por dígito. Passarlo como `id_documento` hacía que la Reference
    # apuntara a un ID que no es un ID, y el digest no cuadraba.
    firmado_ev = dian_firma.firmar_documento(
        raiz_ev, cert, id_documento='EVT-SETP-1-1', momento=momento)
    (SALIDA / 'evento_firmado.xml').write_bytes(firmado_ev)
    print(f'  evento_firmado.xml     {len(firmado_ev):6} bytes')

    print(f'\nEn: {SALIDA}')
    shutil.rmtree(temporal, ignore_errors=True)


if __name__ == '__main__':
    main()