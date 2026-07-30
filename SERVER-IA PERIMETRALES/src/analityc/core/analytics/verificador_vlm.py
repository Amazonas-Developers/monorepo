"""
verificador_vlm.py - Segunda opinion opcional con Qwen2.5-VL (Hito 6).

Cuando la agregacion por track cierra con poca confianza, se puede pedir
una segunda lectura a un modelo vision-lenguaje. Es CARO (segundos por
consulta), asi que:

  * Corre en un hilo aparte con una cola ACOTADA. El pipeline de video
    nunca espera por el: encolar es no bloqueante y, si la cola esta
    llena, la peticion se descarta y se cuenta. Nunca se acumula trabajo.
  * Solo se encolan los tracks por debajo del umbral de confianza. Los
    que ya son firmes no se tocan: gastar el VLM ahi seria tirar GPU.
  * Esta DESACTIVADO por defecto. Si no se activa, el modelo ni siquiera
    se carga (el router lo carga de forma perezosa), asi que no ocupa
    VRAM ni cambia el comportamiento del pipeline.
  * Se pide una categoria GRUESA de edad, no un numero: a esta calidad de
    imagen pedir "35 anos" seria inventar precision.

El resultado NO pisa al del estimador: se guarda aparte para que quien
consuma decida. Un VLM tambien se equivoca, y mezclarlo en silencio con
la votacion haria imposible saber de donde vino cada dato.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Categorias GRUESAS. Deliberadamente no se piden anos exactos.
_CATEGORIAS = ("nino", "adolescente", "adulto", "adulto mayor")
_GENEROS = {"hombre": "Hombre", "masculino": "Hombre", "male": "Hombre",
            "mujer": "Mujer", "femenino": "Mujer", "female": "Mujer"}

_PREGUNTA = (
    "Observa a la persona de la imagen, tomada por una camara de "
    "seguridad. Responde UNICAMENTE con un objeto JSON valido, sin texto "
    "adicional ni explicaciones, con esta forma exacta:\n"
    '{"genero": "hombre|mujer|desconocido", '
    '"categoria_edad": "nino|adolescente|adulto|adulto mayor|desconocido", '
    '"seguridad": "alta|media|baja"}\n'
    "Si la persona esta de espaldas o no se distingue, responde "
    '"desconocido". No inventes.'
)


@dataclass
class ResultadoVLM:
    """Segunda opinion del VLM sobre un track."""

    track_id: int
    genero: Optional[str] = None          # "Hombre" | "Mujer" | None
    categoria_edad: Optional[str] = None
    seguridad: str = "baja"               # alta | media | baja
    crudo: str = ""                       # respuesta literal, para depurar
    timestamp: float = field(default_factory=time.time)

    @property
    def es_util(self) -> bool:
        return self.genero in ("Hombre", "Mujer")


def _parsear(respuesta: str, track_id: int) -> Optional[ResultadoVLM]:
    """Extrae el JSON de la respuesta del modelo.

    Los VLM suelen envolver el JSON en texto o en un bloque markdown, asi
    que se busca el primer objeto `{...}` en lugar de exigir una respuesta
    limpia. Si no hay nada parseable se devuelve None y el caller lo
    descarta: una respuesta rara no debe tumbar el hilo ni contaminar los
    datos.
    """
    if not respuesta:
        return None
    trozo = re.search(r"\{.*?\}", respuesta, re.DOTALL)
    if not trozo:
        return None
    try:
        datos = json.loads(trozo.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(datos, dict):
        return None

    genero = _GENEROS.get(str(datos.get("genero", "")).strip().lower())
    categoria = str(datos.get("categoria_edad", "")).strip().lower()
    if categoria not in _CATEGORIAS:
        categoria = None
    seguridad = str(datos.get("seguridad", "baja")).strip().lower()
    if seguridad not in ("alta", "media", "baja"):
        seguridad = "baja"
    return ResultadoVLM(track_id=track_id, genero=genero,
                        categoria_edad=categoria, seguridad=seguridad,
                        crudo=respuesta[:300])


class VerificadorVLM:
    """Cola asincrona de verificacion con VLM. Nunca bloquea al pipeline."""

    def __init__(self, activo: bool = False, tam_cola: int = 8,
                 umbral_confianza: float = 0.60,
                 max_tokens: int = 80) -> None:
        self._activo = bool(activo)
        self._umbral = float(umbral_confianza)
        self._max_tokens = int(max_tokens)
        self._cola: "queue.Queue[tuple]" = queue.Queue(maxsize=max(1, tam_cola))
        self._resultados: Dict[int, ResultadoVLM] = {}
        self._lock = threading.RLock()
        self._hilo: Optional[threading.Thread] = None
        self._parar = threading.Event()
        self._stats: Dict[str, int] = {
            "encolados": 0, "descartados_cola_llena": 0, "procesados": 0,
            "no_parseables": 0, "errores": 0, "utiles": 0,
        }
        if self._activo:
            self._arrancar()

    # ── Ciclo de vida ───────────────────────────────────────────────────

    def _arrancar(self) -> None:
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True,
                                      name="verificador-vlm")
        self._hilo.start()
        logger.info("Verificador VLM ACTIVO (cola=%d, umbral=%.2f)",
                    self._cola.maxsize, self._umbral)

    def detener(self, esperar: float = 2.0) -> None:
        """Para el hilo. El modelo se libera cuando lo haga el router."""
        self._parar.set()
        try:
            self._cola.put_nowait(None)      # despierta al hilo
        except queue.Full:
            pass
        if self._hilo is not None:
            self._hilo.join(timeout=esperar)

    @property
    def activo(self) -> bool:
        return self._activo

    # ── Entrada ─────────────────────────────────────────────────────────

    def necesita_verificacion(self, confianza: float,
                              tiene_demografia: bool) -> bool:
        """¿Merece la pena gastar el VLM en este track?

        Solo los dudosos: los que no llegaron a veredicto o lo hicieron con
        poca confianza. Un track firme no se re-consulta.
        """
        if not self._activo:
            return False
        return (not tiene_demografia) or confianza < self._umbral

    def encolar(self, track_id: int, imagen: np.ndarray,
                confianza: float = 0.0) -> bool:
        """Encola SIN bloquear. Devuelve False si no entro.

        Si la cola esta llena se descarta a proposito: es preferible perder
        una verificacion que acumular trabajo viejo y quedarse siempre por
        detras del video.
        """
        if not self._activo or imagen is None:
            return False
        try:
            self._cola.put_nowait((int(track_id), imagen, float(confianza)))
            with self._lock:
                self._stats["encolados"] += 1
            return True
        except queue.Full:
            with self._lock:
                self._stats["descartados_cola_llena"] += 1
            logger.debug("Verificador VLM: cola llena, se descarta el "
                         "track %s", track_id)
            return False

    # ── Hilo de trabajo ─────────────────────────────────────────────────

    def _bucle(self) -> None:
        """Consume la cola hasta que se pida parar. Nunca propaga errores."""
        while not self._parar.is_set():
            try:
                item = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            track_id, imagen, _confianza = item
            try:
                self._procesar(track_id, imagen)
            except Exception as exc:  # noqa: BLE001
                # Una consulta fallida jamas debe matar el hilo: si muere,
                # el verificador queda mudo sin que nadie se entere.
                with self._lock:
                    self._stats["errores"] += 1
                logger.warning("Verificador VLM: fallo con el track %s: "
                               "%s: %s", track_id, type(exc).__name__, exc)
            finally:
                self._cola.task_done()

    def _procesar(self, track_id: int, imagen: np.ndarray) -> None:
        from ..multimodal_router import get_multimodal_router
        respuesta = get_multimodal_router().vqa(
            imagen, _PREGUNTA, max_new_tokens=self._max_tokens)
        with self._lock:
            self._stats["procesados"] += 1
        resultado = _parsear(respuesta, track_id)
        if resultado is None:
            with self._lock:
                self._stats["no_parseables"] += 1
            logger.debug("Verificador VLM: respuesta no parseable para %s: %r",
                         track_id, (respuesta or "")[:120])
            return
        with self._lock:
            self._resultados[track_id] = resultado
            if resultado.es_util:
                self._stats["utiles"] += 1
            # Acotar memoria en sesiones largas.
            if len(self._resultados) > 5000:
                for clave in list(self._resultados)[:2500]:
                    self._resultados.pop(clave, None)

    # ── Consulta ────────────────────────────────────────────────────────

    def obtener(self, track_id: int) -> Optional[ResultadoVLM]:
        """Segunda opinion de ese track, si ya llego."""
        with self._lock:
            return self._resultados.get(track_id)

    def estadisticas(self) -> Dict[str, Any]:
        with self._lock:
            datos = dict(self._stats)
        datos["activo"] = self._activo
        datos["en_cola"] = self._cola.qsize()
        datos["capacidad_cola"] = self._cola.maxsize
        return datos


# ── Instancia compartida ────────────────────────────────────────────────

_verificador: Optional[VerificadorVLM] = None
_lock_creacion = threading.Lock()


def obtener_verificador() -> VerificadorVLM:
    """Verificador compartido, configurado desde AnalyticsConfig."""
    global _verificador
    if _verificador is None:
        with _lock_creacion:
            if _verificador is None:
                try:
                    from .config import AnalyticsConfig as _cfg
                    _verificador = VerificadorVLM(
                        activo=bool(_cfg.VLM_VERIFICADOR_ENABLED),
                        tam_cola=int(_cfg.VLM_VERIFICADOR_TAM_COLA),
                        umbral_confianza=float(
                            _cfg.VLM_VERIFICADOR_UMBRAL_CONF),
                        max_tokens=int(_cfg.VLM_VERIFICADOR_MAX_TOKENS))
                except Exception:  # noqa: BLE001
                    _verificador = VerificadorVLM(activo=False)
    return _verificador
