"""Integración opcional con la API de facturación electrónica de Siigo.

Responsabilidad única: hablar con Siigo. Nada de Flask ni de SQLite aquí salvo
lo mínimo para construir la carga útil (el llamador inyecta los datos ya
leídos de la base de datos).

Flujo:
    1. `autenticar()`        -> POST /auth  (obtiene el Bearer token ~24h).
    2. `construir_factura()` -> traduce una venta local al JSON de Siigo.
    3. `emitir_factura()`    -> POST /v1/invoices (transmisión en segundo plano).

Todas las peticiones salen por el proxy estático (`FIXIE_URL` /
`QUOTAGUARDSTATIC_URL`): Siigo solo autoriza una IP fija, y el proxy garantiza
que la IP de salida sea siempre la misma.

Las credenciales (`SIIGO_USERNAME`, `SIIGO_ACCESS_KEY`) se leen del entorno
(.env), cargado por `ferreteria.config`. Si faltan, la emisión falla con un
error claro y la venta local se conserva para reintentar.
"""
import json
import time
import urllib.error
import urllib.request

from ..config import (
    FIXIE_URL,
    SIIGO_ACCESS_KEY,
    SIIGO_API_BASE,
    SIIGO_API_PARTNER_ID,
    SIIGO_DEFAULT_DOCUMENT_ID,
    SIIGO_DEFAULT_DOCUMENT_TYPE,
    SIIGO_DEFAULT_PAYMENT_ID,
    SIIGO_DEFAULT_SELLER,
    SIIGO_DEFAULT_TAX_ID,
    SIIGO_DEFAULT_TAX_PERCENTAGE,
    SIIGO_MAPA_PAGOS,
    SIIGO_MAX_REINTENTOS,
    SIIGO_TIMEOUT,
    SIIGO_TIPOS_DOCUMENTO,
    SIIGO_USERNAME,
)

# Caché del token en memoria del proceso: Siigo lo entrega con ~24h de vigencia
# y no conviene autenticar en cada venta. Se guarda (token, expira_en_epoch).
_TOKEN_CACHE = {'token': None, 'expira': 0.0}
# Margen de seguridad (s) para renovar antes de que el token caduque.
_TOKEN_MARGEN = 300


class SiigoError(Exception):
    """Error de negocio de Siigo (credenciales, validación, rechazo de datos).

    `motivo` es el texto ya listo para mostrar al cajero.
    """

    def __init__(self, motivo, *, status=None, detalle=None):
        super().__init__(motivo)
        self.motivo = motivo
        self.status = status
        self.detalle = detalle


def configurado():
    """Indica si hay credenciales de Siigo cargadas (para avisar en la UI)."""
    return bool(SIIGO_USERNAME and SIIGO_ACCESS_KEY)


def _url(ruta):
    return f"{SIIGO_API_BASE.rstrip('/')}{ruta}"


def _abrir(req, timeout):
    """Ejecuta la petición HTTP, canalizándola por el proxy estático si existe."""
    if FIXIE_URL:
        # El proxy estático firma la salida; se usa el ProxyHandler estándar de
        # urllib, que entiende la URL completa del proxy (incluye credenciales).
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': FIXIE_URL, 'https': FIXIE_URL})
        )
    else:
        opener = urllib.request.build_opener()
    return opener.open(req, timeout=timeout)


def _peticion_json(metodo, ruta, *, payload=None, token=None, timeout=None):
    """Realiza una petición JSON a Siigo y devuelve (status, cuerpo_dict)."""
    timeout = timeout or SIIGO_TIMEOUT
    cuerpo = None
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Partner-Id': SIIGO_API_PARTNER_ID,
    }
    if payload is not None:
        cuerpo = json.dumps(payload).encode('utf-8')
    if token:
        headers['Authorization'] = f'Bearer {token}'

    req = urllib.request.Request(_url(ruta), data=cuerpo, headers=headers, method=metodo)
    try:
        with _abrir(req, timeout) as resp:
            texto = resp.read().decode('utf-8') or '{}'
            return resp.status, _parsear(texto)
    except urllib.error.HTTPError as e:
        texto = ''
        try:
            texto = e.read().decode('utf-8')
        except Exception:
            pass
        return e.code, _parsear(texto)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Falla de red/proxy: no es un rechazo de Siigo sino que no se pudo salir.
        raise SiigoError(
            f"No hubo conexión con Siigo (revisa la red y el proxy FIXIE_URL): {e}"
        ) from e


def _parsear(texto):
    if not texto:
        return {}
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        return {'raw': texto}


def _extraer_mensaje(cuerpo):
    """Extrae un texto legible del cuerpo de error de Siigo."""
    if not isinstance(cuerpo, dict):
        return str(cuerpo)
    for clave in ('Errors', 'errors', 'Message', 'message', 'error', 'detail'):
        valor = cuerpo.get(clave)
        if not valor:
            continue
        if isinstance(valor, str):
            return valor
        if isinstance(valor, list):
            partes = []
            for item in valor:
                if isinstance(item, dict):
                    partes.append(item.get('Message') or item.get('message') or json.dumps(item, ensure_ascii=False))
                else:
                    partes.append(str(item))
            return ' | '.join(partes)
        if isinstance(valor, dict):
            return valor.get('Message') or valor.get('message') or json.dumps(valor, ensure_ascii=False)
    return json.dumps(cuerpo, ensure_ascii=False)


def autenticar(forzar=False):
    """Devuelve un Bearer token válido, autenticándose si es necesario.

    Raises:
        SiigoError: si faltan credenciales o Siigo rechaza el login.
    """
    if not configurado():
        raise SiigoError(
            "Siigo no está configurado. Define SIIGO_USERNAME y SIIGO_ACCESS_KEY en el archivo .env."
        )

    ahora = time.time()
    if not forzar and _TOKEN_CACHE['token'] and ahora < _TOKEN_CACHE['expira']:
        return _TOKEN_CACHE['token']

    status, cuerpo = _peticion_json(
        'POST', '/auth',
        payload={'username': SIIGO_USERNAME, 'access_key': SIIGO_ACCESS_KEY},
    )
    if status != 200 or not isinstance(cuerpo, dict) or not cuerpo.get('access_token'):
        raise SiigoError(
            f"Autenticación rechazada por Siigo: {_extraer_mensaje(cuerpo)}",
            status=status, detalle=cuerpo,
        )

    token = cuerpo['access_token']
    expira_seg = int(cuerpo.get('expires_in') or 86400)
    _TOKEN_CACHE['token'] = token
    _TOKEN_CACHE['expira'] = ahora + max(expira_seg - _TOKEN_MARGEN, 60)
    return token


# ── Construcción del JSON de factura ────────────────────────

def _tipo_documento(codigo):
    """Traduce el tipo de documento local (CC/NIT/...) al id de Siigo."""
    if not codigo:
        return SIIGO_TIPOS_DOCUMENTO['CC']
    clave = str(codigo).strip().upper()
    return SIIGO_TIPOS_DOCUMENTO.get(clave, clave)


def _forma_pago(tipo_pago):
    """Traduce el medio de pago del POS al payment.id configurado en Siigo."""
    return SIIGO_MAPA_PAGOS.get((tipo_pago or '').lower(), SIIGO_DEFAULT_PAYMENT_ID)


def _item_siigo(detalle, tax_id, tax_pct):
    item = {
        'code': str(detalle.get('codigo') or detalle.get('id_producto') or ''),
        'description': detalle.get('nombre') or 'Producto',
        'quantity': float(detalle.get('cantidad') or 1),
        'price': float(detalle.get('precio_unitario') or detalle.get('precio') or 0),
    }
    # Si hay impuesto configurado (p. ej. IVA 19 %), se declara por ítem.
    if tax_id:
        item['taxes'] = [{
            'id': tax_id,
            'percentage': float(tax_pct or 0),
        }]
    return item


def construir_factura(venta):
    """Traduce una venta local al payload de `POST /v1/invoices`.

    Args:
        venta: dict con las claves:
            id_venta, fecha (YYYY-MM-DD), cliente {nombre, cedula_nit,
            tipo_documento, telefono, direccion, email}, tipo_pago, items[]
            (cada uno con nombre, cantidad, precio_unitario, codigo[, id_producto])
            y `total`.

    Returns:
        dict listo para serializar a JSON.
    """
    cliente = venta.get('cliente') or {}
    documento = {
        'id': SIIGO_DEFAULT_DOCUMENT_ID,
    }
    if SIIGO_DEFAULT_DOCUMENT_TYPE:
        documento['type'] = SIIGO_DEFAULT_DOCUMENT_TYPE

    payload = {
        'document': documento,
        'date': venta.get('fecha'),
        'customer': {
            'identification': str(cliente.get('cedula_nit') or '').strip(),
            'id_type': _tipo_documento(cliente.get('tipo_documento')),
            'name': (cliente.get('nombre') or '').strip(),
            'address': {
                'address': (cliente.get('direccion') or '').strip() or 'Sin dirección',
            },
            'contacts': [{
                'first_name': (cliente.get('nombre') or '').strip(),
                'email': (cliente.get('email') or '').strip(),
                'phone': {
                    'indicative': '57',
                    'number': str(cliente.get('telefono') or '').strip(),
                },
            }],
        },
        'seller': SIIGO_DEFAULT_SELLER,
        'items': [
            _item_siigo(it, SIIGO_DEFAULT_TAX_ID, SIIGO_DEFAULT_TAX_PERCENTAGE)
            for it in (venta.get('items') or [])
        ],
        'payments': [{
            'id': _forma_pago(venta.get('tipo_pago')),
            'value': float(venta.get('total') or 0),
        }],
        'observations': f"Venta local #{venta.get('id_venta')} — Ferretería",
    }
    # Siigo rechaza un seller con id 0; se omite para usar el del token.
    if not SIIGO_DEFAULT_SELLER:
        payload.pop('seller', None)
    return payload


# ── Emisión ─────────────────

def _es_transitorio(status):
    """Errores que ameritan reintento automático (red del lado Siigo, 5xx, 429)."""
    return status in (429, 500, 502, 503, 504)


def emitir_factura(venta, reintentos=None):
    """Emite la factura electrónica de una venta en Siigo.

    Intenta la transmisión y reintenta ante errores transitorios. Devuelve un
    dict normalizado con los datos del comprobante aprobado.

    Returns:
        {
          'numero': str,   # número de factura electrónica
          'cufe': str,     # código único de factura electrónica (CUDE/CUFE)
          'pdf_url': str,  # enlace al PDF oficial en Siigo
          'xml_url': str,
          'estado': 'aprobada',
          'respuesta': dict,  # respuesta cruda de Siigo (auditoría)
        }

    Raises:
        SiigoError: ante fallo de credenciales, red o rechazo de datos.
    """
    reintentos = SIIGO_MAX_REINTENTOS if reintentos is None else reintentos
    payload = construir_factura(venta)

    ultimo_error = None
    for intento in range(reintentos + 1):
        token = autenticar()
        status, cuerpo = _peticion_json('POST', '/v1/invoices', payload=payload, token=token)

        # Token vencido a mitad de camino: reintenta una vez con token nuevo.
        if status == 401 and intento < reintentos:
            autenticar(forzar=True)
            ultimo_error = SiigoError("Token de Siigo vencido", status=status, detalle=cuerpo)
            continue

        if status in (200, 201):
            return _normalizar_respuesta(cuerpo)

        ultimo_error = SiigoError(
            f"Siigo rechazó la factura: {_extraer_mensaje(cuerpo)}",
            status=status, detalle=cuerpo,
        )
        if _es_transitorio(status) and intento < reintentos:
            time.sleep(1.5 * (intento + 1))
            continue
        break

    raise ultimo_error or SiigoError("No se pudo transmitir a Siigo por un error desconocido")


def _normalizar_respuesta(cuerpo):
    """Extrae número, CUFE y URLs del PDF/XML de la respuesta de Siigo."""
    if not isinstance(cuerpo, dict):
        raise SiigoError(f"Respuesta inesperada de Siigo: {cuerpo}")

    numero = str(cuerpo.get('number') or cuerpo.get('name') or '').strip()

    # El CUFE puede venir al tope o anidado en `stamp`, según la versión del API.
    stamp = cuerpo.get('stamp') if isinstance(cuerpo.get('stamp'), dict) else {}
    cufe = str(
        cuerpo.get('cufe')
        or cuerpo.get('Cufe')
        or cuerpo.get('cude')
        or stamp.get('cufe')
        or stamp.get('cude')
        or ''
    ).strip()

    pdf_url = _buscar_url(cuerpo, ('pdf', 'pdf_url', 'public_url', 'url_pdf'))
    xml_url = _buscar_url(cuerpo, ('xml', 'xml_url', 'url_x'))

    if not numero:
        raise SiigoError(
            "Siigo respondió sin número de factura: " + _extraer_mensaje(cuerpo)
        )

    return {
        'numero': numero,
        'cufe': cufe,
        'pdf_url': pdf_url,
        'xml_url': xml_url,
        'estado': 'aprobada',
        'respuesta': cuerpo,
    }


def _buscar_url(cuerpo, claves):
    """Busca recursivamente una URL por nombre de clave en la respuesta."""
    if isinstance(cuerpo, dict):
        for clave in claves:
            if cuerpo.get(clave):
                return str(cuerpo[clave])
        for valor in cuerpo.values():
            encontrado = _buscar_url(valor, claves)
            if encontrado:
                return encontrado
    elif isinstance(cuerpo, list):
        for item in cuerpo:
            encontrado = _buscar_url(item, claves)
            if encontrado:
                return encontrado
    return ''
