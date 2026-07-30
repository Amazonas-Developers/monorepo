"""Contrato unico cliente <-> servidor. Fuente unica de verdad."""

from .envelope import ClientType, Envelope, EventType, Pipeline
from .eventos import (POR_EVENTO, ConnectionInit, Deteccion, ErrorEvento,
                      FrameInference, FrameResult, Heartbeat)
from .compat import desde_antiguo, es_formato_antiguo, hacia_antiguo, sin_migrar

__all__ = [
    "ClientType", "Envelope", "EventType", "Pipeline",
    "FrameInference", "FrameResult", "ConnectionInit", "Heartbeat",
    "ErrorEvento", "Deteccion", "POR_EVENTO",
    "es_formato_antiguo", "desde_antiguo", "hacia_antiguo", "sin_migrar",
]
