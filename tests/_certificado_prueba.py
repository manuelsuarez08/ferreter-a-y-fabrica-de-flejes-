"""Genera un certificado .p12 de prueba en memoria (solo para tests y QA).

NO usar en produccion: es autofirmado y su clave es publica.
"""
import sys
import os
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


def generar_p12(ruta, clave=b'clave-de-prueba', nit='900187391',
                comun='Ferreteria y Fabrica de Flejes SAS'):
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
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1))
        .not_valid_after(ahora + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(llave, hashes.SHA256())
    )
    der = pkcs12.serialize_key_and_certificates(
        name=comun.encode('utf-8'),
        key=llave,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(clave),
    )
    with open(ruta, 'wb') as f:
        f.write(der)
    return ruta


if __name__ == '__main__':
    destino = sys.argv[1] if len(sys.argv) > 1 else 'certificado_prueba.p12'
    generar_p12(destino)
    print(f'Certificado de prueba creado: {os.path.abspath(destino)}')