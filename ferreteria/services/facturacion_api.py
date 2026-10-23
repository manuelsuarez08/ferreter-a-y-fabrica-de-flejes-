"""Conexión con un proveedor externo de facturación electrónica (p. ej. Facturatech).

- La clave API se guarda CIFRADA (Fernet) en la base y nunca se devuelve por HTTP.
- Antes de guardarla se valida con una petición de prueba (ping) al proveedor.
- La URL se restringe a https y se rechazan destinos de red interna (SSRF).
"""
import base64
import hashlib
import ipaddress
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from ..config import SECRET_KEY

TIMEOUT_SEGUNDOS = 10
ENCABEZADO_POR_DEFECTO = 'X-API-Key'


class ErrorProveedor(Exception):
    """Error con mensaje apto para mostrar al usuario."""


def _fernet():
    # FE_CIFRADO_KEY permite rotar la llave de cifrado sin tocar SECRET_KEY.
    semilla = os.environ.get('FE_CIFRADO_KEY') or SECRET_KEY
    llave = base64.urlsafe_b64encode(hashlib.sha256(semilla.encode('utf-8')).digest())
    return Fernet(llave)


def cifrar(texto):
    return _fernet().encrypt(texto.encode('utf-8')).decode('ascii')


def descifrar(token):
    """Devuelve la clave en claro, o '' si no existe o no se puede descifrar."""
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''


def enmascarar(clave):
    if not clave:
        return ''
    return '••••' + clave[-4:] if len(clave) > 8 else '••••'


def validar_url_base(url):
    """Normaliza la URL base y exige https hacia un host público."""
    url = (url or '').strip().rstrip('/')
    partes = urlparse(url)
    if partes.scheme != 'https' or not partes.hostname:
        raise ErrorProveedor('La URL del proveedor debe empezar por https://')
    try:
        direcciones = {i[4][0] for i in socket.getaddrinfo(partes.hostname, None)}
    except socket.gaierror:
        raise ErrorProveedor('No se pudo encontrar el servidor. Revise la URL.')
    for direccion in direcciones:
        ip = ipaddress.ip_address(direccion)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise ErrorProveedor('La URL apunta a una red interna; no está permitida.')
    return url


class _SinRedireccion(urllib.request.HTTPRedirectHandler):
    # Un redirect podría llevar la clave a otro host (o a una red interna).
    def redirect_request(self, *args, **kwargs):
        return None


def probar_clave(url_base, ruta_ping, clave, encabezado=ENCABEZADO_POR_DEFECTO):
    """Hace la petición de prueba. Devuelve un mensaje o lanza ErrorProveedor."""
    if not clave:
        raise ErrorProveedor('Ingrese la clave API.')
    base = validar_url_base(url_base)
    ruta = '/' + (ruta_ping or '').strip().lstrip('/')
    encabezado = (encabezado or ENCABEZADO_POR_DEFECTO).strip()
    if not encabezado.replace('-', '').isalnum():
        raise ErrorProveedor('El nombre del encabezado de autenticación no es válido.')

    peticion = urllib.request.Request(
        base + ruta, headers={encabezado: clave, 'Accept': 'application/json'})
    abridor = urllib.request.build_opener(_SinRedireccion)
    try:
        with abridor.open(peticion, timeout=TIMEOUT_SEGUNDOS) as r:
            return f'Conexión correcta (HTTP {r.status}).'
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ErrorProveedor('El proveedor rechazó la clave API. Verifíquela.')
        if e.code == 404:
            raise ErrorProveedor('El endpoint de prueba no existe (404). '
                                 'Revise la ruta con la documentación del proveedor.')
        if 300 <= e.code < 400:
            raise ErrorProveedor('El proveedor respondió con una redirección; '
                                 'revise la URL base.')
        raise ErrorProveedor(f'El proveedor respondió con error HTTP {e.code}.')
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        raise ErrorProveedor('No se pudo conectar con el proveedor. '
                             'Revise la URL o su conexión a internet.')
