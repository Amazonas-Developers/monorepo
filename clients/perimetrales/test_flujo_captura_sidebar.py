"""
Prueba del FLUJO completo: alerta del servidor -> foto en capture/ -> tarjeta
del panel lateral con screenshot_path -> click abre el Explorador.

  C1: render_box.on_text_message_received guarda la foto en capture/ y emite
      la alerta con screenshot_path apuntando a ese archivo.
  C2: AlertItemWidget guarda esa ruta (la usa su mouseReleaseEvent para abrir
      el Explorador con la imagen seleccionada).
  C3: el guardado NO depende del envío a Jarvis (se guarda igual).

Uso:
    venv\\Scripts\\python.exe test_flujo_captura_sidebar.py
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication

from gui.components.sidebar.alerts_sidebar import AlertItemWidget, AlertsSidebar
from core.capture_store import guardar_captura


def _jpg_b64() -> str:
    img = np.full((90, 160, 3), 40, dtype=np.uint8)
    cv2.putText(img, "ALERTA", (5, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", img)
    return base64.b64encode(buf.tobytes()).decode()


def main() -> int:
    app = QApplication(sys.argv)
    img_b64 = _jpg_b64()

    # C1: replica EXACTA de lo que hace render_box.on_text_message_received.
    evento, clase, camara = "llegada", "PERSONA", "patio"
    ruta = guardar_captura(img_b64, clase=clase, camara=camara, evento=evento)
    alerta = {
        "event_type": evento, "class_name": clase, "clase_gruesa": "persona",
        "global_id": "VIG-patio-T7", "description": "PERSONA ENTRÓ al área",
        "timestamp": 0, "image_base64": img_b64, "crop_image": img_b64,
        "camera_name": camara, "camera_id": camara, "screenshot_path": ruta,
    }
    carpeta_ok = Path(ruta).parent.name == "capture" if ruta else False
    c1 = bool(ruta) and Path(ruta).is_file() and carpeta_ok
    print(f"{'OK' if c1 else 'FALLO'} C1: foto en capture/ -> {Path(ruta).name if ruta else '—'}")

    # C2: la tarjeta del panel conserva la ruta (la usa para abrir Explorer).
    item = AlertItemWidget(alerta)
    c2 = item._screenshot_path == ruta and os.path.isfile(item._screenshot_path)
    print(f"{'OK' if c2 else 'FALLO'} C2: tarjeta con screenshot_path válido "
          f"(click -> abre Explorador)")

    # Y que el sidebar la acepta y enruta sin romper.
    sidebar = AlertsSidebar()
    sidebar.add_alert(alerta)
    c2b = sidebar.col_personas.alert_count == 1
    print(f"{'OK' if c2b else 'FALLO'} C2b: alerta enrutada a la columna Personas")

    # C3: el guardado es independiente del envío a Jarvis (forwarder apagado).
    from core.network.jarvis_alert_forwarder import JarvisAlertForwarder

    class _Fake:
        session_user = {"n": 1}; selected_establishment = {"name": "L"}
        def __init__(self): self.llamadas = []
        def enviar_novedad_async(self, **kw): self.llamadas.append(kw)

    fake = _Fake()
    fwd = JarvisAlertForwarder(fake)
    fwd.set_activo(False)
    ruta2 = guardar_captura(img_b64, clase="CARRO", camara=camara, evento="salida")
    fwd.on_alert({**alerta, "class_name": "CARRO", "global_id": "VIG-patio-T8"})
    c3 = Path(ruta2).is_file() and len(fake.llamadas) == 0
    print(f"{'OK' if c3 else 'FALLO'} C3: se guarda la foto aunque Jarvis esté "
          f"desactivado (envíos={len(fake.llamadas)})")

    total = len(list(Path(ruta).parent.glob('*.jpg')))
    print(f"   carpeta: {Path(ruta).parent}  ({total} foto(s))")

    ok = all([c1, c2, c2b, c3])
    print("=" * 50)
    print("OK FLUJO CAPTURA SUPERADO" if ok else "FALLO en el flujo")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
