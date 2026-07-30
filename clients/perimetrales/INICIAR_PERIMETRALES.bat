@echo off
REM ============================================================
REM  INICIAR_PERIMETRALES - Arranca el sistema perimetral:
REM    1) SERVIDOR de inferencia        (ws://0.0.0.0:9000/ws)
REM    2) PANEL web                     (http://localhost:5333)
REM    3) CLIENTE clients\perimetrales     (ventana de escritorio)
REM
REM  El panel lo sirve el propio servidor (no hay proceso aparte) e
REM  incluye la gestion de personas de interes en /gestion, que antes
REM  vivia suelta en el puerto 8090.
REM ============================================================

REM -- Auto-elevacion: la captura de ventanas requiere Administrador --
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permisos de Administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

setlocal
cd /d "%~dp0"
title PERIMETRALES - Sistema completo [Administrador]

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "CLIENTE=%~dp0"
for %%I in ("%~dp0..\..") do set "ELDE=%%~fI"
set "SERVIDOR=%ELDE%\server"
set "PUERTO=9000"
set "PUERTO_PANEL=5333"

REM El panel lee las capturas que escribe ESTE cliente.
set "VIGILANTE_SCREENSHOTS=%CLIENTE%screenshots"
REM El cliente habla con el servidor que arranca este .bat.
set "server_ws_url=ws://127.0.0.1:%PUERTO%/ws"

echo ============================================================
echo  PERIMETRALES - Personas y vehiculos
echo ============================================================
echo.

if not exist "%SERVIDOR%\venv\Scripts\python.exe" (
    echo [ERROR] Falta el venv del SERVIDOR: %SERVIDOR%
    pause
    exit /b 1
)

set "PY_CLIENTE=python"
if exist "%CLIENTE%venv\Scripts\python.exe" set "PY_CLIENTE=%CLIENTE%venv\Scripts\python.exe"

echo [1/4] Cerrando instancias previas...
powershell -NoProfile -Command "foreach($p in @(%PUERTO%,%PUERTO_PANEL%)){ Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }"
timeout /t 3 /nobreak >nul

echo [2/4] Iniciando SERVIDOR IA (puerto %PUERTO%)...
start "SERVIDOR IA - PERIMETRALES (%PUERTO%)" /D "%SERVIDOR%" "%SERVIDOR%\venv\Scripts\python.exe" iniciar_servidor_headless.py %PUERTO%

echo       Esperando a que acepte conexiones (hasta 180s)...
powershell -NoProfile -Command "for($i=0;$i -lt 180;$i++){ $c=New-Object Net.Sockets.TcpClient; try{ $c.Connect('127.0.0.1',%PUERTO%); $c.Close(); exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if %errorlevel% neq 0 (
    echo [ERROR] El servidor no respondio en el puerto %PUERTO%.
    pause
    exit /b 1
)
echo       Servidor LISTO.
echo.

echo [3/4] Abriendo PANEL (http://localhost:%PUERTO_PANEL%)...
start "" http://localhost:%PUERTO_PANEL%

echo [4/4] Iniciando CLIENTE clients\perimetrales...
start "CLIENTE - PERIMETRALES" /D "%CLIENTE%." "%PY_CLIENTE%" src\main.py

echo.
echo ============================================================
echo  TODO LISTO
echo    Servidor  : %server_ws_url%
echo    Panel     : http://localhost:%PUERTO_PANEL%
echo    Personas  : http://localhost:%PUERTO_PANEL%/gestion/
echo    Capturas  : %VIGILANTE_SCREENSHOTS%
echo ============================================================
timeout /t 20 >nul
exit /b 0
