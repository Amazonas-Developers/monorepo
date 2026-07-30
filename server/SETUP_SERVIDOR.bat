@echo off
REM ============================================================
REM  SETUP (una sola vez) del SERVIDOR de inferencia:
REM   1) crea el venv reusando paquetes globales (torch/ultralytics)
REM   2) instala onnxruntime-gpu (IA en GPU)
REM   3) verifica modelos y GPU
REM ============================================================
cd /d "%~dp0"
title SETUP SERVIDOR IA - ELDE

echo ============================================================
echo  1/4  Creando venv (--system-site-packages)...
echo ============================================================
if exist "venv\Scripts\python.exe" (
    echo    venv ya existe, se reutiliza.
) else (
    python -m venv venv --system-site-packages
)

echo.
echo ============================================================
echo  2/4  Instalando onnxruntime-gpu (puede tardar, ~250 MB)...
echo ============================================================
venv\Scripts\python.exe -m pip install --ignore-installed onnxruntime-gpu==1.23.2

echo.
echo ============================================================
echo  3/4  Verificando modelos (esenciales / opcionales)...
echo ============================================================
venv\Scripts\python.exe scripts\setup_modelos.py

echo.
echo ============================================================
echo  4/4  Verificando GPU en ONNX (debe listar CUDAExecutionProvider)...
echo ============================================================
venv\Scripts\python.exe -c "import torch, onnxruntime as ort; ort.preload_dlls(); print('CUDA:', torch.cuda.is_available()); print(ort.get_available_providers())"

echo.
echo ============================================================
echo  SETUP COMPLETO. Ahora ejecuta:  INICIAR_SERVIDOR.bat
echo ============================================================
pause
