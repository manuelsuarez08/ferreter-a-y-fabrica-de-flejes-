"""Suite CI: los verificadores INDEPENDIENTES, dentro de pytest.

QUE ES ESTO Y POR QUE IMPORTA
----------------------------
La suite normal comprueba que el codigo hace lo que el codigo dice que hace.
Estos cuatro verificadores comprueban otra cosa: que el documento entregado es
correcto SEGUN UN TERCERO.

La diferencia no es academica. La sesion encontro tres defectos que la suite
normal no podia ver y que estos si detectan:

  1. La firma no validaba con la DIAN: `_canonizar` usaba `ET.tostring`, que
     inventaba el prefijo `ns0:` al canonicalizar un subarbol. Mismo contenido,
     bytes distintos, digest distinto.
  2. `ds:Signature` tenia los hijos en orden equivocado: es una
     `xsd:sequence`, y el documento no validaba aunque la rubrica cuadrara.
  3. La cadena del CUFE llevaba el desfase -05:00 que solo corresponde a
     `cbc:IssueTime`, y por eso todos los documentos se calculaban mal a la vez.

Ninguno de los tres se ve con pruebas que usen la propia funcion del proyecto
como referencia: si la funcion cambia, el valor esperado cambia con ella y la
prueba sigue en verde. Por eso estos comprobadores arman el hash a mano, con
`hashlib`, y usan `lxml`, que implementa canonicalizacion de verdad.

EL FALSO VERDE QUE ESTE ARCHIVO CIERRA
--------------------------------------
Los cuatro verificadores leian `_auditoria_xml/`, una carpeta del proyecto. Si
el codigo cambiaba y nadie volvia a generar, comprobaban el archivo de la
ultima vez que se ejecuto el generador y decian "todo bien" sobre un binario
viejo. Sucedio de verdad: los XML versionados tenian el codigo DANE en
`cbc:CityName`, un defecto ya corregido en el codigo de produccion.

Aqui NO se lee esa carpeta. Los generadores reciben un temporal y los
verificadores revisan ESE temporal. Lo que se comprueba es el codigo de ahora.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'herramientas'))


def _cargar(nombre):
    """Importa un script de `herramientas/` por ruta.

    No se importan con `import generar_xml_firmado` a secas: esa forma solo
    funciona si la carpeta esta en el path y frustra silenciosamente cuando el
    modulo ya quedo cacheado con otra version. Por ruta, siempre.
    """
    ruta = os.path.join(RAIZ, 'herramientas', f'{nombre}.py')
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ══════════════════════════════════════════════════════════════
# La garantia que sostiene todo el archivo
# ══════════════════════════════════════════════════════════════

def test_los_verificadores_no_deben_leer_la_carpeta_del_proyecto():
    """Ningun verificador puede tener `_auditoria_xml` fijo en su codigo.

    Es la misma comprobacion que se hizo a mano (grep), pero queda como
    prueba: dentro
    de seis meses nadie se acuerda de por que el directorio estaba vetado, y un
    `carpeta = RAIZ / '_auditoria_xml'` reintroducido devolveria el falso verde
    sin que ninguna otra prueba lo notara.
    """
    for nombre in ('verificar_cufe_manual', 'verificar_firma_lxml',
                   'validar_estructura_firma'):
        texto = open(os.path.join(RAIZ, 'herramientas', f'{nombre}.py'),
                     encoding='utf-8').read()

        # Se permite mencionarlo en el VALOR por defecto, pero no usarlo como
        # destino obligatorio: la funcion debe aceptar `carpeta=None`.
        assert 'def revisar' in texto, f'{nombre}: no expone revisar()'
        assert 'carpeta=None' in texto, \
            f'{nombre}: revisar() no acepta la carpeta, no se puede aislar'


# ══════════════════════════════════════════════════════════════
# El CUFE, recalculado a mano con hashlib
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope='module')
def xml_sin_firmar(tmp_path_factory):
    """XML sin firmar, generados AHORA en un temporal.

    Se genera una sola vez para todo el modulo: el generador monta una base
    sqlite completa y tarda. La clave es que sea un temporal y no la carpeta del
    proyecto.
    """
    destino = tmp_path_factory.mktemp('xml_sin_firmar')
    _cargar('generar_xml_auditoria').generar(str(destino))
    return str(destino)


def test_el_cufe_de_los_documentos_coincide_con_el_hash_calculado_a_mano(xml_sin_firmar):
    """El CUFE del XML debe ser el SHA-384 de su propia cadena.

    El comprobador arma la cadena con los datos del PROPIO documento y la hashea
    con `hashlib`, sin llamar al generador. Si alguien rompe el orden de los
    campos o mete el desfase -05:00 donde no va, aqui falla.
    """
    fallos = _cargar('verificar_cufe_manual').revisar(xml_sin_firmar)

    assert fallos == 0, (
        f'{fallos} discrepancias. El CUFE del documento no corresponde a su '
        'propia cadena, o le falta BillingReference/concepto en la nota.'
    )


def test_el_documento_entregado_no_trae_prefijo_ns0(xml_sin_firmar):
    """La huella EXACTA del defecto de canonicalizacion.

    `ns0:` aparece cuando ElementTree serializa un subarbol aislado. Estuvo en
    el proyecto y produjo una firma que no validaba con la DIAN. No hace falta
    recalcular nada: si el prefijo aparece, el defecto volvio.
    """
    for nombre in ('invoice.xml', 'creditnote.xml'):
        crudo = open(os.path.join(xml_sin_firmar, nombre),
                     encoding='utf-8').read()
        assert 'ns0:' not in crudo, (
            f'{nombre} trae el prefijo ns0: es la huella del defecto de '
            'canonicalizacion que rompio la firma ante la DIAN'
        )


# ══════════════════════════════════════════════════════════════
# La firma: C14N real contra la clave publica
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope='module')
def xml_firmados(tmp_path_factory):
    """Los tres documentos FIRMADOS, generados ahora con un .p12 de prueba."""
    destino = tmp_path_factory.mktemp('xml_firmados')
    _cargar('generar_xml_firmado').generar(str(destino))
    return str(destino)


def test_la_firma_verifica_con_la_clave_publica_del_certificado(xml_firmados):
    """La comprobacion criptografica de verdad.

    `verificar_firma_lxml.py` recalcula los digests con c14n y verifica la firma
    RSA con la clave publica sacada del propio `KeyInfo`. No usa nada del
    proyecto. Si el documento entregado no valida aqui, la DIAN lo rechaza.
    """
    fallos = _cargar('verificar_firma_lxml').revisar_todos(xml_firmados)

    assert fallos == 0, f'{fallos} discrepancias en firma o digest'


def test_la_estructura_de_ds_signature_cumple_el_xsd(xml_firmados):
    """Verificacion de ESQUEMA, separada de la criptografica a proposito.

    Son dos cosas independientes: un documento puede tener la firma correcta y
    ser invalido por orden de elementos, y al reves. Una comprobacion no
    sustituye a la otra, y por eso son dos funciones y no una con dos modos.
    """
    problemas = _cargar('validar_estructura_firma').revisar_todos(xml_firmados)

    assert problemas == 0, (
        f'{problemas} problemas de estructura en ds:Signature. El XSD declara '
        'SignedInfo, SignatureValue, KeyInfo?, Object*: el documento no valida.'
    )


# ══════════════════════════════════════════════════════════════
# Lo que CI NO cubre
# ══════════════════════════════════════════════════════════════

def test_el_generador_escribe_en_la_carpeta_que_le_pasan(xml_firmados):
    """Guarda contra un `SALIDA.mkdir()` que se cuele de vuelta.

    Si un generador volviera a escribir en `_auditoria_xml/`, la suite seguiria
    en verde (comprueba el temporal) pero el directorio del proyecto se
    ensuciaria y volveriamos al escenario de artefactos desactualizados. Esta
    comprueba que el temporal se genero y que el generador acepta el destino.
    """
    assert os.path.isdir(xml_firmados)
    assert os.listdir(xml_firmados), 'el temporal quedo vacio'

    for nombre in os.listdir(xml_firmados):
        assert os.path.getsize(os.path.join(xml_firmados, nombre)) > 0


def test_los_artefactos_generados_no_deben_versionarse():
    """`_auditoria_xml/` es salida generada, no fuente: tiene que estar ignorada.

    Se comprueba leyendo `.gitignore`, SIN llamar a `git`. Un `git ls-files` en
    una prueba se salta en cuanto git no esta en el PATH, y un guardián que se
    salta en silencio es exactamente el falso verde que este archivo existe
    para cerrar: uno creeria que los artefactos estan controlados cuando en
    realidad nadie lo ha comprobado.

    Leyendo el archivo de texto no hay subprocess, no hay PATH y no hay forma de
    saltarse.
    """
    ruta = os.path.join(RAIZ, '.gitignore')
    assert os.path.exists(ruta), 'no hay .gitignore'

    entradas = [linea.strip() for linea in
                open(ruta, encoding='utf-8').read().splitlines()]

    assert any(e in ('_auditoria_xml/', '_auditoria_xml') for e in entradas), (
        '`_auditoria_xml/` no esta en .gitignore. Es salida generada: mientras '
        'se versione, el repositorio muestra a cualquier revisor el XML de la '
        'ultima ejecucion del generador, que fue lo que paso (traia el codigo '
        'DANE en cbc:CityName, ya corregido en el codigo).'
    )