"""
comparar_vlm.py - Compara los modelos VLM disponibles (3B vs 7B).

Pasa las MISMAS fotos por cada modelo y mide lo que importa para decidir
cuál dejar activo: cuántas resuelve, cuánto tarda, cuánta VRAM ocupa y
en cuántas coincide con la demografía que ya tiene el sistema.

No decide por ti: imprime la tabla para que la decisión sea con datos.
El modelo activo se fija en `output/vlm_model.txt`.

Uso:
    venv\\Scripts\\python.exe scripts\\comparar_vlm.py
    venv\\Scripts\\python.exe scripts\\comparar_vlm.py --n 20
"""

from __future__ import annotations

import argparse
import gc
import glob
import json
import os
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import cv2

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)


def _vram_mb() -> float:
    try:
        import torch
        return torch.cuda.memory_allocated() / 1024 ** 2
    except Exception:  # noqa: BLE001
        return 0.0


def cargar_muestras(n: int) -> List[Tuple[str, Dict[str, Any]]]:
    """Fotos con demografía YA establecida, para poder contrastar.

    Se usan capturas resueltas porque son el único contraste disponible
    sin etiquetado manual. OJO: coincidir con el sistema no prueba que el
    VLM acierte; ambos podrían equivocarse igual. Mide ACUERDO, no
    precisión.
    """
    muestras: List[Tuple[str, Dict[str, Any]]] = []
    for sidecar in sorted(glob.glob(os.path.join(
            _RAIZ, "output", "captures", "persons", "*.json")), reverse=True):
        try:
            with open(sidecar, encoding="utf-8") as fichero:
                meta = json.load(fichero) or {}
        except (OSError, json.JSONDecodeError):
            continue
        if not meta.get("gender"):
            continue
        jpg = sidecar[:-5] + ".jpg"
        if os.path.isfile(jpg):
            muestras.append((jpg, meta))
        if len(muestras) >= n:
            break
    return muestras


def probar(clave: str, muestras: List[Tuple[str, Dict[str, Any]]]
           ) -> Optional[Dict[str, Any]]:
    """Corre un modelo sobre las muestras y devuelve sus métricas."""
    from src.analityc.core.analytics.verificador_vlm import _PREGUNTA, _parsear
    from src.analityc.core import multimodal_router as mr

    # Instancia nueva por modelo: asi la VRAM medida es solo la suya.
    mr._router = None
    router = mr.MultimodalRouter(device=0, vqa_model=clave)

    vram0 = _vram_mb()
    t_carga = time.time()
    tiempos: List[float] = []
    acuerdos = 0
    resueltas = 0
    generos: Counter = Counter()
    primera = True

    for jpg, meta in muestras:
        imagen = cv2.imread(jpg)
        if imagen is None:
            continue
        t0 = time.time()
        try:
            respuesta = router.vqa(imagen, _PREGUNTA, max_new_tokens=80)
        except Exception as exc:  # noqa: BLE001
            print(f"   fallo en {os.path.basename(jpg)}: {exc}")
            continue
        dt = time.time() - t0
        if primera:
            # La primera incluye la carga del modelo: no falsea la media.
            carga = time.time() - t_carga
            primera = False
        else:
            tiempos.append(dt)
        resultado = _parsear(respuesta, 0)
        if resultado is not None and resultado.genero:
            resueltas += 1
            generos[resultado.genero] += 1
            if resultado.genero == meta.get("gender"):
                acuerdos += 1

    vram = _vram_mb() - vram0
    del router
    mr._router = None
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass

    total = len(muestras)
    return {
        "clave": clave,
        "carga_s": round(carga, 1) if not primera else 0.0,
        "vram_mb": round(vram),
        "resueltas": resueltas,
        "total": total,
        "acuerdos": acuerdos,
        "seg_por_foto": round(sum(tiempos) / len(tiempos), 2) if tiempos else 0,
        "generos": dict(generos),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara los VLM disponibles")
    parser.add_argument("--n", type=int, default=15,
                        help="fotos a evaluar por modelo")
    parser.add_argument("--modelos", default="3b,7b",
                        help="claves a comparar, separadas por coma")
    args = parser.parse_args()

    muestras = cargar_muestras(args.n)
    if not muestras:
        print("No hay capturas con demografia para contrastar.")
        return 1
    print("=" * 74)
    print(" COMPARATIVA DE MODELOS VLM")
    print("=" * 74)
    print(f" Fotos evaluadas por modelo: {len(muestras)}")

    resultados = []
    for clave in [c.strip() for c in args.modelos.split(",") if c.strip()]:
        print(f"\n Probando '{clave}' (la primera foto incluye la carga)…")
        try:
            resultado = probar(clave, muestras)
        except Exception as exc:  # noqa: BLE001
            print(f"   no se pudo probar '{clave}': "
                  f"{type(exc).__name__}: {str(exc)[:160]}")
            continue
        if resultado:
            resultados.append(resultado)
            print(f"   carga {resultado['carga_s']}s · "
                  f"{resultado['seg_por_foto']}s/foto · "
                  f"{resultado['vram_mb']} MB")

    if not resultados:
        return 1

    print("\n" + "-" * 74)
    print(f" {'MODELO':<8}{'RESUELVE':>12}{'ACUERDO':>12}"
          f"{'s/FOTO':>10}{'VRAM':>10}{'CARGA':>10}")
    print("-" * 74)
    for r in resultados:
        pct_res = 100.0 * r["resueltas"] / max(1, r["total"])
        pct_acu = 100.0 * r["acuerdos"] / max(1, r["resueltas"])
        print(f" {r['clave']:<8}{r['resueltas']:>5}/{r['total']:<3}"
              f"{pct_res:>4.0f}%{pct_acu:>9.0f}%"
              f"{r['seg_por_foto']:>10.2f}{r['vram_mb']:>9} MB"
              f"{r['carga_s']:>9.0f}s")
    print("-" * 74)
    print(" RESUELVE = emite un genero (no responde 'desconocido')")
    print(" ACUERDO  = coincide con lo que ya tenia el sistema.")
    print("            NO es precision: si ambos fallan igual, cuenta como")
    print("            acuerdo. Para precision real hace falta etiquetado.")

    activo = "output/vlm_model.txt"
    ruta = os.path.join(_RAIZ, activo)
    actual = ""
    if os.path.isfile(ruta):
        with open(ruta, encoding="utf-8") as fichero:
            actual = fichero.read().strip()
    print(f"\n Modelo activo ahora: '{actual or '(por defecto)'}'")
    print(f" Para cambiarlo:  echo 7b > {activo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
