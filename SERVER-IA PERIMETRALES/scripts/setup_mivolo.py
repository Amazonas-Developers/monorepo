"""Descarga y convierte el modelo MiVOLO 2024 a ONNX.

Pipeline:
  1. Clona el repo oficial MiVOLO (~10MB).
  2. Descarga checkpoint PyTorch (~120MB) desde el release oficial.
  3. Carga el modelo, lo pone en eval mode.
  4. Exporta a ONNX en models/classifiers/mivolo.onnx.
  5. Verifica que el ONNX se puede cargar y correr.

Requisitos:
  - git instalado en PATH
  - Python con torch, timm, omegaconf instalados
  - Conexion a internet
  - ~500MB de espacio temporal

Uso:
  python scripts/setup_mivolo.py

Si algo falla, se imprime el error y se puede reanudar desde donde quedo.
"""

import os
import sys
import subprocess
import urllib.request
import shutil

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
MODELS_DIR = os.path.join(PROJECT_ROOT, 'models', 'classifiers')
TMP_DIR = os.path.join(PROJECT_ROOT, 'tmp_mivolo')
MIVOLO_REPO = os.path.join(TMP_DIR, 'MiVOLO')
ONNX_OUT = os.path.join(MODELS_DIR, 'mivolo.onnx')

# Checkpoint URL del release oficial (cross-person model: cara+cuerpo)
# Si esta URL cambia revisar: https://github.com/WildChlamydia/MiVOLO/releases
CHECKPOINT_URL = (
    "https://variety.softclub.tj/upload/iblock/"
    "model_imdb_cross_person_4.22_99.46.pth.tar"
)
# Fallback URLs (a veces el primer mirror cae)
CHECKPOINT_URLS_FALLBACK = [
    "https://drive.google.com/uc?id=11i8pKctxz3wVkDBlWKvhYIh7kpVFXSZ4",
]
CHECKPOINT_PATH = os.path.join(
    TMP_DIR, 'model_imdb_cross_person_4.22_99.46.pth.tar'
)


def step(msg):
    print(f"\n[*] {msg}")


def ok(msg):
    print(f"  [OK] {msg}")


def err(msg):
    print(f"  [ERR] {msg}")


def run(cmd, cwd=None):
    print(f"  $ {cmd}")
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        err(f"Comando fallo: {result.stderr.strip()}")
        return False
    return True


def step1_clone_repo():
    step("Paso 1/5: Clonando MiVOLO desde GitHub")
    os.makedirs(TMP_DIR, exist_ok=True)
    if os.path.isdir(MIVOLO_REPO):
        ok(f"Repo ya existe en {MIVOLO_REPO}")
        return True
    if not run(
        f'git clone https://github.com/WildChlamydia/MiVOLO.git "{MIVOLO_REPO}"'
    ):
        return False
    ok("Repo clonado")
    return True


def step2_install_deps():
    step("Paso 2/5: Instalando dependencias Python")
    deps = ['timm>=0.9.10', 'omegaconf', 'lapx']
    for d in deps:
        if not run(f'"{sys.executable}" -m pip install --quiet "{d}"'):
            err(f"No se pudo instalar {d}")
            return False
    ok(f"Dependencias instaladas: {', '.join(deps)}")
    return True


def step3_download_checkpoint():
    step("Paso 3/5: Descargando checkpoint MiVOLO (~120MB)")
    if os.path.isfile(CHECKPOINT_PATH):
        ok(f"Checkpoint ya existe ({os.path.getsize(CHECKPOINT_PATH)/1e6:.0f}MB)")
        return True

    urls = [CHECKPOINT_URL] + CHECKPOINT_URLS_FALLBACK
    for url in urls:
        try:
            print(f"  Probando: {url}")
            urllib.request.urlretrieve(url, CHECKPOINT_PATH)
            ok(f"Descargado: {CHECKPOINT_PATH}")
            return True
        except Exception as e:
            err(f"Fallo descarga: {e}")
            continue

    err("Todas las URLs fallaron. Descarga manual:")
    print(f"    1. Abrir https://github.com/WildChlamydia/MiVOLO/releases")
    print(f"    2. Bajar 'model_imdb_cross_person_4.22_99.46.pth.tar'")
    print(f"    3. Copiarlo a: {CHECKPOINT_PATH}")
    print(f"    4. Re-ejecutar este script")
    return False


def step4_convert_to_onnx():
    step("Paso 4/5: Convirtiendo PyTorch a ONNX")
    if os.path.isfile(ONNX_OUT):
        ok(f"ONNX ya existe en {ONNX_OUT}")
        return True

    # Anadir MiVOLO al path
    sys.path.insert(0, MIVOLO_REPO)
    try:
        import torch
        from mivolo.model.mi_volo import MiVOLO
    except ImportError as e:
        err(f"Error importando MiVOLO: {e}")
        err("Revisa que las dependencias se instalaron correctamente")
        return False

    print(f"  Cargando MiVOLO desde {CHECKPOINT_PATH}")
    try:
        model_wrapper = MiVOLO(
            CHECKPOINT_PATH,
            device='cpu',
            half=False,
            use_persons=True,
            disable_faces=False,
            verbose=False
        )
        model = model_wrapper.model
        model.eval()
    except Exception as e:
        err(f"Error cargando MiVOLO: {e}")
        import traceback
        traceback.print_exc()
        return False

    print(f"  Exportando a ONNX...")
    # MiVOLO toma input (N, 6, 224, 224) = cara y cuerpo concatenados RGB
    # Si usaramos solo cara seria (N, 3, 224, 224). Probamos primero
    # solo-cara que es lo que tiene nuestro pipeline.
    dummy = torch.randn(1, 6, 224, 224, dtype=torch.float32)
    try:
        torch.onnx.export(
            model, dummy, ONNX_OUT,
            input_names=['input'],
            output_names=['output'],
            opset_version=14,
            dynamic_axes={'input': {0: 'batch'},
                          'output': {0: 'batch'}},
        )
    except Exception as e:
        err(f"Error en export ONNX: {e}")
        import traceback
        traceback.print_exc()
        return False

    if not os.path.isfile(ONNX_OUT):
        err("ONNX no se genero")
        return False

    ok(f"ONNX exportado: {ONNX_OUT} ({os.path.getsize(ONNX_OUT)/1e6:.0f}MB)")
    return True


def step5_verify():
    step("Paso 5/5: Verificando ONNX")
    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(
            ONNX_OUT, providers=['CPUExecutionProvider']
        )
        inputs = sess.get_inputs()
        outputs = sess.get_outputs()
        print(f"  Inputs: {[(i.name, i.shape) for i in inputs]}")
        print(f"  Outputs: {[(o.name, o.shape) for o in outputs]}")

        # Test forward pass
        dummy = np.random.randn(1, 6, 224, 224).astype(np.float32)
        result = sess.run([o.name for o in outputs],
                          {inputs[0].name: dummy})
        print(f"  Output shape: {result[0].shape}")
        ok("ONNX funcional")
        return True
    except Exception as e:
        err(f"Error verificando: {e}")
        return False


def cleanup():
    step("Limpieza (opcional)")
    print(f"  Carpeta temporal: {TMP_DIR}")
    print(f"  Puedes borrarla manualmente con:")
    print(f"  Remove-Item -Recurse -Force \"{TMP_DIR}\"")


def main():
    print("="*70)
    print("MiVOLO ONNX Setup")
    print("="*70)
    print(f"Target ONNX: {ONNX_OUT}")
    print(f"Temp dir:    {TMP_DIR}")

    os.makedirs(MODELS_DIR, exist_ok=True)

    steps = [
        step1_clone_repo,
        step2_install_deps,
        step3_download_checkpoint,
        step4_convert_to_onnx,
        step5_verify,
    ]

    for i, fn in enumerate(steps):
        if not fn():
            err(f"\nFALLO en paso {i+1}: {fn.__name__}")
            sys.exit(1)

    cleanup()
    print("\n" + "="*70)
    ok("MiVOLO listo. Reinicia el sistema y veras en el log:")
    print('     MiVOLO ONNX loaded -> ensemble activado')
    print("="*70)


if __name__ == "__main__":
    main()
