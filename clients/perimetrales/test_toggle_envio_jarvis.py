"""
Prueba del interruptor "Enviar a Jarvis" (activar/desactivar la API).

  C1: el checkbox existe en el pie y arranca marcado.
  C2: con el envío DESACTIVADO, on_alert NO llama a la API (el sidebar no
      se ve afectado: el forwarder simplemente ignora).
  C3: al REACTIVAR, vuelve a enviar.
  C4: el checkbox notifica por toggled (cableado UI -> forwarder).

Uso:
    venv\\Scripts\\python.exe test_toggle_envio_jarvis.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PySide6.QtWidgets import QApplication

from core.network.jarvis_alert_forwarder import JarvisAlertForwarder
from gui.components.custom_status_bar import CustomStatusBar


class _JarvisFake:
    def __init__(self):
        self.session_user = {"name": "X"}
        self.selected_establishment = {"name": "Local"}
        self.llamadas = []

    def enviar_novedad_async(self, base64_image='', title='', message=''):
        self.llamadas.append(title)


def _al(gid):
    return {"event_type": "llegada", "class_name": "PERSONA", "global_id": gid,
            "camera_name": "patio", "description": "x", "image_base64": "AAAA"}


def main() -> int:
    app = QApplication(sys.argv)
    fake = _JarvisFake()
    fwd = JarvisAlertForwarder(fake)
    barra = CustomStatusBar(list_establishment=[])

    c1 = hasattr(barra, "chk_envio_jarvis") and barra.chk_envio_jarvis.isChecked()
    print(f"{'OK' if c1 else 'FALLO'} C1: checkbox presente y marcado por defecto")

    # Cablear como hace main.py.
    barra.chk_envio_jarvis.toggled.connect(fwd.set_activo)

    # C2: desactivar -> no envía.
    barra.chk_envio_jarvis.setChecked(False)
    fwd.on_alert(_al("VIG-p-T1"))
    fwd.on_alert(_al("VIG-p-T2"))
    c2 = len(fake.llamadas) == 0 and fwd.activo is False
    print(f"{'OK' if c2 else 'FALLO'} C2: desactivado -> 0 envíos "
          f"({len(fake.llamadas)}, activo={fwd.activo})")

    # C3: reactivar -> envía.
    barra.chk_envio_jarvis.setChecked(True)
    fwd.on_alert(_al("VIG-p-T3"))
    c3 = len(fake.llamadas) == 1 and fwd.activo is True
    print(f"{'OK' if c3 else 'FALLO'} C3: reactivado -> envía ({len(fake.llamadas)})")

    # C4: el estado del checkbox manda (cableado toggled).
    barra.chk_envio_jarvis.setChecked(False)
    c4 = fwd.activo is False
    print(f"{'OK' if c4 else 'FALLO'} C4: toggled propaga al forwarder")

    ok = all([c1, c2, c3, c4])
    print("=" * 50)
    print("OK INTERRUPTOR SUPERADO" if ok else "FALLO en el interruptor")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
