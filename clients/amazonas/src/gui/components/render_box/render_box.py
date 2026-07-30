"""
src/gui/components/render_box/render_box.py
RenderBox original + soporte de drag & drop DVR (RTSP) con detección automática de Hik-Connect e IP.

Cambios vs original:
  • dragEnterEvent / dropEvent aceptan también CAMERA_MIME
  • start_dvr_stream() lanza RTSPWorker con detección automática de tipo (Hik-Connect/IP)
  • _stop_dvr_stream() detiene el RTSPWorker
  • Botón "⏹ DVR" en la barra flotante cuando hay stream activo
  • Compatible con datos cifrados de Hik-Connect y URLs de IP
"""
import os, json, base64, sys
import re
import time
import uuid
import msgpack

from PySide6.QtWidgets import (
    QFrame, QWidget, QLabel, QHBoxLayout, QVBoxLayout,
    QGridLayout, QSizePolicy, QMenu, QWidgetAction, QCheckBox,
    QComboBox, QApplication,
)
from PySide6.QtCore  import Qt, Slot, QProcess, QUrl, Signal, QEvent, QBuffer, QIODevice
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtGui   import QPixmap, QCursor, QImage, QMouseEvent

from ..custon_label.interactive_imageLabel import Interactive_imageLabel
from ..custon_btn.btn_footer import BtnIco

from core.state_global.hwnd import hwndState
from core.capture_exaple import capture_window_by_hwnd, pil_image_to_png_bytes, window_exists, get_title
from core.window_controller import set_window_always_on_top
from core.dvr.hikconnect_channel_encoder import ChannelTypeDetector

# DVR
from workers.rtsp_worker import RTSPWorker

# Identidad estable de la camara (H-11). La logica vive en el nucleo: los
# cuatro clientes tenian el mismo fallo y ahora comparten el mismo arreglo.
from elde_core.ui import identidad_camara as _identidad
# Captura compartida: start_dvr_stream y loop_show_result eran el mismo
# codigo en los cuatro clientes (0,97-1,00 de similitud entre tres de
# ellos; amazonas arrastraba una copia anterior).
from elde_core.ui.render_box_captura import CapturaDVRMixin
from workers.video_worker import (
    VideoFileWorker, EXTENSIONES_VIDEO, es_archivo_de_video,
)
_DVR_MIME = "application/x-dvr-channel"

class _BarraFlotante(QWidget):
    """Barra superpuesta al visor que deja pasar los clics sobrantes.

    Las barras de info y de botones se colocan ENCIMA de la imagen, asi
    que tapaban las franjas superior e inferior: los puntos del ROI que
    caian ahi eran imposibles de arrastrar.

    Qt entrega estos eventos al contenedor solo cuando el clic NO cayo
    sobre un boton hijo (los hijos tienen prioridad), de modo que
    reenviar aqui al visor conserva intactos los botones.

    NO sirve `WA_TransparentForMouseEvents`: ese atributo desactiva la
    entrega tambien en los widgets hijos y dejaria la barra muerta.
    """

    def __init__(self, padre: QWidget, visor: QWidget) -> None:
        super().__init__(padre)
        self._visor = visor

    def _reenviar(self, event) -> None:
        visor = self._visor
        if visor is None or not visor.isVisible():
            return
        global_pos = self.mapToGlobal(event.position().toPoint())
        copia = QMouseEvent(
            event.type(),
            visor.mapFromGlobal(global_pos),
            global_pos,
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        QApplication.sendEvent(visor, copia)

    def mousePressEvent(self, event):        self._reenviar(event)
    def mouseMoveEvent(self, event):         self._reenviar(event)
    def mouseReleaseEvent(self, event):      self._reenviar(event)
    def mouseDoubleClickEvent(self, event):  self._reenviar(event)

    def vigilar_hijos(self) -> None:
        """Somete los botones de la barra al filtro de prioridad del ROI.

        Se llama una vez terminada la barra, cuando ya existen todos.
        """
        for hijo in self.findChildren(QWidget):
            hijo.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Da paso al ROI cuando uno de sus puntos cae bajo un boton.

        Un boton consume el clic con toda la razon, pero si justo debajo
        hay un punto del ROI el usuario no tendria forma de agarrarlo.
        Solo se le cede el evento cuando el punto esta literalmente bajo
        el cursor (radio de agarre) o el arrastre ya empezo; el resto de
        la superficie del boton sigue siendo suya.
        """
        tipo = event.type()
        if tipo not in (QEvent.MouseButtonPress, QEvent.MouseMove,
                        QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            return False
        visor = self._visor
        if visor is None or not visor.isVisible():
            return False
        global_pos = obj.mapToGlobal(event.position().toPoint())
        en_visor = visor.mapFromGlobal(global_pos)
        if not (visor.arrastrando_punto() or visor.indice_punto_en(en_visor) != -1):
            return False
        QApplication.sendEvent(visor, QMouseEvent(
            tipo, en_visor, global_pos,
            event.button(), event.buttons(), event.modifiers(),
        ))
        return True


# Grosor del borde de #box-content (global.qss). Todo lo que se coloca
# por geometria absoluta se mete hacia dentro esta cantidad para no
# taparlo.
_BORDE = 1


class Render_box(CapturaDVRMixin, QFrame):

    double_clicked_signal = Signal(int, bool)
    roi_change_signal     = Signal(list)
    # (nombre del archivo, frames analizados) al terminar un video.
    video_finalizado      = Signal(str, int)

    def __init__(self,
                 frames_per_milliseconds=100,
                 index=0,
                 hwnd=None,
                 inferece_play=False,
                 roi=[[100,100],[900,100],[900,900],[100,900]],
                 roi_boolean=False,
                 callback_save_data=None,
                 socket_services=None,
                 api_jarvis=None,
                 ):
        super().__init__()

        self.setAcceptDrops(True)
        self.hwnd        = hwnd
        self.smart_mode  = inferece_play
        self.index       = index
        self.api_jarvis  = api_jarvis
        self.socket      = socket_services
        self.socket.connected_signal.connect(self.reconnect_socket)
        self.socket.disconnected_signal.connect(self.diconect_socket)

        self.roi                     = roi
        self.roi_boolean             = roi_boolean
        self.callback_save_data      = callback_save_data

        self.process              = None
        self.stop                 = False
        self._pausado             = False
        self._detenido            = False
        self.frames_per_milliseconds = frames_per_milliseconds
        self.frame_count          = 0
        self.last_fps_time        = time.time()
        self.current_fps          = 0
        self.is_maximized         = False
        self.image_w              = 0
        self.image_h              = 0
        self.current_pixmap       = None
        self.component_key        = str(uuid.uuid4())
        # Identidad ESTABLE de la camara fisica (H-11). `component_key` se
        # queda como clave de enrutado del recuadro, pero ya NO viaja como
        # `camera_id`: era un uuid4 por sesion y fragmentaba el historico.
        self._dvr_device_serial   = ""
        self._dvr_channel_id      = ""
        # Estado que la version compartida de start_dvr_stream y
        # loop_show_result da por hecho (elde_core.ui.render_box_captura).
        # Amazonas venia de una copia ANTERIOR y no lo tenia, igual que le
        # pasaba con el paquete dvr/ en el HITO 7. Los valores elegidos son
        # los que dejan su comportamiento EXACTAMENTE como estaba:
        #   _direct_mode = False  ->  draw_server = True, que es el valor por
        #   defecto del servidor y lo que amazonas recibe hoy. Ponerlo en True
        #   (como perimetrales) le quitaria la imagen dibujada y este cliente
        #   NO sabe dibujarla: no tiene overlay de Supervision.
        self._unpacker            = None
        self._pending_frame_bytes = None
        self._direct_mode         = False
        self.camera_angle         = "auto"
        self.camera_name_dvr      = ""
        self.heatmap_boolean      = False
        self.order_zone_boolean   = False
        self.delivery_zone_boolean = False
        self.vlm_enabled_boolean  = False
        # Reenvio de alertas a WhatsApp: lo gobierna el interruptor
        # GLOBAL del pie (main.py lo propaga a todos los recuadros).
        # El envio lo hace el servidor.
        self.whatsapp_boolean     = False
        self.can_send_next_frame  = True

        # DVR / archivo de video
        self._rtsp_worker: RTSPWorker | None = None
        self._video_worker: VideoFileWorker | None = None
        self._ruta_video: str = ""
        # Workers que no pararon a tiempo: se conservan referenciados hasta
        # que terminen solos (destruir un QThread vivo aborta la app).
        self._rtsp_huerfanos: list = []
        self._dvr_mode:    bool = False

        # ── Clases para tracking ──
        # Mapa: nombre_display → class_id (int para COCO) o list[int] (grupo).
        # Cosmeticos reemplaza a Bicicleta y agrupa los 16 SKUs del modelo
        # cosmeticos (Personal de Amazonas). El server ignora ids que no
        # correspondan a su modelo activo, asi que es seguro mantenerlo
        # visible en todos los modos.
        # Solo se detectan PERSONAS (clase 0 de COCO). El pipeline del
        # servidor es exclusivamente personas + genero + rango de edad.
        self._selected_classes = [0]

        self.setup_ui()

        if self.hwnd is not None and window_exists(self.hwnd):
            self.get_hwnd_and_print(self.hwnd)
            self.title      = get_title(self.hwnd)
            self.id_windows = int(hwnd)
            if self.socket.is_connected() and self.smart_mode:
                self.init_loop()

        hwndState.change_hwnd.connect(self.get_hwnd_and_print)
        if self.socket is not None:
            self.socket.signal_inference.connect(self.on_text_message_received)


    def setup_ui(self):
        self.setObjectName("box-content")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setContentsMargins(0, 0, 0, 0)

        self.stack = QVBoxLayout(self)
        # 1 px = grosor del borde de #box-content. Sin este margen la
        # imagen se pinta sobre el borde y este parece "sobreponerse".
        self.stack.setContentsMargins(_BORDE, _BORDE, _BORDE, _BORDE)

        # Imagen (se crea antes que las barras: ellas le reenvian el raton)
        self.imagen_label = Interactive_imageLabel(
            "viewing window",
            roi=self.roi, roi_active=self.roi_boolean,
        )
        self.imagen_label.point_change.connect(self.save_point)
        self.imagen_label.setAlignment(Qt.AlignCenter)
        self.imagen_label.installEventFilter(self)
        self.stack.addWidget(self.imagen_label)

        # Barra info
        self.bar_info = _BarraFlotante(self, self.imagen_label)
        self.bar_info.setAttribute(Qt.WA_StyledBackground, True)
        self.bar_info.setMaximumHeight(30)
        self.bar_info.setObjectName("bar_options")
        bar_info_layout = QHBoxLayout(self.bar_info)

        self.text_fps  = QLabel(f"FPS: {self.current_fps}")
        self.text_fps.setObjectName("text-fps")
        self.text_size = QLabel("0x0")
        self.text_size.setObjectName("text-fps")
        self._dvr_label = QLabel("")
        self._dvr_label.setObjectName("text-fps")
        self._dvr_label.setVisible(False)

        bar_info_layout.addWidget(self.text_fps)
        bar_info_layout.addWidget(self.text_size)
        bar_info_layout.addStretch()
        bar_info_layout.addWidget(self._dvr_label)

        # Barra botones
        self.bar_options = _BarraFlotante(self, self.imagen_label)
        self.bar_options.setAttribute(Qt.WA_StyledBackground, True)
        self.bar_options.setMaximumHeight(30)
        self.bar_options.setObjectName("bar_options")
        bar_opt_layout = QHBoxLayout(self.bar_options)
        bar_opt_layout.setContentsMargins(10, 0, 10, 0)
        bar_opt_layout.setSpacing(5)

        # ── Grupo IA ──
        self.btn_smart = BtnIco(ico_path="resource/mode_ai.png",
                                title="Monitoreo inteligente", h=30, w=30)
        self.btn_smart.setObjectName("btn-bar")
        self.btn_smart.clicked.connect(self.activate_modesmart)
        self.btn_smart.setCheckable(True)
        self.btn_smart.setDisabled(True)

        # ── Grupo ROI ──
        self.btn_perimeterroi = BtnIco(ico_path="resource/perimeter.png",
                                       title="Activar/Desactivar ROI", h=30, w=30)
        self.btn_perimeterroi.setCheckable(True)
        self.btn_perimeterroi.setObjectName("btn-bar")
        self.btn_perimeterroi.clicked.connect(self._hideandclear_roy)

        # Botón para ocultar/mostrar puntos del ROI (sin desactivarlo)
        self.btn_hide_points = BtnIco(ico_path="resource/layout.png",
                                      title="Ocultar/Mostrar puntos ROI", h=30, w=30)
        self.btn_hide_points.setCheckable(True)
        self.btn_hide_points.setObjectName("btn-bar")
        self.btn_hide_points.clicked.connect(self._toggle_points_visibility)

        # Resetear posicion de los ROIs
        self.btn_roi_reset = BtnIco(ico_path="resource/close.png",
                                    title="Reiniciar posicion de ROIs", h=30, w=30)
        self.btn_roi_reset.setObjectName("btn-bar")
        self.btn_roi_reset.clicked.connect(self._reset_roi_positions)

        # ── Grupo Clases (selector de qué trackear) ──
        # ── Grupo Controles de captura ──
        # Analizar un archivo de video en esta celda. Tambien se puede
        # soltar el archivo directamente sobre la celda.
        self.btn_video = BtnIco(ico_path="resource/play_box.png",
                                title="Analizar un archivo de video…",
                                h=30, w=30)
        self.btn_video.setObjectName("btn-bar")
        self.btn_video.clicked.connect(self.abrir_video)

        self.btn_cap = BtnIco(ico_path="resource/camera_box.png", title="Captura", h=30, w=30)
        self.btn_cap.setObjectName("btn-bar")

        btn_play  = BtnIco(ico_path="resource/play_box.png",  title="Iniciar", h=30, w=30)
        btn_pause = BtnIco(ico_path="resource/pause_box.png", title="Pausar",  h=30, w=30)
        btn_stop  = BtnIco(ico_path="resource/stop_box.png",  title="Parar",   h=30, w=30)
        for b in (btn_play, btn_pause, btn_stop):
            b.setObjectName("btn-bar")

        btn_play.clicked.connect(self.init_loop)
        btn_pause.clicked.connect(self.pause_loop)
        btn_stop.clicked.connect(self.detroy_loop)

        # Botón detener DVR
        self._btn_stop_dvr = BtnIco(ico_path="resource/stop_box.png",
                                    title="Detener DVR", h=30, w=30)
        self._btn_stop_dvr.setObjectName("btn-bar")
        self._btn_stop_dvr.setToolTip("Detener stream DVR")
        self._btn_stop_dvr.clicked.connect(self._stop_dvr_stream)
        self._btn_stop_dvr.setVisible(False)

        # ── Separador visual (línea vertical) ──
        def _sep():
            s = QFrame()
            s.setFrameShape(QFrame.VLine)
            s.setStyleSheet("color: #666;")
            s.setFixedWidth(2)
            s.setFixedHeight(20)
            return s

        # Layout: [IA | ROI HidePoints Reset] --- [Capture Play Pause Stop DVR]
        bar_opt_layout.addWidget(self.btn_smart)
        bar_opt_layout.addWidget(_sep())
        bar_opt_layout.addWidget(self.btn_perimeterroi)
        bar_opt_layout.addWidget(self.btn_hide_points)
        bar_opt_layout.addWidget(self.btn_roi_reset)
        bar_opt_layout.addStretch(1)
        bar_opt_layout.addWidget(self.btn_video)
        bar_opt_layout.addWidget(self.btn_cap)
        bar_opt_layout.addWidget(btn_play)
        bar_opt_layout.addWidget(btn_pause)
        bar_opt_layout.addWidget(btn_stop)
        bar_opt_layout.addWidget(self._btn_stop_dvr)

        # Ya existen todos los botones: se les aplica el filtro que cede
        # el clic al ROI cuando uno de sus puntos queda justo debajo.
        self.bar_info.vigilar_hijos()
        self.bar_options.vigilar_hijos()

        self.bar_info.hide()
        self.bar_options.hide()


    # ── DVR: stream RTSP ─────────────────────────────────────


    # ── Analisis de un archivo de video ──────────────────────

    def abrir_video(self):
        """Pide un archivo de video y lo pone a analizar en esta celda."""
        from PySide6.QtWidgets import QFileDialog

        patron = " ".join(f"*{e}" for e in sorted(EXTENSIONES_VIDEO))
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Elegir un video para analizar", "",
            f"Videos ({patron});;Todos los archivos (*)")
        if ruta:
            self.start_video_file(ruta)

    def start_video_file(self, ruta: str):
        """Recorre un archivo de video enviando sus frames al servidor.

        Reutiliza el mismo camino que el DVR (misma celda, mismo envio por
        websocket, mismas capturas), asi que las fotos con genero y edad
        salen en `capture/` igual que con una camara en vivo.
        """
        self._stop_dvr_stream()

        # Soltar la captura de ventana, si la habia.
        if self.process is not None:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
            self.process = None
        self.hwnd = None

        if not es_archivo_de_video(ruta):
            self._log_dvr(f"⚠ No es un archivo de video reconocido:\n"
                          f"{os.path.basename(ruta)}", error=True)
            return

        self._ruta_video = ruta
        self._dvr_mode = True
        self._detenido = False
        self.stop = False
        self.can_send_next_frame = True
        self._dvr_label.setText(f"🎬 {os.path.basename(ruta)}")
        self._dvr_label.setVisible(True)
        self._btn_stop_dvr.setVisible(True)

        # Analizar es justo lo que se pide al soltar un video, asi que la
        # IA se enciende sola. OJO: `_stop_dvr_stream()`, unas lineas mas
        # arriba, la apaga; sin esto el video se reproducia entero sin
        # enviar un solo frame al servidor y no salia ninguna captura.
        conectado = bool(self.socket and self.socket.is_connected())
        self.btn_smart.setEnabled(conectado)
        if conectado:
            self.smart_mode = True
            self.btn_smart.setChecked(True)
            self._save_all("inference_play", True)
        else:
            self._log_dvr("⚠ Sin conexion con el servidor: el video se "
                          "reproduce, pero no se analiza.", error=False)

        # Sin IA activa no hay quien conteste: no tiene sentido esperar.
        self._video_worker = VideoFileWorker(
            ruta, esperar_al_servidor=self.smart_mode)
        self._video_worker.frame_ready.connect(self._on_dvr_frame)
        self._video_worker.iniciado.connect(
            lambda texto: self._log_dvr(texto))
        self._video_worker.progreso.connect(self._on_video_progreso)
        self._video_worker.finalizado.connect(self._on_video_finalizado)
        self._video_worker.error.connect(
            lambda msg: self._log_dvr(f"⚠ {msg}", error=True))
        self._video_worker.start()
        self.text_fps.setText("⏳ Abriendo el video…")

    def _on_video_progreso(self, actual: int, total: int, restante: float):
        """Avance del analisis en la barra de la celda."""
        if total > 0:
            pct = 100.0 * actual / total
            self.text_fps.setText(
                f"🎬 {pct:.0f} %  ({actual}/{total})"
                f"   faltan ~{VideoFileWorker._reloj(restante)}")
        else:
            self.text_fps.setText(f"🎬 frame {actual}")

    def _on_video_finalizado(self, entregados: int):
        """Fin del archivo: se avisa y se lanza el repaso del VLM."""
        nombre = os.path.basename(getattr(self, "_ruta_video", "") or "video")
        if entregados <= 0:
            self._log_dvr(f"⚠ No se pudo leer ningun frame de {nombre}",
                          error=True)
            return
        self._log_dvr(f"✅ {nombre}: {entregados} frames analizados")
        self.video_finalizado.emit(nombre, entregados)

    def _on_dvr_frame(self, img: QImage):
        if not self._dvr_mode or self._pausado:
            return

        pix = QPixmap.fromImage(img)
        self.current_pixmap = pix
        w, h = img.width(), img.height()
        self.image_w = w
        self.image_h = h

        # FPS tracking
        self.frame_count += 1
        now = time.time()
        if now - self.last_fps_time >= 1.0:
            self.current_fps = self.frame_count
            self.frame_count = 0
            self.last_fps_time = now
            prefix = "AI" if self.smart_mode else "DVR"
            self.text_fps.setText(f"{prefix} FPS: {self.current_fps}")

        # Si IA activa, enviar frame al servidor (solo mostrar respuesta del servidor)
        if self.smart_mode and self.socket is not None and self.socket.is_connected():
            if self.can_send_next_frame:
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                img.save(buf, "JPEG", 80)
                jpeg_bytes = bytes(buf.data())
                buf.close()

                data = {
                    "image": jpeg_bytes,
                    "roi_coordinates": self.imagen_label.get_coordinates(w, h),
                    "roi_activate": self.roi_boolean,
                    "camera_id": self._device_id(),
                    "enviar_whatsapp": self.whatsapp_boolean,
                    "track_classes": self._selected_classes,
                }
                self.socket.send_binary_frame(self.component_key, data)
                self.can_send_next_frame = False
            # No mostrar frame crudo — solo se muestra el frame del servidor
            # via on_text_message_received → update_streaming_frame
        else:
            # Sin IA: mostrar frame crudo directamente
            self.imagen_label.setPixmap(
                pix.scaled(self.imagen_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _log_dvr(self, mensaje: str, error: bool = False) -> None:
        """Informa del estado del DVR EN LA PROPIA CELDA.

        Este metodo se llamaba pero no existia: cuando un canal no traia
        URL, en vez de avisar se lanzaba AttributeError, el `try` del drop
        se lo tragaba y el canal simplemente no cargaba, sin decir nada.
        """
        print(f"[Canal {self.index}] {mensaje}")
        self.text_fps.setText(mensaje[:60])
        self._dvr_label.setText(mensaje[:70])
        self._dvr_label.setVisible(True)
        if error:
            # Mensaje visible sobre el visor, no solo en la barra.
            self.imagen_label.setText(mensaje)

    def _retirar_rtsp_huerfano(self, worker) -> None:
        """Suelta un worker RTSP antiguo cuando por fin termina."""
        try:
            self._rtsp_huerfanos.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def _stop_dvr_stream(self):
        """Detiene la fuente actual y deja el canal listo para otra."""
        video, self._video_worker = getattr(self, "_video_worker", None), None
        if video is not None:
            try:
                video.frame_ready.disconnect()
            except (RuntimeError, TypeError):
                pass
            video.stop()
            if video.isRunning():
                # Mismo motivo que con el RTSP: destruir un QThread vivo
                # aborta la aplicacion, asi que se conserva referenciado.
                self._rtsp_huerfanos.append(video)
                video.finished.connect(
                    lambda w=video: self._retirar_rtsp_huerfano(w))
            else:
                video.deleteLater()
        self._ruta_video = ""

        anterior, self._rtsp_worker = self._rtsp_worker, None
        if anterior is not None:
            try:
                anterior.frame_ready.disconnect()
            except (RuntimeError, TypeError):
                pass                      # ya estaba desconectado
            if anterior.isRunning():
                anterior.stop()           # pide parada y espera hasta 3 s
            if anterior.isRunning():
                # No termino a tiempo. NO se puede soltar la referencia:
                # destruir un QThread en marcha aborta la aplicacion. Se
                # conserva hasta que termine y se suelta entonces.
                self._rtsp_huerfanos.append(anterior)
                anterior.finished.connect(
                    lambda w=anterior: self._retirar_rtsp_huerfano(w))
            else:
                anterior.deleteLater()

        self._dvr_mode = False
        self._dvr_label.setText("")
        self._dvr_label.setVisible(False)
        self._btn_stop_dvr.setVisible(False)
        self.smart_mode = False
        self.btn_smart.setChecked(False)
        self.btn_smart.setStyleSheet("")     # el color lo pone el QSS global
        self._pausado = False
        self.can_send_next_frame = True
        self.current_pixmap = None
        self.image_w = 0
        self.image_h = 0
        self.text_fps.setText("FPS: 0")

    # ── Drag & Drop (ventana Windows + canal DVR) ─────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-boxcap"):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat(_DVR_MIME):
            event.acceptProposedAction()
        elif self._video_soltado(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    @staticmethod
    def _video_soltado(mime) -> str:
        """Ruta del primer archivo de video del arrastre, o cadena vacia.

        Sirve para el arrastre desde el Explorador de Windows, que viaja
        como lista de URLs.
        """
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            ruta = url.toLocalFile()
            if es_archivo_de_video(ruta):
                return ruta
        return ""

    def dropEvent(self, event):
        try:
            ruta_video = self._video_soltado(event.mimeData())
            if ruta_video:
                self.start_video_file(ruta_video)
                event.acceptProposedAction()
                return

            if event.mimeData().hasFormat(_DVR_MIME):
                raw  = bytes(event.mimeData().data(_DVR_MIME)).decode("utf-8")
                data = json.loads(raw)
                self.start_dvr_stream(data)
                event.acceptProposedAction()
                return

            if event.mimeData().hasFormat("application/x-boxcap"):
                raw = bytes(event.mimeData().data("application/x-boxcap")).decode("utf-8")
                other_hwnd, other_title = raw.split("|", 1)
                if int(other_hwnd) == getattr(self, "id_windows", None):
                    event.ignore()
                    return
                self._stop_dvr_stream()
                self.get_hwnd_and_print(int(other_hwnd))
                self.id_windows = int(other_hwnd)
                self.title      = other_title
                self.hwnd       = self.id_windows
                event.acceptProposedAction()
                if callable(self.callback_save_data):
                    self.callback_save_data(self.index, "hwnd", self.id_windows)
                return

            event.ignore()
        except Exception as e:
            print(f"dropEvent error: {e}")


    # ── Resto del código original (sin cambios) ───────────────

    def _hideandclear_roy(self):
        """Activa/desactiva el ROI del area de conteo."""
        self.imagen_label.toggle_points()
        self.roi_boolean = not self.roi_boolean
        self.imagen_label.update()
        self._save_all("roi_boolean", self.roi_boolean)

    def _toggle_points_visibility(self):
        """Oculta/muestra los puntos del ROI sin desactivar el ROI."""
        self.imagen_label.toggle_points_visibility()

    def _reset_roi_positions(self):
        """Reinicia la posicion del ROI del area de conteo y lo persiste."""
        self.imagen_label.reset_points()
        self.roi_boolean = True
        for key, value in self.imagen_label.get_reset_lists().items():
            self._save_all(key, value)
        self._save_all("roi_boolean", True)

    def _camera_display_name(self) -> str:
        """Nombre legible de esta camara para el dashboard."""
        return _identidad.nombre_visible(
            nombre_dvr=self.camera_name_dvr,
            titulo_ventana=getattr(self, 'title', ''),
            indice=getattr(self, 'index', 0))

    def _device_id(self) -> str:
        """Identificador ESTABLE de la camara, para el `camera_id` del payload.

        Antes se mandaba `component_key`, que es un `uuid.uuid4()` generado al
        construir el panel: cada arranque inventaba camaras nuevas a ojos del
        servidor y el historico por camara no se acumulaba nunca (H-11).

        La logica esta en `elde_core.ui.identidad_camara`; aqui solo se leen
        los atributos del recuadro.
        """
        return _identidad.device_id(
            serie_dvr=self._dvr_device_serial,
            canal_dvr=self._dvr_channel_id,
            titulo_ventana=getattr(self, 'title', ''),
            indice=getattr(self, 'index', 0))

    def init_loop(self):
        """Arranca (o reanuda) la captura de la ventana asignada."""
        try:
            if self.hwnd is None:
                return
            # Rearmar el estado: tras un stop, `stop` quedaba en True y el
            # bucle de lectura no volvia a engancharse, dejando el canal
            # inservible hasta reiniciar la aplicacion.
            self.stop = False
            self._pausado = False
            self._detenido = False
            self.can_send_next_frame = True

            if self.process is None:
                worker_script = "src/workers/capture_woker.py"
                if not os.path.exists(worker_script):
                    print(f"[Canal {self.index}] falta {worker_script}")
                    return
                self.process = QProcess(self)
                self.process.setProcessChannelMode(QProcess.MergedChannels)
                # Conexion UNICA: antes, cada pulsacion de play sobre un
                # proceso ya existente añadia OTRA conexion a la misma
                # señal, asi que loop_show_result corria varias veces por
                # dato, se comian los buffers unos a otros y el canal se
                # volvia erratico.
                self.process.readyReadStandardOutput.connect(
                    self.loop_show_result, Qt.UniqueConnection)
                self.process.start(sys.executable,
                                   [worker_script, str(self.hwnd)])
                if not self.process.waitForStarted(5000):
                    print(f"[Canal {self.index}] el worker de captura no arranco")
                    self._terminar_proceso()
                    return
            else:
                try:
                    self.process.readyReadStandardOutput.connect(
                        self.loop_show_result, Qt.UniqueConnection)
                except (RuntimeError, TypeError):
                    pass          # ya estaba conectada: es lo correcto
        except Exception as e:
            print(f"init_loop error: {e}")

    def pause_loop(self):
        """Pausa/reanuda el canal sin soltar la fuente."""
        self._pausado = not getattr(self, "_pausado", False)
        if self._rtsp_worker is not None:
            try:
                (self._rtsp_worker.pause() if self._pausado
                 else self._rtsp_worker.resume())
            except Exception:  # noqa: BLE001
                pass
        self.text_fps.setText("PAUSA" if self._pausado
                              else f"FPS: {self.current_fps}")

    def _terminar_proceso(self):
        """Cierra el worker de captura de ventana y suelta sus señales."""
        proceso, self.process = self.process, None
        if proceso is None:
            return
        try:
            proceso.readyReadStandardOutput.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            proceso.terminate()
            if not proceso.waitForFinished(1500):
                proceso.kill()
                proceso.waitForFinished(500)
        except Exception:  # noqa: BLE001
            pass
        proceso.deleteLater()

    def activate_modesmart(self):
        """Activa/desactiva el analisis IA de este canal.

        El color del boton lo resuelve el QSS global por su estado
        `:checked` (verde = analizando), en vez de fijarlo aqui a mano.
        """
        self.smart_mode = not self.smart_mode
        self.btn_smart.setChecked(self.smart_mode)
        self._save_all("inference_play", self.smart_mode)
        # Si se esta analizando un video, el handshake solo tiene sentido
        # con la IA encendida: sin ella nadie confirma y el archivo debe
        # correr libre.
        worker = getattr(self, "_video_worker", None)
        if worker is not None:
            worker.fijar_espera(self.smart_mode)
        if self.smart_mode:
            self.stop = False
            self.can_send_next_frame = True
            self.roi_boolean = True
            self.imagen_label.show_points = True
            self.imagen_label.points_hidden = False
            if hasattr(self, 'btn_hide_points'):
                self.btn_hide_points.setChecked(False)
            self.imagen_label.update()
            self._save_all("roi_boolean", True)
        else:
            self.can_send_next_frame = True
            self.roi_boolean = False
            self.imagen_label.show_points = False
            self.imagen_label.update()
            self._save_all("roi_boolean", False)

    def detroy_loop(self):
        """Detiene el canal por completo y lo deja LISTO para reutilizar.

        Antes solo mataba el worker de captura de ventana: si el canal
        estaba reproduciendo un DVR, el hilo RTSP seguia vivo y la imagen
        no se iba. Ademas quedaban banderas puestas (`stop`, `_dvr_mode`,
        `can_send_next_frame`) que impedian volver a usar el canal sin
        reiniciar la aplicacion.
        """
        self._detenido = True          # ignora los frames en vuelo
        self.smart_mode = False
        self.btn_smart.setChecked(False)
        self.btn_smart.setStyleSheet("")

        # 1) Fuente A: worker de captura de ventana.
        self._terminar_proceso()

        # 2) Fuente B: stream DVR (esto es lo que faltaba).
        if self._rtsp_worker is not None or self._dvr_mode:
            self._stop_dvr_stream()

        # 3) Estado de la fuente.
        self.title = None
        self.hwnd = None
        self.id_windows = None
        self._dvr_mode = False
        self._dvr_label.setText("")
        self._dvr_label.setVisible(False)
        self._btn_stop_dvr.setVisible(False)

        # 4) Estado de imagen y transmision. `stop` vuelve a False: es una
        #    bandera de "no seguir el bucle actual", no un cierre
        #    permanente del canal; dejarla en True lo inutilizaba.
        self.stop = False
        self._pausado = False
        self.can_send_next_frame = True
        self.current_pixmap = None
        self.image_w = 0
        self.image_h = 0
        self.frame_count = 0
        self.current_fps = 0

        # 5) Limpiar lo que se ve.
        self.imagen_label.setPixmap(QPixmap())
        self.imagen_label.clear()
        self.imagen_label.setText("viewing window")
        self.text_fps.setText("FPS: 0")
        self.text_size.setText("0x0")

        if callable(self.callback_save_data):
            self.callback_save_data(self.index, "hwnd", None)

    @Slot(int)
    def get_hwnd_and_print(self, hwnd=None):
        try:
            if hwnd is not None:
                self.hwnd = hwnd
            set_window_always_on_top(self.hwnd)
            buffer = capture_window_by_hwnd(self.hwnd)
            if buffer is None: return
            image = pil_image_to_png_bytes(buffer)
            if image is None: return
            self.update_streaming_frame(image, type_image="bytes", tets=False)
            self.bar_options.raise_()
        except Exception as e:
            print(f"get_hwnd error: {e}")


    def update_streaming_frame(self, frame, type_image="base64", tets=False):
        # Canal detenido: se descartan los frames que sigan llegando. El
        # servidor tiene frames EN VUELO cuando se pulsa stop y sus
        # respuestas llegaban despues de limpiar la imagen, repintando la
        # camara que se acababa de quitar (sintoma: "le doy stop y la
        # camara sigue ahi").
        if getattr(self, "_detenido", False):
            return
        try:
            if tets: self.open = True
            pixmap = QPixmap()
            if type_image == "base64":
                frame_bytes = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", frame))
                pixmap.loadFromData(frame_bytes, "JPEG")
            elif type_image == "jpeg_bytes":
                pixmap.loadFromData(frame, "JPEG")
            else:
                pixmap.loadFromData(frame, "PNG")

            if not pixmap.isNull():
                self.current_pixmap = pixmap
                self.image_w = pixmap.width()
                self.image_h = pixmap.height()
                self.text_size.setText(f"{self.image_w}x{self.image_h}")
                self.imagen_label.setPixmap(
                    pixmap.scaled(self.imagen_label.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                )
        except Exception as e:
            print(f"update_frame error: {e}")
        finally:
            if not self.stop and tets:
                self.loop_show_result()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        ancho = max(0, w - 2 * _BORDE)
        if hasattr(self, "bar_info"):
            self.bar_info.setGeometry(_BORDE, _BORDE, ancho, 30)
        if hasattr(self, "bar_options"):
            hb = self.bar_options.height()
            self.bar_options.setGeometry(_BORDE, h - hb - _BORDE, ancho, hb)
        if self.current_pixmap:
            self.imagen_label.setPixmap(
                self.current_pixmap.scaled(self.imagen_label.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            )

    def enterEvent(self, event):
        self.bar_info.show(); self.bar_options.show()
        self.bar_info.raise_(); self.bar_options.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.bar_info.hide(); self.bar_options.hide()
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            self.is_maximized = not self.is_maximized
            self.double_clicked_signal.emit(self.index, self.is_maximized)
        return super().eventFilter(watched, event)

    @Slot(dict)
    def on_text_message_received(self, message):
        try:
            msg_key = message.get("component_key") or message.get("camera_id") or ""
            if msg_key and msg_key not in (self.component_key, self._device_id()):
                return
            data = message["data"]
            if data["status"] == "success" and data["camera_id"] == self._device_id():
                self.update_streaming_frame(data["processed_image"], type_image="base64", tets=False)
            if data["status"] == "error":
                raise Exception(data.get("message", "Error del servidor"))
        except RuntimeError as e:
            if "cannot schedule new futures after shutdown" in str(e):
                self.stop = True
                return
            import traceback
            print(f"on_text_message_received RuntimeError: {e}")
            traceback.print_exc()
        except Exception as e:
            import traceback
            print(f"on_text_message_received error: {e}")
            traceback.print_exc()
        finally:
            self.can_send_next_frame = True
            # El worker de video espera esta confirmacion para leer el
            # frame siguiente: asi el archivo avanza al ritmo real de la
            # inferencia y no se analiza de menos.
            worker = getattr(self, "_video_worker", None)
            if worker is not None:
                worker.permitir_siguiente()

    def reconnect_socket(self, data):
        self.can_send_next_frame = True
        self.btn_smart.setEnabled(True)
        self.loop_show_result()

    def diconect_socket(self, data):
        self.btn_smart.setEnabled(False)

    def _save_all(self, key, value):
        if self.callback_save_data:
            self.callback_save_data(self.index, key, value)

    def save_point(self, roi, roi_boolean):
        """Persiste el ROI del area de conteo al moverlo."""
        if self.callback_save_data:
            self.callback_save_data(self.index, "roi", roi)
            self.callback_save_data(self.index, "roi_boolean", roi_boolean)
