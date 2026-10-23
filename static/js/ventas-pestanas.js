/* Pestañas de venta en paralelo para el POS (templates/index.html).
 *
 * El POS tiene UN solo formulario y UN solo `carrito` global. Este módulo no
 * lo duplica: guarda una "foto" de cada pestaña (carrito + campos del
 * formulario) y la vuelca al formulario al cambiar de pestaña. Así todo el
 * código existente (procesarVenta, totales, IVA) sigue funcionando igual y
 * solo se envía a la API lo que hay en la pestaña activa.
 *
 * Persistencia: localStorage, por usuario. Sobrevive a recargas y al cierre
 * del navegador. Se limpia la pestaña (no se borra) al facturar con éxito.
 */
(function () {
  'use strict';

  const barra = document.getElementById('pos-pestanas');
  if (!barra) return;

  const CLAVE = 'pos_pestanas_v1:' + (barra.dataset.usuario || 'anon');
  const DIARIO = 'Diario';
  let estado = null;
  let cambiando = false;

  const $ = (id) => document.getElementById(id);

  // tsCliente es un let del script de la página: no cuelga de window.
  const hayCliente = () => typeof tsCliente !== 'undefined' && !!tsCliente;

  function pestanaNueva(nombre, diario) {
    return {
      id: estado.siguiente++, nombre, diario: !!diario, carrito: [],
      cliente: null, tipo_pago: 'efectivo', direccion: '',
      tipo_entrega: 'entrega_inmediata', observaciones: '', tipo_doc: 'POS',
    };
  }

  function cargar() {
    try {
      const guardado = JSON.parse(localStorage.getItem(CLAVE) || 'null');
      if (guardado && Array.isArray(guardado.pestanas) && guardado.pestanas.length) {
        return guardado;
      }
    } catch (e) { /* datos corruptos: se empieza de cero */ }
    estado = { siguiente: 1, activa: 1, pestanas: [] };
    estado.pestanas.push(pestanaNueva(DIARIO, true));
    estado.activa = estado.pestanas[0].id;
    return estado;
  }

  const activa = () => estado.pestanas.find((p) => p.id === estado.activa);

  function persistir() {
    try { localStorage.setItem(CLAVE, JSON.stringify(estado)); } catch (e) { /* cuota llena */ }
  }

  // Foto del formulario actual -> pestaña activa.
  function capturar() {
    const p = activa();
    if (!p || cambiando) return;
    p.carrito = JSON.parse(JSON.stringify(carrito));
    p.cliente = (hayCliente() && tsCliente.getValue()) || p.cliente;
    p.tipo_pago = $('select-tipo-pago')?.value || 'efectivo';
    p.direccion = $('input-direccion-venta')?.value || '';
    p.tipo_entrega = $('input-tipo-entrega')?.value || 'entrega_inmediata';
    p.observaciones = $('input-observaciones-venta')?.value || '';
    p.tipo_doc = $('select-tipo-documento-dian')?.value || 'POS';
    persistir();
    dibujar();
  }

  function aplicarCliente(p) {
    if (!hayCliente()) return false;
    const id = String(p.cliente || (typeof CLIENTE_MOSTRADOR_ID !== 'undefined' ? CLIENTE_MOSTRADOR_ID : ''));
    if (id && !tsCliente.options[id]) {
      const c = clientesLista.find((x) => String(x.id) === id);
      if (!c) return false;
      tsCliente.addOption({ value: id, text: `${c.nombre} (${c.cedula_nit || 'Sin doc'})` });
    }
    if (id) tsCliente.setValue(id, true);
    return true;
  }

  // Pestaña activa -> formulario.
  function volcar() {
    const p = activa();
    if (!p) return;
    cambiando = true;
    try {
      carrito = JSON.parse(JSON.stringify(p.carrito));
      aplicarCliente(p);
      if ($('select-tipo-pago')) $('select-tipo-pago').value = p.tipo_pago;
      if ($('input-observaciones-venta')) $('input-observaciones-venta').value = p.observaciones;
      if ($('select-tipo-documento-dian')) $('select-tipo-documento-dian').value = p.tipo_doc;
      if (typeof seleccionarTipoEntrega === 'function') seleccionarTipoEntrega(p.tipo_entrega);
      if ($('input-direccion-venta')) $('input-direccion-venta').value = p.direccion;
      if (typeof actualizarTablaCarrito === 'function') actualizarTablaCarrito();
      if (typeof avanzarTipoDocumentoDian === 'function') avanzarTipoDocumentoDian();
    } finally {
      cambiando = false;
    }
    dibujar();
  }

  function dibujar() {
    barra.querySelectorAll('.pos-tab').forEach((n) => n.remove());
    const boton = $('pos-tab-nueva');
    estado.pestanas.forEach((p) => {
      const tab = document.createElement('div');
      tab.className = 'pos-tab' + (p.id === estado.activa ? ' activa' : '');
      tab.title = 'Doble clic para renombrar';

      const nombre = document.createElement('span');
      nombre.className = 'pos-tab-nombre';
      nombre.textContent = (p.diario ? '📅 ' : '') + p.nombre;
      tab.appendChild(nombre);

      if (p.carrito.length) {
        const n = document.createElement('span');
        n.className = 'badge bg-primary ms-1';
        n.textContent = p.carrito.length;
        tab.appendChild(n);
      }

      const editar = document.createElement('button');
      editar.type = 'button'; editar.className = 'pos-tab-btn';
      editar.title = 'Renombrar'; editar.textContent = '✏️';
      editar.onclick = (e) => { e.stopPropagation(); renombrar(p.id); };
      tab.appendChild(editar);

      if (estado.pestanas.length > 1) {
        const cerrar = document.createElement('button');
        cerrar.type = 'button'; cerrar.className = 'pos-tab-btn';
        cerrar.title = 'Cerrar pestaña'; cerrar.textContent = '×';
        cerrar.onclick = (e) => { e.stopPropagation(); cerrarPestana(p.id); };
        tab.appendChild(cerrar);
      }

      tab.onclick = () => seleccionar(p.id);
      tab.ondblclick = () => renombrar(p.id);
      barra.insertBefore(tab, boton);
    });
  }

  function seleccionar(id) {
    if (id === estado.activa) return;
    capturar();
    estado.activa = id;
    persistir();
    volcar();
  }

  function nueva() {
    capturar();
    const p = pestanaNueva('Pestaña ' + (estado.pestanas.length + 1), false);
    estado.pestanas.push(p);
    estado.activa = p.id;
    persistir();
    volcar();
  }

  function renombrar(id) {
    const p = estado.pestanas.find((x) => x.id === id);
    const nombre = prompt('Nombre de la pestaña:', p.nombre);
    if (nombre === null) return;
    p.nombre = nombre.trim().slice(0, 30) || p.nombre;
    persistir();
    dibujar();
  }

  function cerrarPestana(id) {
    if (estado.pestanas.length <= 1) return;
    const p = estado.pestanas.find((x) => x.id === id);
    const items = id === estado.activa ? carrito.length : p.carrito.length;
    if (items && !confirm(`La pestaña "${p.nombre}" tiene ${items} producto(s). ¿Cerrarla y descartarlos?`)) return;
    const eraActiva = id === estado.activa;
    const idx = estado.pestanas.indexOf(p);
    estado.pestanas.splice(idx, 1);
    if (eraActiva) {
      estado.activa = estado.pestanas[Math.max(0, idx - 1)].id;
      persistir();
      volcar();
    } else {
      persistir();
      dibujar();
    }
  }

  function envolver(nombre, despues) {
    const original = window[nombre];
    if (typeof original !== 'function') return;
    window[nombre] = function () {
      const r = original.apply(this, arguments);
      if (r && typeof r.then === 'function') return r.then((v) => { despues(); return v; });
      despues();
      return r;
    };
  }

  // Todo cambio del carrito pasa por actualizarTablaCarrito.
  envolver('actualizarTablaCarrito', capturar);
  envolver('limpiarFormularioVenta', capturar);
  // cargarFormularioVentas recrea el selector de cliente: se restaura el de la pestaña.
  envolver('cargarFormularioVentas', () => { aplicarCliente(activa()); if ($('input-direccion-venta') && !cambiando) $('input-direccion-venta').value = activa().direccion; });

  ['input', 'change'].forEach((ev) =>
    $('ventas')?.addEventListener(ev, (e) => {
      if (e.target.closest('#pos-pestanas')) return;
      capturar();
    }));
  window.addEventListener('beforeunload', capturar);

  $('pos-tab-nueva').onclick = nueva;

  // Arranque: se restaura la pestaña guardada. El selector de cliente se crea
  // de forma asíncrona, así que se espera a que exista para aplicarlo.
  estado = cargar();
  volcar();
  let intentos = 0;
  const espera = setInterval(() => {
    if ((hayCliente() && clientesLista.length) || ++intentos > 40) {
      clearInterval(espera);
      cambiando = true;
      try {
        aplicarCliente(activa());
        if ($('input-direccion-venta')) $('input-direccion-venta').value = activa().direccion;
        if (typeof actualizarTablaCarrito === 'function') actualizarTablaCarrito();
      } finally { cambiando = false; }
    }
  }, 250);
})();
