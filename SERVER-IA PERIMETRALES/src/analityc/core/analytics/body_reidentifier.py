"""
analytics/body_reidentifier.py - Re-ID corporal/de apariencia (OSNet).

SEGUNDA señal de identidad, complementaria al rostro: cuando una persona NO
muestra la cara (entra de espaldas), su APARIENCIA (cuerpo + vestimenta) se
compara contra una galeria corporal para re-enlazarla con su identidad ya
conocida.

Diseno (decisiones del usuario):
  - El ROSTRO MANDA. El cuerpo solo se usa cuando no hay match facial.
  - El cuerpo NUNCA crea identidades nuevas: solo ENLAZA un track de espaldas
    a un persistent_id que YA fue confirmado por rostro. Asi no infla el
    conteo de visitantes unicos ni lo contamina.
  - EFIMERO con TTL: la ropa cambia dia a dia, asi que los embeddings
    corporales caducan (default ~4 h) y NO se persisten a disco. La galeria
    de ROSTROS (persons.pkl) es la unica permanente y aqui NO se toca.

Modelo: OSNet (osnet_x1_0, torchreid) exportado a ONNX -> onnxruntime-GPU.
Embedding 512-d L2-normalizado, similitud coseno. OJO: el Re-ID corporal es
MENOS discriminativo que el facial (gente con ropa parecida / uniformes da
similitudes altas), por eso el umbral es ALTO y es solo señal secundaria.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Normalizacion ImageNet (OSNet se entreno con ella).
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
# Tamano de entrada OSNet (alto, ancho).
_IN_H, _IN_W = 256, 128


class BodyReidentifier:
    """Galeria EFIMERA de apariencia corporal (OSNet) para re-enlazar tracks
    sin rostro a identidades ya confirmadas por cara."""

    def __init__(self,
                 session=None,
                 similarity_threshold: float = 0.90,
                 ttl_seconds: float = 4 * 3600.0,
                 max_emb_per_person: int = 5):
        """
        Args:
            session: onnxruntime InferenceSession con el OSNet ONNX.
            similarity_threshold: coseno minimo para considerar mismo cuerpo.
                ALTO a proposito (el cuerpo es menos discriminativo que la cara).
            ttl_seconds: vida de un embedding corporal (la ropa cambia).
            max_emb_per_person: plantillas corporales por persona (multi-vista).
        """
        self._session = session
        self._input_name = None
        self._output_name = None
        if self._session is not None:
            try:
                self._input_name = self._session.get_inputs()[0].name
                self._output_name = self._session.get_outputs()[0].name
            except Exception as e:
                logger.error("BodyReidentifier: sesion ONNX invalida: %s", e)
                self._session = None

        self._sim = float(similarity_threshold)
        self._ttl = float(ttl_seconds)
        self._max = int(max_emb_per_person)
        # galeria EN MEMORIA: persistent_id -> [(emb float32(512,), ts), ...]
        self._gallery: Dict[str, List[Tuple[np.ndarray, float]]] = {}
        self._lock = threading.RLock()

    @property
    def is_available(self) -> bool:
        return self._session is not None

    # ── Embedding ─────────────────────────────────────────────────────

    def compute_embedding(self, person_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Calcula el embedding de apariencia (512-d L2) de un recorte de
        persona (cuerpo completo, BGR). None si falla o no hay sesion.
        """
        if self._session is None or person_bgr is None or person_bgr.size == 0:
            return None
        try:
            im = cv2.resize(person_bgr, (_IN_W, _IN_H))
            rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            blob = ((rgb - _MEAN) / _STD).transpose(2, 0, 1)[None]
            out = self._session.run(
                [self._output_name], {self._input_name: blob}
            )[0][0]
            norm = np.linalg.norm(out)
            if norm > 0:
                out = out / norm
            return out.astype(np.float32)
        except Exception as e:
            logger.debug("BodyReidentifier embedding error: %s", e)
            return None

    # ── Galeria (enrolar / matchear) ──────────────────────────────────

    def _prune_locked(self, now: float) -> None:
        """Elimina embeddings caducados (y personas que quedan vacias)."""
        cutoff = now - self._ttl
        vacios = []
        for pid, lst in self._gallery.items():
            lst[:] = [(e, t) for (e, t) in lst if t >= cutoff]
            if not lst:
                vacios.append(pid)
        for pid in vacios:
            del self._gallery[pid]

    def enroll(self, persistent_id: str, embedding: np.ndarray) -> None:
        """Registra una plantilla corporal para una persona YA confirmada por
        rostro. Cap a max_emb_per_person (conserva las mas RECIENTES, que
        reflejan la vestimenta actual)."""
        if embedding is None or not persistent_id:
            return
        now = time.time()
        with self._lock:
            lst = self._gallery.setdefault(persistent_id, [])
            # Evitar plantillas casi-identicas (mantiene diversidad de vista).
            for e, _t in lst:
                if float(np.dot(e, embedding)) > 0.985:
                    # Refrescar su timestamp en vez de duplicar.
                    lst[:] = [(e, now) if (ee is e) else (ee, tt)
                              for (ee, tt) in lst]
                    return
            lst.append((embedding.astype(np.float32), now))
            if len(lst) > self._max:
                del lst[0:len(lst) - self._max]  # tira las mas viejas

    def match(self, embedding: np.ndarray,
              threshold: Optional[float] = None
              ) -> Optional[Tuple[str, float]]:
        """Busca el mejor cuerpo coincidente en la galeria (no caducados).

        Args:
            embedding: embedding corporal (512,) L2 del query.
            threshold: coseno minimo; None usa self._sim.

        Returns:
            (persistent_id, score) si score >= threshold; None si no hay match.
            NUNCA crea identidades: solo devuelve un persistent_id existente.
        """
        if embedding is None:
            return None
        thr = self._sim if threshold is None else float(threshold)
        now = time.time()
        best_pid: Optional[str] = None
        best_sim = -1.0
        with self._lock:
            self._prune_locked(now)
            for pid, lst in self._gallery.items():
                for e, _t in lst:
                    sim = float(np.dot(e, embedding))
                    if sim > best_sim:
                        best_sim = sim
                        best_pid = pid
        if best_pid is not None and best_sim >= thr:
            return best_pid, float(best_sim)
        return None

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "personas_con_cuerpo": len(self._gallery),
                "plantillas_totales": sum(len(v) for v in self._gallery.values()),
            }

    def reset(self) -> None:
        with self._lock:
            self._gallery.clear()
