"""
benchmark_demografia.py - Medicion end-to-end del pipeline (Hito 8).

Mide lo que de verdad importa en produccion: cuantos frames por segundo
aguanta el servidor con 1 y con 4 camaras, cuanto tarda cada etapa y
cuanta VRAM se ocupa. Incluye una prueba de fuga de memoria acelerada.

No sustituye a una prueba con camaras reales: aqui los frames se
reproducen desde disco, asi que no hay red ni captura de pantalla. Lo que
mide es el coste del PROCESADO, que es donde estan los modelos.

Uso:
    venv\\Scripts\\python.exe scripts\\benchmark_demografia.py
    venv\\Scripts\\python.exe scripts\\benchmark_demografia.py --frames 200
    venv\\Scripts\\python.exe scripts\\benchmark_demografia.py --fuga 2000
"""

from __future__ import annotations

import argparse
import gc
import glob
import os
import statistics
import sys
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)


def _vram_mb() -> float:
    try:
        import torch
        return torch.cuda.memory_allocated() / 1024 ** 2
    except Exception:  # noqa: BLE001
        return 0.0


def _vram_pico_mb() -> float:
    try:
        import torch
        return torch.cuda.max_memory_allocated() / 1024 ** 2
    except Exception:  # noqa: BLE001
        return 0.0


def _rss_mb() -> float:
    """Memoria RAM del proceso. Sirve para detectar fugas."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 ** 2
    except Exception:  # noqa: BLE001
        return 0.0


def construir_escena(crops: List[np.ndarray], n_personas: int,
                     ancho: int = 960, alto: int = 540) -> np.ndarray:
    """Compone un frame sintetico con N personas pegadas sobre el fondo.

    Se usan crops REALES de las camaras para que el coste de deteccion y
    de demografia sea representativo (una imagen de ruido no dispara los
    mismos caminos de codigo).
    """
    escena = np.full((alto, ancho, 3), 60, np.uint8)
    if not crops:
        return escena
    hueco = ancho // max(1, n_personas)
    for i in range(n_personas):
        crop = crops[i % len(crops)]
        escala = min(hueco / max(1, crop.shape[1]),
                     (alto - 20) / max(1, crop.shape[0]))
        nw = max(8, int(crop.shape[1] * escala))
        nh = max(8, int(crop.shape[0] * escala))
        redim = cv2.resize(crop, (nw, nh))
        x = i * hueco + max(0, (hueco - nw) // 2)
        y = alto - nh - 10
        if x + nw <= ancho and y >= 0:
            escena[y:y + nh, x:x + nw] = redim
    return escena


def medir(procesador: Any, escena: np.ndarray, camara: Any,
          n_frames: int, roi: List[List[int]]) -> Dict[str, Any]:
    """Procesa N frames y devuelve las estadisticas de latencia."""
    tiempos: List[float] = []
    for _ in range(3):                       # calentamiento, fuera de la media
        procesador.process_frame(escena.copy(), roi, True, camera_id=camara)
    for _ in range(n_frames):
        t0 = time.perf_counter()
        procesador.process_frame(escena.copy(), roi, True, camera_id=camara)
        tiempos.append((time.perf_counter() - t0) * 1000.0)
    tiempos.sort()
    return {
        "media_ms": statistics.mean(tiempos),
        "mediana_ms": statistics.median(tiempos),
        "p95_ms": tiempos[int(len(tiempos) * 0.95)],
        "max_ms": tiempos[-1],
        "fps": 1000.0 / max(1e-6, statistics.mean(tiempos)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark del pipeline")
    parser.add_argument("--frames", type=int, default=60,
                        help="frames por escenario")
    parser.add_argument("--personas", type=int, default=3,
                        help="personas por frame")
    parser.add_argument("--fuga", type=int, default=0,
                        help="iteraciones de la prueba de fuga (0 = omitir)")
    args = parser.parse_args()

    print("=" * 76)
    print(" BENCHMARK DEL PIPELINE DEMOGRAFICO")
    print("=" * 76)

    rutas = sorted(glob.glob(os.path.join(_RAIZ, "output", "captures",
                                          "persons", "*.jpg")))[:12]
    crops = [c for c in (cv2.imread(r) for r in rutas) if c is not None]
    if not crops:
        print(" No hay crops en output/captures/persons para componer las")
        print(" escenas. Deja correr el servidor un rato y reintenta.")
        return 1
    print(f" Crops reales usados para componer escenas: {len(crops)}")

    from src.analityc.core.person_amazona_inference import PersonAmazonas
    import torch

    torch.cuda.reset_peak_memory_stats()
    vram_inicio = _vram_mb()
    t0 = time.time()
    procesador = PersonAmazonas(client_id="benchmark")
    carga_s = time.time() - t0
    vram_modelos = _vram_mb() - vram_inicio
    print(f"\n Arranque: {carga_s:.1f} s | VRAM tras cargar modelos: "
          f"{vram_modelos:.0f} MB")

    roi = [[0, 0], [960, 0], [960, 540], [0, 540]]
    escena = construir_escena(crops, args.personas)

    # ── Escenario 1 camara ──
    print("\n" + "-" * 76)
    print(f" ESCENARIO A: 1 camara, {args.personas} personas/frame, "
          f"{args.frames} frames")
    print("-" * 76)
    r1 = medir(procesador, escena, "cam_bench_1", args.frames, roi)
    print(f"   latencia  media={r1['media_ms']:6.1f} ms   "
          f"mediana={r1['mediana_ms']:6.1f} ms   p95={r1['p95_ms']:6.1f} ms")
    print(f"   FPS       {r1['fps']:.1f}")

    # ── Escenario 4 camaras (secuencial, como el ThreadPool del servidor) ──
    print("\n" + "-" * 76)
    print(f" ESCENARIO B: 4 camaras alternando, {args.frames} frames c/u")
    print("-" * 76)
    tiempos: List[float] = []
    camaras = [f"cam_bench_{i}" for i in range(1, 5)]
    for cam in camaras:                       # calentamiento por camara
        procesador.process_frame(escena.copy(), roi, True, camera_id=cam)
    t0 = time.perf_counter()
    for _ in range(args.frames):
        for cam in camaras:
            ti = time.perf_counter()
            procesador.process_frame(escena.copy(), roi, True, camera_id=cam)
            tiempos.append((time.perf_counter() - ti) * 1000.0)
    total_s = time.perf_counter() - t0
    n_total = args.frames * len(camaras)
    tiempos.sort()
    print(f"   latencia  media={statistics.mean(tiempos):6.1f} ms   "
          f"p95={tiempos[int(len(tiempos)*0.95)]:6.1f} ms")
    print(f"   FPS agregado (4 camaras) : {n_total / total_s:.1f}")
    print(f"   FPS por camara           : {n_total / total_s / 4:.1f}")
    print(f"   VRAM pico                : {_vram_pico_mb():.0f} MB")

    # ── Coste por etapa ──
    print("\n" + "-" * 76)
    print(" COSTE POR ETAPA (una escena)")
    print("-" * 76)
    demo = getattr(procesador, "_demographics", None)
    est = getattr(demo, "_estimador_cuerpo", None) if demo else None
    print(f"   Detector de personas : YOLO26 "
          f"{'engine TensorRT' if getattr(procesador, '_detector_is_engine', False) else '.pt PyTorch'}"
          f" @ imgsz {procesador._analytics_config.PERSON_DETECT_IMGSZ}")
    print(f"   Rama corporal        : "
          f"{'MiVOLO v2 (' + est.backend + ')' if est else 'NO DISPONIBLE'}")
    if est is not None:
        crop = crops[0]
        for lote in (1, 4, 8):
            peticiones = [(i, crop, None) for i in range(lote)]
            est.estimar_lote(peticiones)      # calentamiento
            t0 = time.perf_counter()
            for _ in range(5):
                est.estimar_lote(peticiones)
            dt = (time.perf_counter() - t0) / 5
            print(f"     MiVOLO lote={lote:<2} {dt*1000:6.1f} ms "
                  f"({dt*1000/lote:5.1f} ms/persona)")

    # ── Prueba de fuga de memoria ──
    if args.fuga > 0:
        print("\n" + "-" * 76)
        print(f" PRUEBA DE FUGA: {args.fuga} iteraciones")
        print("-" * 76)
        gc.collect()
        rss0, vram0 = _rss_mb(), _vram_mb()
        hitos = max(1, args.fuga // 4)
        for i in range(1, args.fuga + 1):
            # Cada iteracion usa un camera_id/track distinto, que es el
            # patron que hacia crecer la memoria antes (un acumulador por
            # track que nunca se liberaba).
            procesador.process_frame(escena.copy(), roi, True,
                                     camera_id=f"fuga_{i % 8}")
            if i % hitos == 0:
                gc.collect()
                print(f"   iter {i:>6}: RSS {_rss_mb():7.1f} MB "
                      f"(+{_rss_mb()-rss0:6.1f})   "
                      f"VRAM {_vram_mb():7.1f} MB (+{_vram_mb()-vram0:6.1f})")
        gc.collect()
        crec_rss = _rss_mb() - rss0
        crec_vram = _vram_mb() - vram0
        print(f"\n   Crecimiento total: RSS {crec_rss:+.1f} MB, "
              f"VRAM {crec_vram:+.1f} MB")
        # Umbral generoso: cachés y fragmentación hacen que un poco de
        # crecimiento sea normal; lo que se busca es una fuga lineal.
        if crec_rss > 300:
            print("   AVISO: el crecimiento de RSS es alto. Revisar.")
        else:
            print("   Sin indicios de fuga (crecimiento acotado).")

    print("\n" + "=" * 76)
    print(" Nota: los frames vienen de disco, sin red ni captura de pantalla.")
    print(" El FPS real en produccion tambien depende del cliente y la red.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
