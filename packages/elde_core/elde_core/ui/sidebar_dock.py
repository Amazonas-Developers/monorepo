"""
src/gui/components/sidebar/sidebar_dock.py
Sidebar con dos secciones en QToolBox:
  1. Ventanas de Windows (existente)
  2. Dispositivos DVR   (nuevo — DVRTree)
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QToolBox, QAbstractButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui  import QPixmap

from ..capture.window_monitor import windows_monitor
from .box_image import Box_cap
from .dvr_tree import DVRTree
from ..dvr.storage import DVRRepository


class Sidebar_Dock(QWidget):

    def __init__(self, parent=None, title="Panel Lateral", src_ico="src/resources/ico.png"):
        super().__init__(parent)
        self.ico   = src_ico
        self.title = title
        self._repo = DVRRepository()
        self.setup_ui()

    def setup_ui(self):
        self.setObjectName("SidebarDock")
        self.setFixedWidth(250)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout_dock = QVBoxLayout(self)
        layout_dock.setContentsMargins(0, 0, 0, 0)

        # ── Header ──────────────────────────────────────────
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header.setObjectName("Head")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setFixedHeight(50)

        img_image = QLabel()
        ico = QPixmap(self.ico).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img_image.setFixedSize(32, 32)
        if not ico.isNull():
            img_image.setPixmap(ico)

        text_title = QLabel(self.title)
        text_title.setObjectName("title_semibold")

        header_layout.addWidget(img_image)
        header_layout.addWidget(text_title)

        # ── Sección: Ventanas Windows ────────────────────────
        content_center = QWidget()
        content_center.setAttribute(Qt.WA_StyledBackground, True)
        content_center.setObjectName("ContentSidebar")
        self.content_layaut = QVBoxLayout(content_center)
        self.content_layaut.setContentsMargins(0, 0, 0, 0)
        self.content_layaut.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        # Aviso de seccion vacia: sin el, una lista sin ventanas es un panel
        # EN BLANCO y parece que la aplicacion fallo.
        self._aviso_sin_ventanas = QLabel(
            'Sin ventanas para capturar todavía.\n'
            'Abre iVMS-4200 / SmartPSS y aparecerá aquí.')
        self._aviso_sin_ventanas.setAlignment(Qt.AlignCenter)
        self._aviso_sin_ventanas.setWordWrap(True)
        self._aviso_sin_ventanas.setStyleSheet(
            'color:#8b949e; font-size:11px; padding:14px 8px;')
        self.content_layaut.addWidget(self._aviso_sin_ventanas)

        # ── Sección: DVR Tree ────────────────────────────────
        self.dvr_tree = DVRTree()
        self.dvr_tree.refresh(self._repo.all())

        # ── ToolBox ──────────────────────────────────────────
        # Antes habia un tercer item VACIO e invisible seleccionado por
        # defecto "para que ninguno quede activo": el resultado real era un
        # sidebar EN BLANCO al arrancar sin DVRs guardados. Se arranca
        # mostrando las ventanas (o los DVR, si ya hay guardados).
        toolbox = QToolBox()
        toolbox.addItem(content_center, "Ventanas de Windows")
        toolbox.addItem(self.dvr_tree,  "Dispositivos DVR")
        toolbox.setCurrentIndex(1 if self._repo.all() else 0)

        layout_dock.addWidget(header, alignment=Qt.AlignTop)
        layout_dock.addWidget(toolbox)

    # ── API: refrescar árbol DVR ─────────────────────────────

    def refresh_dvr_tree(self, devices=None):
        """Llamado desde windows_main._on_devices_updated()."""
        if devices is None:
            devices = self._repo.all()
        self.dvr_tree.refresh(devices)

    # ── Ventanas Windows ─────────────────────────────────────

    def print_list(self, list_windows):
        if list_windows:
            self._aviso_sin_ventanas.setVisible(False)
            for window in list_windows:
                self.content_layaut.addWidget(Box_cap(window))

    def add_new_window(self, hwnd, title):
        self._aviso_sin_ventanas.setVisible(False)
        self.content_layaut.addWidget(Box_cap({"hwnd": hwnd, "title": title}))

    def remove_closed_windows(self, hwnd):
        for i in range(self.content_layaut.count()):
            widget = self.content_layaut.itemAt(i).widget()
            if widget and hwnd == getattr(widget, "id_windows", None):
                self.content_layaut.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
                break
