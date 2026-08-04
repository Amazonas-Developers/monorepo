"""
src/analityc/core/estacionamiento.py — Modo ESTACIONAMIENTO (4-ago-2026).

La óptica vehicular del motor vigilante, en SU PROPIO archivo junto al resto
de procesadores del servidor (regla del operador: un archivo por
funcionalidad, nada de módulos gigantes).

Pedido del operador: vigilancia completa de estacionamiento. Reutiliza TODO
el motor de VigilanteWS (detección 7 clases + ByteTrack + rastreador de área
con re-vinculación) y cambia solo la óptica:

  * Solo los VEHÍCULOS alertan (las personas se detectan y se dibujan, pero
    no generan tarjetas: en un estacionamiento la persona es contexto).
  * La alerta central es «estacionado»: un vehículo quieto en el área más de
    ESTACIONAMIENTO_UMBRAL_SEG (config de vigilante, 5 min por defecto). Por
    debajo, es solo un vehículo pasando.
  * La salida reporta el tiempo total que estuvo estacionado.
  * `metadata['ocupacion']` lleva cuántos vehículos hay AHORA en el área.
  * WhatsApp/Jarvis: mismo flujo del vigilante (toggle del pie + local de la
    cámara como encabezado), con sus propios eventos
    (ESTACIONAMIENTO_EVENTOS_WHATSAPP: llegada, estacionado, salida).

Importar del paquete vigilante desde aquí tiene precedente: es el mismo
sentido de dependencia que `puente_vigilante.py` (core -> vigilante).

Instancia PROPIA (no comparte el singleton del vigilante): cada modo tiene su
rastreador de área con su umbral y su lock — compartir el detector entre dos
locks distintos arriesgaría inferencias concurrentes sobre el mismo modelo.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.adaptador_websocket import VigilanteWS
from vigilante_amazonas.servicios.rastreador_area import (
    RastreadorArea, _formatear_permanencia)
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


def adaptar_tarjeta(tarjeta: Any) -> dict[str, Any] | None:
    """La óptica de estacionamiento sobre una tarjeta del área. PURA a
    propósito: se prueba sin GPU ni motor.

    - Tarjeta que no sea de VEHÍCULO -> None (se descarta).
    - 'permanencia' -> 'estacionado', con descripción propia.
    - llegada / salida / merodeo de vehículo -> pasan tal cual.
    """
    if not isinstance(tarjeta, dict):
        return None
    if str(tarjeta.get("clase_gruesa") or "").strip().lower() != "vehiculo":
        return None
    if str(tarjeta.get("event_type") or "").strip().lower() == "permanencia":
        adaptada = dict(tarjeta)
        adaptada["event_type"] = "estacionado"
        etiqueta = str(tarjeta.get("class_name") or "VEHÍCULO")
        duracion = _formatear_permanencia(
            float(tarjeta.get("permanencia_s") or 0.0))
        adaptada["description"] = f"{etiqueta} ESTACIONADO ({duracion})"
        return adaptada
    return tarjeta


class EstacionamientoWS(VigilanteWS):
    """VigilanteWS con la óptica de estacionamiento (ver módulo)."""

    def __init__(self) -> None:
        super().__init__()
        # Rastreador de área PROPIO con el umbral de "estacionado" (el del
        # vigilante sigue con AREA_ALERTA_PERMANENCIA_SEG global).
        from vigilante_amazonas.servicios.detector_merodeo import DetectorMerodeo
        self.area = RastreadorArea(
            DetectorMerodeo(),
            umbral_permanencia_seg=config.ESTACIONAMIENTO_UMBRAL_SEG)
        self._whatsapp_eventos = config.ESTACIONAMIENTO_EVENTOS_WHATSAPP
        self._whatsapp_clases = ("vehiculo",)
        logger.info(
            "EstacionamientoWS listo: umbral estacionado = %.0f s; "
            "eventos WhatsApp = %s",
            config.ESTACIONAMIENTO_UMBRAL_SEG,
            ",".join(config.ESTACIONAMIENTO_EVENTOS_WHATSAPP))

    def _filtrar_tarjeta(self, tarjeta: dict[str, Any]) -> dict[str, Any] | None:
        return adaptar_tarjeta(tarjeta)

    def process_frame(self, img: np.ndarray, camera_id: str,
                      camera_name: str | None = None,
                      track_classes: list[int] | None = None,
                      draw: bool = True,
                      roi: Any = None,
                      roi_activate: bool = False,
                      establecimiento: str | None = None) -> tuple[np.ndarray, dict[str, Any]]:
        img_salida, metadata = super().process_frame(
            img, camera_id, camera_name=camera_name,
            track_classes=track_classes, draw=draw,
            roi=roi, roi_activate=roi_activate,
            establecimiento=establecimiento)
        # Ocupación vigente del área (vehículos): el cliente y el dashboard
        # la leen del metadata sin cálculo propio.
        try:
            camara = str(camera_name or camera_id)
            metadata["ocupacion"] = self.area.ocupacion(camara, "vehiculo")
        except Exception:                    # noqa: BLE001 — nunca al frame loop
            pass
        return img_salida, metadata


_instancia: EstacionamientoWS | None = None
_lock_instancia = threading.Lock()


def get_estacionamiento_ws() -> EstacionamientoWS:
    """Singleton por proceso (mismo contrato que get_vigilante_ws)."""
    global _instancia
    with _lock_instancia:
        if _instancia is None:
            _instancia = EstacionamientoWS()
        return _instancia
