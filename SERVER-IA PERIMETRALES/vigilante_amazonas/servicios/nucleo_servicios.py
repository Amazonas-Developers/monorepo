"""
Núcleo COMPARTIDO de servicios (singleton por proceso).

`get_servicios()` construye una sola vez:
  - seguridad:   ClasificadorSeguridad (Hito 3)
  - motor_reid:  MotorReID rostro+vestimenta (Hito 4)
  - vlm:         VerificadorVLM no bloqueante (Hito 6)  [puede ser None]
  - alertas:     EmisorAlertas SQLite + Socket.IO (Hito 7)

Lo usan el pipeline propio (motor de detección), el adaptador websocket del
servidor ELDE y el puente del modo Perimetrales. Cada servicio degrada con
gracia si su modelo no está disponible; el sistema nunca se cae por eso.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_lock = threading.Lock()
_instancia: "Servicios | None" = None


@dataclass
class Servicios:
    """Contenedor de los servicios compartidos del proceso."""
    seguridad: Any            # ClasificadorSeguridad
    motor_reid: Any           # MotorReID (Hito 4)
    vlm: Any                  # VerificadorVLM o None (Hito 6)
    alertas: Any              # EmisorAlertas o None (Hito 7)


def get_servicios() -> Servicios:
    """Singleton: modelos y galería compartidos entre todos los modos."""
    global _instancia
    with _lock:
        if _instancia is not None:
            return _instancia

        from vigilante_amazonas.servicios.clasificador_seguridad import (
            ClasificadorSeguridad)
        seguridad = ClasificadorSeguridad()

        motor_reid: Any = None
        try:
            from vigilante_amazonas.servicios.motor_reid import MotorReID
            motor_reid = MotorReID()
        except Exception as exc:
            logger.warning(f"MotorReID no disponible aún: {exc}")

        vlm: Any = None
        try:
            from vigilante_amazonas.servicios.verificador_vlm import crear_vlm
            vlm = crear_vlm()
        except Exception as exc:
            logger.warning(f"VLM verificador no disponible: {exc}")

        alertas: Any = None
        try:
            from vigilante_amazonas.servicios.emisor_alertas import EmisorAlertas
            alertas = EmisorAlertas()
        except Exception as exc:
            logger.warning(f"EmisorAlertas no disponible aún: {exc}")

        # Cableado entre servicios (si existen).
        if motor_reid is not None:
            if vlm is not None:
                motor_reid.conectar_vlm(vlm)
            if alertas is not None:
                motor_reid.registrar_consumidor(alertas.procesar_evento)

        _instancia = Servicios(seguridad=seguridad, motor_reid=motor_reid,
                               vlm=vlm, alertas=alertas)
        logger.info("núcleo de servicios inicializado",
                    extra={"datos": {
                        "seguridad": bool(getattr(seguridad, "disponible", False)),
                        "motor_reid": motor_reid is not None,
                        "vlm": vlm is not None,
                        "alertas": alertas is not None}})
        return _instancia
