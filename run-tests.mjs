// Prueba completa del flujo de despachos:
//   1) asegura que la app este corriendo (la levanta si hace falta)
//   2) resetea el pedido de prueba a "pendiente_preparar"
//   3) levanta Chromium y corre _qa_flujo_despacho.mjs con los dos roles
//
// Se ejecuta desde el propio node (sin depender de que `npm` este en el PATH).
// Si el servidor NO estaba levantado, este script lo apaga al terminar; si ya
// estaba corriendo, lo deja como estaba.
//
//   npm run test:full
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, openSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';

const PY = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python';
const TEST_DETECCION = process.platform === 'win32'
  ? 'tests\\test_deteccion_puerto.py'
  : 'tests/test_deteccion_puerto.py';
const PUERTO = process.env.PORT || '5000';
const URL_LOGIN = `http://127.0.0.1:${PUERTO}/login`;
const URL_TITULO = `http://127.0.0.1:${PUERTO}/`;
let servidorPropio = null; // proceso Flask que levanto este script
async function servidorResponde() {
  try {
    const res = await fetch(URL_LOGIN, { redirect: 'manual', signal: AbortSignal.timeout(3000) });
    return res.status < 500;
  } catch {
    return false;
  }
}

// Distingue NUESTRA app de cualquier otra que ocupe el puerto: el login de la
// ferreteria trae este titulo. Evita un falso "ya esta corriendo" que haria
// pasar la prueba contra una app ajena.
async function esNuestraApp() {
  try {
    const res = await fetch(URL_LOGIN, { signal: AbortSignal.timeout(3000) });
    const html = await res.text();
    return /Sistema Ferreter/i.test(html);
  } catch {
    return false;
  }
}

// Playwright sin navegador instalado da un error larguisimo y confuso; aqui se
// detecta antes y se dice exactamente que comando ejecutar.
async function verificarChromium() {
  try {
    const { chromium } = await import('playwright');
    const navegador = await chromium.launch({ headless: true });
    await navegador.close();
  } catch (err) {
    throw new Error(
      'no pude abrir Chromium con Playwright: ' +
        err.message +
        '\n       Instala el navegador con: npx playwright install chromium',
    );
  }
}

async function asegurarServidor() {
  if (await servidorResponde()) {
    if (!(await esNuestraApp())) {
      throw new Error(
        `el puerto ${PUERTO} esta ocupado por OTRA app (responde ${URL_LOGIN} pero no es la ferreteria). ` +
          'Cierrala, o define PORT con otro valor.',
      );
    }
    console.log(`== App ya corriendo en 127.0.0.1:${PUERTO} (la dejo como estaba) ==`);
    return;
  }
  if (!existsSync(PY)) {
    console.log(`AVISO: app caida y no encontre ${PY}; no puedo levantarla.`);
    console.log('       Levantala a mano con: .\\.venv\\Scripts\\python.exe app.py');
    // OJO: no usar process.exit() (mata la tuberia de los hijos); ver nota al
    // final del archivo. Lanzar deja que el finally haga la limpieza.
    throw new Error(`no encontre el interprete del venv (${PY})`);
  }
  console.log('== App caida: levantando .venv\\Scripts\\python.exe app.py ==');
  const out = openSync('_server_out.log', 'a');
  const err = openSync('_server_err.log', 'a');
  // `detached: true` es OBLIGATORIO aqui: si el hijo queda en el grupo de
  // procesos del shell, Windows lo mata en cuanto el terminal corta la tarea
  // (p.ej. a los 120s) y Chromium recibe ERR_CONNECTION_REFUSED aunque el
  // servidor si hubiera arrancado. Desacoplado, sobrevive hasta que este
  // script lo apaga en detenerServidor().
  servidorPropio = spawn(PY, ['app.py'], { stdio: ['ignore', out, err], detached: true });
  servidorPropio.unref();

  for (let intento = 1; intento <= 20; intento++) {
    await delay(700);
    if (await servidorResponde()) {
      console.log(`   servidor listo (intento ${intento})`);
      return;
    }
  }
  detenerServidor();
  throw new Error('el servidor no respondio tras 14s; revisa _server_err.log');
}

function detenerServidor() {
  if (!servidorPropio) return;
  console.log('== Apagando el servidor que levanto esta prueba ==');
  const { pid } = servidorPropio;
  servidorPropio = null;
  try {
    // OJO: `taskkill` NO existe en el PATH de esta maquina, hay que llamarlo
    // por su ruta completa. Si igual falla, queda process.kill como respaldo.
    if (process.platform === 'win32') {
      const taskkill = `${process.env.SystemRoot}\\System32\\taskkill.exe`;
      execFileSync(taskkill, ['/pid', String(pid), '/t', '/f'], { stdio: 'ignore' });
    } else {
      process.kill(-pid, 'SIGTERM');
    }
  } catch {
    try {
      process.kill(pid);
    } catch {
      /* el proceso ya habia terminado */
    }
  }
}

let fallo = false;
try {
  // Se comprueba antes de levantar nada: si falta el navegador no tiene sentido
  // arrancar la app para nada.
  await verificarChromium();

  // Fija el contrato de esNuestraApp() (titulo del login). Si falla, alguien
  // cambio la plantilla o el patron y la deteccion de puerto quedaria ciega.
  console.log('== Deteccion de puerto (es la ferreteria?) ==');
  try {
    execFileSync(PY, [TEST_DETECCION], { stdio: 'inherit' });
  } catch {
    throw new Error(`fallo ${TEST_DETECCION} (ver salida arriba)`);
  }

  await asegurarServidor();

  console.log('\n== Reset del pedido de prueba ==');
  try {
    execFileSync(PY, ['_reset_venta_prueba.py'], { stdio: 'inherit' });
  } catch (err) {
    throw new Error(
      'no pude resetear el pedido de prueba (_reset_venta_prueba.py): ' + err.message,
    );
  }

  console.log('\n== Flujo de despacho (bodega -> motocarguero) ==');
  await import('./_qa_flujo_despacho_runner.mjs');
} catch (err) {
  fallo = true;
  console.error('FALLO: ' + err.message);
} finally {
  detenerServidor();
}

// OJO: NO usar process.exit() aqui. En Windows, exit() destruye el event loop y
// con el la tuberia del proceso hijo, que muere aunque se haya lanzado con
// detached:true + unref(). Eso tumbaba el servidor a mitad del flujo y Chromium
// recibia ERR_CONNECTION_REFUSED. Con exitCode el proceso termina solo, de forma
// ordenada, una vez que no queda trabajo pendiente.
process.exitCode = fallo ? 1 : 0;
