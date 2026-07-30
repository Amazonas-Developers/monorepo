"""
AlertsSidebar – Panel lateral para mostrar alertas generadas por las detecciones de YOLO.

Muestra cada alerta con: icono de tipo, nombre de clase, descripción, timestamp
y opcionalmente la imagen crop del objeto detectado.
"""

import base64
import os
import subprocess
import time
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QPushButton, QFrame, QSizePolicy, QToolButton
)
from PySide6.QtCore import Qt, Signal, Slot, QByteArray, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPixmap, QColor, QFont, QIcon


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
MAX_ALERTS = 200           # Máximo de alertas en el historial antes de descartar las más antiguas
CROP_THUMB_SIZE = 60       # Tamaño del thumbnail del crop en px
# Cada cuántas alertas RECIBIDAS (total del sidebar, ambas columnas) se
# vacía automáticamente. 0 desactiva el auto-vaciado.
AUTO_CLEAR_AFTER = 150


# Colores por tipo de evento (retail)
EVENT_COLORS = {
    # Clientes y productos
    "Cliente agarra producto":     "#2ecc71",   # verde
    "Persona evaluando producto":  "#00c8ff",   # cian
    "Consulta de precio":          "#9b59b6",   # morado
    # Operacion y reposicion
    "Anaquel vacio":               "#e74c3c",   # rojo
    "Caja obstruyendo":            "#e67e22",   # naranja
    "Empleado reponiendo":         "#f1c40f",   # amarillo
    "Reposicion de mercancia":     "#f1c40f",
    # Genericos (compat)
    "Alerta":                      "#e74c3c",
}

EVENT_ICONS = {
    "Cliente agarra producto":     "🤚",
    "Persona evaluando producto":  "👀",
    "Consulta de precio":          "🏷️",
    "Anaquel vacio":               "📉",
    "Caja obstruyendo":            "📦",
    "Empleado reponiendo":         "🧑‍🔧",
    "Reposicion de mercancia":     "🔄",
    "Alerta":                      "⚠",
}

DEFAULT_COLOR = "#95a5a6"


# ─────────────────────────────────────────────────────────────────────────────
# Widget individual de alerta
# ─────────────────────────────────────────────────────────────────────────────

class AlertItemWidget(QFrame):
    """Representa una sola alerta en la lista."""

    def __init__(self, alert_data: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("AlertItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Guardar ruta del screenshot para abrir al hacer click
        self._screenshot_path = alert_data.get("screenshot_path", "")

        event_type = alert_data.get("event_type", "Alerta")
        class_name = alert_data.get("class_name", "Desconocido")
        description = alert_data.get("description", "")
        timestamp = alert_data.get("timestamp", time.time())
        crop_b64 = alert_data.get("crop_image", "") or alert_data.get("image_base64", "")
        camera_id = alert_data.get("camera_id", "")

        color = EVENT_COLORS.get(event_type, DEFAULT_COLOR)
        icon_text = EVENT_ICONS.get(event_type, "●")

        # ── Formatear hora ──
        try:
            if isinstance(timestamp, str):
                raw_timestamp = timestamp.strip()
                if raw_timestamp:
                    if raw_timestamp.endswith("Z"):
                        raw_timestamp = raw_timestamp[:-1] + "+00:00"
                    dt = datetime.fromisoformat(raw_timestamp)
                    if dt.tzinfo is not None:
                        dt = dt.astimezone().replace(tzinfo=None)
                else:
                    dt = datetime.fromtimestamp(time.time())
            else:
                dt = datetime.fromtimestamp(float(timestamp))
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError, OSError):
            time_str = "--:--:--"

        # ── Estilo del frame ──
        self.setStyleSheet(f"""
            #AlertItem {{
                background-color: #1e1e1e;
                border-left: 3px solid {color};
                border-radius: 4px;
                margin: 2px 4px;
                padding: 6px;
            }}
            #AlertItem:hover {{
                background-color: #2a2a2a;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── Thumbnail (miniatura) ──
        # Fuente de la imagen, por orden: base64 del evento (si viene), o el
        # ARCHIVO local guardado por el servidor (output/detecciones/...),
        # que es el caso de las alertas de retail. Cliente y servidor en la
        # misma maquina -> se lee el jpg directo del disco, sin base64.
        pixmap = None
        if crop_b64:
            try:
                pm = QPixmap()
                pm.loadFromData(QByteArray(base64.b64decode(crop_b64)))
                if not pm.isNull():
                    pixmap = pm
            except Exception:
                pixmap = None
        if pixmap is None and self._screenshot_path \
                and os.path.isfile(self._screenshot_path):
            pm = QPixmap(self._screenshot_path)
            if not pm.isNull():
                pixmap = pm

        if pixmap is not None or self._screenshot_path:
            thumb_label = QLabel()
            thumb_label.setFixedSize(CROP_THUMB_SIZE, CROP_THUMB_SIZE)
            thumb_label.setCursor(Qt.PointingHandCursor)
            thumb_label.setToolTip("Abrir la carpeta de la foto")
            thumb_label.setStyleSheet(
                "border-radius: 4px; background-color: #111;")
            if pixmap is not None:
                thumb_label.setPixmap(pixmap.scaled(
                    CROP_THUMB_SIZE, CROP_THUMB_SIZE,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation))
                thumb_label.setAlignment(Qt.AlignCenter)
            else:
                # Ruta presente pero el archivo aun no esta disponible.
                thumb_label.setText("📷")
                thumb_label.setAlignment(Qt.AlignCenter)
            # Click en la MINIATURA -> abrir la carpeta (mismo handler).
            thumb_label.mouseReleaseEvent = self._on_thumb_click
            layout.addWidget(thumb_label)

        # ── Texto ──
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 0, 0, 0)

        # Línea superior: icono + tipo + clase
        header_label = QLabel(f"{icon_text}  {event_type} — {class_name}")
        header_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold; background: transparent; border: none;")
        header_label.setWordWrap(True)
        text_layout.addWidget(header_label)

        # Descripción
        if description:
            desc_label = QLabel(description)
            desc_label.setStyleSheet("color: #b0b0b0; font-size: 10px; background: transparent; border: none;")
            desc_label.setWordWrap(True)
            text_layout.addWidget(desc_label)

        # Pie: hora + cámara
        footer_parts = [f"🕐 {time_str}"]
        if camera_id:
            footer_parts.append(f"📷 Cam {camera_id}")
        footer_label = QLabel("  ".join(footer_parts))
        footer_label.setStyleSheet("color: #666; font-size: 9px; background: transparent; border: none;")
        text_layout.addWidget(footer_label)

        layout.addLayout(text_layout, stretch=1)

    def mouseReleaseEvent(self, event):
        """Click en la alerta -> abre la CARPETA de la foto."""
        if event.button() == Qt.LeftButton:
            self._abrir_carpeta()
        super().mouseReleaseEvent(event)

    def _on_thumb_click(self, event):
        """Click en la MINIATURA -> abre la CARPETA de la foto."""
        if event.button() == Qt.LeftButton:
            self._abrir_carpeta()

    def _abrir_carpeta(self):
        """Abre en el Explorador la carpeta que contiene la foto (con el
        archivo resaltado si existe). No hace nada si no hay ruta."""
        path = self._screenshot_path
        if not path:
            return
        try:
            if os.path.isfile(path):
                # Abre la carpeta con el archivo seleccionado.
                subprocess.Popen(['explorer', '/select,',
                                  os.path.normpath(path)])
            else:
                carpeta = path if os.path.isdir(path) else os.path.dirname(path)
                if carpeta and os.path.isdir(carpeta):
                    subprocess.Popen(['explorer', os.path.normpath(carpeta)])
        except Exception as e:
            print(f"No se pudo abrir la carpeta de la foto: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Columna individual de alertas (usada internamente por AlertsSidebar)
# ─────────────────────────────────────────────────────────────────────────────

class _AlertColumn(QWidget):
    """Una columna con header, scroll de alertas y contador propio."""

    def __init__(self, title: str, icon: str, color: str, max_alerts: int = MAX_ALERTS, parent=None):
        super().__init__(parent)
        self.max_alerts = max_alerts
        self.alert_count = 0
        self._color = color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header de columna ──
        header = QWidget()
        header.setFixedHeight(36)
        header.setStyleSheet(f"""
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 #333333, stop:1 #292929
            );
            border-bottom: 1px solid #444;
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 0, 6, 0)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 13px; background: transparent; border: none;")
        header_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold; background: transparent; border: none;")
        header_layout.addWidget(title_label, stretch=1)

        self.badge_label = QLabel("0")
        self.badge_label.setFixedSize(24, 16)
        self.badge_label.setAlignment(Qt.AlignCenter)
        self._update_badge_style()
        header_layout.addWidget(self.badge_label)

        layout.addWidget(header)

        # ── Scroll area ──
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self.alerts_container = QWidget()
        self.alerts_container.setStyleSheet("background: transparent;")
        self.alerts_layout = QVBoxLayout(self.alerts_container)
        self.alerts_layout.setContentsMargins(2, 2, 2, 2)
        self.alerts_layout.setSpacing(3)
        self.alerts_layout.setAlignment(Qt.AlignTop)

        self.empty_label = QLabel("Sin alertas")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #555; font-size: 10px; padding: 20px; background: transparent; border: none;")
        self.alerts_layout.addWidget(self.empty_label)

        self.scroll_area.setWidget(self.alerts_container)
        layout.addWidget(self.scroll_area, stretch=1)

        # ── Footer ──
        self.count_label = QLabel("0")
        self.count_label.setFixedHeight(20)
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet("color: #666; font-size: 9px; background-color: #222; border-top: 1px solid #333;")
        layout.addWidget(self.count_label)

    def add_alert(self, alert_data: dict):
        if self.empty_label.isVisible():
            self.empty_label.hide()

        item = AlertItemWidget(alert_data)
        self.alerts_layout.insertWidget(0, item)
        self.alert_count += 1
        self._update_counters()

        while self.alerts_layout.count() > self.max_alerts + 1:
            idx = self.alerts_layout.count() - 1
            layout_item = self.alerts_layout.itemAt(idx)
            if layout_item and layout_item.widget() and layout_item.widget() != self.empty_label:
                w = layout_item.widget()
                self.alerts_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
                self.alert_count = max(0, self.alert_count - 1)

        self.scroll_area.verticalScrollBar().setValue(0)

    def clear(self):
        while self.alerts_layout.count():
            item = self.alerts_layout.takeAt(0)
            widget = item.widget()
            if widget and widget != self.empty_label:
                widget.setParent(None)
                widget.deleteLater()
        self.alert_count = 0
        self._update_counters()
        self.empty_label.show()
        self.alerts_layout.addWidget(self.empty_label)

    def _update_counters(self):
        self.count_label.setText(f"{self.alert_count}")
        self._update_badge_style()

    def _update_badge_style(self):
        self.badge_label.setText(str(min(self.alert_count, 999)))
        if self.alert_count == 0:
            bg = "#555"
        elif self.alert_count < 10:
            bg = "#e67e22"
        else:
            bg = "#e74c3c"
        self.badge_label.setStyleSheet(f"""
            background-color: {bg}; color: white; font-size: 9px;
            font-weight: bold; border-radius: 8px; border: none;
        """)


# ─────────────────────────────────────────────────────────────────────────────
# Panel lateral de alertas (2 columnas)
# ─────────────────────────────────────────────────────────────────────────────

# Clasificacion de la alerta en columna. Columna DERECHA = operacion (lo
# accionable para el personal: reponer, retirar cajas). El resto (conducta
# de cliente) va a la IZQUIERDA. Se decide por substrings del tipo/clase.
_OPERACION_KEYWORDS = (
    "anaquel", "vacio", "vacío", "caja", "obstru", "repon", "reposic",
    "empleado", "stock", "mercancia", "mercancía",
)


class AlertsSidebar(QWidget):
    """
    Sidebar de tienda con dos columnas:
      - IZQUIERDA 'Clientes y Productos': agarres, evaluacion, consulta de
        precio (conducta de compra).
      - DERECHA 'Operacion y Reposicion': anaqueles vacios, cajas en el
        piso, reposiciones (accionable para el personal).
    Cada columna tiene su propio scroll y contador.
    """

    new_alert = Signal(dict)

    def __init__(self, parent=None, title="Alertas Tienda", max_alerts=MAX_ALERTS,
                 auto_clear_after=AUTO_CLEAR_AFTER):
        super().__init__(parent)
        self.max_alerts = max_alerts
        # Tras recibir `auto_clear_after` alertas (total del sidebar) se vacía
        # solo. 0 lo desactiva.
        self.auto_clear_after = int(auto_clear_after)
        self._received_since_clear = 0
        self._title = title
        self._setup_ui()
        self.new_alert.connect(self.add_alert)

    def _setup_ui(self):
        self.setObjectName("AlertsSidebar")
        self.setFixedWidth(420)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            #AlertsSidebar {
                background-color: #1a1a1a;
            }
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Header global ──
        header = QWidget()
        header.setObjectName("AlertsSidebarHeader")
        header.setFixedHeight(38)
        header.setStyleSheet("""
            #AlertsSidebarHeader {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #333333, stop:1 #292929
                );
                border-bottom: 1px solid #444;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 10, 0)

        icon_label = QLabel("🔔")
        icon_label.setStyleSheet("font-size: 14px; background: transparent; border: none;")
        header_layout.addWidget(icon_label)

        title_label = QLabel(self._title)
        title_label.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; background: transparent; border: none;")
        header_layout.addWidget(title_label, stretch=1)

        self.badge_label = QLabel("0")
        self.badge_label.setFixedSize(28, 18)
        self.badge_label.setAlignment(Qt.AlignCenter)
        self.badge_label.setStyleSheet("""
            background-color: #555; color: white; font-size: 9px;
            font-weight: bold; border-radius: 9px; border: none;
        """)
        header_layout.addWidget(self.badge_label)

        btn_clear = QPushButton("Limpiar")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setFixedHeight(22)
        btn_clear.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888; font-size: 9px;
                border: 1px solid #555; border-radius: 3px;
                padding: 2px 6px;
            }
            QPushButton:hover { color: #fff; border-color: #888; }
        """)
        btn_clear.clicked.connect(self.clear_alerts)
        header_layout.addWidget(btn_clear)

        root_layout.addWidget(header)

        # ── Dos columnas ──
        columns_widget = QWidget()
        columns_widget.setStyleSheet("background: transparent;")
        columns_layout = QHBoxLayout(columns_widget)
        columns_layout.setContentsMargins(2, 2, 2, 2)
        columns_layout.setSpacing(3)

        self.col_order = _AlertColumn(
            title="Clientes y Productos",
            icon="🛒",
            color="#00c8ff",
            max_alerts=self.max_alerts,
        )
        self.col_delivery = _AlertColumn(
            title="Operacion y Reposicion",
            icon="🧑‍🔧",
            color="#f1c40f",
            max_alerts=self.max_alerts,
        )

        columns_layout.addWidget(self.col_order, stretch=1)

        # Separador vertical
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setStyleSheet("color: #444;")
        columns_layout.addWidget(separator)

        columns_layout.addWidget(self.col_delivery, stretch=1)

        root_layout.addWidget(columns_widget, stretch=1)

    # ─────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────

    @Slot(dict)
    def add_alert(self, alert_data: dict):
        """Clasifica la alerta y la envía a la columna correspondiente.

        DERECHA (operacion) si el tipo/clase menciona anaquel, caja,
        reposicion, etc.; IZQUIERDA (clientes/productos) en caso contrario.
        """
        combined = (f"{alert_data.get('event_type', '')} "
                    f"{alert_data.get('class_name', '')}").lower()
        if any(k in combined for k in _OPERACION_KEYWORDS):
            self.col_delivery.add_alert(alert_data)
        else:
            self.col_order.add_alert(alert_data)

        self._update_global_badge()

        # Auto-vaciado: al alcanzar el umbral de alertas RECIBIDAS, se limpia
        # todo el sidebar y arranca un nuevo ciclo. Evita que el historial
        # crezca sin control durante turnos largos.
        self._received_since_clear += 1
        if (self.auto_clear_after > 0
                and self._received_since_clear >= self.auto_clear_after):
            self.clear_alerts()

    @Slot()
    def clear_alerts(self):
        """Limpia ambas columnas y reinicia el contador del ciclo de
        auto-vaciado (tambien lo llama el boton 'Limpiar')."""
        self.col_order.clear()
        self.col_delivery.clear()
        self._received_since_clear = 0
        self._update_global_badge()

    def _update_global_badge(self):
        total = self.col_order.alert_count + self.col_delivery.alert_count
        self.badge_label.setText(str(min(total, 999)))
        if total == 0:
            bg = "#555"
        elif total < 10:
            bg = "#e67e22"
        else:
            bg = "#e74c3c"
        self.badge_label.setStyleSheet(f"""
            background-color: {bg}; color: white; font-size: 9px;
            font-weight: bold; border-radius: 9px; border: none;
        """)
