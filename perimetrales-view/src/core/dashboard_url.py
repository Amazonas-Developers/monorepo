"""Movido a `config/ajustes.py`.

Se conserva como redireccion para no tocar los imports existentes. La logica
—derivar la URL del panel desde `server_ws_url`— es la misma; lo que se anadio
al mudarla es la **validacion**: antes, si faltaba `server_ws_url`, se caia en
silencio a 127.0.0.1 y el cliente intentaba conectar a un servidor local que
quiza no estaba.
"""

from config import cargar

PUERTO_DASHBOARD_DEFECTO = "5333"


def url_dashboard() -> str:
    """URL del panel de VIGILANTE (sin barra final)."""
    return cargar().panel_url
