@echo off
REM ============================================================
REM  SETUP (una sola vez) del CLIENTE Windows.
REM  Detecta venv roto/relocalizado (de otra PC) y lo recrea.
REM  Usa --system-site-packages (reusa PySide6/opencv globales).
REM ============================================================
cd /d "%~dp0"
title SETUP CLIENTE - ELDE

REM 1) Si el venv existe pero su python no corre, esta roto -> borrar
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe --version >nul 2>&1
    if errorlevel 1 (
        echo [!] venv roto detectado, recreando...
        rmdir /s /q venv
    )
)

REM 2) Crear venv si no existe
if not exist "venv\Scripts\python.exe" (
    echo Creando venv con system-site-packages...
    python -m venv venv --system-site-packages
)

REM 3) Instalar/verificar dependencias del CLIENTE (slim, sin torch).
REM    requirements-cliente.txt: solo deps de la app Qt. Con
REM    --system-site-packages pip omite lo que ya esta global y solo baja
REM    lo que falte (NO reinstala el torch CUDA del servidor).
echo Instalando/verificando dependencias del cliente...
venv\Scripts\python.exe -m pip install -r requirements-cliente.txt

echo.
echo SETUP COMPLETO.
echo Arranca TODO el proyecto de tienda con:  INICIAR_TIENDA.bat
echo (en la carpeta ELDE: servidor + dashboard + cliente)
pause
