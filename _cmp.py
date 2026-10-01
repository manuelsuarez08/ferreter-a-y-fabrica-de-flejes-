"""Compara la invocacion con ?wsdl y sin ?wsdl, con la respuesta cruda."""

import ssl
import time
import urllib.error
import urllib.request

from ferreteria.services import dian_soap

SERVICE = 'https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc'
ACTION = dian_soap.SOAP_ACTION_BASE + 'GetStatus'

sobre = (
    '<?xml version="1.0" encoding="utf-8"?>'
    f'<soap:Envelope xmlns:soap="{dian_soap.NS_SOAP}" '
    f'xmlns:wcf="{dian_soap.NS_WCF}">'
    '<soap:Body><wcf:trackId>00000000-0000-0000-0000-000000000000'
    '</wcf:trackId></soap:Body>'
    '</soap:Envelope>'
).encode('utf-8')

print('Sobre:', len(sobre), 'bytes')
print('SOAP 1.2:', dian_soap.NS_SOAP)
print('Action  :', ACTION)
print()

for etiqueta, url in (('sin ?wsdl (segun el WSDL)', SERVICE),
                      ('con ?wsdl (como esta hoy)', SERVICE + '?wsdl')):
    peticion = urllib.request.Request(
        url, data=sobre, method='POST',
        headers={
            'Content-Type':
                f'application/soap+xml; charset=utf-8; action="{ACTION}"',
            'Content-Length': str(len(sobre)),
            'User-Agent': 'FerreControl/1.0',
        })
    t = time.time()
    print(f'--- {etiqueta} ---')
    try:
        with urllib.request.urlopen(
            peticion, timeout=40, context=ssl.create_default_context()
        ) as r:
            cuerpo = r.read().decode('utf-8', 'replace')
            print(f'    HTTP {r.status} en {time.time() - t:.1f}s')
            print('    ', cuerpo[:500].replace('\n', ' '))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode('utf-8', 'replace')
        print(f'    HTTP {e.code} en {time.time() - t:.1f}s')
        print('    ', cuerpo[:500].replace('\n', ' '))
    except Exception as e:
        print(f'    {type(e).__name__} en {time.time() - t:.1f}s: {e}')
    print()