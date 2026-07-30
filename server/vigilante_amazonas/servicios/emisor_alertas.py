"""
Emisor de alertas (Hito 7): Socket.IO + persistencia SQLite + snapshots.

Consume EventoReID del MotorReID y hace tres cosas:
  1. Guarda el snapshot (crop JPEG reescalado) en RUTA_SNAPSHOTS.
  2. Persiste la alerta en SQLite (visible en el dashboard :8090).
  3. Emite el evento Socket.IO `alerta_persona` en el puerto PUERTO_SOCKETIO
     con el contrato acordado para perimetrales-view:
     {persona, persona_id, nivel, camara, timestamp, score, tipo_match,
      snapshot_base64, snapshot_url, track_id, verificacion}

El servidor Socket.IO corre en su PROPIO hilo con su propio event loop; la
emisión desde los hilos del pipeline usa run_coroutine_threadsafe (nunca
bloquea la detección).

NOTA de integración: el cliente perimetrales-view actual recibe las alertas
por el websocket del servidor ELDE (metadata.alerts del modo
"VigilanteAmazonas" — ver adaptador_websocket.py). Este canal Socket.IO es
ADICIONAL: sirve para consumidores externos (ver ejemplo_cliente/).
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import socketio
import uvicorn

from vigilante_amazonas import config
from vigilante_amazonas.servicios.motor_reid import EventoReID
from vigilante_amazonas.utilidades.registro import configurar_registro
from vigilante_amazonas.web.base_datos import get_bd

logger = configurar_registro(__name__)


class EmisorAlertas:
    """Servidor Socket.IO de alertas + persistencia, thread-safe."""

    def __init__(self, puerto: int | None = None,
                 iniciar_servidor: bool = True) -> None:
        self.puerto: int = puerto or config.PUERTO_SOCKETIO
        self.clientes_conectados: int = 0
        self.alertas_emitidas: int = 0
        self._sio = socketio.AsyncServer(async_mode="asgi",
                                         cors_allowed_origins="*")
        self._app = socketio.ASGIApp(self._sio)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listo = threading.Event()
        self._registrar_manejadores()
        if iniciar_servidor:
            hilo = threading.Thread(target=self._servir, name="socketio-alertas",
                                    daemon=True)
            hilo.start()
            self._listo.wait(timeout=10.0)

    # ------------------------------------------------------------- servidor
    def _registrar_manejadores(self) -> None:
        @self._sio.event
        async def connect(sid: str, environ: dict) -> None:  # noqa: ANN001
            self.clientes_conectados += 1
            logger.info(f"cliente Socket.IO conectado ({sid}); "
                        f"total {self.clientes_conectados}")

        @self._sio.event
        async def disconnect(sid: str) -> None:
            self.clientes_conectados = max(0, self.clientes_conectados - 1)
            logger.info(f"cliente Socket.IO desconectado ({sid})")

    def _servir(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        cfg = uvicorn.Config(self._app, host=config.HOST_ESCUCHA,
                             port=self.puerto, log_level="warning",
                             loop="asyncio")
        servidor = uvicorn.Server(cfg)
        servidor.install_signal_handlers = lambda: None  # hilo secundario
        self._loop.call_soon(self._listo.set)
        logger.info(f"servidor Socket.IO de alertas en puerto {self.puerto}")
        try:
            self._loop.run_until_complete(servidor.serve())
        except Exception:
            logger.exception("servidor Socket.IO caído")

    # ------------------------------------------------------------ consumidor
    def procesar_evento(self, evento: EventoReID) -> None:
        """Consumidor registrado en el MotorReID (corre en hilos del pipeline)."""
        try:
            ruta_snapshot = self._guardar_snapshot(evento)
            bd = get_bd()
            bd.registrar_camara(evento.camara)
            bd.registrar_alerta(
                persona_id=evento.persona_id, persona=evento.persona,
                nivel=evento.nivel, camara=evento.camara, score=evento.score,
                tipo_match=evento.tipo_match,
                snapshot=str(ruta_snapshot) if ruta_snapshot else "",
                track_id=evento.track_id, verificacion=evento.verificacion)
            self._emitir(evento, ruta_snapshot)
        except Exception:
            logger.exception("fallo procesando alerta (el pipeline continúa)")

    # ------------------------------------------------------------- internos
    def _guardar_snapshot(self, evento: EventoReID) -> Path | None:
        if evento.crop is None or evento.crop.size == 0:
            return None
        imagen = self._reescalar(evento.crop)
        nombre = (f"{time.strftime('%Y%m%d_%H%M%S')}_"
                  f"{evento.persona.replace(' ', '_')}_{evento.camara}.jpg")
        ruta = config.RUTA_SNAPSHOTS / nombre
        cv2.imwrite(str(ruta), imagen,
                    [cv2.IMWRITE_JPEG_QUALITY, config.SNAPSHOT_JPEG_CALIDAD])
        return ruta

    @staticmethod
    def _reescalar(imagen: np.ndarray) -> np.ndarray:
        lado = max(imagen.shape[:2])
        if lado <= config.SNAPSHOT_MAX_LADO_PX:
            return imagen
        factor = config.SNAPSHOT_MAX_LADO_PX / lado
        return cv2.resize(imagen, None, fx=factor, fy=factor)

    def _emitir(self, evento: EventoReID, ruta_snapshot: Path | None) -> None:
        if self._loop is None:
            return
        b64 = ""
        if evento.crop is not None and evento.crop.size > 0:
            ok, jpg = cv2.imencode(".jpg", self._reescalar(evento.crop),
                                   [cv2.IMWRITE_JPEG_QUALITY,
                                    config.SNAPSHOT_JPEG_CALIDAD])
            if ok:
                b64 = base64.b64encode(jpg.tobytes()).decode()
        payload: dict[str, Any] = {
            "persona": evento.persona,
            "persona_id": evento.persona_id,
            # alias legado: el consumidor vigilante_alertas_cliente.py del
            # cliente perimetrales-view arma su global_id con d.get("id").
            "id": evento.persona_id,
            "nivel": evento.nivel,
            "camara": evento.camara,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(evento.timestamp)),
            "score": evento.score,
            "tipo_match": evento.tipo_match,
            "snapshot_base64": b64,
            "snapshot_url": (f"/snapshots/{ruta_snapshot.name}"
                             if ruta_snapshot else ""),
            "track_id": evento.track_id,
            "verificacion": evento.verificacion,
        }
        futuro = asyncio.run_coroutine_threadsafe(
            self._sio.emit(config.EVENTO_ALERTA, payload), self._loop)
        try:
            futuro.result(timeout=5.0)
            self.alertas_emitidas += 1
        except Exception:
            logger.warning("no se pudo emitir la alerta por Socket.IO")
