# -*- mode: python ; coding: utf-8 -*-
"""
Spec de PyInstaller para el CLIENTE distribuible de perimetrales-view.

Genera dist/PerimetralesView/ con TODO lo necesario para instalarse en la
máquina de un cliente y conectarse al servidor (72.68.60.171):

  PerimetralesView.exe          (sin ventana de consola)
  _internal/                    (librerías + gui/styles/global.qss)
  resource/                     iconos del pie (persona, layout, stop, …)
  src/resources/                ico.png / logo.ico
  src/sdk/                      DLLs Hikvision/Dahua (DVR)
  src/workers/                  capture_woker.py
  .env                          servidor + credenciales jarvis

main.py hace os.chdir(carpeta_del_exe) cuando va congelado, así que todas
las rutas relativas (resource/, src/, .env) resuelven junto al exe.

Compilar:  venv\\Scripts\\pyinstaller.exe PerimetralesView.spec --noconfirm
"""

import os
import shutil

NOMBRE = 'PerimetralesView'
raiz = os.getcwd()

a = Analysis(
    ['src/main.py'],
    pathex=[raiz, os.path.join(raiz, 'src')],
    binaries=[],
    datas=[
        # global.qss se carga relativo a __file__ de main -> va DENTRO de
        # _internal, en gui/styles/.
        (os.path.join(raiz, 'src', 'gui', 'styles'), 'gui/styles'),
    ],
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PySide6.QtNetwork', 'PySide6.QtWebSockets',
        'numpy', 'msgpack', 'dotenv',
        'PIL', 'PIL._imaging', 'PIL.Image', 'PIL.ImageGrab',
        'win32gui', 'win32ui', 'win32con', 'win32api', 'pywintypes',
        # Módulos propios que se resuelven en tiempo de ejecución.
        'core.capture_exaple', 'core.window_controller', 'core.app_singleton',
        'core.state_global.hwnd', 'core.capture_store', 'core.dashboard_url',
        'core.network.socket_client', 'core.network.jarvis_api',
        'core.network.jarvis_alert_forwarder',
        # (vigilante_alertas_cliente NO se incluye: es un consumidor
        #  Socket.IO huérfano —main.py no lo usa, las alertas llegan por el
        #  websocket del servidor— y su import de `socketio` haría fallar el
        #  hidden import al no estar esa dependencia instalada.)
        'core.dvr.hikvision_sdk', 'core.dvr.hikvision_http',
        'core.dvr.dahua_sdk', 'core.dvr.dahua_http',
        # El worker de captura se ejecuta DENTRO del exe (modo multi-call
        # --capture-worker), así que debe ir empaquetado, no solo copiado.
        'workers.capture_woker',
        'model.windows.list_windows', 'model.settings_model',
        'gui.windows_main', 'gui.components.SplashScreen',
        'gui.components.custom_status_bar',
        'gui.components.render_box.render_box',
        'gui.components.render_box.sv_overlay',
        'gui.components.sidebar.sidebar_dock',
        'gui.components.sidebar.alerts_sidebar',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Pesados que el CLIENTE no usa (la IA corre en el servidor).
        'torch', 'torchvision', 'tensorrt', 'transformers',
        'matplotlib', 'pandas',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NOMBRE,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX a veces rompe DLLs de Qt/SDK
    console=False,                   # sin ventana negra en la máquina del cliente
    icon=os.path.join(raiz, 'src', 'resources', 'logo.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=NOMBRE,
)

# ---------------------------------------------------------------------------
# POST-BUILD: copiar junto al exe todo lo que el cliente referencia por ruta
# RELATIVA al CWD (main.py hace chdir a la carpeta del exe al arrancar).
# ---------------------------------------------------------------------------
destino = os.path.join(raiz, 'dist', NOMBRE)
copias = [
    ('resource', 'resource'),                     # iconos del pie
    (os.path.join('src', 'resources'), os.path.join('src', 'resources')),
    (os.path.join('src', 'sdk'), os.path.join('src', 'sdk')),          # DLLs DVR
    (os.path.join('src', 'workers'), os.path.join('src', 'workers')),
    ('.env', '.env'),                             # servidor + jarvis + calidad
    ('INSTRUCCIONES_CLIENTE.txt', 'INSTRUCCIONES.txt'),
]
# OJO: `capture/` (fotos de alertas reales) NO se copia a propósito — son
# datos de producción con imágenes de personas; cada instalación crea la suya.
print('\n=== POST-BUILD: copiando recursos junto al exe ===')
for origen_rel, destino_rel in copias:
    origen = os.path.join(raiz, origen_rel)
    dest = os.path.join(destino, destino_rel)
    if not os.path.exists(origen):
        print(f'  ⚠️ no existe: {origen_rel} (omitido)')
        continue
    if os.path.isdir(origen):
        shutil.copytree(origen, dest, dirs_exist_ok=True)
    else:
        os.makedirs(os.path.dirname(dest) or destino, exist_ok=True)
        shutil.copy2(origen, dest)
    print(f'  ✅ {origen_rel} -> dist/{NOMBRE}/{destino_rel}')
print('=== POST-BUILD terminado ===')
