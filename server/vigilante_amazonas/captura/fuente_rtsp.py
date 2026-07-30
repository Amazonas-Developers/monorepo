"""
Captura RTSP multihilo con cola descartable por cámara.

Principios:
  - Un hilo lector por cámara; NUNCA acumular lag: la cola guarda como máximo
    COLA_FRAMES_MAX frames y al llenarse se descarta el más viejo.
  - El hilo lee a la velocidad nativa del stream pero solo ENCOLA a
    FPS_PROCESAMIENTO (el resto de frames se tira).
  - Reconexión con backoff exponencial (RTSP_BACKOFF_INICIAL_SEG →
    RTSP_BACKOFF_MAXIMO_SEG) sin tumbar el sistema.
  - También acepta rutas a archivos de video (pruebas): se reproducen en bucle
    respetando su FPS nativo para simular una cámara real.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


@dataclass
class FrameCamara:
    """Un frame listo para inferencia. Contrato usado también por el puente
    del modo Perimetrales (src/analityc/core/puente_vigilante.py)."""
    camara: str
    frame: np.ndarray          # BGR sin anotar
    secuencia: int
    timestamp: float


class FuenteCamara(threading.Thread):
    """Hilo lector de UNA cámara (RTSP) o archivo de video (pruebas)."""

    def __init__(self, nombre: str, url: str) -> None:
        super().__init__(name=f"captura-{nombre}", daemon=True)
        self.nombre: str = nombre
        self.url: str = url
        self.cola: "queue.Queue[FrameCamara]" = queue.Queue(maxsize=config.COLA_FRAMES_MAX)
        self._detener = threading.Event()
        self._secuencia: int = 0
        self._es_archivo: bool = Path(url).exists() if "://" not in url else False
        self.conectada: bool = False
        self.frames_leidos: int = 0
        self.frames_encolados: int = 0
        self.reconexiones: int = 0

    # ------------------------------------------------------------------ ciclo
    def run(self) -> None:
        backoff: float = config.RTSP_BACKOFF_INICIAL_SEG
        while not self._detener.is_set():
            captura = self._abrir()
            if captura is None:
                logger.warning(
                    f"cámara '{self.nombre}' sin conexión; reintento en {backoff:.0f}s",
                    extra={"datos": {"camara": self.nombre, "backoff_seg": backoff}})
                if self._detener.wait(backoff):
                    break
                backoff = min(backoff * 2.0, config.RTSP_BACKOFF_MAXIMO_SEG)
                self.reconexiones += 1
                continue

            backoff = config.RTSP_BACKOFF_INICIAL_SEG
            self.conectada = True
            logger.info(f"cámara '{self.nombre}' conectada",
                        extra={"datos": {"camara": self.nombre, "url_es_archivo": self._es_archivo}})
            self._bucle_lectura(captura)
            captura.release()
            self.conectada = False

    def _abrir(self) -> cv2.VideoCapture | None:
        try:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # latencia mínima en RTSP
            if not cap.isOpened():
                cap.release()
                return None
            return cap
        except Exception as exc:
            logger.error(f"error abriendo cámara '{self.nombre}': {exc}",
                         extra={"datos": {"camara": self.nombre}})
            return None

    def _bucle_lectura(self, cap: cv2.VideoCapture) -> None:
        """Lee frames hasta que la cámara falle o se pida detener."""
        intervalo_enc: float = 1.0 / max(1, config.FPS_PROCESAMIENTO)
        fps_nativo: float = cap.get(cv2.CAP_PROP_FPS) or 25.0
        intervalo_archivo: float = 1.0 / fps_nativo if self._es_archivo else 0.0
        # Planificación por DEADLINE acumulativo: aunque cada iteración de
        # lectura tenga jitter, el promedio converge a FPS_PROCESAMIENTO
        # (un umbral "ahora - ultimo >= intervalo" redondea al múltiplo de la
        # iteración y regala FPS).
        proximo_encolado: float = time.time()
        proxima_lectura: float = time.time()
        ultimo_frame_ok: float = time.time()

        while not self._detener.is_set():
            ok, frame = cap.read()
            ahora = time.time()

            if not ok or frame is None:
                if self._es_archivo:
                    # Archivo terminado: rebobinar (simula cámara infinita).
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                if ahora - ultimo_frame_ok > config.RTSP_TIMEOUT_LECTURA_SEG:
                    logger.warning(f"cámara '{self.nombre}' sin frames; reconectando",
                                   extra={"datos": {"camara": self.nombre}})
                    self.reconexiones += 1
                    return
                time.sleep(0.05)
                continue

            ultimo_frame_ok = ahora
            self.frames_leidos += 1

            # Throttle: solo encolar a FPS_PROCESAMIENTO (deadline acumulativo).
            if ahora >= proximo_encolado:
                # Avanza el deadline; si venimos MUY atrasados, reancla para
                # no ametrallar frames de golpe.
                proximo_encolado += intervalo_enc
                if proximo_encolado < ahora - intervalo_enc:
                    proximo_encolado = ahora + intervalo_enc
                self._secuencia += 1
                fc = FrameCamara(camara=self.nombre, frame=frame,
                                 secuencia=self._secuencia, timestamp=ahora)
                # Cola descartable: si está llena, fuera el más viejo.
                try:
                    self.cola.put_nowait(fc)
                    self.frames_encolados += 1
                except queue.Full:
                    try:
                        self.cola.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.cola.put_nowait(fc)
                        self.frames_encolados += 1
                    except queue.Full:
                        pass

            # Los archivos se reproducen a su FPS nativo (simula tiempo real)
            # descontando el costo de decodificación (deadline, no sleep fijo).
            if self._es_archivo and intervalo_archivo > 0:
                proxima_lectura += intervalo_archivo
                restante = proxima_lectura - time.time()
                if restante > 0:
                    time.sleep(restante)
                else:
                    proxima_lectura = time.time()

    def detener(self) -> None:
        self._detener.set()

    def ultimo_frame(self) -> FrameCamara | None:
        """Drena la cola y devuelve SOLO el frame más reciente (o None)."""
        fc: FrameCamara | None = None
        while True:
            try:
                fc = self.cola.get_nowait()
            except queue.Empty:
                return fc


class CapturaMulticamara:
    """Administra todas las fuentes y entrega lotes de frames recientes."""

    def __init__(self, camaras: dict[str, str] | None = None) -> None:
        self._camaras: dict[str, str] = dict(camaras if camaras is not None
                                             else config.CAMARAS_RTSP)
        self._fuentes: dict[str, FuenteCamara] = {}

    def iniciar(self) -> None:
        if not self._camaras:
            logger.warning("CAMARAS_RTSP está vacío: no hay fuentes que iniciar")
        for nombre, url in self._camaras.items():
            fuente = FuenteCamara(nombre, url)
            self._fuentes[nombre] = fuente
            fuente.start()
        logger.info(f"captura iniciada con {len(self._fuentes)} cámara(s)",
                    extra={"datos": {"camaras": list(self._fuentes)}})

    def detener(self) -> None:
        for fuente in self._fuentes.values():
            fuente.detener()
        for fuente in self._fuentes.values():
            fuente.join(timeout=3.0)
        logger.info("captura detenida")

    def obtener_lote(self) -> list[FrameCamara]:
        """Frame más reciente de cada cámara que tenga algo pendiente."""
        lote: list[FrameCamara] = []
        for fuente in self._fuentes.values():
            fc = fuente.ultimo_frame()
            if fc is not None:
                lote.append(fc)
        return lote

    def estado(self) -> dict[str, dict[str, float | bool | int]]:
        """Métricas por cámara para el dashboard y los logs."""
        return {
            nombre: {
                "conectada": fuente.conectada,
                "frames_leidos": fuente.frames_leidos,
                "frames_encolados": fuente.frames_encolados,
                "reconexiones": fuente.reconexiones,
            }
            for nombre, fuente in self._fuentes.items()
        }
