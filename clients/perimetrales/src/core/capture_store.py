"""
Almacén local de las fotos de las alertas (screenshots).

Cada alerta que llega del servidor trae su imagen en base64; aquí se guarda a
disco para poder abrirla luego desde el panel lateral (click en la tarjeta ->
Explorador con el archivo seleccionado) y para que el dashboard :5333 pueda
mostrarlas.

Ruta por defecto: <perimetrales-view>/screenshots  (junto al proyecto, robusto
ante mover la carpeta). Se puede sobrescribir con `screenshots_dir` en el .env.

Junto a cada JPEG se escribe un JSON con los mismos datos. El nombre del
archivo NO es fuente fiable: el saneado para Windows convierte "CAMIÓN" en
"CAMI_N" y "PERSONA SEGURIDAD" en "PERSONA_SEGURIDAD", así que reconstruir la
clase a partir del nombre daba resultados equivocados. El sidecar conserva los
valores tal cual llegaron.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict

# Raíz del proyecto: este módulo está en <proyecto>/src/core/capture_store.py
_RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

# Clases que el servidor agrupa como vehículo. El resto es persona.
_CLASES_VEHICULO = {"MOTO", "CARRO", "CAMIONETA", "CAMION", "CAMIÓN", "BUS",
                    "VEHICULO", "VEHÍCULO"}


def carpeta_capturas() -> Path:
    """Carpeta donde se guardan las fotos (crea si no existe)."""
    ruta = (os.getenv("screenshots_dir") or os.getenv("capture_dir")
            or str(_RAIZ_PROYECTO / "screenshots"))
    p = Path(ruta)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sanear(texto: str) -> str:
    """Deja solo caracteres válidos para un nombre de archivo en Windows."""
    limpio = re.sub(r"[^A-Za-z0-9._-]+", "_", (texto or "").strip())
    return limpio.strip("_") or "x"


def clase_gruesa(clase: str, gruesa: str = "") -> str:
    """'persona' o 'vehiculo'. Prefiere lo que diga el servidor."""
    gruesa = (gruesa or "").strip().lower()
    if gruesa in ("persona", "vehiculo"):
        return gruesa
    primera = (clase or "").strip().upper().split(" ")[0]
    return "vehiculo" if primera in _CLASES_VEHICULO else "persona"


def guardar_captura(image_base64: str, clase: str = "", camara: str = "",
                    evento: str = "", gruesa: str = "",
                    extra: Dict[str, Any] | None = None) -> str:
    """Decodifica el base64 y lo guarda como JPEG + sidecar JSON.

    Devuelve la RUTA ABSOLUTA del JPEG, o '' si no había imagen o falló el
    guardado (nunca lanza: una alerta no debe romperse por esto).
    """
    if not image_base64:
        return ""
    try:
        b64 = re.sub(r"^data:image/\w+;base64,", "", image_base64)
        crudo = base64.b64decode(b64)
        # Nombre único: fecha-hora-milis + evento + clase + cámara.
        ahora = time.localtime()
        milis = int((time.time() % 1) * 1000)
        partes = [time.strftime("%Y%m%d_%H%M%S", ahora), f"{milis:03d}"]
        for texto in (evento, clase, camara):
            if texto:
                partes.append(_sanear(str(texto)))
        base = "_".join(partes)
        carpeta = carpeta_capturas()
        destino = carpeta / f"{base}.jpg"
        with open(destino, "wb") as f:
            f.write(crudo)

        meta: Dict[str, Any] = {
            "archivo": f"{base}.jpg",
            "clase": (clase or "").strip(),
            "clase_gruesa": clase_gruesa(clase, gruesa),
            "evento": (evento or "").strip(),
            "camara": (camara or "").strip(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", ahora),
            "epoch": time.time(),
        }
        if extra:
            meta.update({k: v for k, v in extra.items() if k not in meta})
        # Escritura atómica: un JSON a medias haría fallar al dashboard.
        temporal = carpeta / f"{base}.json.tmp"
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(temporal, carpeta / f"{base}.json")
        return str(destino)
    except Exception as e:
        print(f"[screenshots] no se pudo guardar la foto de la alerta: {e}")
        return ""


def leer_meta(jpg: str | Path) -> Dict[str, Any]:
    """Metadatos de una captura. Diccionario vacío si no hay sidecar."""
    ruta = Path(jpg)
    sidecar = ruta.with_suffix(".json")
    try:
        with open(sidecar, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}
