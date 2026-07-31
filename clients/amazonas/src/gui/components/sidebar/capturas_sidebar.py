"""
capturas_sidebar.py - Panel lateral con las capturas en vivo.

Sustituye al antiguo "Alertas IA", que quedo permanentemente vacio: el
servidor ya no emite eventos de alerta para este pipeline (solo personas,
genero y edad), asi que aquel panel ocupaba 420 px sin mostrar nada.

Ahora muestra las ULTIMAS PERSONAS DETECTADAS segun van apareciendo, cada
una con su hora, genero y rango de edad. Lee directamente la carpeta
`capture/` que escribe el servidor, igual que la pestaña "Capturas": no
depende del WebSocket, asi que sigue mostrando el historial aunque la
conexion se caiga.

Click en una tarjeta abre la foto completa.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QPushButton, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QPixmap

from gui.styles import tema

# src/gui/components/sidebar/ -> raiz del cliente
_RAIZ = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "..", ".."))
CARPETA_CAPTURAS = os.path.join(_RAIZ, "capture")

MAX_TARJETAS = 60          # historial visible; el resto sigue en disco
INTERVALO_MS = 2000        # relectura de la carpeta
ANCHO_MINIATURA = 116

# Los mismos literales que interpreta el resto del sistema.
COLOR_GENERO = {"Hombre": tema.GENERO_HOMBRE, "Mujer": tema.GENERO_MUJER}
COLOR_SIN = tema.GENERO_SIN


def _hora_legible(marca: str) -> str:
    """'20260727_154210' -> '15:42:10'."""
    try:
        return datetime.strptime(str(marca), "%Y%m%d_%H%M%S").strftime(
            "%H:%M:%S")
    except (ValueError, TypeError):
        return ""


def _fecha_legible(marca: str) -> str:
    try:
        return datetime.strptime(str(marca), "%Y%m%d_%H%M%S").strftime("%d/%m")
    except (ValueError, TypeError):
        return ""


class _TarjetaCaptura(QFrame):
    """Una persona detectada: miniatura + genero, edad y hora."""

    def __init__(self, ruta_jpg: str, meta: Dict[str, Any],
                 pixmap: Optional[QPixmap], parent=None) -> None:
        super().__init__(parent)
        self._ruta = ruta_jpg
        self.setObjectName("TarjetaCaptura")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Click: abrir la foto\nClick derecho: ver en carpeta")

        genero = meta.get("gender")
        color = COLOR_GENERO.get(genero, COLOR_SIN)
        self.setStyleSheet(f"""
            #TarjetaCaptura {{
                background-color: {tema.ELEVADO};
                border-left: 3px solid {color};
                border-radius: {tema.RADIO};
                margin: 2px 5px;
            }}
            #TarjetaCaptura:hover {{
                background-color: {tema.ELEVADO_HOVER};
            }}
        """)

        fila = QHBoxLayout(self)
        fila.setContentsMargins(6, 6, 6, 6)
        fila.setSpacing(8)

        # ── Miniatura ──
        foto = QLabel()
        foto.setFixedWidth(ANCHO_MINIATURA)
        foto.setAlignment(Qt.AlignCenter)
        foto.setStyleSheet(f"background:{tema.FONDO}; border:none;"
                   f" border-radius:{tema.RADIO_SM};")
        if pixmap is not None and not pixmap.isNull():
            foto.setPixmap(pixmap)
        else:
            foto.setText("🖼")
        fila.addWidget(foto)

        # ── Texto ──
        texto = QVBoxLayout()
        texto.setSpacing(3)
        texto.setContentsMargins(0, 2, 0, 2)

        if genero:
            titulo = f"{genero}"
            edad = meta.get("age_range") or "edad desconocida"
        else:
            # Sin demografia: se dice POR QUE, en vez de dejarlo en blanco.
            titulo = "Sin identificar"
            motivo = str(meta.get("motivo_sin_demografia") or "")
            edad = {
                "sin_rostro": "no se le vio la cara",
                "rostro_muy_pequeno": "demasiado lejos",
                "calidad_insuficiente": "imagen no valida",
                "muestras_insuficientes": "paso muy rapido",
                "track_no_cerrado": "paso muy rapido",
                "sin_modelo_cargado": "estimador no disponible",
            }.get(motivo, "analizando…" if not motivo else motivo)

        etiqueta = QLabel(titulo)
        etiqueta.setStyleSheet(
            f"color:{color}; font-size:13px; font-weight:600;"
            " background:transparent; border:none;")
        texto.addWidget(etiqueta)

        sub = QLabel(edad)
        sub.setStyleSheet(f"color:{tema.TEXTO_SUAVE}; font-size:11px;"
                          " background:transparent; border:none;")
        sub.setWordWrap(True)
        texto.addWidget(sub)

        partes: List[str] = []
        hora = _hora_legible(meta.get("timestamp", ""))
        if hora:
            partes.append(f"🕐 {hora}")
        camara = meta.get("camera")
        if camara:
            partes.append(f"📷 {camara}")
        visitas = meta.get("visitas")
        if visitas and int(visitas) > 1:
            partes.append(f"↻ {int(visitas)}ª visita")
        pie = QLabel("  ".join(partes))
        pie.setStyleSheet(f"color:{tema.TEXTO_TENUE}; font-size:10px;"
                          " background:transparent; border:none;")
        texto.addWidget(pie)

        fila.addLayout(texto, stretch=1)

    def mouseReleaseEvent(self, event):
        try:
            if os.path.isfile(self._ruta):
                if event.button() == Qt.LeftButton:
                    os.startfile(self._ruta)
                elif event.button() == Qt.RightButton:
                    subprocess.Popen(
                        ["explorer", "/select,", os.path.normpath(self._ruta)])
        except Exception as exc:  # noqa: BLE001
            print(f"[Capturas] no se pudo abrir {self._ruta}: {exc}")
        super().mouseReleaseEvent(event)


class CapturasSidebar(QWidget):
    """Panel lateral: personas detectadas, en vivo."""

    def __init__(self, parent=None, title: str = "Personas detectadas",
                 socket_service=None) -> None:
        super().__init__(parent)
        self._titulo = title
        self._socket = socket_service
        self._firma: Optional[tuple] = None
        self._cache_pixmap: Dict[Tuple[str, int], QPixmap] = {}
        os.makedirs(CARPETA_CAPTURAS, exist_ok=True)
        self._construir()
        self._temporizador = QTimer(self)
        self._temporizador.timeout.connect(self.refrescar)
        self._temporizador.start(INTERVALO_MS)
        self.refrescar(forzar=True)

    # ── Interfaz ────────────────────────────────────────────────────────

    def _construir(self) -> None:
        self.setObjectName("CapturasSidebar")
        self.setFixedWidth(420)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#CapturasSidebar {{ background-color:{tema.SUPERFICIE}; }}")

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        cabecera = QWidget()
        cabecera.setObjectName("CabeceraCapturas")
        cabecera.setFixedHeight(38)
        cabecera.setStyleSheet(f"""
            #CabeceraCapturas {{
                background-color: {tema.SUPERFICIE};
                border-bottom: 1px solid {tema.BORDE};
            }}
        """)
        fila = QHBoxLayout(cabecera)
        fila.setContentsMargins(10, 0, 10, 0)

        icono = QLabel("👤")
        icono.setStyleSheet("font-size:14px; background:transparent;"
                            " border:none;")
        fila.addWidget(icono)

        titulo = QLabel(self._titulo)
        titulo.setStyleSheet(f"color:{tema.TEXTO}; font-size:12px;"
                             " font-weight:600; background:transparent;"
                             " border:none; letter-spacing:0.2px;")
        fila.addWidget(titulo, stretch=1)

        self.contador = QLabel("0")
        self.contador.setFixedHeight(18)
        self.contador.setAlignment(Qt.AlignCenter)
        self.contador.setStyleSheet(
            f"background-color:{tema.ACENTO_FONDO}; color:{tema.ACENTO};"
            f" font-size:10px; font-weight:600; border-radius:9px;"
            f" border:1px solid {tema.BORDE}; padding:0 9px;")
        fila.addWidget(self.contador)

        boton = QPushButton("📁")
        boton.setCursor(Qt.PointingHandCursor)
        boton.setFixedSize(26, 22)
        boton.setToolTip("Abrir la carpeta de capturas")
        boton.setStyleSheet(tema.boton())
        boton.clicked.connect(self._abrir_carpeta)
        fila.addWidget(boton)

        self.btn_vaciar = QPushButton("🗑")
        self.btn_vaciar.setCursor(Qt.PointingHandCursor)
        self.btn_vaciar.setFixedSize(26, 22)
        self.btn_vaciar.setToolTip(
            "Vaciar TODAS las detecciones.\n"
            "Se apartan a output/papelera/, son recuperables.")
        self.btn_vaciar.setStyleSheet(tema.boton())
        self.btn_vaciar.clicked.connect(self._vaciar_detecciones)
        fila.addWidget(self.btn_vaciar)
        raiz.addWidget(cabecera)

        raiz.addWidget(self._construir_totales())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }")
        self._contenedor = QWidget()
        self._contenedor.setStyleSheet("background:transparent;")
        self.lista = QVBoxLayout(self._contenedor)
        self.lista.setContentsMargins(2, 2, 2, 2)
        self.lista.setSpacing(3)
        self.lista.setAlignment(Qt.AlignTop)

        self.vacio = QLabel("Todavía no se ha detectado a nadie.\n\n"
                            "Cuando el análisis esté activo, cada persona\n"
                            "aparecerá aquí con su género y edad.")
        self.vacio.setAlignment(Qt.AlignCenter)
        self.vacio.setStyleSheet(f"color:{tema.TEXTO_TENUE}; font-size:11px;"
                                 " padding:26px; background:transparent;"
                                 " border:none; line-height:150%;")
        self.lista.addWidget(self.vacio)

        self.scroll.setWidget(self._contenedor)
        raiz.addWidget(self.scroll, stretch=1)

    def _construir_totales(self) -> QWidget:
        """Franja con el reparto por genero sobre TODO el historico.

        Cuenta la carpeta entera, no solo las tarjetas visibles: la lista
        se limita a las mas recientes, pero el total debe ser el real.
        """
        barra = QWidget()
        barra.setObjectName("TotalesCapturas")
        barra.setFixedHeight(30)
        barra.setStyleSheet(f"""
            #TotalesCapturas {{
                background-color: {tema.FONDO};
                border-bottom: 1px solid {tema.BORDE};
            }}
        """)
        fila = QHBoxLayout(barra)
        fila.setContentsMargins(10, 0, 10, 0)
        fila.setSpacing(4)

        def _etiqueta(color: str, negrita: bool = True) -> QLabel:
            lbl = QLabel("0")
            lbl.setStyleSheet(
                f"color:{color}; font-size:11px;"
                f" font-weight:{'700' if negrita else '400'};"
                " background:transparent; border:none;")
            return lbl

        self.lbl_mujeres = _etiqueta(tema.GENERO_MUJER)
        self.lbl_hombres = _etiqueta(tema.GENERO_HOMBRE)
        self.lbl_total = _etiqueta(tema.TEXTO)

        for texto, valor, color in (
                ("Mujeres:", self.lbl_mujeres, tema.GENERO_MUJER),
                ("Hombres:", self.lbl_hombres, tema.GENERO_HOMBRE),
                ("Total:", self.lbl_total, tema.TEXTO_SUAVE)):
            rotulo = QLabel(texto)
            rotulo.setStyleSheet(f"color:{color}; font-size:11px;"
                                 " background:transparent; border:none;")
            fila.addWidget(rotulo)
            fila.addWidget(valor)
            fila.addSpacing(8)
        fila.addStretch(1)
        return barra

    def _contar_generos(self) -> Tuple[int, int]:
        """(mujeres, hombres) en TODA la carpeta de capturas."""
        mujeres = hombres = 0
        try:
            nombres = os.listdir(CARPETA_CAPTURAS)
        except OSError:
            return 0, 0
        for nombre in nombres:
            if not nombre.endswith(".json"):
                continue
            try:
                with open(os.path.join(CARPETA_CAPTURAS, nombre),
                          encoding="utf-8") as fichero:
                    genero = (json.load(fichero) or {}).get("gender")
            except (OSError, json.JSONDecodeError):
                continue
            if genero == "Mujer":
                mujeres += 1
            elif genero == "Hombre":
                hombres += 1
        return mujeres, hombres

    # ── Datos ───────────────────────────────────────────────────────────

    def _leer_capturas(self) -> List[Tuple[str, Dict[str, Any], float]]:
        """Las capturas mas recientes primero."""
        try:
            nombres = [n for n in os.listdir(CARPETA_CAPTURAS)
                       if n.endswith(".jpg") and not n.endswith(".tmp.jpg")]
        except OSError:
            return []
        nombres.sort(reverse=True)          # el nombre empieza por fecha-hora
        salida = []
        for nombre in nombres[:MAX_TARJETAS]:
            jpg = os.path.join(CARPETA_CAPTURAS, nombre)
            try:
                mtime = os.path.getmtime(jpg)
            except OSError:
                continue
            meta: Dict[str, Any] = {}
            sidecar = os.path.join(CARPETA_CAPTURAS, nombre[:-4] + ".json")
            if os.path.isfile(sidecar):
                try:
                    with open(sidecar, encoding="utf-8") as fichero:
                        meta = json.load(fichero) or {}
                except (OSError, json.JSONDecodeError):
                    meta = {}
            salida.append((jpg, meta, mtime))
        return salida

    def _miniatura(self, jpg: str, mtime: float) -> Optional[QPixmap]:
        clave = (os.path.basename(jpg), int(mtime))
        pixmap = self._cache_pixmap.get(clave)
        if pixmap is None:
            original = QPixmap(jpg)
            pixmap = (original.scaled(ANCHO_MINIATURA, 150, Qt.KeepAspectRatio,
                                      Qt.SmoothTransformation)
                      if not original.isNull() else original)
            self._cache_pixmap[clave] = pixmap
            if len(self._cache_pixmap) > MAX_TARJETAS * 3:
                for k in list(self._cache_pixmap)[:MAX_TARJETAS]:
                    self._cache_pixmap.pop(k, None)
        return pixmap

    @Slot()
    def refrescar(self, forzar: bool = False) -> None:
        """Relee la carpeta y repinta solo si algo cambio."""
        capturas = self._leer_capturas()
        firma = tuple((os.path.basename(j), int(m), bool(meta.get("gender")))
                      for j, meta, m in capturas)
        if not forzar and firma == self._firma:
            return
        self._firma = firma

        while self.lista.count():
            item = self.lista.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.vacio:
                widget.setParent(None)
                widget.deleteLater()
        self.vacio.setParent(None)

        self.contador.setText(str(len(capturas)))
        mujeres, hombres = self._contar_generos()
        self.lbl_mujeres.setText(str(mujeres))
        self.lbl_hombres.setText(str(hombres))
        self.lbl_total.setText(str(mujeres + hombres))
        if not capturas:
            self.lista.addWidget(self.vacio)
            self.vacio.show()
            return

        for jpg, meta, mtime in capturas:
            self.lista.addWidget(
                _TarjetaCaptura(jpg, meta, self._miniatura(jpg, mtime)))

    def _vaciar_detecciones(self) -> None:
        """Borra todo el historico de detecciones, con doble confirmacion.

        El trabajo lo hace el SERVIDOR (es quien tiene las carpetas de
        capturas, la galeria del Re-ID y los mapas de calor); aqui solo se
        pregunta y se refresca.
        """
        from PySide6.QtWidgets import QMessageBox

        primera = QMessageBox(self)
        primera.setIcon(QMessageBox.Warning)
        primera.setWindowTitle("Vaciar detecciones")
        primera.setText("Se van a vaciar TODAS las detecciones.")
        primera.setInformativeText(
            "Incluye:\n"
            "  • todas las capturas (cliente y servidor)\n"
            "  • la galería de identidades del Re-ID\n"
            "  • los mapas de calor\n\n"
            "No se destruyen: se mueven a output/papelera/ con la fecha, "
            "por si hiciera falta recuperarlas.")
        primera.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        primera.setDefaultButton(QMessageBox.Cancel)
        primera.button(QMessageBox.Yes).setText("Vaciar")
        if primera.exec() != QMessageBox.Yes:
            return

        import json as _json
        import urllib.error
        import urllib.request

        self.btn_vaciar.setEnabled(False)
        try:
            peticion = urllib.request.Request(
                f"{self._servidor_http()}/dashboard/api/"
                "vaciar-detecciones?confirmar=true", method="POST")
            with urllib.request.urlopen(peticion, timeout=30) as respuesta:
                datos = _json.loads(respuesta.read().decode("utf-8"))
            total = datos.get("total", 0)
            papelera = datos.get("papelera", "")
            print(f"[Capturas] vaciado: {datos.get('borrados')} -> {papelera}")
            QMessageBox.information(
                self, "Hecho",
                f"Se apartaron {total} archivos.\n\n"
                f"Copia de seguridad en:\n{papelera}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "No se pudo vaciar",
                f"No se pudo contactar con el servidor.\n\n{exc}")
            print(f"[Capturas] no se pudo vaciar: {exc}")
        finally:
            self.btn_vaciar.setEnabled(True)
            self._cache_pixmap.clear()
            self.refrescar(forzar=True)

    def _servidor_http(self) -> str:
        """URL base del servidor, derivada de la del websocket (en el nucleo,
        sin fallback silencioso a localhost: criterio de H-02)."""
        from elde_core.ui.panel_capturas import base_http_del_websocket
        return base_http_del_websocket(
            getattr(getattr(self, "_socket", None), "url", "") or "")

    def _abrir_carpeta(self) -> None:
        try:
            os.startfile(CARPETA_CAPTURAS)
        except Exception as exc:  # noqa: BLE001
            print(f"[Capturas] no se pudo abrir la carpeta: {exc}")
