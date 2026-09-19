@echo off
REM ============================================================================
REM  ABRIR LA FERRETERIA  (doble clic en este archivo)
REM
REM  Levanta la aplicacion en esta computadora. Deja esta ventana ABIERTA
REM  mientras trabajas: si la cierras, la aplicacion se apaga.
REM
REM  Cuando arranque, veras dos direcciones:
REM    - En esta PC     : http://127.0.0.1:5000
REM    - Desde el celular: http://IP-DE-TU-PC:5000  (mismo Wi-Fi)
REM ============================================================================
setlocal
cd /d "%~dp0"

REM El interprete de Python del proyecto (.venv).
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: no encontre el interprete en .venv\Scripts\python.exe
  echo Revisa que la carpeta .venv exista junto a este archivo.
  pause
  exit /b 1
)

REM Puerto de la aplicacion (se puede cambiar definiendo PORT antes).
if not defined PORT set "PORT=5000"

echo ============================================================
echo   FERRETERIA Y FABRICA DE FLEJES
echo ============================================================
echo.
echo   Iniciando la aplicacion...
echo.

REM Muestra la IP local para entrar desde el celular (misma red Wi-Fi).
echo   En ESTA computadora abre:   http://127.0.0.1:%PORT%
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=1" %%b in ("%%a") do echo   Desde el CELULAR (mismo Wi-Fi): http://%%b:%PORT%
)
echo.
echo   ^>^> DEJA ESTA VENTANA ABIERTA mientras uses la aplicacion. ^<^<
echo   Para cerrar la aplicacion: cierra esta ventana o pulsa Ctrl+C.
echo.

"%PY%" app.py
echo.
echo La aplicacion se detuvo.
pause
