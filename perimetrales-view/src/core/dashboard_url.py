"""
Resuelve la URL del panel de VIGILANTE-AMAZONAS (:5333).

El dashboard corre en el MISMO equipo que el servidor de inferencia, así que
su URL se deduce del `server_ws_url` del .env cambiando el esquema y el
puerto:

    ws://72.68.60.171:9000/ws   ->   http://72.68.60.171:5333

Se puede fijar a mano con `dashboard_url` en el .env (o cambiar solo el
puerto con `dashboard_port`).
"""

from __future__ import annotations

import os
import re

# El panel unifica la galeria de personas (antes :8090, ahora en
# /gestion) y el tablero de detecciones.
PUERTO_DASHBOARD_DEFECTO = "5333"


def url_dashboard() -> str:
    """URL del dashboard (sin barra final)."""
    explicita = (os.getenv("dashboard_url") or "").strip()
    if explicita:
        return explicita.rstrip("/")

    ws = (os.getenv("server_ws_url") or "ws://127.0.0.1:9000/ws").strip()
    coincidencia = re.match(r"wss?://([^:/]+)", ws, re.IGNORECASE)
    host = coincidencia.group(1) if coincidencia else "127.0.0.1"
    # wss:// (seguro) -> https://
    esquema = "https" if ws.lower().startswith("wss://") else "http"
    puerto = (os.getenv("dashboard_port") or PUERTO_DASHBOARD_DEFECTO).strip()
    return f"{esquema}://{host}:{puerto}"
