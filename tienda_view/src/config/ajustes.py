"""
Ajustes del cliente de tienda, con validacion al arrancar.

## Por que existe

Antes la URL del servidor vivia como literal en dos sitios distintos
(`windows_main.py` y `window_bar.py`), con la IP `72.68.60.171` escrita a
mano. Cambiar de servidor obligaba a editar codigo, y era facil que los dos
sitios acabaran apuntando a servidores distintos.

## De donde sale cada valor

Por orden de prioridad: **variable de entorno** > **`.env` del cliente** >
**valor por defecto**. El `.env` no se versiona, asi que cada instalacion
puede apuntar a donde quiera sin tocar el repositorio.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# Identidad de este cliente en el contrato del HITO 3.
CLIENT_TYPE = 'tienda'

# Modo de inferencia que este cliente sabe pedir. Es el unico que ofrece su
# selector: no es un cliente multimodo.
PIPELINE = 'Personal de Amazonas'


@dataclass(frozen=True)
class Ajustes:
    """Configuracion efectiva. Inmutable: se calcula una vez al arrancar."""

    server_ws_url: str
    site_id: str
    client_type: str = CLIENT_TYPE
    pipeline: str = PIPELINE

    @property
    def server_http_url(self) -> str:
        """La URL HTTP del MISMO servidor, derivada del websocket.

        Se deriva en vez de configurarse aparte para que no puedan quedar
        apuntando a servidores distintos, que es lo que pasaba cuando cada
        archivo llevaba su propio literal."""
        base = (self.server_ws_url
                .replace('wss://', 'https://')
                .replace('ws://', 'http://'))
        if '/ws' in base:
            base = base.rsplit('/ws', 1)[0]
        return base.rstrip('/')

    @property
    def dashboard_url(self) -> str:
        return f'{self.server_http_url}/dashboard'


def _limpio(valor: Optional[str]) -> str:
    return (valor or '').strip().strip('"').strip("'")


def cargar() -> Ajustes:
    """Lee la configuracion y valida lo imprescindible.

    Fallar aqui con un mensaje claro es mucho mejor que arrancar y quedarse
    esperando una conexion a una URL sin sentido."""
    ws = _limpio(os.getenv('server_ws_url'))
    if not ws:
        raise SystemExit(
            'Falta `server_ws_url` en el .env de tienda_view.\n'
            "Ejemplo:  server_ws_url = 'ws://192.168.1.50:9000/ws'")
    if not ws.startswith(('ws://', 'wss://')):
        raise SystemExit(
            f'`server_ws_url` debe empezar por ws:// o wss://, y vale {ws!r}.')

    # site_id identifica el LOCAL en el contrato: sin el, los dashboards no
    # pueden comparar entre sucursales. Se admite ausente para no romper
    # instalaciones actuales, con un valor neutro.
    site = _limpio(os.getenv('site_id')) or 'sitio-unico'

    return Ajustes(server_ws_url=ws, site_id=site)
