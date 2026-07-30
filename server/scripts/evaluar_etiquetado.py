"""
evaluar_etiquetado.py - Mide la precision REAL contra etiquetas manuales.

Lee `output/etiquetado/etiquetas.csv` (rellenado a mano) y compara con lo
que estima el sistema sobre esos mismos crops. Da precision global, matriz
de confusion, precision por nivel de confianza y comparacion entre el modo
solo-cuerpo y el modo dual (cara + cuerpo).

Todas las cifras se acompañan de su INTERVALO DE CONFIANZA (Wilson al
95 %): con ~120 muestras el margen ronda +-8 puntos, y dar un porcentaje
a secas sugeriria una precision que la muestra no respalda.

Uso:
    venv\\Scripts\\python.exe scripts\\evaluar_etiquetado.py
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from collections import Counter
from typing import Dict, List, Optional, Tuple

import cv2

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

_CSV = os.path.join(_RAIZ, "output", "etiquetado", "etiquetas.csv")
_CROPS = os.path.join(_RAIZ, "output", "captures", "persons")

_LETRA_A_GENERO = {"H": "Hombre", "M": "Mujer"}


def wilson(aciertos: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """Intervalo de confianza de Wilson al 95 % para una proporcion.

    Se usa este y no el normal porque con pocas muestras o proporciones
    cercanas a 1 el intervalo normal da limites imposibles (>100 %).
    """
    if total == 0:
        return 0.0, 0.0
    p = aciertos / total
    d = 1 + z * z / total
    centro = (p + z * z / (2 * total)) / d
    margen = (z * math.sqrt(p * (1 - p) / total
                            + z * z / (4 * total * total)) / d)
    return max(0.0, centro - margen), min(1.0, centro + margen)


def pct(aciertos: int, total: int) -> str:
    """'82.4 % (IC95 74.1-88.6, n=120)' o '-' si no hay datos."""
    if total == 0:
        return "sin datos"
    lo, hi = wilson(aciertos, total)
    return (f"{100.0 * aciertos / total:5.1f} %  "
            f"(IC95 {100*lo:4.1f}-{100*hi:4.1f}, n={total})")


def cargar_etiquetas(ruta: str) -> Dict[str, str]:
    """archivo -> letra anotada a mano."""
    if not os.path.isfile(ruta):
        print(f"No existe {ruta}")
        print("Ejecuta antes: venv\\Scripts\\python.exe "
              "scripts\\preparar_etiquetado.py")
        return {}
    etiquetas: Dict[str, str] = {}
    for linea in open(ruta, encoding="utf-8"):
        linea = linea.strip()
        if not linea or linea.startswith("#") or linea.startswith("numero;"):
            continue
        partes = linea.split(";")
        if len(partes) < 3:
            continue
        archivo, letra = partes[1].strip(), partes[2].strip().upper()
        if letra:
            etiquetas[archivo] = letra
    return etiquetas


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evalua el sistema contra etiquetas manuales")
    parser.add_argument("--csv", default=_CSV)
    parser.add_argument("--crops", default=_CROPS)
    args = parser.parse_args()

    etiquetas = cargar_etiquetas(args.csv)
    if not etiquetas:
        return 1

    reparto = Counter(etiquetas.values())
    anotadas = {a: l for a, l in etiquetas.items()
                if l in _LETRA_A_GENERO}
    print("=" * 76)
    print(" EVALUACION CONTRA ETIQUETAS MANUALES")
    print("=" * 76)
    print(f" Filas anotadas          : {len(etiquetas)}")
    print(f"   H (hombre)            : {reparto.get('H', 0)}")
    print(f"   M (mujer)             : {reparto.get('M', 0)}")
    print(f"   ? (no se puede saber) : {reparto.get('?', 0)}"
          "   <- se excluyen del calculo")
    print(f"   X (no es persona)     : {reparto.get('X', 0)}"
          "   <- deberian filtrarse solos")
    if not anotadas:
        print("\n No hay ninguna fila con H o M. Rellena el CSV primero.")
        return 1

    from src.analityc.core.analytics.estimador_edad_genero import (
        EstimadorEdadGenero, crop_es_persona_plausible)
    from src.analityc.core.analytics.demographics import DemographicsClassifier
    from src.analityc.core.person_amazona_inference import _create_yunet

    est = EstimadorEdadGenero()
    if not est.disponible:
        print("\n El estimador no esta disponible (falta el modelo).")
        return 1
    print(f"\n Estimador: MiVOLO v2 ({est.backend}, {est.dispositivo})")
    clf = DemographicsClassifier(yunet=_create_yunet("cuda"))
    clf._cfg._overrides["DEMO_DEBUG_REJECTIONS"] = False

    filas: List[Tuple[str, str, Optional[object], Optional[object]]] = []
    for archivo, letra in sorted(anotadas.items()):
        ruta = os.path.join(args.crops, archivo)
        imagen = cv2.imread(ruta)
        if imagen is None:
            continue
        real = _LETRA_A_GENERO[letra]
        cara, _pose, _w = clf._detect_face_with_pose(imagen)
        m_cuerpo = est.estimar(0, imagen, None)
        m_dual = est.estimar(0, imagen, cara) if cara is not None else None
        filas.append((archivo, real, m_cuerpo, m_dual))

    # ── Precision global (modo cuerpo, el caso normal aqui) ──
    validos = [(r, m) for _a, r, m, _d in filas
               if m is not None and m.es_valida()]
    aciertos = sum(1 for r, m in validos if m.genero == r)
    print("\n" + "-" * 76)
    print(" PRECISION DE GENERO - modo solo cuerpo")
    print("-" * 76)
    print(f"   {pct(aciertos, len(validos))}")

    # Matriz de confusion
    print("\n Matriz de confusion (filas = real, columnas = predicho):")
    print(f"   {'':<10}{'Hombre':>10}{'Mujer':>10}")
    for real in ("Hombre", "Mujer"):
        fila = [sum(1 for r, m in validos
                    if r == real and m.genero == pred)
                for pred in ("Hombre", "Mujer")]
        print(f"   {real:<10}{fila[0]:>10}{fila[1]:>10}")

    # Precision por clase (revela sesgo)
    print("\n Precision por clase real (detecta sesgo del modelo):")
    for real in ("Hombre", "Mujer"):
        sub = [(r, m) for r, m in validos if r == real]
        ok = sum(1 for r, m in sub if m.genero == r)
        print(f"   {real:<8} {pct(ok, len(sub))}")

    # ── Precision por nivel de confianza: ¿la confianza es informativa? ──
    print("\n Precision por nivel de confianza declarado:")
    for lo, hi in ((0.5, 0.7), (0.7, 0.9), (0.9, 0.99), (0.99, 1.01)):
        sub = [(r, m) for r, m in validos if lo <= m.conf_genero < hi]
        ok = sum(1 for r, m in sub if m.genero == r)
        print(f"   conf {lo:.2f}-{hi:.2f}: {pct(ok, len(sub))}")

    # ── Cuerpo vs dual, solo donde hay rostro ──
    con_rostro = [(r, c, d) for _a, r, c, d in filas if d is not None
                  and c is not None and c.es_valida() and d.es_valida()]
    if con_rostro:
        ok_c = sum(1 for r, c, _d in con_rostro if c.genero == r)
        ok_d = sum(1 for r, _c, d in con_rostro if d.genero == r)
        print("\n" + "-" * 76)
        print(" ¿APORTA LA ENTRADA DUAL? (solo crops con rostro detectado)")
        print("-" * 76)
        print(f"   solo cuerpo : {pct(ok_c, len(con_rostro))}")
        print(f"   cara+cuerpo : {pct(ok_d, len(con_rostro))}")
        if ok_d > ok_c:
            print("   -> la entrada dual acierta mas")
        elif ok_d < ok_c:
            print("   -> OJO: la entrada dual acierta MENOS; revisar la cascada")
        else:
            print("   -> empate: la muestra no basta para decidir")

    # ── Filtro de crops ──
    descartados = [a for a, _r, m, _d in filas
                   if m is not None and not m.es_valida()]
    if descartados:
        print(f"\n Crops descartados por el filtro: {len(descartados)}")
    equis = [a for a, l in etiquetas.items() if l == "X"]
    if equis:
        bien_filtrados = sum(
            1 for a in equis
            if not crop_es_persona_plausible(
                cv2.imread(os.path.join(args.crops, a)))[0])
        print(f" De los {len(equis)} marcados como 'no es persona', el filtro "
              f"descarta {bien_filtrados}")

    print("\n" + "=" * 76)
    print(" Recuerda: los intervalos son anchos porque la muestra es pequena.")
    print(" Para estrechar el margen hacen falta mas crops etiquetados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
