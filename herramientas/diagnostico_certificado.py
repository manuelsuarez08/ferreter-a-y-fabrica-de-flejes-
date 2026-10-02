"""Diagnostico de un certificado de firma REAL (.p12), antes de conectarlo.

POR QUE EXISTE
--------------
El rechazo por certificado erroneo no llega en local: llega en la DIAN, con el
CUIDE ya consumido en la resolucion y con un mensaje generico. Todo lo que este
archivo comprueba se puede comprobar aqui, en el escritorio, en un segundo.

Lo que se mira, en orden de coste:

  1. Si el .p12 abre con la clave. Si no abre, no hay nada mas que mirar.
  2. Si el NIT del titular se puede LEER. Este es el paso que mas falla: el
     sistema busca el NIT en `serialNumber` y en `subjectAltName`, y hay
     certificados de entidades que no lo ponen en ninguno de los dos. Ahi la
     emision se detiene, y con razon: sin NIT legible no hay forma de confirmar
     que el firmante sea el emisor.
  3. Si ese NIT es el del negocio. Aqui es donde uno se equivoca al pedir el
     certificado: se emite a nombre de la persona y el NIT que va en el
     certificado es el de ESA persona, no el de la empresa.
  4. Si la clave sirve para lo que hace el sistema: RSA-2048 o mas.
  5. Vigencia y dias que faltan.

LO QUE NO COMPRUEBA
-------------------
Que la cadena de confianza llegue a una AC de confianza del pais, ni que la
DIAN lo valide. Eso solo se sabe firmando de verdad y mandando a la
habilitacion. Aqui se revisa que el certificado sea utilizable y que pertenezca al
negocio, que es lo que se puede saber sin salir del escritorio.

USO
---
    python herramientas/diagnostico_certificado.py ruta/al/certificado.p12
    python herramientas/diagnostico_certificado.py cert.p12 --clave "mi clave"
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services import dian_firma  # noqa: E402


def _pedir_clave(ruta, dada=None):
    """La clave del .p12.

    Si viene en la linea de comandos se usa; si no, se pregunta. No se adivina:
    un archivo .p12 protegido con clave no tiene forma de abrirse sin ella.
    """
    if dada:
        return dada
    try:
        return getpass.getpass(f'Clave del .p12 ({os.path.basename(ruta)}): ')
    except (EOFError, KeyboardInterrupt):
        sys.exit('\nCancelado.')


def _origen_del_nit(certificado):
    """De que campo salio el NIT: `serialNumber` o `subjectAltName`.

    Se comprueba de verdad en vez de suponer. Cada entidad certificadora lo
    pone en un sitio distinto (Certicámara, ANDES, GSE...), asi que decir
    siempre uno de los dos seria mentirle a quien esta diagnosticando, y en el
    caso de que saliera del SAN el diagnostico le indicaria revisar el campo
    equivocado.
    """
    from cryptography.x509 import ExtensionOID, NameOID

    try:
        atributos = certificado.certificado.subject.get_attributes_for_oid(
            NameOID.SERIAL_NUMBER)
        if atributos:
            return 'serialNumber (Subject)'

        extension = certificado.certificado.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        return 'subjectAltName'
    except Exception:
        return 'un campo que no se pudo identificar'


def _titulo(texto):
    print()
    print(texto)
    print('-' * len(texto))


def main():
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument('ruta', help='Ruta del archivo .p12')
    analizador.add_argument('--clave', help='Clave del .p12 (si no, se pregunta)')
    analizador.add_argument('--nit', help='NIT del emisor a contrastar. Si no se '
                                          'pasa, se lee de la base de trabajo.')
    argumentos = analizador.parse_args()

    ruta = argumentos.ruta
    if not os.path.exists(ruta):
        sys.exit(f'No existe el archivo: {ruta}')

    # El NIT esperado: el que se le pasa, o el de la base de trabajo.
    nit_esperado = argumentos.nit
    if not nit_esperado:
        import ferreteria.config as config
        import sqlite3
        if os.path.exists(config.DB_NAME):
            conexion = sqlite3.connect(config.DB_NAME)
            fila = conexion.execute(
                'SELECT nit FROM configuracion WHERE id = 1').fetchone()
            conexion.close()
            if fila and fila[0]:
                nit_esperado = fila[0]

    clave = _pedir_clave(ruta, argumentos.clave)

    # ── 1. Abre ──────────────────────────────────────────────────────────
    try:
        certificado = dian_firma.cargar_certificado(ruta, clave)
    except dian_firma.ErrorCertificado as error:
        print()
        print('NO ABRE. El sistema no podria cargar este archivo:')
        print(f'  {error}')
        print()
        print('Lo mas probable: la clave esta mal, o el archivo esta corrupto.')
        print('Verifique la clave con la entidad que se lo entrego. El sistema no')
        print('puede distinguir "clave mala" de "archivo danado": ambos fallan')
        print('al abrirlo.')
        return 1
    except Exception as error:
        print(f'\nNO ABRE. Error inesperado: {type(error).__name__}: {error}')
        return 1

    print()
    print(f'Archivo : {ruta}')
    print('Abre correctamente con la clave indicada.')

    # ── 2. Titular ───────────────────────────────────────────────────────
    _titulo('TITULAR')
    sujeto = certificado.certificado.subject
    for atributo in sujeto:
        print(f'  {atributo.oid._name:<28} {atributo.value}')

    _titulo('EMISOR DEL CERTIFICADO (quien lo firmo)')
    for atributo in certificado.certificado.issuer:
        print(f'  {atributo.oid._name:<28} {atributo.value}')

    # ── 3. El NIT: el paso que mas falla ─────────────────────────────────
    _titulo('EL NIT (lo que decide si la emision arranca)')
    nit_leido = certificado.nit_titular

    if nit_leido:
        print(f'  Se pudo leer: {nit_leido}')
        print(f'  Salio de   : {_origen_del_nit(certificado)}')
    else:
        print('  NO SE PUDO LEER.')
        print()
        print('  El sistema busca el NIT en `serialNumber` y en')
        print('  `subjectAltName`, y este certificado no lo tiene en ninguno de')
        print('  los dos. Sin NIT legible no se puede confirmar que el firmante')
        print('  sea el emisor, asi que la emision SE DETIENE (es lo correcto:')
        print('  emitir a ciegas contra la DIAN consume el CUIDE del documento).')
        print()
        print('  Que hacer: pedir a la entidad certificadora un .p12 que incluya')
        print('  el NIT de la empresa en el campo serialNumber del sujeto.')

    if nit_esperado and nit_leido:
        digitos_esperado = ''.join(c for c in nit_esperado if c.isdigit())
        digitos_leido = ''.join(c for c in nit_leido if c.isdigit())
        coincide = (digitos_leido == digitos_esperado
                    or digitos_leido.endswith(digitos_esperado))

        print()
        print(f'  NIT esperado (emisor): {digitos_esperado}')
        print(f'  NIT del certificado : {digitos_leido}')
        print()
        if coincide:
            print('  COINCIDEN: la emision no se detendra por identidad.')
        else:
            print('  NO COINCIDEN: la emision SE DETENDRA antes de firmar.')
            print()
            print('  Ojo con esto, que es el error mas comun: el certificado se pide')
            print('  a nombre de un REPRESENTANTE y el NIT que lleva es el de la')
            print('  PERSONA, no el de la empresa. Si es el caso, no hay arreglo')
            print('  local: o se pide el certificado a nombre de la empresa, o se')
            print('  cambia el NIT del emisor (que para entonces seria el de la')
            print('  persona, y eso ya no es el negocio).')

    # ── 4. La clave ──────────────────────────────────────────────────────
    _titulo('LA CLAVE (RSA-SHA384 es lo que usa el sistema)')
    publica = certificado.certificado.public_key()
    tipo = type(publica).__name__
    print(f'  Tipo            : {tipo}')

    if tipo != 'RSAPublicKey':
        print()
        print('  NO ES RSA. El sistema firma con RSA-SHA384 (lo exige el Anexo')
        print('  Tecnico V1.9), asi que una clave EC no sirve aunque sea valida.')
    else:
        bits = publica.key_size
        print(f'  Tamano          : {bits} bits')
        if bits < 2048:
            print()
            print(f'  INSUFICIENTE.  El Anexo exige 2048 o mas. Con {bits} bits')
            print('  el documento se rechaza por criptografia, no por estructura.')
        else:
            print()
            print('  Compatible con lo que el sistema firma (RSA-SHA384).')

    # ── 5. Vigencia ──────────────────────────────────────────────────────
    _titulo('VIGENCIA')
    try:
        inicio = certificado.certificado.not_valid_before_utc
        fin = certificado.certificado.not_valid_after_utc
    except AttributeError:
        inicio = certificado.certificado.not_valid_before.replace(
            tzinfo=timezone.utc)
        fin = certificado.certificado.not_valid_after.replace(tzinfo=timezone.utc)

    print(f'  Desde           : {inicio:%Y-%m-%d}')
    print(f'  Hasta           : {fin:%Y-%m-%d}')
    print(f'  Dias para vencer: {certificado.dias_para_vencer()}')

    if not certificado.vigente():
        print()
        print('  VENCIDO O AUN NO VIGENTE. Ningun documento se puede firmar con el.')
    elif certificado.dias_para_vencer() < 30:
        print()
        print(f'  Vence en {certificado.dias_para_vencer()} dias. El sistema va a')
        print('  avisar de esto en la pantalla; conviene renovar antes.')

    # ── Veredicto ────────────────────────────────────────────────────────
    _titulo('VEREDICTO')
    problemas = []
    if not nit_leido:
        problemas.append('no se puede leer el NIT del titular')
    elif nit_esperado:
        d_esp = ''.join(c for c in nit_esperado if c.isdigit())
        d_lei = ''.join(c for c in nit_leido if c.isdigit())
        if d_lei != d_esp and not d_lei.endswith(d_esp):
            problemas.append(f'el NIT del certificado ({d_lei}) no es el del '
                             f'emisor ({d_esp})')
    if tipo != 'RSAPublicKey':
        problemas.append('la clave no es RSA')
    elif publica.key_size < 2048:
        problemas.append(f'la clave es de {publica.key_size} bits')
    if not certificado.vigente():
        problemas.append('el certificado no esta vigente')

    if problemas:
        print('  ESTE CERTIFICADO NO SERVIRA TODAVIA:')
        for problema in problemas:
            print(f'    - {problema}')
        print()
        print('  Se puede cargar igual (asi se prueba la UI), pero la emision')
        print('  se detendra en cada intento. Vale la pena corregirlo antes de')
        print('  seguir, porque el error no aparece hasta la DIAN.')
        return 1

    print('  Sirve para firmar.')
    print()
    print('  Ojo: esto NO comprueba que la DIAN lo valide. Solo que el')
    print('  certificado abre, es de este negocio y sirve para el algoritmo que')
    print('  el sistema usa. El visto bueno de la DIAN solo se obtiene firmando')
    print('  en el ambiente de habilitacion.')
    return 0


if __name__ == '__main__':
    sys.exit(main())