"""
Ajustes del cliente de PERIMETRALES, con validacion al arrancar.

Absorbe lo que hacia `core/dashboard_url.py`, que ya derivaba bien la URL del
panel a partir del websocket, y le anade lo que faltaba: **validar** que
`server_ws_url` existe y tiene sentido. Antes, si faltaba, se caia a
`ws://127.0.0.1:9000/ws` en silencio y el cliente se quedaba intentando
conectar a un servidor local que quiza no estaba.

## Dos servidores distintos, un solo origen

Este cliente habla con dos cosas en la misma maquina:

- el **servidor de inferencia** por websocket (puerto 9000),
- el **panel de VIGILANTE** por HTTP (puerto 5333), que unifica la galeria de
  personas de interes y el tablero de detecciones.

Ambas URLs salen del mismo `server_ws_url`, asi que no pueden acabar apuntando
a maquinas distintas. El puerto del panel se puede cambiar sin tocar codigo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

# Identidad de este cliente en el contrato del HITO 3.
CLIENT_TYPE = 'perimetrales'

# Puerto del panel de VIGILANTE. Unifico lo que antes eran dos servidores
# (galeria en :8090 y tablero aparte); hoy todo vive aqui, con /gestion dentro.
PUERTO_PANEL_DEFECTO = '5333'


@dataclass(frozen=True)
class Ajustes:
    """Configuracion efectiva. Inmutable: se calcula una vez al arrancar."""

    server_ws_url: str
    site_id: str
    puerto_panel: str = PUERTO_PANEL_DEFECTO
    panel_url_fijada: Optional[str] = None
    client_type: str = CLIENT_TYPE

    @property
    def _host(self) -> str:
        m = re.match(r'wss?://([^:/]+)', self.server_ws_url, re.IGNORECASE)
        return m.group(1) if m else '127.0.0.1'

    @property
    def _esquema_http(self) -> str:
        return 'https' if self.server_ws_url.lower().startswith('wss://') \
            else 'http'

    @property
    def panel_url(self) -> str:
        """Panel de VIGILANTE. `dashboard_url` en el .env lo fuerza."""
        if self.panel_url_fijada:
            return self.panel_url_fijada.rstrip('/')
        return f'{self._esquema_http}://{self._host}:{self.puerto_panel}'

    @property
    def gestion_url(self) -> str:
        """Galeria de personas de interes, dentro del panel."""
        return f'{self.panel_url}/gestion'

    @property
    def server_http_url(self) -> str:
        """El servidor de inferencia por HTTP (mismo host y puerto que el ws)."""
        base = (self.server_ws_url
                .replace('wss://', 'https://')
                .replace('ws://', 'http://'))
        if '/ws' in base:
            base = base.rsplit('/ws', 1)[0]
        return base.rstrip('/')


def _limpio(valor: Optional[str]) -> str:
    return (valor or '').strip().strip('"').strip("'")


def cargar() -> Ajustes:
    """Lee la configuracion y valida lo imprescindible."""
    ws = _limpio(os.getenv('server_ws_url'))
    if not ws:
        raise SystemExit(
            'Falta `server_ws_url` en el .env de perimetrales-view.\n'
            "Ejemplo:  server_ws_url = 'ws://192.168.1.50:9000/ws'")
    if not ws.startswith(('ws://', 'wss://')):
        raise SystemExit(
            f'`server_ws_url` debe empezar por ws:// o wss://, y vale {ws!r}.')

    return Ajustes(
        server_ws_url=ws,
        site_id=_limpio(os.getenv('site_id')) or 'sitio-unico',
        puerto_panel=_limpio(os.getenv('dashboard_port')) or PUERTO_PANEL_DEFECTO,
        panel_url_fijada=_limpio(os.getenv('dashboard_url')) or None,
    )
