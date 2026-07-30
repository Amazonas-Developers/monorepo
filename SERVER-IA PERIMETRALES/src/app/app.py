import asyncio
import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple

import cv2
import msgpack
import numpy as np
from fastapi import (FastAPI, WebSocket, WebSocketDisconnect,
                     UploadFile, File, Form)
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState

from . import captura_contrato as _captura
from . import validacion_contrato as _validacion
from . import whatsapp_alertas as _whatsapp
from ..analityc.core.Perimetrales import MultiObjectProcessor
from ..analityc.core.base_perimeter import BasePerimeter
from ..analityc.core.botsort_wrapper import BoTSORTWrapper
from ..analityc.core.car_washed import VehicleProcessor
from ..analityc.core.hardware_available import device_hardware
from ..analityc.core.Hummus import HummusProcess
from ..analityc.core.Misters import MistersProcess
from ..analityc.core.perimetrales_multicam import PerimetralesMultiCam
from ..analityc.core.person_amazona_inference import PersonAmazonas
from ..analityc.config.config import get_config

# Motor perimetral NUEVO (paquete perimetral_nocturna en la raiz del proyecto):
# 8 clases finales con genero (HOMBRE/MUJER/NIÑO + CARRO/MOTO/CAMIONETA/CAMIÓN),
# Re-ID global entre camaras y alertas llegada/salida con tiempos. Reemplaza al
# PerimetralesMultiCam legado para el cliente perimetrales-view.
try:
    from perimetral_nocturna.adaptador_websocket import PerimetralNocturnaWS
except Exception as _exc:  # el server sigue arrancando si el paquete falta
    PerimetralNocturnaWS = None
    logging.getLogger(__name__).warning(
        "perimetral_nocturna no disponible (%s); el modo PerimetralesMultiCam "
        "usara el procesador legado", _exc)

# VIGILANTE-AMAZONAS (paquete vigilante_amazonas): deteccion 7 clases +
# personal_seguridad (CLIP) + Re-ID de personas ESPECIFICAS (galeria de
# rostros/vestimenta administrada en el dashboard :8090) + alertas Socket.IO.
# Las camaras se VEN en perimetrales-view: el cliente envia los frames por
# este websocket (modo "VigilanteAmazonas").
try:
    from vigilante_amazonas.adaptador_websocket import VigilanteWS
except Exception as _exc:
    VigilanteWS = None
    logging.getLogger(__name__).warning(
        "vigilante_amazonas no disponible (%s); modo VigilanteAmazonas "
        "desactivado", _exc)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constantes de configuracion
# -----------------------------------------------------------------------------
WEBSOCKET_TIMEOUT: float = 30.0      # segundos sin mensaje antes de ping
MAX_QUEUE_PER_CLIENT: int = 2        # frames maximos en cola por cliente
JPEG_QUALITY: int = 70               # calidad JPEG para imagen de salida
EXECUTOR_WORKERS: int = 8


# -----------------------------------------------------------------------------
# Ciclo de vida: ThreadPoolExecutor gestionado correctamente
# -----------------------------------------------------------------------------
executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=EXECUTOR_WORKERS)


def _get_live_executor() -> ThreadPoolExecutor:
    """Devuelve el executor activo; si fue apagado, lo recrea."""
    global executor
    if getattr(executor, "_shutdown", False):
        logger.info("Recreando ThreadPoolExecutor (anterior estaba apagado)")
        executor = ThreadPoolExecutor(max_workers=EXECUTOR_WORKERS)
    return executor


@asynccontextmanager
async def lifespan(app: FastAPI):
    global executor
    if getattr(executor, "_shutdown", False):
        executor = ThreadPoolExecutor(max_workers=EXECUTOR_WORKERS)
    logger.info("Servidor iniciando - ThreadPoolExecutor con %d workers", EXECUTOR_WORKERS)
    yield
    logger.info("Servidor apagandose - cerrando ThreadPoolExecutor")
    executor.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard web de analitica de visitantes (GET /dashboard + API JSON).
# Import al final del modulo NO es posible (app debe existir); el router
# usa imports perezosos de este modulo para evitar el ciclo.
from .dashboard import router as dashboard_router  # noqa: E402
app.include_router(dashboard_router)


# -----------------------------------------------------------------------------
# Estado de conexiones
# -----------------------------------------------------------------------------
active_connections: Dict[str, Dict[str, Any]] = {}
connection_lock = asyncio.Lock()


# -----------------------------------------------------------------------------
# Helpers de configuracion / hardware
# -----------------------------------------------------------------------------

def _get_device_default() -> str:
    dev = getattr(device_hardware, "device_default", "cpu")
    if isinstance(dev, dict):
        return dev.get("gpu_use", "cpu")
    return dev if isinstance(dev, str) else "cpu"


def _get_first_gpu() -> str:
    gpus = getattr(device_hardware, "gpu_tuple", [])
    if isinstance(gpus, (list, tuple)) and gpus:
        gpu0 = gpus[0]
        if isinstance(gpu0, dict):
            return gpu0.get("gpu_use", _get_device_default())
    return _get_device_default()


def _get_model_path(config: Dict[str, Any], inference_name: str, default_path: str) -> str:
    model_paths = config.get("model_paths")
    if isinstance(model_paths, dict):
        path = model_paths.get(inference_name)
        if path:
            return path
    return config.get("model_path", default_path)


def _get_person_model_path(config: Dict[str, Any], inference_name: str, default_path: str) -> str:
    person_model_paths = config.get("person_model_paths")
    if isinstance(person_model_paths, dict):
        path = person_model_paths.get(inference_name)
        if path:
            return path
    return default_path


# -----------------------------------------------------------------------------
# Creacion de procesadores
# -----------------------------------------------------------------------------

def _build_processor(type_inference: str, client_id: str, config: Dict[str, Any]) -> Any:
    """Instancia y devuelve el procesador correcto segun el tipo de inferencia."""
    gpu = _get_first_gpu()

    if type_inference == "Misters":
        return MistersProcess(
            client_id=client_id,
            model_path=_get_model_path(config, "Misters", "models/base/misters.pt"),
            person_model_path=_get_person_model_path(config, "Misters", r"C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES\models\base\yolo26m.pt"),
            confidence_threshold=config.get("confidence_threshold", 0.5),
            iou_threshold=config.get("iou_threshold", 0.5),
            device=gpu,
        )

    if type_inference == "Autolavado":
        return VehicleProcessor(
            client_id=client_id,
            model_path=_get_model_path(config, "Lavado", "models/base/best.pt"),
            confidence_threshold=config["confidence_threshold"],
            iou_threshold=config["iou_threshold"],
            device=gpu,
        )

    if type_inference == "Hummus":
        # Piso suave a 0.20 (descartar conf<0.20 del modelo evita la basura).
        _hummus_conf = max(0.20, float(config.get("confidence_threshold", 0.25) or 0.25))
        _hummus_iou  = float(config.get("iou_threshold", 0.45) or 0.45)
        # ETAPA 2 — VLM verificador (opcional, activable desde config)
        _hummus_enable_vlm = bool(config.get("enable_vlm", False))
        _hummus_vlm_model  = str(config.get("vlm_model_id", "Qwen/Qwen2-VL-2B-Instruct"))
        _hummus_dwell_order = float(config.get("order_dwell_seconds", 1.5))
        _hummus_dwell_deliv = float(config.get("delivery_dwell_seconds", 1.5))
        return HummusProcess(
            client_id=client_id,
            model_path=_get_model_path(config, "Hummus", "models/base/1080.pt"),
            person_model_path=_get_person_model_path(config, "Hummus", "models/base/yolo26m.pt"),
            confidence_threshold=_hummus_conf,
            iou_threshold=_hummus_iou,
            device=gpu,
            enable_vlm=_hummus_enable_vlm,
            vlm_model_id=_hummus_vlm_model,
            order_dwell_seconds=_hummus_dwell_order,
            delivery_dwell_seconds=_hummus_dwell_deliv,
        )

    if type_inference == "Perimetrales":
        return BasePerimeter(
            client_id=client_id,
            model_path=_get_model_path(config, "Perimetrales", r"C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES\models\base\yolo26m.pt"),
            device=gpu,
        )

    if type_inference == "PerimetralesMultiCam":
        # Motor perimetral nuevo (singleton: modelos cargados una sola vez y
        # la identidad global sobrevive reconexiones). Si el paquete no esta
        # disponible se cae al procesador legado.
        if PerimetralNocturnaWS is not None:
            from perimetral_nocturna.adaptador_websocket import get_perimetral_ws
            return get_perimetral_ws()
        return PerimetralesMultiCam(
            model_path=_get_model_path(config, "PerimetralesMultiCam", r"C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES\models\base\yolo26m.pt"),
            device=gpu,
            debug=False,
        )

    if type_inference == "VigilanteAmazonas":
        # VIGILANTE-AMAZONAS (singleton: modelos y galeria compartidos entre
        # conexiones; dashboard :8090 y Socket.IO :8091 quedan activos).
        if VigilanteWS is None:
            raise ValueError("vigilante_amazonas no esta disponible en este "
                             "servidor (ver log de arranque)")
        from vigilante_amazonas.adaptador_websocket import get_vigilante_ws
        return get_vigilante_ws()

    if type_inference == "PerimetralesBoTSORT":
        return BoTSORTWrapper(
            model_path=_get_model_path(config, "PerimetralesBoTSORT", r"C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES\models\base\yolo26m.pt"),
            device=gpu,
            match_thresh=0.45,
            debug=False,
        )

    if type_inference == "Personal de Amazonas":
        return PersonAmazonas(
            client_id=client_id,
            # Cosmeticos eliminado: solo personas + genero + edad.
            model_path=None,
            person_model_path=_get_person_model_path(config, "Personal de Amazonas", r"C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES\models\base\yolo26m.pt"),
            confidence_threshold=config["confidence_threshold"],
            iou_threshold=config["iou_threshold"],
            device=gpu,
        )

    raise ValueError(f"Tipo de inferencia desconocido: '{type_inference}'")


# -----------------------------------------------------------------------------
# Procesamiento sincronico de frames (ejecutado en ThreadPoolExecutor)
# -----------------------------------------------------------------------------

def _decode_image(image_data: Any) -> np.ndarray:
    """Decodifica la imagen desde base64-str, bytes o data-URI."""
    if isinstance(image_data, (bytes, bytearray)):
        image_bytes = bytes(image_data)
    elif isinstance(image_data, str):
        if image_data.startswith("data:") and "," in image_data:
            image_data = image_data.split(",", 1)[1]
        image_bytes = base64.b64decode(image_data)
    else:
        raise ValueError(f"Tipo de imagen no soportado: {type(image_data)}")

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Imagen corrupta o formato invalido")
    return img


def _encode_jpeg(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    success, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise ValueError("Error en la codificacion JPEG de salida")
    return buf.tobytes()


def _apply_track_classes(processor: Any, track_classes: list) -> None:
    """Aplica dinámicamente las clases a detectar en el procesador."""
    if not track_classes:
        return
    classes = [int(c) for c in track_classes if isinstance(c, (int, float))]
    if not classes:
        return

    # BasePerimeter: usa class_ids
    if isinstance(processor, BasePerimeter):
        processor.class_ids = classes

    # PersonAmazonas: solo personas + demografia (cosmeticos eliminado).
    # processor.model es None, asi que se usa directamente la lista de
    # clases COCO que envia el cliente (por defecto [0] = Persona).
    elif isinstance(processor, PersonAmazonas):
        if hasattr(processor, 'model') and processor.model and hasattr(processor.model, 'names'):
            full = list(processor.model.names.keys())
            processor.all_classes = full
            processor.staff_names = {
                cid: processor.model.names[cid].replace('_', ' ').title()
                for cid in full
            }
            processor.staff_names[-1] = 'Persona'
        else:
            processor.all_classes = classes

    # MultiObjectProcessor: usa vehicle_classes + person_classes
    elif isinstance(processor, MultiObjectProcessor):
        processor.vehicle_classes = [c for c in classes if c in {2, 3, 5, 7}]
        processor.person_classes = [c for c in classes if c == 0]
        processor.all_classes = classes

    # BoTSORTWrapper / PerimetralesMultiCam: detectan solo personas por defecto
    elif hasattr(processor, 'detect_classes'):
        processor.detect_classes = classes


def process_image_sync(
    processor: Any,
    img: np.ndarray,
    roi: Any,
    roi_activate: bool,
    door_roi: Any = None,
    door_activate: bool = False,
    door_direction: Any = None,
    door_direction_activate: bool = False,
    pay_roi: Any = None,
    withdraw_roi: Any = None,
    pay_roi_activate: bool = True,
    withdraw_roi_activate: bool = True,
    camera_id: Any = 1,
    track_classes: Any = None,
    order_zone: Any = None,
    order_zone_activate: bool = False,
    delivery_zone: Any = None,
    delivery_zone_activate: bool = False,
    enable_vlm: Optional[bool] = None,
    camera_angle: Optional[str] = None,
    heatmap_activate: bool = False,
    camera_name: Optional[str] = None,
    draw_server: bool = True,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Despacha el frame al procesador correcto.
    Se ejecuta en el ThreadPoolExecutor, nunca en el event loop.
    """
    # Aplicar clases dinámicas al procesador si las envía el cliente
    if track_classes is not None and isinstance(track_classes, list):
        _apply_track_classes(processor, track_classes)

    # VIGILANTE-AMAZONAS: deteccion 7 clases + Re-ID + permanencia en el area.
    if VigilanteWS is not None and isinstance(processor, VigilanteWS):
        return processor.process_frame(
            img, camera_id,
            camera_name=camera_name,
            track_classes=track_classes if isinstance(track_classes, list) else None,
            draw=bool(draw_server),
            roi=roi,                       # poligono del area (contador/alertas)
            roi_activate=bool(roi_activate),
        )

    # Motor perimetral NUEVO: detections (modo directo) + alerts con el
    # contrato llegada/salida que renderiza el cliente perimetrales-view.
    if PerimetralNocturnaWS is not None and isinstance(processor, PerimetralNocturnaWS):
        return processor.process_frame(
            img, camera_id,
            camera_name=camera_name,
            track_classes=track_classes if isinstance(track_classes, list) else None,
            draw=bool(draw_server),
        )

    if isinstance(processor, PerimetralesMultiCam):
        tracks = processor.process_frame(
            img, camera_id,
            pay_roi=pay_roi, withdraw_roi=withdraw_roi,
            pay_roi_activate=pay_roi_activate, withdraw_roi_activate=withdraw_roi_activate,
        )
        for t in tracks:
            x1, y1, x2, y2 = t.get("bbox", (0, 0, 0, 0))
            gid = t.get("global_id", 0)
            conf = t.get("conf", 0.0)
            cam = t.get("camera_id", camera_id)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"ID:{gid} CAM:{cam} {conf:.2f}"
            y_text = max(10, y1 - 6)
            (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (x1, max(0, y_text - th - bl)), (x1 + tw, y_text + bl), (0, 255, 255), -1)
            cv2.putText(img, label, (x1, y_text), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        return img, {"tracks": tracks}

    if isinstance(processor, PersonAmazonas):
        try:
            cam_proc = processor.get_camera_processor(camera_id)
            # Perfil de angulo por camara enviado por el cliente (opcional):
            # frontal|lateral|cenital. Ajusta el filtro de aspecto en cenital.
            if camera_angle:
                cam_proc.set_camera_angle(camera_angle)
            # Si el cliente no manda nombre, se usa el camera_id: asi las
            # capturas quedan etiquetadas con la camara REAL y no con "cam"
            # generico, que era lo que impedia el desglose por pasillo en el
            # dashboard de tienda (los heatmaps ya usaban el camera_id).
            cam_proc._camera_display_name = camera_name or str(camera_id)
            cam_proc._draw_server = bool(draw_server)
            return cam_proc.process_frame(img, roi, roi_activate, camera_id, heatmap_activate=heatmap_activate)
        except Exception:
            if camera_angle:
                processor.set_camera_angle(camera_angle)
            processor._camera_display_name = camera_name or str(camera_id)
            processor._draw_server = bool(draw_server)
            return processor.process_frame(img, roi, roi_activate, camera_id, heatmap_activate=heatmap_activate)

    if isinstance(processor, MultiObjectProcessor):
        try:
            cam_proc = processor.get_camera_processor(camera_id)
            return cam_proc.process_frame(
                img, roi, roi_activate,
                door_roi, door_activate, door_direction, door_direction_activate,
            )
        except Exception:
            return processor.process_frame(
                img, roi, roi_activate,
                door_roi, door_activate, door_direction, door_direction_activate,
            )

    # HummusProcess: ROI personas (amarillo) + zonas Toma_De_Orden (azul) y Entrega_De_Plato (rojo)
    if isinstance(processor, HummusProcess):
        effective_roi = roi if (isinstance(roi, list) and len(roi) >= 3) else None
        eff_order   = order_zone    if (isinstance(order_zone, list)    and len(order_zone)    >= 3) else None
        eff_deliv   = delivery_zone if (isinstance(delivery_zone, list) and len(delivery_zone) >= 3) else None
        return processor.process_frame(
            img,
            roi=effective_roi, roi_active=bool(roi_activate),
            order_zone=eff_order,       order_zone_active=bool(order_zone_activate),
            delivery_zone=eff_deliv,    delivery_zone_active=bool(delivery_zone_activate),
            enable_vlm=enable_vlm,
            send_to_server=False,
        )

    # MistersProcess: misma lógica de ROI
    if isinstance(processor, MistersProcess):
        effective_roi = roi if (roi_activate and isinstance(roi, list) and len(roi) >= 3) else None
        return processor.process_frame(img, effective_roi, send_to_server=False)

    # Procesadores legacy genericos
    return processor.process_frame(
        img, roi, roi_activate,
        door_roi, door_activate, door_direction, door_direction_activate,
    )


def _full_frame_sync(
    processor: Any,
    image_data: Any,
    roi: Any,
    roi_activate: bool,
    door_roi: Any,
    door_activate: bool,
    door_direction: Any,
    door_direction_activate: bool,
    pay_roi: Any,
    withdraw_roi: Any,
    pay_roi_activate: bool,
    withdraw_roi_activate: bool,
    camera_id: Any,
    track_classes: Any = None,
    order_zone: Any = None,
    order_zone_activate: bool = False,
    delivery_zone: Any = None,
    delivery_zone_activate: bool = False,
    enable_vlm: Optional[bool] = None,
    camera_angle: Optional[str] = None,
    heatmap_activate: bool = False,
    camera_name: Optional[str] = None,
    draw_server: bool = True,
) -> Dict[str, Any]:
    """
    Combina decode + inferencia + encode JPEG en una sola llamada al executor.
    El event loop no se bloquea en ningun paso de CPU.

    draw_server=False -> MODO DIRECTO: el servidor NO dibuja ni codifica la
    imagen; devuelve solo las detecciones en metadata y el cliente las pinta
    sobre su propio frame (mucha menos latencia/ancho de banda).
    """
    t0 = time.time()
    img = _decode_image(image_data)
    processed_img, metadata = process_image_sync(
        processor, img, roi, roi_activate,
        door_roi, door_activate, door_direction, door_direction_activate,
        pay_roi, withdraw_roi, pay_roi_activate, withdraw_roi_activate, camera_id,
        track_classes=track_classes,
        order_zone=order_zone, order_zone_activate=order_zone_activate,
        delivery_zone=delivery_zone, delivery_zone_activate=delivery_zone_activate,
        enable_vlm=enable_vlm,
        camera_angle=camera_angle,
        heatmap_activate=heatmap_activate,
        camera_name=camera_name,
        draw_server=draw_server,
    )
    processing_time = round(time.time() - t0, 3)

    # MODO DIRECTO: no se codifica ni envia la imagen (el cliente la dibuja).
    if not draw_server:
        return {
            "camera_id": camera_id,
            "status": "success",
            "metadata": metadata,
            "processed_image": None,
            "processing_time": processing_time,
        }

    jpeg_bytes = _encode_jpeg(processed_img)
    return {
        "camera_id": camera_id,
        "status": "success",
        "metadata": metadata,
        "processed_image": f"data:image/jpeg;base64,{base64.b64encode(jpeg_bytes).decode()}",
        "processing_time": processing_time,
    }


# -----------------------------------------------------------------------------
# Worker por cliente
# -----------------------------------------------------------------------------

class ClientWorker:
    """
    Cola y worker dedicados a UN cliente.

    Ventajas vs cola global original:
      - Un cliente lento NO bloquea a los demas (head-of-line blocking eliminado).
      - Limpieza independiente al desconectar sin afectar al resto.
      - Limite de cola y politica de drop configurables por instancia.
    """

    def __init__(self, client_id: str, processor: Any):
        self.client_id = client_id
        self.processor = processor
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_PER_CLIENT)
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"worker-{self.client_id}")

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        while not self._queue.empty():
            try:
                fut, _ = self._queue.get_nowait()
                if not fut.done():
                    fut.cancel()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def enqueue(self, payload: tuple) -> asyncio.Future:
        """
        Encola un frame.
        Si la cola esta llena aplica drop-head: descarta el frame MAS ANTIGUO
        y acepta el nuevo. Asi siempre se procesa el frame mas reciente.
        """
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        if self._queue.full():
            try:
                old_fut, _ = self._queue.get_nowait()
                self._queue.task_done()
                if not old_fut.done():
                    old_fut.set_result({"status": "dropped", "reason": "queue_full"})
                logger.debug("Drop-head frame para client=%s", self.client_id)
            except asyncio.QueueEmpty:
                pass

        await self._queue.put((future, payload))
        return future

    async def _run(self) -> None:
        loop = asyncio.get_event_loop()
        while not self._stopped:
            try:
                future, payload = await self._queue.get()
            except asyncio.CancelledError:
                break

            if future.done():
                self._queue.task_done()
                continue

            try:
                result = await loop.run_in_executor(_get_live_executor(), _full_frame_sync, *payload)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                logger.error("Error en worker client=%s: %s", self.client_id, exc)
                if not future.done():
                    future.set_result({"status": "error", "message": str(exc)})
            finally:
                self._queue.task_done()


# -----------------------------------------------------------------------------
# Endpoint WebSocket
# -----------------------------------------------------------------------------

@app.websocket("/ws/{type_inference}")
async def websocket_endpoint(websocket: WebSocket, type_inference: str):
    await websocket.accept()
    client_id = f"client_{id(websocket)}"
    worker: Optional[ClientWorker] = None
    processor: Any = None

    try:
        # -- 1. Inicializacion del procesador ----------------------------------
        config = get_config()
        try:
            processor = _build_processor(type_inference, client_id, config)
        except ValueError as exc:
            await websocket.send_text(json.dumps({"status": "error", "message": str(exc)}))
            await websocket.close(code=1008)
            return

        worker = ClientWorker(client_id, processor)
        worker.start()

        async with connection_lock:
            active_connections[client_id] = {
                "websocket": websocket,
                "processor": processor,
                "worker": worker,
                "type_inference": type_inference,
                "connected_at": time.time(),
                "last_active": time.time(),
            }

        logger.info("Cliente conectado: %s tipo=%s", client_id, type_inference)

        await websocket.send_text(json.dumps({
            "id_connection": id(websocket),
            "event": "connection_init",
            "type_inference": type_inference,
            "data": {"roi": False},
        }))

        # -- 2. Bucle principal ------------------------------------------------
        while True:
            try:
                recv = await asyncio.wait_for(websocket.receive(), timeout=WEBSOCKET_TIMEOUT)
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.send_text(json.dumps({"status": "ping"}))
                    except Exception:
                        break
                continue

            # Deserializar (msgpack o JSON)
            incoming_is_binary = recv.get("bytes") is not None
            try:
                if incoming_is_binary:
                    request = msgpack.unpackb(recv["bytes"], raw=False, strict_map_key=False)
                else:
                    request = json.loads(recv.get("text") or "")
            except Exception as exc:
                logger.warning("Error deserializando mensaje client=%s: %s", client_id, exc)
                await _send_error(websocket, incoming_is_binary, str(exc))
                continue

            # Captura de payloads para el contrato (HITO 3). Apagada salvo que
            # ELDE_CAPTURA_PAYLOADS=1; guarda una muestra por forma distinta de
            # mensaje, sin binarios. Nunca lanza.
            _captura.registrar("entrante", type_inference, request)

            # Validacion del contrato (HITO 3), detras de la capa de
            # compatibilidad: entiende el formato antiguo y el nuevo. Arranca
            # en modo 'observar' (registra lo que rechazaria y deja pasar), asi
            # que activarla NO cambia el comportamiento hasta que se ponga
            # ELDE_VALIDAR_CONTRATO=estricto.
            _ok, _motivo = _validacion.revisar(request)
            if not _ok:
                logger.warning("contrato rechaza el mensaje client=%s: %s",
                               client_id, _motivo)
                await _send_error(websocket, incoming_is_binary,
                                  f"contrato: {_motivo}")
                continue

            data = request.get("data", {})
            if not isinstance(data, dict):
                await _send_error(websocket, incoming_is_binary, "Campo 'data' invalido o ausente")
                continue

            if "image" not in data:
                await _send_error(websocket, incoming_is_binary, "Campo 'image' requerido")
                continue

            # Extraer y normalizar campos
            image_data = data["image"]
            roi = data.get("roi_coordinates", "")
            roi_activate = bool(data.get("roi_activate", False))
            door_roi = data.get("door_roi_coordinates")
            door_activate = bool(data.get("door_roi_activate", False))
            door_direction = data.get("door_direction")
            door_direction_activate = data.get("door_direction_activate", False)

            # Compatibilidad: door_direction_activate como lista de puntos
            if isinstance(door_direction_activate, (list, tuple)) and door_direction is None:
                door_direction = door_direction_activate
                door_direction_activate = True

            pay_roi = data.get("pay_roi")
            withdraw_roi = data.get("withdraw_roi")
            pay_roi_activate = bool(data.get("pay_roi_activate", True))
            withdraw_roi_activate = bool(data.get("withdraw_roi_activate", True))

            # Zonas Hummus específicas por clase
            order_zone = data.get("order_zone_coordinates")
            order_zone_activate = bool(data.get("order_zone_activate", False))
            delivery_zone = data.get("delivery_zone_coordinates")
            delivery_zone_activate = bool(data.get("delivery_zone_activate", False))

            # ETAPA 2: toggle de VLM desde el cliente (None = no cambiar)
            _raw_enable_vlm = data.get("enable_vlm", None)
            enable_vlm = bool(_raw_enable_vlm) if _raw_enable_vlm is not None else None

            # Perfil de angulo por camara (opcional): frontal|lateral|cenital.
            # Solo aplica a PersonAmazonas; ajusta el filtro de aspecto.
            camera_angle = data.get("camera_angle") or None

            # Mapa de calor: overlay sobre el video (PersonAmazonas).
            heatmap_activate = bool(data.get("heatmap_activate", False))

            # Nombre legible de la camara (para dashboard/heatmap).
            camera_name = str(data.get("camera_name") or "").strip()[:48] or None

            # MODO DIRECTO: si el cliente pide draw_server=False, el servidor
            # NO dibuja ni envia la imagen; devuelve solo detecciones y el
            # cliente las pinta (menos latencia). Default True (compatibilidad).
            draw_server = bool(data.get("draw_server", True))

            # Alertas por WhatsApp (bot 'ava'): lo activa el cliente
            # perimetrales-view con un toggle. Se fija como atributo del
            # procesador (mismo patrón que _draw_server); solo VigilanteWS lo
            # consume — inofensivo para el resto de procesadores.
            try:
                processor._enviar_whatsapp = bool(data.get("enviar_whatsapp", False))
            except Exception:
                pass

            camera_id_raw = data.get("camera_id", 1)
            try:
                camera_id = int(camera_id_raw)
            except (TypeError, ValueError):
                camera_id = camera_id_raw

            # Clases a trackear (lista de class_ids COCO enviadas por el cliente)
            track_classes = data.get("track_classes", None)

            # Payload para _full_frame_sync (orden fijo de argumentos)
            frame_payload = (
                processor, image_data,
                roi, roi_activate,
                door_roi, door_activate,
                door_direction, door_direction_activate,
                pay_roi, withdraw_roi,
                pay_roi_activate, withdraw_roi_activate,
                camera_id,
                track_classes,
                order_zone, order_zone_activate,
                delivery_zone, delivery_zone_activate,
                enable_vlm,
                camera_angle,
                heatmap_activate,
                camera_name,
                draw_server,
            )

            try:
                future = await worker.enqueue(frame_payload)
                result = await future
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error encolando frame client=%s: %s", client_id, exc)
                result = {"status": "error", "message": str(exc)}

            if websocket.client_state != WebSocketState.CONNECTED:
                break

            # Reenvio de alertas a WhatsApp para los pipelines que NO son
            # VigilanteWS (ese ya tiene su propia ruta en su adaptador y
            # duplicariamos los mensajes). Solo actua si el cliente activo su
            # interruptor y si el procesador publica alertas en metadata.
            if (data.get("enviar_whatsapp")
                    and type_inference != "VigilanteAmazonas"
                    and isinstance(result, dict)):
                _whatsapp.reenviar(result.get("metadata"),
                                   camera_name=str(camera_name or ""),
                                   camera_id=str(camera_id_raw or ""))

            request["data"] = result
            _captura.registrar("saliente", type_inference, request)
            try:
                if incoming_is_binary:
                    await websocket.send_bytes(msgpack.packb(request, use_bin_type=True))
                else:
                    await websocket.send_json(request)
            except Exception as exc:
                logger.error("Error enviando respuesta client=%s: %s", client_id, exc)
                break

            async with connection_lock:
                if client_id in active_connections:
                    active_connections[client_id]["last_active"] = time.time()

    except WebSocketDisconnect:
        logger.info("Cliente desconectado: %s", client_id)
    except Exception as exc:
        logger.error("Error critico client=%s: %s", client_id, exc)
    finally:
        # -- 3. Limpieza completa ----------------------------------------------
        if worker:
            await worker.stop()
        async with connection_lock:
            active_connections.pop(client_id, None)
        if processor and hasattr(processor, "cleanup"):
            try:
                processor.cleanup()
            except Exception:
                pass
        logger.info("Limpieza completa para client=%s", client_id)


async def _send_error(websocket: WebSocket, is_binary: bool, message: str) -> None:
    """Envia un error estructurado al cliente."""
    payload = {"data": {"status": "error", "message": message}}
    try:
        if is_binary:
            await websocket.send_bytes(msgpack.packb(payload, use_bin_type=True))
        else:
            await websocket.send_json(payload)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Endpoints HTTP
# -----------------------------------------------------------------------------

@app.get("/")
def init_server():
    return {"status": "active", "connections": len(active_connections)}


@app.get("/health")
def health_check():
    now = time.time()
    clients = [
        {
            "client_id": cid,
            "type_inference": info.get("type_inference", "unknown"),
            "idle_seconds": round(now - info.get("last_active", now), 1),
            "connected_seconds": round(now - info.get("connected_at", now), 1),
        }
        for cid, info in active_connections.items()
    ]
    return {
        "status": "active",
        "total_connections": len(active_connections),
        "executor_max_workers": EXECUTOR_WORKERS,
        "clients": clients,
        # Estado del contrato: sirve para decidir cuando se puede pasar de
        # 'observar' a 'estricto' sin romper ningun cliente.
        "contrato": _validacion.resumen(),
        "captura_payloads": _captura.resumen() if _captura.activa() else None,
    }


# ──────────────────────────────────────────────────────────────────────────
#  Router multimodal de percepcion (bajo demanda)
#    POST /vlm/ask     imagen + question  -> VQA (Qwen2.5-VL)  "¿que hay?"
#    POST /vlm/detect  imagen + classes   -> grounding (YOLO-World)  "detecta X"
#    POST /vlm/route   imagen + query     -> decide VQA vs detect por la consulta
#  Son `def` (no async) -> FastAPI los corre en threadpool y NO bloquean el
#  WebSocket de tiempo real. Los modelos se cargan perezosamente la 1a vez.
# ──────────────────────────────────────────────────────────────────────────
def _decode_upload(image: UploadFile):
    raw = image.file.read()
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    return frame


def _get_router():
    from ..analityc.core.multimodal_router import get_multimodal_router
    return get_multimodal_router(device=0)


@app.post("/vlm/ask")
def vlm_ask(image: UploadFile = File(...), question: str = Form(...),
            max_new_tokens: int = Form(160), context: str = Form("")):
    try:
        frame = _decode_upload(image)
        if frame is None:
            return {"status": "error", "message": "imagen invalida"}
        ans = _get_router().vqa(frame, question, max_new_tokens=max_new_tokens,
                                context=context)
        return {"status": "ok", "answer": ans}
    except Exception as exc:
        logger.exception("vlm_ask error")
        return {"status": "error", "message": str(exc)}


@app.post("/vlm/detect")
def vlm_detect(image: UploadFile = File(...), classes: str = Form(...),
               conf: float = Form(0.05)):
    try:
        frame = _decode_upload(image)
        if frame is None:
            return {"status": "error", "message": "imagen invalida"}
        terms = [t.strip() for t in classes.split(",") if t.strip()] or [classes]
        dets = _get_router().detect(frame, terms, conf=conf)
        return {"status": "ok", "terms": terms, "detections": dets}
    except Exception as exc:
        logger.exception("vlm_detect error")
        return {"status": "error", "message": str(exc)}


@app.post("/vlm/route")
def vlm_route(image: UploadFile = File(...), query: str = Form(...),
              context: str = Form("")):
    try:
        frame = _decode_upload(image)
        if frame is None:
            return {"status": "error", "message": "imagen invalida"}
        result = _get_router().route(frame, query, context=context)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("vlm_route error")
        return {"status": "error", "message": str(exc)}


@app.post("/vlm/events")
def vlm_events(image: UploadFile = File(...), context: str = Form("")):
    """Detecta eventos en el frame: 'compra' y 'entrega de bandeja de comida'.
    'context' (opcional) se suma al contexto global del negocio."""
    try:
        frame = _decode_upload(image)
        if frame is None:
            return {"status": "error", "message": "imagen invalida"}
        result = _get_router().detect_events(frame, context=context)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("vlm_events error")
        return {"status": "error", "message": str(exc)}


@app.post("/vlm/model")
def vlm_set_model(model: str = Form(...)):
    """Selecciona el modelo VQA del servidor ('3b' rapido | '7b' calidad).
    Afecta a TODO el servidor (manual, route y deteccion continua)."""
    try:
        r = _get_router()
        ok = r.set_vqa_model(model)
        return {"status": "ok" if ok else "error", "current": r.current_vqa()}
    except Exception as exc:
        logger.exception("vlm_set_model error")
        return {"status": "error", "message": str(exc)}


@app.get("/vlm/model")
def vlm_get_model():
    try:
        return {"status": "ok", "current": _get_router().current_vqa()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/vlm/context")
def vlm_set_context(context: str = Form("")):
    """Fija el contexto GLOBAL del negocio/escena (se inyecta en todas las
    consultas: VQA, route y deteccion continua). Persiste."""
    try:
        r = _get_router()
        r.set_context(context)
        return {"status": "ok", "context": r.context}
    except Exception as exc:
        logger.exception("vlm_set_context error")
        return {"status": "error", "message": str(exc)}


@app.get("/vlm/context")
def vlm_get_context():
    try:
        return {"status": "ok", "context": _get_router().context}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ───────────────────────────────────────────────────────────────────────
#  Camaras activas de deteccion de personas (lo usa el dashboard)
# ───────────────────────────────────────────────────────────────────────
def _iter_person_processors():
    """(client_id, camera_id, camera_processor) de todas las camaras activas
    del tipo 'Personal de Amazonas'."""
    for cid, info in list(active_connections.items()):
        proc = info.get("processor")
        if not isinstance(proc, PersonAmazonas):
            continue
        cams = getattr(proc, "_camera_processors", None) or {}
        if cams:
            for cam_id, cam_proc in list(cams.items()):
                yield cid, cam_id, cam_proc
        else:
            # Instancia unica (sin multi-camara): primera camara conocida.
            cam_ids = list(getattr(proc, "camera_states", None) or {})
            yield cid, (cam_ids[0] if cam_ids else None), proc
