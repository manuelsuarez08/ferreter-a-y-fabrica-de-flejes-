"""El ORDEN de los hijos de `Invoice` lo decide el XSD, no el orden del codigo.

ESTE ARCHIVO EXISTE POR UN MOTIVO CONCRETO
-------------------------------------------
`Invoice` es una `xsd:sequence` en UBL 2.1: los hijos tienen que ir en un orden
determinado y dos nodos con el contenido CORRECTO pero en orden distinto producen
un documento que no valida. El rechazo llega con un mensaje generico que no
senala el problema, y la aritmetica sigue cuadrando, asi que nada en las pruebas
anteriores lo delataba.

Defectos reales que estas pruebas fijan:
  - `ext:UBLExtensions` se anadia DESPUES de los totales, cuando el XSD lo quiere
    al principio (ahi vive `sts:DianExtensions`, el bloque de la DIAN).
  - La referencia al documento corregido iba en `cac:AdditionalDocumentReference`
    como hijo DIRECTO de la raiz, y en su lugar como `cac:BillingReference`.
  - `BillingReference` se anadia al final, cuando va entre `OrderReference` y
    `DespatchAdvice`.
  - `LineCountNumeric` quedaba tras `DiscrepancyResponse`.

Y ademas fija el normalizador: que no pierda nodos y que sea idempotente.
"""
from __future__ import annotations

import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


@pytest.fixture(scope='module', autouse=True)
def xmls_generados():
    """Genera los XML ANTES de leerlos del disco.

    Leer un archivo de `_auditoria_xml` sin regenerarlo primero es una fuente
    clasica de falsos negativos: el archivo en disco es del ultimo script que se
    ejecuto, no del codigo que se esta probando. Pasa justo lo contrario de lo
    que uno cree —una prueba verde sobre un binario viejo.
    """
    import subprocess

    subprocess.run(
        [sys.executable,
         os.path.join(RAIZ, 'herramientas', 'generar_xml_auditoria.py')],
        cwd=RAIZ, capture_output=True, text=True, timeout=180)


# ═══════════════════════════════════════════
# 8. El orden de los hijos lo DECIDE EL XSD, no el código
# ═══════════════════════════════════════════

@pytest.mark.parametrize('nombre', ['invoice.xml', 'creditnote.xml'])
def test_el_orden_de_los_hijos_cumple_la_secuencia_del_xsd(nombre):
    """`Invoice` es una `xsd:sequence`: el orden NO es libre.

    Este es el fallo queCommit anterior no miraba. El contenido era correcto y la
    aritmética cuadraba, pero:
      - `UBLExtensions` se añadian DESPUÉS de los totales, cuando el XSD lo quiere
        al principio (ahí vive `sts:DianExtensions`);
      - `BillingReference` se añadian al final, cuando va entre `OrderReference`
        y `DespatchAdvice`.

    Dos nodos con el contenido correcto pero en orden distinto producen un
    documento que NO valida, y el rechazo llega con un mensaje que no señala el
    problema.
    """
    import xml.etree.ElementTree as ET

    from ferreteria.services import dian_xml

    raiz = ET.parse(os.path.join(RAIZ, '_auditoria_xml', nombre)).getroot()
    orden = [hijo.tag.split('}')[-1] for hijo in raiz]

    posiciones = {nombre: i for i, nombre in enumerate(dian_xml.SECUENCIA_INVOICE)}
    desconocidas = [n for n in orden if n not in posiciones]
    assert not desconocidas, (
        f'{nombre} tiene nodos que no están en la secuencia del XSD: '
        f'{desconocidas}. Si se añadieron al generador, hay que añadirlos '
        'también a SECUENCIA_INVOICE.')

    # Cada nodo conocido debe aparecer despues del anterior en la secuencia.
    # Se comparan solo los conocidos, para no alterar el orden interno.
    indices = [posiciones[n] for n in orden if n in posiciones]
    assert indices == sorted(indices), (
        f'{nombre} no respeta la secuencia del XSD.\n'
        f'  orden emitido : {orden}\n'
        f' Indices        : {indices}\n'
        '  el XSD exige   :UBLVersionID, ID, UUID, IssueDate, ... '
        'BillingReference, DespatchAdvice, AccountingSupplierParty, '
        'AccountingCustomerParty, PaymentMeans, TaxTotal, '
        'LegalMonetaryTotal, InvoiceLine, DiscrepancyResponse')


def test_las_extensiones_van_al_principio():
    """`ext:UBLExtensions` es lo primero del documento, por XSD."""
    import xml.etree.ElementTree as ET

    raiz = ET.parse(os.path.join(RAIZ, '_auditoria_xml', 'creditnote.xml')).getroot()
    primero = list(raiz)[0].tag.split('}')[-1]
    assert primero == 'UBLExtensions', (
        f'el primer hijo es <{primero}>; el XSD quiere <UBLExtensions>, donde '
        'vive sts:DianExtensions')


def test_el_normalizador_deja_intacto_un_arbol_ya_ordenado():
    """Normalizar dos veces no puede romper nada.

    Sin esto, un día que se llame dos veces desde rutas distintas el árbol se
    desordenaría por un orden inverso y nadie sabría de dónde salió.
    """
    import xml.etree.ElementTree as ET

    from ferreteria.services import dian_xml

    raiz = ET.parse(os.path.join(RAIZ, '_auditoria_xml', 'creditnote.xml')).getroot()
    antes = [hijo.tag.split('}')[-1] for hijo in raiz]

    assert dian_xml._normalizar_secuencia(raiz) is False, (
        'el árbol ya estaba ordenado y el normalizador dice que lo movió')
    despues = [hijo.tag.split('}')[-1] for hijo in raiz]
    assert antes == despues


def test_el_normalizador_conserva_el_contenido():
    """Reordenar no puede perder ni duplicar nodos."""
    import xml.etree.ElementTree as ET

    from ferreteria.services import dian_xml

    raiz = ET.parse(os.path.join(RAIZ, '_auditoria_xml', 'creditnote.xml')).getroot()
    identificadores = [hijo.get('ID') for hijo in raiz]
    total = len(list(raiz))

    dian_xml._normalizar_secuencia(raiz)

    assert len(list(raiz)) == total, 'el normalizador cambió el número de nodos'
    assert [hijo.get('ID') for hijo in raiz] == identificadores
