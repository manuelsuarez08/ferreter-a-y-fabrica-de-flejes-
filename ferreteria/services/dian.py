"""Utilidades tributarias DIAN: dígito de verificación, redondeo y validaciones.

Responsabilidad única: concentrar las reglas de negocio tributario colombiano
que necesitan los blueprints (clientes, ventas), sin acoplarse a Flask ni a
SQLite. Así el cálculo del DV o el cuadre de totales se puede probar de forma
aislada y reutilizar en scripts.

Nota: este módulo NO calcula el CUFE ni firma el XML; solo prepara y valida los
datos de entrada tributarios.
"""
from decimal import Decimal, ROUND_HALF_UP

# Pesos oficiales del algoritmo del dígito de verificación del NIT (DIAN).
# Se aplican de derecha a izquierda sobre los dígitos del NIT.
_PESOS_DV = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)

# Unidades de medida UN/ECE más usadas en ferretería y fábrica de flejes.
# El código viaja al XML; la etiqueta es solo para mostrar en la interfaz.
UNIDADES_MEDIDA = (
    ('94', 'Unidad'),
    ('C62', 'Pieza'),
    ('KGM', 'Kilogramo'),
    ('GRM', 'Gramo'),
    ('TNE', 'Tonelada'),
    ('MTR', 'Metro'),
    ('MTK', 'Metro cuadrado'),
    ('MTQ', 'Metro cúbico'),
    ('LTR', 'Litro'),
    ('BLL', 'Bulto'),
    ('PAR', 'Par'),
    ('KTM', 'Kilómetro'),
    ('SET', 'Juego'),
    ('BG', 'Bolsa'),
    ('RO', 'Rollo'),
)

# Responsabilidades fiscales DIAN que un tercero puede declarar.
RESPONSABILIDADES_FISCALES = (
    ('O-13', 'O-13 Gran contribuyente'),
    ('O-15', 'O-15 Autorretenedor'),
    ('O-23', 'O-23 Agente de retención IVA'),
    ('O-47', 'O-47 Régimen simple de tributación'),
    ('R-99-PN', 'R-99-PN No responsable / Persona natural'),
)

# Tipos de documento de identidad reconocidos en el sistema.
TIPOS_DOCUMENTO = (
    ('CC', 'Cédula de ciudadanía', '13'),
    ('NIT', 'NIT', '31'),
    ('CE', 'Cédula de extranjería', '22'),
    ('PP', 'Pasaporte', '41'),
    ('TI', 'Tarjeta de identidad', '12'),
    ('RC', 'Registro civil', '11'),
    ('DE', 'Documento extranjero', '21'),
)

# Tipos de operación del documento electrónico (anexo técnico DIAN).
TIPOS_OPERACION = (
    ('10', 'Venta estándar'),
    ('09', 'Venta de activos fijos'),
    ('11', 'Mandato / AIU (construcción)'),
    ('12', 'Transporte'),
    ('13', 'Cambiaria'),
    ('14', 'Exportación'),
)

REGIMENES_FISCALES = ('Responsable de IVA', 'No Responsable de IVA')


def calcular_digito_verificacion(nit):
    """Calcula el DV de un NIT con el algoritmo oficial de la DIAN.

    Se recorren los dígitos del NIT de DERECHA a IZQUIERDA multiplicando cada uno
    por los pesos oficiales, se suman los productos y se toma el módulo 11: el DV
    es el residuo cuando es 0 o 1, o 11 - residuo en caso contrario.

    Args:
        nit: NIT como texto o entero (se ignoran guiones, puntos y espacios).

    Returns:
        El dígito (0-9) como string, o '' si el NIT no tiene dígitos.
    """
    digitos = ''.join(c for c in str(nit or '') if c.isdigit())
    if not digitos:
        return ''
    suma = 0
    for posicion, digito in enumerate(reversed(digitos)):
        if posicion >= len(_PESOS_DV):
            break
        suma += int(digito) * _PESOS_DV[posicion]
    residuo = suma % 11
    if residuo in (0, 1):
        return str(residuo)
    return str(11 - residuo)


def validar_nit(nit, dv):
    """True si el DV declarado coincide con el calculado para el NIT."""
    calculado = calcular_digito_verificacion(nit)
    return bool(calculado) and str(dv or '').strip() == calculado


def redondear_pesos(valor):
    """Redondea a peso entero (el peso colombiano no usa centavos).

    Se usa Decimal con ROUND_HALF_UP (desempate al alza) para que el resultado no
    dependa del error de representación binaria de float y cuadre siempre con el
    total que ve el cliente.
    """
    try:
        return float(Decimal(str(valor or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ArithmeticError, ValueError, TypeError):
        return 0.0


def validar_cuadre(subtotal, iva, retenciones, total, total_neto, tolerancia=1.0):
    """Verifica que los totales cuadren dentro de la tolerancia del anexo técnico.

    La DIAN admite una diferencia de +/- 1 peso entre el valor calculado y el
    enviado, así que la comparación se hace contra `tolerancia` (por defecto 1.0).

    Returns:
        (True, '') si cuadra; (False, motivo) si algún total no cuadra.
    """
    esperado_total = redondear_pesos(subtotal) + redondear_pesos(iva)
    if abs(esperado_total - redondear_pesos(total)) > tolerancia:
        return False, f"Total no cuadra: esperado {esperado_total}, recibido {total}"
    esperado_neto = esperado_total - redondear_pesos(retenciones)
    if abs(esperado_neto - redondear_pesos(total_neto)) > tolerancia:
        return False, f"Total neto no cuadra: esperado {esperado_neto}, recibido {total_neto}"
    return True, ''


def es_nit(tipo_documento):
    """True si el tipo de documento exige dígito de verificación (NIT)."""
    return str(tipo_documento or '').strip().upper() == 'NIT'


def descripcion_unidad(codigo):
    """Etiqueta legible de una unidad de medida UN/ECE ('94' -> 'Unidad')."""
    codigo = str(codigo or '').strip().upper()
    for clave, etiqueta in UNIDADES_MEDIDA:
        if clave.upper() == codigo:
            return etiqueta
    return codigo or 'Unidad'