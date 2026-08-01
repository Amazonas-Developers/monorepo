"""
analytics/heatmap_registro.py — registro GLOBAL de mapas de calor por camara.

## Por que existe (1-ago-2026)

Hasta hoy solo el pipeline de visitantes (PersonAmazonas) acumulaba mapa de
calor: el perimetro llevaba cientos de miles de frames y ni un heatmap. Y el
acumulador vivia dentro del procesador: camara cerrada = acumulado perdido.

Este registro centraliza UN acumulador por camara (device_id), compartido por
todos los pipelines:

  * `obtener(camera_id)` lo crea la primera vez y RESTAURA el estado
    persistido — la camara continua donde iba aunque se haya cerrado o el
    servidor se haya reiniciado.
  * `acumular_desde_metadata(...)` es el enganche generico de app.py: come el
    MISMO metadata que ya viaja al cliente (`detections[].box` del vigilante,
    `tracks[].bbox` de los multicam) y no toca ningun pipeline por dentro.
    Si el metadata trae la clave `heatmap`, el pipeline ya acumulo el frame
    internamente (PersonAmazonas) y aqui NO se estampa de nuevo: seria
    contar cada persona dos veces.
  * `volcar_todos()` se llama al desconectarse un cliente y al apagarse el
    servidor: PNG + estado de TODAS las camaras, sin esperar throttles.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from .heatmap import HeatmapAccumulator, _safe_camera_name

logger = logging.getLogger(__name__)

_CANDADO = threading.Lock()
#: nombre saneado -> {'acumulador': HeatmapAccumulator, 'nombre': str|None}
_POR_CAMARA: Dict[str, Dict[str, Any]] = {}


def obtener(camera_id: Any,
            camera_name: Optional[str] = None) -> HeatmapAccumulator:
    """El acumulador de esa camara (creado y restaurado si es la 1a vez)."""
    clave = _safe_camera_name(camera_id)
    with _CANDADO:
        fila = _POR_CAMARA.get(clave)
        if fila is None:
            acumulador = HeatmapAccumulator()
            acumulador.cargar_estado(clave)
            fila = {'acumulador': acumulador, 'nombre': None}
            _POR_CAMARA[clave] = fila
        if camera_name:
            fila['nombre'] = str(camera_name)
        return fila['acumulador']


def _cajas_de(metadata: Dict[str, Any]) -> List[Any]:
    """Las cajas [x1,y1,x2,y2] que el metadata ya lleva al cliente."""
    cajas: List[Any] = []
    for d in metadata.get('detections') or []:
        if isinstance(d, dict):
            caja = d.get('box') or d.get('bbox')
            if caja is not None:
                cajas.append(caja)
    for t in metadata.get('tracks') or []:
        if isinstance(t, dict):
            caja = t.get('bbox') or t.get('box')
            if caja is not None:
                cajas.append(caja)
    return cajas


def acumular_desde_metadata(camera_id: Any, camera_name: Optional[str],
                            frame, metadata: Any) -> int:
    """Estampa en el heatmap lo que este frame detecto. Devuelve cuantas.

    Defensivo por contrato: corre dentro del frame loop y un fallo aqui no
    puede costar un frame de inferencia.
    """
    try:
        from .config import AnalyticsConfig
        if not AnalyticsConfig.HEATMAP_ENABLED:
            return 0
        if frame is None or getattr(frame, 'size', 0) == 0:
            return 0
        if not isinstance(metadata, dict):
            return 0
        if 'heatmap' in metadata:
            return 0            # el pipeline ya acumulo por dentro
        cajas = _cajas_de(metadata)
        if not cajas:
            return 0
        acumulador = obtener(camera_id, camera_name)
        alto, ancho = frame.shape[:2]
        for caja in cajas:
            acumulador.add_person(caja, ancho, alto)
        acumulador.maybe_save_snapshot(camera_id, background=frame,
                                       camera_name=camera_name)
        return len(cajas)
    except Exception as exc:  # noqa: BLE001
        logger.debug('heatmap global fallo para %s: %s', camera_id, exc)
        return 0


def volcar_todos() -> int:
    """Volcado forzado de todas las camaras (PNG + JSON + estado crudo)."""
    with _CANDADO:
        filas = dict(_POR_CAMARA)
    volcados = 0
    for clave, fila in filas.items():
        try:
            if fila['acumulador'].flush(clave, camera_name=fila['nombre']):
                volcados += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug('flush de heatmap %s fallo: %s', clave, exc)
    if volcados:
        logger.info('heatmaps volcados a disco: %d camara(s)', volcados)
    return volcados
