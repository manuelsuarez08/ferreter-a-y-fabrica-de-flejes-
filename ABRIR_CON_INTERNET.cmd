@echo off
REM ============================================================================
REM  ABRIR LA FERRETERIA CON ACCESO DESDE INTERNET  (doble clic aqui)
REM
REM  Hace DOS cosas a la vez:
REM    1. Levanta la aplicacion en esta computadora.
REM    2. Abre un "tunel" de Cloudflare para poder entrar desde el celular
REM       o desde cualquier lugar (con datos moviles).
REM
REM  AL FINAL te mostrara un enlace verde tipo:
REM      https://algo-random.trycloudflare.com
REM  Ese es el enlace para entrar desde afuera. Copialo y usalo en el celular.
REM
REM  IMPORTANTE:
REM  - DEJA ESTA VENTANA ABIERTA mientras necesites el acceso remoto.
REM  - El enlace CAMBIA cada vez que arrancas. Copia el nuevo cada vez.
REM  - Si cierras la ventana, se apaga todo (app y tunel).
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "TUNEL=herramientas\cloudflared.exe"
if not defined PORT set "PORT=5000"

if not exist "%PY%" (
  echo ERROR: no encontre %PY%
  pause
  exit /b 1
)
if not exist "%TUNEL%" (
  echo ERROR: no encontre %TUNEL%
  echo Falta el programa del tunel. Avisa a tu asistente.
  pause
  exit /b 1
)

echo ============================================================
echo   FERRETERIA Y FABRICA DE FLEJES
echo ============================================================
echo   Iniciando la aplicacion...

REM 1) Arranca la aplicacion en segundo plano.
start "Ferreteria_App" /min "%PY%" app.py

REM Espera a que la app responda.
echo   Esperando a que la aplicacion arranque...
timeout /t 6 /nobreak >nul

echo.
echo   ============================================================
echo     ENLACES DE ACCESO
echo   ============================================================
echo     En ESTA computadora:  http://127.0.0.1:%PORT%
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=1" %%b in ("%%a") do echo     En el CELULAR (mismo Wi-Fi): http://%%b:%PORT%
)
echo.
echo   Abriendo tunel para acceso desde INTERNET...
echo   (Mira abajo el enlace https://...trycloudflare.com que aparezca)
echo   ============================================================
echo.

REM 2) Arranca el tunel en PRIMER PLANO (aqui se ven sus mensajes y el enlace).
"%TUNEL%" tunnel --url http://127.0.0.1:%PORT% --no-autoupdate

echo.
echo El tunel se detuvo.
pause
