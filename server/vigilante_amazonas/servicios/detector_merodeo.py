"""
Detector de MERODEO: el mismo individuo/vehículo que entra y sale del área
varias veces en poco tiempo.

Cómo reconoce que "es el mismo" si cada visita trae un track_id distinto:
por APARIENCIA. En cada LLEGADA confirmada se calcula el embedding CLIP del
recorte (la ropa de la persona, el color/forma del vehículo) y se compara por
similitud coseno contra los visitantes recientes de esa cámara y familia
(persona/vehículo). Si coincide (>= MERODEO_SIMILITUD), es otra visita del
mismo visitante; al acumular MERODEO_MIN_VISITAS dentro de
MERODEO_VENTANA_SEG se emite la tarjeta "merodeo".

Limitaciones honestas (documentadas):
  - Si la persona se cambia de ropa entre visitas, cuenta como visitante
    nuevo (la apariencia ES la identidad aquí).
  - Dos vehículos idénticos (mismo modelo y color) pueden confundirse.
El registro es EFÍMERO (TTL) y en memoria: no persiste entre reinicios.

Thread-safety: se invoca dentro del lock de VigilanteWS (vía RastreadorArea);
no toma locks propios.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


@dataclass
class _Visitante:
    """Un visitante recurrente reconocido por apariencia en una cámara."""
    visitante_id: int
    clase: str
    embeddings: list[np.ndarray] = field(default_factory=list)
    visitas: list[float] = field(default_factory=list)   # timestamps de llegada
    ultima_vez: float = 0.0
    ultima_alerta_merodeo: float = 0.0


class DetectorMerodeo:
    """Registro de visitantes por apariencia + alerta de merodeo."""

    def __init__(self) -> None:
        self.disponible: bool = bool(config.MERODEO_HABILITADO)
        self._clip = None            # carga perezosa (comparte el CLIP del proceso)
        self._clip_fallo: bool = False
        # (camara, familia) -> lista de visitantes recientes.
        self._registro: dict[tuple[str, str], list[_Visitante]] = {}
        self._siguiente_id: int = 1

    # ------------------------------------------------------------------ API
    def registrar_llegada(self, camara: str, clase: str, crop: np.ndarray,
                          track_id: int) -> dict[str, Any] | None:
        """Registra una LLEGADA confirmada. Devuelve la tarjeta de MERODEO si
        este visitante acumuló suficientes entradas; None en caso normal.

        Nunca lanza: ante cualquier fallo se registra y se devuelve None.
        """
        if not self.disponible or clase not in config.MERODEO_CLASES:
            return None
        if crop is None or crop.size == 0 or \
                min(crop.shape[0], crop.shape[1]) < config.MERODEO_CROP_MIN_PX:
            return None
        try:
            return self._procesar(camara, clase, crop, track_id)
        except Exception:
            logger.exception("fallo en detector de merodeo (se ignora)")
            return None

    # ------------------------------------------------------------------ interno
    def _procesar(self, camara: str, clase: str, crop: np.ndarray,
                  track_id: int) -> dict[str, Any] | None:
        emb = self._embeber(crop)
        if emb is None:
            return None
        ahora = time.time()
        familia = config.AREA_CLASE_GRUESA.get(clase, "persona")
        visitantes = self._registro.setdefault((camara, familia), [])
        self._purgar(visitantes, ahora)

        # ¿Coincide con un visitante reciente? (máx. coseno contra sus embeddings)
        mejor: _Visitante | None = None
        mejor_sim = config.MERODEO_SIMILITUD
        for v in visitantes:
            sim = max((float(emb @ e) for e in v.embeddings), default=0.0)
            if sim >= mejor_sim:
                mejor, mejor_sim = v, sim

        if mejor is None:
            # Visitante nuevo: primera visita.
            nuevo = _Visitante(visitante_id=self._siguiente_id, clase=clase,
                               embeddings=[emb], visitas=[ahora],
                               ultima_vez=ahora)
            self._siguiente_id += 1
            visitantes.append(nuevo)
            return None

        # Visita repetida del mismo aspecto.
        mejor.clase = clase
        mejor.ultima_vez = ahora
        mejor.visitas.append(ahora)
        mejor.embeddings.append(emb)
        if len(mejor.embeddings) > config.MERODEO_MAX_EMBEDDINGS:
            mejor.embeddings.pop(0)
        # Solo cuentan las visitas dentro de la ventana.
        mejor.visitas = [t for t in mejor.visitas
                         if ahora - t <= config.MERODEO_VENTANA_SEG]
        n_visitas = len(mejor.visitas)
        logger.info(
            f"visitante recurrente en '{camara}': {clase} "
            f"(id {mejor.visitante_id}, visita {n_visitas}, "
            f"similitud {mejor_sim:.2f})",
            extra={"datos": {"camara": camara, "clase": clase,
                             "visitante": mejor.visitante_id,
                             "visitas": n_visitas,
                             "similitud": round(mejor_sim, 3)}})

        if n_visitas < config.MERODEO_MIN_VISITAS:
            return None
        if ahora - mejor.ultima_alerta_merodeo < config.MERODEO_COOLDOWN_SEG:
            return None
        mejor.ultima_alerta_merodeo = ahora
        return self._tarjeta(camara, mejor, crop, track_id, ahora, mejor_sim)

    def _tarjeta(self, camara: str, v: _Visitante, crop: np.ndarray,
                 track_id: int, ahora: float, similitud: float) -> dict[str, Any]:
        import base64
        import cv2

        etiqueta = config.AREA_CLASE_ETIQUETA.get(v.clase, v.clase.upper())
        minutos = max(1, int((ahora - v.visitas[0]) / 60.0)) \
            if len(v.visitas) > 1 else 1
        ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        crop_b64 = base64.b64encode(jpg.tobytes()).decode() if ok else ""
        descripcion = (f"MERODEO: {etiqueta} entró {len(v.visitas)} veces al "
                       f"área en {minutos} min (similitud {similitud:.2f})")
        logger.info(descripcion,
                    extra={"datos": {"camara": camara,
                                     "visitante": v.visitante_id,
                                     "visitas": len(v.visitas)}})
        return {
            "event_type": "merodeo",
            "class_name": etiqueta,
            "clase_gruesa": config.AREA_CLASE_GRUESA.get(v.clase, "persona"),
            "global_id": f"VIG-MER-{camara}-{v.visitante_id}",
            "hora_llegada": v.visitas[0],
            "hora_salida": None,
            "permanencia_s": None,
            "visitas": len(v.visitas),
            "description": descripcion,
            "timestamp": ahora,
            "image_base64": crop_b64,
            "camera_name": camara,
            "camera_id": camara,
        }

    # ------------------------------------------------------------------ soporte
    def _embeber(self, crop: np.ndarray) -> np.ndarray | None:
        """Embedding CLIP L2-normalizado del recorte (o None si CLIP falta)."""
        if self._clip_fallo:
            return None
        if self._clip is None:
            try:
                from vigilante_amazonas.servicios.clip_compartido import get_clip
                self._clip = get_clip()
            except Exception as exc:
                self._clip_fallo = True
                logger.warning(f"merodeo sin CLIP disponible ({exc}); deshabilitado")
                return None
        vecs = self._clip.codificar_imagenes([crop])
        return vecs[0] if vecs.shape[0] else None

    @staticmethod
    def _purgar(visitantes: list[_Visitante], ahora: float) -> None:
        visitantes[:] = [v for v in visitantes
                         if ahora - v.ultima_vez <= config.MERODEO_TTL_SEG]
