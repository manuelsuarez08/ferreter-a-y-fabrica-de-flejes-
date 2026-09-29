"""Pruebas del generador de QR de la tirilla (ferreteria/services/qr.py).

El QR del Documento Equivalente POS es un requisito del anexo técnico: va
impreso en la tirilla y es lo que le permite al cliente consultar su documento
en el catálogo de la DIAN. Un QR que "se ve bien" pero que ningún lector puede
decodificar es una tirilla inservible, así que estas pruebas NO comprueban el
aspecto del código: lo DECODIFICAN y verifican que devuelva el texto original.

Para decodificar se usa OpenCV (`opencv-python-headless`), que implementa el
estándar de forma independiente al generador. Si OpenCV no está instalado, las
pruebas de decodificación se SALTAN (no se marcan como aprobadas): es mejor no
dar por bueno un QR que no se ha podido verificar.

Uso:
    .venv/Scripts/python.exe -m pytest tests/test_qr.py -q
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ferreteria.services.dian_pos import calcular_cuide, url_consulta  # noqa: E402
from ferreteria.services.qr import (  # noqa: E402
    ESCALA_POR_DEFECTO,
    VERSION_MAXIMA,
    _matriz,
    qr_data_uri,
    qr_png,
)

# OpenCV es solo para las pruebas: el POS no lo necesita para generar el QR.
try:
    import cv2
    import numpy as np
    HAY_OPENCV = True
except ImportError:
    HAY_OPENCV = False

requiere_opencv = pytest.mark.skipif(
    not HAY_OPENCV,
    reason='OpenCV no está instalado: no se puede verificar el QR, y un QR sin '
           'verificar no se da por bueno',
)


def decodificar(png):
    """Decodifica el PNG y devuelve el texto, o '' si el lector no lo encuentra."""
    imagen = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    texto, _, _ = cv2.QRCodeDetector().detectAndDecode(imagen)
    return texto


# ── El caso que importa: la URL real del documento ───────────────────────────
@requiere_opencv
def test_qr_de_la_url_real_de_la_dian_se_decodifica():
    """El QR con la URL real (base + CUIDE de 96 hex) debe ser legible.

    Es el caso del documento real: si esto falla, la tirilla que recibe el
    cliente lleva un QR que no sirve para nada.
    """
    cuide = calcular_cuide(
        num_documento='POS-1042', fecha='2026-09-27', hora='14:03:07',
        val_imp1=1900, val_imp2=0, val_total=11900, nit='900187391',
        tipo_documento='POS', clave_tecnica='CLAVE-TECNICA-123',
        tipo_ambiente='2',
    )
    url = url_consulta(cuide, fecha='2026-09-27', nit='900187391', total=11900)

    assert len(url) > 140, 'la URL del documento debería ser larga'
    assert decodificar(qr_png(url)) == url


@requiere_opencv
@pytest.mark.parametrize('texto', [
    'A',
    'HOLA MUNDO',
    'POS-1042',
    'Tubo PVC 1/2" & 3/4 - Ferretería',
    'https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=x',
    'x' * 200,
])
def test_qr_se_decodifica_en_varios_tamanos(texto):
    """Distintas longitudes (y caracteres raros) deben producir un QR legible.

    Recorre varias versiones del estándar: una que solo funciona en la versión 1
    (por casualidad) no sirve para la URL del documento.
    """
    assert decodificar(qr_png(texto)) == texto


@requiere_opencv
def test_qr_conserva_los_caracteres_del_cuide():
    """El CUIDE es hexadecimal, pero la URL lleva ':' y '?': deben sobrevivir."""
    url = ('https://catalogo-vpfe.dian.gov.co/document/searchqr'
           '?documentkey=0123456789abcdef' * 3)
    assert decodificar(qr_png(url)) == url


# ── Estructura del PNG ───────────────────────────────────────────────────────
def test_qr_png_es_un_png_valido():
    """La firma del PNG debe ser la correcta (los visores son estrictos)."""
    png = qr_png('PRUEBA')
    assert png is not None
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    assert png.endswith(b'IEND\xaeB`\x82')


def test_qr_png_es_blanco_y_negro_puro():
    """La tirilla térmica es de 2 tonos: no puede haber grises intermedios.

    Un gris en un módulo deja al lector sin saber si es un 1 o un 0.
    """
    from ferreteria.services.qr import _renderizar
    matriz = _matriz('PRUEBA')
    ancho, alto, pixeles = _renderizar(matriz, escala=4, borde=4)

    # Las líneas llevan un byte de filtro PNG al inicio; se salta.
    colores = set()
    for fila in range(alto):
        inicio = fila * (1 + ancho * 3) + 1
        linea = pixeles[inicio:inicio + ancho * 3]
        for i in range(0, len(linea), 3):
            colores.add(linea[i:i + 3])
    assert colores <= {b'\x00\x00\x00', b'\xff\xff\xff'}, \
        f'el QR tiene colores intermedios: {colores}'


def test_qr_tiene_el_margen_minimo_del_estandar():
    """El estándar exige al menos 4 módulos de margen blanco.

    Sin margen, muchos lectores no enganchan el código: es el error más común
    al imprimir QR en tirillas.
    """
    matriz = _matriz('PRUEBA')
    escala, borde = 3, 4
    from ferreteria.services.qr import _renderizar
    ancho, alto, pixeles = _renderizar(matriz, escala, borde)

    filas_matriz = len(matriz)
    esperado = filas_matriz * escala + 2 * borde * escala
    assert ancho == esperado and alto == esperado

    # La primera franja de píxeles debe ser blanca.
    for fila in range(borde * escala):
        inicio = fila * (1 + ancho * 3) + 1
        assert pixeles[inicio:inicio + ancho * 3] == b'\xff\xff\xff' * ancho


def test_qr_data_uri_es_un_data_uri_valido():
    uri = qr_data_uri('PRUEBA')
    assert uri.startswith('data:image/png;base64,')
    import base64
    assert base64.b64decode(uri.split(',', 1)[1]).startswith(b'\x89PNG')


def test_qr_vacio_no_genera_codigo():
    """Sin texto no se genera QR (mejor sin QR que con un cuadrado sin sentido)."""
    assert qr_png('') is None
    assert qr_data_uri('') == ''


def test_qr_texto_demasiado_largo_no_genera_codigo():
    """Si la URL no cupiera, se devuelve None en vez de un QR ilegible."""
    texto = 'x' * 4000
    assert qr_png(texto) is None, (
        f'debería rechazar el texto en vez de pasar de la versión {VERSION_MAXIMA}'
    )


def test_la_url_real_cabe_en_el_limite_de_version():
    """Guardia: la URL del documento NO debe acercarse al tope de versión.

    Si la DIAN alarga la URL o cambia el formato del CUIDE, la tirilla se
    quedaría sin QR. Esta prueba falla ANTES de llegar a ese punto.
    """
    cuide = 'a' * 96
    url = url_consulta(cuide, fecha='2026-09-27', nit='900187391', total=11900)
    matriz = _matriz(url)
    assert matriz is not None, 'la URL del documento no cabe en el límite de versión'

    version = (len(matriz) - 17) // 4
    assert version < VERSION_MAXIMA, (
        f'la URL usa la versión {version}, pegada al límite {VERSION_MAXIMA}: '
        'si la DIAN la alarga un poco la tirilla se queda sin QR'
    )


def test_escala_por_defecto_da_un_qr_imprimible():
    """El QR debe caber en la tirilla (58 mm a 203 dpi ≈ 460 px de ancho)."""
    ancho_px = 58 / 25.4 * 203
    matriz = _matriz(url_consulta('a' * 96, fecha='2026-09-27'))
    lado = len(matriz) * ESCALA_POR_DEFECTO + 2 * 4 * ESCALA_POR_DEFECTO
    assert lado < ancho_px, (
        f'el QR ({lado}px) no cabe en el ancho útil de la tirilla ({ancho_px:.0f}px)'
    )