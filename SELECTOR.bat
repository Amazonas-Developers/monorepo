@echo off
REM ============================================================
REM  SELECTOR - Hub de arranque de los sistemas ELDE.
REM  Abre una ventana para elegir que sistema iniciar. Cada uno
REM  arranca por separado (no estan ligados). Usa el Python
REM  GLOBAL (ya trae PySide6), sin depender del venv de ningun view.
REM ============================================================
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
