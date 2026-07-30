"""
Prueba del botón "Personas de interés" del pie + resolución de la URL.

  C1: el botón existe en el pie, con su icono y tooltip.
  C2: url_dashboard() deduce http://<host>:8090 del server_ws_url del .env.
  C3: overrides del .env (dashboard_url / dashboard_port) tienen prioridad.
  C4: wss:// (seguro) -> https://.
  C5: el icono resource/person.png existe y es válido.

Uso:
    venv\\Scripts\\python.exe test_boton_dashboard.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "src"))
os.chdir(RAIZ)          # las rutas de icono son relativas al proyecto

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication

from core.dashboard_url import url_dashboard
from gui.components.custom_status_bar import CustomStatusBar


def main() -> int:
    load_dotenv()
    app = QApplication(sys.argv)
    barra = CustomStatusBar(list_establishment=[])

    btn = getattr(barra, "btn_galeria_personas", None)
    c1 = btn is not None and not btn.icon().isNull() and "Personas de inter" in btn.toolTip()
    print(f"{'OK' if c1 else 'FALLO'} C1: botón presente con icono y tooltip")

    # C2: derivación desde el server_ws_url real del .env.
    ws = os.getenv("server_ws_url", "")
    url = url_dashboard()
    host_ws = ws.split("//")[-1].split(":")[0] if "//" in ws else "?"
    c2 = url.startswith("http://") and host_ws in url and url.endswith(":8090")
    print(f"{'OK' if c2 else 'FALLO'} C2: '{ws}' -> '{url}'")

    # C3: overrides.
    os.environ["dashboard_port"] = "9999"
    c3a = url_dashboard().endswith(":9999")
    os.environ["dashboard_url"] = "http://mi-servidor:1234/"
    c3b = url_dashboard() == "http://mi-servidor:1234"
    del os.environ["dashboard_url"]; del os.environ["dashboard_port"]
    c3 = c3a and c3b
    print(f"{'OK' if c3 else 'FALLO'} C3: overrides dashboard_port/dashboard_url")

    # C4: wss -> https.
    guardado = os.environ.get("server_ws_url", "")
    os.environ["server_ws_url"] = "wss://seguro.example.com:9000/ws"
    c4 = url_dashboard() == "https://seguro.example.com:8090"
    os.environ["server_ws_url"] = guardado
    print(f"{'OK' if c4 else 'FALLO'} C4: wss:// -> https://")

    # C5: icono.
    ico = RAIZ / "resource" / "person.png"
    c5 = ico.is_file() and ico.stat().st_size > 200
    print(f"{'OK' if c5 else 'FALLO'} C5: icono {ico.name} ({ico.stat().st_size if ico.is_file() else 0} bytes)")

    ok = all([c1, c2, c3, c4, c5])
    print("=" * 50)
    print("OK BOTON DASHBOARD SUPERADO" if ok else "FALLO en el botón")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
