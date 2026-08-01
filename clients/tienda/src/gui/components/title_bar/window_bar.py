import sys
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget, QHBoxLayout, QPushButton, QLabel, QSizePolicy, QStatusBar
from PySide6.QtGui import QMouseEvent, QIcon
from dotenv import load_dotenv


load_dotenv()


#components 



class CustomTitleBar(QWidget):

    def __init__(self, parent):
        super().__init__(parent)
        self._start_pos = None
        self._is_dragging = False
        self.setup_ui(parent)
        self.apply_styles()    
        self.setAttribute(Qt.WA_StyledBackground, True)



    def setup_ui(self, parent):

        self.setObjectName('title_bar')
        self.setFixedHeight(40)

        
        layout = QHBoxLayout(self)
        layout.setObjectName('layout_title_bar')
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
   


        self.title = QLabel(f"{os.getenv('name_project', 'ELDE Tienda')} {os.getenv('version', '1.0')}")
        self.title.setObjectName('title_label')
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # ── Boton DASHBOARD: abre el dashboard de PRODUCTO de tienda ──
        self.btn_dashboard = QPushButton("  📊  Dashboard  ")
        self.btn_dashboard.setObjectName('DashboardButton')
        self.btn_dashboard.setFixedHeight(40)
        self.btn_dashboard.setCursor(Qt.PointingHandCursor)
        self.btn_dashboard.setToolTip(
            "Abrir el dashboard de tienda: pasillos más y menos concurridos,\n"
            "marketing (género, edad, franjas), capturas y búsqueda con IA.")
        self.btn_dashboard.clicked.connect(self.open_dashboard)

        btn_minimize = QPushButton()
        btn_minimize.setIcon(QIcon('resource/minimize.png')) 
        btn_minimize.setAttribute(Qt.WA_StyledBackground, True)
        btn_minimize.setFixedHeight(40)
        btn_minimize.setObjectName('MinimizeButton')
        btn_minimize.clicked.connect(self.parent().showMinimized)


        btn_maximize = QPushButton()
        btn_maximize.setIcon(QIcon('resource/maximize.png')) 
        btn_maximize.setFixedHeight(40)
        btn_maximize.setObjectName('MaximizeButton')
        btn_maximize.clicked.connect(self.toggle_maximize_restore)
        
        btn_close = QPushButton()
        btn_close.setIcon(QIcon('resource/close.png')) 
        btn_close.setFixedHeight(40)
        btn_close.setObjectName('CloseButton')
        btn_close.clicked.connect(self.parent().close)


        layout.addWidget(self.title)
        layout.addStretch(1)
        layout.addWidget(self.btn_dashboard)
        layout.addWidget(btn_minimize)
        layout.addWidget(btn_maximize)
        layout.addWidget(btn_close)

    def _dashboard_url(self):
        """URL del dashboard de PRODUCTO de tienda (/dashboards/tienda/).

        Lo sirve el MISMO servidor de IA, asi que la URL se deriva de
        server_ws_url (el servidor al que el cliente ya esta conectado) y no
        de un puerto fijo: asi vale igual con servidor local o remoto.
        DASHBOARD_URL en .env fuerza una URL concreta."""
        forced = (os.getenv("DASHBOARD_URL") or "").strip()
        if forced:
            return forced
        # La URL la calcula config/ a partir del MISMO `server_ws_url` que usa
        # la conexion, asi que el boton no puede apuntar a un servidor distinto
        # del que esta analizando — que es lo que pasaba con dos literales.
        from config import cargar
        return cargar().dashboard_url

    def open_dashboard(self):
        """Abre el dashboard de tienda en el navegador."""
        import webbrowser
        url = self._dashboard_url()
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"No se pudo abrir el dashboard ({url}): {e}")




    def toggle_maximize_restore(self):
        """Alterna entre maximizar y restaurar la ventana"""
        parent = self.parent().parent()
        if parent.isMaximized():
            parent.showNormal()
        else:
            parent.showMaximized()
    



    def mousePressEvent(self, event: QMouseEvent):
        """Maneja el evento de presión del mouse"""
        if event.button() == Qt.LeftButton:
            self._start_pos = event.globalPosition().toPoint()
            self._is_dragging = True
            event.accept()
    



    def mouseMoveEvent(self, event: QMouseEvent):
        """Maneja el evento de movimiento del mouse"""
        if self._is_dragging and self._start_pos:
            delta = event.globalPosition().toPoint() - self._start_pos
            self.parent().parent().move(self.parent().parent().pos() + delta)
            self._start_pos = event.globalPosition().toPoint()
            event.accept()
    



    def mouseReleaseEvent(self, event: QMouseEvent):
        """Maneja el evento de liberación del mouse"""
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
            self._start_pos = None
            event.accept()
    


    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Maximiza/restaura con doble click"""
        if event.button() == Qt.LeftButton:
            self.toggle_maximize_restore()
            event.accept()


    def apply_styles(self):
        qss_path = os.path.join(os.path.dirname(__file__), 'styles.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r') as f:
                self.setStyleSheet(f.read())
        # Estilo del boton Dashboard (se anade tras cargar el qss para que
        # no dependa de editar el archivo de estilos). Acento del dominio
        # tienda (#00c8ff), texto oscuro para el contraste.
        self.btn_dashboard.setStyleSheet(
            "#DashboardButton{background:#00c8ff;color:#06222c;"
            "font-weight:bold;font-size:10pt;border:none;border-radius:6px;"
            "margin:4px 8px;padding:4px 12px;}"
            "#DashboardButton:hover{background:#33d5ff;}")


    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """Maximiza/restaura con doble click"""
        if event.button() == Qt.LeftButton:
            self.toggle_maximize_restore()
            event.accept()