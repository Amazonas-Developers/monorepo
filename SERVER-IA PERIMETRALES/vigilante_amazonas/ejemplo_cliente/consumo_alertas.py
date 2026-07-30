"""
EJEMPLO de consumo de alertas de VIGILANTE-AMAZONAS para perimetrales-view
(o cualquier otro cliente externo).

Contrato del evento Socket.IO `alerta_persona` (puerto 8091):
    {
      "persona": "Nombre Apellido",
      "persona_id": 3,
      "nivel": "informativo" | "medio" | "critico",
      "camara": "camara_entrada",
      "timestamp": "2026-07-17 14:23:01",
      "score": 0.87,
      "tipo_match": "rostro" | "vestimenta" | "ambos",
      "snapshot_base64": "<JPEG en base64>",
      "snapshot_url": "/snapshots/20260717_142301_Nombre_camara_entrada.jpg",
      "track_id": 42,
      "verificacion": "no_requerida" | "vlm_confirmada" | "sin_vlm"
    }

NOTA: el cliente perimetrales-view YA recibe estas alertas dentro del
websocket del servidor ELDE (metadata.alerts del modo "VigilanteAmazonas"),
que su AlertsSidebar renderiza sin cambios. Este script es para consumidores
adicionales o para depurar el canal Socket.IO.

Uso:
    python consumo_alertas.py [http://127.0.0.1:8091]
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import socketio

URL_SERVIDOR: str = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
CARPETA_SNAPSHOTS: Path = Path(__file__).resolve().parent / "snapshots_recibidos"

cliente = socketio.Client()


@cliente.event
def connect() -> None:
    print(f"✅ conectado a {URL_SERVIDOR}; esperando alertas…")


@cliente.event
def disconnect() -> None:
    print("⚠️ desconectado del servidor de alertas")


@cliente.on("alerta_persona")
def alerta_persona(datos: dict) -> None:
    print(f"\n🚨 ALERTA [{datos['nivel'].upper()}] {datos['timestamp']}")
    print(f"   Persona: {datos['persona']} (score {datos['score']:.2f}, "
          f"match {datos['tipo_match']}, verificación {datos['verificacion']})")
    print(f"   Cámara: {datos['camara']} | track {datos['track_id']}")
    if datos.get("snapshot_base64"):
        CARPETA_SNAPSHOTS.mkdir(exist_ok=True)
        nombre = CARPETA_SNAPSHOTS / f"alerta_{int(time.time() * 1000)}.jpg"
        nombre.write_bytes(base64.b64decode(datos["snapshot_base64"]))
        print(f"   Snapshot guardado en {nombre}")


if __name__ == "__main__":
    cliente.connect(URL_SERVIDOR)
    cliente.wait()
