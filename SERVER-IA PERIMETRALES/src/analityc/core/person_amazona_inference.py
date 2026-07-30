import cv2
import numpy as np
import torch
import time
from ultralytics import YOLO
import supervision as sv
from collections import defaultdict, deque, Counter
import logging
from typing import Tuple, Dict, Any, List, Optional
import asyncio
import threading
import base64
import httpx
import datetime
import os
import traceback
from ..config.config import (
    DEFAULT_ROI, get_config, CAMERA_ANGLE_PRESETS, DEFAULT_CAMERA_ANGLE,
    AUTO_ANGLE_DEFAULT, AUTO_ANGLE_MIN_SAMPLES, AUTO_ANGLE_CENITAL_HW_MAX,
    AUTO_ANGLE_WINDOW,
)

# ── Modulos de analitica de personas (deteccion + genero/edad) ──
from .analytics.config import AnalyticsConfig
from .analytics.demographics import DemographicsClassifier
from .analytics.face_reidentifier import FaceReidentifier
from .analytics.body_reidentifier import BodyReidentifier
from .analytics.people_counter import PeopleCounter
from .analytics.heatmap import HeatmapAccumulator
from .utils.overlay import draw_demographics_panel, draw_people_total
from .utils.logger import AnalyticsLogger

logger = logging.getLogger(__name__)

# ── Raiz del proyecto (relativa a este archivo) ──────────────────────
# src/analityc/core/person_amazona_inference.py -> ../../.. = raiz del repo.
# Evita rutas hardcodeadas a C:\Users\... (portabilidad entre equipos).
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')
)


def _safe_report_name(value: Any) -> str:
    """Sanitiza un id (client_id/camera_id, puede ser UUID) para usarlo como
    nombre de archivo del reporte."""
    s = str(value if value is not None else "default")
    return "".join(c for c in s if c.isalnum() or c in "_-")[:64] or "default"

# ── Constantes para el clasificador de género / edad (Caffe DNN) ──────
_MODELS_DIR = os.path.join(_PROJECT_ROOT, 'models', 'classifiers')
_GENDER_PROTO = os.path.join(_MODELS_DIR, 'gender_deploy.prototxt')
_GENDER_MODEL = os.path.join(_MODELS_DIR, 'gender_net.caffemodel')
_AGE_PROTO    = os.path.join(_MODELS_DIR, 'age_deploy.prototxt')
_AGE_MODEL    = os.path.join(_MODELS_DIR, 'age_net.caffemodel')
# ── InsightFace genderage.onnx (primario, mas preciso) ──────────────────
_INSIGHTFACE_GENDERAGE = os.path.join(_MODELS_DIR, 'genderage.onnx')
# ── MiVOLO ONNX (modelo SOTA 2024 opcional para ensemble) ─────────────
# Si esta presente activa ensemble con acuerdo de genero. Descargar de
# https://github.com/WildChlamydia/MiVOLO, convertir a ONNX y copiar a
# models/classifiers/mivolo.onnx
_MIVOLO_MODEL = os.path.join(_MODELS_DIR, 'mivolo.onnx')
# ── ArcFace w600k_r50 ONNX (Face Re-Identification) ──────────────────
# Modelo de embeddings 512-dim para evitar contar la misma persona dos
# veces. Se descarga con scripts/setup_face_embedding.py
_FACE_EMBEDDING_MODEL = os.path.join(_MODELS_DIR, 'w600k_r50.onnx')
# YuNet face detector (2023 mar): detector moderno con keypoints
# (ojos, nariz, boca) que permite alineacion facial. SOTA para
# caras pequenas/ladeadas en CCTV.
_YUNET_MODEL = os.path.join(_MODELS_DIR, 'face_detection_yunet_2023mar.onnx')
# ── PAR (rama corporal E3B): genero desde el cuerpo SIN cara ─────────
# Modelo de atributos de persona (PA-100K/PETA) exportado a ONNX. Solo
# genero. Fallback de la rama corporal cuando MiVOLO body-only no esta.
# Descargar/exportar con scripts/setup_modelos.py
_PAR_MODEL = os.path.join(_MODELS_DIR, 'par_gender.onnx')


# ── Providers ONNX (Hito 6): GPU + flag TensorRT ─────────────────────
_ONNX_DLLS_PRELOADED = False


def _tensorrt_enabled() -> bool:
    """Flag de config (env ENABLE_TENSORRT, default OFF) para TensorRT."""
    return os.environ.get("ENABLE_TENSORRT", "").strip().lower() in (
        "1", "true", "yes", "on")


def _build_onnx_providers() -> list:
    """Lista de execution providers para onnxruntime segun el hardware.

    - Llama `ort.preload_dlls()` una sola vez para que ORT encuentre las
      DLLs de CUDA 12 / cuDNN 9 que trae torch (cinturon de seguridad).
    - Usa CUDA si esta disponible; si no, CPU.
    - Antepone TensorRT solo si ENABLE_TENSORRT esta activo (default OFF).
    """
    global _ONNX_DLLS_PRELOADED
    try:
        import onnxruntime as ort
        if not _ONNX_DLLS_PRELOADED:
            try:
                ort.preload_dlls()
            except Exception:
                pass
            _ONNX_DLLS_PRELOADED = True
        available = ort.get_available_providers()
    except Exception:
        return ['CPUExecutionProvider']
    providers = ['CPUExecutionProvider']
    if 'CUDAExecutionProvider' in available:
        providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
    if _tensorrt_enabled() and 'TensorrtExecutionProvider' in available:
        providers = ['TensorrtExecutionProvider'] + providers
        logger.info("TensorRT habilitado para ONNX (ENABLE_TENSORRT)")
    return providers


class _GpuYuNet:
    """YuNet (face_detection_yunet_2023mar.onnx) sobre onnxruntime-GPU con la
    MISMA interfaz que cv2.FaceDetectorYN: ``setInputSize((w,h))`` +
    ``detect(img) -> (retval, faces)`` con ``faces`` Nx15
    ``[x, y, w, h, 5*(kx, ky), score]`` en pixeles del ``img`` de entrada.

    El modelo onnx tiene input fijo 640x640: se hace letterbox del crop a 640
    (preservando aspecto, pad abajo/derecha) y se mapean las coords de vuelta.
    El decode (priors/bbox/kps) replica EXACTO a cv2 (validado: dbox<0.01px).
    Una sola sesion se comparte entre camaras (ORT run() es thread-safe).
    """

    _INPUT = 640
    _STRIDES = (8, 16, 32)
    is_gpu = True  # marcador para que demographics le pase el crop nativo

    def __init__(self, model_path, score_thr=0.55, nms_thr=0.30, top_k=5000,
                 providers=None):
        import onnxruntime as ort
        self._score_thr = float(score_thr)
        self._nms_thr = float(nms_thr)
        self._top_k = int(top_k)
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self._sess = ort.InferenceSession(
            model_path, sess_options=so, providers=providers)
        self._in_name = self._sess.get_inputs()[0].name
        self._out_names = [o.name for o in self._sess.get_outputs()]
        self._grid = {s: self._INPUT // s for s in self._STRIDES}
        self.providers = self._sess.get_providers()

    def setInputSize(self, size):  # noqa: N802 (compat API cv2)
        # El modelo es 640 fijo; el tamano real se maneja por letterbox interno.
        return None

    def detect(self, img):
        if img is None or getattr(img, 'size', 0) == 0:
            return 0, None
        h, w = img.shape[:2]
        n = self._INPUT
        scale = n / float(max(h, w))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        resized = cv2.resize(img, (nw, nh), interpolation=interp)
        canvas = np.zeros((n, n, 3), np.uint8)
        canvas[:nh, :nw] = resized
        blob = np.ascontiguousarray(
            canvas.astype(np.float32).transpose(2, 0, 1)[None])
        try:
            outs = self._sess.run(self._out_names, {self._in_name: blob})
        except Exception as e:
            logger.debug("GpuYuNet run error: %s", e)
            return 0, None
        omap = {k: v for k, v in zip(self._out_names, outs)}
        dets = self._decode(omap)
        if dets.shape[0] == 0:
            return 0, None
        dets[:, :14] /= scale  # de 640-letterbox de vuelta al img de entrada
        return 1, dets

    def _decode(self, o):
        rows = []
        st = self._score_thr
        for s in self._STRIDES:
            cls = o['cls_%d' % s][0, :, 0]
            obj = o['obj_%d' % s][0, :, 0]
            bbox = o['bbox_%d' % s][0]
            kps = o['kps_%d' % s][0]
            g = self._grid[s]
            score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
            idx = np.where(score >= st)[0]
            if idx.size == 0:
                continue
            r = (idx // g).astype(np.float32)
            c = (idx % g).astype(np.float32)
            cx = (c + bbox[idx, 0]) * s
            cy = (r + bbox[idx, 1]) * s
            ww = np.exp(bbox[idx, 2]) * s
            hh = np.exp(bbox[idx, 3]) * s
            blk = np.stack([cx - ww / 2.0, cy - hh / 2.0, ww, hh], 1)
            kp = np.empty((idx.size, 10), np.float32)
            for k in range(5):
                kp[:, 2 * k] = (c + kps[idx, 2 * k]) * s
                kp[:, 2 * k + 1] = (r + kps[idx, 2 * k + 1]) * s
            rows.append(np.concatenate(
                [blk, kp, score[idx, None]], 1).astype(np.float32))
        if not rows:
            return np.empty((0, 15), np.float32)
        dets = np.concatenate(rows, 0)
        keep = cv2.dnn.NMSBoxes(
            dets[:, :4].tolist(), dets[:, 14].tolist(),
            st, self._nms_thr, top_k=self._top_k)
        if len(keep) == 0:
            return np.empty((0, 15), np.float32)
        keep = np.array(keep).flatten()
        d = dets[keep]
        return d[d[:, 14].argsort()[::-1]]


def _create_yunet(device):
    """Crea el detector YuNet: GPU (onnxruntime CUDA) si hay GPU, con fallback
    automatico a cv2.FaceDetectorYN (CPU). Devuelve un objeto con la interfaz
    setInputSize/detect, o None si no se pudo cargar."""
    if not os.path.exists(_YUNET_MODEL):
        return None
    try:
        import torch
        cuda_ok = torch.cuda.is_available() and str(device) != 'cpu'
    except Exception:
        cuda_ok = False
    if cuda_ok:
        try:
            gy = _GpuYuNet(_YUNET_MODEL, 0.55, 0.30, 5000,
                           providers=_build_onnx_providers())
            if 'CUDAExecutionProvider' in gy.providers:
                logger.info("YuNet en GPU (onnxruntime CUDA, sesion compartida)")
                return gy
            logger.warning(
                "YuNet GPU pedido pero ORT no activo CUDA; uso cv2 CPU")
        except Exception as e:
            logger.warning("No se pudo crear YuNet GPU (%s); uso cv2 CPU", e)
    try:
        y = cv2.FaceDetectorYN_create(
            _YUNET_MODEL, "", (320, 320), 0.55, 0.30, 5000)
        logger.info("YuNet en CPU (cv2.FaceDetectorYN)")
        return y
    except Exception as e:
        logger.warning("No se pudo cargar YuNet: %s", e)
        return None


class _AutoAngleDetector:
    """Deduce automaticamente el angulo de la camara (frontal vs cenital).

    Se basa en la relacion alto/ancho (h/w) de los bboxes de personas:
      - frontal/lateral: personas mas altas que anchas -> h/w alto (~2-3).
      - cenital (techo) : personas vistas desde arriba -> bbox ~cuadrado o
        mas ancho -> h/w bajo (~0.7-1.2).

    Acumula muestras en una ventana y, al superar `min_samples`, clasifica
    por la mediana. Mantiene el resultado estable hasta tener evidencia de
    cambio (se re-evalua continuamente con la ventana deslizante).
    """

    def __init__(self, min_samples: int = 40,
                 cenital_hw_max: float = 1.25, window: int = 250):
        self._ratios = deque(maxlen=window)
        self._min_samples = int(min_samples)
        self._cenital_hw_max = float(cenital_hw_max)
        self._angle: str = None  # type: ignore  # None hasta tener confianza

    def add_person(self, bw: float, bh: float) -> None:
        """Registra el bbox de una persona (ancho, alto en px)."""
        if bw <= 0 or bh <= 0 or bh < 20 or bw < 10:
            return
        self._ratios.append(float(bh) / float(bw))

    def evaluate(self) -> str:
        """Re-clasifica y devuelve 'frontal'|'cenital' o None si no hay datos.

        Usa una banda muerta (histeresis) alrededor del umbral para no
        oscilar en camaras borderline (mediana ~ umbral).
        """
        if len(self._ratios) < self._min_samples:
            return self._angle
        med = float(np.median(self._ratios))
        margin = 0.15
        if self._angle == "cenital":
            if med > self._cenital_hw_max + margin:
                self._angle = "frontal"
        elif self._angle == "frontal":
            if med < self._cenital_hw_max - margin:
                self._angle = "cenital"
        else:  # primera decision
            self._angle = ("cenital" if med < self._cenital_hw_max
                           else "frontal")
        return self._angle

    @property
    def angle(self):
        return self._angle

    @property
    def median_hw(self) -> float:
        return float(np.median(self._ratios)) if self._ratios else 0.0

    @property
    def n_samples(self) -> int:
        return len(self._ratios)


class PersonAmazonas:
    """Procesador para reconocimiento de personal específico"""
    
    # Ruta por defecto del modelo de personas (YOLO estándar COCO)
    _DEFAULT_PERSON_MODEL = os.path.join(
        _PROJECT_ROOT, 'models', 'base', 'yolo26m.pt')

    def __init__(self,
                client_id: None = None,
                model_path: str = None,  # (sin uso) cosmeticos eliminado
                person_model_path: str = None,     # Modelo YOLO para personas
                confidence_threshold: float = 0.7,
                iou_threshold: float = 0.4,
                device: str = 'cuda' if torch.cuda.is_available() else 'cpu',
                log_file: str = "output/detection_log.txt",
                image_quality: int = 70,
                min_time_in_roi: int = 10,
                max_frames_out: int = 5,
                min_track_frames: int = 3,
                show_minimal_info: bool = True,
                exit_frames_threshold: int = 1,
                max_frames_without_detection: int = 5,
                max_image_size: tuple = (640, 480),
                staff_names_file: str = None,
                shared_model: Any = None,
                shared_person_model: Any = None,
                camera_angle: str = None,
                shared_sessions: dict = None):

        try:
            # Sesiones ONNX + reidentifier COMPARTIDOS entre cámaras (los pasa
            # get_camera_processor). Evita recargar ArcFace/MiVOLO/genderage/PAR
            # por cada cámara (ahorra VRAM) y unifica la DB de re-identificación.
            self._shared_sessions = shared_sessions
            self.confidence_threshold = confidence_threshold
            self.iou_threshold = iou_threshold
            # Cosmeticos eliminado: no hay modelo de productos.
            self.model_path = None
            self.person_model_path = person_model_path or self._DEFAULT_PERSON_MODEL
            self.log_file = log_file
            self.image_quality = image_quality
            self.show_minimal_info = show_minimal_info
            self.exit_frames_threshold = exit_frames_threshold
            self.max_image_size = max_image_size
            
            # Configuración de tiempos
            self.min_time_in_roi = min_time_in_roi
            self.max_frames_out = max_frames_out
            self.min_track_frames = min_track_frames
            self.max_frames_without_detection = max_frames_without_detection
            
            # Estado interno
            self.frame_counter = 0
            self.personas_en_area = 0
            self.active_tracks = {}
            self.next_id = 1

            # Tracks ya capturados (foto persona/rostro) para no duplicar.
            self._captured_ids = set()
            # track_id -> stem de su captura, y tracks cuya demografia ya se
            # escribio en el sidecar JSON (para mostrar genero/edad en el dash).
            self._capture_meta = {}
            self._captured_demo_done = set()
            # person_uuid (Re-ID) ya capturados -> stem de SU foto. Dict (no
            # set) a proposito: al deduplicar hay que saber que archivo es el
            # DUENO del uuid para no borrar la foto original como si fuera
            # su propio duplicado.
            self._captured_persons = {}
            # Dedup espacial por celda de grilla: ultima captura por zona de la
            # imagen (cell -> timestamp). Acota a ~1 foto por zona por ventana.
            self._cell_last_capture = {}

            self.track_history = defaultdict(lambda: deque(maxlen=30))
            
            # Contadores por empleado
            self.employee_counters = defaultdict(int)

            # Contadores de entradas por categoría
            self.entry_counts = {'Hombres': 0, 'Mujeres': 0, 'Niños': 0, 'Personas': 0}
            
            # ROI por defecto
            self.roi_polygon = np.array(DEFAULT_ROI if DEFAULT_ROI else [(0, 0), (640, 0), (640, 480), (0, 480)], np.int32)
            
            # Para evitar reconteo
            self.counted_tracks = set()
            self.recent_counted_persons = deque(maxlen=30)
            self.person_cooldown = defaultdict(int)
            
            # Estados de seguimiento
            self.last_counted_frame = 0
            self.last_counted_id = 0
            self.debug_mode = True
            
            # Historial de posiciones para validar movimiento
            self.movement_history = defaultdict(lambda: deque(maxlen=10))

            # Historial de clases por track para estabilizar identidad
            self.class_history_len = 8
            self.class_stability_threshold = 0.6  # proporción para considerar estable
            self.track_class_history = defaultdict(lambda: deque(maxlen=self.class_history_len))
            
            # Nombres del personal (se cargan desde archivo o modelo)
            self.staff_names = {}
            self.load_staff_names(staff_names_file)
            
            # Clases a detectar - SOLO PERSONAL
            self.all_classes = []  # Se determinará después de cargar el modelo
            
            # Para controlar alertas periódicas
            self.alert_minutes_sent = defaultdict(list)
            
            # Para controlar que no se envíen múltiples fotos del mismo evento
            self.sent_entry_photos = defaultdict(lambda: deque(maxlen=2))
            self.sent_exit_photos = defaultdict(lambda: deque(maxlen=2))
            
            # Control de frecuencia de envío
            self.last_sent_time = defaultdict(float)
            self.send_cooldown = 1.0
            
            self.model = None
            self.person_model = None
            self.device = device
            # FP16 (half) en YOLO solo si hay GPU (Turing+ tiene Tensor Cores).
            # En CPU debe ser False (half no soportado / mas lento).
            self._yolo_half = bool(
                torch.cuda.is_available() and str(device) != 'cpu')
            # Tracker de corto plazo (sv.ByteTrack), POR-INSTANCIA = por-camara.
            # Sustituye al BoT-SORT interno de YOLO (que guardaba estado dentro
            # del modelo COMPARTIDO y se mezclaba entre camaras). Cada
            # camera_processor tiene su propio ByteTrack -> tracking aislado.
            self._byte_tracker = self._make_byte_tracker()
            # Zona de conteo (sv.PolygonZone) — Hito 2. Se construye perezosa a
            # partir de roi_polygon y se reconstruye si el poligono cambia.
            self._zone = None
            self._zone_roi_sig = None       # firma del roi_polygon actual
            self._zone_count = 0            # aforo instantaneo (personas dentro)
            # ── Identidad persistente (Hito 4) ──
            # Conjunto de persistent_id (person_uuid) CONFIRMADOS por rostro en
            # esta sesion -> conteo de visitantes unicos SIN inflar por
            # reapariciones (un mismo uuid que reaparece no suma de nuevo).
            # Los tracks sin rostro reciben un id PROVISIONAL y NO entran aqui.
            self._confirmed_unique: set = set()
            # ── Entradas/salidas por persona (Hito 5) ──
            # Maquina de estados de zona POR persistent_id:
            #   _zone_state[pid] = {estado, pending, pending_frames,
            #     conteo_entradas, conteo_salidas, tiempo_total_dentro,
            #     entry_time, last_ts}
            self._zone_state: dict = {}
            self._total_entries: int = 0   # global FUERA->DENTRO confirmadas
            self._total_exits: int = 0     # global DENTRO->FUERA confirmadas
            # persistent_id distintos que ENTRARON al menos una vez (visitantes
            # unicos del AREA; subconjunto de los confirmados que pisaron dentro).
            self._unique_entered: set = set()
            # Reporte consolidado (Hito 8): cacheado + persistido throttled.
            self._last_report_ts: float = 0.0
            self._cached_report = None
            # Identificador del cliente/instancia
            self.client_id = client_id
            logger.info("PersonAmazonas device: %s", device)
            # Si se proporciona un modelo compartido, usarlo y evitar
            # re-inicializar. shared_model (productos) es siempre None.
            if shared_person_model is not None:
                # Caso compartido: la instancia padre ya cargo los modelos
                self.model = shared_model  # puede ser None
                self.person_model = shared_person_model
                if (self.model is not None and
                        hasattr(self.model, 'names') and self.model.names):
                    self.all_classes = list(self.model.names.keys())
                    self.staff_names = {
                        cid: self.model.names[cid]
                            .replace('_', ' ').title()
                        for cid in self.all_classes
                    }
                else:
                    self.all_classes = []
                    self.staff_names = {}
                self.staff_names[-1] = 'Persona'
            else:
                self._initialize_model()

            # Cache de procesadores por cámara (si se usa una instancia compartida)
            self._camera_processors = {}

            # Estado por cámara (separado)
            # Cada camera_id tendrá su propio conjunto de tracks y contadores
            # camera_states[camera_id] = {
            #    'active_tracks': {}, 'next_id': int, 'track_history': defaultdict(deque),
            #    'movement_history': defaultdict(deque), 'employee_counters': defaultdict(int), ...
            # }
            self.camera_states = {}
            self._state_swap_stack = []
            # Lock to prevent concurrent process_frame calls interfering with state swapping
            self._process_lock = threading.RLock()
            # atributo auxiliar para referencia al último frame procesado
            self.last_processed_frame = None

            # Perfiles de cámara por ángulo (detección multi-ángulo): definen
            # el filtro de aspecto que se aplica en process_frame.
            self._init_camera_profiles(camera_angle)

            os.makedirs("output/Personal", exist_ok=True)
            os.makedirs("output/Personal/Entradas", exist_ok=True)
            os.makedirs("output/Personal/Salidas", exist_ok=True)
            os.makedirs("output/Personal/Alertas", exist_ok=True)
            os.makedirs(os.path.dirname(self.log_file) if os.path.dirname(self.log_file) else '.', exist_ok=True)
            
            self._log_buffer = []
            self.setup_log_file()

            # ── Clasificador de género / edad (OpenCV DNN) ──
            self._init_gender_age_classifier()
            # Cadencia de re-clasificacion de un track NO convergido. Antes
            # era 1 (cada frame), lo que con muchas personas sin cara visible
            # disparaba el pipeline facial completo por persona/frame y tumbaba
            # el FPS (~2 fps con 7 personas). Ahora se reintenta cada N frames
            # y ESCALONADO por track_id (ver _track_match_and_classify) para
            # repartir la carga. Configurable en AnalyticsConfig.
            self._classify_every_n = max(
                1, int(getattr(AnalyticsConfig, 'DEMO_RECLASSIFY_EVERY', 6)))
            # Umbral de altura relativa para niño (fallback sin modelo de edad)
            self.child_height_ratio = 0.30

            # ── Modulos de analitica retail ──
            self._analytics_config = AnalyticsConfig
            # Face Re-Identification. Si viene COMPARTIDO (multi-cámara), se
            # reusa: una sola DB en memoria -> conteo único correcto ENTRE
            # cámaras y una sola sesión ArcFace. Es thread-safe (RLock).
            _shared_reid = (self._shared_sessions or {}).get('reidentifier') \
                if getattr(self, '_shared_sessions', None) else None
            if _shared_reid is not None:
                self._reidentifier = _shared_reid
                logger.info("Re-identificador COMPARTIDO reusado (DB única "
                            "entre cámaras)")
            else:
                self._reidentifier = FaceReidentifier(
                    embedding_session=getattr(self, '_face_embedding_session',
                                              None),
                    db_path=AnalyticsConfig.REID_DB_PATH,
                    similarity_threshold=(
                        AnalyticsConfig.REID_SIMILARITY_THRESHOLD),
                    reset_policy=AnalyticsConfig.REID_RESET_POLICY,
                )
                # Guardar la DB al cerrar el proceso (Ctrl+C / shutdown)
                try:
                    import atexit
                    atexit.register(self._reidentifier.force_save)
                except Exception:
                    pass

            # Re-ID CORPORAL (OSNet) — 2a senal para espaldas. COMPARTIDO entre
            # camaras (galeria efimera unica). NUNCA se persiste (la ropa cambia).
            _shared_body = (self._shared_sessions or {}).get('body_reid') \
                if getattr(self, '_shared_sessions', None) else None
            if _shared_body is not None:
                self._body_reidentifier = _shared_body
            elif (AnalyticsConfig.BODY_REID_ENABLED
                  and getattr(self, '_osnet_session', None) is not None):
                self._body_reidentifier = BodyReidentifier(
                    session=self._osnet_session,
                    similarity_threshold=(
                        AnalyticsConfig.BODY_REID_SIMILARITY_THRESHOLD),
                    ttl_seconds=AnalyticsConfig.BODY_REID_TTL_S,
                    max_emb_per_person=AnalyticsConfig.BODY_REID_MAX_EMB_PER_PERSON,
                )
                logger.info("Re-ID corporal (OSNet) ACTIVO")
            else:
                self._body_reidentifier = None

            self._demographics = DemographicsClassifier(
                gender_net=self._gender_net,
                age_net=self._age_net,
                face_cascade=self._face_cascade,
                face_detector_dnn=getattr(self, '_face_dnn', None),
                insightface_session=getattr(self, '_insightface_session', None),
                yunet=getattr(self, '_yunet', None),
                mivolo_session=getattr(self, '_mivolo_session', None),
                reidentifier=self._reidentifier,
                par_session=getattr(self, '_par_session', None),
            )
            self._people_counter = PeopleCounter()
            self._analytics_logger = AnalyticsLogger()
            # Mapa de calor de ocupacion (overlay cliente + snapshot dashboard)
            self._heatmap = HeatmapAccumulator()

            print(f'✅ Modelo de personal inicializado para {client_id}')
            print(f'🖥️  Dispositivo: {self.device}')
            print(f'🎯 Umbral de confianza: {confidence_threshold}')
            print(f'👥 Personal registrado: {len(self.staff_names)} personas')
            print(f'📍 Modo debug: {"ACTIVADO" if self.debug_mode else "DESACTIVADO"}')
        except Exception as e:
            # NO tragar la excepcion: imprimir traza completa y re-lanzar
            # para que el factory en app.py sepa que la instancia no quedo usable.
            print(f"❌ Error fatal en PersonAmazonas.__init__: {e}")
            print(traceback.format_exc())
            logger.error("PersonAmazonas.__init__ fallo: %s", e, exc_info=True)
            raise



    def load_staff_names(self, staff_names_file: str = None):
        """Carga los nombres del personal desde archivo o modelo"""
        try:
            if staff_names_file and os.path.exists(staff_names_file):
                with open(staff_names_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if ':' in line:
                            class_id, name = line.strip().split(':')
                            self.staff_names[int(class_id)] = name
                print(f"📋 Nombres cargados desde archivo: {len(self.staff_names)} empleados")
            else:
                print("ℹ️  No se encontró archivo de nombres, se usarán los del modelo")
        except Exception as e:
            print(f"⚠️  Error cargando nombres: {e}")

    # Compat: algunos caminos de codigo antiguos siguen consultando
    # _ALLOWED_CLASS_IDS. Se calcula dinamicamente tras cargar el modelo.
    _ALLOWED_CLASS_IDS = []

    def _make_byte_tracker(self):
        """Crea un sv.ByteTrack nuevo con los parametros de AnalyticsConfig.
        Uno por camara (stateful). minimum_consecutive_frames = confirmacion
        de track (anti falso-positivo)."""
        return sv.ByteTrack(
            track_activation_threshold=float(
                AnalyticsConfig.BYTETRACK_TRACK_ACTIVATION_THRESH),
            lost_track_buffer=int(AnalyticsConfig.BYTETRACK_LOST_BUFFER),
            minimum_matching_threshold=float(
                AnalyticsConfig.BYTETRACK_MATCH_THRESH),
            frame_rate=float(AnalyticsConfig.BYTETRACK_FRAME_RATE),
            minimum_consecutive_frames=int(
                AnalyticsConfig.BYTETRACK_MIN_CONSECUTIVE_FRAMES),
        )

    def _get_zone(self):
        """Devuelve el sv.PolygonZone del area de conteo (roi_polygon),
        reconstruyendolo si el poligono cambio (set_roi / cambio en frame).
        El ancla de disparo (pie/centro) es configurable via
        AnalyticsConfig.ZONE_TRIGGER_ANCHOR.

        Retorna:
            sv.PolygonZone | None: la zona, o None si no hay roi_polygon.
        """
        roi = getattr(self, 'roi_polygon', None)
        if roi is None:
            return None
        sig = roi.tobytes()
        if self._zone is None or sig != self._zone_roi_sig:
            anchor = getattr(sv.Position,
                             str(AnalyticsConfig.ZONE_TRIGGER_ANCHOR),
                             sv.Position.BOTTOM_CENTER)
            # sv 0.28.0: PolygonZone(polygon, triggering_anchors=...) — SIN
            # frame_resolution_wh (param eliminado; la mascara se dimensiona
            # del propio poligono). Verificado contra el codigo instalado.
            self._zone = sv.PolygonZone(
                polygon=roi.astype(int),
                triggering_anchors=(anchor,),
            )
            self._zone_roi_sig = sig
        return self._zone

    def _resolve_detector_path(self):
        """Cargador inteligente del detector de personas. Prefiere el engine
        TensorRT FP16 construido para el imgsz de produccion
        (`<modelo>_<imgsz>.engine`) por su ~2.7x de velocidad; cae al .pt si no
        existe el engine, no hay CUDA, o el imgsz no coincide (el engine es de
        forma FIJA -> usarlo con otro imgsz lo correria mal). Devuelve
        (ruta, es_engine)."""
        base = self.person_model_path  # p.ej. .../models/base/yolo26m.pt
        try:
            if not bool(AnalyticsConfig.PERSON_DETECT_PREFER_ENGINE):
                return base, False  # forzar .pt (mas portable/exacto)
            imgsz = int(AnalyticsConfig.PERSON_DETECT_IMGSZ)
            root, _ext = os.path.splitext(base)
            engine = f"{root}_{imgsz}.engine"
            if (torch.cuda.is_available() and str(self.device) != 'cpu'
                    and os.path.exists(engine)):
                return engine, True
        except Exception as e:
            print(f"⚠️ _resolve_detector_path fallback a .pt: {e}")
        return base, False

    def _initialize_model(self):
        try:
            # ── Cosmeticos ELIMINADO ──
            # El sistema es exclusivamente deteccion de personas + genero +
            # edad. No se carga ningun modelo de productos.
            self.model = None
            self.all_classes = []
            self.staff_names = {}
            type(self)._ALLOWED_CLASS_IDS = []

            # ── Modelo de personas: YOLO estándar (personas COCO clase 0) ──
            print(f"🚀 Inicializando modelo personas: "
                  f"{self.person_model_path}")
            detector_path, is_engine = self._resolve_detector_path()
            self._detector_is_engine = is_engine
            if is_engine:
                # Engine TensorRT: NO lleva .to(device) (no es nn.Module); su
                # forma/half estan baked. task='detect' explicito.
                self.person_model = YOLO(detector_path, task='detect')
                print(f"⚡ Detector personas: TensorRT engine FP16 "
                      f"-> {detector_path}")
            else:
                self.person_model = YOLO(detector_path).to(self.device)
                print(f"✅ Detector personas: PyTorch -> {detector_path}")
            # Usamos class_id -1 internamente para personas
            self.staff_names[-1] = 'Persona'

            # Calentamiento. El engine es de forma FIJA -> hay que calentarlo a
            # su imgsz de construccion (PERSON_DETECT_IMGSZ); el .pt acepta
            # cualquiera (320 basta para warmup).
            _warm = (int(AnalyticsConfig.PERSON_DETECT_IMGSZ)
                     if is_engine else 320)
            dummy_input = np.zeros((_warm, _warm, 3), dtype=np.uint8)
            if self.model is not None:
                _ = self.model.predict(
                    dummy_input, imgsz=320, device=self.device,
                    classes=self.all_classes, verbose=False
                )
            _ = self.person_model.predict(
                dummy_input, imgsz=_warm, device=self.device,
                classes=[0], verbose=False
            )
            print("✅ Modelos inicializados (personas + demografia)")
        except Exception as e:
            print(f"❌ Error inicializando modelos: {e}")
            raise

    def setup_log_file(self):
        try:
            with open(self.log_file, 'w', encoding="utf-8") as f:
                f.write("Timestamp,Frame,Empleado_ID,Empleado_Nombre,Evento,Tiempo_Area,Confianza\n")
        except Exception as e:
            print(f"❌ Error creando archivo de log: {e}")

    def calculate_iou(self, box1: Tuple, box2: Tuple) -> float:
        x11, y11, x21, y21 = box1
        x1_2, y1_2, x2_2, y2_2 = box2
        xi1 = max(x11, x1_2)
        yi1 = max(y11, y1_2)
        xi2 = min(x21, x2_2)
        yi2 = min(y21, y2_2)
        inter_area = max(0, xi2 - xi1) * max(0, yi2 - yi1)
        box1_area = (x21 - x11) * (y21 - y11)
        box2_area = (x2_2 - x1_2) * (y2_2 - y1_2)
        union_area = box1_area + box2_area - inter_area
        return inter_area / union_area if union_area > 0 else 0

    def center_of(self, box: Tuple) -> Tuple[float, float]:
        x1, y1, x2, y2 = box
        return ((x1 + x2) * 0.5, (y1 + y2) * 0.5)

    def is_inside_polygon(self, point: Tuple, polygon: np.ndarray) -> bool:
        return cv2.pointPolygonTest(polygon, (int(point[0]), int(point[1])), False) >= 0

    def compress_image(self, image: np.ndarray) -> np.ndarray:
        try:
            height, width = image.shape[:2]
            max_width, max_height = self.max_image_size
            
            if width > max_width or height > max_height:
                aspect_ratio = width / height
                if width > max_width:
                    width = max_width
                    height = int(width / aspect_ratio)
                if height > max_height:
                    height = max_height
                    width = int(height * aspect_ratio)
                
                image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
            
            return image
        except Exception as e:
            logger.error(f"Error al comprimir imagen: {e}")
            return image

    def get_staff_name(self, class_id: int, confidence: float = None) -> str:
        """Obtiene el nombre legible del empleado"""
        if class_id in self.staff_names:
            name = self.staff_names[class_id]
            if confidence:
                return f"{name} ({confidence:.2f})"
            return name
        else:
            if confidence:
                return f"Empleado_{class_id} ({confidence:.2f})"
            return f"Empleado_{class_id}"

    def get_staff_display_name(self, class_id: int) -> str:
        """Nombre para mostrar en pantalla"""
        if class_id in self.staff_names:
            return self.staff_names[class_id]
        return f"ID_{class_id}"

    # ── Clasificación por categoría (Hombre / Mujer / Niño) ──────
    _CATEGORY_KEYWORDS = {
        'Hombres': ['hombre', 'hombres', 'man', 'male', 'boy', 'chico'],
        'Mujeres': ['mujer', 'mujeres', 'woman', 'female', 'girl', 'chica'],
        'Niños':   ['niño', 'niña', 'niños', 'child', 'kid', 'infant', 'bebe', 'baby'],
    }

    def _classify_category(self, class_id: int) -> str:
        """Clasifica un class_id del modelo en Hombres/Mujeres/Niños/Personas."""
        name = self.get_staff_display_name(class_id).lower().strip()
        for category, keywords in self._CATEGORY_KEYWORDS.items():
            if any(kw in name for kw in keywords):
                return category
        return 'Personas'

    # ── Clasificador de género / edad con OpenCV DNN ──────────────

    def _init_camera_profiles(self, camera_angle: str = None):
        """Carga los perfiles de cámara por ángulo desde la config.

        Determinan el filtro de aspecto del detector (ver `_aspect_ratio_min_for`):
        en cenital se desactiva, porque una persona vista desde arriba se ve
        más ancha que alta y el filtro clásico la descartaría.

        Precedencia del ángulo: override manual del cliente (`camera_angle`)
        > ángulo AUTO-DETECTADO > mapa por camera_id en config > por defecto.

        Si `camera_angle` es None o 'auto', se activa la auto-detección: el
        ángulo se deduce solo de la forma de los bboxes de personas.
        """
        # Fallbacks defensivos (si la config no estuviera disponible)
        self._camera_angle_presets = dict(CAMERA_ANGLE_PRESETS)
        self._camera_angle_by_id: Dict[str, str] = {}
        self._default_camera_angle = DEFAULT_CAMERA_ANGLE
        try:
            cfg = get_config(validate=False)
            self._camera_angle_presets = cfg.get(
                "camera_angle_presets", self._camera_angle_presets)
            self._default_camera_angle = cfg.get(
                "default_camera_angle", DEFAULT_CAMERA_ANGLE)
            self._camera_angle_by_id = {
                str(k): str(v)
                for k, v in cfg.get("camera_angle_by_id", {}).items()
            }
        except Exception as e:  # noqa: BLE001 - degradar con warning, no crashear
            logger.warning(
                "No se pudieron leer perfiles de cámara de config (%s). "
                "Uso defaults.", e)

        # ── Auto-detección de ángulo ──
        self._auto_angle_detector = _AutoAngleDetector(
            min_samples=AUTO_ANGLE_MIN_SAMPLES,
            cenital_hw_max=AUTO_ANGLE_CENITAL_HW_MAX,
            window=AUTO_ANGLE_WINDOW)
        self._auto_angle: Optional[str] = None
        # Aplicar el ángulo inicial (puede ser 'auto' -> activa detección)
        self._camera_angle_override = None
        self._auto_angle_enabled = bool(AUTO_ANGLE_DEFAULT)
        self.set_camera_angle(camera_angle if camera_angle else "auto")
        logger.info(
            "Perfiles de cámara: default=%s, override=%s, auto=%s, por_id=%s",
            self._default_camera_angle, self._camera_angle_override,
            self._auto_angle_enabled, self._camera_angle_by_id or "{}")

    def _aspect_ratio_min_for(self, camera_id: Any) -> float:
        """Devuelve el aspect_ratio_min (alto/ancho) del perfil de la cámara.

        0.0 => filtro de aspecto desactivado (cenital). Un bbox de persona se
        descarta solo si `aspect_ratio_min > 0` y es más ancho que alto según
        ese umbral.
        """
        angle = (
            self._camera_angle_override                       # manual cliente
            or (self._auto_angle if self._auto_angle_enabled else None)  # auto
            or self._camera_angle_by_id.get(str(camera_id))   # mapa config
            or self._default_camera_angle
            or DEFAULT_CAMERA_ANGLE
        )
        preset = self._camera_angle_presets.get(angle.lower().strip())
        if preset is None:
            preset = self._camera_angle_presets.get(
                DEFAULT_CAMERA_ANGLE, {"aspect_ratio_min": 0.80})
        return float(preset.get("aspect_ratio_min", 0.80))

    def set_camera_angle(self, angle: str):
        """Fija el ángulo de esta cámara en runtime.

        - 'auto' o vacío: activa la AUTO-detección (el ángulo se deduce de la
          forma de los bboxes de personas).
        - 'frontal'|'lateral'|'cenital': override MANUAL (gana sobre la
          auto-detección hasta que se vuelva a poner 'auto').
        """
        a = (angle or "").lower().strip()
        if a in ("", "auto"):
            self._camera_angle_override = None
            self._auto_angle_enabled = True
        else:
            self._camera_angle_override = a
            self._auto_angle_enabled = False
            logger.info("Ángulo de cámara override MANUAL -> %s", a)

    def _init_gender_age_classifier(self):
        """Carga los modelos Caffe de género y edad. Si faltan, desactiva.

        Adicionalmente, intenta cargar el modelo InsightFace genderage.onnx
        como PRIMARIO (mucho mas preciso que el Caffe Levi-Hassner 2015).
        Si ONNX no esta disponible, cae a Caffe automaticamente.
        """
        self._gender_net = None
        self._age_net = None
        self._face_cascade = None
        self._face_dnn = None
        self._yunet = None
        self._insightface_session = None
        self._mivolo_session = None
        self._face_embedding_session = None
        self._par_session = None
        self._osnet_session = None   # Re-ID corporal (OSNet ONNX) — Hito 9

        # Reuso de sesiones ONNX COMPARTIDAS (multi-cámara): si las hay, se
        # asignan aquí y NO se recargan abajo (ahorra VRAM). Los detectores
        # OpenCV (YuNet/Caffe/Haar/Res10) SÍ se cargan por cámara (pequeños y
        # potencialmente no thread-safe).
        _use_shared = bool(getattr(self, '_shared_sessions', None))
        if _use_shared:
            _ss = self._shared_sessions
            self._insightface_session = _ss.get('insightface')
            self._face_embedding_session = _ss.get('embedding')
            self._mivolo_session = _ss.get('mivolo')
            self._par_session = _ss.get('par')
            self._osnet_session = _ss.get('osnet')
            # YuNet GPU compartido (thread-safe). Si es None se crea abajo.
            self._yunet = _ss.get('yunet')
            logger.info("Clasificador ONNX: sesiones COMPARTIDAS reusadas "
                        "(sin recargar; ahorra VRAM en multi-cámara)")

        try:
            if os.path.exists(_GENDER_MODEL) and os.path.exists(_GENDER_PROTO):
                self._gender_net = cv2.dnn.readNet(_GENDER_MODEL, _GENDER_PROTO)
                logger.info("Gender classifier loaded")
            else:
                logger.warning(f"Gender model not found in {_MODELS_DIR}")

            if os.path.exists(_AGE_MODEL) and os.path.exists(_AGE_PROTO):
                self._age_net = cv2.dnn.readNet(_AGE_MODEL, _AGE_PROTO)
                logger.info("Age classifier loaded")
            else:
                logger.warning(f"Age model not found in {_MODELS_DIR}")

            # YuNet face detector (primario moderno, devuelve 5 keypoints).
            # Ahora corre en GPU via onnxruntime (sesion compartida thread-safe);
            # cae a cv2.FaceDetectorYN (CPU) si no hay GPU. Antes iba en CPU por
            # el OOM de OpenCV-CUDA en 8GB (ver _restore_opencv_cuda); con
            # onnxruntime-GPU no hay workspace cuDNN por camara -> VRAM plana.
            if self._yunet is None:
                self._yunet = _create_yunet(self.device)

            # Face detector DNN (Res10 SSD) como fallback secundario
            face_proto = os.path.join(_MODELS_DIR, 'opencv_face_detector.pbtxt')
            face_model = os.path.join(_MODELS_DIR, 'opencv_face_detector_uint8.pb')
            if os.path.exists(face_proto) and os.path.exists(face_model):
                try:
                    self._face_dnn = cv2.dnn.readNetFromTensorflow(face_model, face_proto)
                    logger.info("Face detector DNN loaded (Res10 SSD)")
                except Exception as e:
                    logger.warning(f"No se pudo cargar face DNN: {e}")
                    self._face_dnn = None

            # InsightFace genderage.onnx (primario, preciso >95%)
            if not _use_shared and os.path.exists(_INSIGHTFACE_GENDERAGE):
                try:
                    import onnxruntime as ort
                    providers = _build_onnx_providers()
                    self._insightface_session = ort.InferenceSession(
                        _INSIGHTFACE_GENDERAGE, providers=providers
                    )
                    logger.info("InsightFace genderage ONNX loaded")
                except Exception as e:
                    logger.warning(f"No se pudo cargar InsightFace ONNX: {e}")
                    self._insightface_session = None
            elif not _use_shared:
                logger.info("InsightFace genderage.onnx no presente, "
                            "usando Caffe como primario")

            # ArcFace w600k_r50 ONNX (Face Re-Identification)
            if (AnalyticsConfig.REID_ENABLED and not _use_shared and
                    os.path.exists(_FACE_EMBEDDING_MODEL)):
                try:
                    import onnxruntime as ort
                    providers = _build_onnx_providers()
                    self._face_embedding_session = ort.InferenceSession(
                        _FACE_EMBEDDING_MODEL, providers=providers
                    )
                    logger.info(
                        "ArcFace w600k_r50 loaded -> "
                        "Face Re-Identification activa"
                    )
                except Exception as e:
                    logger.warning(
                        f"No se pudo cargar ArcFace embedding: {e}"
                    )
                    self._face_embedding_session = None
            elif not AnalyticsConfig.REID_ENABLED:
                logger.info("Face Re-Identification deshabilitada en config")
            elif not _use_shared:
                logger.info(
                    "ArcFace no presente. Descargar con: "
                    "python scripts/setup_face_embedding.py"
                )

            # MiVOLO ONNX (SOTA 2024, opcional para ensemble)
            if not _use_shared and os.path.exists(_MIVOLO_MODEL):
                try:
                    import onnxruntime as ort
                    providers = _build_onnx_providers()
                    self._mivolo_session = ort.InferenceSession(
                        _MIVOLO_MODEL, providers=providers
                    )
                    logger.info("MiVOLO ONNX loaded -> ensemble activado")
                except Exception as e:
                    logger.warning(f"No se pudo cargar MiVOLO ONNX: {e}")
                    self._mivolo_session = None
            elif not _use_shared:
                logger.info(
                    "MiVOLO no presente en %s. Para activar ensemble "
                    "SOTA descargar de https://github.com/WildChlamydia/MiVOLO",
                    _MIVOLO_MODEL
                )

            # PAR ONNX (rama corporal E3B: genero desde el cuerpo sin cara)
            if not _use_shared and os.path.exists(_PAR_MODEL):
                try:
                    import onnxruntime as ort
                    providers = _build_onnx_providers()
                    self._par_session = ort.InferenceSession(
                        _PAR_MODEL, providers=providers
                    )
                    logger.info("PAR ONNX loaded -> rama corporal (genero) activa")
                except Exception as e:
                    logger.warning(f"No se pudo cargar PAR ONNX: {e}")
                    self._par_session = None
            elif not _use_shared:
                logger.info(
                    "PAR no presente en %s. La rama corporal usara MiVOLO "
                    "body-only si esta, o quedara inactiva. Generar con "
                    "scripts/setup_modelos.py", _PAR_MODEL
                )

            # OSNet ONNX (Re-ID corporal/vestimenta — 2a senal para espaldas)
            _osnet_path = os.path.join(
                _PROJECT_ROOT, AnalyticsConfig.BODY_REID_MODEL)
            if (AnalyticsConfig.BODY_REID_ENABLED and not _use_shared
                    and os.path.exists(_osnet_path)):
                try:
                    import onnxruntime as ort
                    providers = _build_onnx_providers()
                    self._osnet_session = ort.InferenceSession(
                        _osnet_path, providers=providers)
                    logger.info("OSNet ONNX loaded -> Re-ID corporal activo")
                except Exception as e:
                    logger.warning(f"No se pudo cargar OSNet ONNX: {e}")
                    self._osnet_session = None
            elif AnalyticsConfig.BODY_REID_ENABLED and not _use_shared:
                logger.info(
                    "OSNet no presente en %s. Re-ID corporal inactivo "
                    "(exportar con torchreid).", _osnet_path)

            # Haar cascade (fallback y default)
            self._face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            )

            has_gender = self._gender_net is not None
            has_age = self._age_net is not None
            has_face_dnn = self._face_dnn is not None
            has_yunet = self._yunet is not None
            has_insight = self._insightface_session is not None
            has_mivolo = self._mivolo_session is not None
            ensemble_n = sum([has_insight, has_mivolo, has_gender])
            primary = "MiVOLO-ONNX" if has_mivolo else (
                "InsightFace-ONNX" if has_insight else (
                    "Caffe" if has_gender else "NONE"
                )
            )
            detector = "YuNet+keypoints+pose" if has_yunet else (
                "Res10-SSD" if has_face_dnn else "Haar"
            )
            has_reid = self._face_embedding_session is not None
            print(f"🧑 Clasificador primario: {primary} | "
                  f"Ensemble: {ensemble_n} modelos | "
                  f"Detector: {detector} | "
                  f"MiVOLO: {'OK' if has_mivolo else 'NO'} | "
                  f"InsightFace: {'OK' if has_insight else 'NO'} | "
                  f"Caffe género: {'OK' if has_gender else 'NO'} | "
                  f"Caffe edad: {'OK' if has_age else 'NO'} | "
                  f"Re-ID biométrico: {'OK' if has_reid else 'NO'}")
        except Exception as e:
            logger.error(f"Error loading gender/age classifier: {e}")

    def _classify_person(self, frame: np.ndarray, bbox, track_id: int = None) -> str:
        """Clasifica persona por genero/edad usando DemographicsClassifier.

        Retorna categoria legacy ('Hombres', 'Mujeres', 'Ninos') para
        compatibilidad con el sistema existente de contadores.
        """
        if track_id is not None and hasattr(self, '_demographics'):
            result = self._demographics.classify(frame, bbox, track_id)
            # Log de clasificacion
            if hasattr(self, '_analytics_logger'):
                self._analytics_logger.log_demographic(
                    track_id, result['gender'], result['age_range']
                )
            return result.get('category', 'Hombres')
        return 'Hombres'

    async def send_alert(self, base64_img: str, text: str):
        """Envía una alerta con imagen"""
        payload = {
            "my-text": text, 
            "my-file": base64_img, 
            "type": "image/jpg",
            "compressed": "true",
            "quality": str(self.image_quality)
        }
        
        payload_size = len(base64_img) / 1024
        if self.debug_mode:
            print(f"📦 Tamaño del payload: {payload_size:.2f} KB")
        
        if payload_size > 500:
            logger.warning(f"Payload demasiado grande ({payload_size:.2f} KB)")
            payload = {
                "my-text": f"{text} (Imagen muy grande: {payload_size:.2f} KB)",
                "type": "text"
            }
        
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            try: 
                respuesta = await client.post(
                    "https://72.68.60.254:4000/bot/imgV2/number=120363167230826480@g.us",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                respuesta.raise_for_status()
                logger.info(f"✅ Alerta enviada: {respuesta.status_code}")
                return respuesta.json()
            except Exception as e:
                logger.error(f"❌ Error en envío: {e}")
                raise

    def send_alert_wrapper(self, base64_img: str, text: str, object_id: int):
        current_time = time.time()
        last_time = self.last_sent_time.get(object_id, 0)
        
        if current_time - last_time < self.send_cooldown:
            if self.debug_mode:
                print(f"⏳ Cooldown para objeto {object_id}")
            return
        
        def send_async():
            try:
                asyncio.run(self.send_alert(base64_img, text))
                self.last_sent_time[object_id] = current_time
            except RuntimeError as e:
                if "cannot be called from a running event loop" in str(e):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self.send_alert(base64_img, text))
                        self.last_sent_time[object_id] = current_time
                    finally:
                        loop.close()
                else:
                    logger.error(f"Envío falló: {e}")
            except Exception as e:
                logger.error(f"Envío falló: {e}")
        
        thread = threading.Thread(target=send_async)
        thread.daemon = True
        thread.start()

    def create_annotated_image(self, frame: np.ndarray, staff_id: int, object_id: int, confidence: float = None) -> np.ndarray:
        annotated_frame = frame.copy()
        
        if object_id in self.active_tracks:
            track = self.active_tracks[object_id]
            x1, y1, x2, y2 = [int(v) for v in track['box']]
            
            # Color diferente por empleado (basado en ID)
            color_map = [
                (0, 255, 255),   # Amarillo - ID 0
                (255, 0, 0),     # Azul - ID 1
                (0, 255, 0),     # Verde - ID 2
                (255, 0, 255),   # Magenta - ID 3
                (0, 165, 255),   # Naranja - ID 4
                (255, 255, 0),   # Cyan - ID 5
                (128, 0, 128),   # Púrpura - ID 6
                (255, 192, 203)  # Rosa - ID 7
            ]
            
            color_idx = staff_id % len(color_map)
            box_color = color_map[color_idx]
            
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), box_color, 3)
            
            # Etiqueta con nombre y confianza
            staff_name = self.get_staff_name(staff_id, confidence)
            label = f"{staff_name}"
            
            # Añadir tiempo en área si está dentro del ROI
            if 'entry_time' in track:
                current_time = time.time()
                time_in_roi = int(current_time - track['entry_time'])
                minutes = time_in_roi // 60
                seconds = time_in_roi % 60
                label += f" - {minutes}m {seconds}s"
            
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.6
            thickness = 2
            
            (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            
            # Fondo para el texto
            cv2.rectangle(annotated_frame, 
                        (x1, y1 - text_height - 10), 
                        (x1 + text_width, y1), 
                        (0, 0, 0), 
                        -1)
            
            # Texto
            cv2.putText(annotated_frame, label,
                      (x1, y1 - 5), font, font_scale,
                      (255, 255, 255), thickness)
        
        return annotated_frame

    def get_action_message(self, staff_id: int, event: str, time_in_roi: int = 0, confidence: float = None) -> str:
        staff_name = self.get_staff_display_name(staff_id)
        
        if event == 'entrada':
            return f"👤 {staff_name} entró en el área de oficina"
        elif event == 'salida':
            minutes = time_in_roi // 60
            if minutes == 1:
                return f"👤 {staff_name} salió después de {minutes} minuto"
            else:
                return f"👤 {staff_name} salió después de {minutes} minutos"
        elif event == 'alerta_periodica':
            minutes = time_in_roi // 60
            if minutes == 1:
                return f"⏰ {staff_name} lleva {minutes} minuto en el área"
            else:
                return f"⏰ {staff_name} lleva {minutes} minutos en el área"
        else:
            return f"{staff_name} - {event.upper()}"

    def save_staff_photo(self, frame: np.ndarray, staff_id: int, object_id: int, event: str, 
                         confidence: float = None, time_in_roi: int = 0):
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            staff_name_safe = self.get_staff_display_name(staff_id).replace(' ', '_')
            
            event_dir = os.path.join("output/Personal", event.capitalize())
            os.makedirs(event_dir, exist_ok=True)
            
            filename = f"{staff_name_safe}_{event}_{object_id}_{timestamp}.jpg"
            filepath = os.path.join(event_dir, filename)
            
            annotated_frame = self.create_annotated_image(frame, staff_id, object_id, confidence)
            compressed_frame = self.compress_image(annotated_frame)
            
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.image_quality]
            success, buffer = cv2.imencode('.jpg', compressed_frame, encode_params)
            
            if success:
                imagen_base64 = base64.b64encode(buffer).decode('utf-8')
                base64_size_kb = len(imagen_base64) / 1024
                
                if base64_size_kb > 500:
                    encode_params = [cv2.IMWRITE_JPEG_QUALITY, 40]
                    success, buffer = cv2.imencode('.jpg', compressed_frame, encode_params)
                    if success:
                        imagen_base64 = base64.b64encode(buffer).decode('utf-8')
                        base64_size_kb = len(imagen_base64) / 1024
                
                message = self.get_action_message(staff_id, event, time_in_roi, confidence)
                self.send_alert_wrapper(imagen_base64, message, object_id)
                
                with open(filepath, 'wb') as f:
                    f.write(buffer)
                
                # Registrar en log
                self._log_staff_event(staff_id, event, time_in_roi, confidence)
                
                logger.info(f"✅ Foto de {event} guardada: {filename}")
                if self.debug_mode:
                    minutes = time_in_roi // 60
                    seconds = time_in_roi % 60
                    time_info = f" - Tiempo: {minutes}m {seconds}s" if time_in_roi > 0 else ""
                    print(f"📸 {message}{time_info} ({base64_size_kb:.2f} KB)")
                
                return True
            return False
        except Exception as e:
            logger.error(f"No se pudo guardar la foto: {e}")
            return False

    def _log_staff_event(self, staff_id: int, event: str, time_in_roi: int = 0, confidence: float = None):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        staff_name = self.get_staff_display_name(staff_id)
        minutes = time_in_roi // 60
        seconds = time_in_roi % 60
        
        log_entry = f"{ts},{self.frame_counter},{staff_id},{staff_name},{event},{minutes}:{seconds}"
        if confidence:
            log_entry += f",{confidence:.2f}"
        else:
            log_entry += ",N/A"
        
        with open(self.log_file, 'a', encoding="utf-8") as f:
            f.write(f"{log_entry}\n")

    def _get_cam_state(self, camera_id: Any) -> Dict[str, Any]:
        if camera_id not in self.camera_states:
            self.camera_states[camera_id] = {
                'active_tracks': {},
                'next_id': 1,
                'track_history': defaultdict(lambda: deque(maxlen=30)),
                'movement_history': defaultdict(lambda: deque(maxlen=10)),
                'employee_counters': defaultdict(int),
                'counted_tracks': set(),
                'recent_counted_persons': deque(maxlen=30),
                'person_cooldown': defaultdict(int),
                'alert_minutes_sent': defaultdict(list),
                'sent_entry_photos': defaultdict(lambda: deque(maxlen=2)),
                'sent_exit_photos': defaultdict(lambda: deque(maxlen=2)),
                'last_sent_time': defaultdict(float),
                'last_processed_frame': None,
                'personas_en_area': 0,
                'entry_counts': {'Hombres': 0, 'Mujeres': 0, 'Niños': 0, 'Personas': 0},
            }
        # Migrar estados que no tengan entry_counts
        state = self.camera_states[camera_id]
        if 'entry_counts' not in state:
            state['entry_counts'] = {'Hombres': 0, 'Mujeres': 0, 'Niños': 0, 'Personas': 0}
        # Migrar analytics por camara
        if '_demographics' not in state:
            state['_demographics'] = DemographicsClassifier(
                gender_net=self._gender_net,
                age_net=self._age_net,
                face_cascade=self._face_cascade,
                face_detector_dnn=getattr(self, '_face_dnn', None),
                insightface_session=getattr(self, '_insightface_session', None),
                yunet=getattr(self, '_yunet', None),
                mivolo_session=getattr(self, '_mivolo_session', None),
                reidentifier=getattr(self, '_reidentifier', None),
                par_session=getattr(self, '_par_session', None),
                # Perfil por camara: aplica los overrides de
                # config/camera_profiles/<camera_id>.json si existe.
                camera_id=camera_id,
            )
            state['_people_counter'] = PeopleCounter()
            state['_analytics_logger'] = AnalyticsLogger(
                log_path=f"output/analytics_log_{camera_id}.jsonl"
            )
            state['_heatmap'] = HeatmapAccumulator()
            # ByteTrack propio por camara (stateful) -> tracking aislado en la
            # ruta de swap de estado (igual que active_tracks).
            state['_byte_tracker'] = self._make_byte_tracker()
            # Set de unicos confirmados por camara (Hito 4).
            state['_confirmed_unique'] = set()
            # Maquina entrada/salida por persona y globales (Hito 5).
            state['_zone_state'] = {}
            state['_total_entries'] = 0
            state['_total_exits'] = 0
            state['_unique_entered'] = set()
        return state

    def get_shared_sessions(self) -> dict:
        """Devuelve las sesiones ONNX + reidentifier para compartir entre
        cámaras (las usa get_camera_processor). Solo se comparten objetos
        thread-safe: sesiones ORT y el reidentifier (con RLock)."""
        return {
            'insightface': getattr(self, '_insightface_session', None),
            'embedding': getattr(self, '_face_embedding_session', None),
            'mivolo': getattr(self, '_mivolo_session', None),
            'par': getattr(self, '_par_session', None),
            'reidentifier': getattr(self, '_reidentifier', None),
            # YuNet GPU (onnxruntime) ES thread-safe -> se comparte entre camaras
            # (no se duplica por camara como el cv2.FaceDetectorYN de CPU).
            'yunet': getattr(self, '_yunet', None),
            # Re-ID corporal (OSNet) — sesion + galeria efimera compartidas.
            'osnet': getattr(self, '_osnet_session', None),
            'body_reid': getattr(self, '_body_reidentifier', None),
        }

    def get_camera_processor(self, camera_id: Any):
        """Return or create a per-camera PersonAmazonas instance that shares the heavy model.

        This keeps tracking state isolated per camera while sharing the detection model.
        """
        if camera_id in self._camera_processors:
            return self._camera_processors[camera_id]

        # Crear nueva instancia ligera que comparte ambos modelos
        cam_proc = PersonAmazonas(
            client_id=f"{self.client_id}_{camera_id}",
            model_path=self.model_path,
            person_model_path=self.person_model_path,
            confidence_threshold=self.confidence_threshold,
            iou_threshold=self.iou_threshold,
            device=self.device,
            log_file=self.log_file,
            image_quality=self.image_quality,
            min_time_in_roi=self.min_time_in_roi,
            max_frames_out=self.max_frames_out,
            min_track_frames=self.min_track_frames,
            show_minimal_info=self.show_minimal_info,
            exit_frames_threshold=self.exit_frames_threshold,
            max_frames_without_detection=self.max_frames_without_detection,
            max_image_size=self.max_image_size,
            staff_names_file=None,
            shared_model=self.model,
            shared_person_model=self.person_model,
            shared_sessions=self.get_shared_sessions(),
        )

        # Keep camera-specific state separate
        # Copy model-related metadata so camera instance can use class names and filters
        try:
            cam_proc.staff_names = dict(self.staff_names)
            cam_proc.all_classes = list(self.all_classes)
            cam_proc.roi_polygon = np.array(self.roi_polygon)
            # Los modulos de analytics ya se inicializan en __init__ con sus propias
            # instancias independientes, asi que cada camera_processor tiene su propio estado
        except Exception:
            pass

        self._camera_processors[camera_id] = cam_proc
        return cam_proc

    def _push_state(self, cam_state: Dict[str, Any]):
        # Guardar punteros actuales
        names = [
            'active_tracks', 'next_id', 'track_history', 'movement_history', 'employee_counters',
            'counted_tracks', 'recent_counted_persons', 'person_cooldown', 'alert_minutes_sent',
            'sent_entry_photos', 'sent_exit_photos', 'last_sent_time', 'last_processed_frame', 'personas_en_area',
            'entry_counts',
            '_demographics', '_people_counter', '_analytics_logger',
            '_heatmap', '_byte_tracker', '_confirmed_unique',
            '_zone_state', '_total_entries', '_total_exits', '_unique_entered',
        ]
        saved = {}
        for n in names:
            saved[n] = getattr(self, n, None)
            # asignar el estado de la cámara, si existe la clave
            setattr(self, n, cam_state.get(n))
        self._state_swap_stack.append(saved)

    def _pop_state(self):
        if not self._state_swap_stack:
            return
        saved = self._state_swap_stack.pop()
        for n, v in saved.items():
            setattr(self, n, v)

    def check_periodic_alerts(self, frame: np.ndarray):
        current_time = time.time()
        roi_polygon_points = self.roi_polygon.reshape((-1, 1, 2))
        
        for track_id, track in list(self.active_tracks.items()):
            current_pos = track['center']
            is_inside = self.is_inside_polygon(current_pos, roi_polygon_points)
            
            if is_inside and track['class'] in self.staff_names:
                if 'entry_time' not in track:
                    track['entry_time'] = current_time
                    track['last_alert_minute'] = 0
                
                time_in_roi = int(current_time - track['entry_time'])
                current_minute = time_in_roi // 60
                
                # Alertas a los 1, 3, 6, 9... minutos
                target_minutes = []
                minute = 1
                while minute <= current_minute:
                    target_minutes.append(minute)
                    minute += 3
                
                sent_minutes = self.alert_minutes_sent.get(track_id, [])
                
                for target_minute in target_minutes:
                    if target_minute not in sent_minutes:
                        success = self.save_staff_photo(
                            frame,
                            track['class'],
                            track_id,
                            'alerta_periodica',
                            track.get('confidence'),
                            time_in_roi
                        )
                        
                        if success:
                            if track_id not in self.alert_minutes_sent:
                                self.alert_minutes_sent[track_id] = []
                            
                            if target_minute not in self.alert_minutes_sent[track_id]:
                                self.alert_minutes_sent[track_id].append(target_minute)
                            
                            track['last_alert_minute'] = target_minute
                            
                            if self.debug_mode:
                                staff_name = self.get_staff_display_name(track['class'])
                                print(f"⏰ ALERTA: {staff_name} lleva {target_minute} minuto{'s' if target_minute > 1 else ''}")
            else:
                if 'entry_time' in track:
                    del track['entry_time']
                if track_id in self.alert_minutes_sent:
                    del self.alert_minutes_sent[track_id]

    def is_near_recent_counted(self, center: Tuple, threshold: int = 50) -> bool:
        for counted_id, counted_center, counted_frame in self.recent_counted_persons:
            distance = np.sqrt((center[0] - counted_center[0])**2 + (center[1] - counted_center[1])**2)
            if distance < threshold and (self.frame_counter - counted_frame) < 30:
                return True
        return False

    def validate_movement(self, track_id: int, current_pos: Tuple) -> bool:
        if track_id not in self.movement_history:
            self.movement_history[track_id].append(current_pos)
            return True
        
        positions = list(self.movement_history[track_id])
        if len(positions) < 3:
            self.movement_history[track_id].append(current_pos)
            return True
        
        first_pos = positions[0]
        distance = np.sqrt((current_pos[0] - first_pos[0])**2 + (current_pos[1] - first_pos[1])**2)
        
        self.movement_history[track_id].append(current_pos)
        return distance > 10

    def cleanup_undetected_tracks(self, current_detections: list):
        if not current_detections:
            if self.active_tracks and self.debug_mode:
                print(f"⚠️ No hay detecciones, limpiando tracks")
            self.active_tracks.clear()
            return
        
        track_ids = list(self.active_tracks.keys())
        unmatched_tracks = []
        
        for track_id in track_ids:
            track = self.active_tracks[track_id]
            track_box = track['box']
            
            has_match = False
            for det in current_detections:
                iou = self.calculate_iou(track_box, det['box'])
                if iou > 0.3:
                    has_match = True
                    break
            
            if not has_match:
                track['frames_without_detection'] = track.get('frames_without_detection', 0) + 1
                
                if track['frames_without_detection'] >= self.max_frames_without_detection:
                    unmatched_tracks.append(track_id)
            else:
                track['frames_without_detection'] = 0
        
        for track_id in unmatched_tracks:
            if self.debug_mode:
                track_info = self.active_tracks[track_id]
                staff_name = self.get_staff_display_name(track_info['class'])
                print(f"🗑️ {staff_name} eliminado - {self.max_frames_without_detection} frames sin detección")
            self._remove_track(track_id)

    def process_entry_exit_logic(self, frame: np.ndarray):
        roi_polygon_points = self.roi_polygon.reshape((-1, 1, 2))
        counted_in_frame = []
        tracks_to_remove = []

        self.person_count_inside = 0
        self.active_by_category = {'Hombres': 0, 'Mujeres': 0, 'Niños': 0, 'Personas': 0}

        for track_id, track in list(self.active_tracks.items()):
            current_pos = track['center']
            is_inside = self.is_inside_polygon(current_pos, roi_polygon_points)

            previous_inside = track.get('is_inside', False)
            track['is_inside'] = is_inside

            if is_inside and not previous_inside:
                # ENTRADA del empleado
                track['entry_time'] = time.time()
                track['last_alert_minute'] = 0
                if track_id in self.alert_minutes_sent:
                    del self.alert_minutes_sent[track_id]

                if hasattr(self, 'last_processed_frame'):
                    self.save_staff_photo(
                        self.last_processed_frame,
                        track['class'],
                        track_id,
                        'entrada',
                        track.get('confidence')
                    )

                if self.debug_mode:
                    staff_name = self.get_staff_display_name(track['class'])
                    confidence = track.get('confidence', 0)
                    print(f"🚪 {staff_name} ({confidence:.2f}) entró al área")

                # Incrementar contador del empleado
                self.employee_counters[track['class']] += 1

                # Incrementar contador por categoría (usa la clasificación del track)
                category = track.get('category', 'Personas')
                self.entry_counts[category] = self.entry_counts.get(category, 0) + 1
            
            elif not is_inside and previous_inside:
                # SALIDA del empleado
                total_time = 0
                total_minutes = 0
                if 'entry_time' in track:
                    total_time = int(time.time() - track['entry_time'])
                    total_minutes = total_time // 60

                # Registrar visita en CSV.
                if total_time > 0:
                    gender = "Desconocido"
                    age_range = "Desconocido"
                    if hasattr(self, '_demographics'):
                        demo = self._demographics.get_cached(track_id)
                        if demo:
                            gender = demo.get('gender', 'Desconocido')
                            age_range = demo.get('age_range', 'Desconocido')
                    if hasattr(self, '_analytics_logger'):
                        try:
                            self._analytics_logger.log_visit_csv(
                                gender, age_range, total_time
                            )
                        except Exception as _e:
                            logger.error(f"Error CSV visita: {_e}")

                if 'entry_time' in track:
                    del track['entry_time']
                if track_id in self.alert_minutes_sent:
                    del self.alert_minutes_sent[track_id]

                if hasattr(self, 'last_processed_frame'):
                    self.save_staff_photo(
                        self.last_processed_frame,
                        track['class'],
                        track_id,
                        'salida',
                        track.get('confidence'),
                        total_time
                    )

                if self.debug_mode:
                    staff_name = self.get_staff_display_name(track['class'])
                    print(f"🚪 {staff_name} salió del área - Duró {total_minutes} minutos")
            
            if is_inside:
                track['has_been_inside'] = True
                track['frames_out_roi'] = 0
                track['frames_in_roi'] = track.get('frames_in_roi', 0) + 1
                self.person_count_inside += 1
                cat = track.get('category', 'Personas')
                self.active_by_category[cat] = self.active_by_category.get(cat, 0) + 1
            else:
                track['frames_out_roi'] = track.get('frames_out_roi', 0) + 1
                track['frames_in_roi'] = 0
                
                frames_in_roi = track.get('total_frames_in_roi', 0)
                frames_out_roi = track.get('frames_out_roi', 0)
                
                if (frames_in_roi >= self.min_time_in_roi and 
                    frames_out_roi >= 2 and
                    track['seen_frames'] >= self.min_track_frames and
                    not track.get('counted', False)):
                    
                    if not self.is_near_recent_counted(current_pos):
                        counted_in_frame.append(track_id)
                
                if track.get('has_been_inside', False) and track['frames_out_roi'] >= self.exit_frames_threshold:
                    tracks_to_remove.append(track_id)
                
                elif not track.get('has_been_inside', False) and track['frames_out_roi'] > self.max_frames_out:
                    tracks_to_remove.append(track_id)
        
        for track_id in tracks_to_remove:
            self._remove_track(track_id)
        
        for track_id in counted_in_frame:
            if self._count_staff_safe(track_id):
                if track_id in self.active_tracks:
                    self._remove_track(track_id)
        
        return self.person_count_inside

    def _count_staff_safe(self, track_id: int) -> bool:
        if track_id not in self.active_tracks:
            return False
        
        track = self.active_tracks[track_id]
        
        if track.get('counted', False) or track_id in self.counted_tracks:
            return False
        
        if not self.validate_movement(track_id, track['center']):
            if self.debug_mode:
                print(f"⚠️ Movimiento no válido - ignorando")
            return False
        
        self.counted_tracks.add(track_id)
        track['counted'] = True
        track['counted_at_frame'] = self.frame_counter
        
        self.recent_counted_persons.append((track_id, track['center'], self.frame_counter))
        
        self.personas_en_area += 1
        self.last_counted_frame = self.frame_counter
        self.last_counted_id = track_id
        
        staff_name = self.get_staff_display_name(track['class'])
        confidence = track.get('confidence', 0)
        
        print(f"\n{'='*60}")
        print(f"🎉 {staff_name} ({confidence:.2f}) EN ÁREA!")
        print(f"   Total personal en área: {self.personas_en_area}")
        print(f"   Contador de {staff_name}: {self.employee_counters[track['class']]}")
        print(f"{'='*60}\n")
        
        return True

    def _remove_track(self, track_id: int):
        if track_id in self.active_tracks:
            track = self.active_tracks[track_id]
            staff_id = track['class']
            staff_name = self.get_staff_display_name(staff_id)

            # Si es persona (class == -1) y tiene entry_time, registrar
            # visita en CSV aunque no haya habido SALIDA formal (el track
            # se puede limpiar por timeout antes de cruzar el ROI).
            if staff_id == -1 and 'entry_time' in track:
                try:
                    total_time = int(time.time() - track['entry_time'])
                    if total_time > 0:
                        gender = "Desconocido"
                        age_range = "Desconocido"
                        if hasattr(self, '_demographics'):
                            demo = self._demographics.get_cached(track_id)
                            if demo:
                                gender = demo.get('gender', 'Desconocido')
                                age_range = demo.get('age_range',
                                                     'Desconocido')
                        if hasattr(self, '_analytics_logger'):
                            self._analytics_logger.log_visit_csv(
                                gender, age_range, total_time
                            )
                except Exception as _e:
                    logger.error(f"Error CSV visita en remove: {_e}")

            if self.debug_mode:
                print(f"✅ {staff_name} eliminado del seguimiento")
            del self.active_tracks[track_id]
        
        if track_id in self.track_history:
            del self.track_history[track_id]
        
        if track_id in self.movement_history:
            del self.movement_history[track_id]
        
        if track_id in self.person_cooldown:
            del self.person_cooldown[track_id]
        
        if track_id in self.alert_minutes_sent:
            del self.alert_minutes_sent[track_id]
        # clean up class history
        if track_id in self.track_class_history:
            try:
                del self.track_class_history[track_id]
            except Exception:
                pass

        # El track muere: si tenia una foto y nunca llego a tener genero,
        # se sella el JSON con el MOTIVO (Hito 7). Va ANTES de liberar el
        # estado, que es de donde sale el diagnostico.
        try:
            self._sellar_captura_sin_demografia(track_id)
        except Exception:  # noqa: BLE001
            pass

        # CRITICO (fuga de memoria): liberar el estado de demografia del track
        # (accumulator + contadores). Sin esto, _accum crecia sin limite con
        # cada track_id de BoT-SORT -> RAM al alza -> OOM -> crash del servidor.
        if hasattr(self, '_demographics') and self._demographics is not None:
            try:
                self._demographics.remove_track(track_id)
            except Exception:
                pass

    def cleanup_stale_tracks(self):
        current_frame = self.frame_counter
        tracks_to_remove = []
        
        for track_id, track in self.active_tracks.items():
            frames_since_last = current_frame - track['last_seen']
            
            if (frames_since_last > 30 or 
                (track.get('frames_out_roi', 0) > 50 and not track.get('has_been_inside', False))):
                tracks_to_remove.append(track_id)
        
        for track_id in tracks_to_remove:
            if self.debug_mode:
                staff_id = self.active_tracks[track_id]['class'] if track_id in self.active_tracks else None
                staff_name = self.get_staff_display_name(staff_id) if staff_id else "desconocido"
                print(f"🗑️ {staff_name} eliminado (inactivo)")
            self._remove_track(track_id)

    def match_detections_to_tracks(self, detections: List[Dict]) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        if not self.active_tracks or not detections:
            return [], list(range(len(detections))), list(self.active_tracks.keys())
        
        similarity_matrix = np.zeros((len(self.active_tracks), len(detections)))
        track_ids = list(self.active_tracks.keys())
        
        for i, track_id in enumerate(track_ids):
            track_box = self.active_tracks[track_id]['box']
            for j, det in enumerate(detections):
                similarity_matrix[i, j] = self.calculate_iou(track_box, det['box'])
        
        matched_pairs = []
        unmatched_detections = list(range(len(detections)))
        unmatched_tracks = list(range(len(track_ids)))
        
        iou_threshold = 0.3
        
        while True:
            if similarity_matrix.size == 0:
                break
                
            max_iou = np.max(similarity_matrix)
            if max_iou < iou_threshold:
                break
            
            i, j = np.unravel_index(np.argmax(similarity_matrix), similarity_matrix.shape)
            
            matched_pairs.append((track_ids[i], j))
            unmatched_tracks.remove(i)
            unmatched_detections.remove(j)
            
            similarity_matrix[i, :] = 0
            similarity_matrix[:, j] = 0
        
        unmatched_track_ids = [track_ids[i] for i in unmatched_tracks]
        
        return matched_pairs, unmatched_detections, unmatched_track_ids

    def update_tracks(self, detections: list):
        self.cleanup_undetected_tracks(detections)
        self.cleanup_stale_tracks()
        
        filtered_detections = []
        roi_polygon_points = self.roi_polygon.reshape((-1, 1, 2))
        
        for det in detections:
            center = det['center']
            distance_to_roi = cv2.pointPolygonTest(roi_polygon_points, (int(center[0]), int(center[1])), True)
            
            if distance_to_roi > -50:
                filtered_detections.append(det)
        
        current_detections = []
        for det in filtered_detections:
            current_detections.append({
                'box': det['box'],
                'class': det['class'],
                'center': det['center'],
                'confidence': det.get('confidence', 0.5)
            })
        
        if self.active_tracks and current_detections:
            matched_pairs, unmatched_detections, unmatched_tracks = self.match_detections_to_tracks(current_detections)
            
            for track_id, det_idx in matched_pairs:
                det = current_detections[det_idx]
                self.active_tracks[track_id].update({
                    'box': det['box'],
                    'center': det['center'],
                    'last_seen': self.frame_counter,
                    'seen_frames': self.active_tracks[track_id]['seen_frames'] + 1,
                    'confidence': max(self.active_tracks[track_id].get('confidence', 0), det['confidence'])
                })
                # Re-clasificar mientras la categoria no sea definitiva.
                # Si la HISTERESIS esta activa, seguimos llamando a classify
                # tambien DESPUES del commit: classify devuelve el resultado
                # cacheado (O(1)) y solo re-muestrea la cara a baja cadencia
                # para poder corregir un genero mal fijado.
                _cur_cat = self.active_tracks[track_id].get('category', 'Personas')
                _needs_classify = (
                    _cur_cat in ('Personas', 'Desconocido')
                    or AnalyticsConfig.DEMO_HYSTERESIS_ENABLED
                )
                if (_needs_classify
                        and (self.frame_counter + track_id) % self._classify_every_n == 0
                        and hasattr(self, 'last_processed_frame')
                        and self.last_processed_frame is not None):
                    new_cat = self._classify_person(self.last_processed_frame, det['box'], track_id)
                    # Solo guardamos categorias firmes; 'Desconocido' es estado
                    # transitorio del classifier mientras acumula muestras.
                    if new_cat in ('Hombres', 'Mujeres', 'Ninos'):
                        self.active_tracks[track_id]['category'] = new_cat
                # Append class observation for stability voting
                self.track_history[track_id].append(det['center'])
                try:
                    self.track_class_history[track_id].append(det['class'])
                except Exception:
                    pass
                # Update stable class if threshold reached
                try:
                    self._update_track_class_stability(track_id)
                except Exception:
                    pass
            
            for track_id in unmatched_tracks:
                if track_id in self.active_tracks:
                    self.active_tracks[track_id]['last_seen'] = self.frame_counter
            
            for det_idx in unmatched_detections:
                det = current_detections[det_idx]
                self._create_new_track(det)
        
        elif current_detections:
            for det in current_detections:
                self._create_new_track(det)

    def _create_new_track(self, detection: Dict):
        new_id = self.next_id
        self.next_id += 1

        center = detection['center']
        roi_polygon_points = self.roi_polygon.reshape((-1, 1, 2))
        is_inside = self.is_inside_polygon(center, roi_polygon_points)

        # Clasificar persona (género/edad). Si el classifier devuelve
        # "Desconocido" (no convergio aun), dejamos category='Personas'
        # como estado pendiente para que se siga reclasificando en frames
        # siguientes (en _update_tracks_botsort).
        category = 'Personas'
        if hasattr(self, 'last_processed_frame') and self.last_processed_frame is not None:
            cls_result = self._classify_person(
                self.last_processed_frame, detection['box'], new_id
            )
            if cls_result in ('Hombres', 'Mujeres', 'Ninos'):
                category = cls_result

        # Registrar la persona en el contador.
        if hasattr(self, '_people_counter'):
            is_new = self._people_counter.register_track(new_id)
            if is_new and hasattr(self, '_analytics_logger'):
                demo = self._demographics.get_cached(new_id) if hasattr(self, '_demographics') else None
                self._analytics_logger.log_person_detected(
                    new_id,
                    gender=demo.get('gender', '') if demo else '',
                    age_range=demo.get('age_range', '') if demo else '',
                )

        track_data = {
            'class': detection['class'],
            'box': detection['box'],
            'center': center,
            'last_seen': self.frame_counter,
            'seen_frames': 1,
            'counted': False,
            'is_inside': is_inside,
            'has_been_inside': is_inside,
            'frames_in_roi': 1 if is_inside else 0,
            'total_frames_in_roi': 0,
            'frames_out_roi': 0 if is_inside else 1,
            'entry_frame': self.frame_counter if is_inside else None,
            'frames_without_detection': 0,
            'confidence': detection.get('confidence', 0.5),
            'category': category,
            # Pertenencia a la zona (Hito 2/5) tambien en la ruta fallback.
            'in_zone': detection.get('in_zone'),
        }
        
        if is_inside:
            track_data['entry_time'] = time.time()
            track_data['last_alert_minute'] = 0
        
        self.active_tracks[new_id] = track_data
        self.track_history[new_id].append(center)
        # Initialize class history for stabilization
        try:
            self.track_class_history[new_id].append(detection['class'])
            self._update_track_class_stability(new_id)
        except Exception:
            pass
        
        if self.debug_mode and is_inside:
            staff_name = self.get_staff_display_name(detection['class'])
            confidence = detection.get('confidence', 0)
            print(f"🆕 {staff_name} ({confidence:.2f}) detectado en ROI")

    def _update_tracks_botsort(self, detections: list):
        """Actualiza tracks usando los IDs estables de BoTSORT.

        A diferencia del matching manual por IoU, BoTSORT ya asigna IDs
        consistentes usando Kalman filter + IoU + apariencia. Solo necesitamos
        sincronizar nuestro dict active_tracks con esos IDs.
        """
        current_botsort_ids = set()

        for det in detections:
            tid = det.get('track_id')
            if tid is None:
                # Sin track ID (frame sin tracking), usar fallback
                self._create_new_track(det)
                continue

            current_botsort_ids.add(tid)

            if tid in self.active_tracks:
                # Track existente: actualizar posición y metadata
                track = self.active_tracks[tid]
                track.update({
                    'box': det['box'],
                    'center': det['center'],
                    'last_seen': self.frame_counter,
                    'seen_frames': track['seen_frames'] + 1,
                    'confidence': det['confidence'],
                    'frames_without_detection': 0,
                    # Pertenencia a la zona (Hito 2, ancla pie). La usa la
                    # maquina de entrada/salida por persona (Hito 5). Durante
                    # oclusion el track no se actualiza -> conserva el ultimo
                    # valor (no dispara salida espuria).
                    'in_zone': det.get('in_zone'),
                })
                self.track_history[tid].append(det['center'])

                # Re-clasificar mientras la categoria no sea definitiva.
                # Con HISTERESIS activa tambien post-commit (cacheado O(1);
                # re-muestrea a baja cadencia para corregir generos mal fijados).
                _cur_cat = track.get('category', 'Personas')
                _needs_classify = (
                    _cur_cat in ('Personas', 'Desconocido')
                    or AnalyticsConfig.DEMO_HYSTERESIS_ENABLED
                )
                if (_needs_classify
                        and (self.frame_counter + tid) % self._classify_every_n == 0
                        and self.last_processed_frame is not None):
                    new_cat = self._classify_person(self.last_processed_frame, det['box'], tid)
                    if new_cat in ('Hombres', 'Mujeres', 'Ninos'):
                        track['category'] = new_cat

                # Registrar la persona en el contador.
                if hasattr(self, '_people_counter'):
                    self._people_counter.register_track(tid)
            else:
                # Nuevo track de BoTSORT: crear entrada
                center = det['center']
                roi_polygon_points = self.roi_polygon.reshape((-1, 1, 2))
                is_inside = self.is_inside_polygon(center, roi_polygon_points)

                category = 'Personas'
                if self.last_processed_frame is not None:
                    cls_result = self._classify_person(
                        self.last_processed_frame, det['box'], tid
                    )
                    if cls_result in ('Hombres', 'Mujeres', 'Ninos'):
                        category = cls_result

                self.active_tracks[tid] = {
                    'class': det['class'],
                    'box': det['box'],
                    'center': center,
                    'last_seen': self.frame_counter,
                    'seen_frames': 1,
                    'counted': False,
                    'is_inside': is_inside,
                    'has_been_inside': is_inside,
                    'frames_in_roi': 1 if is_inside else 0,
                    'total_frames_in_roi': 0,
                    'frames_out_roi': 0 if is_inside else 1,
                    'entry_frame': self.frame_counter if is_inside else None,
                    'frames_without_detection': 0,
                    'confidence': det.get('confidence', 0.5),
                    'category': category,
                    # Pertenencia a la zona (Hito 2, ancla pie) -> maquina de
                    # entrada/salida por persona (Hito 5).
                    'in_zone': det.get('in_zone'),
                }
                if is_inside:
                    self.active_tracks[tid]['entry_time'] = time.time()
                    self.active_tracks[tid]['last_alert_minute'] = 0

                self.track_history[tid].append(center)

                # Registrar la persona en el contador.
                if hasattr(self, '_people_counter'):
                    is_new = self._people_counter.register_track(tid)
                    if is_new and hasattr(self, '_analytics_logger'):
                        demo = self._demographics.get_cached(tid) if hasattr(self, '_demographics') else None
                        self._analytics_logger.log_person_detected(
                            tid,
                            gender=demo.get('gender', '') if demo else '',
                            age_range=demo.get('age_range', '') if demo else '',
                        )

                if self.debug_mode and is_inside:
                    print(f"🆕 [{category}] track_id={tid} ({det.get('confidence', 0):.2f}) en ROI")

        # Limpiar tracks (personas) que BoTSORT ya no reporta.
        stale_ids = []
        for tid in list(self.active_tracks.keys()):
            if tid not in current_botsort_ids:
                track = self.active_tracks[tid]
                track['frames_without_detection'] = track.get('frames_without_detection', 0) + 1
                if track['frames_without_detection'] >= self.max_frames_without_detection:
                    stale_ids.append(tid)

        for tid in stale_ids:
            self._remove_track(tid)

    def _resolve_track_identities(self, frame: Optional[np.ndarray] = None) -> None:
        """Resuelve el persistent_id de cada track activo (Hito 4 + Hito 9).

        Capa 2 de identidad: mapea tracker_id (corto plazo, ByteTrack) ->
        persistent_id (largo plazo, galeria facial). Logica de la seccion 2
        del plan:
          - Si el reidentificador YA mapeo este track a un person_uuid (su
            rostro paso los filtros de calidad y matcheo o se registro), el
            track queda CONFIRMADO con ese persistent_id y el uuid entra al
            conteo de unicos confirmados (que NO se infla: un uuid que
            reaparece con otro tracker_id no vuelve a sumar).
          - Si aun no hay rostro confirmado, el track recibe un persistent_id
            PROVISIONAL ('prov:<tid>') y NO se cuenta como visitante unico
            definitivo (se resolvera en frames siguientes al verse la cara).

        FUSION: si dos tracker_id distintos matchean el MISMO rostro, el
        reidentificador los mapea al mismo person_uuid -> ambos tracks
        comparten persistent_id y el set de unicos (indexado por uuid) los
        deduplica automaticamente.

        Rendimiento: NO recomputa embeddings; solo consulta el mapeo cacheado
        del reidentificador (O(1) por track). El embedding ya se calculo (o no)
        en la rama de demografia, throttled.
        """
        reid = getattr(self, '_reidentifier', None)
        reid_ok = reid is not None and reid.is_available
        # Re-ID CORPORAL (Hito 9): 2a senal cuando NO hay rostro (espaldas).
        # El ROSTRO MANDA (se evalua primero); el cuerpo solo ENLAZA a una
        # identidad YA confirmada por cara (nunca crea nuevas -> no infla).
        body = getattr(self, '_body_reidentifier', None)
        body_ok = (body is not None and body.is_available
                   and frame is not None and getattr(frame, 'size', 0) > 0)
        _every = max(1, int(AnalyticsConfig.BODY_REID_EVERY_N))
        _min_h = int(AnalyticsConfig.BODY_REID_MIN_CROP_H)
        _min_seen = int(AnalyticsConfig.BODY_REID_MIN_SEEN_FRAMES)
        _links = 0
        for tid, track in self.active_tracks.items():
            uid = reid.get_persistent_id(tid) if reid_ok else None
            _due = (body_ok and track.get('seen_frames', 0) >= _min_seen
                    and (self.frame_counter + tid) % _every == 0)
            if uid:
                track['persistent_id'] = uid
                track['persistent_confirmed'] = True
                track['identity_source'] = 'face'
                self._confirmed_unique.add(uid)
                # Enrolar su cuerpo en la galeria corporal (throttled) -> poder
                # reconocerlo si reaparece de espaldas dentro de la sesion.
                if _due:
                    crop = self._body_crop(frame, track.get('box'), _min_h)
                    if crop is not None:
                        emb = body.compute_embedding(crop)
                        if emb is not None:
                            body.enroll(uid, emb)
                continue
            # Sin rostro -> intentar enlazar por CUERPO (entra de espaldas).
            matched = None
            if _due:
                crop = self._body_crop(frame, track.get('box'), _min_h)
                if crop is not None:
                    emb = body.compute_embedding(crop)
                    if emb is not None:
                        m = body.match(emb)
                        if m is not None:
                            matched = m[0]
                            track['_body_match'] = matched  # cache para el track
            # Reusar el ultimo match corporal del track (el cuerpo no cambia
            # mientras el track vive) -> no recomputa cada frame.
            if matched is None:
                matched = track.get('_body_match')
            if matched:
                track['persistent_id'] = matched
                track['persistent_confirmed'] = True
                track['identity_source'] = 'body'
                self._confirmed_unique.add(matched)
                _links += 1
            else:
                # Sin rostro ni match corporal -> provisional (no cuenta como
                # unico definitivo hasta confirmarse).
                track['persistent_id'] = f"prov:{tid}"
                track['persistent_confirmed'] = False
        self._body_links = _links  # tracks enlazados por cuerpo este frame

    def _body_crop(self, frame: np.ndarray, box, min_h: int):
        """Recorta el cuerpo de una persona del frame para el Re-ID corporal.
        Devuelve None si la caja es invalida o el recorte es mas bajo que
        min_h px (cuerpos diminutos/lejanos dan embeddings basura)."""
        if frame is None or box is None:
            return None
        try:
            x1, y1, x2, y2 = [int(v) for v in box]
        except (TypeError, ValueError):
            return None
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if (y2 - y1) < int(min_h) or (x2 - x1) < 8:
            return None
        crop = frame[y1:y2, x1:x2]
        return crop if getattr(crop, 'size', 0) > 0 else None

    def _process_zone_transitions(self) -> None:
        """Maquina de estados de zona POR persistent_id (Hito 5).

        Integra Hito 2 (in_zone via sv.PolygonZone, ancla pie) con Hito 4
        (persistent_id) para contar entradas/salidas POR PERSONA con
        anti-rebote, y derivar los visitantes unicos del area sin inflar.

        - Estado por persona: FUERA / DENTRO.
        - Una transicion se CONFIRMA solo tras ZONE_HYSTERESIS_FRAMES frames
          consecutivos en el nuevo estado (evita entradas/salidas fantasma de
          alguien parado en el borde del poligono).
        - FUERA->DENTRO = entrada: visita +1 de esa persona, _total_entries +1
          y la persona entra al set de visitantes unicos del area.
          DENTRO->FUERA = salida: +1 y acumula tiempo_total_dentro.
        - Solo personas con persistent_id CONFIRMADO disparan transiciones
          (ZONE_ENTRY_REQUIRES_CONFIRMED); las provisionales esperan a tener
          rostro -> el conteo de unicos es exacto.
        - Una persona cuyo track desaparecio (ya no esta en active_tracks) se
          observa como FUERA -> tras histeresis cuenta su salida.
        """
        now = time.time()
        require_conf = bool(AnalyticsConfig.ZONE_ENTRY_REQUIRES_CONFIRMED)
        hyst = max(1, int(AnalyticsConfig.ZONE_HYSTERESIS_FRAMES))

        # 1) Observacion por persona desde los tracks activos (DENTRO si
        #    cualquiera de sus tracks tiene el pie en la zona).
        observed: Dict[str, bool] = {}
        for _tid, track in self.active_tracks.items():
            if require_conf and not track.get('persistent_confirmed', False):
                continue
            pid = track.get('persistent_id')
            if not pid or str(pid).startswith('prov:'):
                continue
            observed[pid] = observed.get(pid, False) or bool(
                track.get('in_zone'))

        # 2) Personas ya conocidas SIN track activo este frame -> FUERA
        #    (salieron del encuadre; tras histeresis contara salida).
        for pid in self._zone_state.keys():
            observed.setdefault(pid, False)

        # 3) Histeresis + confirmacion de transiciones.
        for pid, dentro in observed.items():
            nuevo = 'DENTRO' if dentro else 'FUERA'
            st = self._zone_state.get(pid)
            if st is None:
                # Primera vez: estado inicial FUERA. Si ya esta DENTRO, la
                # transicion de abajo contara su primera entrada tras histeresis.
                st = {
                    'estado': 'FUERA', 'pending': None, 'pending_frames': 0,
                    'conteo_entradas': 0, 'conteo_salidas': 0,
                    'tiempo_total_dentro': 0.0, 'entry_time': None,
                    'last_ts': now,
                }
                self._zone_state[pid] = st

            if nuevo == st['estado']:
                st['pending'] = None
                st['pending_frames'] = 0
            else:
                if st['pending'] == nuevo:
                    st['pending_frames'] += 1
                else:
                    st['pending'] = nuevo
                    st['pending_frames'] = 1
                if st['pending_frames'] >= hyst:
                    if nuevo == 'DENTRO':
                        st['estado'] = 'DENTRO'
                        st['conteo_entradas'] += 1
                        st['entry_time'] = now
                        self._total_entries += 1
                        self._unique_entered.add(pid)
                    else:
                        st['estado'] = 'FUERA'
                        st['conteo_salidas'] += 1
                        if st.get('entry_time'):
                            st['tiempo_total_dentro'] += max(
                                0.0, now - st['entry_time'])
                        st['entry_time'] = None
                        self._total_exits += 1
                    st['pending'] = None
                    st['pending_frames'] = 0
            st['last_ts'] = now

    def get_visitor_stats(self) -> Dict[str, Any]:
        """Resumen de visitantes del area (Hito 5), listo para reporte/overlay.

        Returns:
            dict con globales (unicos, entradas, salidas, aforo) y detalle por
            persona (visitas y tiempo dentro, sumando el intervalo abierto si
            sigue dentro).
        """
        now = time.time()
        personas = []
        for pid, st in self._zone_state.items():
            t_dentro = float(st.get('tiempo_total_dentro', 0.0))
            if st.get('estado') == 'DENTRO' and st.get('entry_time'):
                t_dentro += max(0.0, now - st['entry_time'])
            personas.append({
                'persistent_id': pid,
                'visitas': int(st.get('conteo_entradas', 0)),
                'salidas': int(st.get('conteo_salidas', 0)),
                'estado': st.get('estado', 'FUERA'),
                'tiempo_total_dentro': round(t_dentro, 1),
            })
        return {
            'visitantes_unicos': len(self._unique_entered),
            'total_entradas': int(self._total_entries),
            'total_salidas': int(self._total_exits),
            'aforo_actual': int(getattr(self, '_zone_count', 0)),
            'personas': personas,
        }

    def get_analytics_report(self) -> Dict[str, Any]:
        """Reporte CONSOLIDADO de analitica de visitantes (Hito 8).

        Une las metricas de todos los hitos en una estructura coherente, lista
        para guardar a JSON o pintar en overlay:
          - visitantes_unicos: persistent_id distintos que ENTRARON al area.
          - aforo_actual: personas dentro ahora (sv.PolygonZone).
          - total_entradas / total_salidas: transiciones confirmadas.
          - permanencia_media_s: media del tiempo dentro de los que entraron.
          - distribucion_genero / distribucion_edad: de los visitantes unicos
            (segun su demografia consolidada por persona en la galeria).
          - galeria_total: personas distintas historicas (DB persistente).

        Coherencia garantizada: aforo_actual <= visitantes_unicos y
        total_entradas >= aforo_actual (un dentro implica una entrada).
        """
        vs = self.get_visitor_stats()
        reid = getattr(self, '_reidentifier', None)
        # Demografia por uid (de la galeria; solo de quienes ENTRARON).
        persons_by_uid: Dict[str, Any] = {}
        if reid is not None:
            try:
                persons_by_uid = {p['uuid']: p for p in reid.get_all_persons()}
            except Exception:
                persons_by_uid = {}
        gen_dist: Counter = Counter()
        age_dist: Counter = Counter()
        dwell_vals: List[float] = []
        for p in vs['personas']:
            if int(p.get('visitas', 0)) <= 0:
                continue  # solo los que efectivamente entraron al area
            dwell_vals.append(float(p.get('tiempo_total_dentro', 0.0)))
            rec = persons_by_uid.get(p['persistent_id'])
            if rec:
                gen_dist[rec.get('gender') or 'Desconocido'] += 1
                age_dist[rec.get('age_range') or 'Desconocido'] += 1
        avg_dwell = (sum(dwell_vals) / len(dwell_vals)) if dwell_vals else 0.0
        # AFORO coherente: personas CONFIRMADAS actualmente DENTRO (subconjunto
        # de las que entraron) -> garantiza aforo_actual <= visitantes_unicos.
        # El zone_count geometrico (todos los pies dentro, incl. no confirmados)
        # se reporta aparte como aforo_geometrico (ocupacion cruda honesta).
        aforo_confirmado = sum(
            1 for st in self._zone_state.values()
            if st.get('estado') == 'DENTRO')
        return {
            'visitantes_unicos': vs['visitantes_unicos'],
            'aforo_actual': aforo_confirmado,
            'aforo_geometrico': vs['aforo_actual'],
            'total_entradas': vs['total_entradas'],
            'total_salidas': vs['total_salidas'],
            'permanencia_media_s': round(avg_dwell, 1),
            'distribucion_genero': dict(gen_dist),
            'distribucion_edad': dict(age_dist),
            'galeria_total': (reid.total_unique_persons
                              if reid is not None else 0),
            'timestamp': time.time(),
        }

    def save_analytics_report(self, path: str = None) -> bool:
        """Persiste el reporte consolidado a JSON (escritura atomica). Asi el
        dashboard/reportes sobreviven reinicios del proceso. Defensivo."""
        try:
            import json as _json
            path = path or os.path.join(
                "output", f"analytics_report_{_safe_report_name(self.client_id)}.json")
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(self.get_analytics_report(), f, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except Exception as exc:
            logger.debug("save_analytics_report fallo: %s", exc)
            return False

    def _capture_established_tracks(self, frame):
        """Captura UNA foto por persona. Doble dedup, robusto al churn de track:
        (a) ESPACIAL por celda de grilla -> a lo sumo 1 foto por zona de la
        imagen cada CAPTURE_DEDUP_SECONDS (asi una persona quieta a la que el
        tracker le cambia el id no se vuelve a fotografiar); y (b) por IDENTIDAD
        Re-ID despues (agrupa la misma cara aunque cambie de zona)."""
        if frame is None or getattr(frame, 'size', 0) == 0:
            return
        kmin = int(AnalyticsConfig.CAPTURE_MIN_SEEN_FRAMES)
        ddt = float(AnalyticsConfig.CAPTURE_DEDUP_SECONDS)
        now = time.time()
        h, w = frame.shape[:2]
        cell_w = max(1.0, float(w) / float(AnalyticsConfig.CAPTURE_GRID_COLS))
        cell_h = max(1.0, float(h) / float(AnalyticsConfig.CAPTURE_GRID_ROWS))
        for tid, obj in list(self.active_tracks.items()):
            if tid in self._captured_ids or obj.get('class', -1) != -1:
                continue
            if int(obj.get('seen_frames', 0)) < kmin:
                continue
            box = obj.get('box')
            if box is None:
                continue
            # ── Dedup por IDENTIDAD *ANTES* de fotografiar ──
            # Si el track ya tiene persistent_id CONFIRMADO (rostro o cuerpo)
            # y esa persona YA fue fotografiada, NO se escribe un duplicado
            # que luego habria que borrar (y que quedaba huerfano si la
            # demografia no convergia). Una persona = una foto por sesion.
            pid = obj.get('persistent_id')
            confirmado = bool(
                obj.get('persistent_confirmed')
                and pid and not str(pid).startswith('prov:'))
            if confirmado and pid in self._captured_persons:
                self._captured_ids.add(tid)   # persona ya fotografiada
                continue
            cx = (float(box[0]) + float(box[2])) / 2.0
            cy = (float(box[1]) + float(box[3])) / 2.0
            cell = (int(cx / cell_w), int(cy / cell_h))
            if now - self._cell_last_capture.get(cell, 0.0) <= ddt:
                self._captured_ids.add(tid)  # zona ya cubierta: no reintentar
                continue
            stem = self._capture_detection(tid, frame, box)
            if stem and confirmado:
                # Foto escrita con identidad conocida: sellar a la persona
                # YA (no esperar a que la demografia converja).
                self._captured_persons[pid] = stem
            self._cell_last_capture[cell] = now
        # acotar el dict para que no crezca sin limite
        if len(self._cell_last_capture) > 5000:
            self._cell_last_capture = {
                k: v for k, v in self._cell_last_capture.items()
                if now - v <= ddt}

    def _capture_detection(self, track_id, frame, box):
        """Guarda UNA foto del recorte de la persona detectada (por track nuevo)
        y, si se le detecta cara, tambien el recorte del rostro. En CAPTURE_DIR
        (output/captures/persons y .../faces). Devuelve el `stem` del archivo
        escrito o None. Defensivo: nunca lanza."""
        if track_id in self._captured_ids:
            return None
        self._captured_ids.add(track_id)
        if len(self._captured_ids) > 20000:  # acotar memoria en sesiones largas
            self._captured_ids = set(list(self._captured_ids)[-10000:])
        try:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = [int(v) for v in box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if (x2 - x1) < 10 or (y2 - y1) < int(
                    AnalyticsConfig.CAPTURE_MIN_PERSON_H):
                return
            person = frame[y1:y2, x1:x2]
            if person.size == 0:
                return
            base = AnalyticsConfig.CAPTURE_DIR
            ts = time.strftime("%Y%m%d_%H%M%S")
            cam = getattr(self, '_camera_display_name', None) or "cam"
            safe_cam = "".join(
                c for c in str(cam) if c.isalnum() or c in "_-")[:24] or "cam"
            stem = f"{ts}_{safe_cam}_t{track_id}"
            # Foto de la persona
            pdir = os.path.join(base, "persons")
            os.makedirs(pdir, exist_ok=True)
            cv2.imwrite(os.path.join(pdir, f"{stem}.jpg"), person,
                        [cv2.IMWRITE_JPEG_QUALITY, 85])
            # Sidecar con metadata (genero/edad se rellenan al converger).
            self._capture_meta[track_id] = stem
            meta0 = {"track_id": int(track_id), "camera": str(cam),
                     "timestamp": ts, "gender": None, "age_range": None}
            try:
                import json as _json
                with open(os.path.join(pdir, f"{stem}.json"), "w",
                          encoding="utf-8") as _f:
                    _json.dump(meta0, _f, ensure_ascii=False)
            except Exception:
                pass
            # Foto del ROSTRO con ZOOM (recorte natural ampliado): es la
            # que deja ver la cara en la que se baso el genero/edad.
            face_zoom = None
            if hasattr(self, '_demographics') and self._demographics is not None:
                try:
                    face_zoom = self._demographics.extract_face_zoom(person)
                    if face_zoom is None:
                        # Sin rostro detectable: guardar al menos el crop
                        # alineado si la demografia logro uno.
                        face_zoom, _pose, _nw = \
                            self._demographics._detect_face_with_pose(person)
                    if face_zoom is not None and getattr(face_zoom, 'size', 0):
                        fdir = os.path.join(base, "faces")
                        os.makedirs(fdir, exist_ok=True)
                        cv2.imwrite(os.path.join(fdir, f"{stem}.jpg"),
                                    face_zoom,
                                    [cv2.IMWRITE_JPEG_QUALITY, 92])
                except Exception:
                    face_zoom = None
            # Copia ANOTADA inmediata para la galeria del cliente, con el
            # rostro ampliado al lado (se re-anota con genero/edad cuando
            # la demografia converge).
            self._write_client_capture(stem, meta0, person_img=person,
                                       face_zoom=face_zoom)
            return stem
        except Exception as exc:  # noqa: BLE001
            logger.debug("Captura de deteccion fallo: %s", exc)
        return None

    def _delete_capture(self, stem):
        """Borra los archivos de una captura (persona + rostro + sidecar),
        incluida la copia anotada de la carpeta del cliente."""
        base = AnalyticsConfig.CAPTURE_DIR
        for sub, ext in (("persons", ".jpg"), ("persons", ".json"),
                         ("faces", ".jpg")):
            try:
                p = os.path.join(base, sub, f"{stem}{ext}")
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        cdir = getattr(AnalyticsConfig, 'CAPTURE_CLIENT_DIR', '') or ''
        if cdir:
            for ext in (".jpg", ".json"):
                try:
                    p = os.path.join(cdir, f"{stem}{ext}")
                    if os.path.isfile(p):
                        os.remove(p)
                except Exception:
                    pass

    # ── Capturas ANOTADAS para la galeria del cliente (Amazonas View) ──

    @staticmethod
    def _capture_ts_legible(ts: str) -> str:
        """'20260727_154210' -> '27/07/2026 15:42:10' (defensivo)."""
        try:
            dt = datetime.datetime.strptime(str(ts), "%Y%m%d_%H%M%S")
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return str(ts)

    @staticmethod
    def _compose_face_zoom_panel(person_img, face_zoom):
        """Pega el ROSTRO AMPLIADO a la derecha del recorte de la persona.

        El rostro se escala a la altura del cuerpo (con tope) y se separa
        con una franja, de modo que la foto guardada muestre a la vez a
        quien se detecto y el primer plano de su cara.
        """
        ph, pw = person_img.shape[:2]
        fh, fw = face_zoom.shape[:2]
        if fh <= 0 or fw <= 0:
            return person_img
        # El rostro ocupa la altura del cuerpo, sin pasarse de ancho.
        escala = ph / float(fh)
        max_face_w = max(80, int(pw * 1.10))
        if fw * escala > max_face_w:
            escala = max_face_w / float(fw)
        nw = max(1, int(round(fw * escala)))
        nh = max(1, int(round(fh * escala)))
        interp = cv2.INTER_CUBIC if escala > 1.0 else cv2.INTER_AREA
        rostro = cv2.resize(face_zoom, (nw, nh), interpolation=interp)

        sep = 4
        alto = max(ph, nh)
        lienzo = np.full((alto, pw + sep + nw, 3), 18, dtype=np.uint8)
        lienzo[:ph, :pw] = person_img
        y0 = (alto - nh) // 2          # rostro centrado verticalmente
        lienzo[y0:y0 + nh, pw + sep:pw + sep + nw] = rostro
        # Marco del panel de zoom para que se lea como un primer plano.
        cv2.rectangle(lienzo, (pw + sep, y0),
                      (pw + sep + nw - 1, y0 + nh - 1), (90, 90, 90), 1)
        return lienzo

    def _annotate_capture_image(self, person_img, meta: dict,
                                face_zoom=None):
        """Copia del recorte de la persona con un banner inferior legible:
        linea 1 = genero + rango de edad (o 'Analizando...'), linea 2 =
        camara + fecha/hora (+ visitas si el Re-ID las conoce).

        Si se pasa `face_zoom`, se compone a la derecha un panel con el
        ROSTRO AMPLIADO: es la cara concreta sobre la que se decidio el
        genero/edad, para poder verificarlo de un vistazo.
        """
        img = person_img
        h, w = img.shape[:2]

        # ── Panel de ZOOM: el rostro ampliado, a la derecha del cuerpo ──
        if face_zoom is not None and getattr(face_zoom, 'size', 0) > 0:
            img = self._compose_face_zoom_panel(img, face_zoom)
            h, w = img.shape[:2]

        # Ancho minimo para que el texto quepa sin encimarse.
        min_w = 240
        if w < min_w:
            scale = min_w / float(w)
            img = cv2.resize(img, (min_w, max(1, int(round(h * scale)))),
                             interpolation=cv2.INTER_CUBIC)
            h, w = img.shape[:2]
        gender = meta.get('gender')
        age = meta.get('age_range')
        if gender in (None, '', 'Desconocido'):
            linea1 = ("Desconocido" if meta.get('demo_final')
                      else "Analizando...")
            color = (160, 160, 160)                  # gris
        else:
            linea1 = f"{gender} | {age or 'Desconocido'}"
            color = ((200, 120, 40) if gender == 'Hombre'
                     else (180, 60, 200))            # azul / magenta (BGR)
        partes2 = [str(meta.get('camera') or 'cam'),
                   self._capture_ts_legible(meta.get('timestamp', ''))]
        visitas = meta.get('visitas')
        if visitas and int(visitas) > 1:
            partes2.append(f"{int(visitas)}a visita")
        linea2 = "  |  ".join(p for p in partes2 if p)
        footer = 52
        canvas = np.zeros((h + footer, w, 3), dtype=np.uint8)
        canvas[:h] = img
        canvas[h:] = (18, 18, 18)
        cv2.rectangle(canvas, (0, h), (w, h + 3), color, -1)

        def _fit(text, base_scale, max_w):
            # Reduce la escala hasta que el texto quepa (no se recorta).
            scale = base_scale
            while scale > 0.28:
                tw = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                     scale, 1)[0][0]
                if tw <= max_w:
                    break
                scale -= 0.04
            return scale
        s1 = _fit(linea1, 0.60, w - 16)
        s2 = _fit(linea2, 0.42, w - 16)
        cv2.putText(canvas, linea1, (8, h + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, s1, color, 2, cv2.LINE_AA)
        cv2.putText(canvas, linea2, (8, h + 44),
                    cv2.FONT_HERSHEY_SIMPLEX, s2, (210, 210, 210), 1,
                    cv2.LINE_AA)
        return canvas

    def _write_client_capture(self, stem: str, meta: dict, person_img=None,
                              face_zoom=None):
        """Escribe/actualiza la foto ANOTADA (+ sidecar JSON) de una captura
        en la carpeta del cliente (CAPTURE_CLIENT_DIR). Se llama al capturar
        (etiqueta 'Analizando...') y de nuevo cuando la demografia converge
        (etiqueta final con genero/edad; MISMO archivo -> sin duplicados).
        Defensivo: nunca lanza."""
        cdir = getattr(AnalyticsConfig, 'CAPTURE_CLIENT_DIR', '') or ''
        if not cdir or not stem:
            return
        try:
            if person_img is None:
                src = os.path.join(AnalyticsConfig.CAPTURE_DIR, "persons",
                                   f"{stem}.jpg")
                person_img = cv2.imread(src)
            if person_img is None or getattr(person_img, 'size', 0) == 0:
                return
            if face_zoom is None:
                # Re-anotacion: recuperar el rostro ampliado ya guardado.
                fsrc = os.path.join(AnalyticsConfig.CAPTURE_DIR, "faces",
                                    f"{stem}.jpg")
                if os.path.isfile(fsrc):
                    face_zoom = cv2.imread(fsrc)
            os.makedirs(cdir, exist_ok=True)
            annotated = self._annotate_capture_image(person_img, meta,
                                                     face_zoom=face_zoom)
            # Escritura atomica: el watcher del cliente no debe leer a medias.
            dst = os.path.join(cdir, f"{stem}.jpg")
            tmp = os.path.join(cdir, f"{stem}.tmp.jpg")
            if cv2.imwrite(tmp, annotated, [cv2.IMWRITE_JPEG_QUALITY, 88]):
                os.replace(tmp, dst)
            import json as _json
            jtmp = os.path.join(cdir, f"{stem}.json.tmp")
            with open(jtmp, "w", encoding="utf-8") as f:
                _json.dump({**meta, "file": f"{stem}.jpg"}, f,
                           ensure_ascii=False)
            os.replace(jtmp, os.path.join(cdir, f"{stem}.json"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Captura cliente fallo (%s): %s", stem, exc)

    def _sellar_captura_sin_demografia(self, track_id) -> None:
        """Escribe `motivo_sin_demografia` en la captura de un track que
        muere sin genero. Sin esto el JSON se queda con el `null` inicial
        para siempre y no hay forma de saber que paso."""
        stem = self._capture_meta.get(track_id)
        if not stem or track_id in self._captured_demo_done:
            return
        jp = os.path.join(AnalyticsConfig.CAPTURE_DIR, "persons",
                          f"{stem}.json")
        if not os.path.isfile(jp):
            return
        import json as _json
        try:
            meta = _json.loads(open(jp, encoding='utf-8').read())
        except Exception:  # noqa: BLE001
            return
        if meta.get('gender'):
            return                      # ya tenia demografia: nada que sellar
        meta['motivo_sin_demografia'] = self._motivo_sin_demografia(track_id)
        meta['demo_final'] = True
        try:
            with open(jp, "w", encoding="utf-8") as fichero:
                _json.dump(meta, fichero, ensure_ascii=False)
            self._write_client_capture(stem, meta)
            self._captured_demo_done.add(track_id)
        except Exception:  # noqa: BLE001
            pass

    def _motivo_sin_demografia(self, track_id) -> str:
        """Por que este track no tiene genero, en una palabra.

        Reusa el diagnostico que ya calcula la telemetria (Hito 2) en vez
        de duplicar la logica: alli estan los contadores por track
        (intentos sin rostro, rechazos de pose/calidad, tamano visto).
        """
        try:
            demo = getattr(self, '_demographics', None)
            if demo is None:
                return "sin_clasificador"
            acc = demo._accum.get(track_id)
            motivo, _detalles = demo._veredicto_final(track_id, acc)
            return str(motivo)
        except Exception:  # noqa: BLE001
            return "desconocido"

    def _update_captures_demographics(self):
        """Para cada captura cuyo track ya convergio en demografia:
        (1) DEDUP: si el Re-ID la identifica como una persona YA capturada,
            BORRA esta foto (es la misma persona en otro track de BoT-SORT);
        (2) si es nueva, escribe genero/edad/identidad en el sidecar JSON para
            el dashboard. Barato: solo toca tracks aun no procesados."""
        if not self._capture_meta or not hasattr(self, '_demographics'):
            return
        base = AnalyticsConfig.CAPTURE_DIR
        import json as _json
        for tid in list(self._capture_meta.keys()):
            if tid in self._captured_demo_done:
                continue
            try:
                demo = self._demographics.get_cached(tid)
            except Exception:
                demo = None
            if not demo:
                continue
            g = demo.get('gender')
            puid = demo.get('person_uuid') or ''
            # La demografia no siempre adjunta el uuid: preguntar tambien al
            # reidentificador y al track vivo. Sin esto, un duplicado sin
            # uuid en cache quedaba en disco PARA SIEMPRE.
            if not puid and getattr(self, '_reidentifier', None) is not None:
                try:
                    puid = self._reidentifier.get_persistent_id(tid) or ''
                except Exception:
                    puid = ''
            if not puid:
                t = self.active_tracks.get(tid)
                if t is not None and t.get('persistent_confirmed'):
                    p = str(t.get('persistent_id') or '')
                    if p and not p.startswith('prov:'):
                        puid = p
            # Esperar a que haya algo firme (genero o identidad Re-ID).
            if g in (None, 'Desconocido') and not puid:
                continue
            stem = self._capture_meta[tid]
            # ── DEDUP por identidad Re-ID: una sola foto por persona ──
            # Solo es duplicado si el uuid ya tiene OTRA foto (dueno con
            # stem distinto); la foto propia jamas se borra a si misma.
            if puid:
                dueno = self._captured_persons.get(puid)
                if dueno is not None and dueno != stem:
                    self._delete_capture(stem)            # duplicado -> fuera
                    self._captured_demo_done.add(tid)
                    self._capture_meta.pop(tid, None)
                    continue
                self._captured_persons[puid] = stem
            # ── Escribir metadata en el sidecar ──
            try:
                jp = os.path.join(base, "persons", f"{stem}.json")
                meta = {}
                if os.path.isfile(jp):
                    try:
                        meta = _json.loads(open(jp, encoding='utf-8').read())
                    except Exception:
                        meta = {}
                if g not in (None, 'Desconocido'):
                    meta['gender'] = g
                    meta['age_range'] = demo.get('age_range', 'Desconocido')
                    # ── Campos ADICIONALES (Hito 7) ──
                    # `gender` y `age_range` NO cambian de nombre ni de
                    # tipo: el cliente mapea colores sobre esos literales.
                    # Estos son extra y el panel los ignora sin romperse
                    # (lee con .get()).
                    conf = demo.get('confidence')
                    if conf is not None:
                        meta['conf_genero'] = round(float(conf), 3)
                    meta['solo_cuerpo'] = (
                        demo.get('age_source') == 'body'
                        or int(demo.get('n_face_samples', 0) or 0) == 0)
                else:
                    # Sin demografia: dejar constancia de POR QUE. Un null
                    # mudo no distingue "no se pudo" de "no se intento", y
                    # esa ambiguedad hizo invisible el fallo original.
                    meta.setdefault('motivo_sin_demografia',
                                    self._motivo_sin_demografia(tid))
                if puid:
                    meta['person_uuid'] = puid
                    # Visitas conocidas por el Re-ID (para el banner/galeria).
                    try:
                        rec = next(
                            (p for p in self._reidentifier.get_all_persons()
                             if p.get('uuid') == puid), None)
                        if rec:
                            meta['visitas'] = int(rec.get('visit_count', 1))
                    except Exception:
                        pass
                with open(jp, "w", encoding="utf-8") as _f:
                    _json.dump(meta, _f, ensure_ascii=False)
                self._captured_demo_done.add(tid)
                # RE-anotar la copia del cliente con la demografia final
                # (sobreescribe el mismo archivo -> sin duplicados).
                meta['demo_final'] = True
                self._write_client_capture(stem, meta)
                # Y avisar: este es EL evento de tienda. Ver _emitir_visitante.
                self._emitir_visitante(stem, meta)
            except Exception:
                pass
        if len(self._capture_meta) > 20000:  # acotar memoria
            self._capture_meta = dict(
                list(self._capture_meta.items())[-10000:])

    def _emitir_visitante(self, stem: str, meta: dict) -> None:
        """Encola el evento «visitante identificado» de este pipeline.

        ## Por que este momento y no otro

        El pipeline de tienda no producia NINGUNA alerta: su metadata solo
        traia `detections`, `demographics`, `heatmap` y `analytics_report`. Eso
        dejaba dos cosas vacias: el panel lateral del cliente y el envio por
        WhatsApp, que reenvia lo que haya en `metadata['alerts']`.

        Se emite justo cuando **el genero y la edad convergen** para un track,
        que ocurre UNA sola vez por persona. Emitir por deteccion inundaria a
        25 fps; emitir al capturar seria prematuro, porque en ese instante la
        demografia todavia dice «Analizando...».

        Nunca lanza: esto corre dentro del bucle de inferencia.
        """
        try:
            genero = str(meta.get('gender') or '').strip()
            edad = str(meta.get('age_range') or '').strip()
            if not genero or genero.lower() == 'desconocido':
                return              # sin demografia util no hay nada que contar

            visitas = meta.get('visitas')
            recurrente = isinstance(visitas, int) and visitas > 1
            desc = f"Visitante {genero}"
            if edad:
                desc += f", {edad} años"
            desc += (f". Visita nº {visitas} (recurrente)." if recurrente
                     else ". Primera visita registrada.")

            alerta = {
                'event_type': 'Visitante',
                'class_name': f"{genero} {edad}".strip(),
                'description': desc,
                'timestamp': str(meta.get('timestamp') or ''),
                'camera_name': str(
                    getattr(self, '_camera_display_name', '') or ''),
                'gender': genero,
                'age_range': edad,
                'visitas': visitas,
                'person_uuid': meta.get('person_uuid'),
                'crop_image': self._captura_en_b64(stem),
            }
            if not hasattr(self, '_alertas_pendientes'):
                self._alertas_pendientes = []
            self._alertas_pendientes.append(alerta)
            # Cota de memoria: si el cliente no recoge, no se acumula sin fin.
            if len(self._alertas_pendientes) > 200:
                self._alertas_pendientes = self._alertas_pendientes[-100:]
        except Exception:
            pass

    def _captura_en_b64(self, stem: str) -> str:
        """Recorte de la persona en base64, para que la alerta lleve imagen.

        Sin imagen, el emisor de WhatsApp descarta la alerta y la tarjeta del
        panel sale sin miniatura."""
        try:
            import base64 as _b64
            ruta = os.path.join(AnalyticsConfig.CAPTURE_DIR, "persons",
                                f"{stem}.jpg")
            if not os.path.isfile(ruta):
                return ''
            with open(ruta, 'rb') as f:
                return _b64.b64encode(f.read()).decode('ascii')
        except Exception:
            return ''

    def _drenar_alertas(self) -> list:
        """Devuelve las alertas acumuladas y vacia la cola.

        Se drena al construir la metadata de cada frame: asi cada alerta viaja
        exactamente una vez."""
        pendientes = getattr(self, '_alertas_pendientes', None)
        if not pendientes:
            return []
        self._alertas_pendientes = []
        return pendientes

    def _build_detections(self) -> list:
        """Lista de detecciones para que el CLIENTE las dibuje (modo directo,
        baja latencia): [{box:[x1,y1,x2,y2], label:str, color:[r,g,b],
        tracker_id:int, class_id:int, confidence:float}].
        Replica la misma logica/colores/etiquetas que draw_detections, pero
        sin tocar la imagen -> el servidor no codifica ni envia el frame.
        tracker_id/class_id/confidence permiten al cliente armar un
        sv.Detections (Supervision) para estelas, zonas y heatmap.
        """
        # Mismos colores que draw_detections (BGR). Se convierten a RGB para Qt.
        category_colors = {
            'Hombres': (255, 180, 0),
            'Mujeres': (180, 0, 255),
            'Niños':   (0, 255, 180),
            'Personas': (0, 255, 255),
        }
        # class_id estable por categoria (para color_lookup CLASS en Supervision).
        category_ids = {'Hombres': 0, 'Mujeres': 1, 'Niños': 2, 'Personas': 3}
        # MODO DIRECTO: se mandan TODAS las personas (sin filtrar por ROI ni por
        # 'counted'). Asi el cliente (Supervision) ve a todos y PolygonZone puede
        # contar dentro/fuera de las zonas. El filtro de ROI lo aplica el cliente
        # con sus zonas, no el servidor. (El modo clasico draw_detections SI sigue
        # filtrando por ROI; esto solo afecta al modo directo.)
        out = []
        for _tid, obj in self.active_tracks.items():
            if obj.get('class', -1) != -1:   # solo personas (class interno -1)
                continue
            try:
                x1, y1, x2, y2 = [int(v) for v in obj['box']]
            except (KeyError, TypeError, ValueError):
                continue
            category = obj.get('category', 'Personas')
            confidence = obj.get('confidence', 0)
            demo = (self._demographics.get_cached(_tid)
                    if hasattr(self, '_demographics') else None)
            suffix = ""
            if demo:
                g = demo.get('gender', 'Desconocido')
                a = demo.get('age_range', 'Desconocido')
                suffix = (" [?]" if (g == 'Desconocido' and a == 'Desconocido')
                          else f" [{g[:1]}|{a}]")
            text = f"{category} {confidence:.2f}{suffix}"
            if 'entry_time' in obj:
                t = int(time.time() - obj['entry_time'])
                text += f" - {t // 60}m {t % 60}s"
            b, g_, r = category_colors.get(category, (0, 255, 0))
            out.append({"box": [x1, y1, x2, y2], "label": text,
                        "color": [r, g_, b],
                        "tracker_id": int(_tid),
                        "class_id": int(category_ids.get(category, 3)),
                        "confidence": float(confidence)})
        return out

    def draw_detections(self, image: np.ndarray, persons_inside: int) -> np.ndarray:
        # Dibujar zona de conteo (estilo configurable — Hito 2).
        _zcol = tuple(AnalyticsConfig.ZONE_DRAW_COLOR_BGR)
        _zop = float(AnalyticsConfig.ZONE_FILL_OPACITY)
        _zth = int(AnalyticsConfig.ZONE_DRAW_THICKNESS)
        roi_overlay = image.copy()
        cv2.fillPoly(roi_overlay, [self.roi_polygon], _zcol)
        cv2.addWeighted(roi_overlay, _zop, image, 1.0 - _zop, 0, image)
        cv2.polylines(image, [self.roi_polygon], isClosed=True,
                      color=_zcol, thickness=_zth)
        # Numero de aforo instantaneo (personas dentro de la zona) — Hito 2.
        if AnalyticsConfig.ZONE_SHOW_COUNT:
            _zc = int(getattr(self, '_zone_count', 0))
            _px, _py = int(self.roi_polygon[:, 0].min()), int(self.roi_polygon[:, 1].min())
            _lbl = f"Dentro: {_zc}"
            cv2.putText(image, _lbl, (_px + 6, max(_py - 8, 18)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(image, _lbl, (_px + 6, max(_py - 8, 18)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, _zcol, 2, cv2.LINE_AA)

        for x, y in self.roi_polygon:
            cv2.circle(image, (x, y), 8, (255, 0, 0), -1)
            cv2.circle(image, (x, y), 8, (255, 255, 255), 2)

        # Colores por categoría (personas)
        category_colors = {
            'Hombres': (255, 180, 0),    # Azul claro
            'Mujeres': (180, 0, 255),    # Rosa/Magenta
            'Niños':   (0, 255, 180),    # Verde/Cyan
            'Personas': (0, 255, 255),   # Amarillo
        }

        # Preparar polígono ROI para filtrar dibujo
        roi_pts = self.roi_polygon.reshape((-1, 1, 2)) if self.roi_polygon is not None else None

        # Solo personas (class == -1); cosmeticos eliminado.
        ordered_items = [
            (tid, obj) for tid, obj in self.active_tracks.items()
            if not obj.get('counted', False)
            and obj.get('class', -1) == -1
        ]

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        font_thickness = 2

        for _tid, obj in ordered_items:
            staff_id_pre = obj.get('class', -1)
            # Filtrar por ROI amarillo SOLO a personas (-1).
            if staff_id_pre == -1 and roi_pts is not None:
                cx, cy = obj.get('center', (0, 0))
                if cv2.pointPolygonTest(roi_pts, (int(cx), int(cy)), False) < 0:
                    continue

            x1, y1, x2, y2 = [int(v) for v in obj['box']]
            staff_id = obj.get('class', -1)
            category = obj.get('category', 'Personas')
            confidence = obj.get('confidence', 0)

            demo = None
            if staff_id == -1 and hasattr(self, '_demographics'):
                demo = self._demographics.get_cached(_tid)

            def _demo_suffix(d):
                if not d:
                    return ""
                g = d.get('gender', 'Desconocido')
                a = d.get('age_range', 'Desconocido')
                if g == 'Desconocido' and a == 'Desconocido':
                    return " [?]"
                return f" [{g[:1]}|{a}]"

            # Solo personas: color por categoria demografica.
            color = category_colors.get(category, (0, 255, 0))
            text = f"{category} {confidence:.2f}{_demo_suffix(demo)}"
            thickness = 2

            cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)

            if 'entry_time' in obj:
                current_time = time.time()
                time_in_roi = int(current_time - obj['entry_time'])
                minutes = time_in_roi // 60
                seconds = time_in_roi % 60
                text += f" - {minutes}m {seconds}s"

            (text_width, text_height), baseline = cv2.getTextSize(
                text, font, font_scale, font_thickness
            )

            cv2.rectangle(
                image, (x1, y1 - text_height - 10),
                (x1 + text_width, y1), (0, 0, 0), -1
            )
            cv2.putText(
                image, text, (x1, y1 - 5),
                font, font_scale, (255, 255, 255),
                font_thickness
            )
        
        # ── Overlay de analitica de personas ──
        if self.show_minimal_info:
            # 1. Panel demografico (izquierda)
            if hasattr(self, '_demographics'):
                demo_counts = self._demographics.get_counts()
                image = draw_demographics_panel(image, demo_counts, x=10, y=10)

            # 2. Personas totales (superior central). Si no hay active_tracks
            # usamos el contador heredado persons_inside como fallback.
            total_unique = 0
            active_persons = sum(
                1 for t in self.active_tracks.values()
                if t.get('class', -1) == -1 and t.get('is_inside', False)
            )
            active_now = active_persons if self.active_tracks else persons_inside
            if hasattr(self, '_people_counter'):
                total_unique = self._people_counter.total_unique
            image = draw_people_total(image, total_unique, active_now)

        return image

    def process_frame(self, image: np.ndarray, roi=None, activate_roi=False, camera_id: Any = 1, heatmap_activate: bool = False) -> Tuple[np.ndarray, Dict[str, Any]]:
        # Solo el modelo de personas es necesario.
        if self.person_model is None:
            raise RuntimeError("Modelo de personas no inicializado")

        if roi is not None and isinstance(roi, list) and len(roi) >= 3:
            self.roi_polygon = np.array(roi, np.int32)
            if self.debug_mode:
                print(f"📍 ROI actualizado: {len(roi)} puntos")

        # Usar estado por cámara para mantener separación de tracks
        cam_state = self._get_cam_state(camera_id)

        # Auto-detección de ángulo: re-evaluar con las muestras acumuladas
        # (de frames previos). Actualiza self._auto_angle, que usa el filtro.
        if self._auto_angle_enabled:
            prev = self._auto_angle
            self._auto_angle = self._auto_angle_detector.evaluate()
            if self._auto_angle != prev and self._auto_angle is not None:
                logger.info(
                    "Auto-ángulo cámara=%s -> %s (mediana h/w=%.2f, n=%d)",
                    camera_id, self._auto_angle,
                    self._auto_angle_detector.median_hw,
                    self._auto_angle_detector.n_samples)

        # Umbral del filtro de aspecto segun el perfil/angulo de esta camara
        # (0.0 = desactivado, p.ej. en cenital).
        _aspect_min = self._aspect_ratio_min_for(camera_id)

        # Evitar solapamiento concurrente entre llamadas a process_frame
        with self._process_lock:
            self._push_state(cam_state)

            try:
                self.frame_counter += 1
                self.last_processed_frame = image.copy()

                # Detectar sobre la imagen COMPLETA (sin máscara) para evitar
                # artefactos en los bordes del ROI que generan falsos positivos.
                inference_image = image

                # Preparar polígono ROI para filtrado espacial
                roi_active = activate_roi and hasattr(self, 'roi_polygon') and self.roi_polygon is not None
                roi_poly = self.roi_polygon.reshape((-1, 1, 2)) if roi_active else None

                detections = []
                staff_detected = 0

                # ── Deteccion de personas (YOLO estándar, clase 0 COCO) ──
                if self.person_model is not None:
                    # Conf personas: piso 0.30 para no perder personas a la
                    # distancia. El modelo COCO de YOLO es robusto, no genera
                    # falsos positivos a 0.30.
                    _per_conf = max(
                        float(AnalyticsConfig.PERSON_DETECT_CONF),
                        float(self.confidence_threshold))
                    results_per = self.person_model.predict(
                        inference_image,
                        imgsz=int(AnalyticsConfig.PERSON_DETECT_IMGSZ),
                        conf=_per_conf,
                        iou=self.iou_threshold,
                        classes=[0],
                        verbose=False,
                        max_det=int(AnalyticsConfig.PERSON_DETECT_MAX_DET),
                        half=self._yolo_half,
                        device=self.device,
                    )

                    # ── Tracking de corto plazo con sv.ByteTrack ──
                    # YOLO26 es NMS-free; predict() entrega cajas limpias. El
                    # tracking lo hace ByteTrack (no el .track interno de YOLO),
                    # POR-CAMARA. update_with_detections DEVUELVE solo las
                    # detecciones con track CONFIRMADO (tracker_id valido, ya
                    # filtradas: minimum_consecutive_frames descarta las
                    # efimeras antes de emitirlas -> anti falso-positivo).
                    if self._byte_tracker is None:
                        self._byte_tracker = self._make_byte_tracker()
                    sv_det = sv.Detections.from_ultralytics(results_per[0])
                    sv_det = self._byte_tracker.update_with_detections(sv_det)

                    # ── Conteo de zona (sv.PolygonZone) — Hito 2 ──
                    # Aforo INSTANTANEO: cuantas personas estan DENTRO del area
                    # (ancla configurable, por defecto el pie). Se calcula sobre
                    # TODAS las detecciones confirmadas, ANTES del filtro ROI de
                    # abajo -> conteo correcto este o no activo el filtro ROI.
                    # El indice de _inside_mask se alinea con sv_det (= boxes_p).
                    _zone = self._get_zone()
                    if _zone is not None and len(sv_det) > 0:
                        _inside_mask = _zone.trigger(sv_det)  # setea current_count
                        self._zone_count = int(_zone.current_count)
                    else:
                        _inside_mask = None
                        self._zone_count = 0

                    if len(sv_det) > 0 and sv_det.tracker_id is not None:
                        boxes_p = sv_det.xyxy
                        confs_p = (sv_det.confidence
                                   if sv_det.confidence is not None
                                   else np.full(len(sv_det), 0.5))
                        track_ids_p = sv_det.tracker_id

                        # Offset de track_id UNICO por camara: el reidentificador
                        # es compartido y los track_id de ByteTrack son locales a
                        # cada camara -> sin namespace colisionarian (camara A
                        # track 1 == camara B track 1). 1a camara=100000 (compat).
                        _cam_off = (
                            self._reidentifier.camera_track_namespace(camera_id)
                            if getattr(self, '_reidentifier', None) is not None
                            else 100000)

                        for i in range(boxes_p.shape[0]):
                            box = boxes_p[i]
                            center = self.center_of(box)
                            confidence = confs_p[i] if i < len(confs_p) else 0.5

                            # Filtro ROI
                            if roi_poly is not None:
                                if cv2.pointPolygonTest(roi_poly, (int(center[0]), int(center[1])), False) < 0:
                                    continue

                            bw = box[2] - box[0]
                            bh = box[3] - box[1]

                            # Auto-detección de ángulo: alimentamos el detector
                            # con TODAS las personas (antes del filtro) para que
                            # vea la forma real, incluidas las anchas del cenital.
                            if self._auto_angle_enabled:
                                self._auto_angle_detector.add_person(bw, bh)

                            # Filtro de aspecto configurable por perfil de
                            # cámara. En cenital `_aspect_min`=0.0 lo desactiva,
                            # porque una persona vista desde arriba se ve más
                            # ancha que alta y aquí se descartaría.
                            if _aspect_min > 0.0 and bh < bw * _aspect_min:
                                continue

                            # Offset de track_id unico por camara (anti-colision
                            # en el reidentificador compartido).
                            tid_p = int(track_ids_p[i]) + _cam_off if track_ids_p is not None else None
                            # Pertenencia a la zona segun sv.PolygonZone (ancla
                            # pie/centro). Disponible para el Hito 5 (entradas/
                            # salidas por persistent_id). None si no hay zona.
                            _in_zone = (bool(_inside_mask[i])
                                        if _inside_mask is not None else None)
                            detections.append({
                                'class': -1,  # -1 = persona genérica
                                'box': box,
                                'center': center,
                                'confidence': confidence,
                                'track_id': tid_p,
                                'in_zone': _in_zone,
                            })
                            staff_detected += 1

                            # Mapa de calor: acumular posicion de los pies
                            if (AnalyticsConfig.HEATMAP_ENABLED
                                    and hasattr(self, '_heatmap')
                                    and self._heatmap is not None):
                                self._heatmap.add_person(
                                    box,
                                    inference_image.shape[1],
                                    inference_image.shape[0])

                if self.debug_mode and detections:
                    print(f"📊 Frame {self.frame_counter}: {len(detections)} detectados ({staff_detected} con track)")

                # Actualizar tracks usando IDs de BoTSORT (estables con Kalman)
                self._update_tracks_botsort(detections)

                # Capturas: foto por persona (tracks ESTABLECIDOS) + dedup Re-ID
                # + relleno de genero/edad cuando converge la demografia.
                if AnalyticsConfig.CAPTURE_DETECTIONS:
                    self._capture_established_tracks(inference_image)
                    self._update_captures_demographics()

                # Resolucion de identidad (Hito 4): tracker_id -> persistent_id
                # (confirmado por rostro) o id provisional. Va DESPUES de la
                # demografia (que es quien mapea el rostro a la galeria) y ANTES
                # de entrada/salida, segun el orden del plan. Se le pasa el
                # frame para el Re-ID corporal (Hito 9): enrolar/matchear cuerpo.
                self._resolve_track_identities(inference_image)

                # Entradas/salidas POR PERSONA (Hito 5): maquina FUERA/DENTRO
                # por persistent_id con anti-rebote -> visitantes unicos del
                # area + entradas/salidas exactas (no infladas).
                self._process_zone_transitions()

                # Procesar entrada/salida (LEGACY por track_id: alimenta fotos
                # de entrada/salida y conteos por categoria del dashboard; el
                # conteo AUTORITATIVO de unicos/entradas es el de arriba).
                persons_inside = self.process_entry_exit_logic(image)

                # Verificar alertas periódicas
                self.check_periodic_alerts(image)

                # ── Analitica de personas (conteo + calidad demografica) ──
                self._run_analytics(image)

                # Log periódico
                if self.frame_counter % 30 == 0:
                    if self.debug_mode:
                        print(f"\n📈 Resumen Frame {self.frame_counter}:")
                        print(f"   Empleados en ROI: {persons_inside}")
                        print(f"   Total en área: {self.personas_en_area}")
                        print(f"   Tracks activos: {len(self.active_tracks)}")

                        # Mostrar contadores por empleado
                        for staff_id, count in self.employee_counters.items():
                            if count > 0:
                                staff_name = self.get_staff_display_name(staff_id)
                                print(f"   {staff_name}: {count} veces")

                # Dibujado: modo CLASICO (servidor dibuja y envia la imagen) o
                # modo DIRECTO (no dibuja ni codifica; devuelve las detecciones
                # y el CLIENTE las pinta -> mucha menos latencia/ancho de banda).
                _draw_server = getattr(self, '_draw_server', True)
                _detections_payload = None
                if _draw_server:
                    processed_image = self.draw_detections(
                        image.copy(), persons_inside)
                else:
                    _detections_payload = self._build_detections()
                    processed_image = image  # frame limpio (solo para snapshot)

                # Mapa de calor: snapshot periodico PNG/JSON para el dashboard
                # (sobre la imagen limpia) y overlay en vivo SOLO en modo clasico
                # (en modo directo el cliente no recibe imagen del servidor).
                if (AnalyticsConfig.HEATMAP_ENABLED
                        and hasattr(self, '_heatmap')
                        and self._heatmap is not None):
                    self._heatmap.maybe_save_snapshot(
                        camera_id, background=processed_image,
                        camera_name=getattr(
                            self, '_camera_display_name', None))
                    if heatmap_activate and _draw_server:
                        processed_image = self._heatmap.render_overlay(
                            processed_image)

                # Metadatos
                ec = getattr(self, 'entry_counts', {})
                abc = getattr(self, 'active_by_category', {})
                metadata = {
                    # Alertas de este pipeline. Antes iba SIEMPRE vacia: el
                    # cliente no tenia nada que mostrar en su panel lateral y
                    # el reenvio por WhatsApp no tenia nada que enviar. Ahora
                    # lleva un evento por visitante, en el instante en que su
                    # genero y edad convergen (ver _emitir_visitante).
                    'alerts': self._drenar_alertas(),
                    'frame_number': self.frame_counter,
                    'roi_active': activate_roi,
                    'staff_detected': staff_detected,
                    'persons_inside': persons_inside,
                    'persons_in_area': self.personas_en_area,
                    # Aforo instantaneo via sv.PolygonZone (Hito 2): personas
                    # DENTRO del area ahora mismo (ancla pie/centro segun config).
                    'zone_count': int(getattr(self, '_zone_count', 0)),
                    # Identidad persistente (Hito 4): visitantes unicos
                    # CONFIRMADOS por rostro en la sesion (NO inflado por
                    # reapariciones, los provisionales no cuentan). Y cuantos
                    # tracks activos aun son provisionales (sin rostro).
                    'unique_visitors_confirmed': len(
                        getattr(self, '_confirmed_unique', set())),
                    'provisional_tracks': sum(
                        1 for t in self.active_tracks.values()
                        if not t.get('persistent_confirmed', False)),
                    # Total de personas distintas en la galeria persistente
                    # (acumulado historico, DB nunca se borra).
                    'total_unique_persons_db': (
                        self._reidentifier.total_unique_persons
                        if getattr(self, '_reidentifier', None) is not None
                        else 0),
                    # Entradas/salidas por persona (Hito 5): visitantes unicos
                    # del AREA (persistent_id distintos que ENTRARON) + totales
                    # de transiciones confirmadas. Aforo instantaneo = zone_count.
                    'unique_visitors_entered': len(
                        getattr(self, '_unique_entered', set())),
                    'zone_entries_total': int(getattr(self, '_total_entries', 0)),
                    'zone_exits_total': int(getattr(self, '_total_exits', 0)),
                    # Re-ID corporal (Hito 9): tracks re-enlazados por cuerpo
                    # este frame (sin rostro, p.ej. de espaldas).
                    'body_relinked_tracks': int(getattr(self, '_body_links', 0)),
                    'active_tracks': len(self.active_tracks),
                    'employee_counters': dict(self.employee_counters),
                    'entry_counts': dict(ec),
                    'active_by_category': dict(abc),
                    'total_entries': sum(ec.values()),
                }
                # Modo DIRECTO: detecciones para que el cliente las dibuje.
                if _detections_payload is not None:
                    metadata['detections'] = _detections_payload


                # Ángulo de cámara (auto-detectado o manual) para que el
                # cliente sepa qué perfil se está aplicando.
                if hasattr(self, '_auto_angle_detector'):
                    _eff = (self._camera_angle_override
                            or (self._auto_angle if self._auto_angle_enabled
                                else None)
                            or self._default_camera_angle)
                    metadata['camera_angle'] = {
                        'efectivo': _eff,
                        'auto_activo': bool(self._auto_angle_enabled),
                        'auto_detectado': self._auto_angle,
                        'auto_muestras': self._auto_angle_detector.n_samples,
                        'mediana_hw': round(
                            self._auto_angle_detector.median_hw, 2),
                    }

                # Enriquecer metadata con analitica de personas
                if hasattr(self, '_people_counter'):
                    metadata['people_counter'] = self._people_counter.get_stats()
                if hasattr(self, '_demographics'):
                    metadata['demographics'] = self._demographics.get_counts()
                if (hasattr(self, '_heatmap')
                        and self._heatmap is not None):
                    _hm = self._heatmap.get_stats()
                    _hm['activo'] = bool(heatmap_activate)
                    metadata['heatmap'] = _hm

                # Reporte consolidado (Hito 8): throttled (la distribucion
                # genero/edad recorre la galeria -> no cada frame). Se cachea,
                # se mete en metadata y se persiste a JSON (sobrevive reinicios).
                _now_r = time.time()
                if (_now_r - getattr(self, '_last_report_ts', 0.0)
                        >= AnalyticsConfig.REPORT_EVERY_S):
                    self._last_report_ts = _now_r
                    try:
                        self._cached_report = self.get_analytics_report()
                        self.save_analytics_report()
                    except Exception as _re:
                        logger.debug("reporte consolidado fallo: %s", _re)
                if getattr(self, '_cached_report', None) is not None:
                    metadata['analytics_report'] = self._cached_report

                return processed_image, metadata
            except Exception as e:
                logger.error(f"Error procesando frame: {e}")
                import traceback
                traceback.print_exc()
                return image, {'error': str(e)}
            finally:
                # Guardar último frame procesado en el estado de la cámara y restaurar estado global
                try:
                    cam_state['last_processed_frame'] = self.last_processed_frame
                    cam_state['personas_en_area'] = getattr(self, 'personas_en_area', cam_state.get('personas_en_area', 0))
                    cam_state['entry_counts'] = getattr(self, 'entry_counts', cam_state.get('entry_counts', {}))
                    # Contadores ENTEROS (inmutables): hay que reescribirlos al
                    # estado de la camara o el incremento del frame se pierde al
                    # restaurar (los sets/dicts persisten solos por referencia).
                    cam_state['_total_entries'] = int(getattr(self, '_total_entries', 0))
                    cam_state['_total_exits'] = int(getattr(self, '_total_exits', 0))
                except Exception:
                    pass
                # Restaurar estado global
                self._pop_state()

    def set_roi(self, roi_points: List[Tuple[int, int]]):
        self.roi_polygon = np.array(roi_points, np.int32)
        print(f"✅ ROI actualizado a {len(roi_points)} puntos")

    def reset_counter(self):
        self.personas_en_area = 0
        self.employee_counters.clear()
        self.entry_counts = {'Hombres': 0, 'Mujeres': 0, 'Niños': 0, 'Personas': 0}
        self.last_counted_frame = 0
        self.last_counted_id = 0
        self.counted_tracks.clear()
        self.recent_counted_persons.clear()
        self.person_cooldown.clear()
        self.active_tracks.clear()
        # Reiniciar el tracker de corto plazo (IDs de ByteTrack desde cero).
        self._byte_tracker = self._make_byte_tracker()
        # Reiniciar el set de unicos confirmados de la sesion (Hito 4). NO
        # toca la galeria persistente (esa nunca se borra automaticamente).
        self._confirmed_unique = set()
        # Reiniciar la maquina de entrada/salida por persona (Hito 5).
        self._zone_state = {}
        self._total_entries = 0
        self._total_exits = 0
        self._unique_entered = set()
        self.track_history.clear()
        self.movement_history.clear()
        self.sent_entry_photos.clear()
        self.sent_exit_photos.clear()
        self.last_sent_time.clear()
        self.alert_minutes_sent.clear()
        # Reiniciar modulos de analitica
        if hasattr(self, '_demographics'):
            self._demographics.reset()
        if hasattr(self, '_people_counter'):
            self._people_counter.reset()
        print("🔄 Contadores reiniciados")

    def toggle_minimal_info(self):
        self.show_minimal_info = not self.show_minimal_info
        status = "MÍNIMA" if self.show_minimal_info else "COMPLETA"
        print(f"🔧 Información: {status}")

    def get_stats(self) -> Dict[str, Any]:
        persons_inside = 0
        
        for track in self.active_tracks.values():
            roi_polygon_points = self.roi_polygon.reshape((-1, 1, 2))
            is_inside = self.is_inside_polygon(track['center'], roi_polygon_points)
            
            if is_inside:
                persons_inside += 1
        
        ec = getattr(self, 'entry_counts', {})
        stats = {
            'total_persons_in_area': self.personas_en_area,
            'persons_inside': persons_inside,
            'frame_counter': self.frame_counter,
            'active_tracks': len(self.active_tracks),
            'employee_counters': dict(self.employee_counters),
            'entry_counts': dict(ec),
            'total_entries': sum(ec.values()),
            'staff_names': dict(self.staff_names),
            'roi_points': self.roi_polygon.tolist(),
        }
        # Estado de la auto-detección de ángulo (visibilidad para el cliente)
        if hasattr(self, '_auto_angle_detector'):
            stats['camera_angle'] = {
                'override_manual': self._camera_angle_override,
                'auto_activo': bool(getattr(self, '_auto_angle_enabled', False)),
                'auto_detectado': getattr(self, '_auto_angle', None),
                'auto_muestras': self._auto_angle_detector.n_samples,
                'auto_mediana_hw': round(
                    self._auto_angle_detector.median_hw, 2),
            }
        # Agregar stats de analitica de personas
        if hasattr(self, '_people_counter'):
            stats['people_counter'] = self._people_counter.get_stats()
        if hasattr(self, '_demographics'):
            stats['demographics'] = self._demographics.get_counts()
        return stats

    # ── Orquestacion de la analitica de personas ─────────────────

    def _run_analytics(self, frame: np.ndarray):
        """Actualiza el conteo de personas y el log de calidad demografica."""
        try:
            # 1. Actualizar contador de personas activas
            if hasattr(self, '_people_counter'):
                self._people_counter.update_active(set(self.active_tracks.keys()))

            # 2. Log periodico del reporte de calidad facial (Hito 3): deja
            #    constancia en el log de que NO entran muestras borrosas.
            if (hasattr(self, '_demographics')
                    and self.frame_counter % 300 == 0):
                self._demographics.log_quality_report()

        except Exception as e:
            logger.error(f"Error en analytics: {e}")

    def get_analytics_stats(self) -> Dict[str, Any]:
        """Retorna estadisticas de los modulos de analitica de personas."""
        stats = {}
        if hasattr(self, '_people_counter'):
            stats['people_counter'] = self._people_counter.get_stats()
        if hasattr(self, '_demographics'):
            stats['demographics'] = self._demographics.get_counts()
        return stats

    def _update_track_class_stability(self, track_id: int):
        """Compute majority vote over recent class observations and update track class if stable."""
        history = self.track_class_history.get(track_id)
        if not history:
            return

        counts = Counter(history)
        most_common, count = counts.most_common(1)[0]
        if len(history) >= 1:
            proportion = count / len(history)
            # require minimum number of observations or proportion
            min_obs = min(3, self.class_history_len)
            if len(history) >= min_obs and proportion >= self.class_stability_threshold:
                # update track's class to stable one
                if track_id in self.active_tracks:
                    self.active_tracks[track_id]['class'] = most_common
                    self.active_tracks[track_id]['stable_class'] = most_common


def create_staff_processor(**kwargs) -> PersonAmazonas:
    return PersonAmazonas(**kwargs)