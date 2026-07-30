"""
Prueba del almacén local de fotos de alertas (capture_store).

  C1: carpeta_capturas() apunta a <perimetrales-view>/capture y la crea.
  C2: guardar_captura() escribe un JPEG real y devuelve su ruta absoluta.
  C3: el archivo se puede releer como imagen (no está corrupto).
  C4: nombres con espacios/acentos ("PERSONA SEGURIDAD") se sanean.
  C5: base64 vacío -> '' (no rompe).

Uso:
    venv\\Scripts\\python.exe test_capture_store.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import cv2
import numpy as np

from core.capture_store import carpeta_capturas, guardar_captura


def _jpg_base64() -> str:
    img = np.full((80, 120, 3), 70, dtype=np.uint8)
    cv2.putText(img, "TEST", (5, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", img)
    return base64.b64encode(buf.tobytes()).decode()


def main() -> int:
    esperada = (Path(__file__).resolve().parent / "capture").resolve()
    carpeta = carpeta_capturas().resolve()
    c1 = carpeta == esperada and carpeta.is_dir()
    print(f"{'OK' if c1 else 'FALLO'} C1: carpeta = {carpeta}")

    ruta = guardar_captura(_jpg_base64(), clase="PERSONA SEGURIDAD",
                           camara="patio norte", evento="llegada")
    c2 = bool(ruta) and Path(ruta).is_file() and Path(ruta).parent.resolve() == esperada
    print(f"{'OK' if c2 else 'FALLO'} C2: archivo guardado -> {Path(ruta).name if ruta else '—'}")

    img = cv2.imread(ruta) if ruta else None
    c3 = img is not None and img.shape[0] == 80 and img.shape[1] == 120
    print(f"{'OK' if c3 else 'FALLO'} C3: JPEG releíble ({img.shape if img is not None else '—'})")

    nombre = Path(ruta).name if ruta else ""
    c4 = " " not in nombre and "PERSONA_SEGURIDAD" in nombre and "patio_norte" in nombre
    print(f"{'OK' if c4 else 'FALLO'} C4: nombre saneado ({nombre})")

    c5 = guardar_captura("", clase="x") == ""
    print(f"{'OK' if c5 else 'FALLO'} C5: base64 vacío -> '' (no rompe)")

    ok = all([c1, c2, c3, c4, c5])
    print("=" * 50)
    print("OK CAPTURE_STORE SUPERADO" if ok else "FALLO en capture_store")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
