"""
Payloads por evento. Un modelo por cada forma de mensaje que existe hoy.

Todos los campos salen del codigo real (`render_box.py` los emite,
`app.py` los lee con `data.get()`), no de un diseno ideal: el objetivo del
HITO 3 es formalizar lo que YA circula, no cambiarlo. Lo que se anade es la
validacion, que hoy no hay ninguna.

Los campos opcionales lo son porque distintos clientes mandan distintos
subconjuntos: el de tienda no manda `pay_roi`, el de autolavado no manda
`heatmap_activate`. Marcarlos obligatorios romperia clientes vivos.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Un poligono es una lista de puntos [x, y] normalizados o en pixeles.
Punto = List[float]
Poligono = List[Punto]


class CameraAngle(str, Enum):
    """Perfil de angulo de la camara. Ajusta el filtro de aspecto del
    detector: en cenital una persona se ve mucho mas ancha que alta.

    `auto` deja que el servidor lo deduzca solo, y es el valor por defecto
    real (`person_amazona_inference.py:860`). Una version anterior de este
    contrato no lo contemplaba y habria **rechazado payloads reales y
    funcionales**: lo detecto la captura de una sesion de verdad, no la
    lectura del codigo emisor."""
    AUTO = "auto"
    FRONTAL = "frontal"
    LATERAL = "lateral"
    CENITAL = "cenital"


def _valida_poligono(v: Optional[Poligono], nombre: str) -> Optional[Poligono]:
    if v is None:
        return v
    if not v:
        # Lista VACIA = "no hay poligono", que no es lo mismo que un poligono
        # mal formado. Es lo que devuelve `get_coordinates()` cuando la zona no
        # tiene puntos, y viaja acompañada de su `*_activate: False`.
        #
        # Rechazarla habria tumbado a los cuatro clientes al pasar la
        # validacion a `estricto`: basta con no tener ROI dibujado. Lo encontro
        # el modo `observar` del HITO 8 con trafico real, que es justo para lo
        # que existe.
        return v
    if len(v) < 3:
        raise ValueError(f"{nombre}: un poligono necesita 3 puntos o mas, "
                         f"llegaron {len(v)}")
    for p in v:
        if len(p) != 2:
            raise ValueError(f"{nombre}: cada punto es [x, y], llego {p!r}")
    return v


class FrameInference(BaseModel):
    """Cliente -> servidor. Un frame a analizar y la configuracion activa.

    Corresponde al `data` de hoy (`render_box.py:645-669`)."""

    model_config = ConfigDict(extra='forbid')  # endurecido el 31-jul; ver nota al pie

    image: bytes = Field(..., description="JPEG del frame.")

    # ── Region de interes principal ──
    roi_activate: bool = False
    roi_coordinates: Optional[Poligono] = None

    # ── Camara ──
    # Duplica el `device_id` del envelope DENTRO del payload. No es un
    # descuido: el formato en transicion lo lleva ahi (`data.camera_id`) y el
    # servidor lo lee de ahi; la capa de compatibilidad lo promociona al
    # envelope SIN sacarlo del payload (sacarlo mutaria el request que el
    # servidor procesa despues). Sin este campo, `extra='forbid'` rechazaria
    # TODOS los frames.
    camera_id: Optional[str] = Field(default=None, max_length=96)
    camera_angle: CameraAngle = CameraAngle.AUTO
    camera_name: Optional[str] = Field(default=None, max_length=48)
    # Cabecera del frame que anade el cliente de perimetrales: timestamp de
    # captura, tamano y formato. Visto en la captura real, no en el codigo de
    # tienda. Se deja abierto porque cada cliente mete lo suyo.
    header: Optional[Dict[str, Any]] = None

    # ── Analitica ──
    heatmap_activate: bool = False
    track_classes: Optional[List[int]] = None
    draw_server: bool = True
    enable_vlm: bool = False
    # Notificacion por WhatsApp de las alertas (clientes perimetrales).
    enviar_whatsapp: bool = False
    # Establecimiento (local) de ESTA camara (3-ago-2026): lo elige el
    # operador en el select "Local" del recuadro. El servidor lo usa como
    # encabezado del mensaje de WhatsApp en lugar del "VIGILANTE" generico.
    establecimiento: Optional[str] = Field(default=None, max_length=120)

    # ── Zonas de Hummus (pedido / entrega de bandeja) ──
    order_zone_activate: bool = False
    order_zone_coordinates: Optional[Poligono] = None
    delivery_zone_activate: bool = False
    delivery_zone_coordinates: Optional[Poligono] = None

    # ── Zonas de autolavado (pago / retiro) ──
    pay_roi_activate: bool = False
    pay_roi: Optional[Poligono] = None
    withdraw_roi_activate: bool = False
    withdraw_roi: Optional[Poligono] = None

    # ── Puerta (perimetrales) ──
    door_roi_activate: bool = False
    door_roi_coordinates: Optional[Poligono] = None
    door_direction_activate: bool = False
    door_direction: Optional[str] = None

    @field_validator('image')
    @classmethod
    def _parece_jpeg(cls, v: bytes) -> bytes:
        """Un frame vacio o que no es JPEG hace fallar al detector mas
        adelante, con un error mucho menos claro que este."""
        if not v:
            raise ValueError("image: llego vacio")
        if not v.startswith(b'\xff\xd8'):
            raise ValueError("image: no empieza por la cabecera JPEG (FF D8)")
        return v

    @field_validator('roi_coordinates')
    @classmethod
    def _roi_ok(cls, v):
        return _valida_poligono(v, 'roi_coordinates')

    @field_validator('order_zone_coordinates')
    @classmethod
    def _order_ok(cls, v):
        return _valida_poligono(v, 'order_zone_coordinates')

    @field_validator('delivery_zone_coordinates')
    @classmethod
    def _deliv_ok(cls, v):
        return _valida_poligono(v, 'delivery_zone_coordinates')


class Deteccion(BaseModel):
    """Una caja detectada, con lo que el pipeline haya podido inferir."""
    model_config = ConfigDict(extra='forbid')

    track_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    confianza: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    clase: Optional[str] = None
    gender: Optional[str] = None
    age_range: Optional[str] = None


class FrameResult(BaseModel):
    """Servidor -> cliente. Resultado del analisis de un frame.

    Hoy el servidor reutiliza el mismo sobre y sustituye `data` por el
    resultado (`app.py:785`), asi que cliente y servidor comparten forma
    exterior pero no interior."""

    model_config = ConfigDict(extra='forbid')

    status: Optional[str] = None           # "ok" o "success" segun pipeline
    message: Optional[str] = None
    processing_time: Optional[float] = Field(default=None, ge=0.0)

    # Eco del device_id, para que el cliente sepa a que camara corresponde
    # la respuesta cuando tiene varias en vuelo.
    camera_id: Optional[str] = None

    # El frame anotado viaja con DOS nombres segun el pipeline: `image` en
    # los de tienda y `processed_image` en los perimetrales. Ambos se
    # modelan porque ambos circulan; unificarlos es trabajo del HITO 8, no
    # de este hito, cuyo objetivo es formalizar lo que hay.
    image: Optional[bytes] = None
    processed_image: Optional[bytes] = None

    # Igual con las detecciones: `tracks` en tienda, `metadata.detections`
    # en perimetrales.
    tracks: Optional[List[Deteccion]] = None
    metadata: Optional[Dict[str, Any]] = None

    detections_in_roi: Optional[int] = None
    valid_tracks_in_roi: Optional[int] = None


class ConnectionInit(BaseModel):
    """Servidor -> cliente, al aceptar la conexion (`app.py:647`)."""
    model_config = ConfigDict(extra='forbid')

    id_connection: int
    roi: bool = False


class Heartbeat(BaseModel):
    """Latido en cualquier direccion. Hoy el servidor manda
    `{"status": "ping"}` cada `WEBSOCKET_TIMEOUT` sin trafico."""
    model_config = ConfigDict(extra='forbid')

    status: str = "ping"


class ErrorEvento(BaseModel):
    """Error estructurado (`app.py:_send_error`)."""
    model_config = ConfigDict(extra='forbid')

    status: str = "error"
    message: str
    codigo: Optional[str] = None


# Que modelo valida cada `event_type`.
POR_EVENTO: Dict[str, Any] = {
    "frame.inference": FrameInference,
    "frame.result": FrameResult,
    "connection.init": ConnectionInit,
    "heartbeat": Heartbeat,
    "error": ErrorEvento,
}


# NOTA sobre `extra='forbid'`
# ---------------------------
# Hasta el 31-jul-2026 los payloads permitian claves desconocidas
# (`extra='allow'`), a proposito y solo durante la migracion: endurecer antes
# habria roto a los clientes sin actualizar. Con los cuatro clientes migrados
# y el envio compartido en el nucleo (loop_show_result), la forma del payload
# es una sola y conocida, asi que una clave desconocida ya no es "un cliente
# viejo": es un error o una deriva del contrato, y debe sonar. Si un campo
# nuevo hace falta, se anade AQUI primero y sube `event_version`.
#
# Cuando los 4 clientes esten migrados (fin del HITO 7), estos modelos deben
# pasar a `extra='forbid'` y las claves sobrantes que aparezcan en el log de
# compatibilidad hay que resolverlas una a una.
