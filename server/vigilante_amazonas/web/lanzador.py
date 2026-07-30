"""
Arranque compartido del panel de VIGILANTE (:5333).

Lo usan tanto el servidor ELDE (iniciar_servidor_headless.py, para que el
dashboard esté disponible desde el arranque) como el modo websocket
VigilanteWS y el main.py autónomo. Es IDEMPOTENTE: si ya se lanzó en este
proceso, o si el puerto ya está ocupado por otra instancia, no hace nada y lo
deja anotado en el log — así nunca hay dos servidores peleando por el puerto.

IMPORTANTE: crear la app NO carga los modelos de IA. El dashboard resuelve
los servicios de forma perezosa (solo al subir una foto o consultar estado),
de modo que levantarlo al arrancar no cuesta VRAM ni tiempo.
"""

from __future__ import annotations

import socket
import threading
from typing import Any

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_lanzado_en_este_proceso: bool = False
_lock = threading.Lock()


def puerto_ocupado(puerto: int, host: str = "127.0.0.1") -> bool:
    """True si algo ya está escuchando en ese puerto."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, puerto)) == 0


def iniciar_dashboard(motor: Any = None) -> bool:
    """Levanta el dashboard en un hilo demonio. Devuelve True si lo lanzó
    ahora, False si ya estaba (en este proceso o en otro)."""
    global _lanzado_en_este_proceso
    with _lock:
        if _lanzado_en_este_proceso:
            return False
        if puerto_ocupado(config.PUERTO_API):
            logger.info(
                f"dashboard ya activo en el puerto {config.PUERTO_API} "
                f"(otro proceso); no se lanza otro")
            _lanzado_en_este_proceso = True     # no reintentar en bucle
            return False
        _lanzado_en_este_proceso = True

    def _servir() -> None:
        try:
            import uvicorn
            # El panel monta dentro la gestion de personas (/gestion), que
            # es lo que antes se servia suelto en :8090.
            from vigilante_amazonas.web.panel import crear_panel
            uvicorn.run(crear_panel(motor), host=config.HOST_ESCUCHA,
                        port=config.PUERTO_API, log_level="warning")
        except Exception:
            logger.exception("el dashboard no pudo iniciar")

    threading.Thread(target=_servir, name="dashboard-vigilante",
                     daemon=True).start()
    logger.info(f"panel de VIGILANTE en http://localhost:{config.PUERTO_API}")
    return True
