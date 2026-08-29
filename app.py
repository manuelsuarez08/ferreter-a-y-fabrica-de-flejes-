from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, render_template
import sqlite3
from datetime import datetime, timedelta
import requests
import json
import re

# Importar configuración
from config import OPENAI_API_KEY

app = Flask(__name__)
app.secret_key = "ferreteria_secret_key_2026"

# Configurar OpenAI
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json"
}

# Configuración de la base de datos
DB_NAME = "ferreteria.db"

# HTML Integrado
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sistema de Gestión - Ferretería</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
  <style>
    body { background-color: #f4f6f9; }
    .nav-tabs .nav-link.active { font-weight: bold; background-color: #0d6efd; color: white !important; }
    .card { border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    
    .asistente-float-btn {
      position: fixed;
      bottom: 25px;
      right: 25px;
      width: 60px;
      height: 60px;
      background-color: #0d6efd;
      color: white;
      border-radius: 50%;
      display: flex;
      justify-content: center;
      align-items: center;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      cursor: pointer;
      z-index: 9999;
      transition: transform 0.2s, background-color 0.2s;
    }
    .asistente-float-btn:hover {
      transform: scale(1.1);
      background-color: #0b5ed7;
    }

    .asistente-chat-box {
      position: fixed;
      bottom: 95px;
      right: 25px;
      width: 350px;
      max-width: 90vw;
      height: 480px;
      background-color: #ffffff;
      border-radius: 12px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.2);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      z-index: 9999;
      transition: opacity 0.3s ease, transform 0.3s ease;
    }
    .asistente-chat-box.oculto {
      opacity: 0;
      pointer-events: none;
      transform: translateY(20px);
    }

    .chat-header {
      background-color: #0d6efd;
      color: white;
      padding: 12px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: bold;
    }
    .chat-header button { background: transparent; border: none; color: white; font-size: 18px; cursor: pointer; }

    .chat-body {
      flex: 1;
      padding: 12px;
      overflow-y: auto;
      background-color: #f8f9fa;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .msg-bot, .msg-user {
      max-width: 85%;
      padding: 8px 12px;
      border-radius: 12px;
      font-size: 14px;
      line-height: 1.4;
      white-space: pre-wrap;
    }
    .msg-bot { background-color: #e9ecef; color: #212529; align-self: flex-start; }
    .msg-user { background-color: #0d6efd; color: white; align-self: flex-end; }

    .chat-footer {
      display: flex;
      padding: 10px;
      border-top: 1px solid #dee2e6;
      background-color: #fff;
      align-items: center;
      gap: 8px;
    }
    .chat-footer input {
      flex: 1;
      border: 1px solid #ced4da;
      border-radius: 20px;
      padding: 6px 12px;
      font-size: 14px;
      outline: none;
    }
    .chat-footer button {
      background-color: #0d6efd;
      color: white;
      border: none;
      border-radius: 50%;
      width: 35px;
      height: 35px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .calendar-shell {
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 4px 14px rgba(0,0,0,0.05);
      padding: 18px;
    }
    .calendar-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
      gap: 12px;
    }
    .calendar-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(220px, 1fr));
      gap: 18px;
    }
    .month-card {
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 12px 10px 10px;
      background: #fafafa;
      min-height: 240px;
    }
    .month-card h6 {
      text-align: center;
      font-weight: 700;
      margin-bottom: 10px;
      color: #1f2937;
    }
    .month-weekdays, .month-days {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 4px;
      text-align: center;
    }
    .month-weekdays div {
      font-size: 11px;
      color: #6b7280;
      font-weight: 600;
      padding-bottom: 5px;
      min-height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .month-day {
      border: 1px solid #e5e7eb;
      background: #fff;
      border-radius: 8px;
      min-height: 34px;
      height: 34px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-direction: column;
      font-size: 12px;
      color: #374151;
      cursor: pointer;
      transition: all 0.15s ease;
      padding: 2px 0;
      line-height: 1.1;
    }
    .month-day.empty {
      background: transparent;
      border: 1px dashed #f0f0f0;
      cursor: default;
    }
    .month-day:hover:not(.empty) {
      background: #eaf2ff;
      border-color: #9ec5fe;
    }
    .month-day.selected {
      background: #0d6efd;
      color: #fff;
      border-color: #0d6efd;
    }
    .month-day.has-events .day-badge {
      background: #198754;
      color: white;
      border-radius: 999px;
      font-size: 9px;
      padding: 2px 4px;
      margin-top: 1px;
      line-height: 1.1;
      min-width: 16px;
    }
    .day-event-list {
      margin-top: 18px;
      background: #f8fafc;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 14px;
    }
    .event-item {
      border-left: 4px solid #0d6efd;
      background: #fff;
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 10px;
    }
    @media (max-width: 1200px) {
      .calendar-grid {
        grid-template-columns: repeat(2, minmax(220px, 1fr));
      }
    }
    @media (max-width: 700px) {
      .calendar-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>

  <nav class="navbar navbar-dark bg-dark px-3 mb-4">
    <span class="navbar-brand mb-0 h1"><i class="bi bi-tools me-2"></i>Sistema de Gestión - Ferretería</span>
    <div class="text-light">
      Usuario: <strong>{{ usuario }}</strong> ({{ rol }}) |
      <a href="{{ url_for('logout') }}" class="btn btn-sm btn-outline-danger ms-2"><i class="bi bi-box-arrow-right"></i> Cerrar Sesión</a>
    </div>
  </nav>

  <div class="container-fluid px-4">
    <ul class="nav nav-tabs mb-4" id="mainTabs" role="tablist">
      <li class="nav-item">
        <button class="nav-link active" id="ventas-tab" data-bs-toggle="tab" data-bs-target="#tab-ventas" type="button">🛒 Ventas / Facturación</button>
      </li>
      <li class="nav-item">
        <button class="nav-link" id="productos-tab" data-bs-toggle="tab" data-bs-target="#tab-productos" type="button">📦 Productos</button>
      </li>
      <li class="nav-item">
        <button class="nav-link" id="clientes-tab" data-bs-toggle="tab" data-bs-target="#tab-clientes" type="button">👥 Clientes</button>
      </li>
      <li class="nav-item">
        <button class="nav-link" id="creditos-tab" data-bs-toggle="tab" data-bs-target="#tab-creditos" type="button">💳 Créditos / Estado de Cuenta</button>
      </li>
      <li class="nav-item">
        <button class="nav-link" id="historial-tab" data-bs-toggle="tab" data-bs-target="#tab-historial" type="button">📋 Historial Ventas</button>
      </li>
    </ul>

    <div class="tab-content" id="mainTabsContent">
      <div class="tab-pane fade show active" id="tab-ventas">
        <div class="row">
          <div class="col-md-5">
            <div class="card p-4 mb-3">
              <h5 class="text-primary mb-3">1. Selección de Cliente y Pago</h5>
              <div class="mb-3">
                <label class="form-label fw-bold">Cliente</label>
                <select id="ventaCliente" class="form-select" required></select>
                <small class="text-muted">Por defecto: Cliente Mostrador (General)</small>
              </div>
              <div class="mb-3">
                <label class="form-label fw-bold">Tipo de Pago</label>
                <select id="ventaTipoPago" class="form-select">
                  <option value="contado">💵 Contado</option>
                  <option value="credito">💳 Crédito (Fiado)</option>
                </select>
              </div>
            </div>

            <div class="card p-4">
              <h5 class="text-primary mb-3">2. Seleccionar Producto para la Factura</h5>
              <div class="mb-3">
                <label class="form-label">Producto</label>
                <select id="ventaProducto" class="form-select"></select>
              </div>
              <div class="mb-3">
                <label class="form-label">Cantidad</label>
                <input type="number" id="ventaCantidad" class="form-control" value="1" min="1">
              </div>
              <button type="button" class="btn btn-secondary w-100" onclick="agregarProductoAlCarrito()">➕ Agregar a la Factura</button>
            </div>
          </div>

          <div class="col-md-7">
            <div class="card p-4">
              <h4 class="mb-3 text-success">📋 Factura de Venta</h4>
              <table class="table table-bordered align-middle">
                <thead class="table-dark">
                  <tr>
                    <th>Producto</th>
                    <th>Precio U.</th>
                    <th>Cant.</th>
                    <th>Subtotal</th>
                    <th>Acción</th>
                  </tr>
                </thead>
                <tbody id="tablaCarrito">
                  <tr><td colspan="5" class="text-center text-muted">Aún no has agregado productos a la factura.</td></tr>
                </tbody>
              </table>

              <div class="d-flex justify-content-between align-items-center mt-3 pt-3 border-top">
                <h3>Total General:</h3>
                <h2 id="lblTotalFactura" class="text-success fw-bold">$0 COP</h2>
              </div>

              <button class="btn btn-success btn-lg w-100 mt-3" onclick="procesarVentaCompleta()">✅ Procesar y Registrar Venta</button>
            </div>
          </div>
        </div>
      </div>

      <div class="tab-pane fade" id="tab-productos">
        <div class="card p-4 mb-4">
          <h4>Agregar Producto</h4>
          <form id="formProducto" class="row g-3">
            <div class="col-md-3"><input type="text" id="prodNombre" class="form-control" placeholder="Nombre (ej. Cemento)" required></div>
            <div class="col-md-2"><input type="text" id="prodCategoria" class="form-control" placeholder="Categoría" required></div>
            <div class="col-md-2"><input type="text" id="prodDimensiones" class="form-control" placeholder="Dimensiones"></div>
            <div class="col-md-2"><input type="number" id="prodCosto" class="form-control" placeholder="Precio Costo" required></div>
            <div class="col-md-2"><input type="number" id="prodVenta" class="form-control" placeholder="Precio Venta" required></div>
            <div class="col-md-1"><input type="number" id="prodStock" class="form-control" placeholder="Stock" required></div>
            <div class="col-md-1"><input type="number" id="prodStockMin" class="form-control" placeholder="Mínimo" value="5" required></div>
            <div class="col-md-2"><button type="submit" class="btn btn-primary w-100">Guardar</button></div>
          </form>
        </div>
        <div class="card p-4">
          <h4>Inventario de Bodega</h4>
          <table class="table table-striped mt-2">
            <thead>
              <tr><th>ID</th><th>Nombre</th><th>Categoría</th><th>Dimensiones</th><th>Precio Venta</th><th>Stock</th></tr>
            </thead>
            <tbody id="tablaProductos"></tbody>
          </table>
        </div>
      </div>

      <div class="tab-pane fade" id="tab-clientes">
        <div class="card p-4 mb-4">
          <h4>Registrar Nuevo Cliente</h4>
          <form id="formCliente" class="row g-3">
            <div class="col-md-3"><input type="text" id="cliNombre" class="form-control" placeholder="Nombre completo" required></div>
            <div class="col-md-3"><input type="text" id="cliCedula" class="form-control" placeholder="CC / NIT"></div>
            <div class="col-md-3"><input type="text" id="cliTelefono" class="form-control" placeholder="Teléfono"></div>
            <div class="col-md-3"><button type="submit" class="btn btn-primary w-100">Guardar Cliente</button></div>
          </form>
        </div>
        <div class="card p-4">
          <h4>Directorio de Clientes</h4>
          <table class="table table-striped mt-2">
            <thead>
              <tr><th>ID</th><th>Nombre</th><th>CC/NIT</th><th>Teléfono</th></tr>
            </thead>
            <tbody id="tablaClientes"></tbody>
          </table>
        </div>
      </div>

      <div class="tab-pane fade" id="tab-creditos">
        <div class="card p-4 mb-4">
          <h4>Estado de Cuenta y Cartera</h4>
          <div class="row g-3">
            <div class="col-md-6">
              <select id="selectClienteCredito" class="form-select">
                <option value="">-- Seleccionar Cliente --</option>
              </select>
            </div>
            <div class="col-md-3">
              <button class="btn btn-primary w-100" onclick="cargarEstadoCuentaCliente()">Consultar Cuenta</button>
            </div>
          </div>
        </div>

        <div id="vistaEstadoCuenta" class="card p-4 d-none">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <div>
              <h3 id="lblClienteNombre" class="text-primary mb-0">ESTADO DE CUENTA</h3>
            </div>
            <div class="text-end">
              <h2 id="lblDeudaTotal" class="text-danger mb-0">$0 COP</h2>
              <small class="text-uppercase fw-bold text-muted">DEUDA TOTAL PENDIENTE</small>
            </div>
          </div>
          <hr>
          <div class="card bg-light p-3 mb-4">
            <h5 class="text-success">💵 Aplicar Abono a la Deuda Global:</h5>
            <div class="row g-2 align-items-center">
              <div class="col-auto"><span>$</span></div>
              <div class="col-md-4">
                <input type="number" id="montoAbonoGlobal" class="form-control" placeholder="Monto a abonar">
              </div>
              <div class="col-md-3">
                <button class="btn btn-success w-100" onclick="procesarAbonoGlobal()">Registrar Abono</button>
              </div>
            </div>
          </div>

          <h5>Facturas Pendientes:</h5>
          <div id="contenedorDeudasFacturas" class="mb-4"></div>

          <h5>Historial de Abonos:</h5>
          <div id="contenedorHistorialAbonos"></div>
        </div>
      </div>

      <div class="tab-pane fade" id="tab-historial">
        <div class="card p-4">
          <div class="calendar-shell">
            <div class="calendar-header">
              <button class="btn btn-outline-secondary btn-sm" onclick="cambiarAnioCalendario(-1)">◀</button>
              <h4 class="mb-0">Calendario de Ventas <span id="anioCalendarioLabel"></span></h4>
              <button class="btn btn-outline-secondary btn-sm" onclick="cambiarAnioCalendario(1)">▶</button>
            </div>
            <div id="calendarioVentas" class="calendar-grid"></div>
            <div class="day-event-list">
              <h5 id="tituloDiaSeleccionado">Eventos del día</h5>
              <div id="detallesVentasDia"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="asistente-float-btn" onclick="toggleAsistenteIA()" title="Asistente Virtual">
    <i class="bi bi-robot fs-3"></i>
  </div>

  <div id="asistenteChatBox" class="asistente-chat-box oculto">
    <div class="chat-header">
      <span><i class="bi bi-robot me-2"></i>Asistente Virtual</span>
      <button onclick="toggleAsistenteIA()">✕</button>
    </div>
    <div id="chatBody" class="chat-body">
      <div class="msg-bot">¡Hola! 🛠️ ¿En qué te puedo colaborar? Pregúntame sobre precios, existencias o cartera contable.</div>
    </div>
    <div class="chat-footer">
      <input type="text" id="chatInput" placeholder="Escribe tu duda..." onkeypress="handleKeyPress(event)">
      <button onclick="enviarPreguntaIA()">➤</button>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    let productosDisponibles = [];
    let carritoVenta = [];
    let historialVentasGlobal = [];
    let anioCalendarioActual = new Date().getFullYear();
    let fechaCalendarioSeleccionada = null;

    document.addEventListener("DOMContentLoaded", () => {
      cargarClientes();
      cargarProductos();
      cargarHistorialVentas();
    });

    function getVentasPorFecha() {
      const mapa = new Map();
      historialVentasGlobal.forEach(v => {
        const fecha = (v.fecha_dia || '').slice(0, 10);
        if (!fecha) return;
        if (!mapa.has(fecha)) mapa.set(fecha, []);
        mapa.get(fecha).push(v);
      });
      return mapa;
    }

    function formatearFechaLegible(fechaStr) {
      if (!fechaStr) return 'Sin fecha';
      const [anio, mes, dia] = fechaStr.split('-');
      const fecha = new Date(Number(anio), Number(mes) - 1, Number(dia));
      return fecha.toLocaleDateString('es-ES', { day: '2-digit', month: 'long', year: 'numeric' });
    }

    function renderDetalleVentasDia(fecha) {
      const contenedor = document.getElementById('detallesVentasDia');
      const titulo = document.getElementById('tituloDiaSeleccionado');
      const ventas = getVentasPorFecha().get(fecha) || [];

      titulo.textContent = `Eventos del día: ${formatearFechaLegible(fecha)}`;
      contenedor.innerHTML = '';

      if (!ventas.length) {
        contenedor.innerHTML = '<div class="text-muted">No hay ventas registradas para este día.</div>';
        return;
      }

      ventas.forEach(v => {
        const item = document.createElement('div');
        item.className = 'event-item';
        item.innerHTML = `
          <div><strong>Venta #${v.id}</strong> - ${v.cliente || 'Cliente'}</div>
          <div class="small text-muted">${v.hora || 'Hora no registrada'} • ${v.tipo_pago || 'contado'}</div>
          <div class="mt-1"><strong>Total:</strong> $${Number(v.total_venta || 0).toLocaleString()} COP</div>
          <div class="mt-1 text-muted">${v.productos_detalle || 'Sin detalle'}</div>
        `;
        contenedor.appendChild(item);
      });
    }

    function renderCalendarioAnual(anio) {
      const contenedor = document.getElementById('calendarioVentas');
      const label = document.getElementById('anioCalendarioLabel');
      label.textContent = anio;

      const meses = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'];
      const diasSemana = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];
      const ventasPorFecha = getVentasPorFecha();

      contenedor.innerHTML = '';

      meses.forEach((mes, indexMes) => {
        const card = document.createElement('div');
        card.className = 'month-card';

        const titulo = document.createElement('h6');
        titulo.textContent = mes;
        card.appendChild(titulo);

        const semanaHeader = document.createElement('div');
        semanaHeader.className = 'month-weekdays';
        diasSemana.forEach(d => {
          const node = document.createElement('div');
          node.textContent = d;
          semanaHeader.appendChild(node);
        });
        card.appendChild(semanaHeader);

        const monthGrid = document.createElement('div');
        monthGrid.className = 'month-days';

        const primerDia = new Date(anio, indexMes, 1);
        const ultimoDiaMes = new Date(anio, indexMes + 1, 0).getDate();
        const primerDiaSemana = (primerDia.getDay() + 6) % 7;

        for (let i = 0; i < primerDiaSemana; i++) {
          const emptyCell = document.createElement('div');
          emptyCell.className = 'month-day empty';
          monthGrid.appendChild(emptyCell);
        }

        for (let dia = 1; dia <= ultimoDiaMes; dia++) {
          const fecha = `${anio}-${String(indexMes + 1).padStart(2,'0')}-${String(dia).padStart(2,'0')}`;
          const ventasDia = ventasPorFecha.get(fecha) || [];
          const cell = document.createElement('button');
          cell.type = 'button';
          cell.className = 'month-day';
          if (fecha === fechaCalendarioSeleccionada) cell.classList.add('selected');
          if (ventasDia.length) cell.classList.add('has-events');

          const dayLabel = document.createElement('span');
          dayLabel.textContent = dia;
          cell.appendChild(dayLabel);

          if (ventasDia.length) {
            const badge = document.createElement('span');
            badge.className = 'day-badge';
            badge.textContent = ventasDia.length;
            cell.appendChild(badge);
          }

          cell.addEventListener('click', () => {
            fechaCalendarioSeleccionada = fecha;
            renderCalendarioAnual(anio);
            renderDetalleVentasDia(fecha);
          });

          monthGrid.appendChild(cell);
        }

        card.appendChild(monthGrid);
        contenedor.appendChild(card);
      });

      if (!fechaCalendarioSeleccionada) {
        const fechas = Array.from(ventasPorFecha.keys()).sort();
        fechaCalendarioSeleccionada = fechas[fechas.length - 1] || null;
      }

      if (fechaCalendarioSeleccionada) {
        renderDetalleVentasDia(fechaCalendarioSeleccionada);
      } else {
        document.getElementById('tituloDiaSeleccionado').textContent = 'Eventos del día';
        document.getElementById('detallesVentasDia').innerHTML = '<div class="text-muted">No hay ventas registradas en este año.</div>';
      }
    }

    function cambiarAnioCalendario(delta) {
      anioCalendarioActual += delta;
      renderCalendarioAnual(anioCalendarioActual);
    }

    async function cargarClientes() {
      try {
        const res = await fetch('/api/clientes');
        const clientes = await res.json();
        
        const selectVenta = document.getElementById('ventaCliente');
        const selectCredito = document.getElementById('selectClienteCredito');
        const tbody = document.getElementById('tablaClientes');
        
        selectVenta.innerHTML = '';
        selectCredito.innerHTML = '<option value="">-- Seleccionar Cliente --</option>';
        tbody.innerHTML = '';

        clientes.forEach(c => {
          const selected = c.id === 1 ? 'selected' : '';
          selectVenta.innerHTML += `<option value="${c.id}" ${selected}>${c.nombre}</option>`;
          selectCredito.innerHTML += `<option value="${c.id}">${c.nombre} (CC/NIT: ${c.cedula_nit || 'N/A'})</option>`;
          tbody.innerHTML += `<tr><td>${c.id}</td><td>${c.nombre}</td><td>${c.cedula_nit || '-'}</td><td>${c.telefono || '-'}</td></tr>`;
        });
      } catch (e) {
        console.error("Error al cargar clientes:", e);
      }
    }

    async function cargarProductos() {
      try {
        const res = await fetch('/api/productos');
        productosDisponibles = await res.json();
        const selectVenta = document.getElementById('ventaProducto');
        const tbody = document.getElementById('tablaProductos');

        selectVenta.innerHTML = '';
        tbody.innerHTML = '';

        productosDisponibles.forEach(p => {
          selectVenta.innerHTML += `<option value="${p.id}">${p.nombre} ($${p.precio_venta.toLocaleString()}) - Stock: ${p.stock_actual}</option>`;
          tbody.innerHTML += `<tr><td>${p.id}</td><td>${p.nombre}</td><td>${p.categoria}</td><td>${p.dimensiones || '-'}</td><td>$${p.precio_venta.toLocaleString()}</td><td>${p.stock_actual}</td></tr>`;
        });
      } catch (e) {
        console.error("Error al cargar productos:", e);
      }
    }

    async function cargarHistorialVentas() {
      try {
        const res = await fetch('/api/ventas');
        historialVentasGlobal = await res.json();
        const fechas = Array.from(getVentasPorFecha().keys()).sort();
        if (fechas.length) {
          fechaCalendarioSeleccionada = fechas[fechas.length - 1];
        }
        renderCalendarioAnual(anioCalendarioActual);
      } catch (e) {
        console.error("Error al cargar historial de ventas:", e);
      }
    }

    function agregarProductoAlCarrito() {
      const idProducto = parseInt(document.getElementById('ventaProducto').value);
      const cantidad = parseInt(document.getElementById('ventaCantidad').value);

      if (!idProducto || cantidad <= 0) return alert("Ingresa una cantidad válida.");

      const prod = productosDisponibles.find(p => p.id === idProducto);
      if (!prod) return;

      if (cantidad > prod.stock_actual) {
        return alert(`Stock insuficiente. Solo hay ${prod.stock_actual} unidades disponibles de ${prod.nombre}.`);
      }

      const existeIdx = carritoVenta.findIndex(item => item.id_producto === idProducto);
      if (existeIdx >= 0) {
        if (carritoVenta[existeIdx].cantidad + cantidad > prod.stock_actual) {
          return alert(`No puedes superar las ${prod.stock_actual} unidades disponibles en inventario.`);
        }
        carritoVenta[existeIdx].cantidad += cantidad;
        carritoVenta[existeIdx].subtotal = carritoVenta[existeIdx].cantidad * prod.precio_venta;
      } else {
        carritoVenta.push({
          id_producto: prod.id,
          nombre: prod.nombre,
          precio_venta: prod.precio_venta,
          cantidad: cantidad,
          subtotal: prod.precio_venta * cantidad
        });
      }

      document.getElementById('ventaCantidad').value = 1;
      renderizarCarrito();
    }

    function eliminarDelCarrito(index) {
      carritoVenta.splice(index, 1);
      renderizarCarrito();
    }

    function renderizarCarrito() {
      const tbody = document.getElementById('tablaCarrito');
      tbody.innerHTML = '';
      let total = 0;

      if (carritoVenta.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Aún no has agregado productos a la factura.</td></tr>';
        document.getElementById('lblTotalFactura').textContent = '$0 COP';
        return;
      }

      carritoVenta.forEach((item, idx) => {
        total += item.subtotal;
        tbody.innerHTML += `
          <tr>
            <td>${item.nombre}</td>
            <td>$${item.precio_venta.toLocaleString()}</td>
            <td>${item.cantidad}</td>
            <td>$${item.subtotal.toLocaleString()}</td>
            <td><button class="btn btn-sm btn-danger" onclick="eliminarDelCarrito(${idx})">❌</button></td>
          </tr>
        `;
      });

      document.getElementById('lblTotalFactura').textContent = `$${total.toLocaleString()} COP`;
    }

    async function procesarVentaCompleta() {
      const selectCliente = document.getElementById('ventaCliente');
      const selectTipoPago = document.getElementById('ventaTipoPago');

      const idCliente = parseInt(selectCliente.value);
      const tipoPago = selectTipoPago.value;

      if (!idCliente || isNaN(idCliente)) {
        return alert("Por favor, selecciona un cliente válido.");
      }
      
      if (carritoVenta.length === 0) {
        return alert("Agrega al menos un producto a la factura antes de procesar.");
      }

      const body = {
        id_cliente: idCliente,
        tipo_pago: tipoPago,
        items: carritoVenta.map(item => ({
          id_producto: parseInt(item.id_producto),
          cantidad: parseInt(item.cantidad)
        }))
      };

      try {
        const res = await fetch('/api/ventas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });

        const textData = await res.text();
        let data;
        try {
          data = JSON.parse(textData);
        } catch (e) {
          data = { error: textData || "Respuesta no válida del servidor" };
        }

        if (res.ok) {
          alert("🎉 " + (data.mensaje || "Venta registrada con éxito."));
          carritoVenta = [];
          renderizarCarrito();
          await cargarProductos();
          await cargarHistorialVentas();
        } else {
          alert("❌ Error al registrar la venta: " + (data.error || "Ocurrió un error inesperado."));
        }
      } catch (error) {
        alert("❌ Error de comunicación con el servidor: " + error.message);
      }
    }

    document.getElementById('formProducto').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = {
        nombre: document.getElementById('prodNombre').value,
        categoria: document.getElementById('prodCategoria').value,
        dimensiones: document.getElementById('prodDimensiones').value,
        precio_costo: parseFloat(document.getElementById('prodCosto').value),
        precio_venta: parseFloat(document.getElementById('prodVenta').value),
        stock_actual: parseInt(document.getElementById('prodStock').value),
        stock_minimo: parseInt(document.getElementById('prodStockMin').value)
      };
      await fetch('/api/productos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      document.getElementById('formProducto').reset();
      await cargarProductos();
    });

    document.getElementById('formCliente').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = {
        nombre: document.getElementById('cliNombre').value,
        cedula_nit: document.getElementById('cliCedula').value,
        telefono: document.getElementById('cliTelefono').value
      };
      await fetch('/api/clientes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      document.getElementById('formCliente').reset();
      await cargarClientes();
    });

    async function cargarEstadoCuentaCliente() {
      const idCliente = document.getElementById('selectClienteCredito').value;
      if (!idCliente) return alert("Por favor selecciona un cliente.");

      const res = await fetch(`/api/historial-cliente/${idCliente}`);
      const data = await res.json();

      document.getElementById('vistaEstadoCuenta').classList.remove('d-none');
      const contDeudas = document.getElementById('contenedorDeudasFacturas');
      const contAbonos = document.getElementById('contenedorHistorialAbonos');

      contDeudas.innerHTML = '';
      contAbonos.innerHTML = '';

      let deudaTotal = 0;
      data.deudas.forEach(d => {
        deudaTotal += d.saldo_pendiente;
        contDeudas.innerHTML += `<div class="border p-2 mb-2 rounded bg-white">
          <strong>Factura #${d.id_venta}</strong> - ${d.fecha}<br>
          Productos: ${d.productos_detalle || '-'}<br>
          Total Venta: $${d.total_venta.toLocaleString()} COP | <span class="text-danger fw-bold">Saldo Pendiente: $${d.saldo_pendiente.toLocaleString()} COP</span>
        </div>`;
      });

      document.getElementById('lblDeudaTotal').textContent = `$${deudaTotal.toLocaleString()} COP`;

      if (data.abonos.length === 0) {
        contAbonos.innerHTML = '<p class="text-muted">No hay registros de abonos recibidos.</p>';
      } else {
        data.abonos.forEach(a => {
          contAbonos.innerHTML += `<div class="border p-2 mb-1 bg-light">
            Abono de <strong>$${a.monto.toLocaleString()} COP</strong> el ${a.fecha} (Factura #${a.id_venta})
          </div>`;
        });
      }
    }

    async function procesarAbonoGlobal() {
      const idCliente = document.getElementById('selectClienteCredito').value;
      const monto = parseFloat(document.getElementById('montoAbonoGlobal').value);

      if (!idCliente || !monto || monto <= 0) return alert("Ingresa un monto de abono válido.");

      const res = await fetch('/api/abonos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_cliente: parseInt(idCliente), monto: monto })
      });

      const data = await res.json();
      if (res.ok) {
        alert(data.mensaje);
        document.getElementById('montoAbonoGlobal').value = '';
        cargarEstadoCuentaCliente();
      } else {
        alert(data.error || "Error al procesar el abono.");
      }
    }

    function toggleAsistenteIA() {
      const chatBox = document.getElementById('asistenteChatBox');
      chatBox.classList.toggle('oculto');
      if (!chatBox.classList.contains('oculto')) {
        document.getElementById('chatInput').focus();
      }
    }

    function handleKeyPress(e) {
      if (e.key === 'Enter') enviarPreguntaIA();
    }

    async function enviarPreguntaIA() {
      const input = document.getElementById('chatInput');
      const chatBody = document.getElementById('chatBody');
      const pregunta = input.value.trim();

      if (!pregunta) return;

      const msgUser = document.createElement('div');
      msgUser.className = 'msg-user';
      msgUser.textContent = pregunta;
      chatBody.appendChild(msgUser);

      input.value = '';
      chatBody.scrollTop = chatBody.scrollHeight;

      const msgCargando = document.createElement('div');
      msgCargando.className = 'msg-bot';
      msgCargando.textContent = '⏳ Consultando IA...';
      chatBody.appendChild(msgCargando);
      chatBody.scrollTop = chatBody.scrollHeight;

      try {
        const resp = await fetch('/api/asistente-ia', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pregunta: pregunta })
        });

        const data = await resp.json();
        msgCargando.innerHTML = data.respuesta || 'Sin respuesta.';

        // Si hay imagen, agregarla debajo de la respuesta
        if (data.imagen) {
          const contenedorImagen = document.createElement('div');
          contenedorImagen.style.marginTop = '10px';
          contenedorImagen.style.textAlign = 'center';
          
          const img = document.createElement('img');
          img.src = data.imagen;
          img.style.maxWidth = '280px';
          img.style.maxHeight = '280px';
          img.style.borderRadius = '8px';
          img.style.cursor = 'pointer';
          img.title = 'Haz clic para ver en tamaño completo';
          
          // Al hacer clic, abrir en nueva pestaña
          img.onclick = () => window.open(data.imagen, '_blank');
          
          contenedorImagen.appendChild(img);
          msgCargando.appendChild(contenedorImagen);
        }
      } catch (err) {
        msgCargando.textContent = '❌ Error: ' + err.message;
      }

      chatBody.scrollTop = chatBody.scrollHeight;
    }
  </script>
</body>
</html>
"""

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL,
                    cedula_nit TEXT,
                    telefono TEXT)''')
    
    c.execute("SELECT id FROM clientes WHERE id = 1")
    if not c.fetchone():
        c.execute("INSERT INTO clientes (nombre, cedula_nit, telefono) VALUES ('Cliente Mostrador', '222222222', '0000000')")
    
    c.execute('''CREATE TABLE IF NOT EXISTS productos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL,
                    categoria TEXT,
                    dimensiones TEXT,
                    precio_costo REAL,
                    precio_venta REAL,
                    stock_actual INTEGER,
                    stock_minimo INTEGER)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_cliente INTEGER,
                    fecha_dia TEXT,
                    hora TEXT,
                    tipo_pago TEXT,
                    total_venta REAL)''')
    
    # Parche automático: añade la columna fecha_dia si tu BD vieja no la tenía
    try:
        c.execute("ALTER TABLE ventas ADD COLUMN fecha_dia TEXT")
    except sqlite3.OperationalError:
        pass

    # Parche automático: añade la columna hora si tu BD vieja no la tenía
    try:
        c.execute("ALTER TABLE ventas ADD COLUMN hora TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS ventas_detalle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_venta INTEGER,
                    id_producto INTEGER,
                    nombre_producto TEXT,
                    cantidad INTEGER,
                    subtotal REAL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS abonos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_cliente INTEGER,
                    monto REAL,
                    fecha TEXT)''')
    
    conn.commit()
    conn.close()

init_db()

# RUTAS
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario', '').strip()
        clave = request.form.get('clave', '').strip()

        conn = get_db()
        usuario_db = conn.execute(
            "SELECT * FROM usuarios WHERE usuario = ? AND clave = ?",
            (usuario, clave)
        ).fetchone()
        conn.close()

        if usuario_db:
            session['usuario_id'] = usuario_db['id']
            session['usuario'] = usuario_db['nombre']
            session['rol'] = usuario_db['rol']
            return redirect(url_for('index'))

        return render_template('login.html', error='Usuario o contraseña incorrectos.')

    if 'usuario' in session:
        return redirect(url_for('index'))

    return render_template('login.html', error=None)

@app.route('/')
def index():
    if 'usuario' not in session:
        return redirect(url_for('login'))

    return render_template_string(
        HTML_TEMPLATE,
        usuario=session.get('usuario', 'Usuario'),
        rol=session.get('rol', 'empleado').title()
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ENDPOINTS API REST
@app.route('/api/clientes', methods=['GET', 'POST'])
def api_clientes():
    conn = get_db()
    if request.method == 'POST':
        data = request.json
        conn.execute("INSERT INTO clientes (nombre, cedula_nit, telefono) VALUES (?, ?, ?)",
                     (data['nombre'], data.get('cedula_nit', ''), data.get('telefono', '')))
        conn.commit()
        return jsonify({"mensaje": "Cliente registrado exitosamente."}), 201
    
    clientes = conn.execute("SELECT * FROM clientes").fetchall()
    return jsonify([dict(c) for c in clientes])

@app.route('/api/productos', methods=['GET', 'POST'])
def api_productos():
    conn = get_db()
    if request.method == 'POST':
        data = request.json
        conn.execute('''INSERT INTO productos 
                        (nombre, categoria, dimensiones, precio_costo, precio_venta, stock_actual, stock_minimo) 
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (data['nombre'], data['categoria'], data.get('dimensiones', ''), 
                      data['precio_costo'], data['precio_venta'], data['stock_actual'], data['stock_minimo']))
        conn.commit()
        return jsonify({"mensaje": "Producto guardado."}), 201
    
    productos = conn.execute("SELECT * FROM productos").fetchall()
    return jsonify([dict(p) for p in productos])

@app.route('/api/ventas', methods=['GET', 'POST'])
def api_ventas():
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'POST':
        data = request.json
        fecha = datetime.now().strftime("%Y-%m-%d")
        hora = datetime.now().strftime("%H:%M:%S")
        
        total_venta = 0
        detalles = []
        for item in data['items']:
            prod = c.execute("SELECT nombre, precio_venta, stock_actual FROM productos WHERE id = ?", (item['id_producto'],)).fetchone()
            if prod:
                subtotal = prod['precio_venta'] * item['cantidad']
                total_venta += subtotal
                detalles.append((item['id_producto'], prod['nombre'], item['cantidad'], subtotal, prod['stock_actual']))
        
        c.execute("INSERT INTO ventas (id_cliente, fecha_dia, hora, tipo_pago, total_venta) VALUES (?, ?, ?, ?, ?)",
                  (data['id_cliente'], fecha, hora, data['tipo_pago'], total_venta))
        id_venta = c.lastrowid
        
        for det in detalles:
            c.execute("INSERT INTO ventas_detalle (id_venta, id_producto, nombre_producto, cantidad, subtotal) VALUES (?, ?, ?, ?, ?)",
                      (id_venta, det[0], det[1], det[2], det[3]))
            nuevo_stock = det[4] - det[2]
            c.execute("UPDATE productos SET stock_actual = ? WHERE id = ?", (nuevo_stock, det[0]))
            
        conn.commit()
        return jsonify({"mensaje": "Venta procesada con éxito", "id_venta": id_venta})

    ventas_raw = c.execute('''SELECT v.*, c.nombre as cliente 
                              FROM ventas v 
                              JOIN clientes c ON v.id_cliente = c.id 
                              ORDER BY v.id DESC LIMIT 50''').fetchall()
    ventas_list = []
    for v in ventas_raw:
        venta_dict = dict(v)
        items = c.execute("SELECT nombre_producto, cantidad FROM ventas_detalle WHERE id_venta = ?", (v['id'],)).fetchall()
        venta_dict['productos_detalle'] = ", ".join([f"{i['cantidad']}x {i['nombre_producto']}" for i in items])
        ventas_list.append(venta_dict)
        
    return jsonify(ventas_list)

@app.route('/api/historial-cliente/<int:id_cliente>')
def api_historial_cliente(id_cliente):
    conn = get_db()
    
    ventas_credito = conn.execute("SELECT * FROM ventas WHERE id_cliente = ? AND tipo_pago = 'credito' ORDER BY id ASC", (id_cliente,)).fetchall()
    abonos_raw = conn.execute("SELECT * FROM abonos WHERE id_cliente = ? ORDER BY id DESC", (id_cliente,)).fetchall()
    
    total_abonado = sum([a['monto'] for a in abonos_raw])
    
    deudas = []
    saldo_abonos = total_abonado
    
    for v in ventas_credito:
        monto_factura = v['total_venta']
        if saldo_abonos >= monto_factura:
            saldo_abonos -= monto_factura
            saldo_pendiente = 0
        else:
            saldo_pendiente = monto_factura - saldo_abonos
            saldo_abonos = 0
            
        if saldo_pendiente > 0:
            items = conn.execute("SELECT nombre_producto FROM ventas_detalle WHERE id_venta = ?", (v['id'],)).fetchall()
            detalles_str = ", ".join([i['nombre_producto'] for i in items])
            
            deudas.append({
                "id_venta": v['id'],
                "fecha": v['fecha'],
                "total_venta": monto_factura,
                "saldo_pendiente": saldo_pendiente,
                "productos_detalle": detalles_str
            })
            
    abonos = [{"id": a["id"], "monto": a["monto"], "fecha": a["fecha"], "id_venta": "Global"} for a in abonos_raw]
    
    return jsonify({"deudas": deudas, "abonos": abonos})

@app.route('/api/abonos', methods=['POST'])
def api_abonos():
    data = request.json
    conn = get_db()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute("INSERT INTO abonos (id_cliente, monto, fecha) VALUES (?, ?, ?)",
                 (data['id_cliente'], data['monto'], fecha))
    conn.commit()
    return jsonify({"mensaje": "Abono registrado correctamente."})

@app.route('/api/asistente-ia', methods=['POST'])
def api_asistente():
    data = request.json
    pregunta = data.get('pregunta', '').lower()
    conn = get_db()
    
    respuesta = ""
    imagen_url = None
    
    # Búsqueda de productos
    palabras_clave = pregunta.split()
    productos = conn.execute("SELECT * FROM productos").fetchall()
    
    # Función mejorada para buscar productos con tolerancia a errores
    def buscar_productos_similares(palabras, productos):
        coincidencias = []
        for p in productos:
            nombre_lower = p['nombre'].lower()
            categoria_lower = p['categoria'].lower() if p['categoria'] else ""
            dimensiones_lower = p['dimensiones'].lower() if p['dimensiones'] else ""
            texto_completo = f"{nombre_lower} {categoria_lower} {dimensiones_lower}"
            
            # Búsqueda exacta o parcial
            puntuacion = 0
            for palabra in palabras:
                if len(palabra) > 2:  # Solo palabras significativas
                    # Búsqueda exacta
                    if palabra in texto_completo:
                        puntuacion += 3
                    # Búsqueda similar (para tolerar errores de tipeo)
                    elif any(palabra in parte or parte in palabra for parte in texto_completo.split()):
                        puntuacion += 1
            
            if puntuacion > 0:
                coincidencias.append((p, puntuacion))
        
        # Ordenar por puntuación (relevancia)
        coincidencias.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in coincidencias]
    
    coincidencias = buscar_productos_similares(palabras_clave, productos)

    def quitar_acentos(texto):
        reemplazos = str.maketrans({
            'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
            'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u',
            'ñ': 'n', 'ü': 'u', 'ç': 'c'
        })
        return texto.lower().translate(reemplazos)

    def parsear_fecha_desde_pregunta(texto):
        texto = quitar_acentos(texto)
        meses = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
            'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
        }

        if 'hoy' in texto:
            return datetime.now().strftime('%Y-%m-%d')
        if 'ayer' in texto:
            fecha = datetime.now() - timedelta(days=1)
            return fecha.strftime('%Y-%m-%d')

        patron1 = r'(\d{1,2})\s*(?:de\s*)?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)(?:\s*(?:de|del)\s*(\d{4}))?'
        match = re.search(patron1, texto)
        if match:
            dia = int(match.group(1))
            mes = meses[match.group(2)]
            anio = int(match.group(3)) if match.group(3) else datetime.now().year
            return f'{anio:04d}-{mes:02d}-{dia:02d}'

        patron2 = r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?'
        match = re.search(patron2, texto)
        if match:
            dia = int(match.group(1))
            mes = int(match.group(2))
            anio = int(match.group(3)) if match.group(3) else datetime.now().year
            return f'{anio:04d}-{mes:02d}-{dia:02d}'

        patron3 = r'(\d{4})-(\d{1,2})-(\d{1,2})'
        match = re.search(patron3, texto)
        if match:
            anio = int(match.group(1))
            mes = int(match.group(2))
            dia = int(match.group(3))
            return f'{anio:04d}-{mes:02d}-{dia:02d}'

        return None

    def buscar_cliente_por_nombre(texto):
        clientes = conn.execute("SELECT id, nombre FROM clientes").fetchall()
        texto_limpio = quitar_acentos(texto)
        mejores = []

        for cliente in clientes:
            nombre_limpio = quitar_acentos(cliente['nombre'])
            if texto_limpio in nombre_limpio or nombre_limpio in texto_limpio:
                mejores.append(cliente)
            else:
                tokens = set(nombre_limpio.replace('&', ' ').split())
                if any(token in texto_limpio.split() for token in tokens):
                    mejores.append(cliente)

        if not mejores:
            return None
        return sorted(mejores, key=lambda x: len(x['nombre']))[0]

    def responder_consulta_ventas(texto):
        fecha = parsear_fecha_desde_pregunta(texto)
        if not fecha:
            return None

        ventas = conn.execute("""
            SELECT v.id, v.fecha_dia, v.hora, v.total_venta, v.tipo_pago, c.nombre AS cliente
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.id_cliente
            WHERE v.fecha_dia = ?
            ORDER BY v.id DESC
        """, (fecha,)).fetchall()

        if not ventas:
            return f"📅 No encontré ventas registradas para el día {fecha}."

        total_general = sum(float(v['total_venta']) for v in ventas)
        clientes = []
        productos_total = {}

        for v in ventas:
            cliente_nombre = v['cliente'] or 'Cliente no asignado'
            clientes.append(cliente_nombre)
            detalle = conn.execute(
                "SELECT nombre_producto, cantidad, subtotal FROM ventas_detalle WHERE id_venta = ?",
                (v['id'],)
            ).fetchall()
            for item in detalle:
                nombre = item['nombre_producto'].strip()
                productos_total[nombre] = productos_total.get(nombre, 0) + int(item['cantidad'])

        respuesta = f"📅 <b>Resumen del {fecha}</b><br>"
        respuesta += f"💰 Total vendido: <b>${total_general:,.0f} COP</b><br>"
        respuesta += f"🧾 Número de ventas: <b>{len(ventas)}</b><br>"
        respuesta += "👥 Clientes que compraron:<br>"
        for cliente in sorted(set(clientes)):
            respuesta += f"• {cliente}<br>"
        if productos_total:
            respuesta += "<br>📦 Productos vendidos:<br>"
            for nombre, cantidad in sorted(productos_total.items()):
                respuesta += f"• {nombre}: {cantidad} unidades<br>"
        return respuesta

    def responder_quien_compro(texto):
        fecha = parsear_fecha_desde_pregunta(texto)
        if not fecha:
            return None

        ventas = conn.execute("""
            SELECT v.id, v.total_venta, c.nombre AS cliente
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.id_cliente
            WHERE v.fecha_dia = ?
            ORDER BY v.id DESC
        """, (fecha,)).fetchall()

        if not ventas:
            return f"📅 Ninguna venta se registró el {fecha}."

        respuesta = f"🧾 <b>Compradores del {fecha}</b><br>"
        for v in ventas:
            respuesta += f"• <b>{v['cliente'] or 'Cliente no registrado'}</b> - ${float(v['total_venta']):,.0f} COP<br>"
        return respuesta

    def responder_deuda_cliente(texto):
        cliente = buscar_cliente_por_nombre(texto)
        if not cliente:
            deudas = conn.execute("""
                SELECT c.nombre, SUM(cr.saldo_pendiente) as deuda_total
                FROM creditos cr
                LEFT JOIN clientes c ON c.id = cr.id_cliente
                GROUP BY c.id, c.nombre
                ORDER BY deuda_total DESC
            """).fetchall()
            if not deudas:
                return "💳 No hay deudas registradas actualmente."
            respuesta = "💳 <b>Deudas actuales por cliente:</b><br>"
            for fila in deudas:
                respuesta += f"• {fila['nombre']}: ${float(fila['deuda_total'] or 0):,.0f} COP<br>"
            return respuesta

        deuda_total = conn.execute("SELECT COALESCE(SUM(saldo_pendiente), 0) AS total FROM creditos WHERE id_cliente = ?", (cliente['id'],)).fetchone()['total']
        respuesta = f"💳 <b>Deuda de {cliente['nombre']}</b><br>"
        respuesta += f"Total pendiente: <b>${float(deuda_total):,.0f} COP</b><br>"
        ventas_credito = conn.execute("""
            SELECT v.id, v.fecha_dia, v.total_venta, v.tipo_pago
            FROM ventas v
            WHERE v.id_cliente = ? AND v.tipo_pago = 'credito'
            ORDER BY v.fecha_dia DESC
        """, (cliente['id'],)).fetchall()
        if ventas_credito:
            respuesta += "Facturas pendientes:<br>"
            for v in ventas_credito:
                saldo = conn.execute("SELECT saldo_pendiente FROM creditos WHERE id_venta = ?", (v['id'],)).fetchone()
                saldo_valor = float(saldo['saldo_pendiente']) if saldo else 0
                respuesta += f"• Venta #{v['id']} del {v['fecha_dia']} - ${float(v['total_venta']):,.0f} COP | saldo: ${saldo_valor:,.0f} COP<br>"
        return respuesta

    texto_normal = quitar_acentos(pregunta)
    fecha_detectada = parsear_fecha_desde_pregunta(texto_normal)
    es_consulta_ventas = (
        any(frase in texto_normal for frase in ["que se hizo", "qué se hizo", "ventas del", "ventas de", "cuanto se hizo", "cuánto se hizo", "total del dia", "total del", "que vendio", "qué vendió", "cuanto vendio", "cuánto vendió"]) or
        ("venta" in texto_normal and fecha_detectada is not None)
    )
    es_consulta_quien = any(frase in texto_normal for frase in ["quien compro", "quién compró", "quien compro el", "quien vendio", "quién vendió", "compradores del", "quien ahorro", "quien pago"])
    es_consulta_deuda = any(frase in texto_normal for frase in ["cuanto debe", "cuánto debe", "deuda de", "debe", "saldo pendiente", "cuanto le deben", "cuánto le deben"]) or "cartera" in texto_normal

    if es_consulta_ventas and fecha_detectada:
        respuesta = responder_consulta_ventas(texto_normal)
    elif es_consulta_quien and fecha_detectada:
        respuesta = responder_quien_compro(texto_normal)
    elif es_consulta_deuda:
        respuesta = responder_deuda_cliente(texto_normal)
    elif any(palabra in pregunta for palabra in ["precio", "cuanto vale", "costo", "valor"]):
        if coincidencias:
            respuesta = "📦 <b>Productos encontrados:</b><br>"
            for p in coincidencias[:5]:
                respuesta += f"• <b>{p['nombre'].strip()}</b><br>"
                respuesta += f"  💰 Precio: ${p['precio_venta']:,.0f} COP<br>"
                respuesta += f"  📊 Stock: {p['stock_actual']} unidades<br>"
                if p['dimensiones']:
                    respuesta += f"  📐 Dimensiones: {p['dimensiones']}<br>"
                respuesta += f"  🏷️ Categoría: {p['categoria']}<br><br>"
        else:
            respuesta = "❌ No encontré productos con ese nombre. Productos disponibles:<br>"
            for p in productos:
                respuesta += f"• <b>{p['nombre'].strip()}</b> - ${p['precio_venta']:,.0f} COP<br>"
    
    elif any(palabra in pregunta for palabra in ["stock", "existencias", "hay de"]):
        if coincidencias:
            respuesta = "📦 <b>Existencias:</b><br>"
            for p in coincidencias[:5]:
                estado = "✅ Disponible" if p['stock_actual'] > 0 else "❌ Agotado"
                respuesta += f"• <b>{p['nombre'].strip()}</b><br>"
                respuesta += f"  📊 Stock: {p['stock_actual']} unidades {estado}<br><br>"
        else:
            respuesta = "❌ No encontré ese producto. Productos disponibles:<br>"
            for p in productos:
                respuesta += f"• <b>{p['nombre'].strip()}</b> ({p['stock_actual']} unidades)<br>"
    
    elif any(palabra in pregunta for palabra in ["categoria", "tipo", "que hay de"]):
        categorias = set(p['categoria'] for p in productos if p['categoria'])
        respuesta = "🏷️ <b>Categorías disponibles:</b><br>"
        for cat in sorted(categorias):
            count = sum(1 for p in productos if p['categoria'] == cat)
            respuesta += f"• <b>{cat}</b> ({count} productos)<br>"
        respuesta += "<br>Pregunta por una categoría específica para ver sus productos."
    
    elif any(palabra in pregunta for palabra in ["cartera", "credito", "pago"]):
        respuesta = "💳 <b>Gestión de Créditos:</b><br>"
        respuesta += "1. Ve a la pestaña '💳 Créditos / Estado de Cuenta'<br>"
        respuesta += "2. Selecciona el cliente<br>"
        respuesta += "3. Haz clic en 'Consultar Cuenta'<br>"
        respuesta += "4. Verás todas sus facturas pendientes y deudas<br>"
        respuesta += "5. Puedes registrar abonos desde esa misma pantalla"
    
    elif any(palabra in pregunta for palabra in ["ayuda", "como", "que puedo hacer", "que hago"]):
        respuesta = "🤖 <b>Asistente Virtual - Comandos:</b><br>"
        respuesta += "• Pregunta por <b>precio</b> de un producto<br>"
        respuesta += "• Pregunta por <b>stock</b> o existencias<br>"
        respuesta += "• Pregunta por <b>categorías</b><br>"
        respuesta += "• Pregunta sobre <b>cartera</b> o créditos<br>"
        respuesta += "• Pregunta por <b>nombre del producto</b><br>"
        respuesta += "• Pregunta sobre <b>dimensiones</b><br>"
        respuesta += "• Pregunta <b>qué es</b> un material o producto"
    
    elif any(palabra in pregunta for palabra in ["todos", "lista", "que hay"]):
        respuesta = "📦 <b>Todos los productos disponibles:</b><br>"
        for p in productos:
            respuesta += f"• <b>{p['nombre'].strip()}</b> - ${p['precio_venta']:,.0f} COP (Stock: {p['stock_actual']})<br>"
    
    # Si es una pregunta sobre QUÉ ES algo, usar OpenAI para explicar
    elif any(palabra in pregunta for palabra in ["que es", "qué es", "que son", "qué son", "como es", "cómo es", "para que sirve", "para qué sirve"]):
        try:
            # Usar OpenAI para explicar qué es
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "Eres un asistente para una ferretería. Explica de forma clara y breve (máximo 3 líneas) qué es un material o producto, para qué sirve, y si podría estar en una ferretería. Sé práctico y útil para empleados."},
                    {"role": "user", "content": f"Explica qué es: {pregunta}"}
                ],
                "temperature": 0.7,
                "max_tokens": 300
            }
            
            response = requests.post(OPENAI_URL, headers=OPENAI_HEADERS, json=payload, timeout=10)
            response.raise_for_status()
            resultado = response.json()
            explicacion = resultado['choices'][0]['message']['content']
            
            # Buscar en BD si existe algo similar
            if coincidencias:
                respuesta = f"📚 <b>¿Qué es?</b><br>{explicacion}<br><br>"
                respuesta += "✅ <b>Lo tenemos en la ferretería:</b><br>"
                p = coincidencias[0]
                respuesta += f"• <b>{p['nombre'].strip()}</b> - ${p['precio_venta']:,.0f} COP (Stock: {p['stock_actual']})"
            else:
                respuesta = f"📚 <b>¿Qué es?</b><br>{explicacion}<br><br>"
                respuesta += "❌ <b>Posible coincidencia en inventario:</b><br>No encontramos exactamente eso, pero tenemos estos productos similares:<br>"
                for p in productos[:3]:
                    respuesta += f"• {p['nombre'].strip()} (${p['precio_venta']:,.0f} COP)<br>"
            
            # Intentar generar imagen con DALL-E
            try:
                payload_imagen = {
                    "model": "dall-e-3",
                    "prompt": f"Professional photo of {pregunta}, product photography style, white background, clear and detailed, realistic",
                    "size": "1024x1024",
                    "quality": "standard",
                    "n": 1
                }
                imagen_response = requests.post("https://api.openai.com/v1/images/generations", headers=OPENAI_HEADERS, json=payload_imagen, timeout=30)
                if imagen_response.status_code == 200:
                    imagen_data = imagen_response.json()
                    imagen_url = imagen_data['data'][0]['url']
            except:
                # Si DALL-E falla, continuar sin imagen
                pass
                
        except Exception as e:
            respuesta = f"❌ Error al consultar IA: {str(e)[:100]}"
    
    else:
        if coincidencias:
            respuesta = "📦 <b>Producto encontrado:</b><br>"
            p = coincidencias[0]
            respuesta += f"<b>{p['nombre'].strip()}</b><br>"
            respuesta += f"💰 Precio: ${p['precio_venta']:,.0f} COP<br>"
            respuesta += f"📊 Stock: {p['stock_actual']} unidades<br>"
            if p['dimensiones']:
                respuesta += f"📐 Dimensiones: {p['dimensiones']}<br>"
            respuesta += f"🏷️ Categoría: {p['categoria']}<br>"
            respuesta += f"💵 Precio de costo: ${p['precio_costo']:,.0f} COP"
        else:
            respuesta = "🤔 Producto no encontrado. Productos disponibles:<br><br>"
            for p in productos:
                respuesta += f"• <b>{p['nombre'].strip()}</b> (${p['precio_venta']:,.0f} COP)<br>"
            respuesta += "<br>💡 Intenta preguntar '¿Qué es?' sobre un producto para que te lo explique en detalle"
    
    resultado = {"respuesta": respuesta}
    if imagen_url:
        resultado["imagen"] = imagen_url
    
    return jsonify(resultado)

if __name__ == '__main__':
    app.run(debug=True, port=5000)