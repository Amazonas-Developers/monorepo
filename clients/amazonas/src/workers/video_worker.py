"""
src/workers/video_worker.py
QThread que recorre un ARCHIVO de video y entrega sus frames a la celda.

Por que no vale RTSPWorker
--------------------------
En un flujo en vivo perder frames es inevitable y aceptable: el servidor
va a su ritmo y lo que no llega, no llega. Con un archivo es al reves —
el material esta completo en disco, asi que descartar frames significa
perder personas que no van a volver a pasar.

Por eso este worker avanza EN HANDSHAKE: entrega un frame y espera a que
el servidor conteste antes de leer el siguiente. El video avanza
exactamente al ritmo que la inferencia puede sostener y no se analiza de
menos. Si nadie contesta (socket caido, IA apagada) se sigue tras
`ESPERA_MAXIMA_S` para no quedarse colgado nunca.
"""
from __future__ import annotations

import os
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

# Extensiones que aceptamos al soltar un archivo sobre una celda. OpenCV
# tira de FFMPEG, asi que en la practica abre bastante mas; esta lista es
# la que se ofrece y la que filtra el drag & drop.
EXTENSIONES_VIDEO = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".mpg",
    ".mpeg", ".m4v", ".ts", ".mts", ".m2ts", ".3gp", ".ogv", ".asf",
    ".dav", ".h264", ".h265", ".hevc",
}

# Si el servidor no contesta en este tiempo, se sigue igualmente. Evita
# que un socket caido deje el analisis parado para siempre.
ESPERA_MAXIMA_S = 15.0


def es_archivo_de_video(ruta: str) -> bool:
    """True si la ruta apunta a un archivo con extension de video."""
    return (bool(ruta) and os.path.isfile(ruta)
            and os.path.splitext(ruta)[1].lower() in EXTENSIONES_VIDEO)


class VideoFileWorker(QThread):
    """Recorre un archivo de video entregando sus frames de uno en uno."""

    frame_ready = Signal(QImage)
    # (frame actual, frames totales, segundos restantes estimados).
    # totales = 0 cuando el contenedor no declara la duracion.
    progreso = Signal(int, int, float)
    # Frames entregados al terminar (0 si no se pudo leer nada).
    finalizado = Signal(int)
    error = Signal(str)
    iniciado = Signal(str)          # descripcion legible del archivo

    def __init__(self, ruta: str, esperar_al_servidor: bool = True,
                 salto: int = 1):
        super().__init__()
        self.ruta = ruta
        # Handshake con el servidor. Se apaga cuando la IA no esta activa:
        # sin nadie que conteste, esperar no aporta nada.
        self.esperar_al_servidor = esperar_al_servidor
        # Analizar 1 de cada N frames. 1 = todos.
        self.salto = max(1, int(salto))

        self._running = False
        self._paused = False
        self._permiso = threading.Event()
        self._entregados = 0

    # ── Control ──────────────────────────────────────────────────────

    def stop(self) -> None:
        self._running = False
        self._permiso.set()          # desbloquea si estaba esperando
        self.wait(3000)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def permitir_siguiente(self) -> None:
        """El servidor contesto: se puede leer el siguiente frame."""
        self._permiso.set()

    def fijar_espera(self, esperar: bool) -> None:
        """Activa o desactiva el handshake (al encender/apagar la IA)."""
        self.esperar_al_servidor = esperar
        if not esperar:
            self._permiso.set()

    # ── Bucle ────────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True
        cap = self._abrir()
        if cap is None:
            self.finalizado.emit(0)
            return

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps_video = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.iniciado.emit(self._descripcion(total, fps_video))

        indice = 0
        comienzo = time.monotonic()
        try:
            while self._running:
                if self._paused:
                    time.sleep(0.05)
                    continue

                ok, frame = cap.read()
                if not ok:
                    break                       # fin del archivo
                indice += 1

                if self.salto > 1 and (indice - 1) % self.salto:
                    continue                    # frame saltado a proposito

                imagen = self._a_qimage(frame)
                if imagen is None:
                    continue

                self._permiso.clear()
                self.frame_ready.emit(imagen)
                self._entregados += 1
                self.progreso.emit(
                    indice, total,
                    self._restante(indice, total, comienzo))

                if self.esperar_al_servidor and self._running:
                    # Si expira, se sigue igualmente: mas vale analizar de
                    # menos que dejar el proceso muerto sin avisar.
                    self._permiso.wait(ESPERA_MAXIMA_S)
        finally:
            cap.release()

        self.finalizado.emit(self._entregados)

    # ── Auxiliares ───────────────────────────────────────────────────

    def _abrir(self):
        if not os.path.isfile(self.ruta):
            self.error.emit(f"No existe el archivo:\n{self.ruta}")
            return None
        try:
            cap = cv2.VideoCapture(self.ruta, cv2.CAP_FFMPEG)
            if cap.isOpened():
                return cap
            cap.release()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Error de OpenCV al abrir el video: {exc}")
            return None
        self.error.emit(
            f"No se pudo abrir el video:\n{os.path.basename(self.ruta)}\n"
            "El formato o el codec pueden no estar soportados.")
        return None

    def _descripcion(self, total: int, fps_video: float) -> str:
        nombre = os.path.basename(self.ruta)
        if total > 0 and fps_video > 0:
            segundos = total / fps_video
            return (f"🎬 {nombre} · {total} frames · "
                    f"{self._reloj(segundos)}")
        return f"🎬 {nombre}"

    def _restante(self, indice: int, total: int, comienzo: float) -> float:
        """Segundos que faltan, estimados con el ritmo real observado."""
        if total <= 0 or indice <= 0:
            return 0.0
        transcurrido = time.monotonic() - comienzo
        if transcurrido <= 0:
            return 0.0
        return max(0.0, (total - indice) * (transcurrido / indice))

    @staticmethod
    def _reloj(segundos: float) -> str:
        segundos = int(max(0, segundos))
        if segundos >= 3600:
            return f"{segundos // 3600}h {(segundos % 3600) // 60}min"
        if segundos >= 60:
            return f"{segundos // 60}min {segundos % 60}s"
        return f"{segundos}s"

    @staticmethod
    def _a_qimage(frame: np.ndarray) -> QImage | None:
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            alto, ancho, canales = rgb.shape
            return QImage(rgb.data.tobytes(), ancho, alto,
                          ancho * canales, QImage.Format_RGB888).copy()
        except Exception:  # noqa: BLE001
            return None
