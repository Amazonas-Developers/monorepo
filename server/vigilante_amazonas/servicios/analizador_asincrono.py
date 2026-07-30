"""
Analizador ASÍNCRONO: desacopla el bucle de detección del análisis pesado
(clasificador de seguridad CLIP + Re-ID).

El motor de detección publica (FrameCamara, detecciones) en una cola
descartable y sigue a su ritmo (>= FPS_PROCESAMIENTO); este hilo consume la
cola a su propio paso. Si el análisis se atrasa, se descartan los frames más
viejos (mismo principio que la captura: nunca acumular lag). El throttle por
track de seguridad/Re-ID ya limita el costo por frame analizado.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from vigilante_amazonas.captura.fuente_rtsp import FrameCamara
from vigilante_amazonas.deteccion.rastreador import DeteccionVig
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_COLA_MAX: int = 8


class AnalizadorAsincrono(threading.Thread):
    """Hilo que corre seguridad + Re-ID fuera del bucle de detección."""

    def __init__(self, servicios: Any, motor: Any = None) -> None:
        super().__init__(name="analizador-vigilante", daemon=True)
        self._servicios = servicios
        self._motor = motor      # opcional: para actualizar etiquetas del visor
        self._cola: "queue.Queue[tuple[FrameCamara, list[DeteccionVig]] | None]" = \
            queue.Queue(maxsize=_COLA_MAX)
        self.frames_analizados: int = 0
        self.frames_descartados: int = 0

    # ------------------------------------------------------------- productor
    def consumidor(self, fc: FrameCamara, dets: list[DeteccionVig]) -> None:
        """Consumidor registrado en el motor: NUNCA bloquea (drop-oldest)."""
        try:
            self._cola.put_nowait((fc, dets))
        except queue.Full:
            try:
                self._cola.get_nowait()
                self.frames_descartados += 1
            except queue.Empty:
                pass
            try:
                self._cola.put_nowait((fc, dets))
            except queue.Full:
                self.frames_descartados += 1

    def detener(self) -> None:
        try:
            self._cola.put_nowait(None)
        except queue.Full:
            pass

    # ------------------------------------------------------------ consumidor
    def run(self) -> None:
        logger.info("analizador asíncrono iniciado (seguridad + Re-ID)")
        while True:
            elemento = self._cola.get()
            if elemento is None:
                break
            fc, dets = elemento
            try:
                seguridad = self._servicios.seguridad
                if seguridad is not None and seguridad.disponible:
                    dets = seguridad.transformador(fc, dets)
                    if self._motor is not None:
                        self._motor.ultimas_dets[fc.camara] = dets
                if self._servicios.motor_reid is not None:
                    self._servicios.motor_reid.suscriptor(fc, dets)
                self.frames_analizados += 1
            except Exception:
                logger.exception("análisis falló para un frame (se continúa)")
        logger.info(
            f"analizador detenido ({self.frames_analizados} analizados, "
            f"{self.frames_descartados} descartados)")
