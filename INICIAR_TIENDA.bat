@echo off
REM ============================================================
REM  INICIAR_TIENDA - Arranca TODO el proyecto de tienda (ELDE):
REM    1) SERVIDOR de inferencia headless  (ws://0.0.0.0:9000/ws)
REM    2) DASHBOARD web de visitantes     (http://localhost:9000/dashboard)
REM    3) CLIENTE de tienda (clients\tienda)   (ventana de escritorio)
REM  Unico lanzador del proyecto. Se ejecuta como Administrador
REM  (auto-elevacion UAC).
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
title INICIAR TIENDA - ELDE [Administrador]

REM UTF-8: los print con acentos/emoji no deben crashear la consola.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "SERVIDOR=%~dp0server"
set "CLIENTE=%~dp0clients\tienda"
set "PUERTO=9000"
set "LOG=%~dp0_tienda_log.txt"
echo ===== INICIAR TIENDA %date% %time% ===== > "%LOG%"

echo ============================================================
echo  INICIAR TIENDA - Analitica de supermercado
echo ============================================================
echo.

REM -- Verificaciones de venv (se crean con los SETUP_*.bat) --------
if not exist "%SERVIDOR%\venv\Scripts\python.exe" (
    echo [ERROR] Falta el venv del SERVIDOR.
    echo         Ejecuta una vez: "server\SETUP_SERVIDOR.bat"
    echo [ERROR] venv servidor ausente >> "%LOG%"
    pause
    exit /b 1
)
if not exist "%CLIENTE%\venv\Scripts\python.exe" (
    echo [ERROR] Falta el venv del CLIENTE de tienda.
    echo         Ejecuta una vez: "clients\tienda\SETUP_CLIENTE.bat"
    echo [ERROR] venv cliente ausente >> "%LOG%"
    pause
    exit /b 1
)

REM -- 1) Cerrar instancias previas (puerto 9000 + procesos) --------
echo [1/4] Cerrando instancias previas...
echo [1/4] limpieza previa >> "%LOG%"
powershell -NoProfile -Command "$p=@(%PUERTO%); Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $p -contains $_.LocalPort } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'iniciar_servidor_headless|webapp\\app\.py|src\\main\.py|capture_woker' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul

REM -- 2) SERVIDOR de inferencia headless (puerto 9000) -------------
echo [2/4] Iniciando SERVIDOR IA (puerto %PUERTO%)...
echo [2/4] start servidor headless >> "%LOG%"
start "SERVIDOR IA - TIENDA (%PUERTO%)" /D "%SERVIDOR%" "%SERVIDOR%\venv\Scripts\python.exe" iniciar_servidor_headless.py %PUERTO%

echo       Esperando a que el servidor acepte conexiones (hasta 180s)...
powershell -NoProfile -Command "for($i=0;$i -lt 180;$i++){ $c=New-Object Net.Sockets.TcpClient; try{ $c.Connect('127.0.0.1',%PUERTO%); $c.Close(); exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] El servidor no respondio en el puerto %PUERTO%.
    echo         Mira la ventana "SERVIDOR IA - TIENDA" para ver el error.
    echo [ERROR] timeout esperando puerto %PUERTO% >> "%LOG%"
    pause
    exit /b 1
)
echo       Servidor LISTO en ws://localhost:%PUERTO%/ws
echo [2/4] servidor listo >> "%LOG%"
echo.

REM -- 3) DASHBOARD DE TIENDA (HITO 9/11) --------------------------
REM  El dashboard propio del 9030 se retiro el 31-jul-2026 (decision
REM  del cierre del refactor): lo sustituye la pagina de dominio que
REM  sirve el propio servidor sobre la API de lectura.
echo [3/4] Abriendo DASHBOARD DE TIENDA (http://localhost:%PUERTO%/dashboards/tienda/)...
echo [3/4] abrir dashboard tienda >> "%LOG%"
start "" http://localhost:%PUERTO%/dashboards/tienda/

REM -- 4) CLIENTE de tienda (clients\tienda) -------------------------
echo [4/4] Iniciando CLIENTE de tienda...
echo [4/4] start cliente >> "%LOG%"
start "CLIENTE - TIENDA" /D "%CLIENTE%" "%CLIENTE%\venv\Scripts\python.exe" src\main.py

echo.
echo ============================================================
echo  TODO LISTO
echo    Servidor  : ws://localhost:%PUERTO%/ws
echo    Dashboard : http://localhost:%PUERTO%/dashboards/tienda/  (TIENDA)
echo    Visitantes: http://localhost:%PUERTO%/dashboard
echo    Perimetral: http://localhost:5333
echo    Cliente   : ventana "ELDE Tienda"
echo.
echo  Log de arranque: %LOG%
echo ============================================================
echo ===== FIN OK %time% ===== >> "%LOG%"
timeout /t 20 >nul
exit /b 0
