# Actualizar los datos en Render

Guía para subir a Render los cambios de la base de datos (por ejemplo, la carga
del Excel) **sin perder datos** y **sin acceso al Shell ni a Git**.

---

## Por qué NO basta con subir el código

Si tu servicio usa un **disco persistente** (Render lo monta en `/var/data`),
la base de datos vive en ese disco, **no** en el repositorio. El código de la app
lo dice (`ferreteria/db.py`):

```python
if os.path.exists(DB_NAME):
    return False   # si el disco YA tiene base, no la sobreescribe
```

Es decir: **si subes solo el código, la base vieja sigue ahí.** Los datos nuevos
no llegan. Por eso existe la restauración por web.

> ¿Tiene disco persistente? En [dashboard.render.com](https://dashboard.render.com)
> → tu servicio → menú **"Disks"**. Si ves `/var/data`, sí lo tiene.

---

## Método recomendado: restaurar desde el navegador

La app incluye un botón **«Restaurar respaldo»** en la pestaña de Administración.

### Pasos

1. **Actualiza el código en Render** (para que aparezca el botón nuevo):
   - Si tienes auto-deploy desde GitHub: haz `git push` y espera el despliegue.
   - Si es manual: botón **"Manual Deploy"** en Render.
   - Al terminar, abre la app y verifica que veas el botón «Restaurar respaldo».

2. **Entra a la app** como administrador y abre la pestaña **Administración**.

3. Pulsa **«Restaurar respaldo»** y elige el archivo `.db` con los datos nuevos
   (`ferreteria.db`).

4. Confirma el aviso. La app:
   - Valida que el archivo sea una base SQLite real y tenga las tablas mínimas.
   - **Guarda una copia de la base anterior** (`ferreteria-respaldo-antes-restaurar-FECHA.db`).
   - Reemplaza la base y recarga la página.

5. Verás un mensaje con el conteo: ventas, clientes y productos.

### Si algo sale mal

En el mismo directorio del disco queda la copia previa. Puedes volver a subirla
con el mismo botón, o si tienes Shell:
```bash
cp /var/data/ferreteria-respaldo-antes-restaurar-FECHA.db /var/data/ferreteria.db
```

---

## Método alternativo: subir el código con la semilla nueva

Solo funciona si **NO hay disco persistente** (la base se recrea en cada deploy):

```powershell
git add ferreteria.db
git commit -m "Datos actualizados"
git push
```

---

## Método manual: Shell de Render

Si prefieres hacerlo por consola:

1. Render → tu servicio → pestaña **Shell**.
2. Sube el archivo (o usa `wget`/`curl` si lo tienes en una URL).
3. Cópialo sobre la base e **elimina los WAL/SHM** viejos:
   ```bash
   cp ferreteria-nueva.db /var/data/ferreteria.db
   rm -f /var/data/ferreteria.db-wal /var/data/ferreteria.db-shm
   ```
4. Reinicia el servicio (**Manual Deploy → Restart**), porque SQLite queda
   abierto por el proceso.

> Importante: el paso 3 (`-wal`/`-shm`) evita que SQLite restaure datos viejos
> desde el journal.

---

## Qué NO hacer

- **No** subas la base mientras la app está escribiendo (podrías leer un estado
  intermedio). El botón «Restaurar» ya maneja esto internamente.
- **No** borres la base del disco "para que se recree": el `asegurar_base_de_datos`
  solo siembra desde el repositorio si **no existe**; si la borras con la app
  corriendo, puede quedar inconsistente.
- **No** confundas el respaldo de la base (`ferreteria-respaldo-*.db`) con el
  código. Son cosas distintas.
