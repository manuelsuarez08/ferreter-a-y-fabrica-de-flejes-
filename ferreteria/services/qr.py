"""Generador del código QR de la tirilla (URL de consulta de la DIAN).

El Documento Equivalente POS obliga a imprimir un código QR con la URL de
consulta en el catálogo de la DIAN. El QR se genera en el SERVIDOR y se entrega
como PNG, porque la librería que ya usa el POS en el navegador (`html5-qrcode`)
solo LEE códigos: no los genera.
"""
from __future__ import annotations

import base64
import struct
import zlib

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.exceptions import DataOverflowError

NIVEL_CORRECCION = ERROR_CORRECT_M
BORDE_MODULOS = 4
ESCALA_POR_DEFECTO = 6
VERSION_MAXIMA = 12


def _matriz(texto):
    """Matriz de módulos del QR (1 = negro, 0 = blanco), sin margen."""
    if not texto:
        return None

    try:
        codigo = qrcode.QRCode(
            version=None,
            error_correction=NIVEL_CORRECCION,
            box_size=1,
            border=0,
        )
        codigo.add_data(texto)
        codigo.make(fit=True)

        if codigo.version > VERSION_MAXIMA:
            return None

        return [[1 if modulo else 0 for modulo in fila]
                for fila in codigo.get_matrix()]
    except (ValueError, DataOverflowError):
        # Evita la excepción cuando el texto excede la versión 40 del QR
        return None


def _renderizar(matriz, escala, borde):
    columnas = len(matriz[0])
    lado = columnas * escala + 2 * borde * escala
    blanco = b'\xff\xff\xff' * lado
    margen = b'\x00' + blanco

    salida = bytearray()
    for _ in range(borde * escala):
        salida.extend(margen)

    for fila in matriz:
        linea = bytearray()
        linea.extend(b'\xff\xff\xff' * (borde * escala))
        for modulo in fila:
            linea.extend((b'\x00\x00\x00' if modulo else b'\xff\xff\xff') * escala)
        linea.extend(b'\xff\xff\xff' * (borde * escala))
        for _ in range(escala):
            salida.extend(b'\x00' + bytes(linea))

    for _ in range(borde * escala):
        salida.extend(margen)

    return lado, lado, bytes(salida)


def _png(ancho, alto, filas_con_filtro):
    def bloque(tipo, datos):
        return (struct.pack('>I', len(datos)) + tipo + datos
                + struct.pack('>I', zlib.crc32(tipo + datos) & 0xFFFFFFFF))

    cabecera = struct.pack('>IIBBBBB', ancho, alto, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n'
            + bloque(b'IHDR', cabecera)
            + bloque(b'IDAT', zlib.compress(filas_con_filtro, 9))
            + bloque(b'IEND', b''))


def qr_png(texto, escala=ESCALA_POR_DEFECTO, borde=BORDE_MODULOS):
    try:
        matriz = _matriz(texto)
        if matriz is None:
            return None
        ancho, alto, pixeles = _renderizar(matriz, escala, borde)
        return _png(ancho, alto, pixeles)
    except (ValueError, DataOverflowError):
        return None


def qr_data_uri(texto, escala=ESCALA_POR_DEFECTO, borde=BORDE_MODULOS):
    png = qr_png(texto, escala=escala, borde=borde)
    if png is None:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
