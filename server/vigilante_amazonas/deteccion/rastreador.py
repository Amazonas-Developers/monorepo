"""
Tracking ByteTrack por cámara con IDs estables (vía supervision).

Expone `DeteccionVig`, el contrato de detección+track que consumen el motor
de Re-ID, el clasificador de seguridad y el puente del modo Perimetrales
(src/analityc/core/puente_vigilante.py importa DeteccionVig de aquí).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import supervision as sv

from vigilante_amazonas import config
from vigilante_amazonas.deteccion.detector import DeteccionCruda
from vigilante_amazonas.deteccion.mapeo_clases import CLASE_A_ID, ID_A_CLASE


@dataclass
class DeteccionVig:
    """Detección rastreada: contrato estable hacia el resto del sistema."""
    track_id: int
    bbox: tuple[int, int, int, int]
    conf: float
    clase: str
    cls_coco: int


class RastreadorCamara:
    """ByteTrack de UNA cámara (los IDs son estables por cámara)."""

    def __init__(self) -> None:
        fps = max(1, config.FPS_PROCESAMIENTO)
        self._tracker = sv.ByteTrack(
            track_activation_threshold=config.TRACK_ACTIVATION_UMBRAL,
            lost_track_buffer=int(config.TRACK_PERDIDO_SEG * fps),
            minimum_matching_threshold=config.TRACK_MATCH_UMBRAL,
            frame_rate=fps,
        )
        # GOTCHA supervision: internamente reescala el buffer con
        # max_time_lost = frame_rate/30 * lost_track_buffer -> con fps=10 y
        # buffer de 20 frames quedaban SOLO 6 frames (<1 s): cualquier
        # parpadeo de detección mataba el ID (contador a 0 y llegada falsa).
        # Se fuerza el valor REAL deseado: TRACK_PERDIDO_SEG segundos a fps.
        frames_perdido = max(1, int(config.TRACK_PERDIDO_SEG * fps))
        for atributo in ("max_time_lost", "maximum_frames_without_update"):
            if hasattr(self._tracker, atributo):
                setattr(self._tracker, atributo, frames_perdido)
        # GOTCHA supervision #2 (3-ago-2026): ByteTrack exige internamente
        # score >= activacion + 0.1 (`det_thresh`) para NACER un track. Con
        # la activacion historica 0.25, un carro de 0.32 no podia INICIAR
        # su track (pedia 0.35) — y las motos (mediana 0.16) jamas. La
        # compuerta de confianza ya vive POR CLASE en el detector, asi que
        # el nacimiento usa el MISMO umbral de activacion, sin el +0.1.
        if hasattr(self._tracker, "det_thresh"):
            self._tracker.det_thresh = config.TRACK_ACTIVATION_UMBRAL

    def actualizar(self, dets: list[DeteccionCruda]) -> list[DeteccionVig]:
        """Asigna track_id a las detecciones crudas de un frame."""
        if not dets:
            # Avanzar el tracker igual (envejece y cierra tracks perdidos).
            self._tracker.update_with_detections(sv.Detections.empty())
            return []

        sv_dets = sv.Detections(
            xyxy=np.array([d.bbox for d in dets], dtype=float),
            confidence=np.array([d.conf for d in dets], dtype=float),
            class_id=np.array([CLASE_A_ID[d.clase] for d in dets], dtype=int),
        )
        # data extra para recuperar la clase COCO tras el tracking.
        sv_dets.data["cls_coco"] = np.array([d.cls_coco for d in dets], dtype=int)

        rastreadas = self._tracker.update_with_detections(sv_dets)
        salida: list[DeteccionVig] = []
        if rastreadas.tracker_id is None:
            return salida
        for i in range(len(rastreadas)):
            tid = rastreadas.tracker_id[i]
            if tid is None:
                continue
            x1, y1, x2, y2 = rastreadas.xyxy[i]
            cid = int(rastreadas.class_id[i])
            salida.append(DeteccionVig(
                track_id=int(tid),
                bbox=(int(x1), int(y1), int(x2), int(y2)),
                conf=float(rastreadas.confidence[i]),
                clase=ID_A_CLASE.get(cid, "persona"),
                cls_coco=int(rastreadas.data["cls_coco"][i])
                if "cls_coco" in rastreadas.data else -1,
            ))
        return salida


class GestorRastreadores:
    """Un RastreadorCamara por cámara, creado bajo demanda."""

    def __init__(self) -> None:
        self._por_camara: dict[str, RastreadorCamara] = {}

    def actualizar(self, camara: str, dets: list[DeteccionCruda]) -> list[DeteccionVig]:
        if camara not in self._por_camara:
            self._por_camara[camara] = RastreadorCamara()
        return self._por_camara[camara].actualizar(dets)

    def reiniciar(self, camara: str) -> None:
        """Descarta el tracker de una cámara (p. ej. tras reconexión larga)."""
        self._por_camara.pop(camara, None)
