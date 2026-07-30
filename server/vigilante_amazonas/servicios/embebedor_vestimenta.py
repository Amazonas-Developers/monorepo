"""
Embeddings de VESTIMENTA (cuerpo completo) con dos backends conmutables por
config.VESTIMENTA_BACKEND:

  - "osnet": OSNet x1_0 (torchreid, export ONNX) en onnxruntime GPU.
             Entrenado ESPECÍFICAMENTE para re-identificación de personas
             (invariante a pose/cámara). 512 dims.
  - "clip":  CLIP ViT-B/32 compartido (embeddings genéricos de imagen).
             512 dims. Más semántico (color/tipo de ropa), menos fino con
             identidad.

La comparativa del Hito 4 (pruebas/test_reid.py) mide separación
intra-persona vs inter-persona de ambos y recomienda cuál dejar activo.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_MEDIA_IMAGENET: np.ndarray = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD_IMAGENET: np.ndarray = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class EmbebedorVestimenta:
    """Extractor de embedding corporal con backend conmutable."""

    def __init__(self, backend: str | None = None) -> None:
        self.backend: str = (backend or config.VESTIMENTA_BACKEND).lower()
        self.dispositivo: str = config.resolver_dispositivo(config.DEVICE_REID)
        self._lock = threading.Lock()
        self._osnet = None
        self._entrada_osnet: str = ""
        self._clip = None
        if self.backend == "osnet":
            self._cargar_osnet()
        elif self.backend == "clip":
            from vigilante_amazonas.servicios.clip_compartido import get_clip
            self._clip = get_clip()
        else:
            raise ValueError(f"backend de vestimenta desconocido: {self.backend}")
        logger.info(f"embebedor de vestimenta listo (backend={self.backend})")

    def _cargar_osnet(self) -> None:
        import onnxruntime as ort
        proveedores: list[tuple[str, dict[str, int]] | str]
        if self.dispositivo.startswith("cuda"):
            proveedores = [("CUDAExecutionProvider",
                            {"device_id": config.indice_de(self.dispositivo)}),
                           "CPUExecutionProvider"]
        else:
            proveedores = ["CPUExecutionProvider"]
        self._osnet = ort.InferenceSession(str(config.RUTA_OSNET_ONNX),
                                           providers=proveedores)
        self._entrada_osnet = self._osnet.get_inputs()[0].name

    # ------------------------------------------------------------------ API
    def embeber(self, crops_bgr: list[np.ndarray]) -> np.ndarray:
        """Crops BGR de cuerpo completo -> (N, 512) float32 L2-normalizado."""
        crops_validos = [c for c in crops_bgr if c.size > 0]
        if not crops_validos:
            return np.zeros((0, 512), dtype=np.float32)
        if self.backend == "clip":
            return self._clip.codificar_imagenes(crops_validos)
        return self._embeber_osnet(crops_validos)

    def _embeber_osnet(self, crops: list[np.ndarray]) -> np.ndarray:
        lote = np.stack([self._preprocesar_osnet(c) for c in crops])
        with self._lock:
            salida = self._osnet.run(None, {self._entrada_osnet: lote})[0]
        normas = np.linalg.norm(salida, axis=1, keepdims=True)
        normas[normas == 0] = 1.0
        return (salida / normas).astype(np.float32)

    @staticmethod
    def _preprocesar_osnet(crop_bgr: np.ndarray) -> np.ndarray:
        """BGR -> RGB 256x128, normalización ImageNet, CHW float32."""
        rgb = cv2.cvtColor(cv2.resize(crop_bgr, (128, 256)), cv2.COLOR_BGR2RGB)
        arr = rgb.astype(np.float32) / 255.0
        arr = (arr - _MEDIA_IMAGENET) / _STD_IMAGENET
        return arr.transpose(2, 0, 1)
