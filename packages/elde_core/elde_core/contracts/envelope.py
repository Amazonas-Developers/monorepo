"""
Envelope comun de todo mensaje del ecosistema ELDE.

Fuente unica de verdad: este mismo archivo lo importan los clientes y el
servidor. Si el esquema cambia, cambia para los dos a la vez — que es
justo lo que hoy no ocurre y por lo que el servidor lee 32 claves sueltas
con `data.get()` sin validar nada.

Diseno del envelope, y por que:

- `client_type` es QUE APLICACION habla (tienda, perimetrales, amazonas,
  managers). Hoy no existe: el servidor solo recibe `type_inference`, que es
  otra cosa.
- `pipeline` es QUE MODO DE INFERENCIA se pide. Es lo que hoy viaja como
  `type_inference`. Se separan porque un mismo cliente puede cambiar de modo
  sin dejar de ser el mismo cliente.
- `device_id` identifica la CAMARA de forma **estable entre reinicios**. Hoy
  es un `uuid4()` generado al crear el panel de video (H-11), asi que cada
  arranque inventa camaras nuevas y no hay historico por zona. El contrato lo
  exige estable: es la razon por la que H-11 se arregla en este hito y no mas
  tarde.
- `event_version` permite evolucionar el payload sin romper clientes viejos.
- `timestamp_utc` lo pone el emisor; el servidor conserva el suyo aparte.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClientType(str, Enum):
    """Que aplicacion emite. Uno por cliente de escritorio."""
    TIENDA = "tienda"
    PERIMETRALES = "perimetrales"
    AMAZONAS = "amazonas"
    MANAGERS = "managers"


class Pipeline(str, Enum):
    """Modo de inferencia solicitado al servidor.

    Los valores son los 8 que `_build_processor` acepta hoy, en su forma
    normalizada. La traduccion desde las cadenas antiguas ("Personal de
    Amazonas", etc.) vive en `compat.py`."""
    PERSONAL_AMAZONAS = "personal_amazonas"
    PERIMETRALES = "perimetrales"
    PERIMETRALES_BOTSORT = "perimetrales_botsort"
    PERIMETRALES_MULTICAM = "perimetrales_multicam"
    VIGILANTE_AMAZONAS = "vigilante_amazonas"
    AUTOLAVADO = "autolavado"
    HUMMUS = "hummus"
    MISTERS = "misters"


class EventType(str, Enum):
    """Que esta pasando. Sustituye al `event: "inference"` unico de hoy."""
    CONNECTION_INIT = "connection.init"
    FRAME_INFERENCE = "frame.inference"     # cliente -> servidor
    FRAME_RESULT = "frame.result"           # servidor -> cliente
    HEARTBEAT = "heartbeat"
    ERROR = "error"


_ID_VALIDO = re.compile(r'^[A-Za-z0-9_.:-]{1,96}$')


class Envelope(BaseModel):
    """Cabecera comun. El `payload` lo valida cada evento por su cuenta."""

    model_config = ConfigDict(extra='forbid', use_enum_values=False)

    client_type: ClientType
    site_id: str = Field(
        ..., description="Local o sucursal. Permite agregar por sitio en los "
                         "dashboards; hoy no existe y por eso no se puede "
                         "comparar entre sucursales.")
    device_id: str = Field(
        ..., description="Camara. ESTABLE entre reinicios (ver H-11).")
    event_type: EventType
    event_version: int = Field(default=1, ge=1)
    timestamp_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    pipeline: Optional[Pipeline] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator('site_id', 'device_id')
    @classmethod
    def _id_es_usable(cls, v: str) -> str:
        """Rechaza identificadores que romperian rutas de archivo o consultas.

        Los `device_id` acaban siendo nombres de archivo (heatmaps, capturas),
        asi que no pueden llevar separadores ni espacios."""
        v = (v or '').strip()
        if not _ID_VALIDO.match(v):
            raise ValueError(
                f"identificador invalido: {v!r}. Se admiten letras, digitos y "
                f"_ . : - (1 a 96 caracteres), sin espacios ni barras")
        return v

    @field_validator('timestamp_utc')
    @classmethod
    def _con_zona_horaria(cls, v: datetime) -> datetime:
        """Sin zona horaria explicita, un timestamp no sirve para agregar por
        franja: se asume UTC en vez de la hora local del que lo emitio."""
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
