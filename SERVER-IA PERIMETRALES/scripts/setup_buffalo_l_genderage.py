"""Descarga el modelo genderage.onnx del pack buffalo_l de InsightFace.

El pack buffalo_l incluye un modelo genderage mas reciente y preciso que
el genderage.onnx standalone que viene en muchos repos. Este script:

  1. Descarga buffalo_l.zip oficial (~280MB) del GitHub release.
  2. Extrae solo genderage.onnx (~1.3MB).
  3. Hace backup del genderage.onnx existente como genderage.old.onnx.
  4. Reemplaza con el nuevo.
  5. Verifica que el nuevo funcione.

URL oficial:
  https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip

Uso:
  python scripts/setup_buffalo_l_genderage.py
"""

import os
import sys
import urllib.request
import zipfile
import shutil

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
MODELS_DIR = os.path.join(PROJECT_ROOT, 'models', 'classifiers')
TMP_DIR = os.path.join(PROJECT_ROOT, 'tmp_buffalo_l')
ZIP_URL = ("https://github.com/deepinsight/insightface/releases/"
           "download/v0.7/buffalo_l.zip")
ZIP_PATH = os.path.join(TMP_DIR, 'buffalo_l.zip')
GENDERAGE_OLD = os.path.join(MODELS_DIR, 'genderage.onnx')
GENDERAGE_BACKUP = os.path.join(MODELS_DIR, 'genderage.legacy.onnx')


def step(msg):
    print(f"\n[*] {msg}")


def ok(msg):
    print(f"  [OK] {msg}")


def err(msg):
    print(f"  [ERR] {msg}")


def download_with_progress(url, dest):
    last_pct = [-1]

    def report(block_num, block_size, total_size):
        downloaded = block_num * block_size
        pct = int(downloaded * 100 / total_size) if total_size > 0 else 0
        if pct != last_pct[0] and pct % 5 == 0:
            mb = downloaded / 1e6
            total_mb = total_size / 1e6
            print(f"  {pct}% ({mb:.0f}/{total_mb:.0f} MB)")
            last_pct[0] = pct

    urllib.request.urlretrieve(url, dest, reporthook=report)


def main():
    print("=" * 70)
    print("Setup: descargando genderage de buffalo_l (modelo SOTA InsightFace)")
    print("=" * 70)

    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)

    # Paso 1: descargar zip
    step(f"Descargando buffalo_l.zip (~280MB)")
    if os.path.isfile(ZIP_PATH) and os.path.getsize(ZIP_PATH) > 100_000_000:
        ok(f"Ya existe: {ZIP_PATH} "
           f"({os.path.getsize(ZIP_PATH) / 1e6:.0f}MB)")
    else:
        try:
            download_with_progress(ZIP_URL, ZIP_PATH)
            ok(f"Descargado: {os.path.getsize(ZIP_PATH) / 1e6:.0f}MB")
        except Exception as e:
            err(f"Fallo descarga: {e}")
            err(f"Intenta descargarlo manualmente de:")
            err(f"  {ZIP_URL}")
            err(f"y copialo a: {ZIP_PATH}")
            sys.exit(1)

    # Paso 2: listar contenido
    step("Explorando zip")
    try:
        with zipfile.ZipFile(ZIP_PATH, 'r') as zf:
            members = zf.namelist()
            for m in members:
                print(f"  -> {m}")
            ga_member = None
            for m in members:
                if 'genderage' in m.lower() and m.endswith('.onnx'):
                    ga_member = m
                    break
            if ga_member is None:
                err("No se encontro genderage.onnx en el zip")
                sys.exit(1)
            ok(f"Encontrado: {ga_member}")
    except Exception as e:
        err(f"Error leyendo zip: {e}")
        sys.exit(1)

    # Paso 3: backup del actual
    step("Haciendo backup del genderage actual")
    if os.path.isfile(GENDERAGE_OLD):
        size_kb = os.path.getsize(GENDERAGE_OLD) / 1024
        ok(f"Backup: {GENDERAGE_BACKUP} ({size_kb:.0f}KB)")
        shutil.copy2(GENDERAGE_OLD, GENDERAGE_BACKUP)
    else:
        ok("No habia genderage.onnx previo")

    # Paso 4: extraer el nuevo
    step("Extrayendo genderage.onnx nuevo")
    with zipfile.ZipFile(ZIP_PATH, 'r') as zf:
        with zf.open(ga_member) as src, open(GENDERAGE_OLD, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    size_kb = os.path.getsize(GENDERAGE_OLD) / 1024
    ok(f"Nuevo genderage: {GENDERAGE_OLD} ({size_kb:.0f}KB)")

    # Paso 5: verificar
    step("Verificando que el nuevo modelo funciona")
    try:
        import onnxruntime as ort
        import numpy as np
        sess = ort.InferenceSession(
            GENDERAGE_OLD, providers=['CPUExecutionProvider']
        )
        inputs = sess.get_inputs()
        outputs = sess.get_outputs()
        print(f"  Inputs:  {[(i.name, i.shape) for i in inputs]}")
        print(f"  Outputs: {[(o.name, o.shape) for o in outputs]}")
        # Test forward
        shape = inputs[0].shape
        # Sustituir None/string por valores concretos
        test_shape = [s if isinstance(s, int) else 1 for s in shape]
        # Para input dinamico, asumir 96x96 RGB
        if len(test_shape) == 4:
            if test_shape[1] == 3 or test_shape[1] == 'channels':
                test_shape = [1, 3, 96, 96]
        dummy = np.random.randn(*test_shape).astype(np.float32)
        result = sess.run([o.name for o in outputs],
                          {inputs[0].name: dummy})
        print(f"  Output shape: {result[0].shape}")
        print(f"  Output[0]: {result[0][0][:5]} ...")
        ok("Modelo funcional")
    except Exception as e:
        err(f"Verificacion fallo: {e}")
        err("Revirtiendo al modelo viejo...")
        if os.path.isfile(GENDERAGE_BACKUP):
            shutil.copy2(GENDERAGE_BACKUP, GENDERAGE_OLD)
            ok("Revertido")
        sys.exit(1)

    # Cleanup
    print(f"\n[*] Limpieza")
    print(f"  Carpeta temporal con el zip:")
    print(f"  {TMP_DIR}")
    print(f"  Para borrar: Remove-Item -Recurse -Force \"{TMP_DIR}\"")

    print("\n" + "=" * 70)
    ok("Genderage actualizado al modelo buffalo_l (SOTA InsightFace)")
    print("=" * 70)
    print("Prueba: python scripts/test_demographics_diagnostico.py "
          '"c:/Users/Sistema-1/Desktop/test_mujer.jpg"')


if __name__ == "__main__":
    main()
