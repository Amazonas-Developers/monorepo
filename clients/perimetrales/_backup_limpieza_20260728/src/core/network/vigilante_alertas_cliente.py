"""
Cliente de EJEMPLO para consumir las alertas de VIGILANTE-AMAZONAS desde
perimetrales-view (generado por el HITO 7 de VIGILANTE-AMAZONAS).

VIGILANTE-AMAZONAS emite por Socket.IO (puerto 8091) el evento
'alerta_persona' con: persona, nivel, camara, timestamp, score, tipo_match,
snapshot_base64, snapshot_url, track_id.

Este módulo lo adapta al contrato del AlertsSidebar de perimetrales-view
(las tarjetas del panel derecho): conecta, escucha y entrega dicts listos
para `alerts_sidebar.add_alert(...)` mediante un callback thread-safe.

Uso mínimo (en main.py de perimetrales-view, tras crear alerts_sidebar):

    from core.network.vigilante_alertas_cliente import ClienteVigilante
    cliente_vigilante = ClienteVigilante(
        url="http://127.0.0.1:8091",
        al_recibir=alerts_sidebar.new_alert.emit,   # señal Qt thread-safe
    )
    cliente_vigilante.conectar()

Requiere: pip install "python-socketio[client]==5.11.4" en el venv del cliente.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class ClienteVigilante:
    """Cliente Socket.IO con reconexión automática (hilo propio)."""

    def __init__(self,
                 url: str = "http://127.0.0.1:8091",
                 al_recibir: Optional[Callable[[dict], None]] = None,
                 evento: str = "alerta_persona") -> None:
        self.url = url
        self.evento = evento
        self.al_recibir = al_recibir
        self._sio = None
        self._hilo: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ API
    def conectar(self) -> None:
        """Conecta en un hilo propio (no bloquea la UI de Qt)."""
        self._hilo = threading.Thread(target=self._correr,
                                      name="vigilante-alertas", daemon=True)
        self._hilo.start()

    def desconectar(self) -> None:
        if self._sio is not None:
            try:
                self._sio.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------ interno
    def _correr(self) -> None:
        import socketio
        self._sio = socketio.Client(reconnection=True,
                                    reconnection_delay=2,
                                    reconnection_delay_max=15)

        @self._sio.on(self.evento)
        def _al_llegar(datos: dict) -> None:
            tarjeta = self._adaptar(datos)
            if self.al_recibir is not None:
                try:
                    self.al_recibir(tarjeta)
                except Exception as e:
                    print(f"[VIGILANTE] error entregando alerta: {e}")

        while True:
            try:
                self._sio.connect(self.url, wait_timeout=10)
                print(f"[VIGILANTE] conectado a {self.url}")
                self._sio.wait()          # bloquea este hilo hasta desconexión
            except Exception as e:
                print(f"[VIGILANTE] sin conexión ({e}); reintento en 5s")
                time.sleep(5)

    @staticmethod
    def _adaptar(d: dict) -> dict:
        """Payload de VIGILANTE-AMAZONAS -> tarjeta del AlertsSidebar."""
        nivel = d.get("nivel", "medio")
        return {
            "event_type": "Alerta",
            "class_name": d.get("persona", "Persona de interés"),
            "clase_gruesa": "persona",         # va a la columna PERSONAS
            "global_id": f"VIG-{d.get('id', '')}",
            "description": (f"PERSONA DE INTERÉS ({nivel.upper()}) — "
                            f"match {d.get('tipo_match', '?')} "
                            f"score {d.get('score', 0):.2f}"),
            "timestamp": d.get("timestamp", time.time()),
            "image_base64": d.get("snapshot_base64", ""),
            "crop_image": d.get("snapshot_base64", ""),
            "camera_name": d.get("camara", ""),
            "camera_id": d.get("camara", ""),
            "screenshot_path": "",
        }
