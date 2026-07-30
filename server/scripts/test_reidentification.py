"""Test del FaceReidentifier:
  1. Procesa la misma imagen con track_id=1 -> debe registrar NEW persona
  2. Procesa la misma imagen con track_id=2 -> debe RE-IDENTIFICAR
  3. Procesa otra imagen (otra persona) con track_id=3 -> debe ser NEW
  4. Verifica stats y persistencia

Uso:
  python scripts/test_reidentification.py
"""
import os
import sys
import shutil
import cv2

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)-7s %(message)s')

from analityc.core.analytics.demographics import DemographicsClassifier  # noqa
from analityc.core.analytics.face_reidentifier import FaceReidentifier   # noqa
from analityc.core.analytics.config import AnalyticsConfig                # noqa
import onnxruntime as ort

MODELS = os.path.join(PROJECT_ROOT, 'models', 'classifiers')
TEST_DB = os.path.join(PROJECT_ROOT, 'output', 'person_db_TEST.pkl')

GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"


def banner(m):
    print(f"\n{CYAN}{'='*70}\n{m}\n{'='*70}{RESET}")


def main():
    # Reset DB de prueba
    if os.path.isfile(TEST_DB):
        os.remove(TEST_DB)

    banner("Cargando modelos")
    ga = ort.InferenceSession(
        os.path.join(MODELS, 'genderage.onnx'),
        providers=['CPUExecutionProvider']
    )
    embed = ort.InferenceSession(
        os.path.join(MODELS, 'w600k_r50.onnx'),
        providers=['CPUExecutionProvider']
    )
    yu = cv2.FaceDetectorYN_create(
        os.path.join(MODELS, 'face_detection_yunet_2023mar.onnx'),
        '', (320, 320), 0.45, 0.30, 5000
    )
    print(f"{GREEN}OK{RESET} modelos cargados")

    banner("Inicializando FaceReidentifier + DemographicsClassifier")
    reid = FaceReidentifier(
        embedding_session=embed,
        db_path=TEST_DB,
        similarity_threshold=AnalyticsConfig.REID_SIMILARITY_THRESHOLD,
        reset_policy="never",  # no reset durante el test
    )
    clf = DemographicsClassifier(
        insightface_session=ga, yunet=yu, reidentifier=reid
    )
    print(f"DB inicial: {reid.total_unique_persons} personas")

    # Imagen 1: la mujer
    img_mujer = cv2.imread('c:/Users/Sistema-1/Desktop/test_mujer.jpg')
    bbox_mujer = (563, 203, 784, 645)

    # Imagen 2: imagen con un hombre (Champu)
    img_hombre = cv2.imread(
        os.path.join(PROJECT_ROOT, 'output', 'Personal',
                     'Alerta_periodica',
                     'Champu_Anticaspa_Kap_alerta_periodica_5_'
                     '20260508_204005_566355.jpg')
    )
    bbox_hombre = (431, 170, 497, 441)

    if img_mujer is None or img_hombre is None:
        print(f"{RED}No se pudieron cargar las imagenes de test{RESET}")
        sys.exit(1)

    # === FASE 1: mujer entra como track 100 ===
    banner("FASE 1: mujer entra al area (track 100)")
    for i in range(20):
        r = clf.classify(img_mujer, bbox_mujer, 100)
        if r.get('gender') not in ('Desconocido', None):
            print(f"  Iter {i+1}: {r}")
            break
    else:
        print(f"  No converge tras 20 iters: {r}")
    print(f"\nDB tras fase 1:")
    print(f"  {reid.stats()}")

    # === FASE 2: mujer "vuelve" como track 200 (nuevo tracking) ===
    banner("FASE 2: mujer vuelve mas tarde (track 200, debe ser RE-ID)")
    for i in range(20):
        r = clf.classify(img_mujer, bbox_mujer, 200)
        if r.get('gender') not in ('Desconocido', None):
            print(f"  Iter {i+1}: {r}")
            if r.get('from_reid'):
                print(f"  {GREEN}EXITO: identificada como persona conocida{RESET}")
            else:
                print(f"  {RED}FALLO: deberia haber sido re-identificada{RESET}")
            break
    else:
        print(f"  No converge: {r}")
    print(f"\nDB tras fase 2:")
    print(f"  {reid.stats()}")

    # === FASE 3: hombre entra como track 300 (NUEVA persona) ===
    banner("FASE 3: hombre distinto entra (track 300, debe ser NEW)")
    for i in range(20):
        r = clf.classify(img_hombre, bbox_hombre, 300)
        if r.get('gender') not in ('Desconocido', None):
            print(f"  Iter {i+1}: {r}")
            if r.get('from_reid'):
                print(f"  {RED}FALLO: deberia haber sido NUEVA persona{RESET}")
            else:
                print(f"  {GREEN}EXITO: registrada como nueva persona{RESET}")
            break
    else:
        print(f"  No converge: {r}")
    print(f"\nDB tras fase 3:")
    print(f"  {reid.stats()}")

    # === FASE 4: hombre vuelve como track 400 (RE-ID) ===
    banner("FASE 4: hombre vuelve (track 400, debe ser RE-ID)")
    for i in range(20):
        r = clf.classify(img_hombre, bbox_hombre, 400)
        if r.get('gender') not in ('Desconocido', None):
            print(f"  Iter {i+1}: {r}")
            if r.get('from_reid'):
                print(f"  {GREEN}EXITO: re-identificado{RESET}")
            else:
                print(f"  {RED}FALLO: deberia haber sido re-identificado{RESET}")
            break
    else:
        print(f"  No converge: {r}")

    banner("RESULTADO FINAL")
    s = reid.stats()
    print(f"  Personas unicas en DB:        {s['total_persons']}")
    print(f"  Nuevas registradas en sesion: {s['session_new']}")
    print(f"  Re-identificadas en sesion:   {s['session_reidentified']}")
    print(f"  Tracks activos:               {s['active_tracks']}")

    if s['total_persons'] == 2:
        print(f"\n  {GREEN}OK: 2 personas unicas (mujer + hombre){RESET}")
    else:
        print(f"\n  {RED}MAL: se esperaban 2 personas, hay {s['total_persons']}{RESET}")

    if s['session_reidentified'] >= 2:
        print(f"  {GREEN}OK: re-identificacion funciona ({s['session_reidentified']} re-ids){RESET}")
    else:
        print(f"  {RED}MAL: pocas re-identificaciones ({s['session_reidentified']}){RESET}")

    # Limpiar DB de test
    if os.path.isfile(TEST_DB):
        os.remove(TEST_DB)
        print(f"\n  Limpieza: {TEST_DB} borrado")


if __name__ == "__main__":
    main()
