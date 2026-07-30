"""
Embeddings FACIALES: YuNet (detección + 5 landmarks) -> alineación ArcFace ->
w600k_r50.onnx (ArcFace 512d) en onnxruntime GPU (DEVICE_REID resuelto).

La correspondencia de landmarks es INDEPENDIENTE del orden que entregue
YuNet: los ojos y las comisuras se ordenan por X antes de estimar la
transformación de similitud (evita rostros volteados si el modelo cambia
la convención izquierda/derecha).
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

# Plantilla ArcFace estándar (112x112): ojo-izq, ojo-der, nariz, boca-izq, boca-der
# ("izquierda" = lado izquierdo de la IMAGEN).
_PLANTILLA_112: np.ndarray = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


class EmbebedorRostro:
    """YuNet + ArcFace con sesión ONNX compartida y thread-safe."""

    def __init__(self) -> None:
        import onnxruntime as ort

        self.dispositivo: str = config.resolver_dispositivo(config.DEVICE_REID)
        self._lock = threading.Lock()

        # YuNet vía OpenCV (CPU): solo corre sobre CROPS de persona ya
        # recortados y con throttle, no sobre el frame completo.
        self._yunet = cv2.FaceDetectorYN.create(
            str(config.RUTA_YUNET_ONNX), "", (320, 320),
            score_threshold=config.ROSTRO_CONF_DETECCION,
            nms_threshold=0.3, top_k=50)

        # ArcFace en GPU (onnxruntime), con fallback silencioso a CPU.
        proveedores: list[tuple[str, dict[str, int]] | str]
        if self.dispositivo.startswith("cuda"):
            proveedores = [("CUDAExecutionProvider",
                            {"device_id": config.indice_de(self.dispositivo)}),
                           "CPUExecutionProvider"]
        else:
            proveedores = ["CPUExecutionProvider"]
        self._arcface = ort.InferenceSession(str(config.RUTA_ARCFACE_ONNX),
                                             providers=proveedores)
        self._entrada: str = self._arcface.get_inputs()[0].name
        logger.info(
            f"embebedor de rostro listo (YuNet CPU + ArcFace {self.dispositivo})",
            extra={"datos": {"proveedores": self._arcface.get_providers()}})

    # ------------------------------------------------------------------ API
    def detectar_rostro(self, imagen_bgr: np.ndarray) -> tuple[np.ndarray, float] | None:
        """Devuelve (landmarks 5x2, confianza) del rostro MÁS GRANDE, o None."""
        alto, ancho = imagen_bgr.shape[:2]
        if alto < config.ROSTRO_MIN_PX or ancho < config.ROSTRO_MIN_PX:
            return None
        with self._lock:
            self._yunet.setInputSize((ancho, alto))
            _, caras = self._yunet.detect(imagen_bgr)
        if caras is None or len(caras) == 0:
            return None
        # Rostro más grande (w*h) del crop.
        mejor = max(caras, key=lambda c: float(c[2]) * float(c[3]))
        if min(float(mejor[2]), float(mejor[3])) < config.ROSTRO_MIN_PX:
            return None
        landmarks = mejor[4:14].reshape(5, 2).astype(np.float32)
        return landmarks, float(mejor[14])

    def embeber(self, imagen_bgr: np.ndarray) -> tuple[np.ndarray, float] | None:
        """Crop de persona (o foto) -> (embedding 512d L2-normalizado, conf).

        None si no hay rostro detectable/utilizable.
        """
        resultado = self.detectar_rostro(imagen_bgr)
        if resultado is None:
            return None
        landmarks, conf = resultado
        alineado = self._alinear(imagen_bgr, landmarks)
        emb = self._forward(alineado)
        return emb, conf

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _ordenar_landmarks(landmarks: np.ndarray) -> np.ndarray:
        """[ojoA, ojoB, nariz, bocaA, bocaB] -> orden canónico por X."""
        ojos = sorted(landmarks[0:2], key=lambda p: p[0])
        boca = sorted(landmarks[3:5], key=lambda p: p[0])
        return np.array([ojos[0], ojos[1], landmarks[2], boca[0], boca[1]],
                        dtype=np.float32)

    def _alinear(self, imagen: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        src = self._ordenar_landmarks(landmarks)
        matriz, _ = cv2.estimateAffinePartial2D(src, _PLANTILLA_112,
                                                method=cv2.LMEDS)
        if matriz is None:
            # Degradado: recorte centrado en los landmarks.
            x, y = landmarks.mean(axis=0)
            mitad = 56
            x1, y1 = int(max(0, x - mitad)), int(max(0, y - mitad))
            recorte = imagen[y1:y1 + 112, x1:x1 + 112]
            return cv2.resize(recorte, (112, 112))
        return cv2.warpAffine(imagen, matriz, (112, 112), borderValue=0)

    def _forward(self, cara_112: np.ndarray) -> np.ndarray:
        """ArcFace: BGR->RGB, (x-127.5)/127.5, NCHW -> 512d normalizado."""
        rgb = cv2.cvtColor(cara_112, cv2.COLOR_BGR2RGB).astype(np.float32)
        tensor = ((rgb - 127.5) / 127.5).transpose(2, 0, 1)[None]
        with self._lock:
            salida = self._arcface.run(None, {self._entrada: tensor})[0][0]
        norma = np.linalg.norm(salida)
        return (salida / norma).astype(np.float32) if norma > 0 else salida.astype(np.float32)
