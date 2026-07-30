"""
Convierte el checkpoint MiVOLO ya descargado a ONNX, compatible con timm 1.0.x.

setup_mivolo.py descarga el checkpoint pero su conversión falla porque MiVOLO
espera timm==0.8.13.dev0 (usa `remap_checkpoint`, renombrado a
`remap_state_dict` en timm 1.0.x). Este script aplica un monkeypatch de
compatibilidad ANTES de importar MiVOLO, sin degradar el timm del venv (que
usan torch/ultralytics). El ONNX resultante corre con onnxruntime (sin timm
en runtime).

Uso:
    venv\\Scripts\\python.exe scripts\\convertir_mivolo_onnx.py
"""

import os
import sys
import traceback

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
MODELS_DIR = os.path.join(PROJECT_ROOT, "models", "classifiers")
TMP_DIR = os.path.join(PROJECT_ROOT, "tmp_mivolo")
MIVOLO_REPO = os.path.join(TMP_DIR, "MiVOLO")
CHECKPOINT_PATH = os.path.join(TMP_DIR, "model_imdb_cross_person_4.22_99.46.pth.tar")
ONNX_OUT = os.path.join(MODELS_DIR, "mivolo.onnx")


def parche_timm() -> None:
    """
    Compatibilidad timm 1.0.x <- MiVOLO (espera timm 0.8.13). Solo 2 símbolos
    cambiaron de sitio; el resto de la API que usa MiVOLO existe igual.
    """
    import timm.models._helpers as h
    import timm.models._pretrained as p
    import timm.models._registry as r
    if not hasattr(h, "remap_checkpoint") and hasattr(h, "remap_state_dict"):
        h.remap_checkpoint = h.remap_state_dict
        print("  [OK] parche timm: remap_checkpoint -> remap_state_dict")
    if not hasattr(p, "split_model_name_tag") and hasattr(r, "split_model_name_tag"):
        p.split_model_name_tag = r.split_model_name_tag
        print("  [OK] parche timm: split_model_name_tag (de _registry a _pretrained)")


def main() -> int:
    print("=" * 70)
    print("Conversion MiVOLO -> ONNX (compatible timm 1.0.x)")
    print("=" * 70)
    os.makedirs(MODELS_DIR, exist_ok=True)

    if not os.path.isfile(CHECKPOINT_PATH):
        print(f"  [ERR] falta el checkpoint: {CHECKPOINT_PATH}")
        print("        Ejecuta antes scripts/setup_mivolo.py (baja el .pth.tar)")
        return 1

    parche_timm()
    sys.path.insert(0, MIVOLO_REPO)

    try:
        import torch
        # torch 2.6+ pone weights_only=True por defecto y rechaza este
        # checkpoint (trae objetos no-tensor). Es del release OFICIAL de MiVOLO
        # (fuente confiable) -> forzar weights_only=False cuando MiVOLO llama a
        # torch.load sin ese argumento.
        _torch_load = torch.load
        def _load_compat(*args, **kwargs):
            kwargs.setdefault("weights_only", False)
            return _torch_load(*args, **kwargs)
        torch.load = _load_compat
        print("  [OK] parche torch.load: weights_only=False (checkpoint oficial)")

        from mivolo.model.mi_volo import MiVOLO
    except Exception as exc:
        print(f"  [ERR] importando MiVOLO: {exc}")
        traceback.print_exc()
        return 1

    print(f"  Cargando MiVOLO desde {CHECKPOINT_PATH}")
    try:
        wrapper = MiVOLO(CHECKPOINT_PATH, device="cpu", half=False,
                         use_persons=True, disable_faces=False, verbose=True)
        model = wrapper.model
        model.eval()
    except Exception as exc:
        print(f"  [ERR] cargando el modelo: {exc}")
        traceback.print_exc()
        return 1

    # Extraer metadatos de normalizacion para des-normalizar la edad en runtime:
    #   edad_real = salida * (max_age - min_age) + avg_age
    # y la normalizacion de entrada (mean/std) propia de MiVOLO.
    try:
        import json
        tam = getattr(wrapper, "input_size", 224)
        if isinstance(tam, int):          # MiVOLO expone input_size como int
            tam = [tam, tam]
        meta = {
            "min_age": float(wrapper.meta.min_age),
            "max_age": float(wrapper.meta.max_age),
            "avg_age": float(wrapper.meta.avg_age),
            "only_age": bool(getattr(wrapper.meta, "only_age", False)),
            "in_chans": 6,  # rostro(3) + cuerpo(3)
            "input_size": list(tam),
            "mean": list(map(float, wrapper.data_config["mean"])),
            "std": list(map(float, wrapper.data_config["std"])),
        }
        ruta_meta = os.path.join(MODELS_DIR, "mivolo_meta.json")
        with open(ruta_meta, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        print(f"  [OK] metadatos guardados: {ruta_meta}")
        print(f"       min_age={meta['min_age']} max_age={meta['max_age']} "
              f"avg_age={meta['avg_age']} only_age={meta['only_age']}")
        print(f"       mean={meta['mean']} std={meta['std']}")
    except Exception as exc:
        print(f"  [ADVERTENCIA] no se pudieron extraer metadatos: {exc}")

    # Entrada MiVOLO cross-person: (N, 6, 224, 224) = rostro(3) + cuerpo(3).
    dummy = torch.randn(1, 6, 224, 224, dtype=torch.float32)
    print("  Exportando a ONNX (entrada 1x6x224x224)...")
    kwargs_export = dict(
        input_names=["input"], output_names=["output"],
        # opset 18: la atencion "outlook" de VOLO usa aten::col2im (Fold), que
        # solo existe en ONNX desde opset 18. Con 14 el export falla.
        opset_version=18,
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
    )
    try:
        # torch >= 2.6 ofrece el exportador dynamo; el legacy (TorchScript) es
        # el que este modelo exporta sin problemas. Se pide explicitamente si
        # la version de torch soporta el argumento.
        import inspect
        if "dynamo" in inspect.signature(torch.onnx.export).parameters:
            kwargs_export["dynamo"] = False
        torch.onnx.export(model, dummy, ONNX_OUT, **kwargs_export)
    except Exception as exc:
        print(f"  [ERR] export ONNX: {exc}")
        traceback.print_exc()
        return 1

    if not os.path.isfile(ONNX_OUT):
        print("  [ERR] no se genero el ONNX")
        return 1

    # Verificacion + descubrir forma de salida (para adaptar el clasificador).
    try:
        import numpy as np
        import onnxruntime as ort
        sess = ort.InferenceSession(ONNX_OUT, providers=["CPUExecutionProvider"])
        entradas = [(i.name, i.shape) for i in sess.get_inputs()]
        salidas = [(o.name, o.shape) for o in sess.get_outputs()]
        out = sess.run(None, {sess.get_inputs()[0].name:
                              np.random.randn(2, 6, 224, 224).astype(np.float32)})
        print(f"  [OK] ONNX: {ONNX_OUT} ({os.path.getsize(ONNX_OUT) / 1e6:.0f} MB)")
        print(f"       entradas={entradas}  salidas={salidas}")
        print(f"       salida real (batch 2): shape={out[0].shape}, "
              f"muestra={out[0].ravel()[:6]}")
    except Exception as exc:
        print(f"  [ERR] verificando ONNX: {exc}")
        traceback.print_exc()
        return 1

    print("=" * 70)
    print("MiVOLO ONNX LISTO")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
