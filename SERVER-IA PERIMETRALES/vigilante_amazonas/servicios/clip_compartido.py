"""
CLIP compartido (laion/CLIP-ViT-B-32) para:
  - clasificador zero-shot de personal de seguridad (Hito 3)
  - embeddings de vestimenta, backend "clip" (Hito 4)

Una sola instancia por proceso, cargada perezosamente en DEVICE_REID
(resuelto). Todas las salidas están L2-normalizadas: la similitud coseno es
un producto punto.
"""

from __future__ import annotations

import threading

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_lock = threading.Lock()
_instancia: "ClipCompartido | None" = None


class ClipCompartido:
    """Envoltura de CLIP (transformers) con codificación por lotes."""

    def __init__(self) -> None:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self.dispositivo: str = config.resolver_dispositivo(config.DEVICE_REID)
        try:
            modelo = CLIPModel.from_pretrained(config.SEGURIDAD_MODELO_CLIP,
                                               local_files_only=True)
            procesador = CLIPProcessor.from_pretrained(config.SEGURIDAD_MODELO_CLIP,
                                                       local_files_only=True)
        except Exception:
            logger.warning("CLIP no está en caché local; intentando descarga…")
            modelo = CLIPModel.from_pretrained(config.SEGURIDAD_MODELO_CLIP)
            procesador = CLIPProcessor.from_pretrained(config.SEGURIDAD_MODELO_CLIP)
        self._torch = torch
        self._modelo = modelo.to(self.dispositivo).eval()
        if self.dispositivo.startswith("cuda"):
            self._modelo = self._modelo.half()
        self._procesador = procesador
        logger.info(f"CLIP cargado ({config.SEGURIDAD_MODELO_CLIP}) en {self.dispositivo}",
                    extra={"datos": {"dispositivo": self.dispositivo}})

    def codificar_imagenes(self, imagenes_bgr: list[np.ndarray]) -> np.ndarray:
        """Crops BGR -> matriz (N, 512) float32 L2-normalizada."""
        if not imagenes_bgr:
            return np.zeros((0, 512), dtype=np.float32)
        rgb = [img[:, :, ::-1] for img in imagenes_bgr]
        entradas = self._procesador(images=rgb, return_tensors="pt")
        pix = entradas["pixel_values"].to(self.dispositivo)
        if self.dispositivo.startswith("cuda"):
            pix = pix.half()
        with self._torch.no_grad():
            feats = self._modelo.get_image_features(pixel_values=pix)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.float().cpu().numpy()

    def codificar_textos(self, textos: list[str]) -> np.ndarray:
        """Prompts -> matriz (N, 512) float32 L2-normalizada."""
        entradas = self._procesador(text=textos, return_tensors="pt",
                                    padding=True, truncation=True)
        entradas = {k: v.to(self.dispositivo) for k, v in entradas.items()}
        with self._torch.no_grad():
            feats = self._modelo.get_text_features(**entradas)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.float().cpu().numpy()


def get_clip() -> ClipCompartido:
    """Singleton por proceso (carga perezosa y thread-safe)."""
    global _instancia
    with _lock:
        if _instancia is None:
            _instancia = ClipCompartido()
        return _instancia
