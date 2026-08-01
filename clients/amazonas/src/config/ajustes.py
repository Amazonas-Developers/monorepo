"""
Ajustes del cliente AMAZONAS VIEW, con validacion al arrancar.

La regla 6 del refactor prohibe hardcodear IPs, puertos y rutas. Antes la URL
del servidor era un literal en el codigo con una IP concreta como valor por
defecto: si el .env no la traia, el cliente intentaba conectar en silencio a
un servidor que quiza no era el suyo, y el operador solo veia que "no llega
nada".

Es un cliente MULTIMODO: su selector ofrece Hummus, HummusVLM, Autolavado,
Perimetrales, PerimetralesMultiCam y Personal de Amazonas. Por eso declarar
`client_type` importa: el servidor lo deducia del modo de inferencia, y esa
deduccion falla justamente en los clientes que ofrecen varios.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# Identidad de este cliente en el contrato del HITO 3.
CLIENT_TYPE = 'amazonas'


@dataclass(frozen=True)
class Ajustes:
    """Configuracion efectiva. Inmutable: se calcula una vez al arrancar."""

    server_ws_url: str
    site_id: str
    client_type: str = CLIENT_TYPE

    @property
    def server_http_url(self) -> str:
        """La URL HTTP del MISMO servidor, derivada del websocket, para que no
        puedan acabar apuntando a maquinas distintas."""
        base = (self.server_ws_url
                .replace('wss://', 'https://')
                .replace('ws://', 'http://'))
        if '/ws' in base:
            base = base.rsplit('/ws', 1)[0]
        return base.rstrip('/')

    @property
    def dashboard_url(self) -> str:
        """El dashboard de PRODUCTO de Amazonas (genero/edad, totales).

        Hasta el 1-ago-2026 apuntaba a `/dashboard` (panel tecnico de
        visitantes); la pagina de producto enlaza a aquel."""
        return f'{self.server_http_url}/dashboards/amazonas/'


def _limpio(valor: Optional[str]) -> str:
    return (valor or '').strip().strip('"').strip("'")


def cargar() -> Ajustes:
    """Lee la configuracion y valida lo imprescindible."""
    ws = _limpio(os.getenv('server_ws_url'))
    if not ws:
        raise SystemExit(
            'Falta `server_ws_url` en el .env de clients/amazonas.'
            '\nEjemplo:  server_ws_url = "ws://192.168.1.50:9000/ws"')
    if not ws.startswith(('ws://', 'wss://')):
        raise SystemExit(
            f'`server_ws_url` debe empezar por ws:// o wss://, y vale {ws!r}.')

    return Ajustes(server_ws_url=ws,
                   site_id=_limpio(os.getenv('site_id')) or 'sitio-unico')
