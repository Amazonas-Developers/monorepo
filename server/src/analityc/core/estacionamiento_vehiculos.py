"""
src/analityc/core/estacionamiento_vehiculos.py — clase fina del vehículo con
el modelo ENTRENADO `aruba.pt`.

## Qué problema resuelve (medido el 3-ago-2026)

La clase carro/camioneta/camión salía de una heurística de caja (área y
aspecto sobre las clases COCO `car`/`truck`). Se midió sobre 208 fotos
reales: **techo del 52 % de acuerdo**, con 0/19 aciertos en CAMIÓN — el
detector genérico ve `car` o nada. La conclusión de aquella medición fue
literal: «el camino real es un clasificador entrenado con recortes de ESTAS
cámaras».

`models/vehiculos/aruba.pt` es ese modelo: YOLO entrenado 100 épocas sobre
UA-DETRAC (10 K) con **mAP50 0,983 · precisión 0,973 · recall 0,951** y las
clases `bus`, `car`, `truck`, `van`.

## ⚠ APAGADO por defecto: medido el 5-ago-2026 en ESTAS cámaras

Aquellas métricas son sobre la validación de UA-DETRAC (cámaras de tráfico
diurnas). Sobre 69 fotos reales de estas cámaras (capturas de 960x600 de la
ventana del DVR, ángulo alto, muchas nocturnas en IR):

    encuentra el vehículo   producción 57/69 (83 %) · aruba.pt 35/69 (51 %)
    acuerdo de clase        aruba.pt 29 % en su MEJOR ajuste (640 / 0,05)

Y cuando acierta a disparar, se equivoca en casos claros: un sedán verde
etiquetado «camioneta» con 0,63 de confianza. Es una brecha de dominio, no
un fallo del entrenamiento.

Por eso el refinador queda listo pero **inactivo**; se enciende con
`ESTACIONAMIENTO_MODELO_VEHICULOS_ACTIVO=1` el día que las cámaras se
parezcan a su dominio. Como solo REETIQUETA cajas que el detector principal
ya encontró, encenderlo nunca pierde vehículos: como mucho cambia su clase.

## Cómo se enchufa (sin tocar el vigilante)

`VigilanteWS._procesar` ya hace, después del tracking y ANTES de área/Re-ID:

    dets = self.vehiculos.refinar(camara, seq, img, dets)

así que el modo Estacionamiento solo cambia ese colaborador por este. Toda
la cadena (tarjetas, bitácora, WhatsApp, reporte) ve la clase fina.

## Por qué por FRAME y no por recorte

`aruba.pt` es un DETECTOR: una pasada por frame reetiqueta todos los
vehículos a la vez, en vez de N pasadas de clasificación (una por recorte)
como hacía el refinador de ImageNet. Sale más barato y más preciso.

Las cajas del detector fino se casan con las rastreadas por IoU, y la clase
se decide por VOTACIÓN por track: la etiqueta no parpadea entre frames.
"""

from __future__ import annotations

import logging
import os
from collections import Counter, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.deteccion.rastreador import DeteccionVig

logger = logging.getLogger(__name__)

#: `server/src/analityc/core/…` -> parents[3] es `server/`.
_RAIZ_SERVIDOR = Path(__file__).resolve().parents[3]

#: Clases de `aruba.pt` -> clases propias de ELDE.
#: `bus` cae en «camion» porque ELDE no tiene clase autobús — es la MISMA
#: aproximación que ya hacía el mapeo COCO (bus=5 -> camion), así que no
#: cambia nada respecto a hoy. Si algún día hace falta separarlo, se añade
#: «autobus» a CLASES_PROPIAS y se cambia esta línea.
ARUBA_A_PROPIA: Dict[str, str] = {
    'car': 'carro',
    'van': 'camioneta',
    'truck': 'camion',
    'bus': 'camion',
}

#: Solo se reetiquetan vehículos: una persona jamás se toca.
CLASES_REFINABLES = frozenset({'carro', 'camioneta', 'camion'})


def ruta_modelo() -> Path:
    propia = (os.getenv('ESTACIONAMIENTO_MODELO_VEHICULOS') or '').strip()
    return (Path(propia) if propia
            else _RAIZ_SERVIDOR / 'models' / 'vehiculos' / 'aruba.pt')


def _entero(nombre: str, defecto: int) -> int:
    try:
        return int((os.getenv(nombre) or '').strip() or defecto)
    except ValueError:
        return defecto


def _flotante(nombre: str, defecto: float) -> float:
    try:
        return float((os.getenv(nombre) or '').strip() or defecto)
    except ValueError:
        return defecto


def iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    """Intersección sobre unión de dos cajas xyxy."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class RefinadorAruba:
    """Reetiqueta carro/camioneta/camión con `aruba.pt` + votación.

    Mismo contrato que `RefinadorVehiculos` del vigilante (`refinar`), para
    poder sustituirlo sin tocar `VigilanteWS`.
    """

    def __init__(self, ruta: Optional[Path] = None) -> None:
        self.ruta = Path(ruta or ruta_modelo())
        self.disponible = False
        self._modelo: Any = None
        self.dispositivo = config.resolver_dispositivo(config.DEVICE_DETECCION)
        self.imgsz = _entero('ESTACIONAMIENTO_MODELO_IMGSZ', 640)
        self.conf = _flotante('ESTACIONAMIENTO_MODELO_CONF', 0.30)
        self.iou_minimo = _flotante('ESTACIONAMIENTO_MODELO_IOU', 0.45)
        self.cada_n = max(1, _entero('ESTACIONAMIENTO_MODELO_CADA_N', 3))
        self.votos_max = max(1, _entero('ESTACIONAMIENTO_MODELO_VOTOS', 5))
        # (camara, track_id) -> votos recientes
        self._votos: Dict[Tuple[str, int], Deque[str]] = {}
        self.reetiquetados = 0
        self._cargar()

    def _cargar(self) -> None:
        # APAGADO por defecto tras MEDIRLO (5-ago-2026, ver cabecera del
        # módulo y H-29): en las cámaras de HOY empeora. Se enciende con
        # ESTACIONAMIENTO_MODELO_VEHICULOS_ACTIVO=1 cuando las cámaras se
        # parezcan a aquello con lo que se entrenó (calle/tráfico diurno).
        if os.getenv('ESTACIONAMIENTO_MODELO_VEHICULOS_ACTIVO',
                     '0').strip() not in ('1', 'true', 'si', 'yes'):
            logger.info('modelo fino de vehículos (%s) disponible pero '
                        'APAGADO: enciéndelo con '
                        'ESTACIONAMIENTO_MODELO_VEHICULOS_ACTIVO=1',
                        self.ruta.name)
            return
        if not self.ruta.exists():
            logger.warning('modelo fino de vehículos no encontrado en %s; '
                           'el estacionamiento usará la heurística de caja',
                           self.ruta)
            return
        try:
            from ultralytics import YOLO
            modelo = YOLO(str(self.ruta), task='detect')
            # Warmup: valida el dispositivo y compila kernels (si el modelo
            # está roto, se sabe AQUÍ y no en el primer frame real).
            modelo.predict(np.zeros((self.imgsz, self.imgsz, 3), np.uint8),
                           imgsz=self.imgsz, device=self.dispositivo,
                           verbose=False)
            self._modelo = modelo
            self.disponible = True
            logger.info('modelo fino de vehículos listo: %s (%s) en %s; '
                        'clases=%s', self.ruta.name, 'aruba', self.dispositivo,
                        list(getattr(modelo, 'names', {}).values()))
        except Exception as exc:                       # noqa: BLE001
            logger.warning('no se pudo cargar el modelo fino de vehículos '
                           '(%s): %s — se usará la heurística de caja',
                           self.ruta.name, exc)

    # ── API (contrato de RefinadorVehiculos) ─────────────────────────
    def refinar(self, camara: str, secuencia: int, frame: np.ndarray,
                dets: List[DeteccionVig]) -> List[DeteccionVig]:
        """Reetiqueta los vehículos de UN frame. Nunca lanza: ante un fallo
        devuelve las detecciones tal cual (la heurística sigue valiendo)."""
        if not self.disponible or not dets:
            return dets
        vehiculos = [d for d in dets if d.clase in CLASES_REFINABLES]
        if not vehiculos:
            return dets
        try:
            # Throttle: una pasada cada N frames; entre medias mandan los
            # votos ya acumulados (por eso se aplican SIEMPRE, más abajo).
            if secuencia % self.cada_n == 0:
                self._votar(camara, frame, vehiculos)
            self._aplicar(camara, vehiculos)
        except Exception:                              # noqa: BLE001
            logger.debug('refinado de vehículos falló', exc_info=True)
        return dets

    # ── interno ──────────────────────────────────────────────────────
    def _votar(self, camara: str, frame: np.ndarray,
               vehiculos: List[DeteccionVig]) -> None:
        """Una pasada de `aruba.pt` sobre el frame; cada caja fina vota en el
        track rastreado con el que mejor solapa."""
        resultado = self._modelo.predict(
            frame, imgsz=self.imgsz, conf=self.conf,
            device=self.dispositivo, verbose=False)[0]
        cajas = getattr(resultado, 'boxes', None)
        if cajas is None or len(cajas) == 0:
            return
        nombres = getattr(self._modelo, 'names', {})
        finas: List[Tuple[Tuple[int, int, int, int], str, float]] = []
        for caja, cls, conf in zip(cajas.xyxy.cpu().numpy(),
                                   cajas.cls.cpu().numpy().astype(int),
                                   cajas.conf.cpu().numpy()):
            propia = ARUBA_A_PROPIA.get(str(nombres.get(int(cls), '')).lower())
            if propia:
                finas.append((tuple(int(v) for v in caja), propia,
                              float(conf)))
        if not finas:
            return
        for d in vehiculos:
            mejor, mejor_iou = None, self.iou_minimo
            for caja, propia, _conf in finas:
                solape = iou(d.bbox, caja)
                if solape >= mejor_iou:
                    mejor, mejor_iou = propia, solape
            if mejor is None:
                continue
            clave = (camara, d.track_id)
            votos = self._votos.get(clave)
            if votos is None:
                votos = deque(maxlen=self.votos_max)
                self._votos[clave] = votos
            votos.append(mejor)

    def _aplicar(self, camara: str, vehiculos: List[DeteccionVig]) -> None:
        """La clase más votada del track manda (etiqueta estable)."""
        for d in vehiculos:
            votos = self._votos.get((camara, d.track_id))
            if not votos:
                continue
            ganadora = Counter(votos).most_common(1)[0][0]
            if ganadora != d.clase:
                self.reetiquetados += 1
                logger.debug("track %s de '%s': %s -> %s (modelo fino)",
                             d.track_id, camara, d.clase, ganadora)
                d.clase = ganadora

    def olvidar_camara(self, camara: str) -> None:
        for clave in [k for k in self._votos if k[0] == camara]:
            self._votos.pop(clave, None)
