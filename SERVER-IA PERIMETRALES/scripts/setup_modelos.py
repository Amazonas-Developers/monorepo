"""
scripts/setup_modelos.py - Verificacion y guia reproducible de modelos.

Comprueba que los modelos del pipeline de demografia esten en
`models/classifiers/` y, para los que faltan, imprime instrucciones
reproducibles para descargarlos/exportarlos.

Modelos:
  ESENCIALES (rama facial + re-id):
    - face_detection_yunet_2023mar.onnx  (detector + 5 keypoints)
    - genderage.onnx                     (InsightFace genero/edad)
    - w600k_r50.onnx                     (ArcFace embeddings, re-id)
  OPCIONALES (rama corporal E3B, sin cara):
    - mivolo.onnx                        (MiVOLO body-only, primario)
    - par_gender.onnx                    (PAR PA-100K/PETA, fallback genero)

Uso:
    python scripts/setup_modelos.py            # solo verifica e informa
    python scripts/setup_modelos.py --strict   # exit!=0 si falta un esencial

NOTA: lanzar con el python del venv para que onnxruntime valide en GPU:
    .\\venv\\Scripts\\python.exe scripts\\setup_modelos.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple

# Raiz del proyecto (scripts/ -> raiz)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODELS_DIR = _PROJECT_ROOT / "models" / "classifiers"


# (filename, etiqueta, esencial, instrucciones_si_falta)
_MODELS: List[Tuple[str, str, bool, str]] = [
    (
        "face_detection_yunet_2023mar.onnx",
        "YuNet (detector facial + keypoints)",
        True,
        "Descargar de: https://github.com/opencv/opencv_zoo/tree/main/models/"
        "face_detection_yunet\n"
        "  y copiar a models/classifiers/face_detection_yunet_2023mar.onnx",
    ),
    (
        "genderage.onnx",
        "InsightFace genero/edad (rama facial primaria)",
        True,
        "Generar con: python scripts/setup_buffalo_l_genderage.py",
    ),
    (
        "w600k_r50.onnx",
        "ArcFace w600k_r50 (embeddings re-identificacion)",
        True,
        "Generar con: python scripts/setup_face_embedding.py",
    ),
    (
        "mivolo.onnx",
        "MiVOLO body-only (rama corporal E3B, primaria)",
        False,
        "Exportar MiVOLO a ONNX (ver instrucciones MiVOLO mas abajo).\n"
        "  Repo: https://github.com/WildChlamydia/MiVOLO\n"
        "  Tambien ayuda: python scripts/setup_mivolo.py",
    ),
    (
        "par_gender.onnx",
        "PAR PA-100K/PETA (rama corporal E3B, fallback solo genero)",
        False,
        "Exportar un modelo PAR de genero a ONNX (ver instrucciones PAR mas "
        "abajo). Solo se usa si MiVOLO body-only no esta disponible.",
    ),
]

_MIVOLO_EXPORT_HELP = """\
--- Exportar MiVOLO a ONNX (rama corporal, primaria) -------------------
MiVOLO oficial usa cara+cuerpo. Para la rama corporal de espaldas/cenital
se necesita un export que acepte SOLO el crop de cuerpo (224x224, RGB,
normalizacion ImageNet). Pasos reproducibles:

  1) git clone https://github.com/WildChlamydia/MiVOLO && cd MiVOLO
  2) Descargar el checkpoint (p.ej. model_imdb_cross_person_4.22_99.46.pth.tar)
  3) pip install torch onnx
  4) Cargar el modelo y exportar el sub-grafo de cuerpo a ONNX con una
     entrada (1,3,224,224). Salida esperada: logits de genero (2) + edad.
  5) Copiar el .onnx resultante a:  models/classifiers/mivolo.onnx

Si tu export entrega edad normalizada (0..1), el pipeline la escala a anos
automaticamente (ver _parse_mivolo_output)."""

_PAR_EXPORT_HELP = """\
--- Exportar PAR a ONNX (fallback de genero corporal) ------------------
Si MiVOLO body-only no es viable, usa un modelo PAR (atributos de persona)
entrenado en PA-100K o PETA, exportado a ONNX SOLO para genero:

  - Entrada: (1,3,224,224) RGB, normalizacion ImageNet
    (mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225]).
  - Salida aceptada por el pipeline (cualquiera de las dos):
      (a) escalar sigmoide P(femenino), o
      (b) 2 logits [masculino, femenino].
  - Copiar a:  models/classifiers/par_gender.onnx

Recursos: PA-100K (https://github.com/dangweili/pedestrian-attribute-recognition-pytorch)
o cualquier checkpoint PAR con la cabeza de 'gender'/'female'."""


def _check_onnx_loads(path: Path) -> str:
    """Intenta abrir el ONNX con onnxruntime y devuelve el provider en uso."""
    try:
        import onnxruntime as ort
        try:
            ort.preload_dlls()  # carga CUDA/cuDNN de torch si estan
        except Exception:
            pass
        providers = ['CPUExecutionProvider']
        if 'CUDAExecutionProvider' in ort.get_available_providers():
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        sess = ort.InferenceSession(str(path), providers=providers)
        return sess.get_providers()[0]
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica modelos del pipeline.")
    ap.add_argument("--strict", action="store_true",
                    help="exit!=0 si falta algun modelo ESENCIAL.")
    ap.add_argument("--check-load", action="store_true",
                    help="ademas, intenta cargar cada ONNX con onnxruntime.")
    args = ap.parse_args()

    print(f"Carpeta de modelos: {_MODELS_DIR}")
    print("=" * 72)

    missing_essential: List[str] = []
    missing_optional: List[str] = []

    for filename, label, essential, _help in _MODELS:
        path = _MODELS_DIR / filename
        exists = path.is_file()
        tag = "ESENCIAL" if essential else "opcional"
        if exists:
            size_mb = path.stat().st_size / (1024 * 1024)
            extra = ""
            if args.check_load:
                extra = f"  [load: {_check_onnx_loads(path)}]"
            print(f"  [OK]    {filename:42s} ({tag}, {size_mb:6.1f} MB){extra}")
        else:
            print(f"  [FALTA] {filename:42s} ({tag}) - {label}")
            (missing_essential if essential else missing_optional).append(
                filename)

    print("=" * 72)

    # Instrucciones para lo que falte
    if missing_essential or missing_optional:
        print("\nComo obtener los modelos que faltan:\n")
        for filename, label, essential, help_text in _MODELS:
            if (_MODELS_DIR / filename).is_file():
                continue
            print(f"- {filename}: {label}")
            for line in help_text.splitlines():
                print(f"    {line}")
            print()

    # Ayudas de export para la rama corporal si falta alguna
    if "mivolo.onnx" in missing_optional:
        print(_MIVOLO_EXPORT_HELP + "\n")
    if "par_gender.onnx" in missing_optional:
        print(_PAR_EXPORT_HELP + "\n")

    # Resumen
    print("Resumen:")
    print(f"  Esenciales faltantes: {missing_essential or 'ninguno'}")
    print(f"  Opcionales faltantes: {missing_optional or 'ninguno'}")
    if missing_optional and not missing_essential:
        print("  -> La rama FACIAL funciona. La rama CORPORAL (E3B) quedara "
              "inactiva hasta tener mivolo.onnx o par_gender.onnx.")

    if args.strict and missing_essential:
        print("\nERROR (strict): faltan modelos esenciales.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
