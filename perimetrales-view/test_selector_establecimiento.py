"""
Prueba del selector de establecimiento del pie (restaurado).

  C1: el combo existe SIEMPRE y arranca deshabilitado ("Cargando…").
  C2: cargar_establecimientos() lo puebla y aplica la selección pedida.
  C3: si el seleccionado no existe, cae al primero.
  C4: la señal establishments_loaded de Jarvis_api se emite con la lista
      REAL (red) y permite poblar el combo.

Uso:
    venv\\Scripts\\python.exe test_selector_establecimiento.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dotenv import load_dotenv
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core.network.jarvis_api import Jarvis_api
from gui.components.custom_status_bar import CustomStatusBar


def main() -> int:
    load_dotenv()
    app = QApplication(sys.argv)

    barra = CustomStatusBar(list_establishment=[])
    c1 = (hasattr(barra, "selector_establishment")
          and not barra.selector_establishment.isEnabled()
          and "Cargando" in barra.selector_establishment.currentText())
    print(f"{'OK' if c1 else 'FALLO'} C1: combo presente y en estado 'Cargando…'")

    barra.cargar_establecimientos(["Local A", "Local B", "Local C"], "Local B")
    c2 = (barra.selector_establishment.isEnabled()
          and barra.selector_establishment.count() == 3
          and barra.selector_establishment.currentText() == "Local B")
    print(f"{'OK' if c2 else 'FALLO'} C2: poblado y selección aplicada "
          f"({barra.selector_establishment.currentText()})")

    barra.cargar_establecimientos(["Local A", "Local B"], "NoExiste")
    c3 = barra.selector_establishment.currentText() == "Local A"
    print(f"{'OK' if c3 else 'FALLO'} C3: selección inexistente cae al primero")

    # C4: señal real contra el servidor de Jarvis.
    jarvis = Jarvis_api(emailuser=os.getenv("jarvis_email"),
                        password=os.getenv("jarvis_password"),
                        url_api=os.getenv("jarvis_url"))
    recibido: list = []
    loop = QEventLoop()
    jarvis.establishments_loaded.connect(
        lambda lista: (recibido.append(lista), loop.quit()))
    QTimer.singleShot(15000, loop.quit)   # tope de seguridad
    loop.exec()

    if recibido:
        nombres = [e.get("name", "") for e in recibido[0] if isinstance(e, dict)]
        barra.cargar_establecimientos(
            nombres, (jarvis.selected_establishment or {}).get("name"))
        c4 = (len(nombres) > 0
              and barra.selector_establishment.count() == len(nombres)
              and barra.selector_establishment.currentText()
              == jarvis.selected_establishment.get("name"))
        print(f"{'OK' if c4 else 'FALLO'} C4: señal real -> combo con "
              f"{len(nombres)} establecimientos, elegido "
              f"'{barra.selector_establishment.currentText()}'")
    else:
        c4 = False
        print("FALLO C4: la señal establishments_loaded no llegó (¿red?)")

    ok = all([c1, c2, c3, c4])
    print("=" * 50)
    print("OK SELECTOR SUPERADO" if ok else "FALLO en el selector")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
