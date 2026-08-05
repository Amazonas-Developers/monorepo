"""
src/analityc/core/estacionamiento_registro.py — bitácora CSV del estacionamiento.

Portado de ARUBA_DEFINITIVO (`base_perimeter._log_event` + `_io_worker`), que
es la fuente de la que come el reporte PDF diario. Una fila por EVENTO, un
archivo por día:

    output/estacionamiento/estacionamiento_log_AAAA-MM-DD.csv

Cabecera (idéntica a la de ARUBA para que sus scripts de análisis sigan
sirviendo, más `Camara` y `Local` que ELDE sí conoce):

    Timestamp,Frame,Vehiculos_Area,Cars,Trucks,Buses,Motorcycles,
    Personas_Dentro,Personas_Area,Evento,Tiempo_Acumulado,TrackID,Clase,
    Camara,Local,Placa

Eventos: `Entrada`, `Salida`, `Alerta_Periodica` (permanencia), `Pernocta`.

## Por qué con buffer

Se escribe desde el bucle de frames. ARUBA ya lo resolvió así: las filas se
acumulan en memoria y un hilo las vuelca cada `ELDE_ESTACIONAMIENTO_FLUSH_SEG`
(o cuando se acumulan bastantes). Un fallo de disco jamás sube al frame loop.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: `server/src/analityc/core/…` -> parents[3] es `server/`.
_RAIZ_SERVIDOR = Path(__file__).resolve().parents[3]

CABECERA: List[str] = [
    'Timestamp', 'Frame', 'Vehiculos_Area', 'Cars', 'Trucks', 'Buses',
    'Motorcycles', 'Personas_Dentro', 'Personas_Area', 'Evento',
    'Tiempo_Acumulado', 'TrackID', 'Clase', 'Camara', 'Local', 'Placa',
]

#: Evento del área -> nombre en la bitácora (el que espera el reporte).
EVENTO_A_BITACORA: Dict[str, str] = {
    'llegada': 'Entrada',
    'salida': 'Salida',
    'estacionado': 'Alerta_Periodica',
    'permanencia': 'Alerta_Periodica',
    'pernocta': 'Pernocta',
}

#: Clase propia de ELDE -> clase de la bitácora (nomenclatura COCO de ARUBA,
#: que es la que leen su reporte y sus scripts).
CLASE_A_BITACORA: Dict[str, str] = {
    'persona': 'person',
    'personal_seguridad': 'person',
    'carro': 'car',
    'camioneta': 'truck',
    'camion': 'truck',
    'moto': 'motorcycle',
    'objeto': 'object',
}


def carpeta() -> Path:
    propia = (os.getenv('ELDE_ESTACIONAMIENTO_DIR') or '').strip()
    return Path(propia) if propia else _RAIZ_SERVIDOR / 'output' / 'estacionamiento'


def ruta_del_dia(dia: Optional[datetime] = None) -> Path:
    momento = dia or datetime.now()
    return carpeta() / f"estacionamiento_log_{momento:%Y-%m-%d}.csv"


class RegistroEstacionamiento:
    """Bitácora CSV con volcado amortiguado. Nunca lanza."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: List[List[Any]] = []
        self._ultimo_volcado = time.monotonic()
        self.anotados = 0          # observabilidad: filas aceptadas
        self.volcados = 0          # filas realmente escritas a disco

    # ── configuración ────────────────────────────────────────────────
    @staticmethod
    def _cada_cuanto() -> float:
        try:
            v = float(os.getenv('ELDE_ESTACIONAMIENTO_FLUSH_SEG', '10'))
        except ValueError:
            return 10.0
        return v if v > 0 else 10.0

    @staticmethod
    def _tope_buffer() -> int:
        try:
            return max(1, int(os.getenv('ELDE_ESTACIONAMIENTO_BUFFER', '50')))
        except ValueError:
            return 50

    # ── API ──────────────────────────────────────────────────────────
    def anotar(self, evento: str, clase: str, track_id: Any,
               tiempo_acumulado_s: float = 0.0, camara: str = '',
               local: str = '', placa: str = '',
               conteos: Optional[Dict[str, int]] = None,
               frame: int = 0) -> None:
        """Anota un evento del estacionamiento. Defensivo por contrato."""
        try:
            nombre = EVENTO_A_BITACORA.get(str(evento).strip().lower())
            if not nombre:
                return
            c = conteos or {}
            fila = [
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                int(frame),
                int(c.get('vehiculos_area', 0)),
                int(c.get('cars', 0)),
                int(c.get('trucks', 0)),
                int(c.get('buses', 0)),
                int(c.get('motorcycles', 0)),
                int(c.get('personas_dentro', 0)),
                int(c.get('personas_area', 0)),
                nombre,
                int(round(float(tiempo_acumulado_s or 0.0))),
                str(track_id),
                CLASE_A_BITACORA.get(str(clase).strip().lower(),
                                     str(clase).strip().lower()),
                str(camara or ''),
                str(local or ''),
                str(placa or ''),
            ]
            with self._lock:
                self._buffer.append(fila)
                self.anotados += 1
                toca = (len(self._buffer) >= self._tope_buffer()
                        or time.monotonic() - self._ultimo_volcado
                        >= self._cada_cuanto())
            if toca:
                self.volcar()
        except Exception as exc:                      # noqa: BLE001
            logger.debug('estacionamiento_registro.anotar: %s', exc)

    def volcar(self) -> int:
        """Escribe lo acumulado. Devuelve cuántas filas fueron a disco."""
        with self._lock:
            if not self._buffer:
                self._ultimo_volcado = time.monotonic()
                return 0
            pendientes = self._buffer
            self._buffer = []
            self._ultimo_volcado = time.monotonic()
        try:
            destino = ruta_del_dia()
            destino.parent.mkdir(parents=True, exist_ok=True)
            nuevo = not destino.exists()
            with open(destino, 'a', encoding='utf-8', newline='') as f:
                escritor = csv.writer(f)
                if nuevo:
                    escritor.writerow(CABECERA)
                escritor.writerows(pendientes)
            self.volcados += len(pendientes)
            return len(pendientes)
        except OSError as exc:
            logger.warning('no se pudo volcar la bitácora del '
                           'estacionamiento: %s', exc)
            with self._lock:                 # que lo reintente el siguiente
                self._buffer = pendientes + self._buffer
            return 0


#: Bitácora única del proceso (la comparten el modo y el planificador).
_REGISTRO = RegistroEstacionamiento()


def registro() -> RegistroEstacionamiento:
    return _REGISTRO


def anotar(evento: str, clase: str, track_id: Any, **kw: Any) -> None:
    _REGISTRO.anotar(evento, clase, track_id, **kw)


def volcar() -> int:
    return _REGISTRO.volcar()


# ── Lectura para el reporte ──────────────────────────────────────────

#: Clase de la bitácora -> familia del reporte.
_FAMILIA = {
    'car': 'carro', 'truck': 'camion', 'bus': 'autobus',
    'autobus': 'autobus', 'motorcycle': 'moto',
}
_EVENTO_A_METRICA = {
    'Entrada': 'entrada', 'Salida': 'salida',
    'Alerta_Periodica': 'permanencia', 'Pernocta': 'pernocta',
}


def contar_periodo(desde: datetime, hasta: datetime,
                   camara: Optional[str] = None,
                   local: Optional[str] = None) -> Dict[str, int]:
    """Cuenta los eventos de la bitácora en un rango, como los espera el
    reporte (persona_*, vehiculo_*, carro_*, camion_*, autobus_*, moto_*).

    Lee los archivos de los días que toca el rango. Volcar primero es cosa
    del llamador (el planificador lo hace).
    """
    metricas = {
        f'{fam}_{ev}': 0
        for fam in ('persona', 'vehiculo', 'carro', 'camion', 'autobus',
                    'moto')
        for ev in ('entrada', 'salida', 'permanencia', 'pernocta')
    }
    for archivo in _archivos_del_rango(desde, hasta):
        try:
            with open(archivo, encoding='utf-8', newline='') as f:
                for fila in csv.DictReader(f):
                    _sumar_fila(fila, desde, hasta, camara, local, metricas)
        except (OSError, ValueError) as exc:
            logger.warning('bitácora ilegible %s: %s', archivo, exc)
    return metricas


def _archivos_del_rango(desde: datetime, hasta: datetime) -> List[Path]:
    vistos: List[Path] = []
    dia = datetime(desde.year, desde.month, desde.day)
    while dia <= hasta:
        ruta = ruta_del_dia(dia)
        if ruta.is_file() and ruta not in vistos:
            vistos.append(ruta)
        dia = datetime.fromtimestamp(dia.timestamp() + 86400)
    return vistos


def _sumar_fila(fila: Dict[str, str], desde: datetime, hasta: datetime,
                camara: Optional[str], local: Optional[str],
                metricas: Dict[str, int]) -> None:
    try:
        momento = datetime.strptime(fila.get('Timestamp', ''),
                                    '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return
    if not (desde <= momento <= hasta):
        return
    if camara and (fila.get('Camara') or '') != camara:
        return
    if local and (fila.get('Local') or '') != local:
        return
    evento = _EVENTO_A_METRICA.get(fila.get('Evento', ''))
    if not evento:
        return
    clase = (fila.get('Clase') or '').strip().lower()
    if clase in ('person', 'persona'):
        # Las personas no acumulan permanencia ni pernocta en el reporte.
        if evento in ('entrada', 'salida'):
            metricas[f'persona_{evento}'] += 1
        return
    familia = _FAMILIA.get(clase)
    if familia is None:
        return                       # 'object' y demás no son vehículos
    metricas[f'vehiculo_{evento}'] += 1
    metricas[f'{familia}_{evento}'] += 1
