# Configurar la Facturación Electrónica (Siigo / DIAN)

Guía paso a paso para activar la facturación electrónica **opcional** de este POS.
La integración ya está implementada; lo único que falta es llenar el archivo
`.env` con tus credenciales y los códigos tributarios de tu cuenta Siigo.

> La facturación electrónica **no** se emite siempre: solo cuando el cajero marca
> la casilla «🧾 Generar Factura Electrónica» en el POS. Si la casilla va sin
> marcar, la venta es normal y no se contacta a Siigo.

---

## 1. Requisitos previos

- Una cuenta activa en **Siigo Nube** con facturación electrónica habilitada.
- Una **IP fija autorizada** en Siigo. Como el POS puede estar en un hosting
  dinámico (Render, etc.), el tráfico debe salir por un **proxy estático**
  (Fixie o QuotaGuard). Siigo solo acepta peticiones desde la IP que registraste.
- Credenciales de la API:
  - `SIIGO_USERNAME`: el usuario de la API.
  - `SIIGO_ACCESS_KEY`: la llave de acceso (se genera en Siigo).

---

## 2. Crear el archivo `.env`

En la **raíz del proyecto** (donde está `app.py`), copia la plantilla:

```powershell
Copy-Item .env.example .env
```

Luego abre `.env` y reemplaza los valores de ejemplo. El archivo `.env` está en
`.gitignore`, así que **no se sube al repositorio** (contiene credenciales).

---

## 3. Obtener los códigos reales desde Siigo

Los valores por defecto (`1`, `0`) son marcadores. **Deben existir en tu cuenta
Siigo** y coincidir con tu resolución de facturación. Para consultarlos, usa la
API autenticada (mismo `Bearer` token del endpoint `/auth`):

| Variable en `.env` | Endpoint de Siigo para consultarlo |
|---|---|
| `SIIGO_DOCUMENT_ID` | `GET /document-types` |
| `SIIGO_PAYMENT_*` | `GET /payment-types` |
| `SIIGO_TAX_ID` | `GET /taxes` |
| `SIIGO_SELLER_ID` | `GET /users` |

> Toda petición a la API de Siigo debe llevar el encabezado **`Partner-Id`**
> (lo agrega automáticamente el código; se controla con `SIIGO_PARTNER_ID`).

Referencia oficial: [Siigo API](https://siigoapi.docs.apiary.io/).

### Cómo probar manualmente un endpoint

```powershell
# 1) Autenticarse (devuelve access_token)
$body = '{"username":"TU_USUARIO","access_key":"TU_ACCESS_KEY"}'
$auth = Invoke-RestMethod -Method Post -Uri "https://api.siigo.com/auth" `
        -ContentType "application/json" -Headers @{ "Partner-Id" = "FerreteriaPOS" } `
        -Body $body

# 2) Consultar los tipos de documento (usa la IP fija / proxy)
Invoke-RestMethod -Method Get -Uri "https://api.siigo.com/v1/document-types" `
  -Headers @{ Authorization = "Bearer $($auth.access_token)"; "Partner-Id" = "FerreteriaPOS" }
```

Repite cambiando la ruta por `/v1/payment-types`, `/v1/taxes` o `/v1/users`, y
copia los `id` que correspondan a tu operación.

---

## 4. Referencia de cada variable

```dotenv
# ── Credenciales ────────────────────────────────
SIIGO_USERNAME=tu_usuario_siigo        # usuario de la API
SIIGO_ACCESS_KEY=tu_access_key_siigo   # llave de acceso

# ── Proxy de IP estática (configura UNA) ────────────────────────
FIXIE_URL=http://usuario:password@proxy.fixie.io:8080
# QUOTAGUARDSTATIC_URL=http://usuario:password@proxy.quotaguardstatic.com:9293

# ── Host de la API (no cambiar salvo pruebas) ───────────────────
SIIGO_API_BASE=https://api.siigo.com
SIIGO_PARTNER_ID=FerreteriaPOS         # 3-100 caracteres, sin espacios ni símbolos

# ── Documento y vendedor ────────────────────────
SIIGO_DOCUMENT_ID=1                    # GET /document-types
SIIGO_DOCUMENT_TYPE=Invoice            # Invoice = factura de venta electrónica
SIIGO_SELLER_ID=0                      # 0 = usa el vendedor del token; o GET /users

# ── Impuesto por defecto de los productos ───────────────────────
SIIGO_TAX_ID=0                         # 0 = sin IVA explícito; o GET /taxes
SIIGO_TAX_PERCENTAGE=0                 # p. ej. 19 para IVA del 19 %

# ── Formas de pago (mapea cada medio del POS al payment.id) ─────
SIIGO_PAYMENT_ID=1                     # valor por defecto (GET /payment-types)
SIIGO_PAYMENT_EFECTIVO=1
SIIGO_PAYMENT_TRANSFERENCIA=1          # Nequi / Daviplata
SIIGO_PAYMENT_TARJETA=1
SIIGO_PAYMENT_CREDITO=1

# ── Tiempos de espera y reintentos ──────────────────────────────
SIIGO_TIMEOUT=30                       # segundos por petición HTTP
SIIGO_MAX_REINTENTOS=2                 # reintentos ante error transitorio (5xx, 429)
```

---

## 5. Datos que exige el cliente

Para poder emitir la factura, el cliente **debe** tener registrados estos campos
(el POS bloquea la emisión y avisa si falta alguno):

- Tipo de Documento (`CC`, `NIT`, `CE`, `PP`, `TI`)
- Cédula / NIT
- Nombre completo
- Correo electrónico
- Teléfono

Se editan en la pestaña **Clientes** (o desde la factura con «✏️ Editar Datos del
Cliente para esta Factura»).

---

## 6. Cómo funciona en el POS

1. El cajero marca la casilla **🧾 Generar Factura Electrónica (Siigo / DIAN)**.
2. El sistema avisa si el cliente tiene los datos completos y si Siigo está
   configurado en el servidor.
3. Al registrar la venta, la factura se transmite a Siigo.
4. **Si Siigo falla** (sin conexión, datos rechazados, credenciales), **la venta
   queda guardada localmente** con estado `error`. No se pierde nada.
5. El **Historial de Facturas** muestra un badge del estado
   (`⏳ FE pendiente`, `✅ aprobada`, `⚠️ FE con error`) y un botón
   **«🧾 Transmitir / Reintentar Siigo»** para volver a enviarla cuando quieras
   (por ejemplo, si el cliente pide la factura minutos después de comprar).
6. Cuando la factura queda **aprobada**, desde el detalle de la factura puedes
   ver/descargar el **PDF** y el **XML** oficiales, ver el **CUFE**, y
   **reenviarla por correo** al cliente (usa el correo registrado del cliente).

Estados posibles en la columna `siigo_estado` de la tabla `ventas`:

| Estado | Significado |
|---|---|
| `no_solicitada` | Venta normal, sin factura electrónica |
| `pendiente` | Se solicitó FE; aún sin transmitir |
| `aprobada` | Siigo emitió el comprobante (guarda número, CUFE y URL del PDF) |
| `error` | Rechazada o sin conexión; reintentable desde el historial |

---

## 7. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| «Siigo no está configurado» | Falta `.env` o está vacío | Crea el `.env` y define `SIIGO_USERNAME` y `SIIGO_ACCESS_KEY` |
| «No hubo conexión con Siigo (revisa la red y el proxy FIXIE_URL)» | Sin proxy estático o URL mal escrita | Configura `FIXIE_URL` o `QUOTAGUARDSTATIC_URL` con las credenciales del proxy |
| «Autenticación rechazada por Siigo» | Usuario/llave incorrectos | Verifica `SIIGO_USERNAME` y `SIIGO_ACCESS_KEY` |
| «Siigo rechazó la factura: …» | Código tributario inexistente o datos inválidos | Revisa `SIIGO_DOCUMENT_ID`, `SIIGO_TAX_ID`, `SIIGO_PAYMENT_*` contra tu cuenta Siigo |
| «El cliente no tiene datos completos» | Faltan campos fiscales | Completa Tipo de Documento, Cédula/NIT, Nombre, Correo y Teléfono |

Tras cambiar el `.env`, **reinicia el servidor** para que tome los nuevos valores.

---

## 8. Prueba automática del flujo
El proyecto incluye una prueba de integración que ejercita todo el flujo de
facturación electrónica **sin necesidad de credenciales reales** (usa una copia
temporal de la base, no toca la real):

```powershell
python tests/test_facturacion_siigo.py
```

Verifica 9 escenarios: login, `/api/siigo/estado`, venta normal, bloqueo cuando
al cliente le faltan datos fiscales, venta con FE que se guarda localmente aunque
Siigo falle, reintento (`/api/ventas/<id>/siigo`), rechazo de ventas anuladas,
venta inexistente (404) y que el historial exponga los campos `siigo_*`. Debe
terminar con `RESULTADO: TODAS LAS PRUEBAS PASARON`.

Si usas un entorno virtual, ejecútala con su intérprete:
`.venv\Scripts\python.exe tests/test_facturacion_siigo.py`.

---

## 9. Notas de seguridad

- El `.env` **nunca** se sube al repositorio (ya está en `.gitignore`).
- Las credenciales se leen del entorno en tiempo de arranque
  (`ferreteria/config.py`), no están escritas en el código.
- En el historial de la venta se guarda la respuesta de Siigo como auditoría.
