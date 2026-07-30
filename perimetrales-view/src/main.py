import sys
import os

# UTF-8 seguro en consola: los print con emoji (📄 ✅ 🔥) NO deben crashear el
# arranque al lanzarse desde INICIAR_TODO.bat (consola cp1252). Sin esto el
# cliente se cerraba con UnicodeEncodeError antes de abrir la ventana.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# EMPAQUETADO (PyInstaller): fijar el directorio de trabajo JUNTO AL EXE.
# Todas las rutas relativas del cliente (resource/*.png de los botones,
# src/resources/ico.png, el .env con el servidor) resuelven contra el CWD;
# sin esto, el exe instalado en otra máquina arrancaría sin iconos ni config.
if getattr(sys, 'frozen', False):
    try:
        os.chdir(os.path.dirname(sys.executable))
    except Exception:
        pass

# ── MODO WORKER DE CAPTURA (multi-call binary) ───────────────────────────
# El cliente lanza un proceso aparte para capturar la ventana de la cámara.
# En desarrollo ejecuta "python src/workers/capture_woker.py <hwnd>", pero
# EMPAQUETADO `sys.executable` es el PROPIO EXE y PyInstaller ignora el
# script pasado como argumento -> arrancaría OTRA copia de la aplicación
# (bucle de ventanas al pulsar Play). Solución: el exe se re-invoca a sí
# mismo con --capture-worker y aquí actúa como worker, SIN abrir la GUI.
# Este bloque va antes de importar Qt para no cargar la interfaz en vano.
if "--capture-worker" in sys.argv:
    try:
        _i = sys.argv.index("--capture-worker")
        _hwnd = int(sys.argv[_i + 1])
    except (IndexError, ValueError):
        sys.exit(1)
    if getattr(sys, 'frozen', False):
        sys.path.insert(0, os.path.join(os.path.dirname(sys.executable), 'src'))
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from workers.capture_woker import ejecutar_worker
    ejecutar_worker(_hwnd)
    sys.exit(0)

import numpy as np
from PIL import Image
from dotenv import load_dotenv

### MODELS AND DATA
from model.windows.list_windows import open_windows_windows
from core.window_global import windows_monitor


###    CONTROLLER AND LOGIC
from core.app_singleton import  AppSingleton


### COMPONENTS AND UI
##from gui.components.modal_msm import ModalDialog
##from gui.windows_main import MainWindow


from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget, QDockWidget, QTextEdit
from PySide6.QtCore import Qt

from gui.windows_main import MainWindow 
from gui.components.SplashScreen import SplashScreen
from gui.components.render_box.render_box import Render_box
from gui.components.sidebar.sidebar_dock import Sidebar_Dock
from gui.components.sidebar.alerts_sidebar import AlertsSidebar
from model.settings_model import SettingsModel

## rest ann straming
from core.network.jarvis_api import Jarvis_api
from core.network.jarvis_alert_forwarder import JarvisAlertForwarder
from core.network.socket_client import Socket_services

        

def load_stylesheet():
    qss_path = os.path.join(os.path.dirname(__file__), 'gui', 'styles', 'global.qss')

    if os.path.exists(qss_path):
        print('Loading stylesheet from:', qss_path)
        with open(qss_path, 'r') as f:
            return f.read()
    else: print('Stylesheet file not found:', qss_path)





def main():
    try:
        load_dotenv()
        
        email_jarvis = os.getenv('jarvis_email')
        password_jarvis = os.getenv('jarvis_password')
        url_api_jarvis = os.getenv('jarvis_url')
        
        settingsModel = SettingsModel()
        list_windows = open_windows_windows()
        
        app = AppSingleton.initialize(sys.argv)
        app.setStyleSheet(load_stylesheet())
        
        jarvis_api = Jarvis_api(emailuser=email_jarvis, password=password_jarvis,
                                url_api=url_api_jarvis,
                                establecimiento=os.getenv('jarvis_establecimiento'))
        
        socket_client = Socket_services()
        
        splashScreen = SplashScreen()
        splashScreen.show()
        window_containter = MainWindow(
            socket_service=socket_client,
            jarvis_api=jarvis_api,
            data_model_gui = settingsModel
        )
        
        windowsPrincipal = window_containter.window_child 
    
        asidebar = Sidebar_Dock(parent=None, title='Visión', src_ico='src/resources/ico.png')
        asidebar.print_list(list_windows)
        
        
        windows_monitor.window_opened.connect(asidebar.add_new_window)
        windows_monitor.window_closed.connect(asidebar.remove_closed_windows)
        
        
        dock = QDockWidget(None)
        dock.setWidget(asidebar)
        dock.setStyleSheet("""
            QDockWidget::title {
                padding: 0px;       /* elimina espacio interno */
                margin: 0px;        /* elimina espacio externo */
                spacing: 0px;       /* elimina separación entre ícono y texto */
                text-align: center; /* centra el texto */
            }
            QDockWidget::close-button, QDockWidget::float-button {
                width: 0px;
                height: 0px;
            }
        """)
        
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QWidget())
        windowsPrincipal.addDockWidget(Qt.LeftDockWidgetArea, dock)  # lo acoplas a la izquierda


        # ── Sidebar de Alertas Perimetrales (lado derecho) ──
        alerts_sidebar = AlertsSidebar(parent=None, title='Alertas Perimetrales')
        
        dock_alerts = QDockWidget(None)
        dock_alerts.setWidget(alerts_sidebar)
        dock_alerts.setStyleSheet("""
            QDockWidget::title {
                padding: 0px;
                margin: 0px;
                spacing: 0px;
                text-align: center;
            }
            QDockWidget::close-button, QDockWidget::float-button {
                width: 0px;
                height: 0px;
            }
        """)
        dock_alerts.setFeatures(QDockWidget.NoDockWidgetFeatures)
        dock_alerts.setTitleBarWidget(QWidget())
        windowsPrincipal.addDockWidget(Qt.RightDockWidgetArea, dock_alerts)

        # Reenviador de alertas hacia la API de Jarvis365 (novedades).
        # Vive en el hilo GUI (envío QtNetwork asíncrono, no bloquea).
        jarvis_forwarder = JarvisAlertForwarder(jarvis_api)

        # Interruptor "Enviar a Jarvis" del pie: restaurar estado guardado y
        # cablear checkbox <-> forwarder (+persistencia para la próxima sesión).
        envio_activo = bool(settingsModel.get("jarvis_envio_activo", True))
        jarvis_forwarder.set_activo(envio_activo)
        chk = window_containter.footer_bar.chk_envio_jarvis
        chk.setChecked(envio_activo)

        def _toggle_envio_jarvis(valor):
            jarvis_forwarder.set_activo(valor)
            settingsModel.set("jarvis_envio_activo", bool(valor))

        chk.toggled.connect(_toggle_envio_jarvis)

        # Interruptor "Enviar por WhatsApp" del pie: GLOBAL para todas las
        # cámaras. Restaurar estado guardado, aplicarlo a cada render_box (el
        # flag viaja en cada frame -> el servidor VigilanteAmazonas envía la
        # imagen de cada alerta al grupo) y persistir para la próxima sesión.
        whatsapp_activo = bool(settingsModel.get("whatsapp_envio_activo", False))
        chk_wa = window_containter.footer_bar.chk_envio_whatsapp
        chk_wa.setChecked(whatsapp_activo)
        for box in window_containter.list_box:
            box.whatsapp_boolean = whatsapp_activo

        def _toggle_envio_whatsapp(valor):
            settingsModel.set("whatsapp_envio_activo", bool(valor))
            for box in window_containter.list_box:
                box.whatsapp_boolean = bool(valor)

        chk_wa.toggled.connect(_toggle_envio_whatsapp)

        # Conectar alertas de cada render_box al sidebar Y a Jarvis
        for box in window_containter.list_box:
            box.alert_received.connect(alerts_sidebar.add_alert)
            box.alert_received.connect(jarvis_forwarder.on_alert)

        window_containter.show()
    
        splashScreen.finish(windowsPrincipal)
        return app.exec()
        


    except Exception as e:
        print(f'Fatal crash: {e}')
        import traceback
        traceback.print_exc()
        return 1






if __name__ == '__main__':  # FUNCTION MAIN

    sys.exit(main())