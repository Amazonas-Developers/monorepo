"""
Verificación del entorno de VIGILANTE-AMAZONAS.

Valida: Python, driver NVIDIA, GPUs visibles (con prueba de kernel real en
cada una — en Blackwell sm_120 `cuda.is_available()` puede dar True y aun así
fallar todo kernel si el build de torch no trae sm_120), PyTorch cu128,
TensorRT, ultralytics, onnxruntime-gpu, modelos ONNX/engine y paquetes clave.

Uso (desde SERVER-IA PERIMETRALES, con el venv):
    venv\\Scripts\\python.exe vigilante_amazonas\\verificar_entorno.py

Código de salida: 0 si no hay fallas bloqueantes (los ⚠️ no bloquean).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vigilante_amazonas import config  # noqa: E402

OK = "✅"       # ✅
ADVERTENCIA = "⚠️"   # ⚠️
FALLA = "❌"    # ❌

# GPUs esperadas por diseño (nombre parcial -> rol).
GPUS_ESPERADAS: list[tuple[str, str, str]] = [
    ("RTX 5060 Ti", "Detección YOLO + tracking (TensorRT FP16)", config.DEVICE_DETECCION),
    ("RTX 3090 Ti", "Re-ID: ArcFace + vestimenta", config.DEVICE_REID),
    ("RTX 3090 Ti", "VLM Qwen2.5-VL verificador", config.DEVICE_VLM),
]


@dataclass
class Resultado:
    """Una fila de la tabla de verificación."""
    check: str
    estado: str          # OK | ADVERTENCIA | FALLA
    detalle: str


@dataclass
class Reporte:
    filas: list[Resultado] = field(default_factory=list)

    def ok(self, check: str, detalle: str) -> None:
        self.filas.append(Resultado(check, OK, detalle))

    def advertencia(self, check: str, detalle: str) -> None:
        self.filas.append(Resultado(check, ADVERTENCIA, detalle))

    def falla(self, check: str, detalle: str) -> None:
        self.filas.append(Resultado(check, FALLA, detalle))

    @property
    def hay_fallas(self) -> bool:
        return any(f.estado == FALLA for f in self.filas)

    def imprimir(self) -> None:
        ancho_check = max(len(f.check) for f in self.filas) + 2
        print("\n" + "=" * 100)
        print("  VERIFICACIÓN DE ENTORNO — VIGILANTE-AMAZONAS")
        print("=" * 100)
        for f in self.filas:
            print(f"  {f.estado}  {f.check:<{ancho_check}} {f.detalle}")
        print("=" * 100)
        fallas = sum(1 for f in self.filas if f.estado == FALLA)
        advert = sum(1 for f in self.filas if f.estado == ADVERTENCIA)
        if fallas:
            print(f"  RESULTADO: {FALLA} {fallas} falla(s) bloqueante(s), {advert} advertencia(s).")
        elif advert:
            print(f"  RESULTADO: {ADVERTENCIA} entorno OPERATIVO en modo degradado ({advert} advertencia(s)).")
        else:
            print(f"  RESULTADO: {OK} entorno completo, todos los checks superados.")
        print("=" * 100 + "\n")


def _version_de(paquete: str) -> str | None:
    """Versión instalada de un paquete importable, o None si falta."""
    try:
        modulo = importlib.import_module(paquete)
        return str(getattr(modulo, "__version__", "instalado"))
    except Exception:
        return None


def verificar_python(rep: Reporte) -> None:
    v = sys.version_info
    detalle = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if (v.major, v.minor) >= (3, 11):
        rep.ok("Python >= 3.11", detalle)
    else:
        rep.falla("Python >= 3.11", detalle + " — se requiere 3.11+")


def verificar_driver(rep: Reporte) -> None:
    try:
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip().splitlines()
        rep.ok("Driver NVIDIA", f"versión {salida[0]} ({len(salida)} GPU física(s) reportada(s))")
    except Exception as exc:
        rep.falla("Driver NVIDIA", f"nvidia-smi no disponible: {exc}")


def verificar_torch_y_gpus(rep: Reporte) -> None:
    try:
        import torch
    except Exception as exc:
        rep.falla("PyTorch", f"no importa: {exc}")
        return

    cuda_build: str = str(torch.version.cuda or "sin CUDA")
    detalle = f"torch {torch.__version__} (build CUDA {cuda_build})"
    partes = cuda_build.split(".")
    cu_ok = len(partes) >= 2 and (int(partes[0]), int(partes[1])) >= (12, 8)
    if cu_ok:
        rep.ok("PyTorch build cu128+", detalle)
    else:
        rep.falla("PyTorch build cu128+", detalle + " — Blackwell sm_120 exige cu128+")

    archs: list[str] = torch.cuda.get_arch_list()
    for sm in ("sm_120", "sm_86"):
        if sm in archs:
            rep.ok(f"Soporte {sm}", f"incluido en arch list de torch")
        else:
            rep.falla(f"Soporte {sm}", f"NO está en arch list: {archs}")

    if not torch.cuda.is_available():
        rep.falla("CUDA disponible", "torch.cuda.is_available() == False")
        return

    n: int = torch.cuda.device_count()
    for i in range(n):
        nombre = torch.cuda.get_device_name(i)
        mayor, menor = torch.cuda.get_device_capability(i)
        libre_b, total_b = torch.cuda.mem_get_info(i)
        libre, total = libre_b / 1024**3, total_b / 1024**3
        # Prueba de kernel REAL (matmul FP16): detecta el caso Blackwell donde
        # is_available()=True pero "no kernel image is available".
        try:
            a = torch.randn(256, 256, dtype=torch.float16, device=f"cuda:{i}")
            _ = (a @ a).sum().item()
            kernel = "kernel FP16 OK"
            rep.ok(f"GPU cuda:{i}",
                   f"{nombre} | sm_{mayor}{menor} | VRAM {libre:.1f}/{total:.1f} GB libre | {kernel}")
        except Exception as exc:
            rep.falla(f"GPU cuda:{i}", f"{nombre} | kernel FP16 FALLÓ: {exc}")

    # Contraste contra el hardware esperado por diseño (3 GPUs).
    nombres = [torch.cuda.get_device_name(i) for i in range(n)]
    for esperado, rol, dev in GPUS_ESPERADAS:
        idx = config.indice_de(dev)
        if idx < n and esperado.lower() in nombres[idx].lower():
            rep.ok(f"Rol {dev}", f"{esperado} presente — {rol}")
        else:
            resuelto = config.resolver_dispositivo(dev)
            rep.advertencia(
                f"Rol {dev}",
                f"{esperado} NO visible ({rol}) — fallback a {resuelto} (modo degradado)")


def verificar_tensorrt(rep: Reporte) -> None:
    try:
        import tensorrt as trt
        version = trt.__version__
        partes = tuple(int(x) for x in version.split(".")[:2])
        if partes >= (10, 8):
            rep.ok("TensorRT >= 10.8", f"tensorrt {version} (sm_120 requiere >= 10.8)")
        else:
            rep.advertencia("TensorRT >= 10.8",
                            f"tensorrt {version} — insuficiente para sm_120; se usará fallback .pt")
    except Exception as exc:
        rep.advertencia("TensorRT >= 10.8", f"no importa ({exc}); se usará fallback PyTorch FP16")


def verificar_detector(rep: Reporte) -> None:
    v = _version_de("ultralytics")
    if v is None:
        rep.falla("ultralytics", "no instalado")
    else:
        partes = tuple(int(x) for x in v.split(".")[:2])
        if partes >= (8, 4):
            rep.ok("ultralytics >= 8.4 (YOLO26)", f"versión {v}")
        else:
            rep.falla("ultralytics >= 8.4 (YOLO26)", f"versión {v} — 8.3.x no soporta YOLO26")

    for candidato in config.DETECTOR_CANDIDATOS:
        if candidato.exists():
            mb = candidato.stat().st_size / 1024**2
            rep.ok("Modelo de detección", f"{candidato.name} ({mb:.0f} MB) — primero de la cadena que existe")
            return
    rep.falla("Modelo de detección",
              f"ninguno de los candidatos existe: {[c.name for c in config.DETECTOR_CANDIDATOS]}")


def verificar_onnxruntime(rep: Reporte) -> None:
    try:
        import onnxruntime as ort
        provs = ort.get_available_providers()
        if "CUDAExecutionProvider" in provs:
            rep.ok("onnxruntime-gpu", f"{ort.__version__} con CUDAExecutionProvider")
        else:
            rep.advertencia("onnxruntime-gpu", f"{ort.__version__} SIN CUDA ({provs}) — Re-ID iría a CPU")
    except Exception as exc:
        rep.falla("onnxruntime-gpu", f"no importa: {exc}")


def verificar_modelos_reid(rep: Reporte) -> None:
    modelos: list[tuple[str, Path]] = [
        ("YuNet (detección facial)", config.RUTA_YUNET_ONNX),
        ("ArcFace w600k_r50 (512d)", config.RUTA_ARCFACE_ONNX),
        ("OSNet x1_0 (vestimenta)", config.RUTA_OSNET_ONNX),
    ]
    for nombre, ruta in modelos:
        if ruta.exists():
            rep.ok(nombre, str(ruta.relative_to(config.RUTA_SERVIDOR)))
        else:
            rep.falla(nombre, f"falta {ruta}")


def verificar_paquetes(rep: Reporte) -> None:
    paquetes: list[tuple[str, str, bool]] = [
        # (nombre_pip, módulo, bloqueante)
        ("supervision (ByteTrack)", "supervision", True),
        ("fastapi", "fastapi", True),
        ("uvicorn", "uvicorn", True),
        ("python-socketio", "socketio", True),
        ("opencv", "cv2", True),
        ("numpy", "numpy", True),
        ("transformers (CLIP/VLM)", "transformers", True),
        ("qwen-vl-utils", "qwen_vl_utils", False),
        ("accelerate", "accelerate", False),
        ("aiofiles", "aiofiles", True),
        ("torchreid (OSNet)", "torchreid", False),
    ]
    for etiqueta, modulo, bloqueante in paquetes:
        v = _version_de(modulo)
        if v is not None:
            rep.ok(etiqueta, f"versión {v}")
        elif bloqueante:
            rep.falla(etiqueta, "no instalado")
        else:
            rep.advertencia(etiqueta, "no instalado (funcionalidad opcional degradada)")


def verificar_carpetas(rep: Reporte) -> None:
    try:
        config.crear_directorios()
        prueba = config.RUTA_LOGS / ".prueba_escritura"
        prueba.write_text("ok", encoding="utf-8")
        prueba.unlink()
        rep.ok("Carpetas de datos", "galeria/, snapshots/, db/, logs/ creadas y escribibles")
    except Exception as exc:
        rep.falla("Carpetas de datos", f"sin permiso de escritura: {exc}")


def main() -> int:
    rep = Reporte()
    verificar_python(rep)
    verificar_driver(rep)
    verificar_torch_y_gpus(rep)
    verificar_tensorrt(rep)
    verificar_detector(rep)
    verificar_onnxruntime(rep)
    verificar_modelos_reid(rep)
    verificar_paquetes(rep)
    verificar_carpetas(rep)
    rep.imprimir()
    return 1 if rep.hay_fallas else 0


if __name__ == "__main__":
    raise SystemExit(main())
