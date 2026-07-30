"""
Prueba de conexión a la API de Jarvis365 (sin GUI).

Verifica, contra el servidor real:
  1. Login (auth/login) con las credenciales del .env.
  2. Carga de establecimientos (localligth) + auto-selección.
  3. (Opcional) Envío de una novedad de prueba si se pasa --enviar.

Uso:
    venv\\Scripts\\python.exe test_jarvis_conexion.py
    venv\\Scripts\\python.exe test_jarvis_conexion.py --enviar   # manda 1 alerta real
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dotenv import load_dotenv
from PySide6.QtCore import QCoreApplication, QTimer

from core.network.jarvis_api import Jarvis_api


def main() -> int:
    load_dotenv()
    app = QCoreApplication(sys.argv)

    jarvis = Jarvis_api(
        emailuser=os.getenv("jarvis_email"),
        password=os.getenv("jarvis_password"),
        url_api=os.getenv("jarvis_url"),
    )

    enviar = "--enviar" in sys.argv
    estado = {"fin": 0}

    def revisar():
        print("\n================ RESULTADO CONEXIÓN JARVIS ================")
        auth = jarvis.session_user is not None
        print(f"  {'OK' if auth else 'FALLO'}  Login autenticado: {auth}")
        if auth and isinstance(jarvis.session_user, dict):
            print(f"       usuario: {jarvis.session_user.get('name','?')} "
                  f"{jarvis.session_user.get('surName','')}")
        n = len(jarvis.list_of_establishments or [])
        print(f"  {'OK' if n else 'FALLO'}  Establecimientos cargados: {n}")
        sel = jarvis.selected_establishment
        sel_nom = sel.get("name") if isinstance(sel, dict) else None
        print(f"  {'OK' if sel_nom else 'FALLO'}  Establecimiento auto-seleccionado: {sel_nom}")
        if n and isinstance(jarvis.list_of_establishments, list):
            nombres = [e.get("name", "?") for e in jarvis.list_of_establishments[:6]
                       if isinstance(e, dict)]
            print(f"       disponibles (máx 6): {nombres}")

        if enviar and auth and sel_nom:
            print("  → enviando novedad de PRUEBA a Jarvis…")
            jarvis.enviar_novedad_async(
                base64_image="", title="PRUEBA integración perimetrales-view",
                message="Alerta de prueba (sin imagen) desde test_jarvis_conexion.py")
            QTimer.singleShot(6000, app.quit)   # dar tiempo al POST /novelties
        else:
            QTimer.singleShot(500, app.quit)
        print("==========================================================")

    # Dar tiempo a que login + establecimientos respondan (red).
    QTimer.singleShot(7000, revisar)
    QTimer.singleShot(20000, app.quit)   # tope de seguridad
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
