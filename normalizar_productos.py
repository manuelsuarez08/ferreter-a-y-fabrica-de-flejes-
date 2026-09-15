"""Corrección y normalización de la tabla de productos (punto 2).

Aplica correcciones CONSERVADORAS y reversibles:
  - Errores ortográficos frecuentes de ferretería (cierra->sierra, maguera->manguera...).
  - Categorías: agrupa las variantes de mayúsculas/tildes en un nombre canónico.
  - Dimensiones: normaliza unidades ('mts'/'metro' -> 'm', 'pda'/'pulg' -> '"', 'kilo' -> 'kg').
  - Capitaliza la primera letra de nombres y categorías para lectura limpia.

NUNCA borra ni fusiona productos. Solo reescribe textos. Hace respaldo y tiene
--dry-run para revisar antes de aplicar.

Uso:
    python normalizar_productos.py --dry-run
    python normalizar_productos.py
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DEFECTO = os.path.join(BASE_DIR, 'ferreteria.db')

# Errores ortográficos frecuentes -> corrección (se aplican como palabra completa).
CORRECCIONES = {
    'cierra': 'sierra',
    'maguera': 'manguera',
    'counduit': 'conduit',
    'condut': 'conduit',
    'flexometro': 'flexómetro',
    'plastico': 'plástico',
    'electrico': 'eléctrico',
    'electricos': 'eléctricos',
    'electronico': 'electrónico',
    'electronicos': 'electrónicos',
    'valvula': 'válvula',
    'valvulas': 'válvulas',
    'tuberia': 'tubería',
    'soldadura': 'soldadura',
    'presion': 'presión',
    'galv': 'galvanizado',
    'met': 'metálico',
}

# Normalización de unidades en el campo dimensiones.
UNIDADES = [
    (r'\bmts\b', 'm'),
    (r'\bmetro(s)?\b', 'm'),
    (r'\bpulgadas?\b', 'in'),
    (r'\bpulg\b', 'in'),
    (r'\bpda\b', 'in'),
    (r'\bkilos?\b', 'kg'),
    (r'\blibras?\b', 'lb'),
    (r'\blitros?\b', 'l'),
]

# Categorías canónicas: variantes -> nombre definitivo.
CATEGORIAS_CANONICAS = {
    'electricos e iluminacion': 'Eléctricos e Iluminación',
    'electrico e iluminacion': 'Eléctricos e Iluminación',
    'electronicos e iluminacion': 'Eléctricos e Iluminación',
    'electronico e iluminacion': 'Eléctricos e Iluminación',
    'electronica e iluminacion': 'Eléctricos e Iluminación',
    'fijacion, anclajes y soporteria': 'Fijación, Anclajes y Soportaría',
    'griferia y sanitarios': 'Grifería y Sanitarios',
    'conectores de riego y jardin': 'Conectores de Riego y Jardín',
    'herramientas manuales': 'Herramientas Manuales',
    'sin categoria': 'Sin Categoría',
    'construccion': 'Construcción',
    'mamposteria': 'Mampostería',
}

# Agrupación genérica por palabras clave (se aplica cuando no hay coincidencia
# exacta). Reduce las decenas de variantes a un set manejable para el POS.
# El orden importa: la primera coincidencia gana.
GRUPOS_POR_CLAVE = (
    (('electr', 'iluminaci', 'bombillo', 'lampara', 'tablero electrico', 'cable'), 'Eléctricos e Iluminación'),
    (('discos', 'abrasiv', 'lija', 'esmeril', 'sierra circular', 'corte'), 'Herramientas y Abrasivos'),
    (('cerradura', 'candado', 'cerrajeri', 'llave', 'pomo'), 'Cerrajería y Seguridad'),
    (('plomeri', 'griferi', 'sanitari', 'grifo', 'sifon', 'desague', 'wc', 'ducha'), 'Plomería y Grifería'),
    (('tuberi', 'pvc', 'conexion', 'hidraulic', 'acople'), 'Tubería y Conexiones'),
    (('quimic', 'lubricant', 'adhesiv', 'sellador', 'silicona', 'pegante', 'boquilla'), 'Químicos, Adhesivos y Selladores'),
    (('pintur', 'acabado', 'esmalte', 'impermeabiliz', 'masilla', 'construccion en seco', 'drywall'), 'Pinturas y Acabados'),
    (('tornill', 'fijaci', 'anclaje', 'soport', 'tornilleri', 'herraje', 'sujeci'), 'Fijación, Tornillería y Herrajes'),
    (('agricol', 'jardiner', 'riego', 'pecuario', 'campo'), 'Agrícola y Jardinería'),
    (('proteccion', 'epp', 'senaliza', 'seguridad industrial', 'guante'), 'Protección Personal y Seguridad'),
    (('herramient', 'medici', 'trazado', 'marcaci', 'encha', 'albañileri', 'neumatic',
      'agarre', 'uso general', 'manejo general'), 'Herramientas Manuales'),
    (('mamposteri', 'estructura', 'cemento', 'ladrillo', 'varilla', 'construccion'), 'Construcción y Mampostería'),
    (('hogar', 'decoraci'), 'Hogar y Decoración'),
    (('soldadura', 'electrod', 'estaño'), 'Soldadura y Electrodo'),
    (('cuerda', 'cadena', 'amarra'), 'Cuerdas, Cadenas y Amarras'),
    (('epp', 'seguridad', 'electic', 'proteccion'), 'Protección Personal y Seguridad'),
    (('riego', 'jardin'), 'Agrícola y Jardinería'),
    (('griferi', 'sanitari'), 'Plomería y Grifería'),
)

# Categorías ya canónicas que se unifican a un único nombre final.
UNIFICAR_CATEGORIA = {
    'Herramientas Manuales y Accesorios': 'Herramientas Manuales',
    'Conectores de Riego y Jardín': 'Agrícola y Jardinería',
    'Grifería y Sanitarios': 'Plomería y Grifería',
    'Construcción': 'Construcción y Mampostería',
    'Mampostería': 'Construcción y Mampostería',
}


def _sin_acentos(texto):
    reemplazos = str.maketrans('áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunAEIOUUN')
    return texto.translate(reemplazos)


def corregir_nombre(nombre):
    """Aplica correcciones de palabras y capitaliza la primera letra."""
    original = nombre
    for mal, bien in CORRECCIONES.items():
        nombre = re.sub(rf'\b{mal}\b', bien, nombre, flags=re.IGNORECASE)
    nombre = re.sub(r'\s+', ' ', nombre).strip()
    if nombre and nombre[0].islower():
        nombre = nombre[0].upper() + nombre[1:]
    return nombre, (nombre != original)


def normalizar_dimensiones(dim):
    """Estandariza unidades del campo dimensiones."""
    if not dim:
        return dim, False
    original = dim
    dim = str(dim).strip()
    for patron, bien in UNIDADES:
        dim = re.sub(patron, bien, dim, flags=re.IGNORECASE)
    dim = re.sub(r'\s+', ' ', dim).strip()
    return dim, (dim != original)


def normalizar_categoria(cat):
    """Agrupa las variantes de categoría en un nombre canónico."""
    if not cat:
        return cat, False
    clave = _sin_acentos(str(cat).strip().lower())
    if cat in UNIFICAR_CATEGORIA:
        nueva = UNIFICAR_CATEGORIA[cat]
        return nueva, (nueva != cat)
    if clave in CATEGORIAS_CANONICAS:
        nueva = CATEGORIAS_CANONICAS[clave]
        return nueva, (nueva != cat)

    # Agrupación por palabras clave (electr, plomeri, pintur, ...).
    for palabras, nombre_grupo in GRUPOS_POR_CLAVE:
        if any(p in clave for p in palabras):
            return nombre_grupo, (nombre_grupo != cat)

    # Categorías basura (solo dígitos, vacías o demasiado largas) -> Sin Categoría.
    if not clave or clave.isdigit() or len(clave) > 60:
        return 'Sin Categoría', (cat != 'Sin Categoría')
    return cat, False

def analizar(db):
    """Devuelve la lista de cambios propuestos (sin escribir)."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    filas = cursor.execute("SELECT id, nombre, categoria, dimensiones FROM productos").fetchall()
    conn.close()

    cambios = []
    for id_p, nombre, categoria, dimensiones in filas:
        nuevo_nombre, c1 = corregir_nombre(nombre or '')
        nueva_cat, c2 = normalizar_categoria(categoria)
        nueva_dim, c3 = normalizar_dimensiones(dimensiones)
        if c1 or c2 or c3:
            cambios.append({
                'id': id_p,
                'nombre': (nombre, nuevo_nombre) if c1 else None,
                'categoria': (categoria, nueva_cat) if c2 else None,
                'dimensiones': (dimensiones, nueva_dim) if c3 else None,
            })
    return cambios


def aplicar(db, cambios):
    """Aplica los cambios propuestos."""
    conn = sqlite3.connect(db, timeout=15)
    cursor = conn.cursor()
    n = 0
    for c in cambios:
        sets, valores = [], []
        if c['nombre']:
            sets.append('nombre = ?'); valores.append(c['nombre'][1])
        if c['categoria']:
            sets.append('categoria = ?'); valores.append(c['categoria'][1])
        if c['dimensiones']:
            sets.append('dimensiones = ?'); valores.append(c['dimensiones'][1])
        valores.append(c['id'])
        cursor.execute(f"UPDATE productos SET {', '.join(sets)} WHERE id = ?", valores)
        n += 1
    conn.commit()
    conn.close()
    return n


def respaldar(db):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destino = os.path.join(BASE_DIR, f'ferreteria-respaldo-normalizacion-{stamp}.db')
    shutil.copy2(db, destino)
    return destino


def main():
    parser = argparse.ArgumentParser(description='Normaliza productos (ortografía, categorías y unidades).')
    parser.add_argument('--db', default=DB_DEFECTO)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limite', type=int, default=30, help='Cuántos cambios mostrar en pantalla.')
    args = parser.parse_args()

    print('Base de datos:', args.db)
    cambios = analizar(args.db)
    print(f'Cambios propuestos: {len(cambios)}')

    print(f'\n--- Muestra (primeros {args.limite}) ---')
    for c in cambios[:args.limite]:
        print(f"  ID {c['id']}:")
        for campo in ('nombre', 'categoria', 'dimensiones'):
            if c[campo]:
                print(f"    {campo}: '{c[campo][0]}'  ->  '{c[campo][1]}'")

    if args.dry_run:
        print('\n(Modo simulacion: no se escribio nada.)')
        return

    ruta = respaldar(args.db)
    print('\n[OK] Respaldo creado:', ruta)
    n = aplicar(args.db, cambios)
    print(f'[OK] Productos actualizados: {n}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ERROR:', e)
        sys.exit(1)
