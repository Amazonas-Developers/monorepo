"""
Arranca el servidor de inferencia (websocket :9000) SIN interfaz gráfica.

Equivalente a abrir main.py y pulsar "Iniciar servidor", pero apto para
lanzarse desatendido (scripts, PM2, inicio automático). Detener: Ctrl+C o
matar el proceso.

Uso:
    venv\\Scripts\\python.exe iniciar_servidor_headless.py [puerto]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _asegurar_venv() -> None:
    """Si se lanzó con OTRO Python (p. ej. el global de Program Files), se
    re-lanza con el del venv. GOTCHA conocido de esta máquina: dos servidores
    (venv y global) compitiendo por el puerto 9000 — el global no tiene el
    stack completo (supervision/tensorrt/socketio) y rompe el modo
    VigilanteAmazonas en silencio."""
    base = Path(__file__).resolve().parent
    # Windows: venv\Scripts\python.exe — Linux/Mac: venv/bin/python
    if os.name == "nt":
        venv_py = base / "venv" / "Scripts" / "python.exe"
    else:
        venv_py = base / "venv" / "bin" / "python"
    actual = Path(sys.executable).resolve()
    if venv_py.exists() and actual != venv_py.resolve():
        print(f"[AVISO] lanzado con {actual}; re-lanzando con el venv: {venv_py}")
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)


_asegurar_venv()

import uvicorn  # noqa: E402  (tras asegurar el venv)


def _iniciar_dashboard_vigilante() -> None:
    """Levanta el dashboard de galería (:8090) desde el arranque, en ESTE
    mismo proceso.

    Así un solo proceso ofrece todo (websocket 9000 + dashboard 8090 +
    alertas 8091) y no hace falta lanzar `vigilante_amazonas/main.py` aparte
    (que duplicaría los modelos en la GPU y chocaría de puertos). Crear la app
    NO carga modelos: se resuelven de forma perezosa al usarlos."""
    try:
        from vigilante_amazonas.web.lanzador import iniciar_dashboard
        from vigilante_amazonas import config as vig_config
        iniciar_dashboard()
        print(f"Dashboard de galería:  http://localhost:{vig_config.PUERTO_API}")
    except Exception as exc:
        print(f"[AVISO] dashboard de VIGILANTE no disponible: {exc}")


def _iniciar_dashboard_tienda() -> None:
    """Levanta el dashboard de TIENDA (:9030) en ESTE mismo proceso.

    Es el que abre el boton "Dashboard" del cliente tienda_view: pasillos
    (camara) mas y menos frecuentados, mapa de calor, aforo, genero/edad y
    franja horaria. Comparte los datos del servidor, asi que no cuesta ni
    VRAM ni un proceso aparte."""
    try:
        from src.app.dashboard_tienda import (PUERTO_TIENDA,
                                              iniciar_dashboard_tienda)
        iniciar_dashboard_tienda()
        print(f"Dashboard de TIENDA:   http://localhost:{PUERTO_TIENDA}")
    except Exception as exc:
        print(f"[AVISO] dashboard de tienda no disponible: {exc}")


def main() -> int:
    puerto: int = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    # Import tardío: carga modelos/GPU (tarda ~1 min la primera vez).
    from src.app.app import app
    _iniciar_dashboard_vigilante()
    _iniciar_dashboard_tienda()
    print(f"Servidor de inferencia SIN GUI en ws://0.0.0.0:{puerto}/ws "
          f"(Ctrl+C para detener)")
    uvicorn.run(app, host="0.0.0.0", port=puerto, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
