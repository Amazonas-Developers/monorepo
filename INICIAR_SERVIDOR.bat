@echo off
REM ============================================================
REM  INICIAR_SERVIDOR - Arranca SOLO el servidor de IA (ELDE):
REM    - websocket de inferencia   ws://0.0.0.0:9000/ws
REM    - dashboards                http://localhost:9000/dashboards/
REM    - panel VIGILANTE           http://localhost:5333
REM  Usa SIEMPRE el venv del servidor (con el Python global se levanta
REM  un servidor a medias que ocupa el puerto: gotcha del 4-ago-2026).
REM  No requiere Administrador. Cerrar: cerrar esta ventana o Ctrl+C.
REM ============================================================

setlocal
cd /d "%~dp0server"
title SERVIDOR ELDE (9000)

REM UTF-8: los print con acentos/emoji no deben crashear la consola.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Si ya hay alguien escuchando en 9000, no levantar un segundo servidor.
netstat -ano | findstr /r ":9000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Ya hay un servidor escuchando en el puerto 9000.
    echo Si los clientes lo ven "offline", puede ser un servidor a medias
    echo lanzado con el Python global: cierralo y vuelve a ejecutar esto.
    echo.
    pause
    exit /b 1
)

echo Iniciando servidor de IA (la primera vez tarda ~1 min por los modelos)...
venv\Scripts\python.exe iniciar_servidor_headless.py 9000
pause
