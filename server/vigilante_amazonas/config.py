"""
Configuración centralizada de VIGILANTE-AMAZONAS.

TODO parámetro ajustable del sistema vive aquí; ningún módulo debe tener
valores mágicos hardcodeados. Los dispositivos CUDA se declaran como
"deseados" y se resuelven en tiempo de ejecución con `resolver_dispositivo()`:
si la GPU pedida no existe (p. ej. las 3090 Ti aún no están instaladas),
todo cae a cuda:0 (la RTX 5060 Ti) en modo degradado documentado en el log.
"""

from __future__ import annotations

import os
from pathlib import Path

# =============================================================================
# RUTAS BASE
# =============================================================================
RUTA_BASE: Path = Path(__file__).resolve().parent               # vigilante_amazonas/
RUTA_SERVIDOR: Path = RUTA_BASE.parent                          # server/
RUTA_MODELOS: Path = RUTA_SERVIDOR / "models"                   # modelos compartidos del servidor

RUTA_GALERIA: Path = RUTA_BASE / "galeria"       # fotos de personas de interés
RUTA_SNAPSHOTS: Path = RUTA_BASE / "snapshots"   # capturas de alertas
RUTA_DB: Path = RUTA_BASE / "db" / "vigilante.db"
RUTA_LOGS: Path = RUTA_BASE / "logs"

# =============================================================================
# CÁMARAS Y CAPTURA
# =============================================================================
# {"nombre_camara": "rtsp://usuario:clave@ip:554/ruta"}. También se aceptan
# rutas a archivos de video (para pruebas) — la fuente detecta el tipo sola.
CAMARAS_RTSP: dict[str, str] = {
    # "camara_entrada":  "rtsp://admin:clave@192.168.1.10:554/Streaming/Channels/101",
    # "camara_porton":   "rtsp://admin:clave@192.168.1.11:554/Streaming/Channels/101",
}

FPS_PROCESAMIENTO: int = 10          # frames/seg a inferir por cámara (el resto se descarta)
COLA_FRAMES_MAX: int = 2             # frames en cola por cámara (descartables, nunca acumular lag)
RTSP_BACKOFF_INICIAL_SEG: float = 1.0    # reintento tras caída de cámara
RTSP_BACKOFF_MAXIMO_SEG: float = 60.0    # tope del backoff exponencial
RTSP_TIMEOUT_LECTURA_SEG: float = 10.0   # sin frames en este lapso => reconectar

# =============================================================================
# DISPOSITIVOS (deseados; ver resolver_dispositivo())
# =============================================================================
DEVICE_DETECCION: str = "cuda:0"     # RTX 5060 Ti 16GB (Blackwell sm_120)
DEVICE_REID: str = "cuda:1"          # RTX 3090 Ti #1 24GB (Ampere sm_86) — aún no instalada
DEVICE_VLM: str = "cuda:2"           # RTX 3090 Ti #2 24GB (Ampere sm_86) — aún no instalada

# =============================================================================
# DETECCIÓN (YOLO26m en TensorRT FP16, fallback a .pt)
# =============================================================================
# Cadena de resolución del detector: el primer archivo existente gana.
# ⚠️ MEDIDO 17-jul-2026: el engine yolo26m_1280_b4.engine tiene la CONFIANZA
# DEGRADADA según el contenido (zidane: 0.57/0.43 vs 0.91/0.89 del .pt) →
# con umbral 0.45 no detectaba NADA en cámaras reales. El .pt FP16 queda como
# preferido (~19 ms vs 16.5 del engine; diferencia irrelevante). Si se
# reconstruye un engine sano, volver a ponerlo primero.
# OJO: los .engine son ESPECÍFICOS de la GPU en que se construyeron (5060 Ti)
# y de su imgsz/batch; si cambia la GPU hay que reconstruirlos.
DETECTOR_CANDIDATOS: list[Path] = [
    RUTA_MODELOS / "base" / "yolo26m.pt",               # PyTorch FP16 (PREFERIDO)
    RUTA_MODELOS / "base" / "yolo26m_1280_b4.engine",   # batch 4 (conf degradada)
    RUTA_MODELOS / "base" / "yolo26m_1280.engine",      # batch 1
]
DETECTOR_IMGSZ: int = 1280
BATCH_MAX: int = 4                   # tamaño máx. de lote (el engine b4 es batch 4)
# ── Umbrales de confianza: SUELO global + compuerta POR CLASE ────────────
# MEDIDO 3-ago-2026 sobre 208 fotos reales de alertas de estas cámaras: cada
# clase vive en un rango de confianza distinto (mediana persona 0.58, carro
# 0.32, objeto 0.27 y MOTO 0.16). Con el umbral único 0.25, las motos se
# reconocían el 25% y los carros nocturnos (IR) el 27%. Con la compuerta por
# clase: moto 25%→72%, carro 60%→70%, objeto 52%→60%, persona intacta, a
# cambio de 14 detecciones cruzadas de baja confianza en 208 fotos (la
# mayoría, carros aparcados REALES de la escena).
# `CONF_DETECCION` es ahora el SUELO que ve el modelo (todo lo de abajo se
# descarta); la compuerta real por clase es CONF_POR_CLASE y se aplica en
# deteccion/detector.py tras mapear la clase propia.
CONF_DETECCION: float = 0.10
CONF_POR_CLASE: dict[str, float] = {
    "persona": 0.25,     # sobrada (mediana 0.58); bajarla solo mete ruido
    "moto": 0.10,        # la clase más débil en estas cámaras (mediana 0.16)
    "carro": 0.15,       # de noche (IR) la confianza cae mucho
    "camioneta": 0.15,
    "camion": 0.15,
    "objeto": 0.15,      # presión de FP casi nula en la banda baja (3/208)
}


def umbral_de_clase(clase: str) -> float:
    """Umbral de confianza efectivo para una clase propia del detector."""
    return float(CONF_POR_CLASE.get(clase, CONF_DETECCION))
MAX_DETECCIONES: int = 300           # YOLO26 es NMS-free, salida (N,300,6)
DETECTOR_HALF: bool = True           # FP16 en el fallback .pt

# Clases COCO relevantes -> clases propias (la lógica vive en
# deteccion/mapeo_clases.py; aquí solo los parámetros ajustables).
#   0=person 1=bicycle 2=car 3=motorcycle 5=bus 7=truck
#   24=backpack 26=handbag 28=suitcase
CLASES_COCO_USADAS: list[int] = [0, 2, 3, 5, 7, 24, 26, 28]
# Heurística camioneta/camión sobre la clase COCO 7 (truck) y 2 (car):
# aspecto = ancho_bbox / alto_bbox; área relativa = área_bbox / área_frame.
CAMIONETA_AREA_REL_MAX: float = 0.18     # truck "pequeño" => camioneta (pickup)
CAMION_ASPECTO_MIN: float = 1.15         # truck ancho y grande => camión
OBJETO_CLASES_COCO: list[int] = [24, 26, 28]   # mochila, bolso, maleta => "objeto"

# =============================================================================
# CLASIFICADOR FINO DE VEHÍCULOS (carro vs camioneta vs camión)
# =============================================================================
# COCO no distingue pickup de camión (y muchas camionetas caen en `car`), así
# que la heurística de caja se queda corta. Este clasificador (yolo11s-cls,
# ImageNet) corre sobre el RECORTE de cada vehículo rastreado, mapea las
# clases ImageNet (pickup/jeep/minivan -> camioneta; trailer_truck/
# garbage_truck/... -> camión; sports_car/cab/... -> carro) y decide por
# VOTACIÓN por track (etiqueta estable, sin parpadeo). Si el modelo no está,
# se cae a la heurística de caja con aviso en el log.
# DESACTIVADO tras medirlo (28-jul-2026). Sobre 277 recortes de vehiculo
# reales de estas camaras, el clasificador ImageNet acertaba "carro" en el
# 5 % y se inventaba camioneta/camion en el 44 %. Las clases ImageNet que
# mas "veia" en los coches eran amphibian, ballpoint, milk_can, grey_whale
# y slide_rule: esta completamente fuera de su dominio con recortes
# pequenos, doblemente comprimidos y de captura de pantalla.
# El detector SOLO (YOLO26) acierta el 99 % con confianza media 0.89, asi
# que esta segunda opinion solo degradaba. Sintoma que produjo: 54
# capturas etiquetadas CAMION frente a 14 CARRO, siendo todos sedanes.
# Para reactivarlo hay que reentrenar el clasificador con recortes de
# ESTAS camaras, no con ImageNet.
VEHICULOS_FINO_HABILITADO: bool = False
RUTA_VEHICULOS_CLS: Path = RUTA_MODELOS / "vehiculos" / "yolo11s-cls.pt"
VEHICULO_CADA_N_FRAMES: int = 4          # throttle por track (coste ~3ms/crop)
VEHICULO_UMBRAL: float = 0.25            # prob. acumulada mínima (ImageNet reparte masa)
VEHICULO_CROP_MIN_PX: int = 40           # crops más chicos no se clasifican
VEHICULO_VOTOS: int = 3                  # historial de votos por track (moda)

# =============================================================================
# MODO ESTACIONAMIENTO (4-ago-2026)
# =============================================================================
# La óptica vehicular del motor vigilante: solo alertan los VEHÍCULOS, la
# alerta central es "estacionado" (vehículo quieto más del umbral) y la
# salida reporta el tiempo total. Ver src/analityc/core/estacionamiento.py.
ESTACIONAMIENTO_UMBRAL_SEG: float = float(
    os.getenv("ESTACIONAMIENTO_UMBRAL_SEG", "300"))     # 5 min por defecto
# Eventos que este modo reenvía a WhatsApp (con su propio antiflood).
ESTACIONAMIENTO_EVENTOS_WHATSAPP: tuple[str, ...] = tuple(
    e.strip() for e in
    os.getenv("ESTACIONAMIENTO_EVENTOS_WHATSAPP",
              "llegada,estacionado,salida").split(",") if e.strip())

# =============================================================================
# TRACKING (ByteTrack vía supervision, por cámara)
# =============================================================================
# 0.25 impedía que las motos (mediana 0.16) llegaran a ACTIVAR un track
# aunque el detector las viera: la compuerta por clase vive ahora en el
# detector (CONF_POR_CLASE), así que el tracker activa lo que aquel dejó
# pasar. El parpadeo lo siguen filtrando AREA_MIN_FRAMES_LLEGADA (3 frames
# dentro del área antes de alertar) y el antiflood de WhatsApp/Jarvis.
TRACK_ACTIVATION_UMBRAL: float = 0.10
# Segundos que un track sobrevive SIN detección antes de perder su ID.
# 7 s aguanta parpadeos largos de objetos estáticos/oscuros (motos aparcadas)
# sin crear IDs nuevos. OJO: rastreador.py fuerza este valor REAL en el
# tracker (supervision lo reescalaba a <1 s — gotcha documentado ahí).
TRACK_PERDIDO_SEG: float = 7.0
TRACK_MATCH_UMBRAL: float = 0.80

# =============================================================================
# CLASIFICADOR PERSONAL DE SEGURIDAD (CLIP zero-shot sobre crops de persona)
# =============================================================================
# APAGADO por regla del operador: NADIE se etiqueta como personal de
# seguridad por su APARIENCIA (chaleco/uniforme via CLIP). La etiqueta de
# personal exige una FOTO registrada en config/personal/ (staff_gallery):
# sin foto subida, es "persona". CLIP confundia chalecos reflectantes y
# uniformes oscuros con guardias -> falsos "personal_seguridad" sin
# configuracion alguna. Re-activar SOLO si se asume ese riesgo.
SEGURIDAD_HABILITADO: bool = False
SEGURIDAD_MODELO_CLIP: str = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"  # en caché HF local
# Prompts zero-shot (ES conceptual, EN literal porque CLIP entiende mejor inglés).
SEGURIDAD_PROMPTS_POSITIVOS: list[str] = [
    "a security guard in uniform with a high-visibility vest",
    "a uniformed security officer with a badge and equipment belt",
    "a police officer wearing a police uniform",
    "a watchman wearing a reflective security vest",
]
# Los negativos deben cubrir TODO lo que no es guardia: casual, formal,
# deportivo, laboral. Un set pobre hace que un traje "gane" como uniforme.
SEGURIDAD_PROMPTS_NEGATIVOS: list[str] = [
    "a person in casual clothes",
    "a man wearing a business suit and tie",
    "an office worker in formal attire",
    "a pedestrian in street clothes",
    "a person wearing sportswear",
    "a construction worker with a hard hat",
    "a person in a t-shirt and jeans",
    "a person wearing a dark winter jacket and dark pants",
    "a commuter in a black coat seen from behind",
    "a person in a hoodie or puffer jacket",
]
SEGURIDAD_UMBRAL: float = 0.62           # prob. softmax mínima para reetiquetar
SEGURIDAD_CADA_N_FRAMES: int = 8         # throttle por track (no clasificar cada frame)
SEGURIDAD_VOTOS_MIN: int = 2             # votos positivos consecutivos para confirmar
SEGURIDAD_CROP_MIN_PX: int = 48          # crops más chicos no se clasifican

# =============================================================================
# RE-IDENTIFICACIÓN (rostro + vestimenta) — corre en DEVICE_REID
# =============================================================================
# --- Rostro: YuNet (detección) + ArcFace w600k_r50 (embedding 512d) vía onnxruntime ---
RUTA_YUNET_ONNX: Path = RUTA_MODELOS / "classifiers" / "face_detection_yunet_2023mar.onnx"
RUTA_ARCFACE_ONNX: Path = RUTA_MODELOS / "classifiers" / "w600k_r50.onnx"
ROSTRO_MIN_PX: int = 40                  # lado mínimo del rostro detectado para embeber
ROSTRO_CONF_DETECCION: float = 0.70      # confianza mínima de YuNet
UMBRAL_ROSTRO: float = 0.55              # similitud coseno ArcFace para match directo

# --- Vestimenta: OSNet (torchreid, onnx) o CLIP — conmutable ---
# Comparativa Hito 4 (crops de una misma escena): CLIP margen intra/inter
# +0.63 vs OSNet +0.29 (y el inter de OSNet 0.681 roza el umbral 0.70)
# => CLIP activo. OJO: con material real multi-cámara/multi-día OSNet podría
# rendir mejor (está entrenado para re-id); re-medir con fotos reales.
VESTIMENTA_BACKEND: str = "clip"         # "osnet" | "clip"
RUTA_OSNET_ONNX: Path = RUTA_MODELOS / "osnet" / "osnet_x1_0_market1501.onnx"
UMBRAL_VESTIMENTA: float = 0.70          # similitud coseno para match por vestimenta
VESTIMENTA_CROP_MIN_PX: int = 64

# --- Fusión y decisión ---
# score_fusionado = max(rostro, vestimenta) con bono si ambos coinciden.
FUSION_BONO_AMBOS: float = 0.10          # bono al score si rostro Y vestimenta coinciden
ZONA_GRIS_VLM: tuple[float, float] = (0.45, 0.65)   # rango que dispara verificación VLM
COOLDOWN_ALERTA_SEG: int = 60            # anti-spam por (persona, cámara)
REID_CADA_N_FRAMES: int = 4              # throttle de Re-ID por track
REID_MAX_GALERIA: int = 500              # tope de embeddings en el índice en memoria

# =============================================================================
# VLM VERIFICADOR (Qwen2.5-VL en DEVICE_VLM, no bloqueante)
# =============================================================================
# "auto": se habilita si hay GPU dedicada (cuda:2 real) O suficiente VRAM libre.
# "si" / "no": forzar. En el caché HF local está el 3B; el 7B es configurable
# pero habría que descargarlo (~9 GB en AWQ).
VLM_HABILITADO: str = "auto"             # "auto" | "si" | "no"
VLM_MODELO_ID: str = "Qwen/Qwen2.5-VL-3B-Instruct"
VLM_VRAM_MINIMA_GB: float = 6.0          # VRAM libre mínima para cargar el VLM
VLM_MAX_TOKENS: int = 320    # el prompt por pasos necesita espacio para describir
VLM_COLA_MAX: int = 16                   # verificaciones pendientes máximas
VLM_TIMEOUT_SEG: float = 30.0            # una verificación que tarde más se descarta
VLM_MAX_REFERENCIAS: int = 2             # fotos de referencia por consulta
# Si el VLM NO está disponible, qué hacer con la zona gris:
#   True  => emitir la alerta marcada "sin verificación VLM"
#   False => descartar el match de zona gris
ZONA_GRIS_SIN_VLM_EMITIR: bool = True

# =============================================================================
# ALERTAS Y RED
# =============================================================================
# Puertos (sobrescribibles por variable de entorno para despliegues donde
# 8090/8091 ya estén en uso: set VIGILANTE_PUERTO_API=8095, etc.).
# El panel unifica lo que antes eran dos cosas: la galeria de :8090 y el
# panel de detecciones. La gestion de personas de interes sigue viva, montada
# en /gestion dentro del mismo puerto.
PUERTO_API: int = int(os.getenv("VIGILANTE_PUERTO_API", "5333"))       # panel
PUERTO_SOCKETIO: int = int(os.getenv("VIGILANTE_PUERTO_SOCKETIO", "8091"))  # alertas
HOST_ESCUCHA: str = "0.0.0.0"
EVENTO_ALERTA: str = "alerta_persona"    # nombre del evento Socket.IO
SNAPSHOT_JPEG_CALIDAD: int = 85
SNAPSHOT_MAX_LADO_PX: int = 640          # el snapshot de la alerta se reescala a este lado máx.
# Carpeta del cliente perimetral, hermana de `server/` bajo la raiz del
# proyecto. Era una ruta absoluta a esta maquina y dejo de existir al mover el
# cliente a `clients/perimetrales` el 30-jul-2026.
RUTA_CLIENTE_VIEW: Path = Path(
    os.getenv("VIGILANTE_RUTA_CLIENTE")
    or RUTA_SERVIDOR.parent / "clients" / "perimetrales")

# =============================================================================
# ALERTAS POR WHATSAPP (bot 'ava')
# =============================================================================
# Cada alerta puede reenviarse como IMAGEN a un grupo de WhatsApp vía el bot
# HTTP (POST con JSON {my-text, my-file(base64), type}). El número/grupo destino
# va embebido en la URL (...number=<id>@g.us). El envío SOLO ocurre si el
# cliente perimetrales-view activó su toggle (flag por frame); aquí solo se
# configura el destino y el anti-flood. Todo sobrescribible por entorno.
WHATSAPP_BOT_URL: str = os.getenv(
    "WHATSAPP_BOT_URL",
    "https://72.68.60.254:4000/bot/imgV2/number=120363167230826480@g.us")
# El bot sirve su certificado sin la cadena intermedia -> verificación TLS OFF
# por defecto (mismo criterio que el resto de integraciones con este bot).
WHATSAPP_VERIFY_TLS: bool = os.getenv("WHATSAPP_VERIFY_TLS", "0") == "1"
WHATSAPP_TIMEOUT_SEG: float = float(os.getenv("WHATSAPP_TIMEOUT_SEG", "20"))
# Anti-flood: mínimo entre envíos de la MISMA alerta (misma persona/evento/cámara).
WHATSAPP_COOLDOWN_SEG: float = float(os.getenv("WHATSAPP_COOLDOWN_SEG", "30"))
# Anti-flood: mínimo entre CUALQUIER par de envíos (limita ráfagas globales).
WHATSAPP_INTERVALO_GLOBAL_SEG: float = float(
    os.getenv("WHATSAPP_INTERVALO_GLOBAL_SEG", "3"))
# QUE se avisa. Por defecto solo la ENTRADA al perimetro marcado por el ROI:
# antes salia una foto por cada evento (llegada, salida, permanencia y
# merodeo), asi que una sola persona cruzando generaba varios mensajes y el
# aviso importante -"ha entrado alguien"- se perdia entre el ruido.
# Ampliar con: set WHATSAPP_EVENTOS=llegada,salida,merodeo
WHATSAPP_EVENTOS: tuple[str, ...] = tuple(
    e.strip().lower() for e in
    os.getenv("WHATSAPP_EVENTOS", "llegada").split(",") if e.strip())
# Tamano maximo del cuerpo POST. El bot devuelve 413 Payload Too Large con
# las imagenes tal cual salian del pipeline (~110 KB de payload), asi que la
# foto se reencoge antes de enviarla. 64 KB es un limite conservador tipico;
# si el bot admite mas, subirlo mejora la nitidez del aviso.
WHATSAPP_MAX_PAYLOAD_KB: int = int(os.getenv("WHATSAPP_MAX_PAYLOAD_KB", "64"))
# Lado maximo de la imagen enviada, antes incluso de bajar calidad.
WHATSAPP_MAX_LADO_PX: int = int(os.getenv("WHATSAPP_MAX_LADO_PX", "640"))

# Clases gruesas que disparan aviso. "objeto" queda fuera a proposito.
WHATSAPP_CLASES: tuple[str, ...] = tuple(
    c.strip().lower() for c in
    os.getenv("WHATSAPP_CLASES", "persona,vehiculo").split(",") if c.strip())

# Niveles de alerta válidos para personas de interés.
NIVELES_ALERTA: list[str] = ["informativo", "medio", "critico"]

# =============================================================================
# ÁREA Y PERMANENCIA (contador de tiempo en el área + alertas entrada/salida)
# =============================================================================
# El "área" es el ROI que el cliente dibuja y activa (roi_activate). Si no hay
# ROI activo, el área = TODO el campo visual de la cámara (cualquier detección
# cuenta). Se rastrea el tiempo de permanencia por objeto (track) y se emiten
# tarjetas de LLEGADA (al entrar) y SALIDA (al irse, con la permanencia total).
# Con un ROI activo, ENFOCAR la detección dentro del área: las detecciones
# cuyo objeto esté FUERA del ROI se descartan (no se dibujan, no se rastrea su
# permanencia, no entran a Re-ID). El detector sigue corriendo en todo el
# frame para que ByteTrack mantenga IDs estables al cruzar el borde, pero solo
# se reporta lo de DENTRO. Sin ROI activo, se ve todo (comportamiento normal).
DETECCION_SOLO_EN_ROI: bool = True

AREA_PERMANENCIA_HABILITADA: bool = True
AREA_ALERTA_LLEGADA: bool = True         # tarjeta al entrar un objeto al área
AREA_ALERTA_SALIDA: bool = True          # tarjeta al salir (con permanencia total)
AREA_CONTADOR_EN_CAJA: bool = True       # mostrar el cronómetro sobre cada caja
# Frames consecutivos que un track debe verse antes de confirmar su LLEGADA
# (filtra tracks fugaces/falsos de ByteTrack; evita spam de alertas basura).
AREA_MIN_FRAMES_LLEGADA: int = 3
# Segundos sin volver a ver un track para declararlo SALIDO del área.
# Debe ser MAYOR que TRACK_PERDIDO_SEG: si el tracker aguanta 7 s un
# parpadeo, el área no debe declarar la salida antes de eso.
AREA_TTL_SALIDA_SEG: float = 10.0
# RE-VINCULACIÓN: si aparece un track "nuevo" cuya caja solapa (IoU >= este
# umbral) con la de un objeto del área que dejó de verse hace poco y es de la
# misma familia (persona/vehículo), HEREDA su identidad: el contador de
# permanencia continúa y NO se emite otra llegada. Robustez ante cambios de
# ID de ByteTrack sobre objetos estáticos.
AREA_REVINCULACION_IOU: float = 0.40
# El estado debe llevar al menos este lapso sin verse para poder heredarse
# (evita robar la identidad de un track que sigue vivo en el mismo frame).
AREA_REVINCULACION_GAP_SEG: float = 0.3
# Permanencia mínima para emitir la SALIDA (descarta objetos que solo pasaron).
AREA_MIN_PERMANENCIA_SALIDA_SEG: float = 1.0
# Alerta de PERMANENCIA: cuando un objeto acumula este tiempo dentro del área
# se emite una tarjeta "permanencia" (una sola vez por objeto). 0 = desactivada.
AREA_ALERTA_PERMANENCIA_SEG: float = 300.0     # 5 minutos
# Foto de las alertas de área: frame COMPLETO de la cámara (sin zoom al
# objeto), con TODOS los objetos vigilados marcados con elipse + etiqueta.
# False = comportamiento anterior (solo el recorte del objeto).
ALERTA_FOTO_COMPLETA: bool = True
ALERTA_FOTO_MAX_ANCHO: int = 960               # se reescala si el frame es más ancho
# Clases sobre las que se rastrea permanencia (por defecto TODAS las propias).
AREA_CLASES_VIGILADAS: list[str] = [
    "persona", "personal_seguridad", "moto", "carro", "camioneta", "camion", "objeto",
]
# Enrutamiento a las columnas del AlertsSidebar del cliente (Vehículos|Personas).
AREA_CLASE_GRUESA: dict[str, str] = {
    "persona": "persona", "personal_seguridad": "persona", "objeto": "persona",
    "moto": "vehiculo", "carro": "vehiculo", "camioneta": "vehiculo", "camion": "vehiculo",
}
# Nombre mostrado (en MAYÚSCULAS; el AlertsSidebar toma el 1er token para el
# icono -> "PERSONA SEGURIDAD" da 👤, "PERSONAL_SEGURIDAD" daría ❔).
AREA_CLASE_ETIQUETA: dict[str, str] = {
    "persona": "PERSONA", "personal_seguridad": "PERSONA SEGURIDAD",
    "moto": "MOTO", "carro": "CARRO", "camioneta": "CAMIONETA",
    "camion": "CAMIÓN", "objeto": "OBJETO",
}

# =============================================================================
# MERODEO EN EL ÁREA (el mismo individuo/vehículo entra y sale varias veces)
# =============================================================================
# La identidad ENTRE visitas no puede venir del track (muere con cada salida):
# se usa la APARIENCIA (embedding CLIP del recorte — ropa de la persona,
# color/forma del vehículo). En cada LLEGADA confirmada se compara contra los
# visitantes recientes de esa cámara; si el mismo aspecto acumula
# MERODEO_MIN_VISITAS entradas dentro de MERODEO_VENTANA_SEG => alerta
# "merodeo" (sidebar + jarvis).
MERODEO_HABILITADO: bool = True
MERODEO_MIN_VISITAS: int = 3             # entradas para declarar merodeo
MERODEO_VENTANA_SEG: float = 600.0       # ventana de conteo (10 min)
MERODEO_SIMILITUD: float = 0.80          # coseno CLIP para "es el mismo"
MERODEO_TTL_SEG: float = 1800.0          # olvidar visitantes tras 30 min sin verse
MERODEO_COOLDOWN_SEG: float = 300.0      # no repetir la alerta del mismo visitante (5 min)
MERODEO_CROP_MIN_PX: int = 48            # recortes más chicos no se registran
MERODEO_MAX_EMBEDDINGS: int = 3          # embeddings guardados por visitante
# Clases vigiladas para merodeo (objeto NO: un bulto no "entra y sale").
MERODEO_CLASES: list[str] = [
    "persona", "personal_seguridad", "moto", "carro", "camioneta", "camion",
]

# =============================================================================
# LOGGING ESTRUCTURADO
# =============================================================================
LOG_NIVEL: str = "INFO"
LOG_ARCHIVO: Path = RUTA_LOGS / "vigilante.jsonl"
LOG_ROTACION_MB: int = 10
LOG_RESPALDOS: int = 5

# =============================================================================
# VISOR DE DEPURACIÓN (Hito 2)
# =============================================================================
VISOR_DEBUG: bool = False                # ventana OpenCV local con cajas
VISOR_MJPEG: bool = True                 # stream MJPEG en el dashboard (/debug/<camara>)

# Colores BGR por clase para el visor y los snapshots.
COLORES_CLASE: dict[str, tuple[int, int, int]] = {
    "persona":            (0, 200, 0),
    "personal_seguridad": (255, 160, 0),
    "moto":               (0, 165, 255),
    "carro":              (255, 0, 0),
    "camioneta":          (255, 0, 255),
    "camion":             (0, 0, 255),
    "objeto":             (0, 255, 255),
}


# =============================================================================
# RESOLUCIÓN DE DISPOSITIVOS
# =============================================================================
def resolver_dispositivo(preferido: str) -> str:
    """Devuelve `preferido` si esa GPU existe; si no, cae a cuda:0 o cpu.

    Permite declarar cuda:1/cuda:2 (las 3090 Ti) en la config aunque hoy solo
    esté instalada la 5060 Ti: el sistema arranca en modo degradado 1-GPU y
    usará las GPUs nuevas automáticamente cuando aparezcan.
    """
    try:
        import torch
        n_gpus: int = torch.cuda.device_count()
        if n_gpus == 0:
            return "cpu"
        if preferido.startswith("cuda:"):
            indice: int = int(preferido.split(":", 1)[1])
            if indice < n_gpus:
                return preferido
        elif preferido == "cpu":
            return "cpu"
        return "cuda:0"
    except Exception:
        return "cpu"


def indice_de(dispositivo: str) -> int:
    """'cuda:1' -> 1; 'cpu' -> -1. Útil para onnxruntime y nvidia-smi."""
    if dispositivo.startswith("cuda:"):
        return int(dispositivo.split(":", 1)[1])
    return -1


def crear_directorios() -> None:
    """Crea las carpetas de datos si no existen (idempotente)."""
    for ruta in (RUTA_GALERIA, RUTA_SNAPSHOTS, RUTA_DB.parent, RUTA_LOGS):
        ruta.mkdir(parents=True, exist_ok=True)
