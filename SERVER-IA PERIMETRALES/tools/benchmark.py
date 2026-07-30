"""
tools/benchmark.py - Benchmark de rendimiento del pipeline de demografia.

Mide, sobre el hardware real:
  - FPS end-to-end de process_frame.
  - Latencia p50/p95 por ETAPA: deteccion (YOLO+track), demografia
    (clasificador genero/edad), y end-to-end.
  - VRAM usada (via pynvml si esta, si no nvidia-smi).

Uso (lanzar con el python del venv para GPU):
    .\\venv\\Scripts\\python.exe tools\\benchmark.py --frames 120
    .\\venv\\Scripts\\python.exe tools\\benchmark.py --source video.mp4 --frames 200
    .\\venv\\Scripts\\python.exe tools\\benchmark.py --source 0

Sin --source usa frames sinteticos (mide compute, no I/O de camara).
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from typing import List, Optional

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))


def _vram_used_mb() -> Optional[float]:
    """VRAM usada en MB (pynvml -> nvidia-smi -> None)."""
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(h)
        return info.used / (1024 * 1024)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            encoding="utf-8", timeout=5)
        return float(out.strip().splitlines()[0])
    except Exception:
        return None


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[k]


def _report(name: str, lat_ms: List[float]) -> None:
    if not lat_ms:
        print(f"  {name:14s}: (sin datos)")
        return
    p50 = _pct(lat_ms, 50)
    p95 = _pct(lat_ms, 95)
    mean = statistics.fmean(lat_ms)
    fps = 1000.0 / mean if mean > 0 else 0.0
    print(f"  {name:14s}: p50={p50:7.1f}ms  p95={p95:7.1f}ms  "
          f"media={mean:7.1f}ms  ({fps:6.1f} FPS)")


def _frames_from_source(source, n: int, w: int, h: int):
    """Genera n frames: de la fuente (CameraSource) o sinteticos."""
    if source is None:
        rng = np.random.default_rng(0)
        for _ in range(n):
            yield rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        return
    from analityc.core.capture import CameraSource
    src = source
    if isinstance(source, str) and source.isdigit():
        src = int(source)
    cam = CameraSource(src, loop_file=True).start()
    got = 0
    deadline = time.monotonic() + 60
    while got < n and time.monotonic() < deadline:
        f = cam.read()
        if f is None:
            time.sleep(0.003)
            continue
        got += 1
        yield f
    cam.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark del pipeline.")
    ap.add_argument("--source", default=None,
                    help="0/rtsp/archivo. Sin esto: frames sinteticos.")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--warmup", type=int, default=10)
    args = ap.parse_args()

    from analityc.core.person_amazona_inference import PersonAmazonas

    print("Cargando PersonAmazonas (modelos en GPU)...")
    t0 = time.time()
    proc = PersonAmazonas()
    print(f"  modelos cargados en {time.time() - t0:.1f}s | "
          f"YOLO half(FP16)={getattr(proc, '_yolo_half', '?')}")
    vram_base = _vram_used_mb()

    e2e: List[float] = []
    det: List[float] = []
    demo: List[float] = []

    frames = list(_frames_from_source(args.source, args.frames + args.warmup,
                                      args.width, args.height))
    if not frames:
        print("No se obtuvieron frames de la fuente.", file=sys.stderr)
        return 1

    print(f"Procesando {len(frames)} frames "
          f"({args.warmup} de warmup)...")
    for i, frame in enumerate(frames):
        warm = i < args.warmup

        # Etapa DETECCION pura (YOLO predict, SIN tracker para no
        # interferir con el estado persist del tracker de process_frame).
        td = time.perf_counter()
        results = proc.person_model.predict(
            frame, imgsz=640, conf=0.30, classes=[0], verbose=False,
            max_det=50, half=getattr(proc, "_yolo_half", False))
        det_ms = (time.perf_counter() - td) * 1000.0

        # Etapa DEMOGRAFIA aislada (sobre las personas detectadas)
        demo_ms = 0.0
        if (results and results[0].boxes is not None
                and hasattr(proc, "_demographics")):
            boxes = results[0].boxes.xyxy.cpu().numpy()
            tdd = time.perf_counter()
            for k, b in enumerate(boxes[:8]):  # tope 8 (como en produccion)
                proc._demographics.classify(frame, tuple(b), 900000 + k)
            demo_ms = (time.perf_counter() - tdd) * 1000.0

        # Etapa END-TO-END (process_frame completo)
        te = time.perf_counter()
        try:
            proc.process_frame(frame, activate_roi=False, camera_id="bench")
        except Exception as exc:
            if not warm:
                print(f"  process_frame error frame {i}: {exc}")
        e2e_ms = (time.perf_counter() - te) * 1000.0

        if not warm:
            det.append(det_ms)
            demo.append(demo_ms)
            e2e.append(e2e_ms)

    vram_peak = _vram_used_mb()

    print("\n──── Latencia por etapa (sin warmup) ────")
    _report("deteccion", det)
    _report("demografia", demo)
    _report("end-to-end", e2e)

    print("\n──── VRAM ────")
    if vram_base is not None:
        print(f"  base (tras cargar): {vram_base:8.0f} MB")
    if vram_peak is not None:
        print(f"  pico (procesando):  {vram_peak:8.0f} MB")
        print(f"  presupuesto Hito 8: < 6500 MB -> "
              f"{'OK' if vram_peak < 6500 else 'EXCEDE'}")
    else:
        print("  (no disponible: instala pynvml o ten nvidia-smi en PATH)")

    print("\n──── Objetivo Hito 8 ────")
    if e2e:
        fps = 1000.0 / statistics.fmean(e2e)
        print(f"  1 stream 1080p @ imgsz=640: {fps:.1f} FPS "
              f"(objetivo >= 20) -> {'OK' if fps >= 20 else 'POR DEBAJO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
