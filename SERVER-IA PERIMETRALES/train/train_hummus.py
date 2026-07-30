import os
# ★ Reduce la fragmentacion del caching allocator (lo recomienda el propio
#   mensaje de OOM). DEBE fijarse ANTES de importar torch / crear el contexto CUDA.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
import torch
import dotenv
from .class_base_train import Train
from src.analityc.core.hardware_available import device_hardware

dotenv.load_dotenv()

# ═══════════════════════════════════════════════════════════════
# VARIABLES DE ENTORNO
# ═══════════════════════════════════════════════════════════════
api_key                 = os.getenv('api_keyroboflow')
workspace_name_roboflow = os.getenv('workspace_name_roboflow')
project_name_roboflow   = 'agora'
version                 = 3


# ═══════════════════════════════════════════════════════════════
# GPU — RTX 5060 Ti 16 GB (Blackwell / GB206)
# ═══════════════════════════════════════════════════════════════
def _pick_gpu() -> int | str:
    """
    Busca la RTX 5060 Ti por nombre en la lista de GPUs disponibles.
    Fallback al índice 1, luego al 0.
    Requiere: CUDA 12.8+, driver 570+, PyTorch 2.5+
    """
    try:
        for i, info in enumerate(device_hardware.gpu_tuple):
            if '5060' in info.get('name', '').lower():
                print(f"[GPU] RTX 5060 Ti encontrada → índice {i} | {info}")
                return info['gpu_use']
        # Fallback original del script
        return device_hardware.gpu_tuple[1]['gpu_use']
    except (IndexError, KeyError):
        return 0

selected_gpu = _pick_gpu()


# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE ENTRENAMIENTO
# Hardware : i9-11900K (8C/16T) · 48 GB RAM · RTX 5060 Ti 16 GB (Blackwell)
# Dataset  : 134 123 imágenes · yolo26m · imgsz 640
# ═══════════════════════════════════════════════════════════════
TRAIN_CONFIG = {

    # ── Core ─────────────────────────────────────────────────
    "epochs":       150,
    "patience":     40,
    "imgsz":        640,
    "batch":        24,     # ★ 16 GB VRAM + AMP + yolo26m @640 → ~8-9 GB (margen seguro).
                            #   batch=64 y 32 DABAN OOM (medium @640 necesita ~26 GB con b=64);
                            #   el auto-reduce de ultralytics NO recupera: el primer OOM corrompe
                            #   el contexto CUDA y el retry vuelve a petar. Hay que caber al 1er
                            #   intento. Para subir gradiente efectivo usa nbs/accumulate, no batch.
                            #   Si quieres exprimir VRAM, prueba 20-24 y vigila nvidia-smi.

    # ── Velocidad y Memoria ───────────────────────────────────
    "amp":           True,   # ★ FP16 mixed precision → Tensor Cores Blackwell (más rápido, −VRAM)
    "cache":         "disk", # 134k imgs cacheadas (~150 GB) → NO re-decodifica el JPEG en cada
                             #   epoch (gran ahorro en 150 epochs). 48 GB RAM no alcanza
                             #   (ram≈160 GB); disco SÍ (293 GB libres). 1er epoch construye cache.
    "workers":       12,     # i9-11900K (8C/16T): con cache en disco los workers solo cargan .npy
                             #   (barato) → 12 alimenta el DataLoader holgado sin pisar el main.
    "deterministic": False,  # ★ habilita cudnn.benchmark (autotune de convoluciones) → +velocidad
                             #   sin coste real en entrenamiento (True forzaría algoritmos lentos).

    # ── Optimizador y Learning Rate ───────────────────────────
    "optimizer":        "SGD",     # Mejor mAP FINAL en deteccion (el estandar de YOLO).
                                    # Converge algo mas lento que AdamW pero generaliza mejor.
    "lr0":              0.01,      # ★ LR inicial de SGD en YOLO (10x el de AdamW). Con warmup+cos_lr.
    "lrf":              0.01,      # LR final = lr0 × lrf = 0.0001
    "momentum":         0.937,     # Momentum SGD (valor por defecto de YOLO)
    "weight_decay":     0.0005,    # Regularización L2 (default SGD)
    "warmup_epochs":    5,         # Warmup más largo para estabilizar 134k imágenes
    "warmup_momentum":  0.8,
    "warmup_bias_lr":   0.1,
    "cos_lr":           True,      # Cosine annealing → bajada suave del LR

    # ── Augmentación ─────────────────────────────────────────
    "mosaic":           1.0,       # Mosaico activo (mezcla 4 imgs → más contexto)
    "close_mosaic":     15,        # ★ Desactiva mosaico los últimos 15 epochs
                                    #   El modelo hace fine-tune con imágenes limpias
    "mixup":            0.1,       # ↓ de 0.15: con 134k imgs sobra data; mixup pesado ralentiza
    "copy_paste":       0.0,       # ↓ de 0.1: aporta poco en deteccion pura y cuesta CPU/epoch
    # NOTA: 'label_smoothing' se ELIMINO -> ya NO es un arg valido de ultralytics
    #       8.4.75 (rompia el entrenamiento con "not a valid YOLO argument").
    "degrees":          0.0,       # Rotación (desactivada: las personas no van de cabeza)
    "translate":        0.1,
    "scale":            0.5,
    "shear":            0.0,
    "perspective":      0.0,
    "flipud":           0.0,
    "fliplr":           0.5,       # Flip horizontal (activo por defecto)
    "hsv_h":            0.015,
    "hsv_s":            0.7,
    "hsv_v":            0.4,

    # ── Checkpoints y Logs ────────────────────────────────────
    "save":             True,
    "save_period":      10,        # Guarda checkpoint cada 10 epochs (no solo el best)
    "plots":            True,      # Genera gráficas de métricas al final
    "verbose":          True,
}

# ───────────────────────────────────────────────────────────────
# class_base_train.run_train(**kwargs) ya recibe y aplica TODO este
# TRAIN_CONFIG (es la unica fuente de verdad de los hiperparametros).
# ───────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════
# INSTANCIA DEL TRAINER
# ═══════════════════════════════════════════════════════════════
train = Train(
    api_key                 = api_key,
    workspace_name_roboflow = workspace_name_roboflow,
    project_name_roboflow   = project_name_roboflow,
    version                 = version,
    device_train            = selected_gpu,
    model_path              = 'models/base/yolo26m.pt',
)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    # Obligatorio en Windows con multiprocesamiento
    try:
        from multiprocessing import freeze_support
        freeze_support()
    except ImportError:
        pass

    # ── Diagnóstico pre-entrenamiento ─────────────────────────
    import psutil
    ram_gb = psutil.virtual_memory().total / 1024**3
    if torch.cuda.is_available():
        dev_idx = selected_gpu if isinstance(selected_gpu, int) else 0
        gpu_name = torch.cuda.get_device_name(dev_idx)
        vram_gb  = torch.cuda.get_device_properties(dev_idx).total_memory / 1024**3
        print(f"\n{'═' * 58}")
        print(f"  CPU      : Intel i9-11900K (8C/16T)")
        print(f"  RAM      : {ram_gb:.0f} GB  (cache en disco — 134k imgs ≈ 150 GB)")
        print(f"  GPU      : {gpu_name}")
        print(f"  VRAM     : {vram_gb:.1f} GB")
        print(f"  CUDA     : {torch.version.cuda}")
        print(f"  PyTorch  : {torch.__version__}")
        print(f"  Batch    : {TRAIN_CONFIG['batch']}  |  ImgSz  : {TRAIN_CONFIG['imgsz']}")
        print(f"  Workers  : {TRAIN_CONFIG['workers']}  |  AMP    : {TRAIN_CONFIG['amp']}")
        print(f"  Cache    : {TRAIN_CONFIG['cache']}  |  Optim  : {TRAIN_CONFIG['optimizer']}")
        print(f"  Dataset  : 134k imgs  |  Epochs : {TRAIN_CONFIG['epochs']}")
        print(f"  LR0      : {TRAIN_CONFIG['lr0']}  |  cos_lr  : {TRAIN_CONFIG['cos_lr']}")
        print(f"{'═' * 58}\n")

        # Alerta si no es la 5060 Ti esperada
        if '5060' not in gpu_name:
            print(f"  ⚠  GPU detectada no es RTX 5060 Ti → {gpu_name}")
            print(f"     Ajusta _pick_gpu() si es necesario.\n")
    else:
        print("⚠  CUDA no disponible. Entrenando en CPU (muy lento).")

    # ── Lanzar entrenamiento ──────────────────────────────────
    train.run_train(**TRAIN_CONFIG)
