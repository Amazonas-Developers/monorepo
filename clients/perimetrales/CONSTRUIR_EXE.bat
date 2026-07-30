@echo off
REM ============================================================
REM  CONSTRUIR el ejecutable PORTABLE del cliente PerimetralesView.
REM  Ejecutar en una maquina WINDOWS 10/11 con Python 3.12 instalado
REM  (python.org, marcando "Add Python to PATH").
REM
REM  Genera:  dist\PerimetralesView\  -> carpeta PORTABLE con el .exe
REM  y todas sus dependencias. Se copia a cualquier Windows 10/11 y
REM  corre SIN instalar Python.
REM
REM  Usa un venv AISLADO (sin --system-site-packages) para que el .exe
REM  quede autocontenido (PyInstaller empaqueta PySide6/opencv/etc.).
REM ============================================================
cd /d "%~dp0"
title CONSTRUIR EXE - PerimetralesView

REM --- 1) Verificar Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro Python en el PATH.
    echo Instala Python 3.12 desde https://www.python.org/downloads/
    echo y marca la casilla "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

REM --- 2) venv LIMPIO y aislado ---
if exist "venv_build\Scripts\python.exe" (
    echo Reusando venv_build existente...
) else (
    echo Creando venv_build aislado...
    python -m venv venv_build
    if errorlevel 1 ( echo [ERROR] No se pudo crear el venv. & pause & exit /b 1 )
)

REM --- 3) Instalar dependencias (incluye PyInstaller) ---
REM     Se usa requirements_cliente.txt (solo lo que el CLIENTE necesita).
REM     El requirements.txt grande es el entorno del SERVIDOR: trae torch,
REM     ultralytics, PyQt6, matplotlib... (~4 GB) que aqui no hacen falta.
echo Instalando dependencias ^(la primera vez tarda varios minutos^)...
venv_build\Scripts\python.exe -m pip install --upgrade pip >nul
venv_build\Scripts\python.exe -m pip install -r requirements_cliente.txt
if errorlevel 1 (
    echo [ERROR] Fallo instalando dependencias. Revisa tu conexion.
    pause
    exit /b 1
)

REM --- 4) Compilar con PyInstaller usando el spec ---
REM     Se invoca como MODULO (python -m PyInstaller): en algunos venv no
REM     se genera pyinstaller.exe en Scripts y el bat fallaria.
echo.
echo Compilando el ejecutable ^(esto tarda un poco^)...
venv_build\Scripts\python.exe -m PyInstaller PerimetralesView.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] Fallo la compilacion. Revisa el log de arriba.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  LISTO. Cliente PORTABLE generado en:
echo     dist\PerimetralesView\
echo.
echo  DISTRIBUIR:
echo   1) Copia TODA la carpeta dist\PerimetralesView a cualquier
echo      Windows 10/11 (no necesita Python instalado).
echo   2) Edita el archivo .env de esa carpeta:
echo        server_ws_url        -> ws://72.68.60.141:9000/ws
echo        jarvis_establecimiento -> el establecimiento de esa maquina
echo        jpeg_calidad         -> 70-80 (menos = menos ancho de banda)
echo   3) Ejecuta PerimetralesView.exe
echo ============================================================
pause
