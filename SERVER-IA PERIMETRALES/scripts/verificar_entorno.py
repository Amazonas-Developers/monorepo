"""
verificar_entorno.py - Verificacion del entorno de GPU para DEMOGRAFIA-AMAZONAS.

Comprueba que la maquina puede correr el pipeline demografico ACELERADO POR
GPU y detecta el fallo silencioso mas peligroso de este stack: que
`onnxruntime` (CPU) pise a `onnxruntime-gpu`. Ambos paquetes comparten el
mismo namespace, asi que cuando conviven la inferencia ONNX cae a CPU SIN
lanzar ninguna excepcion: el sistema "funciona" pero a una fraccion de la
velocidad. `insightface` lo arrastra como dependencia, por eso se revisa.

Uso:
    venv\\Scripts\\python.exe scripts\\verificar_entorno.py

Codigos de salida:
    0 -> entorno apto (CUDAExecutionProvider disponible)
    1 -> falta aceleracion por GPU en onnxruntime (se explica que instalar)
    2 -> error inesperado durante la verificacion
"""

from __future__ import annotations

import platform
import subprocess
import sys
import time
from typing import Any, Callable, Optional

# ── Presentacion ────────────────────────────────────────────────────────

_ANCHO: int = 74


def titulo(texto: str) -> None:
    """Imprime un encabezado de seccion."""
    print("\n" + "=" * _ANCHO)
    print(f" {texto}")
    print("=" * _ANCHO)


def linea(etiqueta: str, valor: Any, estado: str = "") -> None:
    """Imprime una fila 'etiqueta: valor' alineada, con marca de estado."""
    marca = {"ok": "[OK]   ", "aviso": "[AVISO]", "error": "[ERROR]"}.get(
        estado, "       ")
    print(f" {marca} {etiqueta:.<38} {valor}")


def _seguro(fn: Callable[[], Any], defecto: Any = "no disponible") -> Any:
    """Ejecuta `fn` devolviendo `defecto` si lanza. Evita que un dato
    ausente aborte todo el reporte."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return f"{defecto} ({type(exc).__name__}: {exc})"


# ── Bloques de verificacion ─────────────────────────────────────────────

def verificar_sistema() -> None:
    """Datos basicos del sistema y del interprete en uso."""
    titulo("SISTEMA E INTERPRETE")
    linea("Sistema operativo", f"{platform.system()} {platform.release()}")
    linea("Python", platform.python_version())
    # Critico en esta maquina: el Python GLOBAL no tiene tensorrt ni el
    # stack completo. El servidor DEBE correr con el del venv.
    en_venv = (hasattr(sys, "real_prefix")
               or sys.base_prefix != sys.prefix)
    linea("Ejecutable", sys.executable)
    linea("Dentro de un entorno virtual", "si" if en_venv else "NO",
          "ok" if en_venv else "aviso")
    if not en_venv:
        print("\n   AVISO: no estas dentro del venv del servidor. El Python")
        print("   global de esta maquina NO tiene el stack completo")
        print("   (tensorrt, supervision). Usa:")
        print("       venv\\Scripts\\python.exe scripts\\verificar_entorno.py")


def verificar_driver_nvidia() -> None:
    """Driver y GPUs vistas por `nvidia-smi` (fuera de Python)."""
    titulo("DRIVER NVIDIA (nvidia-smi)")
    try:
        salida = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,driver_version,memory.total,memory.used",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        linea("nvidia-smi", "NO ENCONTRADO", "error")
        print("\n   No hay driver NVIDIA accesible en el PATH.")
        return
    except subprocess.TimeoutExpired:
        linea("nvidia-smi", "tiempo de espera agotado", "error")
        return

    if salida.returncode != 0:
        linea("nvidia-smi", f"fallo (codigo {salida.returncode})", "error")
        if salida.stderr.strip():
            print(f"   {salida.stderr.strip()[:200]}")
        return

    for i, fila in enumerate(salida.stdout.strip().splitlines()):
        partes = [p.strip() for p in fila.split(",")]
        if len(partes) >= 4:
            linea(f"GPU {i}", partes[0], "ok")
            linea("  Version del driver", partes[1])
            linea("  Memoria total", partes[2])
            linea("  Memoria en uso", partes[3])


def verificar_torch() -> Optional[Any]:
    """PyTorch: CUDA disponible, version compilada y capability de la GPU."""
    titulo("PYTORCH Y CUDA")
    try:
        import torch
    except ImportError as exc:
        linea("PyTorch", f"NO INSTALADO ({exc})", "error")
        return None

    linea("Version de PyTorch", torch.__version__, "ok")
    linea("CUDA de compilacion", _seguro(lambda: torch.version.cuda))
    linea("cuDNN de compilacion", _seguro(lambda: torch.backends.cudnn.version()))

    disponible = bool(torch.cuda.is_available())
    linea("torch.cuda.is_available()", "si" if disponible else "NO",
          "ok" if disponible else "error")
    if not disponible:
        print("\n   PyTorch no ve la GPU. El pipeline correria en CPU.")
        return torch

    linea("GPUs visibles", torch.cuda.device_count())
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        cap = f"sm_{props.major}{props.minor}"
        # La RTX 5060 Ti (Blackwell) es sm_120. Si PyTorch no trae kernels
        # para esa capability, falla al ejecutar aunque is_available() de True.
        esperado = cap == "sm_120"
        linea(f"GPU {i}", props.name, "ok")
        linea("  Compute capability", cap, "ok" if esperado else "aviso")
        linea("  VRAM total", f"{props.total_memory / 1024**3:.1f} GB")
        soportadas = _seguro(lambda: torch.cuda.get_arch_list(), [])
        if isinstance(soportadas, list) and soportadas:
            linea("  Arquitecturas de este PyTorch", ", ".join(soportadas))
            if cap not in soportadas:
                linea("  Kernels para esta GPU", "NO INCLUIDOS", "error")
                print("\n   Este build de PyTorch no trae kernels para "
                      f"{cap}. Necesitas una rueda con soporte Blackwell "
                      "(cu128 o superior).")
    return torch


def verificar_onnxruntime() -> bool:
    """onnxruntime: providers disponibles. Devuelve True si hay CUDA."""
    titulo("ONNXRUNTIME (detector de rostro y estimadores ONNX)")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        linea("onnxruntime", f"NO INSTALADO ({exc})", "error")
        return False

    linea("Version de onnxruntime", ort.__version__, "ok")
    try:
        # Ayuda a ORT a encontrar las DLLs de CUDA/cuDNN que trae torch.
        ort.preload_dlls()
    except Exception:
        pass

    providers = list(_seguro(lambda: ort.get_available_providers(), []))
    linea("Providers disponibles", ", ".join(providers) or "ninguno")

    hay_cuda = "CUDAExecutionProvider" in providers
    linea("CUDAExecutionProvider", "si" if hay_cuda else "NO",
          "ok" if hay_cuda else "error")
    linea("TensorrtExecutionProvider",
          "si" if "TensorrtExecutionProvider" in providers else "no")

    # Deteccion del conflicto silencioso onnxruntime vs onnxruntime-gpu.
    instalados: list[str] = []
    try:
        from importlib import metadata
        for dist in metadata.distributions():
            nombre = (dist.metadata["Name"] or "").lower()
            if nombre in ("onnxruntime", "onnxruntime-gpu"):
                instalados.append(f"{nombre}=={dist.version}")
    except Exception:
        pass
    if instalados:
        linea("Paquetes onnxruntime instalados", ", ".join(sorted(instalados)),
              "error" if len(instalados) > 1 else "ok")
    if len(instalados) > 1:
        print("\n   CONFLICTO: conviven 'onnxruntime' y 'onnxruntime-gpu'.")
        print("   Comparten namespace; el de CPU puede ganar SIN avisar.")
        print("   Solucion:")
        print("       pip uninstall -y onnxruntime")
        print("       pip install --force-reinstall onnxruntime-gpu")

    if not hay_cuda:
        print("\n   Sin CUDAExecutionProvider la deteccion de rostro corre")
        print("   en CPU y se vuelve el cuello de botella del pipeline.")
        print("   Instala la variante GPU:")
        print("       pip uninstall -y onnxruntime onnxruntime-gpu")
        print("       pip install onnxruntime-gpu")
    return hay_cuda


def benchmark_matmul(torch_mod: Any, n: int = 4096,
                     repeticiones: int = 3) -> None:
    """Multiplicacion de matrices n x n en GPU y CPU, para comparar."""
    titulo(f"BENCHMARK: multiplicacion de matrices {n}x{n}")
    if torch_mod is None:
        linea("Benchmark", "omitido (sin PyTorch)", "aviso")
        return

    def _medir(dispositivo: str) -> Optional[float]:
        """Mejor tiempo en ms de `repeticiones` intentos, o None si falla."""
        try:
            a = torch_mod.randn(n, n, device=dispositivo, dtype=torch_mod.float32)
            b = torch_mod.randn(n, n, device=dispositivo, dtype=torch_mod.float32)
            if dispositivo == "cuda":
                torch_mod.cuda.synchronize()
            _ = a @ b                      # calentamiento (excluido)
            if dispositivo == "cuda":
                torch_mod.cuda.synchronize()
            mejor = float("inf")
            for _ in range(repeticiones):
                inicio = time.perf_counter()
                _ = a @ b
                if dispositivo == "cuda":
                    torch_mod.cuda.synchronize()
                mejor = min(mejor, (time.perf_counter() - inicio) * 1000.0)
            del a, b
            if dispositivo == "cuda":
                torch_mod.cuda.empty_cache()
            return mejor
        except Exception as exc:  # noqa: BLE001
            print(f"   Fallo el benchmark en {dispositivo}: {exc}")
            return None

    # GFLOP de una matmul n x n: 2*n^3 operaciones.
    gflop = 2.0 * (n ** 3) / 1e9

    t_cpu = _medir("cpu")
    if t_cpu is not None:
        linea("CPU", f"{t_cpu:8.1f} ms   ({gflop / (t_cpu / 1000):.0f} GFLOP/s)")

    t_gpu: Optional[float] = None
    if torch_mod.cuda.is_available():
        t_gpu = _medir("cuda")
        if t_gpu is not None:
            linea("GPU", f"{t_gpu:8.1f} ms   ({gflop / (t_gpu / 1000):.0f} GFLOP/s)",
                  "ok")
    else:
        linea("GPU", "omitido (CUDA no disponible)", "aviso")

    if t_cpu and t_gpu:
        linea("Aceleracion GPU vs CPU", f"{t_cpu / t_gpu:.1f}x", "ok")


def main() -> int:
    """Ejecuta todas las verificaciones. Devuelve el codigo de salida."""
    print("=" * _ANCHO)
    print(" VERIFICACION DE ENTORNO - DEMOGRAFIA-AMAZONAS")
    print("=" * _ANCHO)

    verificar_sistema()
    verificar_driver_nvidia()
    torch_mod = verificar_torch()
    hay_cuda_ort = verificar_onnxruntime()
    benchmark_matmul(torch_mod)

    titulo("RESULTADO")
    if not hay_cuda_ort:
        linea("Entorno apto para el pipeline", "NO", "error")
        print("\n Falta CUDAExecutionProvider en onnxruntime (ver arriba).")
        return 1
    if torch_mod is None or not torch_mod.cuda.is_available():
        linea("Entorno apto para el pipeline", "NO", "error")
        print("\n PyTorch no tiene acceso a la GPU.")
        return 1
    linea("Entorno apto para el pipeline", "SI", "ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nVerificacion interrumpida por el usuario.")
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"\nError inesperado durante la verificacion: "
              f"{type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
