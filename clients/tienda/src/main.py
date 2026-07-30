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


from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget, QDockWidget, QTextEdit, QTabWidget
from PySide6.QtCore import Qt

from gui.windows_main import MainWindow 
from gui.components.SplashScreen import SplashScreen
from gui.components.render_box.render_box import Render_box
from gui.components.sidebar.sidebar_dock import Sidebar_Dock
from gui.components.sidebar.alerts_sidebar import AlertsSidebar
from model.settings_model import SettingsModel

## rest ann straming
from core.network.jarvis_api import Jarvis_api
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

        # Registro unificado (nucleo). Hasta el 30-jul-2026 este
        # cliente no registraba NADA: 27 print() y ni un solo
        # getLogger, asi que un fallo no dejaba rastro. Va DESPUES
        # de load_dotenv() para que el .env pueda fijar el nivel y
        # la carpeta (ELDE_LOG_NIVEL, ELDE_LOG_DIR).
        from pathlib import Path as _Path
        from elde_core.logging import configurar as _iniciar_registro
        from config.ajustes import CLIENT_TYPE as _CLIENT_TYPE
        _iniciar_registro(
            _CLIENT_TYPE,
            (os.getenv('site_id') or '').strip() or 'sitio-unico',
            raiz_proyecto=_Path(__file__).resolve().parents[1])
        
        email_jarvis = os.getenv('jarvis_email')
        password_jarvis = os.getenv('jarvis_password')
        url_api_jarvis = os.getenv('jarvis_url')
        
        # app_name PROPIO: los tres clientes compartian la misma
        # carpeta de configuracion y se pisaban los ajustes (causa
        # raiz de H-14). legacy_app_name siembra desde la antigua
        # la primera vez para no perder los DVR ya dados de alta.
        settingsModel = SettingsModel(app_name='tienda_view',
                                      legacy_app_name='windows_managers_view')
        list_windows = open_windows_windows()
        
        app = AppSingleton.initialize(sys.argv)
        app.setStyleSheet(load_stylesheet())

        # El hilo que escanea ventanas arranca al importar core.window_global,
        # antes de que exista la app, y corre hasta que se le diga que pare. Si
        # sigue vivo cuando Qt lo destruye, Qt aborta el proceso (0xc0000409) y
        # el cliente 'crasheaba' al cerrarse. aboutToQuit se emite con todo
        # todavia vivo, que es el momento correcto para pararlo.
        app.aboutToQuit.connect(windows_monitor.stop_scanner)
        
        jarvis_api = Jarvis_api(emailuser=email_jarvis, password=password_jarvis, url_api=url_api_jarvis)
        
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


        # ── Sidebar de Alertas IA (lado derecho) ──
        alerts_sidebar = AlertsSidebar(parent=None, title='Visitantes')
        
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

        # Conectar alertas de cada render_box al sidebar
        for box in window_containter.list_box:
            box.alert_received.connect(alerts_sidebar.add_alert)

        # ── Panel de capturas (pestaña junto a las alertas) ──
        # Lee las fotos del SERVIDOR por HTTP, no de una carpeta local: antes
        # el servidor solo las escribia en la carpeta de Amazonas View (ruta
        # fija en su config), asi que tienda no tenia ninguna que mostrar.
        # Pidiendolas al servidor funciona igual con el servidor en otra
        # maquina, que es el caso real hoy.
        from elde_core.ui.panel_capturas import PanelCapturas
        capturas_panel = PanelCapturas(
            url_ws=os.getenv('server_ws_url', ''),
            titulo='Personas detectadas')

        tabs_derecha = QTabWidget()
        tabs_derecha.addTab(alerts_sidebar, 'Visitantes')
        tabs_derecha.addTab(capturas_panel, 'Capturas')
        dock_alerts.setWidget(tabs_derecha)
    

        # ── Interruptor "Enviar por WhatsApp" del pie ──
        # Es GLOBAL: una sola casilla gobierna todas las camaras. El estado se
        # persiste, y al arrancar se propaga a cada recuadro para que el flag
        # viaje en el payload del frame desde el primer envio.
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