from app import init_db


def crear_base_de_datos():
    init_db()
    print('Base de datos verificada correctamente.')


if __name__ == '__main__':
    crear_base_de_datos()
