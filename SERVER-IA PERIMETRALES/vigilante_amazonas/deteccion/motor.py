"""
Motor central de VIGILANTE-AMAZONAS: captura -> lote YOLO -> ByteTrack ->
consumidores (clasificador de seguridad, Re-ID, alertas).

El bucle corre en su propio hilo. Los consumidores se registran con
`registrar_consumidor(fn)` y reciben (FrameCamara, list[DeteccionVig]) por
cada frame procesado; deben ser RÁPIDOS o delegar a sus propias colas
(el VLM, por ejemplo, nunca corre aquí).

Visor de depuración:
  - config.VISOR_DEBUG:  ventana OpenCV local por cámara.
  - config.VISOR_MJPEG:  guarda el último frame anotado (JPEG) por cámara,
    que el dashboard sirve como stream MJPEG en /debug/<camara>.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.captura.fuente_rtsp import CapturaMulticamara, FrameCamara
from vigilante_amazonas.deteccion.clasificador_vehiculos import RefinadorVehiculos
from vigilante_amazonas.deteccion.detector import DetectorMulticlase
from vigilante_amazonas.deteccion.mapeo_clases import color_de
from vigilante_amazonas.deteccion.rastreador import DeteccionVig, GestorRastreadores
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

Consumidor = Callable[[FrameCamara, list[DeteccionVig]], None]


def anotar_frame(frame: np.ndarray, dets: list[DeteccionVig],
                 etiquetas_extra: dict[int, str] | None = None) -> np.ndarray:
    """Dibuja cajas y etiquetas EN ESPAÑOL sobre una copia del frame."""
    salida = frame.copy()
    for d in dets:
        x1, y1, x2, y2 = d.bbox
        color = color_de(d.clase)
        cv2.rectangle(salida, (x1, y1), (x2, y2), color, 2)
        etiqueta = f"{d.clase} #{d.track_id} {d.conf:.2f}"
        if etiquetas_extra and d.track_id in etiquetas_extra:
            etiqueta += f" | {etiquetas_extra[d.track_id]}"
        (tw, th), _ = cv2.getTextSize(etiqueta, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(salida, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(salida, etiqueta, (x1 + 3, max(12, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return salida


class MotorVigilante(threading.Thread):
    """Hilo del bucle principal de inferencia por lotes."""

    def __init__(self, captura: CapturaMulticamara) -> None:
        super().__init__(name="motor-vigilante", daemon=True)
        self.captura = captura
        self.detector = DetectorMulticlase()
        self.rastreadores = GestorRastreadores()
        self.vehiculos = RefinadorVehiculos()   # carro vs camioneta vs camión
        self._consumidores: list[Consumidor] = []
        self._detener = threading.Event()
        # Métricas y visor.
        self.fps_por_camara: dict[str, float] = {}
        self._conteo_frames: dict[str, int] = {}
        self._t0_fps: float = time.time()
        self.ultimo_jpeg: dict[str, bytes] = {}       # visor MJPEG del dashboard
        self.ultimas_dets: dict[str, list[DeteccionVig]] = {}
        self.etiquetas_extra: dict[str, dict[int, str]] = {}  # {camara: {track_id: texto}}
        # El JPEG del visor solo se codifica si alguien lo pidió hace poco
        # (ahorra ~7 ms/frame cuando nadie mira el stream de depuración).
        self._visor_pedido: dict[str, float] = {}

    def solicitar_visor(self, camara: str) -> None:
        """El dashboard llama esto mientras un cliente ve /debug/<camara>."""
        self._visor_pedido[camara] = time.time()

    # ------------------------------------------------------------------ API
    def registrar_consumidor(self, fn: Consumidor) -> None:
        """Registra un consumidor de (frame, detecciones rastreadas)."""
        self._consumidores.append(fn)

    def detener(self) -> None:
        self._detener.set()

    # ------------------------------------------------------------------ bucle
    def run(self) -> None:
        logger.info("motor de detección iniciado",
                    extra={"datos": {"modelo": str(self.detector.ruta_modelo),
                                     "dispositivo": self.detector.dispositivo}})
        while not self._detener.is_set():
            lote = self.captura.obtener_lote()
            if not lote:
                time.sleep(0.005)
                continue
            try:
                self._procesar_lote(lote)
            except Exception:
                logger.exception("error procesando lote (el motor continúa)")
        if config.VISOR_DEBUG:
            cv2.destroyAllWindows()
        logger.info("motor de detección detenido")

    def _procesar_lote(self, lote: list[FrameCamara]) -> None:
        listas = self.detector.detectar_lote([fc.frame for fc in lote])
        for fc, crudas in zip(lote, listas):
            dets = self.rastreadores.actualizar(fc.camara, crudas)
            dets = self.vehiculos.refinar(fc.camara, fc.secuencia, fc.frame, dets)
            self.ultimas_dets[fc.camara] = dets
            for consumidor in self._consumidores:
                try:
                    consumidor(fc, dets)
                except Exception:
                    logger.exception(
                        f"consumidor falló con frame de '{fc.camara}' (se ignora)")
            self._actualizar_visor(fc, dets)
            self._contar_fps(fc.camara)

    # ------------------------------------------------------------------ visor
    def _actualizar_visor(self, fc: FrameCamara, dets: list[DeteccionVig]) -> None:
        mjpeg_activo = (config.VISOR_MJPEG and
                        time.time() - self._visor_pedido.get(fc.camara, 0.0) < 5.0)
        if not (config.VISOR_DEBUG or mjpeg_activo):
            return
        extra = self.etiquetas_extra.get(fc.camara)
        anotado = anotar_frame(fc.frame, dets, extra)
        if mjpeg_activo:
            ok, jpg = cv2.imencode(".jpg", anotado,
                                   [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                self.ultimo_jpeg[fc.camara] = jpg.tobytes()
        if config.VISOR_DEBUG:
            cv2.imshow(f"VIGILANTE - {fc.camara}", anotado)
            cv2.waitKey(1)

    def _contar_fps(self, camara: str) -> None:
        self._conteo_frames[camara] = self._conteo_frames.get(camara, 0) + 1
        ahora = time.time()
        transcurrido = ahora - self._t0_fps
        if transcurrido >= 5.0:
            for cam, n in self._conteo_frames.items():
                self.fps_por_camara[cam] = n / transcurrido
            logger.info(
                "FPS por cámara: " + ", ".join(
                    f"{c}={v:.1f}" for c, v in sorted(self.fps_por_camara.items())),
                extra={"datos": {"fps": {c: round(v, 1) for c, v in self.fps_por_camara.items()},
                                 "vram_gb": round(self.detector.vram_usada_gb(), 2)}})
            self._conteo_frames = {}
            self._t0_fps = ahora
