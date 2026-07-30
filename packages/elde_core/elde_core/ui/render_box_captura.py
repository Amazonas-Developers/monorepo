"""
Captura de un recuadro: stream DVR y bucle de envio de frames.

Segunda rebanada del despiece de `render_box`. Aqui viven los dos metodos que
estaban REALMENTE duplicados en los cuatro clientes:

| Metodo | tienda · perimetrales · managers | amazonas |
|---|---|---|
| `start_dvr_stream` (168 LOC) | 0,97 – **1,00** | 0,37 — 67 LOC, copia vieja |
| `loop_show_result` (79 LOC) | 0,99 – **1,00** | 0,42 — 50 LOC, copia vieja |

Las tres versiones modernas diferian en dos cosas y nada mas: una linea
(`self._detenido = False`, solo en perimetrales) y el **orden de una clave** de
un diccionario. Es el mismo codigo escrito cuatro veces.

Amazonas arrastraba una copia anterior, igual que le pasaba con el paquete
`dvr/` en el HITO 7. Le faltaban dos cosas de verdad:

1. **Todo el flujo de Hik-Connect con encriptacion de stream**: el dialogo que
   pide el codigo de verificacion, la renovacion del token y el refresco de la
   URL con esa clave. Sin eso, un canal encriptado sencillamente no abre.
2. **El `msgpack.Unpacker` incremental.** Su version desempaquetaba el chunk
   entero de una vez, asi que un frame partido entre dos lecturas del pipe se
   perdia. El Unpacker persistente acumula los bytes parciales.

Que espera del widget
---------------------
Es un mixin, no una clase suelta: se mezcla en el `Render_box` de cada cliente
y lee su estado. Necesita `process`, `hwnd`, `socket`, `text_fps`,
`imagen_label`, `_dvr_label`, `_btn_stop_dvr`, `btn_smart`, `component_key`,
las banderas de zonas y modo, y los metodos `_stop_dvr_stream`, `_on_dvr_frame`,
`update_streaming_frame`, `_device_id` y `_camera_display_name`.

Lo que NO entra aqui: `setup_ui` (0,47–0,84 entre clientes) y `__init__`
(0,41–0,95). Esos divergieron de verdad — son la disposicion y el estado
propios de cada producto, y el HITO 2 ya dijo que cada panel es dedicado a su
dominio.
"""
from __future__ import annotations

import time

import msgpack
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QInputDialog, QLineEdit,
                               QMessageBox)

from ..capture.rtsp_worker import RTSPWorker
from ..dvr.hikconnect import HikConnectStrategy
from ..dvr.hikconnect_channel_encoder import ChannelTypeDetector
from ..dvr.storage import DVRRepository
from ..logging import obtener

log = obtener(__name__)

#: Tope del buffer del desempaquetador, en bytes. Un frame JPEG grande mas los
#: metadatos cabe de sobra; el limite existe para que un pipe corrupto no se
#: coma la memoria.
MAX_BUFFER = 128 * 1024 * 1024


class CapturaDVRMixin:
    """Stream DVR y bucle de envio, compartidos por los cuatro clientes."""

    # ── DVR: stream RTSP ─────────────────────────────────────────────────

    def start_dvr_stream(self, channel_data: dict):
        """Recibe los datos del canal (drop) e inicia el RTSPWorker.

        Detecta si es Hik-Connect o IP. Si es Hik-Connect sin URL valida o con
        encriptacion, pide la clave.
        """
        self._stop_dvr_stream()
        # Vuelve a aceptar frames. Solo perimetrales y amazonas leen esta
        # bandera; en los otros dos queda inerte, que es preferible a duplicar
        # el metodo por una linea.
        self._detenido = False

        # Detener captura HWND sin resetear el estado de IA.
        if self.process is not None:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
            self.process = None
        self.hwnd = None

        channel_type = ChannelTypeDetector.get_channel_type(channel_data)
        rtsp_url = channel_data.get("rtsp_main", "")

        # ── Hik-Connect: deteccion a prueba de balas de encriptacion ──
        # Cualquiera de estas condiciones dispara el dialogo de clave.
        url_invalid = ((not rtsp_url) or ("ErrCode" in rtsp_url)
                       or ("9053" in rtsp_url))
        flag_set = channel_data.get("stream_encrypt_enable", False)
        needs_code = (channel_type == "hikconnect") and (flag_set or url_invalid)

        log.info("start_dvr_stream: type=%s url_invalid=%s flag=%s "
                 "needs_code=%s url=%s", channel_type, url_invalid, flag_set,
                 needs_code, (rtsp_url or '')[:80])

        if needs_code:
            rtsp_url = self._url_con_clave(channel_data, rtsp_url)
            if rtsp_url is None:
                return                      # ya se informo al operador

        if not rtsp_url:
            self.text_fps.setText("⚠ Sin URL de stream disponible")
            return

        # Identidad estable de esta camara fisica (ver _device_id(), H-11).
        self._dvr_device_serial = str(channel_data.get("device_serial", "") or "")
        self._dvr_channel_id = str(channel_data.get("channel_id", "") or "")

        alias = channel_data.get("device_alias", "")
        ch_name = channel_data.get("channel_name", "")
        type_label = "🔐 HC" if channel_type == "hikconnect" else "📹"
        label = (f"{type_label} {alias} · {ch_name}" if alias
                 else f"{type_label} {ch_name}")
        # Nombre legible para el dashboard (sin emojis).
        self.camera_name_dvr = (
            f"{alias} {ch_name}".strip() or ch_name or "DVR")[:48]

        self._dvr_label.setText(label)
        self._dvr_label.setVisible(True)
        self._btn_stop_dvr.setVisible(True)
        self._dvr_mode = True
        self.stop = False
        self.can_send_next_frame = True

        # Habilitar el boton de IA si el socket esta conectado.
        if self.socket and self.socket.is_connected():
            self.btn_smart.setEnabled(True)

        self._rtsp_worker = RTSPWorker(
            rtsp_url, channel_id=channel_data.get("channel_id", ""))
        self._rtsp_worker.frame_ready.connect(self._on_dvr_frame)
        self._rtsp_worker.connected.connect(
            lambda: self.text_fps.setText("🟢 DVR en vivo"))
        self._rtsp_worker.error.connect(
            lambda msg: self.text_fps.setText(f"⚠ {msg.split(chr(10))[0]}"))
        self._rtsp_worker.disconnected.connect(
            lambda: self.text_fps.setText("⚫ DVR desconectado"))
        self._rtsp_worker.start()
        self.text_fps.setText("⏳ Conectando al stream…")

    def _url_con_clave(self, channel_data: dict, rtsp_url: str):
        """Pide el codigo de verificacion y renueva la URL del stream.

        Devuelve la URL nueva, o `None` si no se pudo (ya avisado al operador).
        Estaba en linea dentro de `start_dvr_stream`, que por eso media 168
        lineas; separarlo no cambia nada del flujo.
        """
        code, ok = QInputDialog.getText(
            self, "Código de verificación del stream",
            "Este canal tiene encriptación de stream habilitada.\n\n"
            "Ingresa el Código de Verificación del dispositivo.\n"
            "Puedes encontrarlo en:\n"
            "  • Interfaz web local del DVR → Configuración → Red → Plataforma\n"
            "  • O en la etiqueta física del dispositivo (6 dígitos)",
            QLineEdit.Password,
        )
        if not ok or not code.strip():
            self.text_fps.setText("⚠ Clave de encriptación requerida")
            return None

        resource_id = (channel_data.get("resource_id")
                       or channel_data.get("channel_id", ""))
        device_serial = channel_data.get("device_serial", "")

        self.text_fps.setText("⏳ Obteniendo stream con clave…")
        QApplication.processEvents()

        repo = DVRRepository()
        app_key = app_secret = ""

        # Buscar la cuenta Hik-Connect en el repositorio.
        for dev in repo.all():
            if dev.brand == "Hik-Connect":
                app_key = dev.username
                app_secret = dev.password
                # Si no tenemos device_serial, sacarlo de los canales guardados.
                if not device_serial:
                    for ch in dev.channels:
                        ch_d = ch if isinstance(ch, dict) else {}
                        if (ch_d.get("resource_id") == resource_id
                                or ch_d.get("id") == resource_id):
                            device_serial = ch_d.get("device_serial", "")
                            break
                break

        if not app_key or not app_secret:
            self.text_fps.setText("⚠ No hay cuenta Hik-Connect guardada")
            return None

        token_data = HikConnectStrategy.refresh_token(app_key, app_secret)
        if not token_data:
            self.text_fps.setText("⚠ No se pudo renovar token Hik-Connect")
            return None
        access_token = token_data["access_token"]
        area_domain = token_data["area_domain"]

        log.info("refresh_live_url: rid=%s serial=%s area=%s",
                 resource_id, device_serial, area_domain)

        if not (resource_id and device_serial):
            self.text_fps.setText("⚠ Faltan datos del canal (rid/serial)")
            return None

        new_url = HikConnectStrategy.refresh_live_url(
            area_domain=area_domain, access_token=access_token,
            resource_id=resource_id, device_serial=device_serial,
            quality=1, code=code.strip())
        if new_url:
            log.info("URL nueva con clave: %s", new_url[:100])
            return new_url

        self.text_fps.setText("⚠ Stream encriptado no disponible vía nube")
        msg = QMessageBox(self)
        msg.setWindowTitle("Stream encriptado - HiLook/EZVIZ")
        msg.setIcon(QMessageBox.Warning)
        msg.setText(
            "<b>No se pudo obtener el stream encriptado.</b><br><br>"
            "El DVR HiLook usa encriptación EZVIZ que la API de HikConnect "
            "no puede desbloquear directamente.<br><br>"
            "<b>Solución recomendada:</b><br>"
            "Deshabilita la encriptación de stream en el DVR:<br>"
            "1. Accede a la interfaz web local del DVR<br>"
            "2. Ve a <i>Configuración → Red → Plataforma</i><br>"
            "3. Deshabilita <i>Stream Encryption</i><br>"
            "4. Guarda y vuelve a intentarlo<br><br>"
            "<i>La cámara 1 funciona porque no tiene encriptación habilitada.</i>"
        )
        msg.exec()
        return None

    # ── Bucle de captura por ventana ─────────────────────────────────────

    def _zonas_de_negocio(self, etiqueta) -> dict:
        """Zonas de pedido y entrega, si esta etiqueta las tiene.

        No todos los clientes las tienen: en el HITO 7 se decidio que
        **Amazonas conserva su `Interactive_imageLabel` propia**, mas antigua y
        sin zonas de pedido/entrega, porque adoptar el superconjunto le
        cambiaria la firma que su `render_box` ya llama.

        Se comprueba en vez de asumir: si se llamara al metodo a ciegas, el
        `AttributeError` caeria en el `except` de `loop_show_result` y el
        cliente **dejaria de enviar frames sin decir por que**.
        """
        pedido = getattr(etiqueta, 'get_order_zone_coordinates', None)
        entrega = getattr(etiqueta, 'get_delivery_zone_coordinates', None)
        if not (pedido and entrega):
            return {}
        return {
            "order_zone_coordinates": pedido(self.image_w, self.image_h),
            "order_zone_activate": self.order_zone_boolean,
            "delivery_zone_coordinates": entrega(self.image_w, self.image_h),
            "delivery_zone_activate": self.delivery_zone_boolean,
        }

    def loop_show_result(self):
        if not self.process:
            return
        raw_data = self.process.readAllStandardOutput().data()
        if not raw_data:
            return
        try:
            # Unpacker persistente: acumula los chunks del pipe entre
            # invocaciones. Se drenan TODOS los mensajes completos y nos
            # quedamos con el ULTIMO (frame mas reciente), descartando los
            # intermedios para minimizar latencia. Los bytes parciales de un
            # frame a medio llegar quedan bufferizados para el siguiente chunk.
            if self._unpacker is None:
                self._unpacker = msgpack.Unpacker(
                    raw=False, strict_map_key=False, max_buffer_size=MAX_BUFFER)
            self._unpacker.feed(raw_data)
            message = None
            for _m in self._unpacker:       # itera solo mensajes COMPLETOS
                message = _m
            if message is None:
                return
            header = message.get("header")
            image_bytes = message.get("image_bytes")
            if not header or not image_bytes:
                return

            self.frame_count += 1
            now = time.time()
            if now - self.last_fps_time >= 1.0:
                self.current_fps = self.frame_count
                self.frame_count = 0
                self.last_fps_time = now
                # En modo IA el FPS real lo lleva on_text_message_received
                # (frames procesados por el servidor). Aqui solo se muestra la
                # tasa de captura local cuando la IA esta apagada.
                if not self.smart_mode:
                    self.text_fps.setText(f"FPS: {self.current_fps}")

            if self.socket is not None:
                if self.image_w == 0 or self.image_h == 0:
                    tmp = QPixmap()
                    tmp.loadFromData(image_bytes, "JPEG")
                    if not tmp.isNull():
                        self.image_w = tmp.width()
                        self.image_h = tmp.height()

                etiqueta = self.imagen_label
                data = {
                    "header": header, "image": image_bytes,
                    "roi_coordinates": etiqueta.get_coordinates(
                        self.image_w, self.image_h),
                    "roi_activate": self.roi_boolean,
                    **self._zonas_de_negocio(etiqueta),
                    "enable_vlm": self.vlm_enabled_boolean,
                    "camera_id": self._device_id(),
                    "camera_angle": self.camera_angle,
                    "enviar_whatsapp": self.whatsapp_boolean,
                    "heatmap_activate": self.heatmap_boolean,
                    "camera_name": self._camera_display_name(),
                    "track_classes": self._selected_classes,
                    "draw_server": (not self._direct_mode),
                }
                if self.smart_mode and self.can_send_next_frame:
                    # Modo directo: guardar el frame enviado para dibujar
                    # encima las detecciones que devuelva el servidor.
                    self._pending_frame_bytes = image_bytes
                    self.socket.send_binary_frame(self.component_key, data)
                    self.can_send_next_frame = False

                # Con IA el display llega desde la respuesta del servidor
                # (on_text_message_received -> update_streaming_frame); sin IA
                # se muestra el frame crudo local. (Antes habia aqui una
                # llamada recursiva a loop_show_result: se elimino, causaba
                # reentrancia y lecturas en vacio.)
                if not self.smart_mode:
                    self.update_streaming_frame(
                        image_bytes, type_image="jpeg_bytes", tets=True)
        except Exception as e:
            log.exception("loop_show_result error: %s", e)
