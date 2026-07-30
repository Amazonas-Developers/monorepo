"""
demo.py - Demo CLI de la capa de captura universal (Hito 1).

Prueba `CameraSource` con cualquier fuente de video y muestra en vivo las
métricas (fps de entrada/procesado, frames descartados, reconexiones). Sirve
para validar el criterio de aceptación del Hito 1: corre con webcam, RTSP y
archivo, y se recupera solo al cortar/restaurar la red.

Ejemplos:
    # Webcam (índice 0)
    python demo.py --source 0

    # RTSP Hikvision/Dahua
    python demo.py --source "rtsp://usuario:clave@192.168.1.10:554/Streaming/Channels/101"

    # Archivo de video (en bucle)
    python demo.py --source video.mp4 --loop

    # Sin ventana (servidor headless): imprime métricas por consola
    python demo.py --source 0 --headless

    # Con detección de personas YOLO (valida el pipeline completo)
    python demo.py --source 0 --detect

Controles (con ventana):
    q / ESC -> salir

IMPORTANTE: lanzar con el python del venv para que ONNX use GPU:
    .\\venv\\Scripts\\python.exe demo.py --source 0
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

import cv2
import numpy as np

# Permite ejecutar el script desde la raíz del proyecto sin instalar el paquete
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from analityc.core.capture import CameraSource, CameraProfile  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("demo")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Demo de captura universal (CameraSource).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source", required=True,
                   help="0/1/... (webcam), rtsp://..., http://..., o ruta de video.")
    p.add_argument("--angle", default="frontal",
                   choices=["frontal", "lateral", "cenital"],
                   help="Ángulo de la cámara (perfil).")
    p.add_argument("--headless", action="store_true",
                   help="Sin ventana: solo imprime métricas por consola.")
    p.add_argument("--loop", action="store_true",
                   help="Si es archivo, reiniciar al terminar.")
    p.add_argument("--detect", action="store_true",
                   help="Correr detección de personas YOLO (valida pipeline).")
    p.add_argument("--duration", type=float, default=0.0,
                   help="Segundos a correr (0 = indefinido).")
    p.add_argument("--watchdog", type=float, default=10.0,
                   help="Segundos sin frames antes de reconectar.")
    p.add_argument("--no-reconnect", action="store_true",
                   help="Desactiva la reconexión automática.")
    return p.parse_args()


def _load_person_model() -> Optional["object"]:
    """Carga perezosa de YOLO para --detect. Degrada con warning si falla."""
    try:
        import torch
        from ultralytics import YOLO
        from analityc.config.config import get_config  # type: ignore
        model_path = None
        try:
            cfg = get_config()
            model_path = cfg.get("person_model_paths", {}).get("Hummus")
        except Exception:
            pass
        if not model_path or not os.path.isfile(model_path):
            model_path = os.path.join(
                "models", "base", "yolo11m.pt")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = YOLO(model_path)
        logger.info("YOLO cargado (%s) en %s", model_path, device)
        return model
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo cargar YOLO para --detect: %s", exc)
        return None


def _draw_overlay(frame: np.ndarray, stats: dict) -> np.ndarray:
    """Dibuja un HUD con las métricas sobre el frame."""
    out = frame
    h = out.shape[0]
    lines = [
        f"{stats['nombre']}  [{stats['tipo']}/{stats['perfil_angulo']}]",
        f"entrada: {stats['fps_entrada']:.1f} fps | procesado: "
        f"{stats['fps_procesado']:.1f} fps",
        f"descartados: {stats['frames_descartados']} | "
        f"reconexiones: {stats['reconexiones']}",
        f"conectado: {stats['conectado']}",
    ]
    y0 = 24
    overlay = out.copy()
    cv2.rectangle(overlay, (5, 5), (430, y0 + 20 * len(lines)),
                  (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, out, 0.45, 0, out)
    color = (0, 255, 120) if stats["conectado"] else (0, 0, 255)
    for i, text in enumerate(lines):
        cv2.putText(out, text, (12, y0 + i * 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return out


def main() -> int:
    args = parse_args()

    # Normaliza source numérico
    source: object = args.source
    if isinstance(source, str) and source.isdigit():
        source = int(source)

    profile = CameraProfile(angulo=args.angle, nombre=str(args.source))
    cam = CameraSource(
        source,
        profile=profile,
        reconnect=not args.no_reconnect,
        watchdog_timeout=args.watchdog,
        loop_file=args.loop,
    )

    model = _load_person_model() if args.detect else None

    cam.start()
    logger.info("Demo iniciada. %s para salir.",
                "Ctrl+C" if args.headless else "q/ESC en la ventana")

    t_start = time.monotonic()
    last_stats_print = 0.0
    window = "CameraSource demo"
    try:
        while True:
            if args.duration > 0 and (time.monotonic() - t_start) > args.duration:
                logger.info("Duración alcanzada (%.0fs).", args.duration)
                break

            frame = cam.read()
            if frame is None:
                time.sleep(0.005)
                # Imprime métricas aunque no haya frame (estado de reconexión)
                if args.headless:
                    last_stats_print = _maybe_print_stats(
                        cam, last_stats_print)
                continue

            if model is not None:
                frame = _run_detection(model, frame)

            stats = cam.get_stats()

            if args.headless:
                last_stats_print = _maybe_print_stats(cam, last_stats_print)
                # Pequeña pausa: en headless no hace falta consumir a tope y
                # así no acaparamos el GIL frente al hilo lector.
                time.sleep(0.005)
            else:
                shown = _draw_overlay(frame.copy(), stats)
                cv2.imshow(window, shown)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        logger.info("Interrumpido por el usuario.")
    finally:
        cam.stop()
        if not args.headless:
            cv2.destroyAllWindows()
        _print_final_stats(cam)
    return 0


def _run_detection(model, frame: np.ndarray) -> np.ndarray:
    """Corre YOLO (clase persona) y dibuja las cajas. Defensivo."""
    try:
        res = model.predict(frame, imgsz=640, conf=0.30, classes=[0],
                            verbose=False, max_det=50)
        if res and res[0].boxes is not None:
            for b in res[0].boxes.xyxy.cpu().numpy():
                x1, y1, x2, y2 = [int(v) for v in b]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 180, 0), 2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Error en detección: %s", exc)
    return frame


def _maybe_print_stats(cam: CameraSource, last: float,
                       interval: float = 1.0) -> float:
    now = time.monotonic()
    if now - last < interval:
        return last
    s = cam.get_stats()
    logger.info(
        "[%s] entrada=%.1ffps procesado=%.1ffps descartados=%d "
        "reconexiones=%d conectado=%s res=%s",
        s["nombre"], s["fps_entrada"], s["fps_procesado"],
        s["frames_descartados"], s["reconexiones"], s["conectado"],
        s["resolucion"],
    )
    return now


def _print_final_stats(cam: CameraSource) -> None:
    s = cam.get_stats()
    logger.info("──── Métricas finales ────")
    for k, v in s.items():
        logger.info("  %-20s %s", k, v)


if __name__ == "__main__":
    raise SystemExit(main())
