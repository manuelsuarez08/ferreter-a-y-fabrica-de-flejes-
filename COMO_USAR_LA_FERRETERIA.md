# 🏪 Cómo usar tu Ferretería (guía rápida)

## ✅ Para empezar a trabajar (en tu computadora)

1. Entra a la carpeta del proyecto.
2. Haz **doble clic** en: **`ABRIR_FERRETERIA.cmd`**
3. Se abre una ventana negra que dice "Iniciando la aplicación...". **NO la cierres** mientras trabajas.
4. Abre tu navegador (Chrome, Edge) y entra a:

   **http://127.0.0.1:5000**

5. Entra con: **admin** / **admin123**

> ⚠️ **Importante:** si cierras la ventana negra, la aplicación se apaga.
> Déjala abierta mientras vendes. Para cerrarla, simplemente cierra la ventana.

---

## 📱 Ver la aplicación desde el celular (misma Wi-Fi)

1. Deja la ventana de `ABRIR_FERRETERIA.cmd` abierta en la PC.
2. Conecta el celular al **mismo Wi-Fi** que la computadora.
3. En el navegador del celular escribe:

   **http://192.168.1.64:5000**

   (La ventana negra también te muestra esta dirección al arrancar.)

4. Entra con: **admin** / **admin123**

> ⚠️ Esto solo funciona **dentro del mismo Wi-Fi** (el local o la casa).
> Si sales a la calle con datos móviles, no funcionará (para eso hace falta el paso de abajo).

---

## 🌍 Ver desde CUALQUIER lugar (celular con datos móviles)

Esto YA esta configurado. En vez de `ABRIR_FERRETERIA.cmd`, usa:

**`ABRIR_CON_INTERNET.cmd`** (doble clic)

Ese archivo levanta la aplicacion Y abre un "tunel" para entrar desde afuera.

### Pasos
1. Doble clic en **`ABRIR_CON_INTERNET.cmd`**
2. Espera unos segundos. En la ventana aparecera un enlace **verde** asi:
   `https://algo-random.trycloudflare.com`
3. **Copia ese enlace** y abrelo en el celular (o mandatelo por WhatsApp).
4. Entra con **admin** / **admin123**.

### Puntos importantes
- El enlace **CAMBIA cada vez** que arrancas. Copia el nuevo cada vez.
- La ventana debe quedar **ABIERTA**. Si la cierras, se corta el acceso.
- Tu computadora debe estar **encendida**.
- Sirve desde datos moviles, otra sede, cualquier lugar.

> Cualquier persona con el enlace puede ver la pantalla de login, pero necesita
> usuario y clave para entrar. Para mas seguridad, cambia la clave de admin.

---

## 💾 Respaldos de tus datos

Tus datos viven en el archivo **`ferreteria.db`** (en esta misma carpeta).

**Para hacer un respaldo manual:**
1. Entra a la aplicación como admin.
2. Pestaña **Administración** → botón **«Descargar respaldo»**.
3. Se descarga un archivo `.db` con fecha. Guárdalo en un lugar seguro.

**Recomendación:** haz un respaldo **al final de cada día de trabajo**.

---

## 🔧 Preguntas frecuentes

**¿Se pierden mis datos si cierro la ventana?**
No. Los datos quedan guardados en `ferreteria.db`. Al cerrar, solo se apaga la aplicación.

**¿Cuánto cuesta esto?**
Nada. La aplicación corre en tu propia computadora.

**¿Necesito internet para usarla en la PC?**
No. Funciona sin internet (es local).

**¿Dónde están mis productos y clientes?**
En el archivo `ferreteria.db`. Todo lo que hagas se guarda ahí automáticamente.

**¿Qué pasa con Render (la página de internet que tenía)?**
Tenía el problema de que en su plan gratis los datos se borran. Por eso pasamos a usar tu computadora, que es estable y gratis. Puedes eliminar ese servicio si quieres.
