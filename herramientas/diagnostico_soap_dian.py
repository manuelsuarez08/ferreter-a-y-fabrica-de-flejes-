"""Diagnóstico del consumo del servicio SOAP de la DIAN.

Sirve para responder UNA pregunta con evidencia: ¿el cuelgue es de nuestro
código o del servicio? Se ejecuta a mano, no en la suite, porque depende de la
red y porque manda peticiones reales.

    python herramientas/diagnostico_soap_dian.py

Cada paso imprime QUÉ se mandó y QUÉ respondió, con el tiempo. La comparación
importante es la última: si una operación INEXISTENTE también se cuelga, el
problema no está en nuestra petición.
"""
from __future__ import annotations

import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

from ferreteria.services import dian_pos, dian_soap  # noqa: E402

TIMEOUT = 40


def titulo(texto):
    print()
    print('=' * 72)
    print(texto)
    print('=' * 72)


def red():
    """DNS, puerto y TLS. Si esto falla, no tiene sentido seguir."""
    titulo('1. LA RED LLEGA AL SERVICIO')
    host = dian_soap._endpoint(dian_pos.AMBIENTE_HABILITACION).split('/')[2]
    try:
        ip = socket.gethostbyname(host)
        print(f'  DNS   {host} -> {ip}')
    except OSError as e:
        print(f'  DNS   FALLA: {e}')
        return False

    try:
        socket.create_connection((host, 443), timeout=15).close()
        print('  TCP   puerto 443 abierto')
    except OSError as e:
        print(f'  TCP   FALLA: {e}')
        return False

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert()
                emisor = dict(x[0] for x in cert.get('issuer', ()))
                print(f'  TLS   OK · {emisor.get("commonName")}')
                print(f'        vence {cert.get("notAfter")}')
    except Exception as e:
        print(f'  TLS   FALLA: {type(e).__name__}: {e}')
        return False
    return True


def descriptor():
    """El WSDL. Si baja, el servicio está vivo y declara sus operaciones."""
    titulo('2. EL DESCRIPTOR (?wsdl)')
    url = dian_soap._endpoint(dian_pos.AMBIENTE_HABILITACION) + '?wsdl'
    t = time.time()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            cuerpo = r.read().decode('utf-8', 'replace')
        print(f'  HTTP {r.status} en {time.time() - t:.1f}s · {len(cuerpo)} bytes')
    except Exception as e:
        print(f'  FALLA: {type(e).__name__}: {e}')
        return False

    operaciones = sorted(set(re.findall(r'<wsdl:operation name="([^"]+)"', cuerpo)))
    ns = re.search(r'targetNamespace="([^"]+)"', cuerpo)
    direccion = re.search(r'<(?:\w+:)?address location="([^"]+)"', cuerpo)
    print(f'  targetNamespace : {ns.group(1) if ns else "?"}')
    print(f'  direccion       : {direccion.group(1) if direccion else "?"}')
    print(f'  operaciones ({len(operaciones)}): {", ".join(operaciones)}')

    # Se contrasta lo que el código manda contra lo que el servicio declara.
    print()
    print('  Lo que manda este codigo:')
    print(f'    namespace envelope : {dian_soap.NS_SOAP}')
    print(f'    namespace wcf      : {dian_soap.NS_WCF}')
    print(f'    SOAPAction base    : {dian_soap.SOAP_ACTION_BASE}')
    if ns and ns.group(1) == dian_soap.NS_WCF:
        print('    -> el namespace coincide con el del descriptor')
    else:
        print('    -> OJO: el namespace NO coincide')
    return True


def _enviar(etiqueta, url, sobre, accion):
    t = time.time()
    peticion = urllib.request.Request(
        url, data=sobre, method='POST',
        headers={
            'Content-Type': 'application/soap+xml; charset=utf-8; action="'
            + accion + '"',
            'User-Agent': 'FerreControl-DIAN/1.0',
        },
    )
    try:
        with urllib.request.urlopen(peticion, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            cuerpo = r.read().decode('utf-8', 'replace')
        print(f'  {etiqueta:34} HTTP {r.status} en {time.time() - t:5.1f}s')
        return cuerpo
    except urllib.error.HTTPError as e:
        detalle = e.read().decode('utf-8', 'replace')[:200].replace('\n', ' ')
        print(f'  {etiqueta:34} HTTP {e.code} en {time.time() - t:5.1f}s')
        print(f'       {detalle}')
        return ''
    except Exception as e:
        print(f'  {etiqueta:34} {type(e).__name__} en {time.time() - t:5.1f}s')
        return ''


def operaciones():
    """Tres sobres: uno vacío, uno válido y uno con operación inexistente."""
    titulo('3. CÓMO RESPONDE EL SERVICIO')
    base = dian_soap._endpoint(dian_pos.AMBIENTE_HABILITACION)
    accion = dian_soap.SOAP_ACTION_BASE

    vacio = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{dian_soap.NS_SOAP}">'
        '<soap:Body/></soap:Envelope>'
    ).encode('utf-8')

    get_status = dian_soap._sobre_soap(
        'GetStatus',
        '<wcf:trackId>00000000-0000-0000-0000-000000000000</wcf:trackId>')
    inexistente = dian_soap._sobre_soap(
        'OperacionQueNoExiste', '<wcf:trackId>x</wcf:trackId>')

    print(f'  URL: {base}\n')
    _enviar('sobre VACIO (debe fallar rapido)', base, vacio, accion + 'GetStatus')
    _enviar('GetStatus bien formado', base, get_status, accion + 'GetStatus')
    _enviar('operacion INEXISTENTE', base, inexistente,
            accion + 'OperacionQueNoExiste')

    print()
    print('  LECTURA: si la "operacion inexistente" tambien se cuelga, el')
    print('  problema esta ANTES de la aplicacion (red o servicio), no en la')
    print('  peticion. Un servidor que validara el SOAPAction responderia un')
    print('  error rapido en ese caso.')


def main():
    print(f'Hora: {time.strftime("%d/%m/%Y %H:%M:%S")}')
    print(f'Ambiente: habilitacion · timeout por prueba: {TIMEOUT}s')
    if not red():
        return 1
    descriptor()
    operaciones()
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
