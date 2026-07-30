"""
Motor de Re-Identificación (Hito 4): decide si una persona detectada es una
persona de interés de la galería.

Flujo por track (con throttle REID_CADA_N_FRAMES):
  crop persona ─► rostro (YuNet+ArcFace) ──► buscar_rostro ──┐
              └─► vestimenta (OSNet/CLIP) ─► buscar_vestim. ─┤
                                                             ▼
                                              decisión (fusión de scores)
    - rostro >= UMBRAL_ROSTRO                         => match "rostro"
    - vestimenta >= UMBRAL_VESTIMENTA                 => match "vestimenta"
    - ambos a la vez (misma persona)                  => "ambos" (+ bono)
    - mejor score en ZONA_GRIS_VLM                    => verificación VLM
      (no bloqueante: el evento queda "pendiente" y se emite/descarta con la
       respuesta del VLM; sin VLM aplica ZONA_GRIS_SIN_VLM_EMITIR)
  Cooldown anti-spam por (persona, cámara): COOLDOWN_ALERTA_SEG.

Los consumidores registrados reciben EventoReID ya CONFIRMADOS (el emisor de
alertas, el adaptador websocket y el puente Perimetrales).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.captura.fuente_rtsp import FrameCamara
from vigilante_amazonas.deteccion.rastreador import DeteccionVig
from vigilante_amazonas.servicios.embebedor_rostro import EmbebedorRostro
from vigilante_amazonas.servicios.embebedor_vestimenta import EmbebedorVestimenta
from vigilante_amazonas.servicios.galeria import Galeria, ResultadoBusqueda
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


@dataclass
class EventoReID:
    """Match confirmado de una persona de interés."""
    camara: str
    track_id: int
    persona_id: int
    persona: str
    nivel: str
    score: float
    tipo_match: str            # "rostro" | "vestimenta" | "ambos"
    crop: np.ndarray           # BGR de la persona en el momento del match
    timestamp: float = field(default_factory=time.time)
    verificacion: str = "no_requerida"   # no_requerida | vlm_confirmada | sin_vlm


ConsumidorEvento = Callable[[EventoReID], None]


class MotorReID:
    """Servicio de Re-ID compartido entre todos los modos del servidor."""

    def __init__(self) -> None:
        self.rostro = EmbebedorRostro()
        self.vestimenta = EmbebedorVestimenta()
        self.galeria = Galeria()
        self._consumidores: list[ConsumidorEvento] = []
        self._vlm: Any = None
        self._lock = threading.RLock()
        # Cooldown: (persona_id, camara) -> timestamp de la última alerta.
        self._cooldown: dict[tuple[int, str], float] = {}
        # Pendientes de VLM: evita reencolar el mismo (track, persona).
        self._pendientes_vlm: set[tuple[str, int, int]] = set()

    # ------------------------------------------------------------------ API
    def registrar_consumidor(self, fn: ConsumidorEvento) -> None:
        with self._lock:
            if fn not in self._consumidores:
                self._consumidores.append(fn)

    def conectar_vlm(self, vlm: Any) -> None:
        """Conecta el verificador VLM (Hito 6). Debe exponer
        encolar(crop, referencias, contexto, callback)."""
        self._vlm = vlm

    def suscriptor(self, fc: FrameCamara, dets: list[DeteccionVig]) -> None:
        """Punto de entrada: procesa las personas rastreadas de UN frame.
        Contrato usado por el motor propio, el adaptador websocket y el
        puente del modo Perimetrales."""
        candidatos = [d for d in dets
                      if d.clase in ("persona", "personal_seguridad")
                      and (fc.secuencia + d.track_id) % config.REID_CADA_N_FRAMES == 0]
        if not candidatos:
            return
        tam = self.galeria.tamano
        if tam["rostros"] == 0 and tam["vestimentas"] == 0:
            return
        for d in candidatos:
            try:
                self._procesar_persona(fc, d)
            except Exception:
                logger.exception(f"Re-ID falló para track {d.track_id} "
                                 f"de '{fc.camara}' (se ignora)")

    # ------------------------------------------------------------------ núcleo
    def _procesar_persona(self, fc: FrameCamara, d: DeteccionVig) -> None:
        crop = self._recortar(fc.frame, d.bbox)
        if crop.size == 0:
            return

        # --- señal 1: rostro ---
        res_rostro: ResultadoBusqueda | None = None
        score_rostro = 0.0
        salida_rostro = self.rostro.embeber(crop)
        if salida_rostro is not None:
            res_rostro = self.galeria.buscar_rostro(salida_rostro[0])
            if res_rostro is not None:
                score_rostro = res_rostro.score

        # --- señal 2: vestimenta ---
        res_vest: ResultadoBusqueda | None = None
        score_vest = 0.0
        alto, ancho = crop.shape[:2]
        if alto >= config.VESTIMENTA_CROP_MIN_PX and ancho >= config.VESTIMENTA_CROP_MIN_PX // 2:
            embs = self.vestimenta.embeber([crop])
            if embs.shape[0] > 0:
                res_vest = self.galeria.buscar_vestimenta(embs[0])
                if res_vest is not None:
                    score_vest = res_vest.score

        self._decidir(fc, d, crop, res_rostro, score_rostro, res_vest, score_vest)

    def _decidir(self, fc: FrameCamara, d: DeteccionVig, crop: np.ndarray,
                 res_rostro: ResultadoBusqueda | None, score_rostro: float,
                 res_vest: ResultadoBusqueda | None, score_vest: float) -> None:
        rostro_ok = res_rostro is not None and score_rostro >= config.UMBRAL_ROSTRO
        vest_ok = res_vest is not None and score_vest >= config.UMBRAL_VESTIMENTA
        ambos = (rostro_ok and vest_ok and
                 res_rostro.persona_id == res_vest.persona_id)

        if ambos:
            score = min(1.0, max(score_rostro, score_vest) + config.FUSION_BONO_AMBOS)
            self._emitir(fc, d, crop, res_rostro, score, "ambos", "no_requerida")
            return
        if rostro_ok:
            self._emitir(fc, d, crop, res_rostro, score_rostro, "rostro", "no_requerida")
            return
        if vest_ok:
            self._emitir(fc, d, crop, res_vest, score_vest, "vestimenta", "no_requerida")
            return

        # Zona gris: el mejor candidato entra a verificación VLM.
        gris_min, gris_max = config.ZONA_GRIS_VLM
        mejor_res, mejor_score, mejor_tipo = (
            (res_rostro, score_rostro, "rostro")
            if score_rostro >= score_vest else (res_vest, score_vest, "vestimenta"))
        if mejor_res is None or not (gris_min <= mejor_score < gris_max):
            return
        self._verificar_zona_gris(fc, d, crop, mejor_res, mejor_score, mejor_tipo)

    # -------------------------------------------------------------- zona gris
    def _verificar_zona_gris(self, fc: FrameCamara, d: DeteccionVig,
                             crop: np.ndarray, res: ResultadoBusqueda,
                             score: float, tipo: str) -> None:
        if self._en_cooldown(res.persona_id, fc.camara):
            return
        if self._vlm is None or not getattr(self._vlm, "disponible", False):
            if config.ZONA_GRIS_SIN_VLM_EMITIR:
                self._emitir(fc, d, crop, res, score, tipo, "sin_vlm")
            return

        clave = (fc.camara, d.track_id, res.persona_id)
        with self._lock:
            if clave in self._pendientes_vlm:
                return
            self._pendientes_vlm.add(clave)
        logger.info(
            f"zona gris: '{res.persona}' score={score:.2f} ({tipo}) en "
            f"'{fc.camara}' -> encolado a VLM (pendiente de verificación)",
            extra={"datos": {"persona": res.persona, "score": round(score, 3),
                             "tipo": tipo, "camara": fc.camara}})

        def _resolver(veredicto: dict[str, Any]) -> None:
            with self._lock:
                self._pendientes_vlm.discard(clave)
            misma = bool(veredicto.get("misma_persona", False))
            confianza = float(veredicto.get("confianza", 0.0))
            razon = str(veredicto.get("razon", ""))[:200]
            logger.info(
                f"VLM resolvió zona gris de '{res.persona}': "
                f"{'CONFIRMADA' if misma else 'DESCARTADA'} "
                f"(confianza {confianza:.2f}) — {razon}",
                extra={"datos": {"persona": res.persona, "misma": misma,
                                 "confianza": confianza, "razon": razon}})
            if misma:
                score_final = min(1.0, 0.5 * (score + confianza) + 0.1)
                self._emitir(fc, d, crop, res, score_final, tipo, "vlm_confirmada")

        self._vlm.encolar(crop=crop, persona_id=res.persona_id, tipo=tipo,
                          callback=_resolver)

    # ------------------------------------------------------------------ emisión
    def _emitir(self, fc: FrameCamara, d: DeteccionVig, crop: np.ndarray,
                res: ResultadoBusqueda, score: float, tipo: str,
                verificacion: str) -> None:
        if self._en_cooldown(res.persona_id, fc.camara, marcar=True):
            return
        # timestamp = instante de CAPTURA del frame (no de la emisión): así la
        # latencia captura->alerta es medible y las tarjetas muestran la hora
        # real del avistamiento (relevante en la ruta VLM, que emite después).
        evento = EventoReID(camara=fc.camara, track_id=d.track_id,
                            persona_id=res.persona_id, persona=res.persona,
                            nivel=res.nivel, score=round(float(score), 4),
                            tipo_match=tipo, crop=crop.copy(),
                            timestamp=fc.timestamp,
                            verificacion=verificacion)
        logger.info(
            f"MATCH persona de interés: '{evento.persona}' ({evento.nivel}) "
            f"score={evento.score:.2f} tipo={tipo} cámara='{fc.camara}' "
            f"track={d.track_id} verificación={verificacion}",
            extra={"datos": {"persona": evento.persona, "nivel": evento.nivel,
                             "score": evento.score, "tipo_match": tipo,
                             "camara": fc.camara, "track_id": d.track_id,
                             "verificacion": verificacion}})
        with self._lock:
            consumidores = list(self._consumidores)
        for fn in consumidores:
            try:
                fn(evento)
            except Exception:
                logger.exception("consumidor de EventoReID falló (se ignora)")

    def _en_cooldown(self, persona_id: int, camara: str,
                     marcar: bool = False) -> bool:
        clave = (persona_id, camara)
        ahora = time.time()
        with self._lock:
            ultimo = self._cooldown.get(clave, 0.0)
            if ahora - ultimo < config.COOLDOWN_ALERTA_SEG:
                return True
            if marcar:
                self._cooldown[clave] = ahora
        return False

    @staticmethod
    def _recortar(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
        x1, y1, x2, y2 = bbox
        alto, ancho = frame.shape[:2]
        return frame[max(0, y1):min(alto, y2), max(0, x1):min(ancho, x2)]
