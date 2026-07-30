"""
analytics/demographics.py - Clasificacion de genero y edad con PRECISION
MAXIMA (orientado a ~100% precision, sacrificando cobertura).

Filosofia de diseno:
  * Es preferible NO clasificar (devolver Desconocido) que equivocarse.
  * Cada muestra debe pasar TODOS los filtros (tamano, blur, pose,
    keypoints, score del detector) antes de contar para el voting.
  * Una sample = una inferencia ya validada con heavy-TTA interno.
  * Voting temporal requiere consenso fuerte de >=N muestras coherentes.

Pipeline (de input a label):
  1. Bbox de persona del tracker.
  2. Multi-crop region cabeza+torso.
  3. YuNet detecta cara + 5 keypoints (ojos, nariz, esquinas boca).
  4. Validacion geometrica: keypoints consistentes, score alto.
  5. Estimacion de pose (yaw/pitch/roll) via solvePnP con landmarks 3D
     canonicos. Rechazo si yaw>22deg, pitch>25deg, roll>35deg.
  6. Alineacion: rotacion para nivelar ojos + similarity warp a 112x112
     canonico (igual que ArcFace).
  7. Filtros de calidad: blur (Laplaciano), contraste, simetria, area.
  8. Heavy-TTA: 6 variantes (original, flip, zoom in, zoom out, rot+5,
     rot-5). Cada variante se infiere y se promedia el softmax.
     Si DEMO_TTA_REQUIRE_AGREEMENT=True, todas las variantes deben
     coincidir en el argmax de genero o la sample se descarta.
  9. Ensemble: si hay 2 modelos cargados (InsightFace + MiVOLO),
     ambos deben coincidir en genero. La edad se promedia con peso.
 10. Voting temporal: acumulador por track_id con trimmed mean.
 11. Commit solo si: samples>=8, confianza>=0.85, margen>=0.35,
     agreement_ratio>=0.85.
 12. Si tras 60 samples no converge -> Desconocido permanente.

Modelos soportados:
  * InsightFace genderage.onnx (96x96): primario, viene en el repo.
  * MiVOLO ONNX (224x224): opcional, descargar de
    https://github.com/WildChlamydia/MiVOLO. Si esta en
    models/classifiers/mivolo.onnx se activa el ensemble.
  * Caffe Levi-Hassner 2015: tercer modelo opcional (ya cargado para
    fallback). Si se pasa age_net/gender_net, participa en ensemble.
"""

import cv2
import json
import numpy as np
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, deque

from .config import AnalyticsConfig
from .telemetria_demografica import MotivoFinal, obtener_telemetria

logger = logging.getLogger(__name__)

# Raiz del repo: src/analityc/core/analytics/demographics.py -> 4 niveles up.
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', '..', '..', '..'))


class _CameraConfigView:
    """Vista de AnalyticsConfig con overrides POR CAMARA.

    Resuelve cada atributo primero en el dict de overrides del perfil de
    la camara y, si no esta, cae al valor global de AnalyticsConfig. Asi
    los umbrales del pipeline (deteccion de rostro, pose, calidad,
    tamanos) se AJUSTAN a las condiciones de UNA camara concreta (altura
    de montaje, gran angular, iluminacion) sin tocar la config global ni
    afectar al resto de camaras. Los classmethods de AnalyticsConfig
    (age_range_from_value, etc.) se resuelven igual via getattr.
    """

    def __init__(self, overrides: Optional[Dict[str, Any]] = None,
                 source: str = ""):
        self._overrides = dict(overrides or {})
        self._source = source

    def __getattr__(self, name: str):
        ov = object.__getattribute__(self, '_overrides')
        if name in ov:
            return ov[name]
        return getattr(AnalyticsConfig, name)


def _load_camera_profile(camera_id: Any) -> _CameraConfigView:
    """Carga el perfil demografico de la camara si existe.

    Busca config/camera_profiles/<camera_id>.json (el <camera_id> se
    sanitiza a [alnum_-], igual que los nombres de reporte). Las claves
    del JSON son nombres de atributos de AnalyticsConfig (p.ej.
    "DEMO_MIN_CONTRAST": 12.0); las que empiezan con '_' se ignoran
    (sirven de comentario) y las desconocidas se avisan y descartan.
    Sin archivo -> vista sin overrides (config global intacta).
    """
    if camera_id is None:
        return _CameraConfigView()
    safe = "".join(c for c in str(camera_id)
                   if c.isalnum() or c in "_-")[:64]
    if not safe:
        return _CameraConfigView()
    path = os.path.join(_PROJECT_ROOT, 'config', 'camera_profiles',
                        f"{safe}.json")
    if not os.path.exists(path):
        logger.debug("Sin perfil demografico para camara %s (%s); "
                     "uso config global", camera_id, path)
        return _CameraConfigView()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning("Perfil de camara %s ilegible (%s); uso config "
                       "global", camera_id, e)
        return _CameraConfigView()
    overrides: Dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k.startswith('_'):
            continue
        if not hasattr(AnalyticsConfig, k):
            logger.warning("Perfil camara %s: clave desconocida '%s' "
                           "(ignorada)", camera_id, k)
            continue
        overrides[k] = v
    if overrides:
        logger.info("Perfil demografico camara %s aplicado (%s): %s",
                    camera_id, os.path.basename(path),
                    ", ".join(sorted(overrides)))
    return _CameraConfigView(overrides, source=path)

_MEAN_VALUES = (78.4263377603, 87.7689143744, 114.895847746)
_GENDER_LIST = ['Hombre', 'Mujer']
# Centros de cada bin del modelo Caffe: (0-2), (4-6), (8-12), (15-20),
# (25-32), (38-43), (48-53), (60+)
_AGE_BIN_CENTERS = np.array([1.0, 5.0, 10.0, 17.5, 28.5, 40.5, 50.5, 70.0],
                            dtype=np.float64)

# Landmarks canonicos 3D para solvePnP (modelo aproximado de cara humana
# centrado en origen, en mm). Referencia: face_recognition / dlib.
# Orden: ojo_der, ojo_izq, nariz, boca_der, boca_izq.
_FACE_MODEL_3D = np.array([
    (-30.0,  35.0, -30.0),   # ojo derecho
    ( 30.0,  35.0, -30.0),   # ojo izquierdo
    (  0.0,   0.0,   0.0),   # nariz (origen)
    (-25.0, -30.0, -30.0),   # boca derecha
    ( 25.0, -30.0, -30.0),   # boca izquierda
], dtype=np.float64)

# Template canonico 5-puntos ArcFace para 112x112 (estandar InsightFace).
# Estos son los puntos DONDE deben caer los keypoints despues de alinear.
# Aplicar similarity transform de la cara detectada a esta plantilla
# mejora significativamente la precision de los modelos InsightFace
# (genderage y w600k para embeddings).
# Orden coincide con YuNet: ojo_der, ojo_izq, nariz, boca_der, boca_izq.
_ARCFACE_TEMPLATE_112 = np.array([
    [38.2946, 51.6963],  # ojo derecho
    [73.5318, 51.5014],  # ojo izquierdo
    [56.0252, 71.7366],  # nariz
    [41.5493, 92.3655],  # boca derecha
    [70.7299, 92.2041],  # boca izquierda
], dtype=np.float32)


def _align_face_arcface(image: np.ndarray,
                        src_kpts: np.ndarray,
                        output_size: int = 112) -> Optional[np.ndarray]:
    """Aplica alineacion ArcFace canonica via similarity transform.

    Args:
        image: imagen origen (BGR).
        src_kpts: array (5, 2) con keypoints detectados en orden:
                  ojo_der, ojo_izq, nariz, boca_der, boca_izq.
        output_size: tamano del crop alineado (112 default ArcFace).

    Returns:
        Imagen (output_size, output_size, 3) alineada, o None si falla.
    """
    if src_kpts.shape != (5, 2):
        return None
    # Si output_size != 112, escalar el template
    if output_size == 112:
        dst = _ARCFACE_TEMPLATE_112
    else:
        scale = output_size / 112.0
        dst = _ARCFACE_TEMPLATE_112 * scale

    # estimateAffinePartial2D calcula similarity transform (rotation +
    # translation + uniform scale). Mas robusto que estimateAffine2D
    # que permite shearing y distorsiona la cara.
    src = src_kpts.astype(np.float32)
    try:
        M, _ = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.LMEDS
        )
    except cv2.error:
        return None
    if M is None:
        return None
    # Interpolacion segun el sentido del remuestreo: al AMPLIAR una cara
    # pequena, INTER_LINEAR emborrona justo los bordes que definen ojos y
    # boca; CUBIC conserva ese detalle. Al reducir, LINEAR va bien y es
    # mas barato. La escala sale de la propia matriz de similaridad.
    escala = float(np.hypot(M[0, 0], M[1, 0]))
    flags = cv2.INTER_CUBIC if escala > 1.0 else cv2.INTER_LINEAR
    aligned = cv2.warpAffine(
        image, M, (output_size, output_size),
        flags=flags,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return aligned


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    e = np.exp(x)
    s = e.sum()
    return e / s if s > 0 else np.full_like(e, 1.0 / len(e))


def _estimate_head_pose(keypoints_2d: np.ndarray,
                        img_size: Tuple[int, int]
                        ) -> Optional[Tuple[float, float, float]]:
    """Estima yaw/pitch/roll en grados desde 5 keypoints YuNet.

    Args:
        keypoints_2d: array (5, 2) con [r_eye, l_eye, nose, r_mouth, l_mouth]
        img_size: (w, h) del crop donde estan los keypoints.

    Returns:
        (yaw, pitch, roll) en grados, o None si la estimacion fallo.
    """
    try:
        w, h = img_size
        focal_length = float(w)
        center = (w / 2.0, h / 2.0)
        camera_matrix = np.array([
            [focal_length, 0.0, center[0]],
            [0.0, focal_length, center[1]],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        # SOLVEPNP_SQPNP admite 3+ puntos (vs ITERATIVE que requiere 6).
        # SQPNP es ademas mas robusto numericamente. Fallback a EPNP.
        flags_priority = [
            getattr(cv2, 'SOLVEPNP_SQPNP', cv2.SOLVEPNP_EPNP),
            cv2.SOLVEPNP_EPNP,
        ]
        ok = False
        rvec = None
        for flag in flags_priority:
            try:
                ok, rvec, _tvec = cv2.solvePnP(
                    _FACE_MODEL_3D, keypoints_2d.astype(np.float64),
                    camera_matrix, dist_coeffs, flags=flag
                )
                if ok:
                    break
            except cv2.error:
                continue
        if not ok or rvec is None:
            return None

        rot_mat, _ = cv2.Rodrigues(rvec)
        # Extrae angulos euler de la matriz de rotacion (orden ZYX).
        sy = np.sqrt(rot_mat[0, 0] ** 2 + rot_mat[1, 0] ** 2)
        singular = sy < 1e-6
        if not singular:
            pitch = np.degrees(np.arctan2(rot_mat[2, 1], rot_mat[2, 2]))
            yaw = np.degrees(np.arctan2(-rot_mat[2, 0], sy))
            roll = np.degrees(np.arctan2(rot_mat[1, 0], rot_mat[0, 0]))
        else:
            pitch = np.degrees(np.arctan2(-rot_mat[1, 2], rot_mat[1, 1]))
            yaw = np.degrees(np.arctan2(-rot_mat[2, 0], sy))
            roll = 0.0

        # Normalizar a [-180, 180] y luego corregir signo del pitch
        # (solvePnP devuelve pitch invertido cuando la cara mira a camara).
        if pitch > 90:
            pitch -= 180
        elif pitch < -90:
            pitch += 180

        return float(yaw), float(pitch), float(roll)
    except Exception as e:
        logger.debug(f"Error solvePnP: {e}")
        return None


class _TrackAccumulator:
    """Acumula predicciones por track con voting robusto.

    Cada sample que entra al acumulador YA paso todos los filtros de
    calidad/pose/ensemble en _sample_once. El voting aqui es la ultima
    barrera: requiere que >= N samples coincidan en su argmax de genero
    antes de comprometer.
    """

    __slots__ = ("gender", "age", "samples", "committed", "result",
                 "give_up", "gender_hist", "age_hist", "weight_hist",
                 "age_value_hist", "last_gender_probs",
                 "face_width_hist", "max_face_width",
                 "n_face_samples", "n_body_samples",
                 "committed_gender_idx", "hyst_disagree", "post_commit_calls",
                 "_cfg")

    def __init__(self, cfg: Optional[_CameraConfigView] = None):
        # Config efectiva (global o con overrides del perfil de camara).
        self._cfg = cfg if cfg is not None else AnalyticsConfig
        self.gender = np.zeros(2, dtype=np.float64)
        self.age = np.zeros(8, dtype=np.float64)
        self.samples = 0
        self.committed = False
        self.result: Optional[Dict[str, str]] = None
        self.give_up = False
        max_hist = 30
        self.gender_hist: deque = deque(maxlen=max_hist)
        self.age_hist: deque = deque(maxlen=max_hist)
        self.weight_hist: deque = deque(maxlen=max_hist)
        # Edad escalar (anos) por sample, para detectar dispersion alta
        self.age_value_hist: deque = deque(maxlen=max_hist)
        self.last_gender_probs: Optional[np.ndarray] = None
        # Historial de ancho de cara observado por sample (px). Usado
        # para threshold adaptativo: caras mas grandes exigen mas certeza.
        self.face_width_hist: deque = deque(maxlen=max_hist)
        self.max_face_width: float = 0.0
        # Conteo de muestras por origen (rama facial E3A vs corporal E3B).
        # Si un track converge SOLO con muestras corporales, la edad se
        # reporta en taxonomia gruesa y age_source='body'.
        self.n_face_samples: int = 0
        self.n_body_samples: int = 0
        # ── Histeresis (Hito 5): correccion de un genero ya fijado ──
        # Indice de genero comprometido y conteo de muestras faciales
        # consecutivas de alta confianza que lo CONTRADICEN.
        self.committed_gender_idx: int = -1
        self.hyst_disagree: int = 0
        self.post_commit_calls: int = 0

    def add(self, gender_probs: np.ndarray, age_probs: np.ndarray,
            age_value: float, face_width: float = 0.0,
            weight: float = 1.0, decay: float = 1.0,
            source: str = "face"):
        if decay < 1.0:
            self.gender *= decay
            self.age *= decay
        self.gender += gender_probs * weight
        self.age += age_probs * weight
        self.samples += 1
        self.gender_hist.append(np.array(gender_probs, dtype=np.float64))
        self.age_hist.append(np.array(age_probs, dtype=np.float64))
        self.weight_hist.append(float(weight))
        self.age_value_hist.append(float(age_value))
        self.last_gender_probs = np.array(gender_probs, dtype=np.float64)
        self.face_width_hist.append(float(face_width))
        if face_width > self.max_face_width:
            self.max_face_width = float(face_width)
        if source == "body":
            self.n_body_samples += 1
        else:
            self.n_face_samples += 1

    @property
    def is_body_only(self) -> bool:
        """True si el track solo acumulo muestras corporales (sin cara)."""
        return self.n_body_samples > 0 and self.n_face_samples == 0

    def register_hysteresis(self, gender_probs: np.ndarray,
                            min_conf: float, min_samples: int) -> bool:
        """Procesa una muestra FACIAL post-commit para histeresis (Hito 5).

        Solo cuentan muestras de alta confianza que CONTRADICEN el genero
        fijado. Si se acumulan `min_samples` CONSECUTIVAS, invierte el genero
        comprometido y devuelve True. Una muestra que confirma el genero (o
        de baja confianza) RESETEA el contador: exige evidencia sostenida,
        nunca una sola muestra.
        """
        if not self.committed or self.result is None:
            return False
        idx = int(np.argmax(gender_probs))
        conf = float(gender_probs[idx])
        if conf < min_conf or idx == self.committed_gender_idx:
            self.hyst_disagree = 0
            return False
        self.hyst_disagree += 1
        if self.hyst_disagree < min_samples:
            return False
        # Evidencia sostenida suficiente -> invertir el genero fijado.
        self.hyst_disagree = 0
        self.committed_gender_idx = idx
        new_gender = _GENDER_LIST[idx]
        self.result["gender"] = new_gender
        self.result["gender_confidence"] = round(conf, 3)
        self.result["confidence"] = round(conf, 3)
        self.result["hysteresis_flipped"] = True
        category = "Hombres" if new_gender == "Hombre" else "Mujeres"
        if self.result.get("age_range") in ("0-12", "Nino"):
            category = "Ninos"
        self.result["category"] = category
        return True

    def _robust_gender(self) -> np.ndarray:
        """Trimmed mean ponderado del genero descartando 20% outliers."""
        if len(self.gender_hist) == 0:
            return np.full(2, 0.5)
        if len(self.gender_hist) < 3:
            w = np.array(self.weight_hist, dtype=np.float64)
            gh = np.stack(list(self.gender_hist))
            return (gh * w[:, None]).sum(axis=0) / max(w.sum(), 1e-9)

        gh = np.stack(list(self.gender_hist))
        w = np.array(self.weight_hist, dtype=np.float64)
        mean = (gh * w[:, None]).sum(axis=0) / max(w.sum(), 1e-9)
        dists = np.linalg.norm(gh - mean, axis=1)
        keep_n = max(2, int(round(len(gh) * 0.8)))
        keep_idx = np.argsort(dists)[:keep_n]
        gh_k = gh[keep_idx]
        w_k = w[keep_idx]
        return (gh_k * w_k[:, None]).sum(axis=0) / max(w_k.sum(), 1e-9)

    def _robust_age(self) -> np.ndarray:
        if len(self.age_hist) == 0:
            return np.full(8, 1.0 / 8.0)
        if len(self.age_hist) < 3:
            w = np.array(self.weight_hist, dtype=np.float64)
            ah = np.stack(list(self.age_hist))
            s = (ah * w[:, None]).sum(axis=0) / max(w.sum(), 1e-9)
            return s / max(s.sum(), 1e-9)

        ah = np.stack(list(self.age_hist))
        w = np.array(self.weight_hist, dtype=np.float64)
        mean = (ah * w[:, None]).sum(axis=0) / max(w.sum(), 1e-9)
        dists = np.linalg.norm(ah - mean, axis=1)
        keep_n = max(2, int(round(len(ah) * 0.8)))
        keep_idx = np.argsort(dists)[:keep_n]
        ah_k = ah[keep_idx]
        w_k = w[keep_idx]
        s = (ah_k * w_k[:, None]).sum(axis=0) / max(w_k.sum(), 1e-9)
        return s / max(s.sum(), 1e-9)

    def _try_fast_commit(self) -> Optional[Dict[str, str]]:
        """Intenta committear con UNA sola sample si tiene altisima
        confianza y la cara es de buen tamano. Resultado: clasificacion
        inmediata desde el primer frame en condiciones ideales."""
        # Usar la sample mas reciente (la primera si solo hay 1)
        last_g = self.gender_hist[-1]
        last_w = self.face_width_hist[-1]
        # Necesitamos cara de buen tamano
        if last_w < self._cfg.DEMO_FAST_COMMIT_MIN_FACE_W:
            return None
        top1 = float(last_g.max())
        top2 = float(last_g.min())  # solo 2 clases (Hombre/Mujer)
        if top1 < self._cfg.DEMO_FAST_COMMIT_MIN_CONFIDENCE:
            return None
        if (top1 - top2) < self._cfg.DEMO_FAST_COMMIT_MIN_MARGIN:
            return None

        top_idx = int(np.argmax(last_g))
        gender_label = _GENDER_LIST[top_idx]

        # Edad: usar la sample mas reciente (sin trimmed mean porque
        # hay solo 1 sample)
        last_age = self.age_hist[-1]
        last_age_value = self.age_value_hist[-1]
        a_sum = last_age.sum()
        a_normalized = (last_age / a_sum if a_sum > 0
                        else np.full(8, 1.0 / 8.0))
        expected_age = float(np.sum(a_normalized * _AGE_BIN_CENTERS))
        age_range_label = self._cfg.age_range_from_value(
            int(round(expected_age))
        )

        if gender_label == "Hombre":
            category = "Hombres"
        else:
            category = "Mujeres"
        if age_range_label == "0-12":
            category = "Ninos"

        self.committed = True
        self.committed_gender_idx = top_idx
        self.result = {
            "gender": gender_label,
            "age_range": age_range_label,
            "category": category,
            "confidence": round(top1, 3),
            "gender_confidence": round(top1, 3),
            "age_value": round(expected_age, 1),
            "age_std": 0.0,
            "samples": self.samples,
            "bracket": "fast-commit",
            "age_source": "face",
            "median_face_w": float(last_w),
            "fast_commit": True,
        }
        logger.info(
            "FAST-COMMIT: %s %s conf=%.3f face_w=%.0f (samples=%d)",
            gender_label, age_range_label, top1, last_w, self.samples
        )
        return self.result

    def _adaptive_thresholds(self, base_conf: float, base_margin: float
                             ) -> Tuple[float, float, str]:
        """Devuelve (min_conf, min_margin, bracket_label) según el ancho
        de cara MEDIANO observado en las samples acumuladas.

        Usa la mediana (no max) para que un frame con cara grande no
        eleve el threshold cuando casi todas las samples fueron de cara
        chica.
        """
        if not self._cfg.DEMO_ADAPTIVE_THRESHOLD:
            return base_conf, base_margin, "fixed"
        if not self.face_width_hist:
            return base_conf, base_margin, "no-face-data"
        # Excluir muestras CORPORALES (face_width=0): no tienen cara y
        # arrastrarian la mediana hacia 'face-too-small' en tracks mixtos.
        widths = np.array([w for w in self.face_width_hist if w > 0],
                          dtype=np.float64)
        if widths.size == 0:
            return base_conf, base_margin, "no-face-data"
        med_w = float(np.median(widths))
        brackets = self._cfg.DEMO_ADAPTIVE_BRACKETS
        # Brackets ya estan de mayor a menor face_w_min
        for face_w_min, mc, mm in brackets:
            if med_w >= face_w_min:
                return mc, mm, f"face>={face_w_min}px"
        # Si la cara es mas chica que el bracket minimo -> rechazar
        return 99.0, 99.0, "face-too-small"

    def try_commit(self, min_samples: int, min_conf: float,
                   min_margin: float) -> Optional[Dict[str, str]]:
        # ── Fast-commit: 1 sample con MUY alta confianza + cara grande ──
        # Permite clasificacion inmediata (1 frame) cuando el modelo
        # esta extremadamente seguro. Para casos dudosos se cae al
        # voting normal de min_samples.
        if (self._cfg.DEMO_FAST_COMMIT_ENABLED and
                self.samples >= 1 and not self.committed and
                len(self.gender_hist) >= 1):
            fast_result = self._try_fast_commit()
            if fast_result is not None:
                return fast_result

        if self.samples < min_samples:
            return None

        g = self._robust_gender()
        g_sum = g.sum()
        if g_sum <= 0:
            return None
        g = g / g_sum

        # Threshold para comprometer. La rama corporal (sin cara) no tiene
        # face_width, asi que el umbral adaptativo por tamano de cara no
        # aplica: usamos un umbral corporal fijo (mas laxo, objetivo ~85%).
        if self.is_body_only:
            eff_conf = self._cfg.DEMO_BODY_MIN_GENDER_CONF
            eff_margin = 0.20
            bracket = "body-only"
        else:
            eff_conf, eff_margin, bracket = self._adaptive_thresholds(
                min_conf, min_margin)

        g_sorted_idx = np.argsort(g)[::-1]
        top1_p = float(g[g_sorted_idx[0]])
        top2_p = float(g[g_sorted_idx[1]])
        if top1_p < eff_conf:
            return None
        if (top1_p - top2_p) < eff_margin:
            return None

        # Acuerdo entre samples individuales: >= AGREE_RATIO deben
        # predecir el mismo genero.
        if len(self.gender_hist) >= min_samples:
            top_idx = int(g_sorted_idx[0])
            agree = sum(
                1 for gs in self.gender_hist
                if int(np.argmax(gs)) == top_idx
            )
            agree_ratio = agree / max(1, len(self.gender_hist))
            if agree_ratio < self._cfg.DEMO_COMMIT_AGREE_RATIO:
                return None

        gender_label = _GENDER_LIST[int(g_sorted_idx[0])]

        # Edad: comprobar que la varianza entre samples no es desastrosa.
        # Si los samples varian >= 25 anos entre si, no comprometer edad.
        if len(self.age_value_hist) >= 4:
            age_arr = np.array(self.age_value_hist, dtype=np.float64)
            age_std = float(age_arr.std())
        else:
            age_std = 0.0

        a = self._robust_age()
        expected_age = float(np.sum(a * _AGE_BIN_CENTERS))

        # Si la dispersion es muy alta, marcamos edad incierta.
        age_uncertain = age_std > 18.0
        if age_uncertain:
            age_range_label = "Desconocido"
        else:
            age_range_label = self._cfg.age_range_from_value(
                int(round(expected_age))
            )

        # Origen de la clasificacion: si SOLO hubo muestras corporales, la
        # edad se reporta en taxonomia gruesa (Nino/Joven/Adulto/Mayor) y se
        # marca age_source='body' (la edad sin cara es inherentemente gruesa).
        age_source = "body" if self.is_body_only else "face"
        if age_source == "body" and not age_uncertain:
            age_range_label = self._cfg.coarse_age_from_value(
                expected_age)

        if gender_label == "Hombre":
            category = "Hombres"
        else:
            category = "Mujeres"
        if age_range_label in ("0-12", "Nino"):
            category = "Ninos"

        self.committed = True
        self.committed_gender_idx = int(g_sorted_idx[0])
        self.result = {
            "gender": gender_label,
            "age_range": age_range_label,
            "category": category,
            "confidence": round(top1_p, 3),
            "gender_confidence": round(top1_p, 3),
            "age_value": round(expected_age, 1),
            "age_std": round(age_std, 1),
            "samples": self.samples,
            "bracket": bracket,
            "age_source": age_source,
            "n_face_samples": self.n_face_samples,
            "n_body_samples": self.n_body_samples,
            "median_face_w": round(
                float(np.median(list(self.face_width_hist)))
                if self.face_width_hist else 0.0, 1),
        }
        return self.result


class DemographicsClassifier:
    """Clasificador de genero y rango de edad con precision maxima.

    Multi-frame voting + ensemble multi-modelo + heavy-TTA + pose filter.
    Devuelve "Desconocido" agresivamente cuando hay duda.
    """

    def __init__(self, gender_net=None, age_net=None, face_cascade=None,
                 face_detector_dnn=None, insightface_session=None,
                 yunet=None, mivolo_session=None, reidentifier=None,
                 par_session=None, camera_id=None):
        # Config efectiva de ESTA camara: AnalyticsConfig global + overrides
        # de config/camera_profiles/<camera_id>.json si existe. Permite
        # calibrar rostro/pose/calidad por camara (montaje, lente, luz).
        self._camera_id = camera_id
        self._cfg = _load_camera_profile(camera_id)
        self._gender_net = gender_net
        self._age_net = age_net
        self._face_cascade = face_cascade
        self._face_dnn = face_detector_dnn
        self._yunet = yunet
        # Face Re-Identifier (opcional). Si esta presente, cada cara
        # aceptada se registra en su DB y se reusa para identificar
        # tracks que correspondan a personas ya vistas.
        self._reidentifier = reidentifier

        # ── Rama CORPORAL E3B: modelo PAR (solo genero) opcional ──
        # Si esta presente clasifica genero desde el cuerpo cuando NO hay
        # cara. Si no, la rama corporal intenta usar MiVOLO body-only.
        self._par = par_session
        if self._par is not None:
            try:
                self._par_input = self._par.get_inputs()[0].name
                self._par_output = [o.name for o in self._par.get_outputs()]
                logger.info("PAR (rama corporal, solo genero) activo")
            except Exception as e:
                logger.warning("PAR session invalida: %s", e)
                self._par = None
                self._par_input = None
                self._par_output = None
        else:
            self._par_input = None
            self._par_output = None

        # InsightFace ONNX session (primario)
        self._insight = insightface_session
        if self._insight is not None:
            try:
                self._insight_input = self._insight.get_inputs()[0].name
                self._insight_output = [
                    o.name for o in self._insight.get_outputs()
                ]
            except Exception:
                self._insight = None
                self._insight_input = None
                self._insight_output = None
        else:
            self._insight_input = None
            self._insight_output = None

        # MiVOLO ONNX session (opcional, SOTA 2024)
        self._mivolo = mivolo_session
        if self._mivolo is not None:
            try:
                self._mivolo_input = self._mivolo.get_inputs()[0].name
                self._mivolo_output = [
                    o.name for o in self._mivolo.get_outputs()
                ]
                logger.info("MiVOLO activo para ensemble")
            except Exception as e:
                logger.warning(f"MiVOLO session invalida: {e}")
                self._mivolo = None
                self._mivolo_input = None
                self._mivolo_output = None
        else:
            self._mivolo_input = None
            self._mivolo_output = None

        # Accumulator por track_id
        self._accum: Dict[int, _TrackAccumulator] = {}
        self._no_face_counter: Dict[int, int] = {}
        # Contadores de rechazo por motivo (para diagnostico)
        self._pose_reject_counter: Dict[int, int] = {}
        self._quality_reject_counter: Dict[int, int] = {}
        self._ensemble_reject_counter: Dict[int, int] = {}
        self._tta_reject_counter: Dict[int, int] = {}

        # ── Instrumentacion de calidad (Hito 3) ──────────────────────────
        # Demuestra de forma auditable que NINGUNA muestra borrosa entra al
        # voting: registramos el blur (varianza Laplaciano) de cada muestra
        # ACEPTADA y el desglose agregado de rechazos por motivo.
        self._q_accepted: int = 0
        self._q_accepted_blur_min: float = float("inf")
        self._q_accepted_blur_sum: float = 0.0
        self._q_accepted_body: int = 0   # muestras corporales (E3B) aceptadas
        self._q_reject: defaultdict = defaultdict(int)

        # ── Telemetria por track (Hito 2) ──────────────────────────────
        # Solo observan; ninguno participa en decisiones del pipeline.
        self._telemetria = obtener_telemetria()
        self._camara_id = camera_id
        # Rostros vistos pero descartados por tamano (bracket < 25 px).
        self._pequeno_reject_counter: Dict[int, int] = {}
        # Mayor ancho de rostro NATIVO visto por track (px reales).
        self._mejor_ancho_rostro: Dict[int, float] = {}
        # Tracks en los que el estimador lanzo una excepcion.
        self._tracks_con_excepcion: set = set()
        # Tracks cuya demografia se heredo del Re-ID (no se estimo aqui).
        self._tracks_heredados: set = set()
        # Tracks que ni se intentaron por falta de modelo cargado.
        self._sin_modelo_counter: Dict[int, int] = {}

        # ── Rama CORPORAL: MiVOLO v2 (Hito 4) ──────────────────────────
        # Es la ruta PRINCIPAL, no un fallback: 4 de cada 5 personas de
        # estas camaras no muestran rostro utilizable. Instancia
        # compartida, se carga una sola vez por proceso.
        self._estimador_cuerpo = None
        try:
            from .estimador_edad_genero import obtener_estimador
            estimador = obtener_estimador()
            if estimador.disponible:
                self._estimador_cuerpo = estimador
                logger.info("Rama corporal ACTIVA con MiVOLO v2 (%s)",
                            estimador.backend)
            else:
                logger.warning(
                    "MiVOLO v2 no disponible: las personas SIN ROSTRO no "
                    "podran clasificarse (es el 79 % de los casos)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo preparar la rama corporal: %s", exc)

        # Counters solo se actualizan cuando un track se compromete
        self._gender_counts = defaultdict(int)
        self._age_counts = defaultdict(int)
        self._combined_counts = defaultdict(int)
        # Acotado: en demos largas crecia sin limite (1 entrada por commit).
        self._history: deque = deque(maxlen=5000)

    @property
    def is_available(self) -> bool:
        return (self._insight is not None or self._gender_net is not None
                or self._mivolo is not None or self._par is not None)

    @property
    def ensemble_size(self) -> int:
        n = 0
        if self._insight is not None:
            n += 1
        if self._mivolo is not None:
            n += 1
        if self._gender_net is not None:
            n += 1
        return n

    # ── Clasificacion publica ─────────────────────────────────────

    def classify(self, frame: np.ndarray, bbox: tuple,
                 track_id: int) -> Dict[str, str]:
        acc = self._accum.get(track_id)
        if acc is None:
            acc = _TrackAccumulator(self._cfg)
            self._accum[track_id] = acc

        if acc.committed and acc.result is not None:
            # Lock: por defecto NO se reclasifica (cero parpadeo, ahorra GPU).
            # Si la histeresis esta activa, se permite CORREGIR el genero con
            # evidencia facial fuerte y sostenida (ver _maybe_hysteresis).
            if self._cfg.DEMO_HYSTERESIS_ENABLED:
                self._maybe_hysteresis(frame, bbox, acc, track_id)
            return acc.result

        # Si el reidentifier tiene este track mapeado a una persona ya
        # clasificada anteriormente, reusar esos demograficos sin
        # esperar a que el accumulator converja con la cara actual.
        if (self._reidentifier is not None
                and self._reidentifier.is_available):
            reid_demo = self._reidentifier.get_demographics_for_person(
                track_id
            )
            if (reid_demo is not None
                    and reid_demo.get('gender') not in (None, 'Desconocido')
                    and reid_demo.get('demo_confidence', 0.0)
                    >= self._cfg.REID_MIN_DEMO_CONFIDENCE):
                # Esta persona ya fue clasificada en una visita anterior
                # o en otro track. Reusar resultado y marcar como committed.
                if not acc.committed:
                    self._commit_from_reid(acc, reid_demo, track_id)
                return acc.result

        if acc.give_up:
            return self._unknown_result(acc.samples)

        self._sample_once(frame, bbox, acc, track_id)

        committed = acc.try_commit(
            self._cfg.DEMO_MIN_SAMPLES,
            self._cfg.DEMO_MIN_CONFIDENCE,
            self._cfg.DEMO_MIN_MARGIN,
        )

        if committed is not None:
            self._gender_counts[committed["gender"]] += 1
            self._age_counts[committed["age_range"]] += 1
            self._combined_counts[
                f"{committed['gender']}_{committed['age_range']}"
            ] += 1
            self._history.append({"track_id": track_id, **committed})
            # Update reidentifier con demograficos finales del track
            if (self._reidentifier is not None
                    and self._reidentifier.is_available
                    and committed.get('confidence', 0.0)
                    >= self._cfg.REID_MIN_DEMO_CONFIDENCE):
                reid_demo = self._reidentifier.get_demographics_for_person(
                    track_id
                )
                if reid_demo is not None:
                    # Persona ya en DB; actualizar con confianza final
                    self._reidentifier.identify_or_register(
                        track_id=track_id,
                        face_bgr=np.zeros((0,), dtype=np.uint8),
                        gender=committed.get('gender'),
                        age_range=committed.get('age_range'),
                        age_value=committed.get('age_value', 0.0),
                        demo_confidence=committed.get('confidence', 0.0),
                    )
                    # Incluir person_uuid en el resultado
                    committed['person_uuid'] = reid_demo.get(
                        'person_uuid', ''
                    )
            return committed

        # Modo precision maxima: NO se devuelve provisional. Solo commit
        # final o Desconocido. Esto evita que el overlay muestre etiquetas
        # erroneas en los primeros frames.
        if self._cfg.DEMO_SHOW_PROVISIONAL and acc.samples >= 1:
            provisional = self._provisional_result(acc)
            if provisional is not None:
                return provisional

        if acc.samples >= self._cfg.DEMO_MAX_SAMPLES_BEFORE_GIVEUP:
            acc.give_up = True
            logger.debug(
                f"Track {track_id}: {acc.samples} samples sin "
                f"convergencia -> Desconocido permanente."
            )

        return self._unknown_result(acc.samples)

    def _maybe_hysteresis(self, frame: np.ndarray, bbox: tuple,
                          acc: "_TrackAccumulator", track_id: int) -> None:
        """Re-muestrea FACIALMENTE a baja cadencia un track ya fijado para
        permitir correccion por evidencia sostenida (Hito 5).

        No usa la rama corporal: corregir una etiqueta fijada exige cara.
        Solo invierte tras N muestras faciales de alta confianza que
        contradicen el genero (ver `_TrackAccumulator.register_hysteresis`).
        """
        acc.post_commit_calls += 1
        every = max(1, self._cfg.DEMO_HYSTERESIS_RESAMPLE_EVERY)
        if acc.post_commit_calls % every != 0:
            return
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return
        face, _pose, _nw = self._detect_face_with_pose(crop)
        if face is None or not self._face_passes_quality(face):
            return
        try:
            gender_p, _age_p, _av = self._infer_ensemble(face, track_id)
        except Exception as exc:
            # Antes se tragaba sin dejar rastro (hipotesis H4 de la
            # auditoria). Se sigue devolviendo igual, pero ahora consta.
            self._tracks_con_excepcion.add(track_id)
            self._telemetria.registrar_excepcion("histeresis", exc)
            return
        if gender_p is None:
            return
        old_gender = acc.result.get("gender") if acc.result else None
        flipped = acc.register_hysteresis(
            gender_p,
            self._cfg.DEMO_HYSTERESIS_MIN_CONFIDENCE,
            self._cfg.DEMO_HYSTERESIS_MIN_SAMPLES,
        )
        if flipped and acc.result is not None:
            new_gender = acc.result.get("gender")
            # Mantener consistentes los contadores agregados.
            if (old_gender and old_gender in self._gender_counts
                    and self._gender_counts[old_gender] > 0):
                self._gender_counts[old_gender] -= 1
            if new_gender:
                self._gender_counts[new_gender] += 1
            logger.info(
                "HISTERESIS: track %s genero corregido %s -> %s",
                track_id, old_gender, new_gender)
            # Propagar la correccion a la DB de re-identificacion para que
            # la persona no vuelva a heredar el genero erroneo en su
            # proxima visita.
            if (self._reidentifier is not None
                    and self._reidentifier.is_available):
                try:
                    self._reidentifier.identify_or_register(
                        track_id=track_id,
                        face_bgr=face,
                        gender=new_gender,
                        age_range=acc.result.get("age_range"),
                        age_value=acc.result.get("age_value", 0.0),
                        demo_confidence=acc.result.get(
                            "gender_confidence", 0.0),
                    )
                except Exception as exc:
                    logger.debug("Reid update tras histeresis: %s", exc)

    def _provisional_result(self, acc: "_TrackAccumulator"
                            ) -> Optional[Dict[str, str]]:
        g = acc._robust_gender()
        g_sum = g.sum()
        if g_sum <= 0:
            return None
        g = g / g_sum

        g_sorted = np.argsort(g)[::-1]
        top1_idx = int(g_sorted[0])
        top1_p = float(g[top1_idx])
        top2_p = float(g[int(g_sorted[1])])

        min_conf = self._cfg.DEMO_PROVISIONAL_MIN_CONFIDENCE
        min_margin = self._cfg.DEMO_PROVISIONAL_MIN_MARGIN

        agree_ratio = 1.0
        if len(acc.gender_hist) >= 3:
            agree = sum(
                1 for gs in acc.gender_hist
                if int(np.argmax(gs)) == top1_idx
            )
            agree_ratio = agree / max(1, len(acc.gender_hist))

        if (top1_p >= min_conf and
                (top1_p - top2_p) >= min_margin and
                agree_ratio >= self._cfg.DEMO_PROVISIONAL_AGREE_RATIO):
            gender_label = _GENDER_LIST[top1_idx]
        else:
            gender_label = "Desconocido"

        a = acc._robust_age()
        expected_age = float(np.sum(a * _AGE_BIN_CENTERS))
        age_range_label = self._cfg.age_range_from_value(
            int(round(expected_age))
        )

        if gender_label == "Hombre":
            category = "Hombres"
        elif gender_label == "Mujer":
            category = "Mujeres"
        else:
            category = "Desconocido"
        if age_range_label == "0-12" and gender_label != "Desconocido":
            category = "Ninos"

        return {
            "gender": gender_label,
            "age_range": (age_range_label if gender_label != "Desconocido"
                          else "Desconocido"),
            "category": category,
            "confidence": round(top1_p, 3),
            "age_value": round(expected_age, 1),
            "samples": acc.samples,
            "provisional": True,
        }

    def _unknown_result(self, samples: int) -> Dict[str, str]:
        return {
            "gender": "Desconocido",
            "age_range": "Desconocido",
            "category": "Desconocido",
            "confidence": 0.0,
            "age_value": 0.0,
            "samples": samples,
        }

    def _commit_from_reid(self, acc: _TrackAccumulator,
                          reid_demo: Dict[str, Any], track_id: int):
        """Commitea un accumulator usando demograficos de re-identificacion
        previa. Util cuando una persona ya conocida vuelve a aparecer.
        """
        gender = reid_demo.get('gender', 'Desconocido')
        age_range = reid_demo.get('age_range', 'Desconocido')
        if gender == 'Hombre':
            category = 'Hombres'
        elif gender == 'Mujer':
            category = 'Mujeres'
        else:
            category = 'Desconocido'
        if age_range == '0-12' and gender != 'Desconocido':
            category = 'Ninos'

        # Telemetria: este track NO se estimo, hereda de una visita previa.
        self._tracks_heredados.add(track_id)

        acc.committed = True
        acc.committed_gender_idx = (
            _GENDER_LIST.index(gender) if gender in _GENDER_LIST else -1)
        acc.result = {
            "gender": gender,
            "age_range": age_range,
            "category": category,
            "confidence": float(reid_demo.get('demo_confidence', 0.0)),
            "gender_confidence": float(reid_demo.get('demo_confidence', 0.0)),
            "age_source": "reid",
            "age_value": float(reid_demo.get('age_value', 0.0)),
            "samples": 0,
            "bracket": "from_reid",
            "person_uuid": reid_demo.get('person_uuid', ''),
            "visit_count": int(reid_demo.get('visit_count', 1)),
            "from_reid": True,
        }
        # No incrementar gender_counts: ya se conto cuando la persona
        # fue clasificada por primera vez.
        logger.info(
            "Track %s reusa demograficos de re-identificacion: %s",
            track_id, acc.result
        )

    # ── Sampling de un frame ──────────────────────────────────────

    def _sample_once(self, frame: np.ndarray, bbox: tuple,
                     acc: _TrackAccumulator, track_id: int):
        if (self._gender_net is None and self._insight is None
                and self._mivolo is None):
            # Sin ningun modelo cargado no hay nada que intentar. Antes se
            # salia en silencio; ahora queda constancia, porque este caso
            # es indistinguible de "la escena no daba" mirando el JSON.
            self._q_reject["sin_modelo_cargado"] += 1
            self._sin_modelo_counter[track_id] = (
                self._sin_modelo_counter.get(track_id, 0) + 1)
            return

        x1, y1, x2, y2 = [int(v) for v in bbox]
        h_frame, w_frame = frame.shape[:2]

        person_w = x2 - x1
        person_h = y2 - y1
        if (person_w < self._cfg.DEMO_MIN_PERSON_BBOX_W or
                person_h < self._cfg.DEMO_MIN_PERSON_BBOX_H):
            if self._cfg.DEMO_DEBUG_REJECTIONS and acc.samples == 0:
                if id(acc) not in self._no_face_counter:
                    logger.warning(
                        "Persona muy lejana: bbox=%dx%d (min=%dx%d).",
                        person_w, person_h,
                        self._cfg.DEMO_MIN_PERSON_BBOX_W,
                        self._cfg.DEMO_MIN_PERSON_BBOX_H,
                    )
            self._q_reject["bbox_pequeno"] += 1
            self._no_face_counter[id(acc)] = (
                self._no_face_counter.get(id(acc), 0) + 1
            )
            return

        person_h = max(1, person_h)
        mx = int(person_w * 0.25)

        crops_to_try = []
        ty2 = y1 + int(person_h * 0.55)
        t_x1 = max(0, x1 - mx)
        t_y1 = max(0, y1 - int(person_h * 0.20))
        t_x2 = min(w_frame, x2 + mx)
        t_y2 = min(h_frame, ty2)
        if t_x2 - t_x1 >= 20 and t_y2 - t_y1 >= 20:
            crops_to_try.append(frame[t_y1:t_y2, t_x1:t_x2])

        m_x1 = max(0, x1 - mx)
        m_y1 = max(0, y1 - int(person_h * 0.15))
        m_x2 = min(w_frame, x2 + mx)
        m_y2 = min(h_frame, y1 + int(person_h * 0.70))
        if m_x2 - m_x1 >= 20 and m_y2 - m_y1 >= 20:
            crops_to_try.append(frame[m_y1:m_y2, m_x1:m_x2])

        f_x1 = max(0, x1 - mx)
        f_y1 = max(0, y1 - int(person_h * 0.25))
        f_x2 = min(w_frame, x2 + mx)
        f_y2 = min(h_frame, y2)
        if f_x2 - f_x1 >= 20 and f_y2 - f_y1 >= 20:
            crops_to_try.append(frame[f_y1:f_y2, f_x1:f_x2])

        face = None
        pose = None
        native_face_w = 0.0
        for crop in crops_to_try:
            face, pose, native_face_w = self._detect_face_with_pose(crop)
            if face is not None and face.size > 0:
                break
        if face is None:
            # ── E3B: rama CORPORAL (clasificar genero/edad SIN cara) ──
            # De espaldas / cenital / perfil extremo no hay cara: intentamos
            # clasificar desde el cuerpo completo. Si aporta una muestra,
            # damos por procesado este frame.
            if self._try_body_sample(frame, bbox, acc, track_id):
                return
            self._q_reject["sin_cara"] += 1
            nf = self._no_face_counter.get(id(acc), 0) + 1
            self._no_face_counter[id(acc)] = nf
            if acc.samples == 0 and (nf % 20) == 0:
                logger.warning(
                    "Cara no detectada (samples=%d) bbox=%s",
                    acc.samples, (x1, y1, x2, y2)
                )
            # BACKOFF: si NUNCA se logro una muestra facial (samples==0) tras
            # muchos intentos sin cara, rendirse para no gastar YuNet/CPU en
            # cada frame (camara lejana/cenital sin modelo corporal). Esto
            # libera CPU -> sube el FPS. Tracks con al menos una muestra
            # facial NO se rinden por esta via (convergen normalmente).
            if (acc.samples == 0 and not acc.give_up and
                    nf >= self._cfg.DEMO_MAX_NOFACE_BEFORE_GIVEUP):
                acc.give_up = True
                logger.debug(
                    "Track %s: %d intentos sin cara -> backoff (Desconocido)",
                    track_id, nf,
                )
            return

        # Filtro de pose: solo caras casi frontales
        if pose is not None:
            yaw, pitch, roll = pose
            if abs(yaw) > self._cfg.DEMO_POSE_MAX_YAW_DEG:
                if self._cfg.DEMO_DEBUG_REJECTIONS:
                    logger.info("RECHAZO pose yaw=%.1f > %.1f",
                                yaw, self._cfg.DEMO_POSE_MAX_YAW_DEG)
                self._pose_reject_counter[track_id] = (
                    self._pose_reject_counter.get(track_id, 0) + 1)
                self._q_reject["pose"] += 1
                # CASCADA: la pose no vale para la rama facial estricta,
                # pero MiVOLO puede aprovechar cara+cuerpo igualmente.
                self._try_body_sample(frame, bbox, acc, track_id, cara=face)
                return
            if abs(pitch) > self._cfg.DEMO_POSE_MAX_PITCH_DEG:
                if self._cfg.DEMO_DEBUG_REJECTIONS:
                    logger.info("RECHAZO pose pitch=%.1f > %.1f",
                                pitch,
                                self._cfg.DEMO_POSE_MAX_PITCH_DEG)
                self._pose_reject_counter[track_id] = (
                    self._pose_reject_counter.get(track_id, 0) + 1)
                self._q_reject["pose"] += 1
                # CASCADA: la pose no vale para la rama facial estricta,
                # pero MiVOLO puede aprovechar cara+cuerpo igualmente.
                self._try_body_sample(frame, bbox, acc, track_id, cara=face)
                return
            if abs(roll) > self._cfg.DEMO_POSE_MAX_ROLL_DEG:
                if self._cfg.DEMO_DEBUG_REJECTIONS:
                    logger.info("RECHAZO pose roll=%.1f > %.1f",
                                roll, self._cfg.DEMO_POSE_MAX_ROLL_DEG)
                self._pose_reject_counter[track_id] = (
                    self._pose_reject_counter.get(track_id, 0) + 1)
                self._q_reject["pose"] += 1
                # CASCADA: la pose no vale para la rama facial estricta,
                # pero MiVOLO puede aprovechar cara+cuerpo igualmente.
                self._try_body_sample(frame, bbox, acc, track_id, cara=face)
                return

        # ── Telemetria: rostro visto y su tamano REAL ──
        # Se anota ANTES de los gates de calidad para poder distinguir
        # "no habia cara" de "habia cara pero era diminuta".
        if native_face_w > 0:
            if native_face_w > self._mejor_ancho_rostro.get(track_id, 0.0):
                self._mejor_ancho_rostro[track_id] = float(native_face_w)
            # Umbral del bracket mas bajo: por debajo, el commit es imposible.
            brackets = self._cfg.DEMO_ADAPTIVE_BRACKETS
            minimo_util = min(b[0] for b in brackets) if brackets else 0
            if native_face_w < minimo_util:
                self._pequeno_reject_counter[track_id] = (
                    self._pequeno_reject_counter.get(track_id, 0) + 1)

        if not self._face_passes_quality(face):
            # Aqui se descartan, entre otras, las caras BORROSAS
            # (blur_var < DEMO_MIN_BLUR_VAR). Por eso ninguna muestra
            # borrosa llega nunca al acumulador.
            self._q_reject["calidad"] += 1
            self._quality_reject_counter[track_id] = (
                self._quality_reject_counter.get(track_id, 0) + 1)
            # CASCADA: rostro insuficiente para el clasificador facial, no
            # necesariamente inutil para MiVOLO en entrada dual.
            self._try_body_sample(frame, bbox, acc, track_id, cara=face)
            return

        # Inferencia con heavy-TTA y ensemble
        try:
            gender_p, age_p, age_value = self._infer_ensemble(face, track_id)
            if gender_p is None:
                # Ensemble o TTA disagreement -> sample descartada
                self._q_reject["ensemble_tta"] += 1
                return
        except Exception as e:
            logger.debug(f"Error en inferencia: {e}")
            self._q_reject["error_inferencia"] += 1
            self._tracks_con_excepcion.add(track_id)
            self._telemetria.registrar_excepcion("_infer_ensemble", e)
            return

        # Peso de la sample: tamano + frontalidad + nitidez
        fh, fw = face.shape[:2]
        size_factor = min(1.0, (fh * fw) / (112.0 * 112.0))
        frontal_score = self._face_frontal_score(face)
        try:
            gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
            blur_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            blur_factor = min(1.0, blur_var / 200.0)
        except Exception:
            blur_var = -1.0
            blur_factor = 0.5

        # Bonus si la pose es muy buena (cara casi perfectamente frontal)
        pose_factor = 1.0
        if pose is not None:
            yaw, pitch, _ = pose
            pose_factor = max(0.4, 1.0 -
                              (abs(yaw) + abs(pitch)) / 60.0)

        weight = (
            0.25 * size_factor
            + 0.30 * frontal_score
            + 0.20 * blur_factor
            + 0.25 * pose_factor
        )
        weight = max(0.25, weight)
        # Multiplicador base facial (configurable). Hace explicito el ratio
        # facial(1.0)/corporal(0.5) del voting ponderado.
        weight *= self._cfg.DEMO_FACE_SAMPLE_WEIGHT

        # Pasar el ancho NATIVO de la cara (no el del crop alineado a
        # 112x112). Esto permite que el threshold adaptativo razone con
        # la informacion REAL disponible.
        acc.add(gender_p, age_p, age_value,
                face_width=float(native_face_w if native_face_w > 0
                                  else fw),
                weight=weight, decay=self._cfg.DEMO_DECAY)

        # ── Instrumentacion (Hito 3): muestra ACEPTADA ──
        # La cara ya paso _face_passes_quality, que exige
        # blur_var >= DEMO_MIN_BLUR_VAR. Registramos su blur para poder
        # demostrar que el minimo aceptado nunca cae por debajo del umbral.
        if blur_var >= 0.0:
            self._q_accepted += 1
            self._q_accepted_blur_sum += blur_var
            if blur_var < self._q_accepted_blur_min:
                self._q_accepted_blur_min = blur_var

        # Face Re-Identification: registrar embedding ahora que tenemos
        # una cara de calidad aceptada. Si la persona ya estaba en la DB,
        # esto la re-identifica y nos da un person_uuid estable.
        if (self._reidentifier is not None
                and self._reidentifier.is_available):
            # Pasamos demograficos solo si tenemos confianza suficiente
            # (despues del primer commit). Sino guardamos None y se
            # rellenara despues.
            current_gender = None
            current_age_range = None
            current_age_value = 0.0
            current_conf = 0.0
            if acc.committed and acc.result:
                current_gender = acc.result.get('gender')
                current_age_range = acc.result.get('age_range')
                current_age_value = acc.result.get('age_value', 0.0)
                current_conf = acc.result.get('confidence', 0.0)
            # Extraer yaw/pitch de pose para pasar al reidentifier
            face_yaw_val = None
            face_pitch_val = None
            if pose is not None:
                face_yaw_val = float(pose[0])
                face_pitch_val = float(pose[1])
            try:
                result = self._reidentifier.identify_or_register(
                    track_id=track_id,
                    face_bgr=face,
                    gender=current_gender,
                    age_range=current_age_range,
                    age_value=current_age_value,
                    demo_confidence=current_conf,
                    face_yaw=face_yaw_val,
                    face_pitch=face_pitch_val,
                )
                # Si fue RE-IDENTIFICADA (no nueva) y tiene demograficos
                # almacenados con buena confianza -> commitear ya, sin
                # esperar a acumular 8 samples. Asi la persona conocida
                # se etiqueta correctamente desde el primer frame.
                if (result is not None and not acc.committed):
                    person_uuid, is_new, rec = result
                    if (not is_new
                            and rec.get('gender') not in (None, 'Desconocido')
                            and rec.get('demo_confidence', 0.0)
                            >= self._cfg.REID_MIN_DEMO_CONFIDENCE):
                        reid_demo = {
                            'person_uuid': person_uuid,
                            'gender': rec.get('gender'),
                            'age_range': rec.get('age_range'),
                            'age_value': rec.get('age_value', 0.0),
                            'demo_confidence': rec.get(
                                'demo_confidence', 0.0),
                            'visit_count': rec.get('visit_count', 1),
                        }
                        self._commit_from_reid(acc, reid_demo, track_id)
            except Exception as e:
                logger.debug(f"Reidentifier error: {e}")

    # ── Inferencia con ensemble + heavy-TTA ─────────────────────

    def _infer_ensemble(
        self, face: np.ndarray, track_id: Optional[int] = None
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], float]:
        """Ensemble multi-modelo con heavy-TTA y verificacion de acuerdo.

        Returns (gender_probs, age_probs, age_years_scalar) o
        (None, None, 0) si los modelos discrepan y se rechaza la sample.
        """
        results = []

        # Modelo 1: InsightFace ONNX (primario, ~95% accuracy)
        if self._insight is not None:
            g, a, av = self._infer_insightface_tta(face)
            if g is not None:
                results.append(("insight", g, a, av))

        # Modelo 2: MiVOLO ONNX (SOTA 2024 opcional)
        if self._mivolo is not None:
            g, a, av = self._infer_mivolo_tta(face)
            if g is not None:
                results.append(("mivolo", g, a, av))

        # Modelo 3: Caffe Levi-Hassner (2015, ~70% accuracy).
        # SOLO se usa cuando ningun modelo moderno esta disponible. Si hay
        # InsightFace o MiVOLO, Caffe queda fuera del ensemble porque su
        # ruido degrada el acuerdo y descarta samples validas.
        has_modern_model = (self._insight is not None
                            or self._mivolo is not None)
        if (not has_modern_model and
                self._gender_net is not None
                and self._age_net is not None):
            g, a, av = self._infer_caffe_tta(face)
            if g is not None:
                results.append(("caffe", g, a, av))

        if not results:
            return None, None, 0.0

        # Si hay >=2 modelos y se exige acuerdo, validar que todos
        # predigan el mismo genero antes de combinar.
        if (len(results) >= 2 and
                self._cfg.DEMO_REQUIRE_ENSEMBLE_AGREEMENT):
            argmaxes = [int(np.argmax(r[1])) for r in results]
            if len(set(argmaxes)) > 1:
                if self._cfg.DEMO_DEBUG_REJECTIONS:
                    detail = ", ".join(
                        f"{r[0]}={_GENDER_LIST[int(np.argmax(r[1]))]}"
                        f"({float(r[1].max()):.2f})"
                        for r in results
                    )
                    logger.info(
                        "ENSEMBLE DISAGREEMENT genero: %s -> sample descartada",
                        detail
                    )
                if track_id is not None:
                    self._ensemble_reject_counter[track_id] = (
                        self._ensemble_reject_counter.get(track_id, 0) + 1)
                return None, None, 0.0

            # Edad: si los modelos disienten mucho, marcar edad como
            # incierta (no descartar el sample, pero no fiarse de edad).
            ages = [r[3] for r in results]
            if (max(ages) - min(ages)
                    > self._cfg.DEMO_MAX_AGE_DISAGREEMENT_YEARS):
                # Aplanamos la distribucion de edad para que no influya
                age_avg = float(np.mean(ages))
                age_arr = np.ones(8, dtype=np.float64) / 8.0
                # Aun asi ponemos los gender_p del promedio
                weights_g = self._ensemble_weights(results)
                gender_p = np.zeros(2, dtype=np.float64)
                for (name, gp, _ap, _av), wgt in zip(results, weights_g):
                    gender_p += gp * wgt
                gender_p /= max(weights_g.sum(), 1e-9)
                return gender_p, age_arr, age_avg

        # Combinar con pesos por modelo
        weights_g = self._ensemble_weights(results)
        gender_p = np.zeros(2, dtype=np.float64)
        age_p = np.zeros(8, dtype=np.float64)
        age_value = 0.0
        for (name, gp, ap, av), wgt in zip(results, weights_g):
            gender_p += gp * wgt
            age_p += ap * wgt
            age_value += av * wgt
        gender_p /= max(weights_g.sum(), 1e-9)
        age_p /= max(weights_g.sum(), 1e-9)
        age_value /= max(weights_g.sum(), 1e-9)
        # Renormalizar age_p
        age_p_sum = age_p.sum()
        if age_p_sum > 0:
            age_p = age_p / age_p_sum
        else:
            age_p = np.full(8, 1.0 / 8.0)

        return gender_p, age_p, age_value

    def _ensemble_weights(self, results: list) -> np.ndarray:
        """Pesos del ensemble segun modelo: MiVOLO > InsightFace > Caffe."""
        w_map = {
            "mivolo": self._cfg.MIVOLO_WEIGHT_IN_ENSEMBLE,
            "insight": 1.0 - self._cfg.MIVOLO_WEIGHT_IN_ENSEMBLE,
            "caffe": 0.15,
        }
        return np.array([w_map.get(r[0], 0.1) for r in results],
                        dtype=np.float64)

    def _build_tta_variants(self, face: np.ndarray) -> List[np.ndarray]:
        """Genera 6 variantes TTA: original, flip, zoom_in, zoom_out,
        rot+5, rot-5."""
        variants = [face, cv2.flip(face, 1)]
        fh, fw = face.shape[:2]

        # Zoom in (crop central 85%)
        cx1 = int(fw * 0.075)
        cy1 = int(fh * 0.075)
        cx2 = fw - cx1
        cy2 = fh - cy1
        if cx2 - cx1 > 20 and cy2 - cy1 > 20:
            variants.append(face[cy1:cy2, cx1:cx2])

        # Zoom out (padding 10%)
        pad = int(max(fw, fh) * 0.10)
        if pad > 0:
            padded = cv2.copyMakeBorder(
                face, pad, pad, pad, pad, cv2.BORDER_REPLICATE
            )
            variants.append(padded)

        # Rotaciones ±5 grados
        if self._cfg.DEMO_TTA_VARIANTS >= 5:
            center = (fw / 2.0, fh / 2.0)
            for angle in (5.0, -5.0):
                M = cv2.getRotationMatrix2D(center, angle, 1.0)
                rot = cv2.warpAffine(
                    face, M, (fw, fh),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE
                )
                variants.append(rot)

        return variants[:max(2, self._cfg.DEMO_TTA_VARIANTS)]

    def _infer_insightface_tta(
        self, face: np.ndarray
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], float]:
        """InsightFace genderage.onnx con heavy-TTA y validacion interna."""
        variants = self._build_tta_variants(face)

        gender_per_variant: List[np.ndarray] = []
        age_per_variant: List[float] = []

        for v in variants:
            if v is None or v.size == 0:
                continue
            blob = cv2.dnn.blobFromImage(
                v, 1.0, (96, 96), (0, 0, 0), swapRB=True
            )
            try:
                out = self._insight.run(
                    self._insight_output, {self._insight_input: blob}
                )[0][0]
            except Exception:
                continue
            g_insight = _softmax(out[:2])
            g = np.array([g_insight[1], g_insight[0]], dtype=np.float64)
            gender_per_variant.append(g)
            age_per_variant.append(float(out[2]) * 100.0)

        if not gender_per_variant:
            return None, None, 0.0

        # Si se exige acuerdo entre variantes TTA, descartar sample si no
        if (self._cfg.DEMO_TTA_REQUIRE_AGREEMENT and
                len(gender_per_variant) >= 3):
            argmaxes = [int(np.argmax(g)) for g in gender_per_variant]
            # Permitir hasta 1 variante en desacuerdo
            top = max(argmaxes, key=argmaxes.count)
            agree = sum(1 for a in argmaxes if a == top)
            if agree / len(argmaxes) < 0.80:
                return None, None, 0.0

        gender_p = np.mean(np.stack(gender_per_variant), axis=0)
        age_years = max(0.0, min(100.0, float(np.mean(age_per_variant))))

        sigma = 4.0
        age_p = np.exp(-((_AGE_BIN_CENTERS - age_years) ** 2) /
                       (2.0 * sigma * sigma))
        age_p = (age_p / age_p.sum() if age_p.sum() > 0
                 else np.full(8, 1.0 / 8.0))

        return gender_p, age_p, age_years

    def _infer_mivolo_tta(
        self, face: np.ndarray
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], float]:
        """MiVOLO ONNX con heavy-TTA. Input 224x224 RGB normalizado."""
        variants = self._build_tta_variants(face)
        size = self._cfg.MIVOLO_INPUT_SIZE

        gender_per_variant: List[np.ndarray] = []
        age_per_variant: List[float] = []

        for v in variants:
            if v is None or v.size == 0:
                continue
            try:
                resized = cv2.resize(v, (size, size),
                                     interpolation=cv2.INTER_CUBIC)
                rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                arr = rgb.astype(np.float32) / 255.0
                # ImageNet normalization
                mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
                std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
                arr = (arr - mean) / std
                # HWC -> CHW -> NCHW
                blob = arr.transpose(2, 0, 1)[None, ...].astype(np.float32)

                out = self._mivolo.run(
                    self._mivolo_output, {self._mivolo_input: blob}
                )
            except Exception as e:
                logger.debug(f"MiVOLO inference error: {e}")
                continue

            # MiVOLO outputs (variantes segun checkpoint):
            # opcion A: [gender_logits(2), age_scalar]
            # opcion B: [age_scalar, gender_logits(2)]
            # opcion C: dos tensores separados
            try:
                gender_logits, age_scalar = self._parse_mivolo_output(out)
            except Exception:
                continue

            g_mivolo = _softmax(gender_logits)
            # MiVOLO orden tipico: [male, female] -> mapear a [Hombre, Mujer]
            g = np.array([g_mivolo[0], g_mivolo[1]], dtype=np.float64)
            gender_per_variant.append(g)
            age_per_variant.append(float(age_scalar))

        if not gender_per_variant:
            return None, None, 0.0

        if (self._cfg.DEMO_TTA_REQUIRE_AGREEMENT and
                len(gender_per_variant) >= 3):
            argmaxes = [int(np.argmax(g)) for g in gender_per_variant]
            top = max(argmaxes, key=argmaxes.count)
            agree = sum(1 for a in argmaxes if a == top)
            if agree / len(argmaxes) < 0.80:
                return None, None, 0.0

        gender_p = np.mean(np.stack(gender_per_variant), axis=0)
        age_years = max(0.0, min(100.0, float(np.mean(age_per_variant))))

        sigma = 3.5
        age_p = np.exp(-((_AGE_BIN_CENTERS - age_years) ** 2) /
                       (2.0 * sigma * sigma))
        age_p = (age_p / age_p.sum() if age_p.sum() > 0
                 else np.full(8, 1.0 / 8.0))

        return gender_p, age_p, age_years

    def _parse_mivolo_output(self, out) -> Tuple[np.ndarray, float]:
        """Adapta el output de MiVOLO a (gender_logits[2], age_scalar).

        MiVOLO oficial devuelve un tensor (N, 3) = [male, female, age_norm]
        o (N, 2)+(N,1) dependiendo del export. Cubrimos ambos casos.
        """
        if len(out) == 1:
            arr = np.asarray(out[0])
            if arr.ndim == 2 and arr.shape[1] >= 3:
                row = arr[0]
                # Si el ultimo valor parece normalizado (0..1) lo
                # escalamos a anos. Si parece anos (>1) lo dejamos.
                age = float(row[2])
                if age <= 1.0:
                    age *= 100.0
                return np.asarray(row[:2], dtype=np.float64), age
            elif arr.ndim == 1 and arr.shape[0] >= 3:
                age = float(arr[2])
                if age <= 1.0:
                    age *= 100.0
                return np.asarray(arr[:2], dtype=np.float64), age
        elif len(out) >= 2:
            # gender_logits separados de age
            gender_arr = np.asarray(out[0]).reshape(-1)[:2]
            age_arr = np.asarray(out[1]).reshape(-1)
            age = float(age_arr[0])
            if age <= 1.0:
                age *= 100.0
            return gender_arr.astype(np.float64), age
        raise ValueError("Formato de output MiVOLO desconocido")

    def _infer_caffe_tta(
        self, face: np.ndarray
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], float]:
        """Caffe Levi-Hassner con TTA simple."""
        try:
            blob_a = cv2.dnn.blobFromImage(
                face, 1.0, (227, 227), _MEAN_VALUES, swapRB=False
            )
            face_flip = cv2.flip(face, 1)
            blob_b = cv2.dnn.blobFromImage(
                face_flip, 1.0, (227, 227), _MEAN_VALUES, swapRB=False
            )

            self._gender_net.setInput(blob_a)
            g_a = _softmax(self._gender_net.forward()[0])
            self._gender_net.setInput(blob_b)
            g_b = _softmax(self._gender_net.forward()[0])
            gender_p = (g_a + g_b) * 0.5

            if self._age_net is not None:
                self._age_net.setInput(blob_a)
                a_a = _softmax(self._age_net.forward()[0])
                self._age_net.setInput(blob_b)
                a_b = _softmax(self._age_net.forward()[0])
                age_p = (a_a + a_b) * 0.5
            else:
                age_p = np.full(8, 1.0 / 8.0)

            age_value = float(np.sum(age_p * _AGE_BIN_CENTERS))
            return gender_p.astype(np.float64), age_p.astype(np.float64), age_value
        except Exception:
            return None, None, 0.0

    # ── Rama CORPORAL E3B: clasificacion de genero/edad SIN cara ──────

    def _try_body_sample(self, frame: np.ndarray, bbox: tuple,
                         acc: "_TrackAccumulator", track_id: int,
                         cara: Optional[np.ndarray] = None) -> bool:
        """Intenta una muestra CORPORAL (rama E3B).

        Se usa en dos situaciones:
          * No se vio rostro (el caso normal: 79 % de las personas).
          * Se vio rostro pero los gates estrictos de la rama facial lo
            rechazaron. Entonces se pasa esa `cara` a MiVOLO, que la
            aprovecha en entrada DUAL en lugar de tirarla.

        Aplica gates (tamano de persona, blur), corre el clasificador y, si
        pasa, agrega la muestra al acumulador con peso 0.5 y source='body'.
        Devuelve True si agrego una muestra.
        """
        if not self._cfg.DEMO_BODY_BRANCH_ENABLED:
            return False
        if (self._estimador_cuerpo is None and self._par is None
                and self._mivolo is None):
            return False
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        pw, ph = x2 - x1, y2 - y1
        if (ph < self._cfg.DEMO_BODY_MIN_PERSON_H or
                pw < self._cfg.DEMO_BODY_MIN_PERSON_W):
            self._q_reject["body_bbox_pequeno"] += 1
            return False
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False
        # Gate de blur del crop corporal (descarta motion blur fuerte)
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            if (cv2.Laplacian(gray, cv2.CV_64F).var()
                    < self._cfg.DEMO_BODY_MIN_BLUR_VAR):
                self._q_reject["body_blur"] += 1
                return False
        except Exception:
            pass

        res = self._infer_body_only(crop, cara=cara)
        if res is None:
            self._q_reject["body_sin_modelo"] += 1
            return False
        gender_probs, age_value = res
        if gender_probs is None:
            return False
        if float(np.max(gender_probs)) < self._cfg.DEMO_BODY_MIN_GENDER_CONF:
            self._q_reject["body_conf_baja"] += 1
            return False

        # Edad: gaussiana sobre los bins si el modelo la da; plana si no.
        if age_value is not None:
            sigma = 6.0
            age_p = np.exp(-((_AGE_BIN_CENTERS - float(age_value)) ** 2) /
                           (2.0 * sigma * sigma))
            s = age_p.sum()
            age_p = age_p / s if s > 0 else np.full(8, 1.0 / 8.0)
            av = float(age_value)
        else:
            age_p = np.full(8, 1.0 / 8.0)
            av = 0.0

        acc.add(gender_probs, age_p, av, face_width=0.0,
                weight=self._cfg.DEMO_BODY_SAMPLE_WEIGHT,
                decay=self._cfg.DEMO_DECAY, source="body")
        self._q_accepted_body += 1
        return True

    def _infer_body_only(self, person_crop: np.ndarray,
                         cara: Optional[np.ndarray] = None):
        """Clasifica desde el CUERPO (con o sin cara).

        Primario: MiVOLO v2 (Hito 4), que es la ruta principal del sistema
        porque el 79 % de las personas de estas camaras no muestra rostro
        utilizable. Si se le pasa `cara`, MiVOLO usa su ENTRADA DUAL
        (rostro + cuerpo), que es su modo mas informado.
        Fallbacks legacy: PAR y el MiVOLO ONNX antiguo, por si alguna
        instalacion los tuviera.

        Returns (gender_probs[Hombre, Mujer], age_value|None) o None.
        """
        if self._estimador_cuerpo is not None:
            muestra = self._estimador_cuerpo.estimar(-1, person_crop, cara)
            if muestra is None or not muestra.es_valida():
                return None
            # El acumulador vota con probabilidades [Hombre, Mujer].
            probs = np.zeros(2, dtype=np.float64)
            indice = 0 if muestra.genero == "Hombre" else 1
            probs[indice] = muestra.conf_genero
            probs[1 - indice] = 1.0 - muestra.conf_genero
            return probs, muestra.edad_anios
        if self._par is not None:
            return self._infer_par_gender(person_crop)
        if self._mivolo is not None:
            return self._infer_mivolo_body_only(person_crop)
        return None

    def _infer_par_gender(self, person_crop: np.ndarray):
        """PAR (PA-100K/PETA) -> genero desde el cuerpo. SOLO genero.

        El export ONNX puede dar (a) un escalar sigmoide P(femenino) o
        (b) 2 logits [masculino, femenino]. Cubrimos ambos.
        Returns (gender_probs[Hombre, Mujer], None).
        """
        try:
            size = self._cfg.DEMO_BODY_INPUT_SIZE
            resized = cv2.resize(person_crop, (size, size),
                                 interpolation=cv2.INTER_CUBIC)
            rgb = (cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                   .astype(np.float32) / 255.0)
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            blob = ((rgb - mean) / std).transpose(2, 0, 1)[None, ...]
            blob = blob.astype(np.float32)
            out = self._par.run(self._par_output, {self._par_input: blob})
            arr0 = np.asarray(out[0]).reshape(-1)
            if arr0.size == 1:
                p_fem = float(1.0 / (1.0 + np.exp(-arr0[0])))
                gender_probs = np.array([1.0 - p_fem, p_fem],
                                        dtype=np.float64)
            else:
                gender_probs = _softmax(arr0[:2].astype(np.float64))
            return gender_probs, None
        except Exception as e:
            logger.debug("PAR inference error: %s", e)
            return None

    def _infer_mivolo_body_only(self, person_crop: np.ndarray):
        """MiVOLO en modo body-only (cara ausente). Requiere un checkpoint
        MiVOLO exportado para aceptar solo el crop de cuerpo.
        Returns (gender_probs[Hombre, Mujer], age_value) o None.
        """
        try:
            size = self._cfg.MIVOLO_INPUT_SIZE
            resized = cv2.resize(person_crop, (size, size),
                                 interpolation=cv2.INTER_CUBIC)
            rgb = (cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                   .astype(np.float32) / 255.0)
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            blob = ((rgb - mean) / std).transpose(2, 0, 1)[None, ...]
            blob = blob.astype(np.float32)
            out = self._mivolo.run(self._mivolo_output,
                                   {self._mivolo_input: blob})
            gender_logits, age_scalar = self._parse_mivolo_output(out)
            g = _softmax(np.asarray(gender_logits, dtype=np.float64))
            return np.array([g[0], g[1]], dtype=np.float64), float(age_scalar)
        except Exception as e:
            logger.debug("MiVOLO body-only error: %s", e)
            return None

    # ── Deteccion de rostro con pose ──────────────────────────────

    def _detect_face_with_pose(self, person_crop: np.ndarray
                               ) -> Tuple[Optional[np.ndarray],
                                          Optional[Tuple[float, float, float]],
                                          float]:
        """Detecta cara y devuelve (crop_alineado, pose, native_face_w).

        native_face_w es el ancho REAL de la cara en el crop original
        (sin upscale). Si la cara real es muy chica, la informacion
        para clasificar es limitada aunque la imagen este upscaleada.

        Solo YuNet provee keypoints -> solo con YuNet hay estimacion de
        pose real. Si no hay YuNet, pose=None y native_face_w=0.
        """
        if person_crop.size == 0:
            return None, None, 0.0

        if self._yunet is not None:
            return self._detect_align_and_pose_yunet(person_crop)

        # Fallback sin pose
        face = self._detect_face_legacy(person_crop)
        return face, None, 0.0

    def _detect_face_legacy(self, person_crop: np.ndarray
                            ) -> Optional[np.ndarray]:
        """Detector legacy DNN + Haar sin keypoints (sin pose)."""
        candidates: list = []

        if self._face_dnn is not None:
            dnn_best = self._detect_face_dnn_bbox(person_crop)
            if dnn_best is not None:
                (x1, y1, x2, y2), conf = dnn_best
                area = max(1, (x2 - x1) * (y2 - y1))
                score = float(conf) * (area ** 0.5)
                candidates.append((score, (x1, y1, x2, y2), "dnn"))

        if self._face_cascade is not None:
            haar_best = self._detect_face_haar_bbox(person_crop)
            if haar_best is not None:
                (x1, y1, x2, y2), conf = haar_best
                area = max(1, (x2 - x1) * (y2 - y1))
                score = float(conf) * (area ** 0.5)
                candidates.append((score, (x1, y1, x2, y2), "haar"))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            _score, (x1, y1, x2, y2), _src = candidates[0]
            return self._crop_with_padding(person_crop, x1, y1, x2, y2,
                                           pad_ratio=0.22)
        return None

    def _detect_align_and_pose_yunet(self, person_crop: np.ndarray
                                     ) -> Tuple[Optional[np.ndarray],
                                                Optional[Tuple[float, float, float]],
                                                float]:
        """YuNet + alineacion + estimacion de pose.

        Returns (face_aligned_112, pose_yaw_pitch_roll, native_face_w)
        donde native_face_w es el ancho REAL en el frame original.
        """
        h, w = person_crop.shape[:2]
        if h < 40 or w < 40:
            return None, None, 0.0
        try:
            if getattr(self._yunet, 'is_gpu', False):
                # YuNet GPU: estandariza internamente a 640 (letterbox que
                # preserva aspecto). Le pasamos el crop NATIVO -> un solo resize
                # y MAS recall de caras lejanas (no degradar con un downscale
                # intermedio). Las coords vuelven en el espacio del crop nativo.
                img_resized = person_crop
                scale_x = 1.0
                scale_y = 1.0
            else:
                min_side = self._cfg.DEMO_YUNET_UPSCALE_TARGET
                max_side = 960
                longer = max(w, h)
                if longer < min_side:
                    scale = min_side / float(longer)
                elif longer > max_side:
                    scale = max_side / float(longer)
                else:
                    scale = 1.0
                target_w = int(round(w * scale))
                target_h = int(round(h * scale))
                self._yunet.setInputSize((target_w, target_h))
                if scale != 1.0:
                    img_resized = cv2.resize(
                        person_crop, (target_w, target_h),
                        interpolation=(cv2.INTER_CUBIC if scale > 1.0
                                       else cv2.INTER_AREA)
                    )
                    scale_x = w / target_w
                    scale_y = h / target_h
                else:
                    img_resized = person_crop
                    scale_x = 1.0
                    scale_y = 1.0
            _, faces = self._yunet.detect(img_resized)
        except Exception as e:
            logger.debug(f"Error YuNet detect: {e}")
            return None, None, 0.0

        if faces is None or len(faces) == 0:
            if self._cfg.DEMO_DEBUG_REJECTIONS:
                logger.debug("YuNet: 0 caras en crop %dx%d", w, h)
            return None, None, 0.0

        min_score = self._cfg.DEMO_YUNET_MIN_SCORE
        valid_idx = [i for i in range(len(faces))
                     if float(faces[i, -1]) >= min_score]
        if not valid_idx:
            if self._cfg.DEMO_DEBUG_REJECTIONS:
                top = max(float(f[-1]) for f in faces)
                logger.info(
                    "YuNet: %d cara(s) pero score max=%.2f < %.2f",
                    len(faces), top, min_score
                )
            return None, None, 0.0
        best_idx = max(valid_idx, key=lambda i: float(faces[i, -1]))
        face = faces[best_idx]
        face_score = float(face[-1])

        fx = face[0] * scale_x
        fy = face[1] * scale_y
        fw_box = face[2] * scale_x
        fh_box = face[3] * scale_y
        r_eye = (face[4] * scale_x, face[5] * scale_y)
        l_eye = (face[6] * scale_x, face[7] * scale_y)
        nose = (face[8] * scale_x, face[9] * scale_y)
        r_mouth = (face[10] * scale_x, face[11] * scale_y)
        l_mouth = (face[12] * scale_x, face[13] * scale_y)

        # Validaciones geometricas basicas
        eye_dx = abs(l_eye[0] - r_eye[0])
        eye_dy = abs(l_eye[1] - r_eye[1])
        eye_dist = (eye_dx ** 2 + eye_dy ** 2) ** 0.5
        if fw_box <= 0 or eye_dist <= 0:
            return None, None, 0.0
        ratio_eye_face = eye_dist / fw_box
        if ratio_eye_face < self._cfg.DEMO_MIN_EYE_FACE_RATIO:
            if self._cfg.DEMO_DEBUG_REJECTIONS:
                logger.info(
                    "YuNet RECHAZO eye_ratio=%.2f < %.2f (perfil)",
                    ratio_eye_face,
                    self._cfg.DEMO_MIN_EYE_FACE_RATIO
                )
            return None, None, 0.0

        eye_cx = (r_eye[0] + l_eye[0]) / 2.0
        eye_cy = (r_eye[1] + l_eye[1]) / 2.0
        nose_offset_x = abs(nose[0] - eye_cx) / max(eye_dist, 1e-6)
        if nose_offset_x > self._cfg.DEMO_MAX_NOSE_OFFSET:
            if self._cfg.DEMO_DEBUG_REJECTIONS:
                logger.info(
                    "YuNet RECHAZO nose_off=%.2f > %.2f (girada)",
                    nose_offset_x,
                    self._cfg.DEMO_MAX_NOSE_OFFSET
                )
            return None, None, 0.0

        mouth_cy = (r_mouth[1] + l_mouth[1]) / 2.0
        if mouth_cy <= eye_cy:
            return None, None, 0.0

        if not (eye_cy < nose[1] < mouth_cy +
                (mouth_cy - eye_cy) * 0.3):
            return None, None, 0.0

        # ── Estimacion de pose 3D con solvePnP ──
        keypoints_2d = np.array([
            r_eye, l_eye, nose, r_mouth, l_mouth
        ], dtype=np.float64)
        pose = _estimate_head_pose(keypoints_2d, (w, h))

        src_kpts = keypoints_2d
        # ── Alineacion ArcFace canonica (similarity transform a
        # template 112x112). Mucho mas preciso que solo rotar: alinea
        # ojos/nariz/boca a posiciones exactas donde InsightFace
        # genderage fue entrenado. Mejora gender/age accuracy.
        face_aligned = _align_face_arcface(
            person_crop,
            src_kpts.astype(np.float32),
            output_size=112,
        )
        if face_aligned is None or face_aligned.size == 0:
            # Fallback al metodo viejo (rotar + crop) si la alineacion
            # canonica falla por alguna razon.
            dy = l_eye[1] - r_eye[1]
            dx = l_eye[0] - r_eye[0]
            angle = float(np.degrees(np.arctan2(dy, dx)))
            pad = 0.30
            cx = fx + fw_box / 2.0
            cy = fy + fh_box / 2.0
            side = max(fw_box, fh_box) * (1.0 + pad)
            crop_x1 = int(max(0, cx - side / 2.0))
            crop_y1 = int(max(0, cy - side / 2.0))
            crop_x2 = int(min(w, cx + side / 2.0))
            crop_y2 = int(min(h, cy + side / 2.0))
            if crop_x2 - crop_x1 < 24 or crop_y2 - crop_y1 < 24:
                return None, None, 0.0
            M = cv2.getRotationMatrix2D((eye_cx, eye_cy), angle, 1.0)
            rotated = cv2.warpAffine(
                person_crop, M, (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
            face_aligned = rotated[crop_y1:crop_y2, crop_x1:crop_x2]
            if face_aligned.size == 0:
                return None, None, 0.0

        if self._cfg.DEMO_DEBUG_REJECTIONS and pose is not None:
            logger.debug(
                "YuNet OK: score=%.2f eye_ratio=%.2f nose_off=%.2f "
                "yaw=%.1f pitch=%.1f roll=%.1f native_w=%.0f",
                face_score, ratio_eye_face, nose_offset_x, *pose, fw_box
            )

        # native_face_w = ancho de la cara en coords del person_crop
        # (sin upscale a YuNet). Esa es la info REAL disponible para
        # clasificar genero/edad.
        return face_aligned, pose, float(fw_box)

    def extract_face_zoom(self, person_crop: np.ndarray,
                          out_w: int = 0) -> Optional[np.ndarray]:
        """Rostro recortado con margen y AMPLIADO, para ver la cara.

        A diferencia del crop que come el clasificador (alineado a 112x112
        con el template de ArcFace, que sale rotado/recortado), este es un
        recorte NATURAL del rostro pensado para mirarlo: sirve para la foto
        de la captura y para verificar a ojo en que cara se baso el
        genero/edad. Devuelve None si no se detecta rostro.
        """
        if self._yunet is None or person_crop is None:
            return None
        h, w = person_crop.shape[:2]
        if h < 40 or w < 40:
            return None
        if out_w <= 0:
            out_w = int(self._cfg.DEMO_FACE_ZOOM_OUT_W)
        try:
            if not getattr(self._yunet, 'is_gpu', False):
                self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(person_crop)
            if faces is None or len(faces) == 0:
                return None
            best = max(faces, key=lambda f: float(f[-1]))
            if float(best[-1]) < float(self._cfg.DEMO_FACE_ZOOM_MIN_SCORE):
                return None
            fx, fy, fw, fh = (float(best[0]), float(best[1]),
                              float(best[2]), float(best[3]))
            if fw <= 0 or fh <= 0:
                return None
            # Encuadre tipo retrato: algo mas de alto que de ancho.
            cx, cy = fx + fw / 2.0, fy + fh / 2.0
            half_w = fw * 0.80
            half_h = fh * 0.95
            x1 = int(max(0, round(cx - half_w)))
            y1 = int(max(0, round(cy - half_h)))
            x2 = int(min(w, round(cx + half_w)))
            y2 = int(min(h, round(cy + half_h)))
            if x2 - x1 < 12 or y2 - y1 < 12:
                return None
            rostro = person_crop[y1:y2, x1:x2]
            if rostro.size == 0:
                return None
            escala = float(out_w) / float(x2 - x1)
            escala = min(escala, 6.0)
            nw = max(1, int(round((x2 - x1) * escala)))
            nh = max(1, int(round((y2 - y1) * escala)))
            interp = cv2.INTER_CUBIC if escala > 1.0 else cv2.INTER_AREA
            return cv2.resize(rostro, (nw, nh), interpolation=interp)
        except Exception as exc:  # noqa: BLE001
            logger.debug("extract_face_zoom fallo: %s", exc)
            return None

    def _crop_with_padding(self, img: np.ndarray, x1: int, y1: int,
                           x2: int, y2: int,
                           pad_ratio: float = 0.22) -> np.ndarray:
        h, w = img.shape[:2]
        fw = x2 - x1
        fh = y2 - y1
        pad = int(max(fw, fh) * pad_ratio)
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        return img[y1:y2, x1:x2]

    def _detect_face_dnn_bbox(self, person_crop: np.ndarray
                              ) -> Optional[Tuple[Tuple[int, int, int, int],
                                                  float]]:
        h, w = person_crop.shape[:2]
        best_conf = 0.0
        best_box = None
        conf_min = self._cfg.DEMO_FACE_DETECTOR_CONF

        for size in (300, 416):
            try:
                blob = cv2.dnn.blobFromImage(
                    person_crop, 1.0, (size, size),
                    (104.0, 177.0, 123.0), swapRB=False,
                )
                self._face_dnn.setInput(blob)
                detections = self._face_dnn.forward()
            except Exception as e:
                logger.debug(f"Error face DNN size={size}: {e}")
                continue

            for i in range(detections.shape[2]):
                c = float(detections[0, 0, i, 2])
                if c < conf_min or c <= best_conf:
                    continue
                x1 = int(detections[0, 0, i, 3] * w)
                y1 = int(detections[0, 0, i, 4] * h)
                x2 = int(detections[0, 0, i, 5] * w)
                y2 = int(detections[0, 0, i, 6] * h)
                if x2 <= x1 or y2 <= y1:
                    continue
                best_conf = c
                best_box = (x1, y1, x2, y2)

        if best_box is None:
            return None
        return best_box, best_conf

    def _detect_face_haar_bbox(self, person_crop: np.ndarray
                               ) -> Optional[Tuple[Tuple[int, int, int, int],
                                                   float]]:
        try:
            gray = cv2.cvtColor(person_crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            faces, _rej, weights = self._face_cascade.detectMultiScale3(
                gray, scaleFactor=1.05, minNeighbors=5,
                minSize=(self._cfg.DEMO_MIN_FACE_SIZE,
                         self._cfg.DEMO_MIN_FACE_SIZE),
                outputRejectLevels=True,
            )
            if len(faces) == 0:
                return None
            idx = int(np.argmax(weights)) if len(weights) else 0
            fx, fy, fw, fh = faces[idx]
            raw_w = float(weights[idx]) if len(weights) else 1.0
            conf = float(min(1.0, max(0.0, raw_w / 8.0)))
            return (int(fx), int(fy), int(fx + fw), int(fy + fh)), conf
        except Exception as e:
            logger.debug(f"Error Haar: {e}")
            return None

    def _face_passes_quality(self, face: np.ndarray) -> bool:
        if face is None or face.size == 0:
            return False
        h, w = face.shape[:2]
        if (h < self._cfg.DEMO_MIN_FACE_SIZE or
                w < self._cfg.DEMO_MIN_FACE_SIZE):
            if self._cfg.DEMO_DEBUG_REJECTIONS:
                logger.info(
                    "QUALITY REJECT: tamano %dx%d < %d",
                    w, h, self._cfg.DEMO_MIN_FACE_SIZE
                )
            return False
        ar = h / float(w)
        if (ar < self._cfg.DEMO_FACE_ASPECT_MIN or
                ar > self._cfg.DEMO_FACE_ASPECT_MAX):
            if self._cfg.DEMO_DEBUG_REJECTIONS:
                logger.info(
                    "QUALITY REJECT: aspect_ratio=%.2f fuera de [%.2f, %.2f]",
                    ar, self._cfg.DEMO_FACE_ASPECT_MIN,
                    self._cfg.DEMO_FACE_ASPECT_MAX
                )
            return False
        try:
            gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
            blur_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            if blur_var < self._cfg.DEMO_MIN_BLUR_VAR:
                if self._cfg.DEMO_DEBUG_REJECTIONS:
                    logger.info(
                        "QUALITY REJECT: blur_var=%.1f < %.1f",
                        blur_var, self._cfg.DEMO_MIN_BLUR_VAR
                    )
                return False
            contrast = gray.std()
            if contrast < self._cfg.DEMO_MIN_CONTRAST:
                if self._cfg.DEMO_DEBUG_REJECTIONS:
                    logger.info(
                        "QUALITY REJECT: contrast=%.1f < %.1f",
                        contrast, self._cfg.DEMO_MIN_CONTRAST
                    )
                return False
            # Simetria L-R: rechaza caras parcialmente ocluidas
            gh, gw = gray.shape
            half = gw // 2
            if half >= 10:
                left = gray[:, :half]
                right_m = cv2.flip(gray[:, gw - half:], 1)
                if left.shape == right_m.shape:
                    asym = float(cv2.absdiff(left, right_m).mean())
                    if asym > self._cfg.DEMO_MAX_ASYMMETRY:
                        if self._cfg.DEMO_DEBUG_REJECTIONS:
                            logger.info(
                                "QUALITY REJECT: asym=%.1f > %.1f",
                                asym,
                                self._cfg.DEMO_MAX_ASYMMETRY
                            )
                        return False
        except Exception:
            return False
        return True

    def _face_frontal_score(self, face: np.ndarray) -> float:
        try:
            gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
            gh, gw = gray.shape
            half = gw // 2
            if half < 10:
                return 0.5
            left = gray[:, :half]
            right_mirror = cv2.flip(gray[:, gw - half:], 1)
            if left.shape != right_mirror.shape:
                right_mirror = cv2.resize(
                    right_mirror, (left.shape[1], left.shape[0])
                )
            diff = cv2.absdiff(left, right_mirror).mean()
            score = max(0.0, 1.0 - diff / 40.0)
            return float(score)
        except Exception:
            return 0.5

    # ── API publica (compat) ──────────────────────────────────────

    def get_cached(self, track_id: int) -> Optional[Dict[str, str]]:
        acc = self._accum.get(track_id)
        if acc is None:
            return None
        if acc.committed:
            return acc.result
        if (self._cfg.DEMO_SHOW_PROVISIONAL and
                acc.samples >= 1 and not acc.give_up):
            provisional = self._provisional_result(acc)
            if provisional is not None:
                return provisional
        return self._unknown_result(acc.samples)

    def _veredicto_final(self, track_id: int,
                         acc: Optional["_TrackAccumulator"]
                         ) -> tuple[str, Dict[str, Any]]:
        """Decide el motivo FINAL de un track y reune sus detalles.

        Los motivos son excluyentes y se evaluan de mas a menos
        concluyente: primero los exitos, luego la causa dominante del
        fracaso segun los contadores acumulados por ese track.
        """
        intentos_sin_rostro = (self._no_face_counter.get(id(acc), 0)
                               if acc is not None else 0)
        detalles: Dict[str, Any] = {
            "muestras": int(getattr(acc, "samples", 0) or 0),
            "muestras_faciales": int(getattr(acc, "n_face_samples", 0) or 0),
            "muestras_corporales": int(getattr(acc, "n_body_samples", 0) or 0),
            "intentos_sin_rostro": int(intentos_sin_rostro),
            "rechazos_pose": self._pose_reject_counter.get(track_id, 0),
            "rechazos_calidad": self._quality_reject_counter.get(track_id, 0),
            "rechazos_rostro_pequeno": self._pequeno_reject_counter.get(
                track_id, 0),
            "mejor_ancho_rostro_px": round(
                self._mejor_ancho_rostro.get(track_id, 0.0), 1),
            "intentos_sin_modelo": self._sin_modelo_counter.get(track_id, 0),
        }

        if track_id in self._tracks_con_excepcion:
            return MotivoFinal.EXCEPCION_ESTIMADOR, detalles

        # Falta de modelo: manda sobre cualquier otro diagnostico, porque
        # con el estimador descargado el resto de contadores no significa
        # nada (no llego a mirarse la escena).
        if (detalles["intentos_sin_modelo"] > 0
                and not (acc is not None and acc.committed)):
            return MotivoFinal.SIN_MODELO, detalles

        if acc is not None and acc.committed and acc.result:
            detalles["genero"] = acc.result.get("gender")
            detalles["rango_edad"] = acc.result.get("age_range")
            detalles["confianza"] = acc.result.get("confidence")
            detalles["bracket"] = acc.result.get("bracket")
            if track_id in self._tracks_heredados:
                return MotivoFinal.HEREDADO_REID, detalles
            if acc.is_body_only:
                return MotivoFinal.CLASIFICADO_SOLO_CUERPO, detalles
            return MotivoFinal.CLASIFICADO, detalles

        # ── Fracasos, por causa dominante ──
        if acc is None:
            return MotivoFinal.TRACK_NO_CERRADO, detalles

        if acc.samples > 0:
            # Hubo muestras validas pero el voting nunca alcanzo el umbral.
            return MotivoFinal.MUESTRAS_INSUFICIENTES, detalles

        # Sin ninguna muestra aceptada: ¿por que?
        if detalles["rechazos_rostro_pequeno"] > 0:
            return MotivoFinal.ROSTRO_MUY_PEQUENO, detalles
        if (detalles["rechazos_calidad"] > 0
                or detalles["rechazos_pose"] > 0):
            return MotivoFinal.CALIDAD_INSUFICIENTE, detalles
        if intentos_sin_rostro > 0:
            return MotivoFinal.SIN_ROSTRO, detalles
        # El track murio antes de que el estimador llegara a mirarlo.
        return MotivoFinal.TRACK_NO_CERRADO, detalles

    def remove_track(self, track_id: int):
        # Liberar TODO el estado por track para no fugar memoria.
        acc = self._accum.pop(track_id, None)

        # ── Telemetria (Hito 2): veredicto final, uno por track ──
        # Va ANTES de limpiar los contadores, que son su materia prima.
        try:
            motivo, detalles = self._veredicto_final(track_id, acc)
            self._telemetria.registrar_track(
                track_id, motivo, camara=self._camara_id, detalles=detalles)
        except Exception as exc:  # noqa: BLE001
            logger.debug("telemetria: veredicto fallido para %s: %s",
                         track_id, exc)

        if acc is not None:
            # _no_face_counter usa id(acc) como clave: limpiarlo aqui, con el
            # acc aun en mano, antes de que el GC lo recicle.
            self._no_face_counter.pop(id(acc), None)
        self._pose_reject_counter.pop(track_id, None)
        self._ensemble_reject_counter.pop(track_id, None)
        self._quality_reject_counter.pop(track_id, None)
        self._tta_reject_counter.pop(track_id, None)
        self._pequeno_reject_counter.pop(track_id, None)
        self._mejor_ancho_rostro.pop(track_id, None)
        self._sin_modelo_counter.pop(track_id, None)
        self._tracks_con_excepcion.discard(track_id)
        self._tracks_heredados.discard(track_id)
        # Liberar mapping del reidentifier (la persona queda en DB)
        if self._reidentifier is not None:
            self._reidentifier.remove_track(track_id)

    def get_counts(self) -> Dict:
        return {
            "gender": dict(self._gender_counts),
            "age": dict(self._age_counts),
            "combined": dict(self._combined_counts),
            "total_classified": sum(self._gender_counts.values()),
            "total_pending": sum(
                1 for a in self._accum.values()
                if not a.committed and not a.give_up
            ),
            "total_unknown": sum(
                1 for a in self._accum.values() if a.give_up
            ),
            "quality": self.get_quality_report(),
        }

    def get_quality_report(self) -> Dict[str, Any]:
        """Reporte AUDITABLE de la rama facial (Hito 3).

        Demuestra el criterio "cero muestras borrosas aceptadas": como toda
        muestra aceptada pasó `_face_passes_quality` (blur_var >=
        DEMO_MIN_BLUR_VAR), el mínimo blur aceptado nunca cae por debajo del
        umbral. `cero_borrosas_aceptadas` lo verifica explícitamente.

        Returns:
            dict con muestras aceptadas, blur mín/medio aceptado, el umbral,
            el flag de garantía, y el desglose de rechazos por motivo.
        """
        umbral = float(self._cfg.DEMO_MIN_BLUR_VAR)
        n = self._q_accepted
        blur_min = (None if n == 0 else round(self._q_accepted_blur_min, 1))
        blur_mean = (0.0 if n == 0
                     else round(self._q_accepted_blur_sum / n, 1))
        cero_borrosas = (n == 0 or self._q_accepted_blur_min >= umbral)
        return {
            "muestras_aceptadas": n,
            "muestras_faciales_aceptadas": n,
            "muestras_corporales_aceptadas": self._q_accepted_body,
            "blur_min_aceptado": blur_min,
            "blur_medio_aceptado": blur_mean,
            "umbral_blur": umbral,
            "cero_borrosas_aceptadas": bool(cero_borrosas),
            "rama_corporal_activa": (self._par is not None
                                     or self._mivolo is not None),
            "rechazos_por_motivo": dict(self._q_reject),
            "total_rechazos": int(sum(self._q_reject.values())),
        }

    def log_quality_report(self) -> None:
        """Loguea el reporte de calidad (para demostrar el criterio en vivo)."""
        r = self.get_quality_report()
        logger.info(
            "CALIDAD FACIAL | aceptadas=%d blur_min=%s (umbral=%.1f) "
            "cero_borrosas=%s | rechazos=%s",
            r["muestras_aceptadas"], r["blur_min_aceptado"],
            r["umbral_blur"], r["cero_borrosas_aceptadas"],
            r["rechazos_por_motivo"],
        )

    def reset(self):
        self._accum.clear()
        self._gender_counts.clear()
        self._age_counts.clear()
        self._combined_counts.clear()
        self._history.clear()
        self._pose_reject_counter.clear()
        # Instrumentacion de calidad (Hito 3/4)
        self._q_accepted = 0
        self._q_accepted_blur_min = float("inf")
        self._q_accepted_blur_sum = 0.0
        self._q_accepted_body = 0
        self._q_reject.clear()
