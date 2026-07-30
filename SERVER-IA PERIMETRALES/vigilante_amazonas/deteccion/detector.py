"""
Detector multiclase YOLO26m con inferencia POR LOTES centralizada.

- Carga el primer candidato existente de config.DETECTOR_CANDIDATOS:
  TensorRT FP16 batch 4 -> TensorRT batch 1 -> .pt (PyTorch FP16 nativo).
- Si un engine falla al cargar o en el warmup (p. ej. TensorRT vs sm_120),
  cae al siguiente candidato y lo deja documentado en el log estructurado.
- Los engines TensorRT son de batch ESTÁTICO: el lote se rellena (padding)
  hasta el batch del engine y luego se recortan los resultados.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.deteccion.mapeo_clases import mapear_clase
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


@dataclass
class DeteccionCruda:
    """Salida del detector ANTES del tracking."""
    bbox: tuple[int, int, int, int]     # x1, y1, x2, y2 en píxeles del frame
    conf: float
    cls_coco: int
    clase: str                          # clase propia ya mapeada


class DetectorMulticlase:
    """YOLO26m (TensorRT FP16 o .pt) con lote centralizado en DEVICE_DETECCION."""

    def __init__(self) -> None:
        self.dispositivo: str = config.resolver_dispositivo(config.DEVICE_DETECCION)
        self.ruta_modelo: Path | None = None
        self.es_engine: bool = False
        self.batch_engine: int = 1
        self._modelo = None
        self._cargar_con_fallback()

    # ------------------------------------------------------------------ carga
    def _cargar_con_fallback(self) -> None:
        from ultralytics import YOLO

        ultimo_error: Exception | None = None
        for candidato in config.DETECTOR_CANDIDATOS:
            if not candidato.exists():
                continue
            try:
                modelo = YOLO(str(candidato), task="detect")
                es_engine = candidato.suffix == ".engine"
                # b4 en el nombre => batch ESTÁTICO 4: todo lote (incluido el
                # warmup) debe ir relleno exactamente a ese tamaño.
                batch_engine = (4 if "_b4" in candidato.stem else 1) if es_engine else 1
                # Warmup real: valida que el engine/kernels funcionen en esta GPU.
                dummy = np.zeros((640, 640, 3), dtype=np.uint8)
                modelo.predict([dummy] * batch_engine, imgsz=config.DETECTOR_IMGSZ,
                               batch=batch_engine,
                               device=self.dispositivo, verbose=False)
                self._modelo = modelo
                self.ruta_modelo = candidato
                self.es_engine = es_engine
                self.batch_engine = batch_engine
                logger.info(
                    f"detector cargado: {candidato.name} "
                    f"({'TensorRT' if es_engine else 'PyTorch'}, batch {self.batch_engine}) "
                    f"en {self.dispositivo}",
                    extra={"datos": {"modelo": candidato.name,
                                     "engine": es_engine,
                                     "dispositivo": self.dispositivo}})
                return
            except Exception as exc:
                ultimo_error = exc
                logger.warning(
                    f"candidato de detector FALLÓ y se pasa al siguiente: "
                    f"{candidato.name} -> {exc}",
                    extra={"datos": {"modelo": candidato.name, "error": str(exc)}})
        raise RuntimeError(
            f"ningún modelo de detección pudo cargarse "
            f"(candidatos: {[c.name for c in config.DETECTOR_CANDIDATOS]}): {ultimo_error}")

    # -------------------------------------------------------------- inferencia
    def detectar_lote(self, frames: list[np.ndarray]) -> list[list[DeteccionCruda]]:
        """Inferencia por lotes: un forward para frames de VARIAS cámaras.

        Devuelve una lista de detecciones por cada frame de entrada, en el
        mismo orden.
        """
        if not frames:
            return []
        salidas: list[list[DeteccionCruda]] = []
        tam_lote: int = max(self.batch_engine, 1) if self.es_engine else config.BATCH_MAX
        for inicio in range(0, len(frames), tam_lote):
            trozo = frames[inicio:inicio + tam_lote]
            n_reales = len(trozo)
            # Padding para engines de batch estático (se recorta después).
            if self.es_engine and self.batch_engine > 1 and n_reales < self.batch_engine:
                trozo = trozo + [trozo[-1]] * (self.batch_engine - n_reales)
            resultados = self._modelo.predict(
                trozo,
                imgsz=config.DETECTOR_IMGSZ,
                conf=config.CONF_DETECCION,
                classes=config.CLASES_COCO_USADAS,
                max_det=config.MAX_DETECCIONES,
                device=self.dispositivo,
                half=config.DETECTOR_HALF and not self.es_engine,
                batch=len(trozo),
                verbose=False,
            )
            for res, frame in zip(resultados[:n_reales], frames[inicio:inicio + tam_lote]):
                salidas.append(self._convertir(res, frame.shape))
        return salidas

    def _convertir(self, resultado, forma_frame: tuple[int, ...]) -> list[DeteccionCruda]:
        """Result de ultralytics -> lista de DeteccionCruda con clase propia."""
        dets: list[DeteccionCruda] = []
        cajas = resultado.boxes
        if cajas is None or len(cajas) == 0:
            return dets
        xyxy = cajas.xyxy.cpu().numpy()
        confs = cajas.conf.cpu().numpy()
        clases = cajas.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), conf, cls_coco in zip(xyxy, confs, clases):
            clase = mapear_clase(int(cls_coco), (float(x1), float(y1), float(x2), float(y2)),
                                 forma_frame)
            if clase is None:
                continue
            dets.append(DeteccionCruda(
                bbox=(int(x1), int(y1), int(x2), int(y2)),
                conf=float(conf),
                cls_coco=int(cls_coco),
                clase=clase,
            ))
        return dets

    # ------------------------------------------------------------------ info
    def vram_usada_gb(self) -> float:
        """VRAM reservada por torch en el dispositivo de detección (GB)."""
        try:
            import torch
            if self.dispositivo.startswith("cuda"):
                return torch.cuda.memory_reserved(config.indice_de(self.dispositivo)) / 1024**3
        except Exception:
            pass
        return 0.0
