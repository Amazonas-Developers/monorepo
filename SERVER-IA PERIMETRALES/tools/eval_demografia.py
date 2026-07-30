"""
tools/eval_demografia.py - Evaluacion de exactitud de la demografia.

Recibe un CSV con clips etiquetados y produce:
  - Matriz de confusion de GENERO (Hombre/Mujer/Desconocido).
  - Accuracy de EDAD a +/- 1 rango (solo sobre tracks clasificados con CARA).
  - Desglose por RAMA: facial (E3A) vs corporal (E3B).

Formato del CSV (con cabecera):
    archivo,genero,rango_edad
    clip_001.mp4,Hombre,26-35
    clip_002.mp4,Mujer,18-25
    ...
Las rutas de 'archivo' son relativas a la carpeta del CSV (o absolutas).
'genero' admite Hombre/Mujer (o H/M, hombre/mujer). 'rango_edad' usa la
taxonomia canonica del sistema (0-12,13-17,18-25,26-35,36-50,51-65,65+).

Uso (con el python del venv para GPU):
    .\\venv\\Scripts\\python.exe tools\\eval_demografia.py --csv clips/labels.csv
    ... --max-frames 150   (frames maximos a procesar por clip antes de rendirse)

NOTA: requiere los modelos faciales. Para evaluar la rama corporal,
ademas mivolo.onnx o par_gender.onnx (ver scripts/setup_modelos.py).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

# Taxonomia canonica ordenada (la que produce el sistema)
_AGE_ORDER = ["0-12", "13-17", "18-25", "26-35", "36-50", "51-65", "65+"]
# Mapeo de la taxonomia gruesa corporal a un indice aproximado del orden
# canonico (solo para display; la edad NO se evalua en rama corporal).
_COARSE_TO_CANON = {"Nino": "0-12", "Joven": "18-25",
                    "Adulto": "36-50", "Mayor": "65+"}


def _norm_gender(g: str) -> str:
    g = (g or "").strip().lower()
    if g in ("hombre", "h", "m", "male", "masculino"):
        return "Hombre"
    if g in ("mujer", "f", "female", "femenino"):
        return "Mujer"
    return "Desconocido"


def _age_within_one(pred: str, gt: str) -> Optional[bool]:
    """True/False si pred esta a +/-1 rango de gt. None si no evaluable."""
    if pred not in _AGE_ORDER or gt not in _AGE_ORDER:
        return None
    return abs(_AGE_ORDER.index(pred) - _AGE_ORDER.index(gt)) <= 1


def _largest_person_box(proc, frame) -> Optional[tuple]:
    """Detecta personas y devuelve el bbox mas grande, o None."""
    res = proc.person_model.predict(frame, imgsz=640, conf=0.30,
                                    classes=[0], verbose=False, max_det=20)
    if not res or res[0].boxes is None or len(res[0].boxes) == 0:
        return None
    boxes = res[0].boxes.xyxy.cpu().numpy()
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return tuple(boxes[int(np.argmax(areas))])


def _classify_clip(proc, path: str, max_frames: int
                   ) -> Optional[Dict[str, str]]:
    """Procesa un clip hasta que la demografia commitea (o se agota).

    Asume UNA persona etiquetada por clip: usa un track_id fijo para que
    el acumulador converja. Devuelve el resultado committeado o None.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"  [!] no se pudo abrir: {path}")
        return None
    proc._demographics.reset()
    track_id = 1
    last = None
    frames = 0
    try:
        while frames < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            frames += 1
            box = _largest_person_box(proc, frame)
            if box is None:
                continue
            res = proc._demographics.classify(frame, box, track_id)
            last = res
            if res.get("gender") not in (None, "Desconocido") and \
                    proc._demographics._accum[track_id].committed:
                return res
    finally:
        cap.release()
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluacion de demografia.")
    ap.add_argument("--csv", required=True,
                    help="CSV con columnas archivo,genero,rango_edad.")
    ap.add_argument("--max-frames", type=int, default=150)
    args = ap.parse_args()

    csv_dir = os.path.dirname(os.path.abspath(args.csv))
    rows: List[Dict[str, str]] = []
    with open(args.csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        print("CSV vacio o sin filas.", file=sys.stderr)
        return 1
    print(f"Clips a evaluar: {len(rows)}")

    from analityc.core.person_amazona_inference import PersonAmazonas
    print("Cargando modelos...")
    proc = PersonAmazonas()
    body_active = (getattr(proc, "_par_session", None) is not None
                   or getattr(proc, "_mivolo_session", None) is not None)
    print(f"Rama corporal activa: {body_active}")

    genders = ["Hombre", "Mujer", "Desconocido"]
    confusion = defaultdict(lambda: defaultdict(int))  # gt -> pred -> n
    age_hits = age_total = 0
    branch = defaultdict(lambda: {"n": 0, "gender_ok": 0})  # face/body/reid

    for r in rows:
        archivo = r.get("archivo") or r.get("file") or ""
        path = archivo if os.path.isabs(archivo) else os.path.join(
            csv_dir, archivo)
        gt_gender = _norm_gender(r.get("genero") or r.get("gender") or "")
        gt_age = (r.get("rango_edad") or r.get("age_range") or "").strip()

        res = _classify_clip(proc, path, args.max_frames)
        pred_gender = _norm_gender(res.get("gender")) if res else "Desconocido"
        pred_age = (res.get("age_range") if res else "") or ""
        source = (res.get("age_source") if res else "none") or "none"

        confusion[gt_gender][pred_gender] += 1
        branch[source]["n"] += 1
        if pred_gender == gt_gender and gt_gender != "Desconocido":
            branch[source]["gender_ok"] += 1

        # Edad +/-1 SOLO en tracks con cara (rama facial)
        if source == "face":
            w = _age_within_one(pred_age, gt_age)
            if w is not None:
                age_total += 1
                age_hits += 1 if w else 0

        print(f"  {os.path.basename(path):28s} GT={gt_gender}/{gt_age:6s} "
              f"-> {pred_gender}/{pred_age:8s} [{source}]")

    # ── Matriz de confusion de genero ──
    print("\n──── Matriz de confusion GENERO (filas=GT, cols=pred) ────")
    header = "  GT\\pred     " + "".join(f"{g:>12s}" for g in genders)
    print(header)
    correct = total = 0
    for gt in genders:
        line = f"  {gt:12s}"
        for pred in genders:
            n = confusion[gt][pred]
            line += f"{n:>12d}"
            total += n
            if gt == pred and gt != "Desconocido":
                correct += n
        print(line)
    gender_acc = (correct / total * 100.0) if total else 0.0
    print(f"  Accuracy genero (excluye GT Desconocido): {gender_acc:.1f}%")

    # ── Edad +/-1 ──
    print("\n──── EDAD +/- 1 rango (solo rama facial) ────")
    if age_total:
        print(f"  aciertos: {age_hits}/{age_total} = "
              f"{age_hits / age_total * 100.0:.1f}%  (objetivo >= 80%)")
    else:
        print("  (sin tracks faciales evaluables)")

    # ── Desglose por rama ──
    print("\n──── Desglose por RAMA ────")
    for src in ("face", "body", "reid", "none"):
        b = branch.get(src)
        if not b or b["n"] == 0:
            continue
        acc = b["gender_ok"] / b["n"] * 100.0
        print(f"  {src:6s}: {b['n']:3d} clips | acc genero {acc:.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
