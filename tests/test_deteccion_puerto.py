"""Prueba de la deteccion "el puerto 5000 es la ferreteria o es otra app?".

`run-tests.mjs` no puede asumir que lo que responde en el puerto es nuestra
app: si hay otra cosa escuchando, la prueba correria contra ella y daria un
verde mentiroso. Para eso existe `esNuestraApp()`, que comprueba el titulo de
la pagina de login. Esta prueba fija ese contrato.

Casos cubiertos:
  1. Nadie escucha                 -> NO es nuestra app.
  2. Impostor que responde 200     -> NO es nuestra app (aunque el status sea ok).
  3. Una pagina con nuestro titulo -> SI es nuestra app.

Importante: el titulo esperado NO se copia a mano aqui. Se lee de la plantilla
real (templates/login.html), de modo que si alguien la cambia, esta prueba
avisa en vez de fallar en silencio dentro de run-tests.mjs.

Uso:
    python tests/test_deteccion_puerto.py
"""
import http.server
import os
import re
import socket
import sys
import threading

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- El mismo patron que usa run-tests.mjs ---------------------------------
PATRON_TITULO = re.compile(r"Sistema Ferreter", re.IGNORECASE)


def es_nuestra_app(url):
    """Replica la logica de run-tests.mjs: lee la pagina y busca el titulo."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=3) as res:
            html = res.read().decode("utf-8", "replace")
        return bool(PATRON_TITULO.search(html))
    except (urllib.error.URLError, OSError):
        return False


# --- Infraestructura minima: servidores de prueba --------------------------
def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Handler(http.server.BaseHTTPRequestHandler):
    """Sirve el HTML que le diga `respuesta_actual` (mutable por el test)."""

    def do_GET(self):  # noqa: N802 (nombre impuesto por BaseHTTPRequestHandler)
        cuerpo = self.server.respuesta_actual.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args):
        pass  # silencio: el test imprime lo suyo


def levantar_servidor(respuesta_inicial):
    puerto = puerto_libre()
    servidor = http.server.HTTPServer(("127.0.0.1", puerto), Handler)
    servidor.respuesta_actual = respuesta_inicial
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor, f"http://127.0.0.1:{puerto}/login"


# --- Runner ---------------------------------------------------------------
pasos = 0
fallos = 0


def comprobar(nombre, obtenido, esperado):
    global pasos, fallos
    pasos += 1
    ok = obtenido == esperado
    if not ok:
        fallos += 1
    print(f"  {'OK   ' if ok else 'FALLA'} {nombre}: {obtenido} (esperado {esperado})")


def main():
    # El titulo esperado sale de la plantilla real, no de una copia a mano.
    ruta_login = os.path.join(RAIZ, "templates", "login.html")
    with open(ruta_login, encoding="utf-8") as f:
        plantilla = f.read()
    titulo_real = PATRON_TITULO.search(plantilla)
    print(f"== Titulo hallado en templates/login.html: {'SI' if titulo_real else 'NO'} ==")
    if not titulo_real:
        print("ERROR: templates/login.html ya no contiene el titulo que busca run-tests.mjs.")
        print("       Actualiza PATRON_TITULO en _qa/run-tests.mjs y en esta prueba.")
        return 1

    # Caso 1: nadie escucha.
    puerto_vacio = puerto_libre()
    comprobar(
        "sin servidor -> es nuestra app?",
        es_nuestra_app(f"http://127.0.0.1:{puerto_vacio}/login"),
        False,
    )

    # Caso 2 y 3: impostor primero, luego la pagina "nuestra".
    servidor, url = levantar_servidor(
        "<html><head><title>Otra App</title></head><body>hola</body></html>"
    )
    try:
        comprobar("impostor ajeno (200) -> es nuestra app?", es_nuestra_app(url), False)

        servidor.respuesta_actual = (
            "<html><head><title>Iniciar Sesión - Sistema Ferretería</title></head></html>"
        )
        comprobar("login real -> es nuestra app?", es_nuestra_app(url), True)
    finally:
        servidor.shutdown()
        servidor.server_close()

    print(f"\n{'FALLARON ' + str(fallos) + '/' + str(pasos) if fallos else str(pasos) + '/' + str(pasos) + ' correctos'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
