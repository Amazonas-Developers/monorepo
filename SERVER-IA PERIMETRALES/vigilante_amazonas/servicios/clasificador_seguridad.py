"""
Clasificador de PERSONAL DE SEGURIDAD (Hito 3).

Pipeline: detección `persona` -> crop -> CLIP zero-shot (prompts positivos de
guardia/uniforme vs negativos de civil) -> votación por track -> la clase
`personal_seguridad` REEMPLAZA a `persona` en la salida.

Diseño anti-parpadeo:
  - Throttle por track: solo se clasifica cada SEGURIDAD_CADA_N_FRAMES
    frames, escalonado por track_id para repartir el costo.
  - Votación: SEGURIDAD_VOTOS_MIN positivos consecutivos confirman; una vez
    confirmado, el estado es pegajoso mientras el track siga vivo (con TTL).
"""

from __future__ import annotations

import time

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.captura.fuente_rtsp import FrameCamara
from vigilante_amazonas.deteccion.rastreador import DeteccionVig
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_TTL_ESTADO_SEG: float = 120.0     # limpiar estado de tracks sin actividad


class ClasificadorSeguridad:
    """CLIP zero-shot sobre crops de persona, con votación por track."""

    def __init__(self) -> None:
        self.disponible: bool = False
        self._clip = None
        self._feats_texto: np.ndarray | None = None
        self._n_positivos: int = len(config.SEGURIDAD_PROMPTS_POSITIVOS)
        # Estado por (camara, track_id): [votos, confirmado, ultimo_uso]
        self._estado: dict[tuple[str, int], list[float]] = {}
        if config.SEGURIDAD_HABILITADO:
            try:
                self._cargar()
                self.disponible = True
            except Exception as exc:
                logger.error(f"clasificador de seguridad NO disponible: {exc}")

    def _cargar(self) -> None:
        from vigilante_amazonas.servicios.clip_compartido import get_clip
        self._clip = get_clip()
        textos = (config.SEGURIDAD_PROMPTS_POSITIVOS +
                  config.SEGURIDAD_PROMPTS_NEGATIVOS)
        self._feats_texto = self._clip.codificar_textos(textos)
        logger.info(
            "clasificador de seguridad listo "
            f"({self._n_positivos} prompts positivos, "
            f"{len(config.SEGURIDAD_PROMPTS_NEGATIVOS)} negativos, "
            f"umbral {config.SEGURIDAD_UMBRAL})")

    # ------------------------------------------------------------------ API
    def transformador(self, fc: FrameCamara,
                      dets: list[DeteccionVig]) -> list[DeteccionVig]:
        """Reetiqueta a `personal_seguridad` las personas confirmadas.

        Contrato usado por el puente del modo Perimetrales:
        recibe y devuelve la lista completa de detecciones del frame.
        """
        if not self.disponible or not dets:
            return dets

        # 1) Elegir qué tracks toca clasificar en ESTE frame (throttle).
        a_clasificar: list[DeteccionVig] = []
        for d in dets:
            if d.clase != "persona":
                continue
            clave = (fc.camara, d.track_id)
            estado = self._estado.get(clave)
            if estado is not None and estado[1] >= 1.0:
                estado[2] = time.time()      # confirmado: solo refrescar TTL
                continue
            if (fc.secuencia + d.track_id) % config.SEGURIDAD_CADA_N_FRAMES != 0:
                continue
            x1, y1, x2, y2 = d.bbox
            if (x2 - x1) < config.SEGURIDAD_CROP_MIN_PX or \
               (y2 - y1) < config.SEGURIDAD_CROP_MIN_PX:
                continue
            a_clasificar.append(d)

        # 2) Clasificar el lote de crops (un solo forward de CLIP).
        if a_clasificar:
            crops = [self._recortar(fc.frame, d.bbox) for d in a_clasificar]
            probs = self._probabilidad_seguridad(crops)
            for d, prob in zip(a_clasificar, probs):
                self._votar(fc.camara, d.track_id, prob >= config.SEGURIDAD_UMBRAL)

        # 3) Emitir la lista con la clase reemplazada donde corresponda.
        salida: list[DeteccionVig] = []
        for d in dets:
            estado = self._estado.get((fc.camara, d.track_id))
            if d.clase == "persona" and estado is not None and estado[1] >= 1.0:
                salida.append(DeteccionVig(track_id=d.track_id, bbox=d.bbox,
                                           conf=d.conf, clase="personal_seguridad",
                                           cls_coco=d.cls_coco))
            else:
                salida.append(d)
        self._limpiar_estado()
        return salida

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _recortar(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
        x1, y1, x2, y2 = bbox
        alto, ancho = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(ancho, x2), min(alto, y2)
        return frame[y1:y2, x1:x2]

    def _probabilidad_seguridad(self, crops: list[np.ndarray]) -> list[float]:
        """Prob. de "guardia" por crop.

        Doble condición anti-falso-positivo: la masa softmax de los prompts
        positivos debe superar el umbral Y el prompt top-1 debe ser positivo.
        Si el top-1 es negativo se devuelve 0.0 directamente.
        """
        feats_img = self._clip.codificar_imagenes(crops)          # (N, 512)
        logits = 100.0 * feats_img @ self._feats_texto.T          # escala CLIP
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = exp / exp.sum(axis=1, keepdims=True)
        p_pos = probs[:, :self._n_positivos].sum(axis=1)
        top1_positivo = probs.argmax(axis=1) < self._n_positivos
        return [float(p) if top else 0.0
                for p, top in zip(p_pos, top1_positivo)]

    def _votar(self, camara: str, track_id: int, positivo: bool) -> None:
        clave = (camara, track_id)
        estado = self._estado.setdefault(clave, [0.0, 0.0, time.time()])
        estado[2] = time.time()
        if positivo:
            estado[0] += 1.0
            if estado[0] >= config.SEGURIDAD_VOTOS_MIN:
                estado[1] = 1.0
                logger.info(f"personal_seguridad CONFIRMADO track={track_id} cámara={camara}",
                            extra={"datos": {"camara": camara, "track_id": track_id}})
        else:
            estado[0] = 0.0       # los votos deben ser consecutivos

    def _limpiar_estado(self) -> None:
        ahora = time.time()
        vencidos = [k for k, v in self._estado.items() if ahora - v[2] > _TTL_ESTADO_SEG]
        for k in vencidos:
            del self._estado[k]
