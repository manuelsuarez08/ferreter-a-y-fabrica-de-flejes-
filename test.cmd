@echo off
REM Lanzador de pruebas de la ferreteria. No requiere `npm` ni que `node` este
REM en el PATH: localiza el runtime por su cuenta (ver mas abajo).
REM   test.cmd            -> flujo completo (levanta la app si hace falta)
REM   test.cmd despacho   -> solo el flujo de despacho (requiere la app arriba)
REM   test.cmd reset      -> deja el pedido de prueba en "pendiente_preparar"
REM enabledelayedexpansion es imprescindible: sin el, `set "CODIGO=%ERRORLEVEL%"`
REM DENTRO de un bloque ( ) se expande antes de ejecutarse, asi que CODIGO se
REM queda con el valor viejo y las tareas devuelven 0 aunque fallen.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM `reset` NO necesita Node, asi que se atiende antes de resolverlo.
if /i "%~1"=="reset" (
  ".venv\Scripts\python.exe" _reset_venta_prueba.py
  set "CODIGO=!ERRORLEVEL!"
  goto :fin
)

REM Las tareas con Node si lo necesitan. No basta con `where node`: en esta
REM maquina conviven DOS node distintos (el Node.js instalado y un shim de VS
REM Code), y el que gane depende del PATH del shell que lanzo el .cmd. Para que
REM el resultado no cambie segun desde donde se ejecute, se elige a mano y
REM SIEMPRE con el Node.js real primero; el shim queda solo como ultimo recurso.
set "NODE="
if exist "%ProgramFiles%\nodejs\node.exe" set "NODE=%ProgramFiles%\nodejs\node.exe"
if not defined NODE if exist "%ProgramFiles(x86)%\nodejs\node.exe" set "NODE=%ProgramFiles(x86)%\nodejs\node.exe"
if not defined NODE if exist "%LOCALAPPDATA%\Programs\nodejs\node.exe" set "NODE=%LOCALAPPDATA%\Programs\nodejs\node.exe"
if not defined NODE if exist "%USERPROFILE%\.codegpt\bin\node.cmd" set "NODE=%USERPROFILE%\.codegpt\bin\node.cmd"
if not defined NODE (
  for /f "delims=" %%i in ('where node.exe 2^>nul') do if not defined NODE set "NODE=%%i"
)
if not defined NODE (
  echo ERROR: no encontre Node.js. Instalalo desde https://nodejs.org
  endlocal & exit /b 1
)

REM La app y las pruebas hablan por este puerto. Se puede sobreescribir con
REM PORT si el 5000 esta ocupado por otra cosa.
if not defined PORT set "PORT=5000"

if /i "%~1"=="despacho" (
  "%NODE%" _qa_flujo_despacho_runner.mjs
  set "CODIGO=!ERRORLEVEL!"
  goto :fin
)

"%NODE%" run-tests.mjs
set "CODIGO=%ERRORLEVEL%"

:fin
REM OJO: el codigo se captura ANTES de endlocal; leer %ERRORLEVEL% despues
REM de endlocal o tras un `goto` devuelve un valor equivocado (0 falso).
endlocal & exit /b %CODIGO%
