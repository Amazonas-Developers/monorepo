"""
analytics/staff_gallery.py - Personal registrado por FOTO.

Regla de oro: NADIE es tratado como personal (empleado, seguridad,
repositor...) si el operador no subio una foto suya. La conducta puede
sugerir "esta reponiendo", pero la ETIQUETA de personal exige identidad
verificada contra una foto registrada. Sin foto -> es "persona", punto.

Como se registra el personal:

    config/personal/juan_perez.jpg
    config/personal/maria_lopez_1.jpg     (varias fotos de la misma
    config/personal/maria_lopez_2.jpg      persona: sufijo _1, _2...)

  El nombre del archivo ES el nombre del empleado (guiones bajos ->
  espacios). La carpeta se re-lee sola cuando cambia: subir una foto NO
  requiere reiniciar el servidor.

Como se matchea: cada foto se convierte a un embedding facial (ArcFace,
la MISMA red del re-identificador) y, periodicamente, se busca en la
galeria VIVA de personas vistas por las camaras (`search_gallery`). Si una
persona vista matchea la foto con similitud >= STAFF_MATCH_THRESHOLD, su
uuid queda marcado como personal con ese nombre. El umbral es MAS estricto
que el del re-id normal: llamar "empleado" a un cliente es peor que no
reconocer a un empleado.

Consumidores: el orquestador de retail excluye a los uuid de personal de
todas las metricas de CLIENTES (agarres, evaluaciones, compras, trafico de
pasillos, consultas de precio) y usa el match para poner nombre y
"verificado" en las reposiciones.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import numpy as np

from .config import AnalyticsConfig

logger = logging.getLogger(__name__)

_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _name_from_file(path: str) -> str:
    """'maria_lopez_2.jpg' -> 'Maria Lopez' (el sufijo _N agrupa fotos)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[_\-\s]+\d+$", "", stem)     # quitar sufijo _1/_2/...
    return stem.replace("_", " ").replace("-", " ").strip().title() or stem


class StaffGallery:
    """Galeria de personal registrada por fotos + match contra la galeria
    viva del re-identificador."""

    def __init__(self, reidentifier, photos_dir: str = None,
                 threshold: float = None, match_every_s: float = None,
                 yunet_model_path: str = None):
        cfg = AnalyticsConfig
        self._reid = reidentifier
        self._dir = photos_dir or cfg.STAFF_PHOTOS_DIR
        self._thr = float(threshold if threshold is not None
                          else cfg.STAFF_MATCH_THRESHOLD)
        self._every = float(match_every_s if match_every_s is not None
                            else cfg.STAFF_MATCH_EVERY_S)
        self._yunet_path = yunet_model_path
        self._yunet = None
        self._yunet_failed = False
        # nombre -> [embeddings]; uuid -> nombre (matches vigentes)
        self._staff: Dict[str, List[np.ndarray]] = {}
        self._uuid_to_name: Dict[str, str] = {}
        self._dir_sig: Any = None
        self._last_match_ts = 0.0
        self._loaded_files = 0
        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError:
            pass
        self._reload_if_changed(force=True)

    # ── Carga de fotos ───────────────────────────────────────────────

    def _dir_signature(self):
        """(archivos, mtimes) para detectar fotos nuevas sin reiniciar."""
        try:
            files = sorted(
                f for f in os.listdir(self._dir)
                if f.lower().endswith(_EXTS))
            return tuple((f, os.path.getmtime(os.path.join(self._dir, f)))
                         for f in files)
        except OSError:
            return None

    def _reload_if_changed(self, force: bool = False) -> None:
        sig = self._dir_signature()
        if not force and sig == self._dir_sig:
            return
        self._dir_sig = sig
        self._staff.clear()
        self._uuid_to_name.clear()   # los matches se recalculan
        self._loaded_files = 0
        if not sig:
            return
        if self._reid is None or not getattr(self._reid, "is_available",
                                             False):
            logger.warning("Fotos de personal presentes pero el "
                           "re-identificador facial no esta disponible; "
                           "no se puede verificar personal")
            return
        for fname, _mt in sig:
            path = os.path.join(self._dir, fname)
            emb = self._embed_photo(path)
            if emb is None:
                logger.warning("Foto de personal SIN cara utilizable: %s "
                               "(usar foto frontal, bien iluminada)", fname)
                continue
            self._staff.setdefault(_name_from_file(path), []).append(emb)
            self._loaded_files += 1
        if self._staff:
            logger.info("Personal registrado por foto: %s (%d foto(s))",
                        ", ".join(sorted(self._staff)), self._loaded_files)

    def _embed_photo(self, path: str) -> Optional[np.ndarray]:
        """Embedding de la cara de una foto: recorta la cara mas grande con
        YuNet (si esta disponible) o usa la imagen completa como cara."""
        import cv2
        img = cv2.imread(path)
        if img is None or img.size == 0:
            return None
        crop = self._largest_face(img)
        return self._reid.compute_embedding(crop if crop is not None else img)

    def _largest_face(self, img) -> Optional[np.ndarray]:
        import cv2
        det = self._ensure_yunet(img.shape[1], img.shape[0])
        if det is None:
            return None
        try:
            det.setInputSize((img.shape[1], img.shape[0]))
            _rv, faces = det.detect(img)
            if faces is None or len(faces) == 0:
                return None
            f = max(faces, key=lambda r: float(r[2]) * float(r[3]))
            x, y, w, h = [int(v) for v in f[:4]]
            m = int(0.15 * max(w, h))            # margen alrededor
            x0, y0 = max(0, x - m), max(0, y - m)
            x1 = min(img.shape[1], x + w + m)
            y1 = min(img.shape[0], y + h + m)
            crop = img[y0:y1, x0:x1]
            return crop if crop.size > 0 else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("YuNet sobre foto de personal fallo: %s", exc)
            return None

    def _ensure_yunet(self, w: int, h: int):
        if self._yunet is not None or self._yunet_failed:
            return self._yunet
        import cv2
        try:
            if self._yunet_path and os.path.exists(self._yunet_path):
                self._yunet = cv2.FaceDetectorYN_create(
                    self._yunet_path, "", (w, h), 0.6, 0.3, 500)
        except Exception as exc:  # noqa: BLE001
            logger.debug("YuNet para fotos de personal no disponible: %s",
                         exc)
        if self._yunet is None:
            self._yunet_failed = True
        return self._yunet

    # ── Match contra la galeria viva ─────────────────────────────────

    def refresh(self, now: float = None) -> None:
        """Re-lee la carpeta si cambio y re-matchea (throttled)."""
        if self._reid is None:
            return
        now = time.time() if now is None else now
        if now - self._last_match_ts < self._every:
            return
        self._last_match_ts = now
        self._reload_if_changed()
        if not self._staff:
            return
        try:
            for nombre, embs in self._staff.items():
                for emb in embs:
                    r = self._reid.search_gallery(emb, threshold=self._thr)
                    if r is not None:
                        uid, _score = r
                        if self._uuid_to_name.get(uid) != nombre:
                            logger.info("Personal VERIFICADO por foto: %s "
                                        "(uuid=%s)", nombre, uid)
                        self._uuid_to_name[uid] = nombre
        except Exception as exc:  # noqa: BLE001
            logger.debug("Match de personal fallo: %s", exc)

    # ── Consultas ────────────────────────────────────────────────────

    @property
    def has_photos(self) -> bool:
        return bool(self._staff)

    def name_for(self, pid: Any) -> Optional[str]:
        """Nombre del personal para ese persistent_id, o None si no es
        personal registrado (o no hay fotos)."""
        return self._uuid_to_name.get(str(pid))

    def staff_pids(self) -> set:
        """uuids verificados como personal AHORA."""
        return set(self._uuid_to_name.keys())

    def get_stats(self) -> Dict[str, Any]:
        return {
            "fotos_cargadas": int(self._loaded_files),
            "personal_registrado": sorted(self._staff.keys()),
            "verificados_en_escena": len(self._uuid_to_name),
            "carpeta": self._dir,
            "umbral": self._thr,
        }

    def reset_matches(self) -> None:
        self._uuid_to_name.clear()
