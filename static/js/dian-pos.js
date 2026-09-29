/* Módulo de Facturación Electrónica DIAN para el POS (cliente).
 *
 * POR QUÉ ESTÁ EN UN ARCHIVO APARTE
 * ─────────────────────────────────
 * `templates/index.html` tiene ~6.000 líneas. Meter aquí una función más lo
 * volvía inmanejable (y de hecho el editor se rinde al abrirlo entero). Este
 * archivo concentra TODO lo que la interfaz del POS necesita saber del documento
 * electrónico:
 *
 *   1. Pintar el bloque del Documento Equivalente en la tirilla (CUIDE + QR).
 *   2. El badge de estado fiscal en el historial y en la lista de facturas.
 *   3. Emitir el documento de una venta desde el modal de la factura.
 *
 * Se carga con una sola etiqueta <script> en index.html y no depende de ninguna
 * otra librería: el QR NO se genera aquí, se pide al servidor, que es el que ya
 * tiene el generador verificado (`ferreteria/services/qr.py`).
 */

(function (global) {
  'use strict';

  // Estados posibles del documento electrónico y cómo se pinta cada uno.
  // Los colores coinciden con los del panel de la DIAN (`templates/dian.html`)
  // para que el estado se vea igual en todo el sistema.
  var ESTADOS = {
    sin_emitir:   { texto: 'Sin emitir',              badge: 'secondary' },
    pendiente:    { texto: 'Pendiente de envío',      badge: 'secondary' },
    firmado:      { texto: 'Firmado',                 badge: 'secondary' },
    enviado:      { texto: 'Enviado (en proceso)',    badge: 'info text-dark' },
    aceptado:     { texto: 'Aceptado DIAN',           badge: 'success' },
    rechazado:    { texto: 'Rechazado DIAN',          badge: 'danger' },
    contingencia: { texto: 'Contingencia',            badge: 'warning text-dark' },
    error:        { texto: 'Error de envío',          badge: 'danger' }
  };

  function estadoInfo(estado) {
    return ESTADOS[estado] || { texto: estado || 'Sin emitir', badge: 'secondary' };
  }

  function escapar(texto) {
    return String(texto === null || texto === undefined ? '' : texto)
      .replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
  }

  /* Badge de una línea con el estado fiscal (para tablas del POS). */
  function badge(dato) {
    var estado = (dato && dato.estado) || 'sin_emitir';
    var info = estadoInfo(estado);
    var numero = (dato && dato.numero) ? escapar(dato.numero) : '';
    var titulo = numero ? ' title="' + numero + '"' : '';
    return '<span class="badge bg-' + info.badge + '"' + titulo + '>'
      + info.texto + '</span>';
  }

  /* ── Bloque del documento en la tirilla ─────────────────────────────────
   * La tirilla DEBE llevar el CUIDE y el código QR: es lo que le permite al
   * cliente consultar su documento en el catálogo de la DIAN. Si el documento
   * todavía no se ha emitido, el bloque no se muestra (imprimir un QR de un
   * documento inexistente sería peor que no imprimir nada).
   */
  function pintarBloque(factura) {
    var bloque = document.getElementById('fac-bloque-dian');
    if (!bloque) return;

    var dato = (factura && factura.dian) || {};
    var numero = dato.numero || '';
    var cuide = dato.cuide || '';
    var qrUrl = dato.qr_url || '';

    // Sin documento emitido no hay nada que mostrar en la tirilla.
    if (!numero && !cuide) {
      bloque.style.display = 'none';
      return;
    }

    bloque.style.display = '';

    var info = estadoInfo(dato.estado);
    var nodoEstado = document.getElementById('fac-dian-estado');
    if (nodoEstado) {
      nodoEstado.innerHTML = '<span class="badge bg-' + info.badge + '">'
        + info.texto + '</span>';
    }

    var nodoNumero = document.getElementById('fac-dian-numero');
    if (nodoNumero) {
      nodoNumero.textContent = numero ? 'Documento: ' + numero : '';
    }

    var nodoCuide = document.getElementById('fac-dian-cuide');
    if (nodoCuide) {
      // El CUIDE se parte en trozos para que quepa en la tirilla térmica (58 mm)
      // sin desbordar; el CSS ya lo deja cortar, esto solo lo hace legible.
      nodoCuide.textContent = cuide ? 'CUIDE: ' + cuide : '';
    }

    // Resolución de facturación: el anexo técnico exige que el documento
    // equivalente declare bajo qué numeración fue emitido.
    var nodoRes = document.getElementById('fac-dian-resolucion');
    if (nodoRes) {
      var partes = [];
      if (dato.numero_resolucion) {
        partes.push('Resolución DIAN: ' + dato.numero_resolucion);
      }
      if (dato.prefijo_resolucion) {
        partes.push('Prefijo: ' + dato.prefijo_resolucion);
      }
      if (Number(dato.rango_hasta) > 0) {
        partes.push('Rango autorizado: ' + (dato.rango_desde || 1)
                    + ' - ' + dato.rango_hasta);
      }
      nodoRes.textContent = partes.join(' | ');
    }

    // Leyenda del software de facturacion: el anexo tecnico exige identificar
    // el software propio que genero el documento electronico.
    var nodoSoft = document.getElementById('fac-dian-software');
    if (nodoSoft) {
      var soft = [];
      if (dato.nombre_software) soft.push(dato.nombre_software);
      if (dato.version_software) soft.push('v' + dato.version_software);
      if (dato.empresa_software) soft.push(dato.empresa_software);
      nodoSoft.textContent = soft.length
        ? 'Software de facturación: ' + soft.join(' - ')
        : '';
    }

    pintarQr(qrUrl);
  }

  /* ¿La venta ya fue emitida a la DIAN? Si lo esta, no se puede editar ni
   * anular: el documento firmado dejaria de coincidir con la venta. El servidor
   * tambien lo bloquea (HTTP 409), esto solo oculta el boton para que el usuario
   * no se encuentre con un error. */
  function estaEmitida(factura) {
    if (!factura) return false;
    if (factura.anulada) return true;
    var estado = (factura.dian && factura.dian.estado) || 'sin_emitir';
    return estado !== 'sin_emitir';
  }

  /* Pide el QR al SERVIDOR y lo deja en un <img>.
   *
   * Se genera en el servidor (no con una librería JS) porque allí vive el
   * generador ya verificado, y así el navegador no descarga una dependencia más.
   * El endpoint devuelve un PNG.
   *
   * Se usa un <img> y NO un <canvas> a propósito: `cloneNode` (impresión) y
   * html2canvas (PDF) no trasladan el contenido dibujado de un canvas, así que
   * con canvas el QR salía vacío en la tirilla impresa.
   */
  function pintarQr(qrUrl) {
    var imagen = document.getElementById('fac-dian-qr');
    if (!imagen) return;

    if (!qrUrl) {
      imagen.style.display = 'none';
      imagen.removeAttribute('src');
      return;
    }

    imagen.src = '/api/dian/qr?url=' + encodeURIComponent(qrUrl);
    imagen.style.display = '';
    imagen.onerror = function () {
      // Si el QR no se pudo generar (URL demasiado larga), la tirilla se imprime
      // sin él en lugar de con un recuadro roto.
      imagen.style.display = 'none';
    };
  }

  /* ── Emisión desde el modal de la factura ───────────────────────────────
   * Botón para emitir el documento de la venta que se está viendo. Se usa
   * cuando la emisión automática está apagada (lo normal mientras se configura
   * el software ante la DIAN).
   */
  function emitirFactura(idVenta, opciones) {
    opciones = opciones || {};
    var cuerpo = {
      contingencia: !!opciones.contingencia,
      forzar: !!opciones.forzar
    };

    return fetch('/api/dian/emitir/' + idVenta, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cuerpo)
    })
      .then(function (respuesta) {
        return respuesta.json().then(function (datos) {
          if (!respuesta.ok) throw new Error(datos.error || 'Error al emitir');
          return datos;
        });
      })
      .then(function (resultado) {
        var info = estadoInfo(resultado.estado);
        alert('Documento ' + resultado.numero + ': ' + info.texto + '\n\n'
          + (resultado.mensaje || ''));
        return resultado;
      })
      .catch(function (error) {
        alert('No se pudo emitir el documento: ' + error.message);
        throw error;
      });
  }

  /* ¿Se puede emitir el documento de esta factura? Se usa para mostrar u
   * ocultar el botón: no tiene sentido ofrecer emitir una venta anulada o que
   * ya tiene documento aceptado. */
  function sePuedeEmitir(factura) {
    if (!factura || factura.anulada) return false;
    var estado = (factura.dian && factura.dian.estado) || 'sin_emitir';
    return estado === 'sin_emitir' || estado === 'error'
      || estado === 'rechazado' || estado === 'contingencia';
  }

  global.DianPos = {
    ESTADOS: ESTADOS,
    estadoInfo: estadoInfo,
    badge: badge,
    pintarBloque: pintarBloque,
    emitirFactura: emitirFactura,
    sePuedeEmitir: sePuedeEmitir
  };
})(window);