"""
CapturesPanel (Amazonas View) — Pestaña "Capturas".

Galería de las fotos que el servidor guarda ANOTADAS (género / rango de
edad / cámara / fecha) en la carpeta `capture/` del cliente. Cada tarjeta
es clickeable: click izquierdo abre la foto; el botón 📁 abre la carpeta.
Incluye filtro por género, contador y acceso directo al dashboard web
del servidor (http://<host>:9000/dashboard).

La carpeta se re-escanea sola (timer, 2 s): no hace falta refrescar.
"""

import json
import os
import subprocess
import webbrowser
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QScrollArea,
    QPushButton, QFrame, QComboBox, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap

from gui.styles import tema

# Raíz del cliente: src/gui/components/captures_panel.py -> ../../..
_CLIENT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', '..', '..'))
CAPTURE_DIR = os.path.join(_CLIENT_ROOT, 'capture')

THUMB_W = 190          # ancho de cada tarjeta
THUMB_IMG_H = 210      # alto máx. de la imagen
MAX_ITEMS = 200        # más recientes en pantalla (el resto sigue en disco)
REFRESH_MS = 2000

GENDER_COLORS = {"Hombre": tema.GENERO_HOMBRE,
                 "Mujer": tema.GENERO_MUJER}


def _fmt_ts(ts: str) -> str:
    """'20260727_154210' -> '27/07 15:42:10' (defensivo)."""
    try:
        return datetime.strptime(str(ts), "%Y%m%d_%H%M%S")\
            .strftime("%d/%m %H:%M:%S")
    except Exception:
        return str(ts or "")


class _CaptureCard(QFrame):
    """Tarjeta de una captura. Click izquierdo = abrir la foto."""

    def __init__(self, jpg_path: str, meta: dict, pixmap: QPixmap,
                 parent=None):
        super().__init__(parent)
        self._path = jpg_path
        self.setObjectName("CaptureCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(THUMB_W)
        self.setToolTip("Click: abrir la foto\nClick derecho: "
                        "mostrar en la carpeta")
        self.setStyleSheet(f"""
            #CaptureCard {{
                background-color: {tema.ELEVADO};
                border: 1px solid {tema.BORDE};
                border-radius: {tema.RADIO};
            }}
            #CaptureCard:hover {{
                border-color: {tema.ACENTO};
                background-color: {tema.ELEVADO_HOVER};
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 6)
        lay.setSpacing(4)

        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet(f"background:{tema.FONDO}; border:none;"
                  f" border-radius:{tema.RADIO_SM};")
        if pixmap is not None and not pixmap.isNull():
            img.setPixmap(pixmap)
        else:
            img.setText("🖼")
        lay.addWidget(img)

        gender = meta.get('gender')
        age = meta.get('age_range')
        if gender:
            color = GENDER_COLORS.get(gender, "#cccccc")
            linea1 = f"{gender} · {age or '—'}"
            # Rescatada por el VLM sobre un recorte que la geometria daba
            # por perdido: se marca para no confundirla con una lectura
            # respaldada por MiVOLO.
            if meta.get('origen_demografia') == 'vlm_rescate':
                linea1 += "  🤖"
        elif meta.get('no_es_persona'):
            # Caso cerrado: el VLM miro la foto y no hay nadie. Dejarlo en
            # "Analizando…" daba a entender que seguia pendiente.
            color = tema.TEXTO_TENUE
            linea1 = "No es una persona"
        elif meta.get('revisado_por_vlm'):
            # Hay alguien, pero ni MiVOLO ni el VLM lo distinguen.
            color = tema.TEXTO_TENUE
            linea1 = "Persona sin identificar"
        else:
            color = tema.GENERO_SIN
            linea1 = "Analizando…"
        l1 = QLabel(linea1)
        l1.setStyleSheet(f"color:{color}; font-size:11px; font-weight:600;"
                         " background:transparent; border:none;")
        l1.setWordWrap(True)
        lay.addWidget(l1)

        partes = [_fmt_ts(meta.get('timestamp', ''))]
        cam = meta.get('camera')
        if cam:
            partes.append(f"📷 {cam}")
        visitas = meta.get('visitas')
        if visitas and int(visitas) > 1:
            partes.append(f"{int(visitas)} visitas")
        l2 = QLabel("  ".join(p for p in partes if p))
        l2.setStyleSheet(f"color:{tema.TEXTO_TENUE}; font-size:10px;"
                         " background:transparent; border:none;")
        l2.setWordWrap(True)
        lay.addWidget(l2)

    def mouseReleaseEvent(self, event):
        try:
            if os.path.isfile(self._path):
                if event.button() == Qt.LeftButton:
                    os.startfile(self._path)          # abre la foto
                elif event.button() == Qt.RightButton:
                    subprocess.Popen(
                        ['explorer', '/select,', os.path.normpath(self._path)])
        except Exception as e:
            print(f"[Capturas] no se pudo abrir {self._path}: {e}")
        super().mouseReleaseEvent(event)


class CapturesPanel(QWidget):
    """Pestaña 'Capturas': galería viva de la carpeta capture/."""

    def __init__(self, parent=None, socket_service=None):
        super().__init__(parent)
        self._socket = socket_service
        self._signature = None      # (archivos, mtimes) del último render
        self._pix_cache = {}        # (nombre, mtime) -> QPixmap escalado
        self._filter = "Todos"
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        self._setup_ui()
        self._vlm_activo = True          # por defecto encendido
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self.refresh(force=True)
        # El estado real lo tiene el servidor: se consulta al abrir.
        QTimer.singleShot(600, self._consultar_vlm)

    # ── UI ────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setObjectName("CapturesPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#CapturesPanel {{ background-color:{tema.FONDO}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──
        header = QWidget()
        header.setFixedHeight(42)
        header.setStyleSheet(f"background-color:{tema.SUPERFICIE};"
                             f" border-bottom:1px solid {tema.BORDE};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 0, 10, 0)
        hl.setSpacing(8)

        titulo = QLabel("📸 Capturas de personas")
        titulo.setStyleSheet(f"color:{tema.TEXTO}; font-size:13px;"
                             " font-weight:600; background:transparent;"
                             " border:none;")
        # Que el titulo ceda el ancho antes que los botones: si no, en
        # pantallas estrechas empujaba los botones fuera de la vista.
        titulo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        titulo.setMinimumWidth(0)
        hl.addWidget(titulo)

        self.lbl_count = QLabel("0 fotos")
        self.lbl_count.setStyleSheet(f"color:{tema.TEXTO_SUAVE};"
                                     " font-size:11px; background:transparent;"
                                     " border:none;")
        hl.addWidget(self.lbl_count)
        hl.addStretch(1)

        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(["Todos", "Hombre", "Mujer",
                                  "Sin clasificar"])
        self.cmb_filter.setFixedHeight(26)
        self.cmb_filter.setMinimumWidth(80)
        # El estilo del desplegable viene del QSS global.
        self.cmb_filter.currentTextChanged.connect(self._on_filter)
        hl.addWidget(self.cmb_filter)

        # Los botones guardan su texto corto (solo icono) para poder
        # encogerse cuando la ventana es estrecha. Antes la cabecera
        # exigia ~1340 px y en pantallas menores los botones de la
        # derecha —el de VLM entre ellos— quedaban fuera de la vista.
        self._botones_cabecera: list[QPushButton] = []

        def _btn(texto, tooltip, handler, corto=""):
            b = QPushButton(texto)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(26)
            # Sin este minimo la cabecera no podia encogerse y la
            # compactacion no llegaba a activarse nunca.
            b.setMinimumWidth(34)
            b.setToolTip(tooltip or texto)
            b.setStyleSheet(tema.boton())
            b.clicked.connect(handler)
            b.setProperty("texto_largo", texto)
            b.setProperty("texto_corto", corto or texto.split(" ")[0])
            self._botones_cabecera.append(b)
            return b

        self.btn_vlm = _btn("🤖 VLM",
                            "Enciende o apaga el VLM que da una segunda\n"
                            "opinión sobre género y edad en las fotos dudosas.",
                            self._alternar_vlm, corto="🤖")
        self.btn_vlm.setCheckable(True)
        hl.addWidget(self.btn_vlm)

        self.btn_analizar = _btn(
            "🧠 Analizar pendientes",
            "Vuelve a analizar en el servidor las fotos que quedaron sin\n"
            "género. Al no ser en vivo puede gastar más cómputo por foto.",
            self._analizar_pendientes, corto="🧠")
        hl.addWidget(self.btn_analizar)
        hl.addWidget(_btn("📁 Carpeta", "Abrir la carpeta de capturas",
                          self._open_folder, corto="📁"))
        hl.addWidget(_btn("📊 Dashboard",
                          "Abrir el dashboard web de analitica",
                          self._open_dashboard, corto="📊"))
        hl.addWidget(_btn("🗑 Vaciar",
                          "Borrar TODAS las detecciones. No se puede deshacer.",
                          self._vaciar_detecciones, corto="🗑"))
        root.addWidget(header)

        # ── Grid con scroll ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }")
        self._grid_host = QWidget()
        self._grid_host.setStyleSheet("background:transparent;")
        self.grid = QGridLayout(self._grid_host)
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self._grid_host)
        root.addWidget(self.scroll, stretch=1)

        self.lbl_empty = QLabel(
            "Sin capturas todavía.\nCuando el servidor detecte personas, "
            "sus fotos con género y edad aparecerán aquí.")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        self.lbl_empty.setStyleSheet(f"color:{tema.TEXTO_TENUE};"
                                     " font-size:12px; background:transparent;"
                                     " border:none; line-height:150%;")
        self.grid.addWidget(self.lbl_empty, 0, 0)

    # ── Acciones ──────────────────────────────────────────────────

    def _servidor_http(self) -> str:
        """URL base del servidor, derivada de la del websocket."""
        host = "127.0.0.1:9000"
        try:
            url = getattr(self._socket, 'url', '') or ''
            if '://' in url:
                host = url.split('://', 1)[1].split('/', 1)[0] or host
        except Exception:
            pass
        return f"http://{host}"

    def resizeEvent(self, event):
        """Compacta la cabecera cuando la ventana no da para los textos."""
        super().resizeEvent(event)
        self._ajustar_cabecera()

    def _ajustar_cabecera(self):
        """Alterna los botones entre texto completo y solo icono.

        Se calcula con el ancho que piden los propios botones, asi que
        sigue valiendo aunque cambien sus etiquetas (el de VLM cambia a
        "VLM 3b: ON" al conectar con el servidor).
        """
        botones = getattr(self, "_botones_cabecera", None)
        if not botones:
            return
        # Ancho reservado al titulo, al contador y al desplegable.
        fijo = 260
        holgura = sum(b.sizeHint().width() for b in botones) + fijo
        compacto = self.width() < holgura
        if compacto == getattr(self, "_cabecera_compacta", None):
            return
        self._cabecera_compacta = compacto
        for b in botones:
            largo = b.property("texto_largo")
            corto = b.property("texto_corto")
            b.setText(corto if compacto else largo)

    def _pintar_vlm(self, activo: bool, modelo: str = ""):
        """Refleja el estado del VLM en el boton."""
        self._vlm_activo = bool(activo)
        self.btn_vlm.setChecked(bool(activo))
        sufijo = f" {modelo}" if modelo else ""
        etiqueta = f"🤖 VLM{sufijo}: {'ON' if activo else 'OFF'}"
        # `texto_largo` es lo que repone `_ajustar_cabecera`: si no se
        # actualiza aqui, vuelve a poner el rotulo inicial y el estado
        # ON/OFF no llega a verse nunca.
        self.btn_vlm.setProperty("texto_largo", etiqueta)
        # En ventanas estrechas se ve solo el icono: que lleve el estado.
        self.btn_vlm.setProperty("texto_corto", "🤖ON" if activo else "🤖OFF")
        self.btn_vlm.setText(etiqueta)
        self.btn_vlm.setToolTip(
            "El VLM da una segunda opinión en las fotos donde el modelo\n"
            "principal duda. Es más lento (~2 s por foto) pero resuelve\n"
            "casos difíciles.\n\nPulsa para "
            + ("desactivarlo." if activo else "activarlo."))
        # Verde cuando está encendido; el resto lo pone el tema.
        self._cabecera_compacta = None      # fuerza recalculo
        self._ajustar_cabecera()
        self.btn_vlm.setStyleSheet(
            tema.boton() + (f"QPushButton {{ color:{tema.EXITO};"
                            f" border-color:{tema.EXITO}; }}"
                            if activo else ""))

    def _consultar_vlm(self):
        """Lee del servidor si el VLM esta activo (al abrir la pestana)."""
        import json as _json
        import urllib.request
        import urllib.error
        try:
            with urllib.request.urlopen(
                    f"{self._servidor_http()}/dashboard/api/vlm",
                    timeout=6) as respuesta:
                datos = _json.loads(respuesta.read().decode("utf-8"))
            self._pintar_vlm(datos.get("activo", True), datos.get("modelo", ""))
        except (urllib.error.URLError, OSError, ValueError):
            # Sin servidor no se puede saber: se muestra el valor por
            # defecto (encendido) sin dar el estado por confirmado.
            self._pintar_vlm(True)
            self.btn_vlm.setToolTip("Sin conexión con el servidor.")

    def _alternar_vlm(self):
        """Enciende/apaga el VLM en el servidor."""
        import json as _json
        import urllib.request
        import urllib.error
        nuevo = not getattr(self, "_vlm_activo", True)
        try:
            peticion = urllib.request.Request(
                f"{self._servidor_http()}/dashboard/api/vlm?activo="
                f"{'true' if nuevo else 'false'}", method="POST")
            with urllib.request.urlopen(peticion, timeout=8) as respuesta:
                datos = _json.loads(respuesta.read().decode("utf-8"))
            self._pintar_vlm(datos.get("activo", nuevo))
            self.lbl_count.setText(datos.get("mensaje", ""))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # No se cambio nada: se deja el boton como estaba.
            self._pintar_vlm(getattr(self, "_vlm_activo", True))
            self.lbl_count.setText("No se pudo contactar con el servidor")
            print(f"[Capturas] no se pudo cambiar el VLM: {exc}")

    def analizar_tras_video(self, nombre_video: str = ""):
        """Repasa con el VLM lo que dejo pendiente un video recien analizado.

        El servidor va guardando las capturas mientras corre el video,
        pero muchas quedan sin genero hasta que el reanalisis las repasa.
        Lanzarlo solo evita que el usuario tenga que venir a pulsarlo.
        """
        if getattr(self, "_timer_analisis", None) is not None \
                and self._timer_analisis.isActive():
            return                      # ya hay un repaso en marcha
        if nombre_video:
            self.lbl_count.setText(
                f"Video «{nombre_video[:34]}» terminado · repasando con IA…")
        self._analizar_pendientes()

    def _analizar_pendientes(self):
        """Pide al servidor que reanalice las capturas sin genero.

        El trabajo ocurre EN EL SERVIDOR (es quien tiene los modelos y la
        GPU); aqui solo se lanza y se informa. El panel se refresca solo,
        asi que los resultados van apareciendo segun se resuelven.
        """
        import json as _json
        import urllib.request
        import urllib.error
        self.btn_analizar.setEnabled(False)
        self.btn_analizar.setText("🧠 Analizando…")
        try:
            peticion = urllib.request.Request(
                f"{self._servidor_http()}/dashboard/api/analizar-pendientes",
                method="POST")
            with urllib.request.urlopen(peticion, timeout=10) as respuesta:
                datos = _json.loads(respuesta.read().decode("utf-8"))
            mensaje = datos.get("mensaje", "")
            print(f"[Capturas] {mensaje}")
            self.lbl_count.setText(mensaje[:60])
            # Sondear el progreso hasta que termine.
            self._timer_analisis = QTimer(self)
            self._timer_analisis.timeout.connect(self._consultar_analisis)
            self._timer_analisis.start(1500)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.btn_analizar.setEnabled(True)
            self.btn_analizar.setText("🧠 Analizar pendientes")
            self.lbl_count.setText("No se pudo contactar con el servidor")
            print(f"[Capturas] no se pudo lanzar el analisis: {exc}")

    def _consultar_analisis(self):
        """Sondea el progreso del reanalisis en el servidor."""
        import json as _json
        import urllib.request
        import urllib.error
        try:
            with urllib.request.urlopen(
                    f"{self._servidor_http()}/dashboard/api/analisis-estado",
                    timeout=8) as respuesta:
                estado = _json.loads(
                    respuesta.read().decode("utf-8")).get("analisis", {})
        except (urllib.error.URLError, OSError, ValueError):
            self._detener_sondeo("Sin conexion con el servidor")
            return
        if estado.get("ejecutando"):
            self.btn_analizar.setText(
                f"🧠 {estado.get('procesadas', 0)}/{estado.get('total', 0)}")
            self.lbl_count.setText(
                f"Analizando… {estado.get('resueltas', 0)} resueltas")
        else:
            resueltas = estado.get("resueltas", 0)
            total = estado.get("total", 0)
            self._detener_sondeo(
                f"Analisis terminado: {resueltas} de {total} resueltas"
                if total else "Sin capturas pendientes")
            self.refresh(force=True)

    def _detener_sondeo(self, mensaje: str):
        temporizador = getattr(self, "_timer_analisis", None)
        if temporizador is not None:
            temporizador.stop()
            self._timer_analisis = None
        self.btn_analizar.setEnabled(True)
        self.btn_analizar.setText("🧠 Analizar pendientes")
        self.lbl_count.setText(mensaje)

    def _vaciar_detecciones(self):
        """Delega en el mismo dialogo de confirmacion del panel lateral.

        La logica vive en `CapturasSidebar` para no duplicarla; aqui solo
        se reutiliza con el servidor de este panel.
        """
        from gui.components.sidebar.capturas_sidebar import CapturasSidebar
        CapturasSidebar._vaciar_detecciones(self)

    def _open_folder(self):
        try:
            os.startfile(CAPTURE_DIR)
        except Exception as e:
            print(f"[Capturas] no se pudo abrir la carpeta: {e}")

    def _open_dashboard(self):
        """Abre el dashboard del servidor. Deriva el host de la URL del
        websocket (ws://host:9000/ws -> http://host:9000/dashboard)."""
        host = "127.0.0.1:9000"
        try:
            url = getattr(self._socket, 'url', '') or ''
            if '://' in url:
                host = url.split('://', 1)[1].split('/', 1)[0] or host
        except Exception:
            pass
        webbrowser.open(f"http://{host}/dashboard")

    def _on_filter(self, texto):
        self._filter = texto
        self.refresh(force=True)

    # ── Escaneo / render ──────────────────────────────────────────

    def _scan(self):
        """[(jpg_path, meta, mtime)] más recientes primero (máx MAX_ITEMS)."""
        try:
            nombres = [f for f in os.listdir(CAPTURE_DIR)
                       if f.endswith('.jpg') and not f.endswith('.tmp.jpg')]
        except Exception:
            return []
        nombres.sort(reverse=True)   # stem = YYYYMMDD_HHMMSS_... => cronológico
        items = []
        for fn in nombres[:MAX_ITEMS]:
            jpg = os.path.join(CAPTURE_DIR, fn)
            try:
                mtime = os.path.getmtime(jpg)
            except OSError:
                continue
            meta = {}
            jpath = os.path.join(CAPTURE_DIR, fn[:-4] + '.json')
            if os.path.isfile(jpath):
                try:
                    with open(jpath, encoding='utf-8') as f:
                        meta = json.load(f) or {}
                except Exception:
                    meta = {}
            items.append((jpg, meta, mtime))
        return items

    def _pasa_filtro(self, meta: dict) -> bool:
        if self._filter == "Todos":
            return True
        g = meta.get('gender')
        if self._filter == "Sin clasificar":
            return g in (None, '', 'Desconocido')
        return g == self._filter

    def _pixmap(self, jpg, mtime):
        key = (os.path.basename(jpg), int(mtime))
        pix = self._pix_cache.get(key)
        if pix is None:
            raw = QPixmap(jpg)
            pix = (raw.scaled(THUMB_W - 10, THUMB_IMG_H,
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
                   if not raw.isNull() else raw)
            self._pix_cache[key] = pix
            if len(self._pix_cache) > MAX_ITEMS * 3:
                self._pix_cache = dict(
                    list(self._pix_cache.items())[-MAX_ITEMS:])
        return pix

    def refresh(self, force=False):
        items = self._scan()
        visibles = [it for it in items if self._pasa_filtro(it[1])]
        cols = max(1, (self.scroll.viewport().width() - 20)
                   // (THUMB_W + 10))
        firma = (self._filter, cols,
                 tuple((os.path.basename(j), int(m), bool(meta.get('gender')))
                       for j, meta, m in visibles))
        if not force and firma == self._signature:
            return
        self._signature = firma

        # Vaciar el grid
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w is not None and w is not self.lbl_empty:
                w.setParent(None)
                w.deleteLater()
        self.lbl_empty.setParent(None)

        total = len(items)
        self.lbl_count.setText(
            f"{total} fotos" + (f" · {len(visibles)} visibles"
                                if len(visibles) != total else ""))
        if not visibles:
            self.grid.addWidget(self.lbl_empty, 0, 0)
            self.lbl_empty.show()
            return

        for i, (jpg, meta, mtime) in enumerate(visibles):
            card = _CaptureCard(jpg, meta, self._pixmap(jpg, mtime))
            self.grid.addWidget(card, i // cols, i % cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()          # re-fluye las columnas si cambió el ancho
