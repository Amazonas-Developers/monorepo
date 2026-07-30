@echo off
REM ============================================================
REM  CLIENTE Windows (PerimetralesView) -> SERVIDOR de IA.
REM
REM  Ejecutar UNA vez en CADA computadora cliente (doble clic;
REM  se pedira permiso de administrador solo).
REM
REM  Que hace:
REM   1) Permite en el Firewall de Windows la SALIDA hacia el
REM      servidor en los puertos 9000 (video/IA), 8090 (dashboard)
REM      y 8091 (alertas). Normalmente la salida ya esta permitida,
REM      pero esto cubre firewalls corporativos/antivirus estrictos.
REM   2) PRUEBA la conexion al servidor y te dice si lo alcanza.
REM
REM  Si necesitas cambiar la IP del servidor, edita la linea SERVIDOR.
REM ============================================================
setlocal EnableDelayedExpansion
set "SERVIDOR=72.68.60.141"
set "PUERTOS=9000 8090 8091"

REM --- Elevar a administrador si hace falta ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permisos de administrador...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================================
echo  Permitiendo SALIDA del firewall hacia %SERVIDOR%
echo ============================================================
for %%P in (%PUERTOS%) do (
    netsh advfirewall firewall delete rule name="PerimetralesView OUT %%P" >nul 2>&1
    netsh advfirewall firewall add rule name="PerimetralesView OUT %%P" dir=out action=allow protocol=TCP remoteport=%%P remoteip=%SERVIDOR% >nul
    echo    - Puerto %%P permitido ^(salida hacia %SERVIDOR%^)
)

echo.
echo ============================================================
echo  Probando conexion al servidor %SERVIDOR% ...
echo ============================================================
set "FALLO=0"
for %%P in (%PUERTOS%) do (
    powershell -NoProfile -Command "$r = Test-NetConnection -ComputerName '%SERVIDOR%' -Port %%P -WarningAction SilentlyContinue; if ($r.TcpTestSucceeded) { Write-Host '    OK    puerto %%P alcanzable' -ForegroundColor Green; exit 0 } else { Write-Host '    FALLA puerto %%P NO alcanzable' -ForegroundColor Red; exit 1 }"
    if !errorlevel! neq 0 set "FALLO=1"
)

echo.
if "!FALLO!"=="0" (
    echo ============================================================
    echo  TODO OK: este cliente alcanza el servidor. Ya puede conectar
    echo  a  ws://%SERVIDOR%:9000/ws
    echo ============================================================
) else (
    echo ============================================================
    echo  ALGUN PUERTO NO SE ALCANZA. Revisa, EN ESTE ORDEN:
    echo   1^) Firewall del SERVIDOR Linux ^(en el servidor, una vez^):
    echo        sudo ufw allow 9000/tcp ^&^& sudo ufw allow 8090/tcp ^&^& sudo ufw allow 8091/tcp
    echo   2^) Que cliente y servidor esten en la MISMA red, o que el
    echo      router haga port-forward de 9000/8090/8091 a %SERVIDOR%.
    echo   3^) Que el servidor este encendido y el servicio activo.
    echo ============================================================
)

echo.
pause
