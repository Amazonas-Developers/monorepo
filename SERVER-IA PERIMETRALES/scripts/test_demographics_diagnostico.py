"""Script de diagnostico end-to-end del pipeline de genero/edad.

Toma una imagen, corre cada etapa del pipeline en aislamiento y reporta
QUE etapa falla y por que. Sirve para calibrar thresholds rapidamente
contra imagenes reales de CCTV del cliente.

Uso:
  python scripts/test_demographics_diagnostico.py <ruta_imagen>
  python scripts/test_demographics_diagnostico.py <carpeta>
"""

import os
import sys
import glob
import time
import cv2
import numpy as np
import logging

# Permite ejecutar desde la raiz del proyecto
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

# Subimos verbosidad para ver mensajes de rechazo
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-7s %(name)-30s | %(message)s',
)

from analityc.core.analytics.demographics import (  # noqa: E402
    DemographicsClassifier, _estimate_head_pose, _GENDER_LIST
)
from analityc.core.analytics.config import AnalyticsConfig  # noqa: E402
from ultralytics import YOLO  # noqa: E402
import torch  # noqa: E402

MODELS_DIR = os.path.join(PROJECT_ROOT, 'models', 'classifiers')

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def banner(msg, color=CYAN):
    print(f"\n{color}{BOLD}{'=' * 70}\n{msg}\n{'=' * 70}{RESET}")


def load_all_classifiers():
    """Carga los mismos modelos que carga el sistema en produccion."""
    out = {
        'gender_net': None, 'age_net': None, 'face_cascade': None,
        'face_dnn': None, 'yunet': None,
        'insightface_session': None, 'mivolo_session': None,
    }
    g_proto = os.path.join(MODELS_DIR, 'gender_deploy.prototxt')
    g_model = os.path.join(MODELS_DIR, 'gender_net.caffemodel')
    if os.path.exists(g_proto) and os.path.exists(g_model):
        out['gender_net'] = cv2.dnn.readNet(g_model, g_proto)
        print(f"{GREEN}OK{RESET} Caffe gender loaded")

    a_proto = os.path.join(MODELS_DIR, 'age_deploy.prototxt')
    a_model = os.path.join(MODELS_DIR, 'age_net.caffemodel')
    if os.path.exists(a_proto) and os.path.exists(a_model):
        out['age_net'] = cv2.dnn.readNet(a_model, a_proto)
        print(f"{GREEN}OK{RESET} Caffe age loaded")

    yunet_path = os.path.join(MODELS_DIR,
                              'face_detection_yunet_2023mar.onnx')
    if os.path.exists(yunet_path):
        out['yunet'] = cv2.FaceDetectorYN_create(
            yunet_path, "", (320, 320), 0.45, 0.30, 5000,
        )
        print(f"{GREEN}OK{RESET} YuNet face detector loaded")

    face_pbtxt = os.path.join(MODELS_DIR, 'opencv_face_detector.pbtxt')
    face_pb = os.path.join(MODELS_DIR, 'opencv_face_detector_uint8.pb')
    if os.path.exists(face_pbtxt) and os.path.exists(face_pb):
        out['face_dnn'] = cv2.dnn.readNetFromTensorflow(face_pb, face_pbtxt)
        print(f"{GREEN}OK{RESET} Res10 SSD face detector loaded")

    out['face_cascade'] = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )

    insight_path = os.path.join(MODELS_DIR, 'genderage.onnx')
    if os.path.exists(insight_path):
        import onnxruntime as ort
        providers = ['CPUExecutionProvider']
        if torch.cuda.is_available():
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        out['insightface_session'] = ort.InferenceSession(
            insight_path, providers=providers
        )
        print(f"{GREEN}OK{RESET} InsightFace genderage loaded")

    mivolo_path = os.path.join(MODELS_DIR, 'mivolo.onnx')
    if os.path.exists(mivolo_path):
        import onnxruntime as ort
        providers = ['CPUExecutionProvider']
        if torch.cuda.is_available():
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        out['mivolo_session'] = ort.InferenceSession(
            mivolo_path, providers=providers
        )
        print(f"{GREEN}OK{RESET} MiVOLO loaded")
    else:
        print(f"{YELLOW}--{RESET} MiVOLO no presente (opcional)")
    return out


def detect_persons(image, person_model):
    """Detecta personas con YOLO. Devuelve lista de bboxes."""
    res = person_model.predict(
        image, classes=[0], conf=0.35, iou=0.4, verbose=False
    )
    boxes = []
    for r in res:
        if r.boxes is None:
            continue
        for box in r.boxes:
            xy = box.xyxy[0].tolist()
            conf = float(box.conf[0])
            boxes.append((int(xy[0]), int(xy[1]),
                          int(xy[2]), int(xy[3]), conf))
    return boxes


def diagnose_one_image(img_path, classifier, person_model, max_samples=30):
    """Diagnostica una imagen estatica.

    Estrategia: simulamos un track persistente sobre la misma imagen
    durante N "frames" (la misma imagen). Asi vemos si converge o no,
    y donde se atasca.
    """
    banner(f"IMAGEN: {os.path.basename(img_path)}", CYAN)

    img = cv2.imread(img_path)
    if img is None:
        print(f"{RED}No se pudo leer la imagen{RESET}")
        return None

    h, w = img.shape[:2]
    print(f"  Resolucion: {w}x{h}")

    boxes = detect_persons(img, person_model)
    if not boxes:
        print(f"{RED}YOLO no detecto ninguna persona en esta imagen.{RESET}")
        return None
    print(f"  Personas detectadas: {len(boxes)}")
    for i, (x1, y1, x2, y2, conf) in enumerate(boxes):
        pw, ph = x2 - x1, y2 - y1
        big_enough = (
            pw >= classifier._cfg.DEMO_MIN_PERSON_BBOX_W and
            ph >= classifier._cfg.DEMO_MIN_PERSON_BBOX_H
        )
        marker = GREEN + "OK" + RESET if big_enough else RED + "PEQ" + RESET
        print(f"    [{i}] bbox={pw}x{ph} conf={conf:.2f} {marker}")

    # Probar la persona mas grande (la mas cercana a camara)
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    x1, y1, x2, y2, _ = boxes[0]
    print(f"\n  Testeando persona MAS GRANDE: bbox=({x1},{y1},{x2},{y2})")

    # Simulamos un track con la misma imagen N veces
    track_id = 9999
    classifier.remove_track(track_id)
    last_result = None
    samples_added = 0
    rejects_pose = 0
    rejects_quality = 0
    rejects_face = 0

    for i in range(max_samples):
        before_samples = classifier._accum.get(track_id)
        before_n = before_samples.samples if before_samples else 0
        before_pose_reject = classifier._pose_reject_counter.get(track_id, 0)

        result = classifier.classify(img, (x1, y1, x2, y2), track_id)

        after_acc = classifier._accum.get(track_id)
        after_n = after_acc.samples if after_acc else 0
        after_pose_reject = classifier._pose_reject_counter.get(track_id, 0)

        if after_n > before_n:
            samples_added += 1
        if after_pose_reject > before_pose_reject:
            rejects_pose += 1

        last_result = result
        if result.get('gender') not in ('Desconocido', None) and \
                not result.get('provisional', False):
            print(f"  {GREEN}COMMIT en iteracion {i+1}{RESET}: {result}")
            break

    if last_result is None or last_result.get('gender') == 'Desconocido':
        acc = classifier._accum.get(track_id)
        final_samples = acc.samples if acc else 0
        print(f"\n  {YELLOW}NO CONVERGIO en {max_samples} iteraciones.{RESET}")
        print(f"  Samples validos acumulados: {final_samples}")
        print(f"  Rechazos:")
        print(f"    - por pose:         "
              f"{classifier._pose_reject_counter.get(track_id, 0)}")
        print(f"    - por calidad:      "
              f"{classifier._quality_reject_counter.get(track_id, 0)}")
        print(f"    - por ensemble:     "
              f"{classifier._ensemble_reject_counter.get(track_id, 0)}")
        print(f"  Resultado final: gender='{last_result.get('gender')}' "
              f"age='{last_result.get('age_range')}' "
              f"conf={last_result.get('confidence', 0):.2f}")
        if acc and acc.samples > 0:
            g = acc._robust_gender()
            print(f"  Distribucion genero acumulada: "
                  f"{_GENDER_LIST[0]}={g[0]:.3f} {_GENDER_LIST[1]}={g[1]:.3f}")

    # Diagnostico detallado: probamos cada etapa por separado
    banner("DIAGNOSTICO POR ETAPAS", YELLOW)

    person_crop = img[max(0, y1-int((y2-y1)*0.20)):y2,
                      max(0, x1-int((x2-x1)*0.25)):
                      min(w, x2+int((x2-x1)*0.25))]
    print(f"  Crop persona: {person_crop.shape}")

    if classifier._yunet is not None:
        h2, w2 = person_crop.shape[:2]
        target = classifier._cfg.DEMO_YUNET_UPSCALE_TARGET
        scale = target / max(w2, h2)
        if scale != 1.0:
            tw, th = int(w2*scale), int(h2*scale)
            crop_rs = cv2.resize(person_crop, (tw, th))
            classifier._yunet.setInputSize((tw, th))
        else:
            crop_rs = person_crop
            classifier._yunet.setInputSize((w2, h2))
        _, faces = classifier._yunet.detect(crop_rs)
        if faces is None or len(faces) == 0:
            print(f"  {RED}YuNet: 0 caras detectadas{RESET}")
        else:
            print(f"  YuNet detecto {len(faces)} cara(s):")
            for fi, f in enumerate(faces):
                score = float(f[-1])
                w_face = float(f[2])
                h_face = float(f[3])
                # Estimacion de pose
                r_eye = (f[4], f[5])
                l_eye = (f[6], f[7])
                nose = (f[8], f[9])
                r_mouth = (f[10], f[11])
                l_mouth = (f[12], f[13])
                kp = np.array([r_eye, l_eye, nose, r_mouth, l_mouth],
                              dtype=np.float64)
                pose = _estimate_head_pose(kp, (crop_rs.shape[1],
                                                crop_rs.shape[0]))
                eye_dx = abs(l_eye[0] - r_eye[0])
                eye_dy = abs(l_eye[1] - r_eye[1])
                eye_dist = (eye_dx**2 + eye_dy**2) ** 0.5
                ratio_eye_face = eye_dist / max(w_face, 1)
                eye_cx = (r_eye[0] + l_eye[0]) / 2.0
                nose_off = abs(nose[0] - eye_cx) / max(eye_dist, 1e-6)

                checks = []
                if score < classifier._cfg.DEMO_YUNET_MIN_SCORE:
                    checks.append(f"{RED}score{RESET} {score:.2f}<{classifier._cfg.DEMO_YUNET_MIN_SCORE}")
                else:
                    checks.append(f"{GREEN}score{RESET} {score:.2f}")
                if ratio_eye_face < classifier._cfg.DEMO_MIN_EYE_FACE_RATIO:
                    checks.append(f"{RED}eye_ratio{RESET} {ratio_eye_face:.2f}<{classifier._cfg.DEMO_MIN_EYE_FACE_RATIO}")
                else:
                    checks.append(f"{GREEN}eye_ratio{RESET} {ratio_eye_face:.2f}")
                if nose_off > classifier._cfg.DEMO_MAX_NOSE_OFFSET:
                    checks.append(f"{RED}nose_off{RESET} {nose_off:.2f}>{classifier._cfg.DEMO_MAX_NOSE_OFFSET}")
                else:
                    checks.append(f"{GREEN}nose_off{RESET} {nose_off:.2f}")

                if pose is not None:
                    yaw, pitch, roll = pose
                    py_ok = abs(yaw) <= classifier._cfg.DEMO_POSE_MAX_YAW_DEG
                    pp_ok = abs(pitch) <= classifier._cfg.DEMO_POSE_MAX_PITCH_DEG
                    pr_ok = abs(roll) <= classifier._cfg.DEMO_POSE_MAX_ROLL_DEG
                    yc = GREEN if py_ok else RED
                    pc = GREEN if pp_ok else RED
                    rc = GREEN if pr_ok else RED
                    checks.append(f"{yc}yaw{RESET}={yaw:+.1f}")
                    checks.append(f"{pc}pitch{RESET}={pitch:+.1f}")
                    checks.append(f"{rc}roll{RESET}={roll:+.1f}")
                else:
                    checks.append(f"{YELLOW}pose=None{RESET}")

                print(f"    [{fi}] {w_face:.0f}x{h_face:.0f} | " +
                      " | ".join(checks))
    else:
        print(f"  {YELLOW}YuNet no cargado{RESET}")

    return last_result


def main():
    if len(sys.argv) < 2:
        print("Uso: python test_demographics_diagnostico.py "
              "<imagen|carpeta> [camera_id]")
        print("  camera_id (opcional): aplica el perfil "
              "config/camera_profiles/<camera_id>.json (p.ej. cam12)")
        sys.exit(1)

    target = sys.argv[1]
    camera_id = sys.argv[2] if len(sys.argv) > 2 else None
    if os.path.isdir(target):
        images = sorted(glob.glob(os.path.join(target, '*.jpg')))[:5]
        # Tomar 5 frames muestreados uniformemente para diversidad
        if len(glob.glob(os.path.join(target, '*.jpg'))) > 5:
            all_imgs = sorted(glob.glob(os.path.join(target, '*.jpg')))
            step = len(all_imgs) // 5
            images = [all_imgs[i*step] for i in range(5)]
    else:
        images = [target]

    print(f"Cargando modelos...")
    models = load_all_classifiers()
    classifier = DemographicsClassifier(
        gender_net=models['gender_net'],
        age_net=models['age_net'],
        face_cascade=models['face_cascade'],
        face_detector_dnn=models['face_dnn'],
        insightface_session=models['insightface_session'],
        yunet=models['yunet'],
        mivolo_session=models['mivolo_session'],
        camera_id=camera_id,
    )
    print(f"Ensemble size: {classifier.ensemble_size} modelos")
    if camera_id:
        print(f"Perfil de camara: {camera_id} "
              f"(overrides: {sorted(classifier._cfg._overrides) or 'NINGUNO'})")

    # Cargar YOLO de personas
    person_model_path = os.path.join(
        PROJECT_ROOT, 'models', 'base', 'yolo11m.pt'
    )
    if not os.path.exists(person_model_path):
        person_model_path = 'yolo11m.pt'
    print(f"Cargando YOLO: {person_model_path}")
    person_model = YOLO(person_model_path)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    person_model.to(device)
    print(f"Device: {device}")

    print(f"\nConfig actual:")
    print(f"  DEMO_MIN_FACE_SIZE = {classifier._cfg.DEMO_MIN_FACE_SIZE}")
    print(f"  DEMO_MIN_PERSON_BBOX = "
          f"{classifier._cfg.DEMO_MIN_PERSON_BBOX_W}x"
          f"{classifier._cfg.DEMO_MIN_PERSON_BBOX_H}")
    print(f"  DEMO_YUNET_MIN_SCORE = {classifier._cfg.DEMO_YUNET_MIN_SCORE}")
    print(f"  DEMO_POSE_MAX_YAW = {classifier._cfg.DEMO_POSE_MAX_YAW_DEG}")
    print(f"  DEMO_POSE_MAX_PITCH = "
          f"{classifier._cfg.DEMO_POSE_MAX_PITCH_DEG}")
    print(f"  DEMO_MIN_SAMPLES = {classifier._cfg.DEMO_MIN_SAMPLES}")
    print(f"  DEMO_MIN_CONFIDENCE = "
          f"{classifier._cfg.DEMO_MIN_CONFIDENCE}")
    print(f"  DEMO_MIN_EYE_FACE_RATIO = "
          f"{classifier._cfg.DEMO_MIN_EYE_FACE_RATIO}")
    print(f"  DEMO_MAX_NOSE_OFFSET = "
          f"{classifier._cfg.DEMO_MAX_NOSE_OFFSET}")

    for img_path in images:
        diagnose_one_image(img_path, classifier, person_model)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
