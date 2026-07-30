"""
AlertsSidebar – Panel lateral de alertas PERIMETRALES.

Dos columnas: VEHÍCULOS (izquierda) y PERSONAS (derecha). Cada alerta es una
tarjeta con: tipo de evento (Llegada/Salida/Intrusión/Escalamiento), la CLASE
(CARRO/MOTO/CAMIONETA/CAMIÓN — HOMBRE/MUJER/NIÑO), la HORA DE LLEGADA, la
HORA DE SALIDA y la PERMANENCIA en el sitio, más cámara e imagen si viene.

Contrato del payload de alerta (metadata.alerts[] del servidor):
{
  "event_type":   "llegada" | "salida" | "intrusion" | "escalamiento",
  "class_name":   "CARRO|MOTO|CAMIONETA|CAMIÓN|HOMBRE|MUJER|NIÑO|ADULTO|...",
  "global_id":    "G-0012",                 # identidad única entre cámaras
  "hora_llegada": epoch | iso,              # primera vez visto
  "hora_salida":  epoch | iso | null,       # al salir (null = sigue en sitio)
  "permanencia_s": float,                   # segundos en el sitio
  "camera_name":  str,  "camera_id": str,
  "description":  str (opcional),
  "timestamp":    epoch | iso (hora del evento),
  "image_base64" / "crop_image": b64 (opcional),
  "screenshot_path": str (opcional, abre el Explorador al hacer click)
}
Es tolerante con el formato antiguo (event_type/class_name/description).
"""

import base64
import os
import subprocess
import time
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QPushButton, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, Slot, QByteArray
from PySide6.QtGui import QPixmap

from gui.styles import tema


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
MAX_ALERTS = 200           # Máximo de alertas en el historial antes de descartar las más antiguas
CROP_THUMB_SIZE = 60       # Tamaño del thumbnail del crop en px

# Colores por tipo de evento perimetral
EVENT_COLORS = {
    "Llegada":       tema.EVENTO_LLEGADA,
    "Salida":        tema.EVENTO_SALIDA,
    "Intrusión":     tema.ERROR,
    "Escalamiento":  tema.AVISO,
    "Permanencia":   tema.ACENTO,
    "Merodeo":       tema.EVENTO_MERODEO,
    "Alerta":        tema.EVENTO_ALERTA,
}
DEFAULT_COLOR = tema.TEXTO_SUAVE

EVENT_ICONS = {
    "Llegada":      "▶",
    "Salida":       "◀",
    "Intrusión":    "⚠",
    "Escalamiento": "⏱",
    "Permanencia":  "⏱",
    "Merodeo":      "↻",
    "Alerta":       "⚠",
}

# Normalización de event_type entrante -> etiqueta mostrada
_EVENT_ALIASES = {
    "llegada": "Llegada", "entrada": "Llegada", "arrival": "Llegada",
    "salida": "Salida", "exit": "Salida",
    "intrusion": "Intrusión", "intrusión": "Intrusión",
    "escalamiento": "Escalamiento", "permanencia": "Permanencia",
    "merodeo": "Merodeo", "loitering": "Merodeo",
    "alerta": "Alerta",
}

# Icono por clase perimetral
CLASS_ICONS = {
    "CARRO": "🚗", "AUTO": "🚗", "MOTO": "🏍", "CAMIONETA": "🛻",
    "CAMION": "🚚", "CAMIÓN": "🚚", "BUS": "🚌",
    "VEHICULO": "🚙", "VEHÍCULO": "🚙", "OBJETO": "🎒",
    "HOMBRE": "👨", "MUJER": "👩", "NIÑO": "🧒", "NINO": "🧒",
    "ADULTO": "🧍", "PERSONA": "👤",
}

# Clases que van a cada columna (por prefijo, en mayúsculas)
_CLASES_PERSONA = ("HOMBRE", "MUJER", "NIÑO", "NINO", "ADULTO", "PERSONA")
_CLASES_VEHICULO = ("CARRO", "AUTO", "MOTO", "CAMIONETA", "CAMION", "CAMIÓN",
                    "BUS", "VEHICULO", "VEHÍCULO")


def _parse_dt(value):
    """epoch | iso | None -> datetime local o None (tolerante)."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        return datetime.fromtimestamp(float(value))
    except (ValueError, TypeError, OSError):
        return None


def _fmt_hora(value, vacio="—"):
    """epoch/iso -> 'HH:MM:SS' (o el marcador `vacio`)."""
    dt = _parse_dt(value)
    return dt.strftime("%H:%M:%S") if dt else vacio


def _fmt_permanencia(segundos):
    """Segundos -> 'X min Y s' / 'Y s' (o '' si no hay dato)."""
    try:
        s = int(float(segundos))
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def _norm_evento(event_type):
    """Normaliza el event_type entrante a la etiqueta mostrada."""
    clave = (event_type or "").strip().lower()
    return _EVENT_ALIASES.get(clave, (event_type or "Alerta").strip() or "Alerta")


# ─────────────────────────────────────────────────────────────────────────────
# Widget individual de alerta
# ─────────────────────────────────────────────────────────────────────────────

class AlertItemWidget(QFrame):
    """Tarjeta de una alerta perimetral (llegada/salida con tiempos)."""

    def __init__(self, alert_data: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("AlertItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Guardar ruta del screenshot para abrir al hacer click
        self._screenshot_path = alert_data.get("screenshot_path", "")

        evento      = _norm_evento(alert_data.get("event_type"))
        clase       = (alert_data.get("class_name") or "Desconocido").strip()
        global_id   = (alert_data.get("global_id") or "").strip()
        descripcion = alert_data.get("description", "")
        crop_b64    = alert_data.get("crop_image", "") or alert_data.get("image_base64", "")
        camara      = (alert_data.get("camera_name") or alert_data.get("camera_id") or "").strip()

        hora_llegada = alert_data.get("hora_llegada")
        hora_salida  = alert_data.get("hora_salida")
        permanencia  = alert_data.get("permanencia_s")
        ts_evento    = alert_data.get("timestamp", time.time())

        color = EVENT_COLORS.get(evento, DEFAULT_COLOR)
        icono_evento = EVENT_ICONS.get(evento, "●")
        icono_clase = CLASS_ICONS.get(clase.upper().split(" ")[0], "❔")

        # ── Estilo del frame ──
        self.setStyleSheet(f"""
            #AlertItem {{
                background-color: {tema.ELEVADO};
                border-left: 3px solid {color};
                border-radius: {tema.RADIO};
                margin: 2px 5px;
            }}
            #AlertItem:hover {{
                background-color: {tema.ELEVADO_HOVER};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── Thumbnail del crop ──
        if crop_b64:
            thumb_label = QLabel()
            thumb_label.setFixedSize(CROP_THUMB_SIZE, CROP_THUMB_SIZE)
            thumb_label.setStyleSheet(
                f"border-radius:{tema.RADIO_SM}; background-color:{tema.FONDO};"
                " border:none;")
            try:
                pixmap = QPixmap()
                raw = base64.b64decode(crop_b64)
                pixmap.loadFromData(QByteArray(raw))
                if not pixmap.isNull():
                    thumb_label.setPixmap(
                        pixmap.scaled(
                            CROP_THUMB_SIZE, CROP_THUMB_SIZE,
                            Qt.KeepAspectRatio, Qt.SmoothTransformation
                        )
                    )
                    thumb_label.setAlignment(Qt.AlignCenter)
            except Exception:
                thumb_label.setText("?")
                thumb_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(thumb_label)

        # ── Texto ──
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 0, 0, 0)

        # Cabecera: [icono evento] Evento — [icono clase] CLASE (G-XXXX)
        gid_txt = f"  ({global_id})" if global_id else ""
        header_label = QLabel(f"{icono_evento}  {evento} — {icono_clase} {clase}{gid_txt}")
        header_label.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: bold; "
            "background: transparent; border: none;")
        header_label.setWordWrap(True)
        text_layout.addWidget(header_label)

        # Tiempos: llegada / salida / permanencia
        filas_tiempo = []
        if hora_llegada not in (None, ""):
            filas_tiempo.append(f"Llegada: {_fmt_hora(hora_llegada)}")
        if hora_salida not in (None, ""):
            filas_tiempo.append(f"Salida: {_fmt_hora(hora_salida)}")
        elif hora_llegada not in (None, ""):
            filas_tiempo.append("Salida: — (en sitio)")
        perm_txt = _fmt_permanencia(permanencia)
        if perm_txt:
            filas_tiempo.append(f"Permanencia: {perm_txt}")
        if filas_tiempo:
            tiempos_label = QLabel("   ".join(filas_tiempo))
            tiempos_label.setStyleSheet(
                f"color:{tema.TEXTO_SUAVE}; font-size:10px;"
                " background:transparent; border:none;")
            tiempos_label.setWordWrap(True)
            text_layout.addWidget(tiempos_label)

        # Descripción opcional
        if descripcion:
            desc_label = QLabel(descripcion)
            desc_label.setStyleSheet(
                f"color:{tema.TEXTO_SUAVE}; font-size:10px;"
                " background:transparent; border:none;")
            desc_label.setWordWrap(True)
            text_layout.addWidget(desc_label)

        # Pie: hora del evento + cámara
        footer_parts = [f"🕐 {_fmt_hora(ts_evento, vacio='--:--:--')}"]
        if camara:
            footer_parts.append(f"📷 {camara[:28]}")
        footer_label = QLabel("  ".join(footer_parts))
        footer_label.setStyleSheet(
            f"color:{tema.TEXTO_TENUE}; font-size:10px;"
            " background:transparent; border:none;")
        text_layout.addWidget(footer_label)

        layout.addLayout(text_layout, stretch=1)

    def mouseReleaseEvent(self, event):
        """Al hacer click en la alerta, abre la carpeta que contiene el screenshot."""
        if event.button() == Qt.LeftButton and self._screenshot_path:
            path = self._screenshot_path
            if os.path.isfile(path):
                # Abre Explorer con el archivo seleccionado
                subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
            elif os.path.isdir(os.path.dirname(path)):
                # Si el archivo no existe pero la carpeta sí, abre la carpeta
                subprocess.Popen(['explorer', os.path.normpath(os.path.dirname(path))])
        super().mouseReleaseEvent(event)


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
        header.setStyleSheet(
            f"background-color:{tema.SUPERFICIE};"
            f" border-bottom:1px solid {tema.BORDE};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 0, 6, 0)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size:13px; background:transparent;"
                                 " border:none;")
        header_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"color:{color}; font-size:11px; font-weight:600;"
            " background:transparent; border:none; letter-spacing:0.3px;")
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
        self.scroll_area.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }")

        self.alerts_container = QWidget()
        self.alerts_container.setStyleSheet("background:transparent;")
        self.alerts_layout = QVBoxLayout(self.alerts_container)
        self.alerts_layout.setContentsMargins(2, 2, 2, 2)
        self.alerts_layout.setSpacing(3)
        self.alerts_layout.setAlignment(Qt.AlignTop)

        self.empty_label = QLabel("Sin detecciones todavía.\n\n"
                                  "Aparecerán aquí en cuanto el análisis\n"
                                  "esté activo.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            f"color:{tema.TEXTO_TENUE}; font-size:11px; padding:26px;"
            " background:transparent; border:none; line-height:150%;")
        self.alerts_layout.addWidget(self.empty_label)

        self.scroll_area.setWidget(self.alerts_container)
        layout.addWidget(self.scroll_area, stretch=1)

        # ── Footer ──
        self.count_label = QLabel("0")
        self.count_label.setFixedHeight(20)
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet(
            f"color:{tema.TEXTO_TENUE}; font-size:10px;"
            f" background-color:{tema.FONDO};"
            f" border-top:1px solid {tema.BORDE};")
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
            bg = tema.BORDE
        elif self.alert_count < 10:
            bg = tema.AVISO
        else:
            bg = tema.ERROR
        self.badge_label.setStyleSheet(f"""
            background-color: {bg}; color: white; font-size: 9px;
            font-weight: bold; border-radius: 8px; border: none;
        """)


# ─────────────────────────────────────────────────────────────────────────────
# Panel lateral de alertas (VEHÍCULOS | PERSONAS)
# ─────────────────────────────────────────────────────────────────────────────

class AlertsSidebar(QWidget):
    """
    Sidebar con dos columnas perimetrales: 'Vehículos' (izquierda) y
    'Personas' (derecha). Cada columna tiene su propio scroll y contador.
    """

    new_alert = Signal(dict)

    def __init__(self, parent=None, title="Alertas Perimetrales", max_alerts=MAX_ALERTS):
        super().__init__(parent)
        self.max_alerts = max_alerts
        self._title = title
        self._setup_ui()
        self.new_alert.connect(self.add_alert)

    def _setup_ui(self):
        self.setObjectName("AlertsSidebar")
        self.setFixedWidth(420)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#AlertsSidebar {{ background-color:{tema.SUPERFICIE}; }}")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Header global ──
        header = QWidget()
        header.setObjectName("AlertsSidebarHeader")
        header.setFixedHeight(38)
        header.setStyleSheet(f"""
            #AlertsSidebarHeader {{
                background-color: {tema.SUPERFICIE};
                border-bottom: 1px solid {tema.BORDE};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 10, 0)

        icon_label = QLabel("🔔")
        icon_label.setStyleSheet("font-size:14px; background:transparent;"
                                 " border:none;")
        header_layout.addWidget(icon_label)

        title_label = QLabel(self._title)
        title_label.setStyleSheet(
            f"color:{tema.TEXTO}; font-size:12px; font-weight:600;"
            " background:transparent; border:none; letter-spacing:0.3px;")
        header_layout.addWidget(title_label, stretch=1)

        self.badge_label = QLabel("0")
        self.badge_label.setFixedSize(28, 18)
        self.badge_label.setAlignment(Qt.AlignCenter)
        self.badge_label.setStyleSheet(
            f"background-color:{tema.ACENTO_FONDO}; color:{tema.ACENTO};"
            f" font-size:10px; font-weight:600; border-radius:9px;"
            f" border:1px solid {tema.BORDE};")
        header_layout.addWidget(self.badge_label)

        btn_clear = QPushButton("Limpiar")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setFixedHeight(22)
        btn_clear.setStyleSheet(tema.boton())
        btn_clear.clicked.connect(self.clear_alerts)
        header_layout.addWidget(btn_clear)

        root_layout.addWidget(header)

        # ── Dos columnas: Vehículos | Personas ──
        columns_widget = QWidget()
        columns_widget.setStyleSheet("background: transparent;")
        columns_layout = QHBoxLayout(columns_widget)
        columns_layout.setContentsMargins(2, 2, 2, 2)
        columns_layout.setSpacing(3)

        self.col_vehiculos = _AlertColumn(
            title="Vehículos",
            icon="🚗",
            color=tema.VEHICULO,
            max_alerts=self.max_alerts,
        )
        self.col_personas = _AlertColumn(
            title="Personas",
            icon="👤",
            color=tema.PERSONA,
            max_alerts=self.max_alerts,
        )

        columns_layout.addWidget(self.col_vehiculos, stretch=1)

        # Separador vertical
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setStyleSheet("color: #444;")
        columns_layout.addWidget(separator)

        columns_layout.addWidget(self.col_personas, stretch=1)

        root_layout.addWidget(columns_widget, stretch=1)

    # ─────────────────────────────────────────────────────────────
    # API pública
    # ─────────────────────────────────────────────────────────────

    @Slot(dict)
    def add_alert(self, alert_data: dict):
        """Enruta la alerta a la columna Vehículos o Personas según la clase."""
        clase = (alert_data.get("class_name") or "").strip().upper()
        gruesa = (alert_data.get("clase_gruesa") or "").strip().lower()

        # "objeto" (mochilas, bolsos, maletas) no se muestra: el servidor lo
        # agrupa con personas y ensuciaba la columna con detecciones que no
        # son ni una persona ni un vehiculo.
        if clase.startswith("OBJETO"):
            return

        if gruesa == "vehiculo" or clase.startswith(_CLASES_VEHICULO):
            self.col_vehiculos.add_alert(alert_data)
        elif gruesa == "persona" or clase.startswith(_CLASES_PERSONA):
            self.col_personas.add_alert(alert_data)
        else:
            # Sin clase reconocible: heurística por texto y, si no, Personas
            texto = f"{clase} {alert_data.get('event_type', '')}".upper()
            if any(v in texto for v in _CLASES_VEHICULO):
                self.col_vehiculos.add_alert(alert_data)
            else:
                self.col_personas.add_alert(alert_data)

        self._update_global_badge()

    @Slot()
    def clear_alerts(self):
        """Limpia ambas columnas."""
        self.col_vehiculos.clear()
        self.col_personas.clear()
        self._update_global_badge()

    def _update_global_badge(self):
        total = self.col_vehiculos.alert_count + self.col_personas.alert_count
        self.badge_label.setText(str(min(total, 999)))
        if total == 0:
            bg = tema.BORDE
        elif total < 10:
            bg = tema.AVISO
        else:
            bg = tema.ERROR
        self.badge_label.setStyleSheet(f"""
            background-color: {bg}; color: white; font-size: 9px;
            font-weight: bold; border-radius: 9px; border: none;
        """)
