"""Provisiona una ferretería de prueba COMPLETA, con logo y datos reales.

Recorre el mismo camino que el panel (servicio `padron_ferreterias`), pero
además sube un logo y rellena los datos comerciales, para poder ver las
tarjetas del panel con contenido de verdad y no con la marca de la plataforma.

    python herramientas/provisionar_ferreteria_prueba.py

Deja la instancia en el directorio de instancias del desarrollador y la
registra en su base. Es reversible: al final imprime cómo deshacerlo.
"""
from __future__ import annotations

import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass

from ferreteria.services import padron_ferreterias as padron  # noqa: E402
from ferreteria.services import personalizacion as marca  # noqa: E402

# ── Datos de la ferretería de prueba ─────────────────────────────────
NOMBRE = 'Ferretería El Tornillo S.A.S.'
NIT = '901456789'
DV = '3'
DIRECCION = 'CALLE 10 # 5-8, Samaná, Caldas'
TELEFONO = '3105554444'
EMAIL = 'compras@eltornillo.local'
USUARIO_DUENO = 'dueno_el_tornillo'
CLAVE_DUENO = 'Tornillo2026'
MUNICIPIO = '17690'   # Samaná, Caldas
DEPARTAMENTO = '17'

# ── Logo de prueba: un PNG con la inicial, dibujado sin dependencias ──
# Se genera en memoria en vez de llevar un binario en el repo: el archivo va a
# ser de la ferretería, no del software.
def _png_de_prueba(letra='T'):
    """PNG válido (el mismo generador de pruebas) con la firma correcta."""
    sys.path.insert(0, os.path.join(RAIZ, 'tests'))
    from _certificado_prueba import generar_p12  # noqa: F401  (valida el import)

    # Se construye un PNG 1x1 y se nombra como la ferretería. El tamaño es
    # irrelevante: lo que se prueba es el AISLAMIENTO entre instancias, no
    # que la imagen se vea bonita.
    import struct
    import zlib

    ancho = alto = 64
    crudo = b''
    for y in range(alto):
        crudo += b'\x00' + bytes([31, 78, 121] * ancho)  # franja azul FerreControl

    def _fragmento(tipo, datos):
        return (struct.pack('>I', len(datos)) + tipo + datos
                + struct.pack('>I', zlib.crc32(tipo + datos) & 0xFFFFFFFF))

    return (b'\x89PNG\r\n\x1a\n'
            + _fragmento(b'IHDR', struct.pack('>IIBBBBB', ancho, alto, 8, 2, 0, 0, 0))
            + _fragmento(b'IDAT', zlib.compress(crudo))
            + _fragmento(b'IEND', b''))


def main():
    from ferreteria.config import resolver_directorio_instancias

    directorio = resolver_directorio_instancias()
    ruta_padron = os.path.join(RAIZ, 'ferreteria.db')

    print('=' * 72)
    print(' PROVISIONAMIENTO DE FERRETERÍA DE PRUEBA')
    print('=' * 72)
    print(f'Padron   : {ruta_padron}')
    print(f'Directorio de instancias: {directorio}')
    print()

    conn = sqlite3.connect(ruta_padron)
    try:
        # Si ya existe una con ese nombre, se avisa y no se duplica.
        previa = conn.execute(
            'SELECT id FROM ferreterias WHERE nombre = ?', (NOMBRE,)
        ).fetchone()
        if previa:
            print(f'ATENCION: ya existe "{NOMBRE}" (id {previa[0]}).')
            print('           No se vuelve a crear para no duplicar el cliente.')
            conn.close()
            return 1

        registro = padron.crear_ferreteria(
            conn=conn,
            directorio=directorio,
            nombre=NOMBRE,
            usuario_dueno=USUARIO_DUENO,
            clave_dueno=CLAVE_DUENO,
            nit=NIT,
            digito_verificacion=DV,
            direccion=DIRECCION,
            telefono=TELEFONO,
            email=EMAIL,
            notas='Cliente de prueba del ciclo de provisionamiento',
        )
        print(f'1. Instancia creada : {registro["archivo"]}')
        print(f'   Ruta             : {registro["ruta"]}')
        print(f'   Usuario dueño    : {registro["usuario_dueno"]}')
        print(f'   Avisos           : {registro.get("avisos") or "ninguno"}')

        # Datos fiscales completos en SU base.
        inst = sqlite3.connect(registro['ruta'])
        inst.execute(
            'UPDATE configuracion SET codigo_municipio = ?, '
            'codigo_departamento = ?, regimen_fiscal = ?, '
            'responsabilidades = ? WHERE id = 1',
            (MUNICIPIO, DEPARTAMENTO, 'Responsable de IVA', 'O-13'),
        )
        inst.commit()

        # Logo y marca. Se parchea `directorio_logos` para que caiga en la
        # carpeta de ESTA instancia, no en la compartida del desarrollador.
        import ferreteria.config as config_mod
        nombre_db = os.path.splitext(registro['archivo'])[0]
        ruta_logos = os.path.join(directorio, nombre_db, 'static', 'logos')
        os.makedirs(ruta_logos, exist_ok=True)

        original_db = config_mod.DB_NAME
        original_fn = marca.directorio_logos
        config_mod.DB_NAME = registro['ruta']
        marca.directorio_logos = lambda: ruta_logos
        try:
            marca.guardar_logo(inst, _png_de_prueba(), f'{NOMBRE}.png')
            marca.guardar_datos(inst, {
                'negocio_nombre_comercial': 'El Tornillo',
                'negocio_mensaje_pie': (
                    'Garantía de 6 meses en herramientas\n'
                    'No se aceptan devoluciones sin recibo\n'
                    'Gracias por su compra'
                ),
                'negocio_logo_tamano': 110,
                'negocio_logo_mostrar': True,
                'negocio_color_primario': '#8B4513',
            })
            # Plan anual que vence dentro de 10 meses.
            vencimiento = padron.renovacion_sugerida('anual')
            padron.configurar_pago(conn, registro['id'], plan='anual',
                                   fecha_vencimiento=vencimiento)
        finally:
            config_mod.DB_NAME = original_db
            marca.directorio_logos = original_fn

        inst.commit()
        marca_actual = marca.leer(inst)
        inst.close()

        print(f'2. Logo guardado    : {ruta_logos}')
        print(f'   Nombre comercial  : {marca_actual["nombre_comercial"]}')
        print(f'   Logo en disco     : {marca_actual["logo_tiene"]}')
        print(f'   Mensaje al pie   : {len(marca_actual["mensaje_pie"])} caracteres')
        print(f'3. Plan             : anual, vence {vencimiento}')

        pago = padron.obtener_pago(conn, registro['id'])
        print(f'   Dias para vencer : {pago["dias_para_vencer"]}')
        print()

        print('=' * 72)
        print(' ESTADO FINAL')
        print('=' * 72)
        cifras = padron.resumen(conn, directorio)
        for clave in ('total', 'operativos', 'requiere_atencion'):
            print(f'  {clave:22} {cifras[clave]}')
        print()
        print('  Para verla en el panel:  /superadmin')
        print('  Para entrar como dueño:  usuario', USUARIO_DUENO,
              '/ clave', CLAVE_DUENO)
        print()
        print('  DESHACER (borra la instancia y su registro):')
        print(f'    python -c "import sqlite3;'
              f"c=sqlite3.connect(r'{ruta_padron}');"
              f"c.execute('DELETE FROM ferreterias WHERE id = {registro['id']}');"
              f"c.commit()\"")
        print(f'    y borrar {ruta_logos}')
        print()
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
