"""
telemetria_demografica.py - Instrumentacion del modulo demografico (Hito 2).

Responde con NUMEROS a la pregunta "por que el 98 % de las capturas sale con
genero nulo". Registra, por cada track que pasa por el estimador, un unico
veredicto final con su motivo, de forma que la suma de motivos explique el
100 % de los tracks vistos.

Diseno:
  * NO altera el comportamiento del pipeline. Solo observa y registra.
  * Thread-safe: el servidor procesa varias camaras en un ThreadPoolExecutor.
  * Escritura JSONL con buffer, para no castigar el disco frame a frame.
  * Si algo falla aqui, se traga la excepcion: la telemetria JAMAS puede
    tumbar la inferencia.

Los motivos son excluyentes: cada track recibe exactamente uno.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from collections import Counter
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MotivoFinal:
    """Veredicto de un track al salir del modulo demografico.

    Son excluyentes y cubren todos los caminos posibles, para que la suma
    de contadores sea siempre el total de tracks observados.
    """

    # ── Exitos ──
    CLASIFICADO: str = "clasificado"
    """Se comprometio genero/edad con muestras propias (rama facial)."""
    CLASIFICADO_SOLO_CUERPO: str = "clasificado_solo_cuerpo"
    """Se comprometio usando SOLO muestras corporales (sin rostro)."""
    HEREDADO_REID: str = "heredado_reid"
    """No se estimo: se reuso la demografia que el Re-ID ya tenia de una
    visita anterior de la misma persona."""

    # ── Fracasos ──
    SIN_ROSTRO: str = "sin_rostro"
    """Nunca se detecto un rostro y no hubo via corporal disponible."""
    ROSTRO_MUY_PEQUENO: str = "rostro_muy_pequeno"
    """Hubo rostro, pero por debajo del minimo util (bracket < 25 px)."""
    CALIDAD_INSUFICIENTE: str = "calidad_insuficiente"
    """Hubo rostro de tamano suficiente, pero lo rechazaron los gates de
    pose, nitidez, contraste, simetria o el acuerdo del ensemble/TTA."""
    MUESTRAS_INSUFICIENTES: str = "muestras_insuficientes"
    """Se acumularon muestras validas pero nunca alcanzaron el umbral de
    commit (confianza, margen o acuerdo)."""
    TRACK_NO_CERRADO: str = "track_no_cerrado"
    """El track desaparecio antes de que hubiera veredicto (persona que
    cruza rapido o tracking que se rompe)."""
    EXCEPCION_ESTIMADOR: str = "excepcion_estimador"
    """El estimador lanzo una excepcion en algun punto de este track."""
    SIN_MODELO: str = "sin_modelo_cargado"
    """No habia NINGUN modelo de genero/edad cargado, asi que no se intento
    nada. Es un fallo de despliegue, no de la escena: sin este motivo el
    caso pasaba totalmente inadvertido (el estimador salia en su primera
    linea sin dejar rastro)."""

    TODOS: tuple[str, ...] = (
        CLASIFICADO, CLASIFICADO_SOLO_CUERPO, HEREDADO_REID,
        SIN_ROSTRO, ROSTRO_MUY_PEQUENO, CALIDAD_INSUFICIENTE,
        MUESTRAS_INSUFICIENTES, TRACK_NO_CERRADO, EXCEPCION_ESTIMADOR,
        SIN_MODELO,
    )

    EXITOSOS: frozenset[str] = frozenset(
        {CLASIFICADO, CLASIFICADO_SOLO_CUERPO, HEREDADO_REID})


class TelemetriaDemografica:
    """Acumula contadores por motivo y escribe un JSONL por track.

    Se comparte entre camaras (una sola instancia por proceso) para que el
    resumen sea del sistema completo. Ver `obtener_telemetria()`.
    """

    def __init__(self, ruta_jsonl: str = "output/telemetria_demografica.jsonl",
                 volcar_cada: int = 20, activa: bool = True) -> None:
        self._ruta: str = ruta_jsonl
        self._volcar_cada: int = max(1, int(volcar_cada))
        self._activa: bool = bool(activa)
        self._lock = threading.RLock()

        self._motivos: Counter = Counter()
        self._por_camara: Dict[str, Counter] = {}
        self._excepciones: Counter = Counter()
        self._buffer: list[str] = []
        self._inicio: str = datetime.datetime.now().isoformat(timespec="seconds")
        self._total_tracks: int = 0
        # Estadisticas del tamano de rostro observado (informativo).
        self._anchos_rostro: list[float] = []
        # Tracks ya registrados, para no contar dos veces el mismo.
        self._vistos: set[tuple[str, int]] = set()

    # ── Registro ────────────────────────────────────────────────────────

    def registrar_track(self, track_id: int, motivo: str,
                        camara: Any = None,
                        detalles: Optional[Dict[str, Any]] = None) -> None:
        """Registra el veredicto FINAL de un track. Idempotente por
        (camara, track_id): una segunda llamada para el mismo track se
        ignora, de modo que los contadores no se inflen."""
        if not self._activa:
            return
        try:
            cam = str(camara if camara is not None else "?")
            clave = (cam, int(track_id))
            with self._lock:
                if clave in self._vistos:
                    return
                self._vistos.add(clave)
                # Acotar memoria en sesiones largas.
                if len(self._vistos) > 100_000:
                    self._vistos = set(list(self._vistos)[-50_000:])

                self._motivos[motivo] += 1
                self._total_tracks += 1
                self._por_camara.setdefault(cam, Counter())[motivo] += 1

                registro: Dict[str, Any] = {
                    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                    "track_id": int(track_id),
                    "camara": cam,
                    "motivo": motivo,
                    "exitoso": motivo in MotivoFinal.EXITOSOS,
                }
                if detalles:
                    registro.update(detalles)
                    ancho = detalles.get("mejor_ancho_rostro_px")
                    if isinstance(ancho, (int, float)) and ancho > 0:
                        self._anchos_rostro.append(float(ancho))
                        if len(self._anchos_rostro) > 50_000:
                            del self._anchos_rostro[:25_000]

                self._buffer.append(json.dumps(registro, ensure_ascii=False))
                if len(self._buffer) >= self._volcar_cada:
                    self._volcar_sin_lock()
        except Exception as exc:  # noqa: BLE001
            logger.debug("telemetria: fallo al registrar track: %s", exc)

    def registrar_excepcion(self, punto: str, excepcion: BaseException) -> None:
        """Registra una excepcion capturada en la ruta demografica.

        Existe porque el modulo tiene bloques `except` que se tragan el
        error sin dejar rastro: sin esto no se puede saber si el estimador
        esta fallando en silencio (hipotesis H4 de la auditoria).
        """
        if not self._activa:
            return
        try:
            with self._lock:
                self._excepciones[
                    f"{punto}:{type(excepcion).__name__}"] += 1
            logger.warning("Excepcion en la ruta demografica (%s): %s: %s",
                           punto, type(excepcion).__name__, excepcion)
        except Exception:  # noqa: BLE001
            pass

    # ── Volcado ─────────────────────────────────────────────────────────

    def _volcar_sin_lock(self) -> None:
        """Escribe el buffer al JSONL. El llamador ya tiene el lock."""
        if not self._buffer:
            return
        try:
            carpeta = os.path.dirname(self._ruta)
            if carpeta:
                os.makedirs(carpeta, exist_ok=True)
            with open(self._ruta, "a", encoding="utf-8") as fichero:
                fichero.write("\n".join(self._buffer) + "\n")
            self._buffer.clear()
        except Exception as exc:  # noqa: BLE001
            logger.debug("telemetria: no se pudo escribir el JSONL: %s", exc)
            self._buffer.clear()   # no acumular sin limite si el disco falla

    def volcar(self) -> None:
        """Fuerza la escritura del buffer pendiente."""
        with self._lock:
            self._volcar_sin_lock()

    # ── Consulta ────────────────────────────────────────────────────────

    def resumen(self) -> Dict[str, Any]:
        """Resumen acumulado desde el arranque del proceso."""
        with self._lock:
            total = max(1, self._total_tracks)
            motivos = {
                motivo: {
                    "tracks": self._motivos.get(motivo, 0),
                    "porcentaje": round(
                        100.0 * self._motivos.get(motivo, 0) / total, 1),
                }
                for motivo in MotivoFinal.TODOS
                if self._motivos.get(motivo, 0) > 0
            }
            exitosos = sum(self._motivos.get(m, 0)
                           for m in MotivoFinal.EXITOSOS)
            anchos = sorted(self._anchos_rostro)
            estad_rostro: Dict[str, Any] = {}
            if anchos:
                def _pct(p: float) -> float:
                    return round(anchos[min(len(anchos) - 1,
                                            int(len(anchos) * p))], 1)
                estad_rostro = {
                    "muestras": len(anchos),
                    "min_px": round(anchos[0], 1),
                    "p25_px": _pct(0.25),
                    "mediana_px": _pct(0.50),
                    "p75_px": _pct(0.75),
                    "max_px": round(anchos[-1], 1),
                }
            return {
                "inicio": self._inicio,
                "total_tracks": self._total_tracks,
                "tracks_con_demografia": exitosos,
                "tasa_exito_pct": round(
                    100.0 * exitosos / total, 1),
                "motivos": motivos,
                "suma_motivos": sum(self._motivos.values()),
                "cuadra": sum(self._motivos.values()) == self._total_tracks,
                "excepciones": dict(self._excepciones),
                "ancho_rostro_observado": estad_rostro,
                "por_camara": {
                    cam: dict(cnt) for cam, cnt in self._por_camara.items()
                },
            }

    def reiniciar(self) -> None:
        """Vacia los contadores (no borra el JSONL ya escrito)."""
        with self._lock:
            self._volcar_sin_lock()
            self._motivos.clear()
            self._por_camara.clear()
            self._excepciones.clear()
            self._anchos_rostro.clear()
            self._vistos.clear()
            self._total_tracks = 0
            self._inicio = datetime.datetime.now().isoformat(timespec="seconds")


# ── Instancia compartida por proceso ────────────────────────────────────

_telemetria: Optional[TelemetriaDemografica] = None
_lock_creacion = threading.Lock()


def obtener_telemetria() -> TelemetriaDemografica:
    """Devuelve la instancia unica de telemetria (la crea al primer uso)."""
    global _telemetria
    if _telemetria is None:
        with _lock_creacion:
            if _telemetria is None:
                try:
                    from .config import AnalyticsConfig as _cfg
                    ruta = getattr(_cfg, "TELEMETRIA_DEMO_JSONL",
                                   "output/telemetria_demografica.jsonl")
                    activa = bool(getattr(_cfg, "TELEMETRIA_DEMO_ENABLED",
                                          True))
                except Exception:  # noqa: BLE001
                    ruta, activa = "output/telemetria_demografica.jsonl", True
                _telemetria = TelemetriaDemografica(ruta_jsonl=ruta,
                                                    activa=activa)
    return _telemetria
