"""
Prueba E2E REAL: persona registrada en el dashboard -> alerta CON SU NOMBRE.

Habla con el servidor que ya está corriendo (no carga modelos aquí, así que
no duplica VRAM). Flujo idéntico al del cliente:

  1. Registra "Juan Perez Prueba" por la API del dashboard y le sube una foto
     de rostro (queda en la galería de vigilancia).
  2. Se conecta al websocket del servidor en modo VigilanteAmazonas y le
     envía un frame con ESA persona (msgpack, igual que socket_client).
  3. Verifica que la alerta devuelta trae class_name = el NOMBRE asignado.
  4. Pasa esa alerta por el reenviador y comprueba que el título que llegaría
     a Jarvis lleva también el nombre.
  5. Limpia la persona de prueba.

Uso:
    venv\\Scripts\\python.exe test_alerta_con_nombre.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "src"))

import msgpack
import requests
from websocket import create_connection      # websocket-client

from core.dashboard_url import url_dashboard

NOMBRE = "Juan Perez Prueba"
# Foto de ejemplo: la que trae ultralytics dentro del venv del servidor.
# Era una ruta absoluta a esta maquina y dejo de existir al mover el servidor
# a `server/` el 30-jul-2026. Se deduce desde la raiz del proyecto, que es la
# abuela de este archivo (`clients/perimetrales/test_...`).
_RAIZ = Path(__file__).resolve().parents[2]
FOTO = (_RAIZ / "server" / "venv" / "Lib" / "site-packages" / "ultralytics"
        / "assets" / "zidane.jpg")


def main() -> int:
    base = url_dashboard()
    ws_url = (os.getenv("server_ws_url")
              or "ws://127.0.0.1:9000/ws") + "/VigilanteAmazonas"
    print(f"dashboard: {base}\nwebsocket: {ws_url}\n")

    # -- 1) Registrar la persona y subir su foto -------------------------
    pid = requests.post(f"{base}/api/personas", json={
        "nombre": NOMBRE, "descripcion": "prueba e2e alerta con nombre",
        "nivel": "critico"}, timeout=30).json()["id"]
    with open(FOTO, "rb") as f:
        r = requests.post(f"{base}/api/personas/{pid}/fotos/rostro",
                          files={"archivo": ("cara.jpg", f, "image/jpeg")},
                          timeout=300)
    c1 = r.status_code == 201
    print(f"{'OK' if c1 else 'FALLO'} C1: '{NOMBRE}' registrado con foto "
          f"({r.status_code}) -> {r.json().get('mensaje', r.text)[:80]}")

    # -- 2) Enviar frames de esa persona por el websocket ----------------
    jpeg = FOTO.read_bytes()
    alerta = None
    detecciones = 0
    try:
        ws = create_connection(ws_url, timeout=90)
        init = json.loads(ws.recv())          # el servidor manda id_connection
        id_conn = init.get("id_connection") or init.get("data", {}).get("id_connection")
        print(f"   conectado (id_connection={id_conn})")

        for i in range(14):                   # varios frames: throttle de Re-ID
            ws.send_binary(msgpack.packb({
                "event": "inference",
                "id_connection": id_conn,
                "type_inference": "VigilanteAmazonas",
                "component_key": "cam-prueba",
                "data": {
                    "image": jpeg,
                    "camera_id": "cam-prueba",
                    "camera_name": "patio prueba",
                    "draw_server": False,
                    "roi_activate": False,
                    "roi_coordinates": [],
                },
            }))
            msg = ws.recv()
            if isinstance(msg, bytes):
                msg = msgpack.unpackb(msg, raw=False, strict_map_key=False)
            else:
                msg = json.loads(msg)
            meta = (msg.get("data") or {}).get("metadata") or {}
            detecciones = max(detecciones, len(meta.get("detections") or []))
            for t in meta.get("alerts") or []:
                if t.get("class_name") == NOMBRE:
                    alerta = t
            if alerta:
                break
        ws.close()
    except Exception as e:
        print(f"   error websocket: {e}")

    c2 = detecciones > 0
    print(f"{'OK' if c2 else 'FALLO'} C2: el servidor detecta ({detecciones} detección/es)")

    c3 = alerta is not None and alerta.get("class_name") == NOMBRE
    print(f"{'OK' if c3 else 'FALLO'} C3: alerta con el NOMBRE asignado -> "
          f"{alerta.get('class_name') if alerta else 'SIN ALERTA'} | "
          f"{alerta.get('description', '')[:70] if alerta else ''}")

    # -- 3) El reenviador la mandaría a Jarvis con el nombre -------------
    c4 = False
    if alerta:
        from core.network.jarvis_alert_forwarder import JarvisAlertForwarder

        class _Fake:
            session_user = {"n": 1}; selected_establishment = {"name": "L"}
            def __init__(self): self.titulos = []
            def enviar_novedad_async(self, base64_image='', title='', message=''):
                self.titulos.append(title)

        fake = _Fake()
        JarvisAlertForwarder(fake).on_alert(alerta)
        c4 = bool(fake.titulos) and NOMBRE in fake.titulos[0]
        print(f"{'OK' if c4 else 'FALLO'} C4: Jarvis recibiría -> "
              f"'{fake.titulos[0] if fake.titulos else 'NADA'}'")

    # -- 4) Limpiar ------------------------------------------------------
    requests.delete(f"{base}/api/personas/{pid}", timeout=30)
    print("   persona de prueba eliminada")

    ok = all([c1, c2, c3, c4])
    print("=" * 50)
    print("OK ALERTA CON NOMBRE SUPERADA" if ok else "FALLO en la alerta con nombre")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
