"""
Genera el icono de persona del botón "Galería de personas" (resource/person.png).

Silueta blanca sobre fondo transparente, al estilo de los demás iconos del pie.
Se ejecuta una sola vez; el PNG queda versionado en resource/.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

DESTINO = Path(__file__).resolve().parent / "resource" / "person.png"
TAM = 128            # se renderiza grande y Qt lo escala al tamaño del botón
BLANCO = (255, 255, 255, 255)


def main() -> int:
    img = Image.new("RGBA", (TAM, TAM), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Cabeza: círculo centrado en el tercio superior.
    r_cabeza = TAM * 0.20
    cx, cy = TAM / 2, TAM * 0.30
    d.ellipse([cx - r_cabeza, cy - r_cabeza, cx + r_cabeza, cy + r_cabeza],
              fill=BLANCO)

    # Hombros/torso: media elipse ancha en la parte inferior.
    ancho = TAM * 0.68
    alto = TAM * 0.52
    x0, y0 = cx - ancho / 2, TAM * 0.56
    d.pieslice([x0, y0, x0 + ancho, y0 + alto * 2], start=180, end=360,
               fill=BLANCO)

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    img.save(DESTINO)
    print(f"icono generado: {DESTINO} ({img.size[0]}x{img.size[1]} RGBA)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
