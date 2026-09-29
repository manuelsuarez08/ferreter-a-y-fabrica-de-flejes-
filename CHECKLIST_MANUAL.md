# Checklist manual — verificación en navegador

El harness de navegador headless de este entorno resultó no confiable (no
ejecutaba JavaScript), así que la verificación de la interfaz se hizo por
revisión estática y pruebas de servidor. Lo que sigue hay que validarlo a mano.

Servidor: `.\.venv\Scripts\python.exe -c "from ferreteria.app_factory import create_app; create_app().run()"`
Usuario: `admin` / `admin123`

---

## 1. Flejes a medida eliminado (y fábrica por categoría)

- [ ] Pestaña **Ventas**: la tarjeta amarilla "🧰 Flejes a medida / orden a fábrica" ya no aparece.
- [ ] Vender un producto de categoría **Fleje** → Pestaña **Fábrica de Flejes**: aparece una orden nueva.
- [ ] Vender un producto de categoría **Tubería** → NO aparece orden en fábrica.
- [ ] Verificar en Inventario que la categoría del producto sea exactamente `Fleje` o `Flejes`
      (si dice "Flejes tubulares" o "No-fleje", **no** debe generar orden).
- [ ] La pestaña **Fábrica de Flejes** sigue cargando la cola de órdenes.

## 2. Pedidos manuales eliminado (cola de despacho intacta)

- [ ] Pestaña **Pedidos**: aparece directamente "🚚 Cola de Despacho", sin sub-selector.
- [ ] No existe ningún botón "➕ Nuevo Pedido" ni la tabla de pedidos manuales.
- [ ] La cola de despacho sigue funcionando: filtrar por estado, registrar entrega.

## 3. Modal de factura en una pantalla

- [ ] Facturas → **Ver**: el modal abre con dos columnas (tirilla izquierda, campos derecha).
- [ ] Con una factura larga, cada columna hace scroll propio y el pie (Cerrar / Imprimir / PDF) queda visible.
- [ ] **Imprimir recibo** y **Descargar PDF** siguen saliendo bien (la tirilla se clona, no debería verse afectada).
- [ ] En una ventana angosta (<992 px) se apila en una columna sin romper.

## 4. Ubicación por defecto (Samaná / Caldas)

- [ ] Clientes → **Registrar Nuevo Cliente**: los campos "Cód. Municipio" y "Cód. Departamento" ya vienen con `17665` y `17`.
- [ ] Guardar un cliente con esos valores → al volver a editarlo, conserva 17665 / 17.
- [ ] Factura de un cliente nuevo: el XML lleva el municipio 17665 y departamento 17.

## 5. Consolidado mensual y tirilla de abono

- [ ] Créditos → elegir un cliente → **🧾 Consolidated del mes**: imprime la hoja de vida con sus compras del mes, total facturado, abonado y saldo actual.
- [ ] **Probar en un mes sin compras**: debe imprimir "Sin compras en el mes", con el saldo global correcto.
- [ ] Créditos → registrar un abono → **debe imprimir la tirilla automáticamente**, sin depender del botón "Impresión automática: NO".

## 6. Producto rápido en cotizaciones

- [ ] Cotizaciones → dentro de una cotización, botón **+ Producto** junto al buscador.
- [ ] Crear un producto ahí: aparece el alert de éxito, el modal se cierra, **la cotización sigue abierta** y el producto queda seleccionado.
- [ ] Poner cantidad → **Agregar**: el renglón entra en la cotización.
- [ ] El producto nuevo aparece en Inventario de Productos.

## 7. Recibo de entrega de alquiler

- [ ] Alquiler → registrar un alquiler (equipo, cliente, fechas).
- [ ] En "Alquileres activos": botón **🧾 Recibo** → imprime con fecha/hora de entrega, cliente, equipos con su estado de salida, fecha pactada de devolución y espacio para dos firmas.
- [ ] El botón "Devolver" sigue funcionando.

## 8. Tirilla de cierre legible

- [ ] Administración → **Cierre Diario** → imprimir: letra grande (16 px), **todo en negrita**, tablas con bordes y espacio para firma.
- [ ] Imprimir en la térmica real y confirmar que los números se leen sin esfuerzo.

## 9. Los dos bugs corregidos

- [ ] Ventas → Historial → **Ver Hoy** *después de las 7:00 p. m.*: debe mostrar las ventas de HOY, no las de mañana.
- [ ] Facturas → **Ver**: el modal abre (antes no abría por el id `fac-metodo-pago` inexistente).
- [ ] Créditos → filtro de abonos por Día / Mes / Año: el saldo que reporta no cambia al filtrar.

---

## Notas de riesgo

- **La orden a fábrica cambió de criterio.** Si algún producto antiguo usa una
  categoría tipo "Fleje 3/8", ya no generará orden (ahora debe decir `Fleje`).
  Conviene revisar el catálogo una vez.
- **Las funciones de pedidos manuales se borraron del JS.** Si algún rol usaba
  ese flujo, deja de existir por diseño (queda absorbedo por Ventas).
- **El POST de productos ahora devuelve `id` y `nombre`** además del mensaje.
  Si algo consumía esa respuesta, son campos extra, no un cambio incompatible.
