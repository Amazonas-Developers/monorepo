"""
Puente VIGILANTE-AMAZONAS para el modo "Perimetrales" (BasePerimeter).

Conecta las detecciones de persona del modo Perimetrales con el núcleo
COMPARTIDO de vigilante_amazonas (vigilante_amazonas.servicios):

  personas de BasePerimeter ─► ClasificadorSeguridad (CLIP, cuda:0)
        │                            └─ reetiqueta "personal_seguridad"
        └─► MotorReID (rostro ArcFace + vestimenta OSNet, cuda:1)
                ├─ confirmados ─► EmisorAlertas (SQLite + Socket.IO :8091)
                ├─ zona gris   ─► VerificadorVLM (cuda:2, no bloqueante)
                └─ tarjetas para el AlertsSidebar del cliente (metadata.alerts)

El puente es SINGLETON por proceso: un solo consumidor registrado en el
MotorReID aunque haya varias conexiones/instancias de BasePerimeter, y las
tarjetas se drenan POR CÁMARA para que cada conexión reciba solo lo suyo.
La galería de personas de interés se administra en el dashboard :8090.

Si el paquete vigilante_amazonas no está disponible, el puente se desactiva
con un aviso en el log y el modo Perimetrales sigue funcionando sin Re-ID.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Segundos que se mantiene el nombre de la persona identificada sobre su caja.
TTL_IDENTIDAD_SEG: float = 10.0
# Tope de tarjetas retenidas por cámara (anti-fuga si nadie las drena).
MAX_TARJETAS_CAMARA: int = 50

# (nombre_persona, nivel_alerta) que BasePerimeter usa para dibujar.
Identidad = Tuple[str, str]
# (track_id, bbox_xyxy, confianza) de una persona detectada por BasePerimeter.
PersonaDetectada = Tuple[int, Tuple[int, int, int, int], float]


class PuenteVigilante:
    """Puente entre BasePerimeter y el núcleo compartido de VIGILANTE."""

    def __init__(self) -> None:
        # RLock: el consumidor puede ejecutarse dentro de procesar() (mismo
        # hilo, vía motor_reid.suscriptor) o desde el hilo del VLM.
        self._lock = threading.RLock()
        self._servicios: Optional[Any] = None
        self._fallo_carga: bool = False
        self._seq: Dict[str, int] = {}
        self._camaras: set[str] = set()
        self._tarjetas: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=MAX_TARJETAS_CAMARA))
        # (camara, track_id) -> (nombre, nivel, ts_expira)
        self._identidades: Dict[Tuple[str, int], Tuple[str, str, float]] = {}

    # ------------------------------------------------------------------ API
    @property
    def activo(self) -> bool:
        """True si el núcleo de VIGILANTE está cargado y en uso."""
        return self._servicios is not None

    def procesar(self,
                 imagen: np.ndarray,
                 camara: str,
                 personas: List[PersonaDetectada]
                 ) -> Tuple[Dict[int, str], List[Dict[str, Any]],
                            Dict[int, Identidad]]:
        """Procesa las personas de UN frame del modo Perimetrales.

        Devuelve:
          refinados:   {track_id: "personal_seguridad"} para el dibujo.
          tarjetas:    alertas de PERSONA DE INTERÉS para metadata['alerts'].
          identidades: {track_id: (nombre, nivel)} para rotular las cajas.

        `imagen` debe ser el frame SIN anotar (los crops de Re-ID no deben
        llevar cajas dibujadas encima).
        """
        if not self._asegurar_servicios():
            return {}, [], {}

        with self._lock:
            self._camaras.add(camara)
            refinados: Dict[int, str] = {}
            if personas:
                refinados = self._clasificar_y_reid(imagen, camara, personas)
            # Drenar SIEMPRE: el VLM confirma pendientes de forma asíncrona.
            tarjetas = self._drenar_tarjetas(camara)
            identidades = self._identidades_de(camara)
        return refinados, tarjetas, identidades

    # ------------------------------------------------------------------ núcleo
    def _asegurar_servicios(self) -> bool:
        """Carga perezosa del núcleo compartido (una sola vez por proceso)."""
        if self._servicios is not None:
            return True
        if self._fallo_carga:
            return False
        with self._lock:
            if self._servicios is not None:
                return True
            try:
                from vigilante_amazonas.servicios import get_servicios
                servicios = get_servicios()
                servicios.motor_reid.registrar_consumidor(self._consumidor)
                self._servicios = servicios
                logger.info(
                    "Puente VIGILANTE activo en modo Perimetrales "
                    "(personal_seguridad + Re-ID + VLM; galeria en :8090, "
                    "alertas Socket.IO en :8091)")
                return True
            except Exception as exc:
                self._fallo_carga = True
                logger.warning(
                    "Puente VIGILANTE no disponible (%s); el modo "
                    "Perimetrales sigue funcionando sin Re-ID", exc)
                return False

    def _clasificar_y_reid(self, imagen: np.ndarray, camara: str,
                           personas: List[PersonaDetectada]) -> Dict[int, str]:
        """CLIP personal_seguridad + MotorReID sobre las personas del frame."""
        from vigilante_amazonas.captura.fuente_rtsp import FrameCamara
        from vigilante_amazonas.deteccion.rastreador import DeteccionVig

        seq = self._seq.get(camara, 0) + 1
        self._seq[camara] = seq
        fc = FrameCamara(camara=camara, frame=imagen, secuencia=seq,
                         timestamp=time.time())
        dets = [DeteccionVig(track_id=int(tid),
                             bbox=(int(b[0]), int(b[1]), int(b[2]), int(b[3])),
                             conf=float(conf), clase="persona", cls_coco=0)
                for tid, b, conf in personas]

        servicios = self._servicios
        if servicios.seguridad.disponible:
            dets = servicios.seguridad.transformador(fc, dets)
        servicios.motor_reid.suscriptor(fc, dets)

        return {d.track_id: d.clase for d in dets
                if d.clase == "personal_seguridad"}

    # ------------------------------------------------------------------ consumidor
    def _consumidor(self, evento: Any) -> None:
        """EventoReID -> tarjeta del sidebar + identidad para el dibujo.

        El MotorReID es compartido entre modos: solo se aceptan eventos de
        las cámaras que procesa ESTE puente.
        """
        with self._lock:
            if evento.camara not in self._camaras:
                return
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
                "timestamp": evento.timestamp,
                "image_base64": crop_b64,
                "camera_name": evento.camara,
            })
            self._identidades[(evento.camara, evento.track_id)] = (
                evento.persona, evento.nivel,
                time.time() + TTL_IDENTIDAD_SEG)

    # ------------------------------------------------------------------ drenajes
    def _drenar_tarjetas(self, camara: str) -> List[Dict[str, Any]]:
        cola = self._tarjetas.get(camara)
        if not cola:
            return []
        tarjetas = list(cola)
        cola.clear()
        return tarjetas

    def _identidades_de(self, camara: str) -> Dict[int, Identidad]:
        """Identidades vigentes de la cámara; purga las expiradas."""
        ahora = time.time()
        vencidas = [k for k, (_, _, exp) in self._identidades.items()
                    if exp < ahora]
        for k in vencidas:
            del self._identidades[k]
        return {tid: (nombre, nivel)
                for (cam, tid), (nombre, nivel, _) in self._identidades.items()
                if cam == camara}


# ---------------------------------------------------------------------------
# Singleton por proceso.
# ---------------------------------------------------------------------------

_instancia: Optional[PuenteVigilante] = None
_lock_instancia = threading.Lock()


def get_puente() -> PuenteVigilante:
    """Devuelve el puente compartido, creándolo la primera vez (barato:
    los modelos solo se cargan cuando llega el primer frame)."""
    global _instancia
    with _lock_instancia:
        if _instancia is None:
            _instancia = PuenteVigilante()
        return _instancia
