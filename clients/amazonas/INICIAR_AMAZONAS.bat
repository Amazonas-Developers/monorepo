@echo off
REM ============================================================
REM  INICIAR_AMAZONAS - Arranca el sistema completo:
REM    1) SERVIDOR de inferencia IA        (ws://0.0.0.0:9000/ws)
REM    2) DASHBOARD web de analitica       (http://localhost:9000/dashboard)
REM    3) CLIENTE Amazonas View            (ventana de escritorio)
REM  El dashboard lo sirve el MISMO proceso del servidor (no hay
REM  proceso aparte): se abre en el navegador cuando el servidor
REM  esta listo. Se ejecuta como Administrador (auto-elevacion UAC,
REM  igual que init.bat: la captura de ventanas lo requiere).
REM ============================================================

REM -- Auto-elevacion: relanzar como Administrador si no lo es --
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permisos de Administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

setlocal
cd /d "%~dp0"
title AMAZONAS VIEW - Sistema completo [Administrador]

REM UTF-8: los print con acentos/emoji no deben crashear la consola.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "CLIENTE=%~dp0"
for %%I in ("%~dp0..\..") do set "ELDE=%%~fI"
set "SERVIDOR=%ELDE%\server"
set "PUERTO=9000"

REM El cliente debe hablar con el servidor que arranca ESTE .bat, no con
REM el remoto que trae por defecto el codigo: si no, el servidor local se
REM queda sin usar y el dashboard sale vacio. Para apuntar a otro
REM servidor, cambia esta linea (admite "host:puerto" o la URL completa).
set "AMAZONAS_SERVER_WS=ws://127.0.0.1:%PUERTO%/ws"
set "LOG=%~dp0_amazonas_log.txt"
echo ===== INICIAR AMAZONAS %date% %time% ===== > "%LOG%"

echo ============================================================
echo  AMAZONAS VIEW - Deteccion de personas + genero/edad
echo ============================================================
echo.

REM -- Verificaciones ----------------------------------------------
if not exist "%SERVIDOR%\venv\Scripts\python.exe" (
    echo [ERROR] Falta el venv del SERVIDOR.
    echo         Ejecuta una vez: "server\SETUP_SERVIDOR.bat"
    echo [ERROR] venv servidor ausente >> "%LOG%"
    pause
    exit /b 1
)

REM Python del cliente: su venv si existe; si no, el del sistema
REM (mismo comportamiento que init.bat).
set "PY_CLIENTE=python"
if exist "%CLIENTE%venv\Scripts\python.exe" set "PY_CLIENTE=%CLIENTE%venv\Scripts\python.exe"

REM -- 1) Cerrar instancias previas (puerto 9000 + procesos) --------
echo [1/4] Cerrando instancias previas...
echo [1/4] limpieza previa >> "%LOG%"
powershell -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort %PUERTO% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'iniciar_servidor_headless|amazonas\\src\\main\.py|capture_woker' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul

REM -- 2) SERVIDOR de inferencia (puerto 9000) ----------------------
echo [2/4] Iniciando SERVIDOR IA (puerto %PUERTO%)...
echo [2/4] start servidor headless >> "%LOG%"
start "SERVIDOR IA - AMAZONAS (%PUERTO%)" /D "%SERVIDOR%" "%SERVIDOR%\venv\Scripts\python.exe" iniciar_servidor_headless.py %PUERTO%

echo       Esperando a que el servidor acepte conexiones (hasta 180s)...
powershell -NoProfile -Command "for($i=0;$i -lt 180;$i++){ $c=New-Object Net.Sockets.TcpClient; try{ $c.Connect('127.0.0.1',%PUERTO%); $c.Close(); exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] El servidor no respondio en el puerto %PUERTO%.
    echo         Mira la ventana "SERVIDOR IA - AMAZONAS" para ver el error.
    echo [ERROR] timeout esperando puerto %PUERTO% >> "%LOG%"
    pause
    exit /b 1
)
echo       Servidor LISTO en ws://localhost:%PUERTO%/ws
echo [2/4] servidor listo >> "%LOG%"
echo.

REM -- 3) DASHBOARD web (lo sirve el propio servidor) ---------------
echo [3/4] Abriendo DASHBOARD (http://localhost:%PUERTO%/dashboard)...
echo [3/4] abrir dashboard >> "%LOG%"
start "" http://localhost:%PUERTO%/dashboard

REM -- 4) CLIENTE Amazonas View ------------------------------------
echo [4/4] Iniciando CLIENTE Amazonas View...
echo [4/4] start cliente >> "%LOG%"
start "CLIENTE - AMAZONAS VIEW" /D "%CLIENTE%." "%PY_CLIENTE%" src\main.py

echo.
echo ============================================================
echo  TODO LISTO
echo    Servidor  : %AMAZONAS_SERVER_WS%  (al que se conecta el cliente)
echo    Dashboard : http://localhost:%PUERTO%/dashboard
echo    Cliente   : ventana "Amazonas View"
echo    Capturas  : %CLIENTE%capture
echo.
echo  Log de arranque: %LOG%
echo ============================================================
echo ===== FIN OK %time% ===== >> "%LOG%"
timeout /t 20 >nul
exit /b 0
