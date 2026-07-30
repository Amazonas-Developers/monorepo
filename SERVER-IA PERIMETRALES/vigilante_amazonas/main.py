"""
Orquestador de VIGILANTE-AMAZONAS (Hito 8).

Levanta, con arranque y apagado limpios:
  1. Núcleo de servicios (clasificador seguridad, Re-ID, VLM, alertas :8091).
  2. Captura RTSP multihilo (config.CAMARAS_RTSP) + motor de detección por
     lotes con ByteTrack (modo autónomo). Si no hay cámaras configuradas, el
     sistema queda en modo SOLO-SERVICIOS (dashboard + alertas + el modo
     websocket "VigilanteAmazonas" del servidor ELDE siguen operativos).
  3. Dashboard web :8090 (galería, historial, visor de depuración).

Uso:
    venv\\Scripts\\python.exe vigilante_amazonas\\main.py
    (o bajo PM2: pm2 start vigilante_amazonas\\ecosystem.config.js)
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vigilante_amazonas import config  # noqa: E402
from vigilante_amazonas.utilidades.registro import configurar_registro  # noqa: E402

logger = configurar_registro("main")

_detener = threading.Event()


def _manejar_senal(numero: int, marco: FrameType | None) -> None:
    logger.info(f"señal {numero} recibida: apagado limpio en curso…")
    _detener.set()


def _iniciar_dashboard(motor) -> None:  # noqa: ANN001
    """Dashboard FastAPI en hilo daemon (lanzador compartido e idempotente)."""
    from vigilante_amazonas.web.lanzador import iniciar_dashboard
    iniciar_dashboard(motor)


def main() -> int:
    config.crear_directorios()
    signal.signal(signal.SIGINT, _manejar_senal)
    signal.signal(signal.SIGTERM, _manejar_senal)
    logger.info("=== VIGILANTE-AMAZONAS iniciando ===",
                extra={"datos": {"camaras": list(config.CAMARAS_RTSP),
                                 "fps_objetivo": config.FPS_PROCESAMIENTO}})

    # 1) Núcleo de servicios compartidos (modelos, galería, alertas :8091).
    from vigilante_amazonas.servicios import get_servicios
    servicios = get_servicios()

    # 2) Pipeline autónomo (solo si hay cámaras configuradas).
    captura = None
    motor = None
    analizador = None
    if config.CAMARAS_RTSP:
        from vigilante_amazonas.captura.fuente_rtsp import CapturaMulticamara
        from vigilante_amazonas.deteccion.motor import MotorVigilante
        from vigilante_amazonas.servicios.analizador_asincrono import (
            AnalizadorAsincrono)
        from vigilante_amazonas.web.base_datos import get_bd

        captura = CapturaMulticamara()
        motor = MotorVigilante(captura)
        bd = get_bd()
        for nombre, url in config.CAMARAS_RTSP.items():
            bd.registrar_camara(nombre, url)

        # seguridad + Re-ID corren en su propio hilo (la detección no espera).
        analizador = AnalizadorAsincrono(servicios, motor)
        motor.registrar_consumidor(analizador.consumidor)
        analizador.start()

        if servicios.motor_reid is not None:
            def rotular(evento) -> None:  # noqa: ANN001
                """Nombre de la persona identificada sobre su caja (visor)."""
                etiquetas = motor.etiquetas_extra.setdefault(evento.camara, {})
                etiquetas[evento.track_id] = f"{evento.persona} ({evento.nivel})"
            servicios.motor_reid.registrar_consumidor(rotular)

        captura.iniciar()
        motor.start()
    else:
        logger.warning("CAMARAS_RTSP vacío: modo SOLO-SERVICIOS (el modo "
                       "websocket del servidor ELDE sigue disponible)")

    # 3) Dashboard.
    _iniciar_dashboard(motor)

    # Bucle principal (espera señal de apagado).
    try:
        while not _detener.is_set():
            _detener.wait(1.0)
    except KeyboardInterrupt:
        pass

    # Apagado limpio.
    logger.info("deteniendo servicios…")
    if motor is not None:
        motor.detener()
        motor.join(timeout=5.0)
    if captura is not None:
        captura.detener()
    if analizador is not None:
        analizador.detener()
    if servicios.vlm is not None:
        servicios.vlm.detener()
    logger.info("=== VIGILANTE-AMAZONAS detenido ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
