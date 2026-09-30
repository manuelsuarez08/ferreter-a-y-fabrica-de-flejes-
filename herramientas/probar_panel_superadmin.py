"""Prueba de humo del panel: crear una ferretería y usar sus endpoints.

Corre contra una base temporal y un directorio temporal, y al final verifica
que la instancia creada sirve para lo único que importa: que el dueño pueda
iniciar sesión en SU propia base con SU usuario.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from werkzeug.security import check_password_hash  # noqa: E402


def main():
    from ferreteria import db as db_mod
    from ferreteria.app_factory import create_app
    from ferreteria.blueprints import superadmin as panel
    from ferreteria.services import padron_ferreterias as padron

    temporal = tempfile.mkdtemp(prefix='panel_humo_')
    ruta_padron = os.path.join(temporal, 'desarrollador.db')

    conn = sqlite3.connect(ruta_padron)
    cursor = conn.cursor()
    db_mod._crear_tablas_base(cursor)
    db_mod._crear_tablas_operacion(cursor)
    db_mod._crear_tablas_alquiler(cursor)
    db_mod._crear_tablas_pedidos(cursor)
    db_mod._crear_tablas_cotizaciones(cursor)
    db_mod._crear_tablas_dian(cursor)
    db_mod._aplicar_migraciones(cursor)
    conn.commit()
    conn.close()

    os.environ['FERRETERIA_DB'] = ruta_padron
    import importlib
    import ferreteria.config as config
    importlib.reload(config)
    importlib.reload(db_mod)
    importlib.reload(panel)

    app = create_app(inicializar_db=False, iniciar_hilo_dian=False)

    fallos = []

    def comprobar(descripcion, condicion):
        print(('  OK   ' if condicion else '  FALLA') + '  ' + descripcion)
        if not condicion:
            fallos.append(descripcion)

    with app.test_client() as cliente:
        print('\n1. Un admin de ferreteria NO puede entrar al panel')
        with cliente.session_transaction() as sesion:
            sesion['usuario'] = 'admin'
            sesion['rol'] = 'admin'
        r = cliente.get('/superadmin')
        comprobar('La pantalla /superadmin responde 403', r.status_code == 403)

        r = cliente.get('/api/superadmin/ferreterias')
        comprobar('La API responde 403', r.status_code == 403)

        print('\n2. Sin sesion tampoco')
        cliente2 = app.test_client()
        r = cliente2.get('/api/superadmin/ferreterias')
        comprobar('Sin sesion responde 401', r.status_code == 401)

        print('\n3. El desarrollador si entra')
        with cliente.session_transaction() as sesion:
            sesion['usuario'] = 'programador'
            sesion['rol'] = 'superadmin'
        r = cliente.get('/superadmin')
        comprobar('La pantalla /superadmin responde 200', r.status_code == 200)
        comprobar('Se renderiza la plantilla', b'Panel del Desarrollador' in r.data)

        print('\n4. Crea una ferreteria por la API')
        r = cliente.post('/api/superadmin/ferreterias', json={
            'nombre': 'Ferretería La Prueba',
            'nit': '900555444',
            'digito_verificacion': '1',
            'usuario_dueno': 'dueno_la_prueba',
            'clave_dueno': 'ClaveDePrueba123',
        })
        comprobar('La API responde 201', r.status_code == 201)
        datos = r.get_json()
        comprobar('Devuelve el archivo creado',
                   datos['ferreteria']['archivo'] == 'ferreteria_la_prueba.db')
        ruta_instancia = os.path.join(temporal, datos['ferreteria']['archivo'])
        comprobar('El archivo existe en disco', os.path.exists(ruta_instancia))

        print('\n5. La instancia quedo bien sembrada')
        base = sqlite3.connect(ruta_instancia)
        fila = base.execute(
            'SELECT nombre, nit, software_proveedor_nit, certificado_ruta, '
            'clave_tecnica, dian_ambiente FROM configuracion WHERE id = 1'
        ).fetchone()
        comprobar('Nombre del negocio', fila[0] == 'Ferretería La Prueba')
        comprobar('NIT del emisor', fila[1] == '900555444')
        comprobar('NIT del proveedor (1054552590)', fila[2] == '1054552590')
        comprobar('Certificado vacio', fila[3] == '')
        comprobar('Clave tecnica vacia', fila[4] == '')
        comprobar('Ambiente habilitacion', fila[5] == '2')

        usuarios = dict(base.execute('SELECT usuario, clave FROM usuarios').fetchall())
        comprobar('No quedo el admin de la semilla', 'admin' not in usuarios)
        comprobar('Esta el dueno', 'dueno_la_prueba' in usuarios)
        comprobar('La clave esta cifrada',
                   'ClaveDePrueba123' not in usuarios['dueno_la_prueba'])
        comprobar('La clave verifica',
                   check_password_hash(usuarios['dueno_la_prueba'], 'ClaveDePrueba123'))
        base.close()

        print('\n6. El listado y el detalle')
        r = cliente.get('/api/superadmin/ferreterias')
        comprobar('Lista 1 cliente', len(r.get_json()['ferreterias']) == 1)
        comprobar('Marca que puede operar',
                   r.get_json()['ferreterias'][0]['puede_operar'] is True)

        id_ferreteria = datos['ferreteria']['id']
        r = cliente.get(f'/api/superadmin/ferreterias/{id_ferreteria}/verificar')
        pendientes = r.get_json()['pendiente_dian']
        comprobar('Verificacion: integridad correcta', r.get_json()['integridad'] == 'ok')
        comprobar('Verificacion: 4 pendientes de DIAN', len(pendientes) == 4)

        r = cliente.get(f'/api/superadmin/ferreterias/{id_ferreteria}/historial')
        comprobar('El historial registra la creacion',
                   r.get_json()['historial'][0]['accion'] == 'creada')

        print('\n7. Suspender y reactivar')
        r = cliente.put(f'/api/superadmin/ferreterias/{id_ferreteria}',
                        json={'estado': 'suspendido'})
        comprobar('Cambia a suspendido',
                   r.get_json()['ferreteria']['estado'] == 'suspendido')
        comprobar('Ya no puede operar',
                   r.get_json()['ferreteria']['puede_operar'] is False)
        comprobar('El archivo sigue en disco', os.path.exists(ruta_instancia))

        r = cliente.put(f'/api/superadmin/ferreterias/{id_ferreteria}',
                        json={'estado': 'activo'})
        comprobar('Vuelve a activo',
                   r.get_json()['ferreteria']['estado'] == 'activo')

        print('\n8. Un estado inventado se rechaza')
        r = cliente.put(f'/api/superadmin/ferreterias/{id_ferreteria}',
                        json={'estado': 'inventado'})
        comprobar('Responde 400', r.status_code == 400)

        print('\n9. Datos incompletos se rechazan')
        r = cliente.post('/api/superadmin/ferreterias',
                         json={'nombre': 'Sin usuario'})
        comprobar('Responde 400 y explica', r.status_code == 400)
        comprobar('Dice que falta el usuario', 'usuario_dueno' in r.get_json()['error'])

        print('\n10. Eliminar del padron conserva el archivo')
        r = cliente.delete(f'/api/superadmin/ferreterias/{id_ferreteria}')
        comprobar('Responde 200', r.status_code == 200)
        comprobar('El archivo NO se borro', os.path.exists(ruta_instancia))

    print()
    if fallos:
        print(f'FALLARON {len(fallos)} comprobaciones:')
        for f in fallos:
            print('  -', f)
    else:
        print('Todas las comprobaciones pasaron.')
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())