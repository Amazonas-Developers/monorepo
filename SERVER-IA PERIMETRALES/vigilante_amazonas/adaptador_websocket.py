"""
Adaptador WEBSOCKET de VIGILANTE-AMAZONAS para el servidor ELDE.

Las cámaras se VEN en el cliente perimetrales-view y este envía los frames
por el websocket del servidor (src/app/app.py, puerto 9000). app.py hace:

    from vigilante_amazonas.adaptador_websocket import VigilanteWS
    ...
    if type_inference == "VigilanteAmazonas":
        from vigilante_amazonas.adaptador_websocket import get_vigilante_ws
        return get_vigilante_ws()
    ...
    img, metadata = processor.process_frame(img, camera_id,
                                            camera_name=..., track_classes=...,
                                            draw=bool(draw_server))

Contrato de salida (lo que renderiza el cliente):
  metadata["detections"]: [{box, label, confidence, class_id, tracker_id,
                            color(RGB)}]  -> modo directo (draw=False)
  metadata["alerts"]:     tarjetas para el AlertsSidebar (event_type,
                          class_name, global_id, description, timestamp,
                          image_base64, camera_name)

SINGLETON por proceso: modelos y galería compartidos entre conexiones; al
activarse el modo quedan vivos el dashboard :8090 y el Socket.IO :8091.
"""

from __future__ import annotations

import base64
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.captura.fuente_rtsp import FrameCamara
from vigilante_amazonas.deteccion.clasificador_vehiculos import RefinadorVehiculos
from vigilante_amazonas.deteccion.detector import DetectorMulticlase
from vigilante_amazonas.deteccion.mapeo_clases import CLASE_A_ID, color_de
from vigilante_amazonas.deteccion.motor import anotar_frame
from vigilante_amazonas.deteccion.rastreador import DeteccionVig, GestorRastreadores
from vigilante_amazonas.servicios import get_servicios
from vigilante_amazonas.servicios.emisor_whatsapp import get_emisor_whatsapp
from vigilante_amazonas.servicios.motor_reid import EventoReID
from vigilante_amazonas.servicios.rastreador_area import RastreadorArea
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

# Segundos que se mantiene el nombre de la persona identificada sobre su caja.
TTL_IDENTIDAD_SEG: float = 10.0
# Tope de tarjetas retenidas por cámara (anti-fuga si nadie las drena).
MAX_TARJETAS_CAMARA: int = 50


class VigilanteWS:
    """Procesador websocket multi-cámara (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.detector = DetectorMulticlase()
        self.rastreadores = GestorRastreadores()
        self.vehiculos = RefinadorVehiculos()  # carro vs camioneta vs camión
        from vigilante_amazonas.servicios.detector_merodeo import DetectorMerodeo
        self.area = RastreadorArea(DetectorMerodeo())  # permanencia + llegada/salida + merodeo
        self._servicios = get_servicios()
        self._seq: dict[str, int] = defaultdict(int)
        self._tarjetas: dict[str, Deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=MAX_TARJETAS_CAMARA))
        # (camara, track_id) -> (nombre, nivel, ts_expira)
        self._identidades: dict[tuple[str, int], tuple[str, str, float]] = {}
        if self._servicios.motor_reid is not None:
            self._servicios.motor_reid.registrar_consumidor(self._consumidor_reid)
        _iniciar_dashboard_una_vez()
        logger.info("VigilanteWS listo (modo websocket): detección 7 clases + "
                    "personal_seguridad + Re-ID; dashboard :%d, alertas :%d"
                    % (config.PUERTO_API, config.PUERTO_SOCKETIO))

    # ------------------------------------------------------------------ API
    def process_frame(self, img: np.ndarray, camera_id: str,
                      camera_name: str | None = None,
                      track_classes: list[int] | None = None,
                      draw: bool = True,
                      roi: Any = None,
                      roi_activate: bool = False) -> tuple[np.ndarray, dict[str, Any]]:
        """Procesa UN frame de UNA cámara del cliente. Nunca lanza: ante un
        error interno devuelve el frame original con metadata vacía.

        `roi`/`roi_activate`: polígono del área que dibuja el cliente. Si no
        está activo, el área es todo el frame."""
        try:
            return self._procesar(img, camera_id, camera_name, draw,
                                  roi, roi_activate)
        except Exception:
            logger.exception(f"error procesando frame de '{camera_id}'")
            return img, {"detections": [], "alerts": []}

    # ------------------------------------------------------------------ núcleo
    def _procesar(self, img: np.ndarray, camera_id: str,
                  camera_name: str | None, draw: bool,
                  roi: Any, roi_activate: bool) -> tuple[np.ndarray, dict[str, Any]]:
        camara = camera_name or camera_id
        with self._lock:
            self._seq[camara] += 1
            seq = self._seq[camara]

        # ROI del cliente (None si no hay o no está activo).
        poligono = _roi_a_poligono(roi, roi_activate)

        crudas = self.detector.detectar_lote([img])[0]
        with self._lock:
            # El tracker ve TODAS las detecciones (IDs estables al cruzar el
            # borde del ROI); el filtro por ROI se aplica DESPUÉS.
            dets = self.rastreadores.actualizar(camara, crudas)
            n_antes = len(dets)
            if config.DETECCION_SOLO_EN_ROI and poligono is not None:
                dets = _filtrar_por_roi(dets, poligono)
            # Clase FINA de vehículos (carro/camioneta/camión) con votación
            # por track — antes de área/Re-ID para que toda la cadena la vea.
            dets = self.vehiculos.refinar(camara, seq, img, dets)

        # AVISO: frame diminuto = casi seguro la ventana capturada está
        # MINIMIZADA en el cliente (Windows no captura ventanas minimizadas;
        # entrega una tira de ~160x28). Sin escena no hay detecciones.
        if (img.shape[1] < 320 or img.shape[0] < 180) and seq % 100 == 1:
            logger.warning(
                f"'{camara}': el cliente envía frames de "
                f"{img.shape[1]}x{img.shape[0]} px — ¿ventana minimizada o "
                f"selección incorrecta? Restaure la ventana en el cliente.",
                extra={"datos": {"camara": camara, "ancho": int(img.shape[1]),
                                 "alto": int(img.shape[0])}})

        # Telemetría: primer frame de cada cámara y luego cada 100 (para ver
        # de un vistazo en el log si el detector "ve" algo y a qué resolución).
        if seq == 1 or seq % 100 == 0:
            roi_txt = (f", {n_antes}->{len(dets)} tras filtro ROI"
                       if config.DETECCION_SOLO_EN_ROI and poligono is not None
                       else "")
            logger.info(
                f"'{camara}': frame {seq} ({img.shape[1]}x{img.shape[0]}), "
                f"{len(crudas)} detección(es) crudas, {len(dets)} rastreada(s)"
                f"{roi_txt}, clases={sorted({d.clase for d in dets})}",
                extra={"datos": {"camara": camara, "seq": seq,
                                 "ancho": int(img.shape[1]),
                                 "alto": int(img.shape[0]),
                                 "crudas": len(crudas),
                                 "rastreadas": len(dets)}})

        fc = FrameCamara(camara=camara, frame=img, secuencia=seq,
                         timestamp=time.time())
        # personal_seguridad (CLIP) reemplaza a persona donde aplique.
        if self._servicios.seguridad is not None and self._servicios.seguridad.disponible:
            dets = self._servicios.seguridad.transformador(fc, dets)
        # Re-ID de personas de interés (no bloqueante para el VLM).
        if self._servicios.motor_reid is not None:
            self._servicios.motor_reid.suscriptor(fc, dets)

        # Permanencia en el área + alertas de LLEGADA/SALIDA (7 clases).
        # (dets ya viene filtrada al ROI si DETECCION_SOLO_EN_ROI; el área
        # re-verifica con el mismo criterio, inofensivo.)
        with self._lock:
            contadores, tarjetas_area = self.area.actualizar(camara, img, dets,
                                                             poligono)
            identidades = self._identidades_de(camara)
            for t in tarjetas_area:
                self._tarjetas[camara].append(t)
            tarjetas = self._drenar_tarjetas(camara)

        # Reenvío a WhatsApp: cada alerta sale como imagen al bot SOLO si el
        # cliente activó el toggle (flag fijado en el procesador por app.py).
        # No bloquea: el emisor usa hilo daemon + anti-flood.
        if getattr(self, "_enviar_whatsapp", False) and tarjetas:
            for t in tarjetas:
                self._notificar_whatsapp(t)

        detections = self._a_detections(dets, identidades, contadores)
        if draw:
            etiquetas: dict[int, str] = {}
            for d in dets:
                partes: list[str] = []
                if d.track_id in identidades:
                    nombre, nivel = identidades[d.track_id]
                    partes.append(f"{nombre} ({nivel})")
                if d.track_id in contadores:
                    partes.append(f"⏱{contadores[d.track_id]}")
                if partes:
                    etiquetas[d.track_id] = " ".join(partes)
            img_salida = anotar_frame(img, dets, etiquetas)
        else:
            img_salida = img
        return img_salida, {"detections": detections, "alerts": tarjetas}

    # ------------------------------------------------------------ conversores
    @staticmethod
    def _a_detections(dets: list[DeteccionVig],
                      identidades: dict[int, tuple[str, str]],
                      contadores: dict[int, str] | None = None
                      ) -> list[dict[str, Any]]:
        contadores = contadores or {}
        salida: list[dict[str, Any]] = []
        for d in dets:
            # Etiqueta: "<clase> #<id> · <cronómetro>" (o el nombre si es
            # persona de interés). El cronómetro es la permanencia en el área.
            etiqueta = f"{d.clase} #{d.track_id}"
            if d.track_id in identidades:
                nombre, nivel = identidades[d.track_id]
                etiqueta = f"{nombre} ({nivel}) — {d.clase}"
            if d.track_id in contadores:
                etiqueta += f" · {contadores[d.track_id]}"
            b, g, r = color_de(d.clase)
            salida.append({
                "box": [int(v) for v in d.bbox],
                "label": etiqueta,
                "confidence": round(float(d.conf), 3),
                "class_id": CLASE_A_ID.get(d.clase, 0),
                "tracker_id": int(d.track_id),
                "permanencia": contadores.get(d.track_id, ""),
                "color": [int(r), int(g), int(b)],   # el cliente espera RGB
            })
        return salida

    # ------------------------------------------------------------- consumidor
    def _consumidor_reid(self, evento: EventoReID) -> None:
        """EventoReID -> tarjeta del AlertsSidebar + rótulo sobre la caja."""
        with self._lock:
            ok, jpg = cv2.imencode(".jpg", evento.crop,
                                   [cv2.IMWRITE_JPEG_QUALITY, 80])
            crop_b64 = base64.b64encode(jpg.tobytes()).decode() if ok else ""
            self._tarjetas[evento.camara].append({
                "event_type": "Alerta",
                "class_name": evento.persona,
                "clase_gruesa": "persona",
                "global_id": f"VIG-{evento.persona_id}",
                "description": (f"PERSONA DE INTERÉS ({evento.nivel.upper()})"
                                f" — match {evento.tipo_match}"
                                f" score {evento.score:.2f}"),
                "timestamp": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(evento.timestamp)),
                "image_base64": crop_b64,
                "camera_name": evento.camara,
            })
            self._identidades[(evento.camara, evento.track_id)] = (
                evento.persona, evento.nivel, time.time() + TTL_IDENTIDAD_SEG)

    # ------------------------------------------------------------------ drenes
    def _drenar_tarjetas(self, camara: str) -> list[dict[str, Any]]:
        cola = self._tarjetas.get(camara)
        if not cola:
            return []
        tarjetas = list(cola)
        cola.clear()
        return tarjetas

    def _identidades_de(self, camara: str) -> dict[int, tuple[str, str]]:
        ahora = time.time()
        vencidas = [k for k, (_, _, exp) in self._identidades.items() if exp < ahora]
        for k in vencidas:
            del self._identidades[k]
        return {tid: (nombre, nivel)
                for (cam, tid), (nombre, nivel, _) in self._identidades.items()
                if cam == camara}

    # -------------------------------------------------------------- whatsapp
    def _notificar_whatsapp(self, tarjeta: dict[str, Any]) -> None:
        """Reenvía una tarjeta de alerta como imagen a WhatsApp (bot 'ava').

        Solo se invoca si el cliente activó el toggle. Por defecto avisa
        UNICAMENTE de la entrada de una persona o un vehículo al perímetro
        marcado por el ROI (config.WHATSAPP_EVENTOS / WHATSAPP_CLASES): las
        detecciones ya vienen recortadas al ROI porque
        DETECCION_SOLO_EN_ROI está activo. Nunca lanza.
        """
        try:
            evento = str(tarjeta.get("event_type") or "").strip().lower()
            if config.WHATSAPP_EVENTOS and evento not in config.WHATSAPP_EVENTOS:
                return
            gruesa = str(tarjeta.get("clase_gruesa") or "").strip().lower()
            if config.WHATSAPP_CLASES and gruesa not in config.WHATSAPP_CLASES:
                return
            img_b64 = tarjeta.get("image_base64") or ""
            if not img_b64:
                return
            clase = str(tarjeta.get("class_name") or "detección")
            cam = str(tarjeta.get("camera_name") or "")
            desc = str(tarjeta.get("description") or "")
            ts = str(tarjeta.get("timestamp") or "")
            gid = str(tarjeta.get("global_id") or "")
            icono = "🚗" if gruesa == "vehiculo" else "🚶"
            texto = f"🚨 VIGILANTE · {icono} {clase} ENTRÓ AL PERÍMETRO"
            if cam:
                texto += f" · cámara {cam}"
            if desc:
                texto += f"\n{desc}"
            if ts:
                texto += f"\n🕒 {ts}"
            # Clave anti-flood: misma persona/evento en la misma cámara no se
            # reenvía más de una vez por WHATSAPP_COOLDOWN_SEG.
            clave = f"{gid}|{clase}|{cam}"
            get_emisor_whatsapp().enviar_b64(img_b64, texto, clave)
        except Exception:
            logger.exception("fallo notificando alerta por WhatsApp")


# -----------------------------------------------------------------------------
# Utilidad: ROI del cliente -> polígono para el rastreador de área.
# -----------------------------------------------------------------------------

def _filtrar_por_roi(dets: list[DeteccionVig],
                     poligono: np.ndarray) -> list[DeteccionVig]:
    """Deja solo las detecciones cuya BASE (centro-inferior del bbox) esté
    dentro del polígono del ROI — mismo criterio que el rastreador de área
    (los pies de la persona / el apoyo del vehículo)."""
    dentro: list[DeteccionVig] = []
    for d in dets:
        x1, y1, x2, y2 = d.bbox
        base = (float((x1 + x2) / 2.0), float(y2))
        if cv2.pointPolygonTest(poligono, base, False) >= 0:
            dentro.append(d)
    return dentro


def _roi_a_poligono(roi: Any, roi_activate: bool) -> np.ndarray | None:
    """Convierte el ROI del cliente en un polígono (N,1,2) int32, o None.

    El cliente envía `roi` como lista de puntos [x, y] en PÍXELES del frame
    (interactive_imageLabel._coords_pixels). Se necesitan >= 3 puntos y que el
    ROI esté activo; en cualquier otro caso el área es todo el frame (None).
    """
    if not roi_activate or not roi:
        return None
    try:
        puntos: list[list[int]] = []
        for p in roi:
            if isinstance(p, dict):
                puntos.append([int(p.get("x", 0)), int(p.get("y", 0))])
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                puntos.append([int(p[0]), int(p[1])])
        if len(puntos) < 3:
            return None
        return np.array(puntos, dtype=np.int32).reshape((-1, 1, 2))
    except Exception:
        logger.warning("ROI inválido recibido del cliente; se usa el frame completo")
        return None


# -----------------------------------------------------------------------------
# Singleton y dashboard compartido.
# -----------------------------------------------------------------------------

_instancia: VigilanteWS | None = None
_lock_instancia = threading.Lock()


def _iniciar_dashboard_una_vez() -> None:
    """Levanta el dashboard :8090 si aún no está arriba (idempotente: si ya lo
    lanzó el servidor al arrancar, esto no hace nada)."""
    from vigilante_amazonas.web.lanzador import iniciar_dashboard
    iniciar_dashboard()


def get_vigilante_ws() -> VigilanteWS:
    """Singleton por proceso (contrato con src/app/app.py)."""
    global _instancia
    with _lock_instancia:
        if _instancia is None:
            _instancia = VigilanteWS()
        return _instancia
