"""Test de sincronizacion: simula el race condition entre webapp y
analytics. Verifica que un borrado desde webapp no es sobrescrito por
el analytics que tiene su propia copia en memoria.

Escenario:
  1. FaceReidentifier carga la DB (analytics-side).
  2. Otro "proceso" (simulado) borra una persona del pickle.
  3. Analytics-side hace su throttle-save tipico.
  4. Verificamos que la persona borrada NO reaparece.
"""
import os
import sys
import pickle
import time

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)-7s %(message)s')

import cv2
import onnxruntime as ort
from analityc.core.analytics.demographics import DemographicsClassifier
from analityc.core.analytics.face_reidentifier import FaceReidentifier

TEST_DB = os.path.join(PROJECT_ROOT, 'output', 'person_db_SYNC_TEST.pkl')

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def main():
    # Reset
    if os.path.isfile(TEST_DB):
        os.remove(TEST_DB)
    faces_dir = os.path.join(os.path.dirname(TEST_DB), 'faces')

    M = os.path.join(PROJECT_ROOT, 'models', 'classifiers')
    ga = ort.InferenceSession(os.path.join(M, 'genderage.onnx'),
                              providers=['CPUExecutionProvider'])
    embed = ort.InferenceSession(os.path.join(M, 'w600k_r50.onnx'),
                                 providers=['CPUExecutionProvider'])
    yu = cv2.FaceDetectorYN_create(
        os.path.join(M, 'face_detection_yunet_2023mar.onnx'),
        '', (320, 320), 0.45, 0.30, 5000
    )

    reid = FaceReidentifier(
        embedding_session=embed, db_path=TEST_DB,
        similarity_threshold=0.50, reset_policy='never'
    )
    clf = DemographicsClassifier(insightface_session=ga, yunet=yu,
                                 reidentifier=reid)

    print("\n=== FASE 1: registrar 2 personas (mujer + hombre) ===")
    img_w = cv2.imread('c:/Users/Sistema-1/Desktop/test_mujer.jpg')
    img_m = cv2.imread(
        os.path.join(PROJECT_ROOT, 'output', 'Personal',
                     'Alerta_periodica',
                     'Champu_Anticaspa_Kap_alerta_periodica_5_'
                     '20260508_204005_566355.jpg')
    )
    for _ in range(12):
        clf.classify(img_w, (563, 203, 784, 645), 100)
    for _ in range(12):
        clf.classify(img_m, (431, 170, 497, 441), 200)
    reid.force_save()
    print(f"  Personas en memoria: {len(reid._db)}")
    print(f"  Personas en disco: ver pickle")

    uids_initial = list(reid._db.keys())
    if len(uids_initial) < 2:
        print(f"{RED}No se pudo registrar 2 personas, abortando{RESET}")
        return
    uid_to_delete = uids_initial[0]
    print(f"  UID a borrar: {uid_to_delete[:8]}")

    print("\n=== FASE 2: borrado externo (simula webapp) ===")
    # Simular que la webapp lee, borra una persona, y guarda
    time.sleep(1.5)  # Asegurar que mtime sea distinto al de la save anterior
    with open(TEST_DB, 'rb') as f:
        payload = pickle.load(f)
    print(f"  Disco antes: {len(payload['db'])} personas")
    del payload['db'][uid_to_delete]
    print(f"  Borrando {uid_to_delete[:8]} del disco")
    with open(TEST_DB, 'wb') as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"  Disco ahora: {len(payload['db'])} personas")
    print(f"  Memoria de analytics todavia tiene: "
          f"{len(reid._db)} personas (sin sync)")

    print("\n=== FASE 3: analytics hace su throttle-save ===")
    print("  Llamando _save_db_now() (deberia sincronizar primero)...")
    reid._save_db_now()

    print(f"  Memoria de analytics tras sync: {len(reid._db)} personas")
    with open(TEST_DB, 'rb') as f:
        payload = pickle.load(f)
    print(f"  Disco final: {len(payload['db'])} personas")

    print("\n=== VERIFICACION ===")
    if uid_to_delete not in reid._db:
        print(f"  {GREEN}OK{RESET}: {uid_to_delete[:8]} "
              f"removido de memoria")
    else:
        print(f"  {RED}FAIL{RESET}: {uid_to_delete[:8]} "
              f"sigue en memoria")
    if uid_to_delete not in payload['db']:
        print(f"  {GREEN}OK{RESET}: {uid_to_delete[:8]} "
              f"NO reaparecio en disco")
    else:
        print(f"  {RED}FAIL{RESET}: {uid_to_delete[:8]} "
              f"REAPARECIO en disco!")
    if len(reid._db) == 1 and len(payload['db']) == 1:
        print(f"\n  {GREEN}TEST PASSED{RESET}: borrado externo respetado")
    else:
        print(f"\n  {RED}TEST FAILED{RESET}")

    # Limpiar
    if os.path.isfile(TEST_DB):
        os.remove(TEST_DB)


if __name__ == "__main__":
    main()
