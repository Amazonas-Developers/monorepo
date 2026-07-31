@echo off
REM ============================================================
REM  SELECTOR - Hub de arranque de los sistemas ELDE.
REM  Abre una ventana para elegir que sistema iniciar. Cada uno
REM  arranca por separado (no estan ligados). Usa el Python
REM  GLOBAL (ya trae PySide6), sin depender del venv de ningun view.
REM  Al lanzar un sistema abre ademas su dashboard en el navegador.
REM ============================================================

REM -- Auto-elevacion: relanzar como Administrador si no lo es --
REM  (los INICIAR_*.bat de los clientes tambien se elevan; heredar
REM   admin desde aqui evita un segundo aviso de UAC por cliente)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permisos de Administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM pythonw = sin ventana de consola; si no esta, usar python normal.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "%~dp0selector.py"
) else (
    start "" python "%~dp0selector.py"
)
exit /b 0
