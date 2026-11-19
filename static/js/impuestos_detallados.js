'use strict';

const elemento = id => document.getElementById(id);
const campos = ['base_ventas', 'impuesto_ventas', 'base_compras', 'impuesto_compras'];
const formato = new Intl.NumberFormat('es-CO', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let filtrosAplicados = null;
let solicitud = 0;
let consultaTercero = 0;
let esperaTercero;

function fechaLocal(fecha) {
    return `${fecha.getFullYear()}-${String(fecha.getMonth() + 1).padStart(2, '0')}-${String(fecha.getDate()).padStart(2, '0')}`;
}

function establecerPeriodo() {
    const hoy = new Date();
    const anterior = elemento('periodo').value === 'anterior';
    if (elemento('periodo').value === 'personalizado') return;
    const inicio = new Date(hoy.getFullYear(), hoy.getMonth() - (anterior ? 1 : 0), 1);
    const fin = anterior ? new Date(hoy.getFullYear(), hoy.getMonth(), 0) : hoy;
    elemento('desde').value = fechaLocal(inicio);
    elemento('hasta').value = fechaLocal(fin);
}

function parametros() {
    const resultado = new URLSearchParams({ tipo: elemento('tipo').value, desde: elemento('desde').value, hasta: elemento('hasta').value, tercero: elemento('tercero').value.trim() });
    if (elemento('tarifas').value) resultado.append('tarifa', elemento('tarifas').value);
    if (elemento('cierre').checked) resultado.set('incluye_cierre', '1');
    return resultado;
}

function fechaVisible(fecha) {
    const partes = fecha.split('-');
    return partes.length === 3 ? `${partes[2]}/${partes[1]}/${partes[0]}` : fecha;
}

function celda(fila, valor) {
    const columna = document.createElement('td');
    columna.textContent = valor;
    fila.appendChild(columna);
}

function filaResumen(clase, etiqueta, totales, cambiar) {
    const fila = document.createElement('tr');
    fila.className = clase;
    const columna = document.createElement('td');
    columna.colSpan = 5;
    const boton = document.createElement('button');
    boton.type = 'button';
    boton.className = 'grupo-boton';
    boton.setAttribute('aria-expanded', 'true');
    const flecha = document.createElement('span');
    flecha.className = 'flecha';
    flecha.setAttribute('aria-hidden', 'true');
    flecha.textContent = '▾';
    boton.append(flecha, document.createTextNode(etiqueta));
    boton.addEventListener('click', () => {
        const expandido = boton.getAttribute('aria-expanded') === 'true';
        boton.setAttribute('aria-expanded', String(!expandido));
        flecha.textContent = expandido ? '▸' : '▾';
        cambiar(!expandido);
    });
    columna.appendChild(boton);
    fila.appendChild(columna);
    campos.forEach(campo => celda(fila, formato.format(Number(totales[campo]))));
    return fila;
}

function mostrar(datos) {
    const cuerpo = elemento('resultados');
    cuerpo.replaceChildren();
    let tipoExpandido = true;
    const grupos = [];
    function actualizar() {
        grupos.forEach(grupo => {
            grupo.fila.hidden = !tipoExpandido;
            grupo.detalles.forEach(fila => { fila.hidden = !tipoExpandido || !grupo.expandido; });
        });
    }
    cuerpo.appendChild(filaResumen('tipo', 'Tipo: IVA', datos.totales, valor => { tipoExpandido = valor; actualizar(); }));
    let cantidad = 0;
    datos.grupos.forEach(grupo => {
        const estado = { expandido: true, detalles: [] };
        const etiqueta = grupo.tarifa === 'sin-dato' ? 'Tarifa no disponible' : `IVA ${grupo.tarifa}%`;
        if (![...elemento('tarifas').options].some(opcion => opcion.value === grupo.tarifa)) {
            elemento('tarifas').add(new Option(etiqueta, grupo.tarifa));
        }
        estado.fila = filaResumen('tarifa', `Impuesto: ${etiqueta}`, grupo.totales, valor => { estado.expandido = valor; actualizar(); });
        cuerpo.appendChild(estado.fila);
        grupo.documentos.forEach(documento => {
            const fila = document.createElement('tr');
            fila.className = 'detalle';
            const comprobante = documento.comprobante + (documento.fuente === 'venta_sin_asiento' ? ' (sin asiento)' : '');
            [comprobante, documento.identificacion || '-', documento.sucursal || '-', documento.nombre || '-', fechaVisible(documento.fecha)].forEach(valor => celda(fila, valor));
            campos.forEach(campo => celda(fila, documento[campo] === null ? '—' : formato.format(Number(documento[campo]))));
            estado.detalles.push(fila);
            cuerpo.appendChild(fila);
            cantidad += 1;
        });
        grupos.push(estado);
    });
    const pendientes = datos.ventas_sin_desglose || 0;
    elemento('estado').textContent = cantidad ? `${cantidad} ${cantidad === 1 ? 'registro' : 'registros'}` +
        (pendientes ? `; ${pendientes} sin desglose fiscal, excluidos de los totales` : '') :
        'No se encontraron movimientos. Comprueba las fechas y los filtros seleccionados.';
}

async function buscar(evento) {
    if (evento) evento.preventDefault();
    if (!elemento('filtros').reportValidity()) return;
    const numeroSolicitud = ++solicitud;
    const filtros = parametros();
    elemento('error').classList.add('d-none');
    elemento('estado').textContent = 'Cargando informe...';
    elemento('exportar').disabled = true;
    try {
        const respuesta = await fetch(`/api/contabilidad/impuestos-detallados?${filtros}`);
        const datos = await respuesta.json();
        if (!respuesta.ok) throw new Error(datos.error || 'No se pudo consultar el informe.');
        if (numeroSolicitud !== solicitud) return;
        filtrosAplicados = filtros;
        mostrar(datos);
        elemento('periodo-aplicado').textContent = `${fechaVisible(datos.filtros.desde)} - ${fechaVisible(datos.filtros.hasta)}`;
        elemento('exportar').disabled = false;
    } catch (error) {
        if (numeroSolicitud !== solicitud) return;
        elemento('error').textContent = error.message || 'No se pudo consultar el informe.';
        elemento('error').classList.remove('d-none');
        elemento('estado').textContent = 'No se pudo actualizar el informe.';
    }
}

elemento('periodo').addEventListener('change', establecerPeriodo);
elemento('tercero').addEventListener('input', () => {
    clearTimeout(esperaTercero);
    const numeroConsulta = ++consultaTercero;
    const texto = elemento('tercero').value.trim();
    if (texto.length < 2) { elemento('terceros').replaceChildren(); return; }
    esperaTercero = setTimeout(async () => {
        try {
            const respuesta = await fetch(`/api/contabilidad/impuestos-detallados/terceros?q=${encodeURIComponent(texto)}`);
            if (!respuesta.ok || numeroConsulta !== consultaTercero) return;
            const terceros = await respuesta.json();
            if (numeroConsulta !== consultaTercero) return;
            elemento('terceros').replaceChildren(...terceros.map(tercero => {
                const opcion = document.createElement('option');
                opcion.value = tercero.identificacion || tercero.nombre;
                opcion.label = `${tercero.nombre} ${tercero.identificacion}`.trim();
                return opcion;
            }));
        } catch (error) {
            elemento('terceros').replaceChildren();
        }
    }, 250);
});
['desde', 'hasta'].forEach(id => elemento(id).addEventListener('change', () => { elemento('periodo').value = 'personalizado'; }));
elemento('filtros').addEventListener('submit', buscar);
elemento('limpiar').addEventListener('click', () => { elemento('filtros').reset(); establecerPeriodo(); buscar(); });
elemento('exportar').addEventListener('click', () => { if (filtrosAplicados) window.location.href = `/api/contabilidad/impuestos-detallados.xlsx?${filtrosAplicados}`; });
elemento('alternar-filtros').addEventListener('click', evento => {
    const oculto = elemento('filtros').hidden;
    elemento('filtros').hidden = !oculto;
    evento.currentTarget.setAttribute('aria-expanded', String(oculto));
    evento.currentTarget.textContent = oculto ? 'Ocultar criterios de búsqueda' : 'Mostrar criterios de búsqueda';
});
establecerPeriodo();
buscar();