"""
src/analityc/core/estacionamiento_placas.py — lectura de placas (ALPR).

Portado de ARUBA_DEFINITIVO (`alpr_processor.py` + `alpr_database.py`): OCR
sobre el recorte del vehículo rastreado, con base de datos por track.

Cómo funciona, igual que allá:
  * La placa se busca en el TERCIO INFERIOR del recorte (donde va en casi
    todos los vehículos) y se realza el contraste con CLAHE antes del OCR.
  * El OCR (EasyOCR) va restringido a A-Z0-9 y guiones; se acepta la lectura
    si supera `ESTACIONAMIENTO_PLACA_CONF` y parece placa (5-8 caracteres).
  * Todo corre en un HILO propio con cola: la lectura tarda decenas de ms y
    no puede meterse en el bucle de frames.
  * Se guarda en SQLite por `track_id`, y **gana la lectura de mayor
    confianza** (una placa se ve mejor en unos frames que en otros).

## Opcional a propósito

EasyOCR arrastra su propio stack y ~95 MB de modelos. Si no está instalado,
`disponible()` devuelve False y el modo sigue funcionando sin placas — nunca
se cae el servidor por esto. Se enciende con `ESTACIONAMIENTO_PLACAS=1`.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: `server/src/analityc/core/…` -> parents[3] es `server/`.
_RAIZ_SERVIDOR = Path(__file__).resolve().parents[3]

_NO_ALFANUM = re.compile(r'[^A-Z0-9]')


def activado() -> bool:
    return os.getenv('ESTACIONAMIENTO_PLACAS', '0').strip() in ('1', 'true',
                                                                'si', 'yes')


def disponible() -> bool:
    """¿Está EasyOCR instalado? (no lo carga: solo comprueba)."""
    try:
        import importlib.util
        return importlib.util.find_spec('easyocr') is not None
    except Exception:                                  # noqa: BLE001
        return False


def ruta_bd() -> Path:
    propia = (os.getenv('ESTACIONAMIENTO_PLACAS_BD') or '').strip()
    return (Path(propia) if propia
            else _RAIZ_SERVIDOR / 'output' / 'estacionamiento' / 'placas.db')


def carpeta_modelos() -> Path:
    propia = (os.getenv('ESTACIONAMIENTO_PLACAS_MODELOS') or '').strip()
    return Path(propia) if propia else _RAIZ_SERVIDOR / 'models' / 'easyocr'


class BaseDePlacas:
    """SQLite: una fila por track, con la MEJOR lectura vista."""

    def __init__(self, ruta: Optional[Path] = None) -> None:
        self.ruta = Path(ruta or ruta_bd())
        self._lock = threading.Lock()
        self._preparar()

    @contextmanager
    def _conectar(self):
        """Conexión que se CIERRA de verdad.

        GOTCHA: `with sqlite3.connect(...) as con` gestiona la TRANSACCIÓN,
        no la conexión — sin cerrarla se fugan descriptores y en Windows el
        archivo .db queda bloqueado (lo cazó la prueba, que no podía borrar
        su carpeta temporal).
        """
        con = sqlite3.connect(str(self.ruta), timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    def _preparar(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as con:
            con.execute('''
                CREATE TABLE IF NOT EXISTS placas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id TEXT,
                    camara TEXT,
                    placa TEXT,
                    confianza REAL,
                    primera_vez TEXT,
                    ultima_vez TEXT
                )''')
            con.execute('CREATE INDEX IF NOT EXISTS idx_track '
                        'ON placas(track_id)')

    def registrar(self, track_id: str, placa: str, confianza: float,
                  camara: str = '') -> None:
        """Alta o mejora: solo pisa la placa si la confianza es mayor."""
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with self._lock, self._conectar() as con:
            fila = con.execute(
                'SELECT id, confianza FROM placas WHERE track_id = ?',
                (str(track_id),)).fetchone()
            if fila is None:
                con.execute(
                    'INSERT INTO placas (track_id, camara, placa, confianza, '
                    'primera_vez, ultima_vez) VALUES (?, ?, ?, ?, ?, ?)',
                    (str(track_id), camara, placa, float(confianza), ahora,
                     ahora))
                return
            ident, conf_previa = fila
            if float(confianza) > float(conf_previa or 0.0):
                con.execute('UPDATE placas SET placa = ?, confianza = ?, '
                            'ultima_vez = ? WHERE id = ?',
                            (placa, float(confianza), ahora, ident))
            else:
                con.execute('UPDATE placas SET ultima_vez = ? WHERE id = ?',
                            (ahora, ident))

    def de_track(self, track_id: str) -> Optional[Tuple[str, float]]:
        with self._lock, self._conectar() as con:
            fila = con.execute(
                'SELECT placa, confianza FROM placas WHERE track_id = ?',
                (str(track_id),)).fetchone()
        return (fila[0], float(fila[1])) if fila else None

    def del_periodo(self, desde: datetime, hasta: datetime,
                    limite: int = 50) -> List[Tuple[str, str, str]]:
        """[(placa, confianza, última vez)] para la tabla del reporte."""
        with self._lock, self._conectar() as con:
            filas = con.execute(
                'SELECT placa, confianza, ultima_vez FROM placas '
                'WHERE ultima_vez BETWEEN ? AND ? '
                'ORDER BY ultima_vez DESC LIMIT ?',
                (desde.strftime('%Y-%m-%d %H:%M:%S'),
                 hasta.strftime('%Y-%m-%d %H:%M:%S'), int(limite))).fetchall()
        return [(p, f'{float(c):.2f}', u) for p, c, u in filas]


class LectorDePlacas:
    """Cola + hilo de OCR. `encolar()` no bloquea el bucle de frames."""

    _lector_compartido: Any = None
    _lock_lector = threading.Lock()

    def __init__(self, al_detectar: Optional[Callable[[str, str, float], None]] = None
                 ) -> None:
        self.bd = BaseDePlacas()
        self.al_detectar = al_detectar
        self.leidas = 0
        self.descartadas = 0
        self._cola: "queue.Queue[Optional[Tuple[str, Any, str]]]" = queue.Queue(
            maxsize=int(os.getenv('ESTACIONAMIENTO_PLACAS_COLA', '60')))
        self._vivo = True
        self._hilo = threading.Thread(target=self._trabajar, daemon=True,
                                      name='estacionamiento-placas')
        self._hilo.start()

    # ── configuración ────────────────────────────────────────────────
    @staticmethod
    def _umbral() -> float:
        try:
            return float(os.getenv('ESTACIONAMIENTO_PLACA_CONF', '0.6'))
        except ValueError:
            return 0.6

    @staticmethod
    def _lado_minimo() -> int:
        try:
            return int(os.getenv('ESTACIONAMIENTO_PLACA_CROP_MIN', '48'))
        except ValueError:
            return 48

    # ── API ──────────────────────────────────────────────────────────
    def encolar(self, track_id: Any, recorte: Any, camara: str = '') -> bool:
        """Encola un recorte de vehículo. Si la cola está llena, se descarta
        (mejor perder una lectura que atrasar el vídeo)."""
        try:
            self._cola.put_nowait((str(track_id), recorte, camara))
            return True
        except queue.Full:
            self.descartadas += 1
            return False

    def detener(self) -> None:
        self._vivo = False
        try:
            self._cola.put_nowait(None)
        except queue.Full:
            pass

    # ── interno ──────────────────────────────────────────────────────
    @classmethod
    def _lector(cls) -> Any:
        """EasyOCR cargado UNA vez por proceso (pesa; no se duplica)."""
        with cls._lock_lector:
            if cls._lector_compartido is None:
                import easyocr
                carpeta = carpeta_modelos()
                carpeta.mkdir(parents=True, exist_ok=True)
                usar_gpu = os.getenv('ESTACIONAMIENTO_PLACAS_GPU',
                                     '1').strip() not in ('0', 'false')
                logger.info('cargando OCR de placas (EasyOCR, gpu=%s, '
                            'modelos en %s)…', usar_gpu, carpeta)
                cls._lector_compartido = easyocr.Reader(
                    ['en'], gpu=usar_gpu,
                    model_storage_directory=str(carpeta),
                    download_enabled=True, verbose=False)
            return cls._lector_compartido

    @staticmethod
    def parece_placa(texto: str) -> bool:
        """5-8 alfanuméricos: el filtro de ARUBA, que descarta la basura de
        OCR (letreros, sombras, el reloj del DVR)."""
        limpio = _NO_ALFANUM.sub('', (texto or '').upper())
        return 5 <= len(limpio) <= 8

    @staticmethod
    def normalizar(texto: str) -> str:
        return _NO_ALFANUM.sub('', (texto or '').upper())

    def _preparar_recorte(self, recorte: Any) -> Any:
        """Tercio inferior + gris + CLAHE (receta de ARUBA)."""
        import cv2
        alto, ancho = recorte.shape[:2]
        minimo = self._lado_minimo()
        if alto < minimo or ancho < minimo:
            return None
        inferior = recorte[int(alto * 0.6):, :]
        gris = cv2.cvtColor(inferior, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(gris)

    def _trabajar(self) -> None:
        while self._vivo:
            tarea = self._cola.get()
            if tarea is None:
                break
            track_id, recorte, camara = tarea
            try:
                imagen = self._preparar_recorte(recorte)
                if imagen is None:
                    continue
                resultados = self._lector().readtext(
                    imagen,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
                mejor, mejor_conf = None, 0.0
                for _, texto, prob in resultados:
                    limpio = self.normalizar(texto)
                    if (float(prob) > self._umbral()
                            and self.parece_placa(limpio)
                            and float(prob) > mejor_conf):
                        mejor, mejor_conf = limpio, float(prob)
                if mejor:
                    self.bd.registrar(track_id, mejor, mejor_conf, camara)
                    self.leidas += 1
                    if self.al_detectar:
                        self.al_detectar(track_id, mejor, mejor_conf)
            except Exception as exc:                   # noqa: BLE001
                logger.debug('OCR de placa falló (track %s): %s', track_id, exc)
            finally:
                self._cola.task_done()


_lector: Optional[LectorDePlacas] = None
_lock_singleton = threading.Lock()


def lector(al_detectar: Optional[Callable[[str, str, float], None]] = None,
           forzar: bool = False) -> Optional[LectorDePlacas]:
    """El lector del proceso, o None si las placas están apagadas o EasyOCR
    no está instalado. Nunca lanza: sin placas, el modo sigue igual.

    `forzar=True` lo crea aunque la variable de entorno no esté puesta: es
    lo que usa el BOTÓN «Placas» del cliente, que manda la orden por frame.
    El env `ESTACIONAMIENTO_PLACAS=1` sigue valiendo para arrancar ya
    encendido sin tocar el cliente.
    """
    global _lector
    if not (forzar or activado()):
        return None
    if not disponible():
        logger.warning('placas activadas pero EasyOCR no está instalado: '
                       'pip install easyocr (el estacionamiento sigue sin '
                       'placas)')
        return None
    with _lock_singleton:
        if _lector is None:
            try:
                _lector = LectorDePlacas(al_detectar)
            except Exception:                          # noqa: BLE001
                logger.exception('no se pudo iniciar el lector de placas')
                return None
        return _lector


def placas_del_periodo(desde: datetime, hasta: datetime,
                       limite: int = 50) -> List[Tuple[str, str, str]]:
    """Para el reporte. Lee la BD aunque el lector esté apagado."""
    try:
        return BaseDePlacas().del_periodo(desde, hasta, limite)
    except Exception:                                  # noqa: BLE001
        logger.debug('no se pudieron leer las placas del período')
        return []
