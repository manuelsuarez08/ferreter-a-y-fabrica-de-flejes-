"""Servicio de cálculo de flejes (figurado de hierro).

Funciones puras: dada la geometría de un fleje y su calibre, devuelven
perímetro, metros lineales y consumo de hierro en kg. Antes vivían sueltas
en app.py; agruparlas aquí aísla la fórmula y la hace testeable.
"""
import math

# Densidad lineal del acero: kg por mm² de sección por metro.
DENSIDAD_ACERO_KG_MM2_M = 0.00785

# Equivalencias conocidas de calibres a milímetros.
EQUIVALENCIAS_CALIBRE_MM = {
    '1/4"': 6.35, '1/4in': 6.35, '1/4': 6.35,
    '3/8"': 9.53, '3/8in': 9.53, '3/8': 9.53,
    '1/2"': 12.7, '1/2in': 12.7, '1/2': 12.7,
    '5/8"': 15.88, '5/8in': 15.88, '5/8': 15.88,
    '3/4"': 19.05, '3/4in': 19.05, '3/4': 19.05,
    '7/8"': 22.23, '7/8in': 22.23, '7/8': 22.23,
    '1"': 25.4, '1in': 25.4, '1': 25.4,
    '6mm': 6.0, '8mm': 8.0, '10mm': 10.0, '12mm': 12.0,
    '16mm': 16.0, '18mm': 18.0, '20mm': 20.0, '22mm': 22.0,
    '25mm': 25.0, '32mm': 32.0,
}


def parse_calibre_mm(calibre):
    """Convierte un calibre escrito de cualquier forma a milímetros."""
    if calibre is None:
        return 0.0
    texto = str(calibre).strip().lower().replace(' ', '')
    if not texto:
        return 0.0
    if texto in EQUIVALENCIAS_CALIBRE_MM:
        return float(EQUIVALENCIAS_CALIBRE_MM[texto])

    texto_normal = texto.replace('"', '').replace('in', '').replace('″', '')
    if texto_normal.endswith('mm'):
        try:
            return float(texto_normal[:-2])
        except ValueError:
            return 0.0
    if texto_normal.endswith('cm'):
        try:
            return float(texto_normal[:-2]) * 10
        except ValueError:
            return 0.0
    try:
        valor = float(texto_normal)
        return valor if valor > 0 else 0.0
    except ValueError:
        return 0.0


def calcular_consumo_fleje(ancho_cm, largo_cm, largo_gancho_cm, cantidad_piezas, calibre):
    """Calcula perímetro, metros lineales y consumo (kg) de una orden de fleje."""
    ancho = float(ancho_cm or 0)
    largo = float(largo_cm or 0)
    gancho = float(largo_gancho_cm or 0)
    piezas = max(1, int(cantidad_piezas or 1))
    diametro_mm = parse_calibre_mm(calibre)

    perimetro_cm = (2 * (ancho + largo)) + (2 * gancho)
    metros_lineales = (perimetro_cm / 100.0) * piezas

    if diametro_mm <= 0:
        return {
            'perimetro_cm': round(perimetro_cm, 2),
            'metros_lineales': round(metros_lineales, 4),
            'diametro_mm': 0.0,
            'consumo_kg': 0.0,
            'densidad': 0.0,
        }

    area_mm2 = math.pi * ((diametro_mm / 2.0) ** 2)
    consumo_kg = metros_lineales * area_mm2 * DENSIDAD_ACERO_KG_MM2_M
    return {
        'perimetro_cm': round(perimetro_cm, 2),
        'metros_lineales': round(metros_lineales, 4),
        'diametro_mm': round(diametro_mm, 2),
        'consumo_kg': round(consumo_kg, 4),
        'densidad': round(area_mm2 * DENSIDAD_ACERO_KG_MM2_M, 4),
    }
