"""
preparar_etiquetado.py - Prepara el set de validacion con datos REALES.

Genera hojas de contacto NUMERADAS con los crops de nuestras camaras y una
plantilla CSV para anotarlos a mano. Es el unico modo de saber la precision
real del sistema: un dataset de retratos de estudio mide otro problema
(rostros de 385 px frontales) y no el nuestro (cuerpos, 29 px de rostro
cuando lo hay, 79 % de espaldas).

Uso:
    venv\\Scripts\\python.exe scripts\\preparar_etiquetado.py

Deja en output/etiquetado/:
    hoja_01.jpg, hoja_02.jpg, ...   crops numerados para mirar
    etiquetas.csv                   plantilla a rellenar

Como etiquetar: abre las hojas, y en `etiquetas.csv` escribe en la columna
`genero` una de estas letras:
    H = hombre
    M = mujer
    ? = no se puede saber ni mirandolo (cuenta aparte, NO como fallo)
    X = el recorte no es una persona (pared, puerta, fragmento)

Lo importante es anotar lo que se ve, sin mirar lo que dijo el sistema.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List, Tuple

import cv2
import numpy as np

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

_SALIDA = os.path.join(_RAIZ, "output", "etiquetado")
_ANCHO_CELDA = 190
_ALTO_CELDA = 330
_COLUMNAS = 8
_FILAS = 5                      # 40 crops por hoja


def dibujar_celda(imagen: np.ndarray, numero: int) -> np.ndarray:
    """Miniatura del crop con su numero bien visible."""
    alto_img = _ALTO_CELDA - 30
    escala = min(_ANCHO_CELDA / imagen.shape[1], alto_img / imagen.shape[0])
    nw = max(1, int(imagen.shape[1] * escala))
    nh = max(1, int(imagen.shape[0] * escala))
    mini = cv2.resize(imagen, (nw, nh), interpolation=cv2.INTER_AREA)

    celda = np.full((_ALTO_CELDA, _ANCHO_CELDA, 3), 22, np.uint8)
    x0 = (_ANCHO_CELDA - nw) // 2
    celda[0:nh, x0:x0 + nw] = mini
    # Numero grande, con reborde para que se lea sobre cualquier fondo.
    texto = str(numero)
    for grosor, color in ((5, (0, 0, 0)), (2, (60, 235, 255))):
        cv2.putText(celda, texto, (6, _ALTO_CELDA - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, color, grosor, cv2.LINE_AA)
    return celda


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepara hojas numeradas y plantilla de etiquetado")
    parser.add_argument("--carpeta",
                        default=os.path.join(_RAIZ, "output", "captures",
                                             "persons"),
                        help="carpeta con los crops reales")
    parser.add_argument("--salida", default=_SALIDA)
    parser.add_argument("--limite", type=int, default=0)
    args = parser.parse_args()

    rutas = sorted(glob.glob(os.path.join(args.carpeta, "*.jpg")))
    if args.limite > 0:
        rutas = rutas[:args.limite]
    if not rutas:
        print(f"No hay crops en {args.carpeta}")
        return 1

    os.makedirs(args.salida, exist_ok=True)
    print(f"Crops a etiquetar: {len(rutas)}")

    filas_csv: List[Tuple[int, str]] = []
    celdas: List[np.ndarray] = []
    hoja_num = 1
    por_hoja = _COLUMNAS * _FILAS

    def volcar_hoja(celdas: List[np.ndarray], numero: int) -> None:
        if not celdas:
            return
        filas = (len(celdas) + _COLUMNAS - 1) // _COLUMNAS
        hoja = np.full((filas * _ALTO_CELDA, _COLUMNAS * _ANCHO_CELDA, 3),
                       14, np.uint8)
        for i, celda in enumerate(celdas):
            f, c = divmod(i, _COLUMNAS)
            hoja[f * _ALTO_CELDA:(f + 1) * _ALTO_CELDA,
                 c * _ANCHO_CELDA:(c + 1) * _ANCHO_CELDA] = celda
        ruta = os.path.join(args.salida, f"hoja_{numero:02d}.jpg")
        cv2.imwrite(ruta, hoja, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"  {ruta}  ({len(celdas)} crops)")

    for indice, ruta in enumerate(rutas, start=1):
        imagen = cv2.imread(ruta)
        if imagen is None:
            continue
        celdas.append(dibujar_celda(imagen, indice))
        filas_csv.append((indice, os.path.basename(ruta)))
        if len(celdas) >= por_hoja:
            volcar_hoja(celdas, hoja_num)
            celdas, hoja_num = [], hoja_num + 1
    volcar_hoja(celdas, hoja_num)

    csv = os.path.join(args.salida, "etiquetas.csv")
    if os.path.isfile(csv):
        print(f"\n  OJO: ya existe {csv}. No se sobrescribe para no perder")
        print("  el trabajo hecho. Borralo a mano si quieres empezar de cero.")
    else:
        with open(csv, "w", encoding="utf-8") as fichero:
            fichero.write("# Escribe en la columna 'genero': "
                          "H=hombre  M=mujer  ?=no se sabe  X=no es persona\n")
            fichero.write("numero;archivo;genero\n")
            for numero, nombre in filas_csv:
                fichero.write(f"{numero};{nombre};\n")
        print(f"\n  Plantilla: {csv}")

    print(f"\nListo. Abre las hojas de {args.salida}, rellena la columna")
    print("'genero' del CSV y luego ejecuta:")
    print("    venv\\Scripts\\python.exe scripts\\evaluar_etiquetado.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
