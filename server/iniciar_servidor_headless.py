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

# Registro unificado (nucleo), ANTES de importar nada del servidor: sus modulos
# hacen `logging.getLogger(__name__)` al importarse y `app.py` llama a
# `logging.basicConfig()`. Configurando primero, esa llamada queda en nada (no
# hace nada si la raiz ya tiene manejadores) y el servidor entero escribe con el
# mismo formato que los cuatro clientes, con `server/<site_id>` delante.
from elde_core.logging import configurar as _iniciar_registro  # noqa: E402

_iniciar_registro(
    'server',
    (os.getenv('site_id') or '').strip() or 'sitio-unico',
    raiz_proyecto=Path(__file__).resolve().parent,
    tambien_raiz=True)

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


# El dashboard de TIENDA del 9030 se RETIRO el 31-jul-2026 (decision del
# cierre del refactor, HITO 11): lo sustituye /dashboards/tienda/, la pagina
# de dominio que lee de /api/v1. El modulo `src/app/dashboard_tienda.py` sigue
# en el arbol: revivirlo es volver a llamar a `iniciar_dashboard_tienda()`
# aqui, y esta anotado como rollback en docs/refactor/12_CIERRE.md.


def main() -> int:
    puerto: int = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    # Import tardío: carga modelos/GPU (tarda ~1 min la primera vez).
    from src.app.app import app
    _iniciar_dashboard_vigilante()
    print(f"Dashboards por dominio: http://localhost:{puerto}/dashboards/")
    print(f"Servidor de inferencia SIN GUI en ws://0.0.0.0:{puerto}/ws "
          f"(Ctrl+C para detener)")
    # Keepalive del websocket (H-26). Uvicorn manda un PING de protocolo y
    # si el PONG no vuelve a tiempo cierra con 1011 "keepalive ping timeout".
    # Con los valores por defecto (20s/20s) mataba conexiones VIVAS cada
    # 40-100s: el pong se retrasa cuando la GUI del cliente se congela unos
    # segundos (AppHangB1) o cuando hay contrapresion (el cliente escribe mas
    # rapido de lo que el servidor drena y el pong queda en cola tras los
    # frames). Reproducido sinteticamente: closeCode=1011 a los 51s con
    # 427 frames enviados y 3 respuestas. Un timeout de 60s tolera esos
    # baches y sigue detectando peers realmente muertos; el cliente ademas
    # se reconecta solo a los 5s si aun asi se corta.
    ping_cada = float(os.getenv("ELDE_WS_PING_INTERVALO_SEG", "20"))
    ping_timeout = float(os.getenv("ELDE_WS_PING_TIMEOUT_SEG", "60"))
    uvicorn.run(app, host="0.0.0.0", port=puerto, log_level="info",
                ws_ping_interval=ping_cada, ws_ping_timeout=ping_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
