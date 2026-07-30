"""
Clasificador FINO de vehículos: carro vs camioneta (pickup/SUV/van) vs camión.

Por qué existe: COCO no separa estos tipos — las pickups suelen caer en
`car` (→ "carro") o en `truck`, y camioneta/camión por heurística de caja
depende de la distancia a la cámara. Este módulo corre `yolo11s-cls`
(ImageNet-1k, ~11 MB) sobre el RECORTE de cada vehículo rastreado y mapea las
clases ImageNet a las 3 propias, con dos protecciones:

  - Umbral de probabilidad ACUMULADA por clase sobre el top-5 (ImageNet
    reparte masa entre variantes: pickup 0.3 + jeep 0.2 => camioneta 0.5).
  - VOTACIÓN por track (moda de los últimos VEHICULO_VOTOS votos): etiqueta
    estable entre frames, sin parpadeo carro↔camioneta.

Trampa documentada (heredada del proyecto perimetral): `passenger_car` y
`freight_car` en ImageNet son VAGONES DE TREN — excluidos explícitamente.

Si el modelo no está disponible, `refinar()` devuelve las detecciones tal
cual (la heurística de caja de mapeo_clases sigue siendo el fallback).
"""

from __future__ import annotations

import time
from collections import Counter, deque
from typing import Deque

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.deteccion.rastreador import DeteccionVig
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

# Clases propias que este módulo refina (moto y bus quedan como estén).
CLASES_OBJETIVO: tuple[str, ...] = ("carro", "camioneta", "camion")

_TTL_TRACK_SEG: float = 120.0     # limpiar estado de tracks sin actividad

# ---------------------------------------------------------------------------
# Mapeo ImageNet -> clase propia. Nombres EXACTOS de ImageNet-1k (ultralytics
# los expone con guion_bajo). Comentado el porqué de los dudosos.
# ---------------------------------------------------------------------------
MAPEO_IMAGENET: dict[str, str] = {
    # ---- camioneta: pickup, SUV, van, furgoneta ----
    "pickup": "camioneta",
    "jeep": "camioneta",              # jeep/landrover = SUV
    "minivan": "camioneta",
    "minibus": "camioneta",           # van de pasajeros
    "police_van": "camioneta",
    "ambulance": "camioneta",         # tipo furgoneta
    # ---- carro: turismos ----
    "sports_car": "carro",
    "convertible": "carro",
    "cab": "carro",                   # taxi
    "limousine": "carro",
    "beach_wagon": "carro",           # station wagon
    "racer": "carro",
    "Model_T": "carro",
    # ---- camión: carga pesada y buses ----
    "trailer_truck": "camion",
    "moving_van": "camion",           # camión de mudanzas
    "fire_engine": "camion",
    "garbage_truck": "camion",
    "tow_truck": "camion",
    "snowplow": "camion",
    "school_bus": "camion",           # sin clase bus propia: porte de camión
    "trolleybus": "camion",
    "recreational_vehicle": "camion", # motorhome
}
# EXCLUIDOS a propósito (ImageNet los confunde con vehículos de calle):
#   passenger_car / freight_car = VAGONES DE TREN; streetcar = tranvía;
#   forklift/half_track/tank = maquinaria/militar; moped/motor_scooter = moto.
EXCLUIDOS_IMAGENET: frozenset[str] = frozenset({
    "passenger_car", "freight_car", "streetcar", "electric_locomotive",
    "bullet_train", "forklift", "half_track", "tank", "moped",
    "motor_scooter", "go-kart", "golfcart", "snowmobile", "lawn_mower",
})


def mapear_imagenet(nombres: list[str], probs: list[float]) -> tuple[str | None, float]:
    """Top-K de ImageNet -> (clase propia, prob. acumulada) o (None, 0).

    Suma la probabilidad de todas las entradas del top-K que mapeen a la
    misma clase propia y devuelve la ganadora si supera VEHICULO_UMBRAL.
    """
    acumulado: dict[str, float] = {}
    for nombre, prob in zip(nombres, probs):
        if nombre in EXCLUIDOS_IMAGENET:
            continue
        clase = MAPEO_IMAGENET.get(nombre)
        if clase is not None:
            acumulado[clase] = acumulado.get(clase, 0.0) + float(prob)
    if not acumulado:
        return None, 0.0
    clase = max(acumulado, key=acumulado.get)
    prob = acumulado[clase]
    if prob < config.VEHICULO_UMBRAL:
        return None, prob
    return clase, prob


class RefinadorVehiculos:
    """Refina carro/camioneta/camión con yolo11s-cls + votación por track."""

    def __init__(self) -> None:
        self.disponible: bool = False
        self._modelo = None
        self.dispositivo: str = config.resolver_dispositivo(config.DEVICE_DETECCION)
        # (camara, track_id) -> votos recientes / última clase aplicada / uso.
        self._votos: dict[tuple[str, int], Deque[str]] = {}
        self._ultimo_uso: dict[tuple[str, int], float] = {}
        if not config.VEHICULOS_FINO_HABILITADO:
            logger.info("clasificador fino de vehículos deshabilitado por config")
            return
        try:
            from ultralytics import YOLO
            if not config.RUTA_VEHICULOS_CLS.exists():
                raise FileNotFoundError(config.RUTA_VEHICULOS_CLS)
            self._modelo = YOLO(str(config.RUTA_VEHICULOS_CLS), task="classify")
            # Warmup (compila kernels y valida el dispositivo).
            dummy = np.zeros((224, 224, 3), dtype=np.uint8)
            self._modelo.predict(dummy, imgsz=224, device=self.dispositivo,
                                 verbose=False)
            self.disponible = True
            logger.info(
                f"clasificador fino de vehículos listo "
                f"({config.RUTA_VEHICULOS_CLS.name} en {self.dispositivo}; "
                f"umbral {config.VEHICULO_UMBRAL}, votos {config.VEHICULO_VOTOS})")
        except Exception as exc:
            logger.warning(
                f"clasificador fino de vehículos NO disponible ({exc}); "
                f"se usa solo la heurística de caja")

    # ------------------------------------------------------------------ API
    def refinar(self, camara: str, secuencia: int, frame: np.ndarray,
                dets: list[DeteccionVig]) -> list[DeteccionVig]:
        """Reetiqueta carro/camioneta/camión sobre las detecciones de UN frame.

        Va DESPUÉS del tracking y ANTES de área/Re-ID/alertas, para que toda
        la cadena (tarjetas, jarvis, cajas) vea la clase fina.
        """
        if not self.disponible or not dets:
            return dets

        # 1) Elegir qué tracks clasificar en este frame (throttle escalonado).
        a_clasificar: list[DeteccionVig] = []
        for d in dets:
            if d.clase not in CLASES_OBJETIVO:
                continue
            if (secuencia + d.track_id) % config.VEHICULO_CADA_N_FRAMES != 0:
                continue
            x1, y1, x2, y2 = d.bbox
            if (x2 - x1) < config.VEHICULO_CROP_MIN_PX or \
               (y2 - y1) < config.VEHICULO_CROP_MIN_PX:
                continue
            a_clasificar.append(d)

        # 2) Clasificar el lote de crops y VOTAR.
        if a_clasificar:
            crops = [self._recortar(frame, d.bbox) for d in a_clasificar]
            resultados = self._clasificar_crops(crops)
            ahora = time.time()
            for d, resultado in zip(a_clasificar, resultados):
                clave = (camara, d.track_id)
                self._ultimo_uso[clave] = ahora
                if resultado is None:
                    continue
                clase_fina, _prob = resultado
                votos = self._votos.setdefault(
                    clave, deque(maxlen=config.VEHICULO_VOTOS))
                votos.append(clase_fina)

        # 3) Aplicar la MODA de los votos de cada track (etiqueta estable).
        salida: list[DeteccionVig] = []
        for d in dets:
            votos = self._votos.get((camara, d.track_id))
            if d.clase in CLASES_OBJETIVO and votos:
                ganadora = Counter(votos).most_common(1)[0][0]
                if ganadora != d.clase:
                    d = DeteccionVig(track_id=d.track_id, bbox=d.bbox,
                                     conf=d.conf, clase=ganadora,
                                     cls_coco=d.cls_coco)
            salida.append(d)

        self._limpiar()
        return salida

    # ------------------------------------------------------------------ interno
    def _clasificar_crops(self, crops: list[np.ndarray]
                          ) -> list[tuple[str, float] | None]:
        """Crops BGR -> [(clase propia, prob) | None] usando el top-5."""
        validos = [c if c.size > 0 else np.zeros((32, 32, 3), np.uint8)
                   for c in crops]
        try:
            resultados = self._modelo.predict(validos, imgsz=224,
                                              device=self.dispositivo,
                                              verbose=False)
        except Exception:
            logger.exception("fallo clasificando vehículos (se ignora el lote)")
            return [None] * len(crops)
        salida: list[tuple[str, float] | None] = []
        for res in resultados:
            probs = res.probs
            if probs is None:
                salida.append(None)
                continue
            indices = probs.top5
            confs = [float(v) for v in probs.top5conf]
            nombres = [str(res.names[i]) for i in indices]
            clase, prob = mapear_imagenet(nombres, confs)
            salida.append((clase, prob) if clase else None)
        return salida

    @staticmethod
    def _recortar(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
        x1, y1, x2, y2 = bbox
        alto, ancho = frame.shape[:2]
        return frame[max(0, y1):min(alto, y2), max(0, x1):min(ancho, x2)]

    def _limpiar(self) -> None:
        ahora = time.time()
        vencidos = [k for k, t in self._ultimo_uso.items()
                    if ahora - t > _TTL_TRACK_SEG]
        for k in vencidos:
            self._ultimo_uso.pop(k, None)
            self._votos.pop(k, None)
