"""
agregador_demografico.py - Consolidacion por track (Hito 5).

    +---------------------------------------------------------------+
    | AVISO: HOY NO ESTA EN EL CAMINO CRITICO.                       |
    |                                                               |
    | El pipeline en produccion consolida con `_TrackAccumulator`    |
    | (en demographics.py), que ya hacia votacion ponderada por      |
    | confianza con pesos distintos para rostro y cuerpo. Meter un   |
    | segundo agregador en paralelo duplicaria el estado por track   |
    | sin ganar nada, asi que la integracion del Hito 7 se hizo      |
    | sobre el existente.                                           |
    |                                                               |
    | Este modulo se conserva porque su formula de confianza esta    |
    | documentada y medida, y porque su manejo de                    |
    | `motivo_sin_demografia` es mas explicito. Si algun dia se      |
    | unifica la logica, este es el candidato a quedarse; mientras   |
    | tanto, NO lo conectes creyendo que ya esta activo.             |
    +---------------------------------------------------------------+

Una sola lectura de un frame no es fiable con estas camaras: la persona
camina, se gira, cambia la iluminacion. Este modulo junta todas las
muestras de un mismo track y emite UN veredicto por persona.

Reglas:
  * Genero: moda PONDERADA POR CONFIANZA. Una muestra de 0.99 pesa mucho
    mas que tres de 0.55, que es justo lo que interesa cuando la mayoria
    de lecturas vienen de espaldas.
  * Rango de edad: bucket modal (el mas repetido); si hay empate, gana el
    de mayor confianza acumulada.
  * Ventana deslizante: se conservan las ultimas N muestras, no todas. Una
    persona que cruza despacio no debe quedar dominada por lo que se vio
    al principio, cuando estaba lejos.
  * Nunca se emite por debajo del minimo de muestras.
  * Los tracks que NO llegan al minimo se registran igualmente, con
    `genero=None` y un `motivo_sin_demografia` explicito. Un `null` mudo
    no distingue "no se pudo" de "no se intento", y esa ambiguedad fue
    precisamente lo que mantuvo invisible el fallo original.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from .estimador_edad_genero import MuestraDemografica

logger = logging.getLogger(__name__)

# ── Motivos por los que un track se queda sin demografia ────────────────
SIN_MUESTRAS: str = "sin_muestras"
"""Ni una sola lectura valida (nunca se pudo estimar)."""
MUESTRAS_INSUFICIENTES: str = "muestras_insuficientes"
"""Hubo lecturas, pero menos que el minimo exigido."""
CONFIANZA_INSUFICIENTE: str = "confianza_insuficiente"
"""Suficientes lecturas, pero la votacion quedo demasiado repartida."""


@dataclass
class VeredictoDemografico:
    """Resultado consolidado de UN track. Es lo que se persiste."""

    track_id: int
    genero: Optional[str] = None          # "Hombre" | "Mujer" | None
    rango_edad: Optional[str] = None
    confianza_global: float = 0.0
    n_muestras: int = 0
    n_con_rostro: int = 0
    solo_cuerpo: bool = True
    motivo_sin_demografia: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def tiene_demografia(self) -> bool:
        return self.genero in ("Hombre", "Mujer")

    def a_dict(self) -> Dict[str, Any]:
        """Campos para el JSON de `capture/`.

        `gender` y `age_range` conservan nombre y tipo EXACTOS porque el
        cliente ya los interpreta (mapea colores sobre los literales
        "Hombre"/"Mujer"). El resto son campos ADICIONALES: el panel del
        cliente los ignora sin romperse porque lee con `.get()`.
        """
        datos: Dict[str, Any] = {
            "gender": self.genero,
            "age_range": self.rango_edad,
            "conf_genero": round(self.confianza_global, 3),
            "solo_cuerpo": self.solo_cuerpo,
            "n_muestras": self.n_muestras,
        }
        if not self.tiene_demografia:
            datos["motivo_sin_demografia"] = self.motivo_sin_demografia
        return datos


class AgregadorDemografico:
    """Acumula muestras por track y emite un veredicto por persona.

    Seguro para uso concurrente: el servidor procesa varias camaras en un
    ThreadPoolExecutor y varias pueden cerrar tracks a la vez.
    """

    def __init__(self, min_muestras: Optional[int] = None,
                 max_muestras: Optional[int] = None,
                 conf_minima_muestra: Optional[float] = None,
                 conf_minima_veredicto: Optional[float] = None) -> None:
        # Los valores por defecto viven en AnalyticsConfig (config
        # centralizada); los parametros solo sirven para los tests.
        from .config import AnalyticsConfig as _cfg
        if min_muestras is None:
            min_muestras = _cfg.DEMO_AGG_MIN_MUESTRAS
        if max_muestras is None:
            max_muestras = _cfg.DEMO_AGG_MAX_MUESTRAS
        if conf_minima_muestra is None:
            conf_minima_muestra = _cfg.DEMO_AGG_CONF_MIN_MUESTRA
        if conf_minima_veredicto is None:
            conf_minima_veredicto = _cfg.DEMO_AGG_CONF_MIN_VEREDICTO
        self._min = max(1, int(min_muestras))
        self._max = max(self._min, int(max_muestras))
        self._conf_min_muestra = float(conf_minima_muestra)
        self._conf_min_veredicto = float(conf_minima_veredicto)
        self._buffers: Dict[int, Deque[MuestraDemografica]] = defaultdict(
            lambda: deque(maxlen=self._max))
        self._descartadas: Dict[int, int] = defaultdict(int)
        self._emitidos: Dict[int, VeredictoDemografico] = {}
        self._lock = threading.RLock()

    # ── Entrada de muestras ─────────────────────────────────────────────

    def agregar(self, muestra: Optional[MuestraDemografica]) -> bool:
        """Anade una muestra al track. True si entro en la votacion.

        Se filtran las lecturas de baja confianza: en una votacion
        ponderada tambien arrastran, y con estas camaras hay muchas.
        """
        if muestra is None:
            return False
        with self._lock:
            if not muestra.es_valida():
                self._descartadas[muestra.track_id] += 1
                return False
            if muestra.conf_genero < self._conf_min_muestra:
                self._descartadas[muestra.track_id] += 1
                return False
            self._buffers[muestra.track_id].append(muestra)
            return True

    def listo_para_emitir(self, track_id: int) -> bool:
        """True si el track ya alcanzo el tope de la ventana."""
        with self._lock:
            return len(self._buffers.get(track_id, ())) >= self._max

    def n_muestras(self, track_id: int) -> int:
        with self._lock:
            return len(self._buffers.get(track_id, ()))

    # ── Veredicto ───────────────────────────────────────────────────────

    def cerrar_track(self, track_id: int) -> VeredictoDemografico:
        """Emite el veredicto FINAL del track y libera su buffer.

        Idempotente: si el track ya se cerro, devuelve el mismo veredicto
        en lugar de recalcularlo. Asi una persona que cruza la escena
        produce exactamente UN registro, aunque el pipeline llame dos
        veces (p. ej. al perder el track y luego al limpiar el estado).
        """
        with self._lock:
            if track_id in self._emitidos:
                return self._emitidos[track_id]
            muestras = list(self._buffers.pop(track_id, ()))
            descartadas = self._descartadas.pop(track_id, 0)
            veredicto = self._calcular(track_id, muestras, descartadas)
            self._emitidos[track_id] = veredicto
            # Acotar el historico de emitidos en sesiones largas.
            if len(self._emitidos) > 10_000:
                for clave in list(self._emitidos)[:5_000]:
                    self._emitidos.pop(clave, None)
            return veredicto

    def _calcular(self, track_id: int, muestras: List[MuestraDemografica],
                  descartadas: int) -> VeredictoDemografico:
        """Aplica la votacion. Ver el docstring de la clase para las reglas."""
        n = len(muestras)
        con_rostro = sum(1 for m in muestras if not m.solo_cuerpo)

        if n == 0:
            return VeredictoDemografico(
                track_id=track_id, n_muestras=0,
                motivo_sin_demografia=(
                    MUESTRAS_INSUFICIENTES if descartadas else SIN_MUESTRAS))

        if n < self._min:
            return VeredictoDemografico(
                track_id=track_id, n_muestras=n, n_con_rostro=con_rostro,
                solo_cuerpo=con_rostro == 0,
                motivo_sin_demografia=MUESTRAS_INSUFICIENTES)

        # ── Genero: moda ponderada por confianza ──
        peso_por_genero: Dict[str, float] = defaultdict(float)
        for m in muestras:
            # Las lecturas con rostro valen mas: MiVOLO con entrada dual
            # es mas certero que con el cuerpo solo.
            factor = 1.0 if m.solo_cuerpo else 1.5
            peso_por_genero[m.genero] += m.conf_genero * factor
        genero = max(peso_por_genero, key=peso_por_genero.get)
        peso_total = sum(peso_por_genero.values())
        peso_ganador = peso_por_genero[genero]
        # Margen de la votacion: 1.0 = unanimidad, 0.0 = empate absoluto.
        margen = ((peso_ganador - (peso_total - peso_ganador)) / peso_total
                  if peso_total > 0 else 0.0)
        margen = max(0.0, margen)

        # ── Edad: bucket modal; empate -> mayor confianza acumulada ──
        votos_edad: Dict[str, int] = defaultdict(int)
        conf_edad: Dict[str, float] = defaultdict(float)
        for m in muestras:
            if m.genero != genero:
                continue      # solo cuentan las coherentes con el genero
            votos_edad[m.rango_edad] += 1
            conf_edad[m.rango_edad] += m.conf_edad
        if votos_edad:
            tope = max(votos_edad.values())
            empatados = [b for b, v in votos_edad.items() if v == tope]
            rango = (empatados[0] if len(empatados) == 1
                     else max(empatados, key=lambda b: conf_edad[b]))
        else:
            rango = "Desconocido"

        confianza = self._confianza_global(muestras, con_rostro, margen)
        if confianza < self._conf_min_veredicto:
            return VeredictoDemografico(
                track_id=track_id, n_muestras=n, n_con_rostro=con_rostro,
                solo_cuerpo=con_rostro == 0,
                confianza_global=round(confianza, 3),
                motivo_sin_demografia=CONFIANZA_INSUFICIENTE)

        return VeredictoDemografico(
            track_id=track_id, genero=genero, rango_edad=rango,
            confianza_global=round(confianza, 3), n_muestras=n,
            n_con_rostro=con_rostro, solo_cuerpo=con_rostro == 0)

    def _confianza_global(self, muestras: List[MuestraDemografica],
                          con_rostro: int, margen: float) -> float:
        """Confianza del veredicto, en 0..1.

        FORMULA (documentada porque condiciona que se publica y que no):

            confianza = conf_media * f_muestras * f_rostro * f_margen

          conf_media  media de la confianza de genero de las muestras.
                      Es la base: si el modelo dudo en todas, el veredicto
                      no puede ser firme por muchas lecturas que haya.
          f_muestras  0.70 -> 1.00 segun n / max_muestras. Nunca baja de
                      0.70: con el minimo ya exigido, mas muestras suman
                      pero su ausencia no debe hundir el resultado.
          f_rostro    0.85 -> 1.00 segun la proporcion con rostro. Un
                      veredicto apoyado solo en el cuerpo es legitimo (es
                      el caso normal aqui), pero vale algo menos.
          f_margen    0.50 -> 1.00 segun lo repartida que quedo la
                      votacion. Es el factor que mas castiga: si la mitad
                      de las lecturas dicen Hombre y la otra mitad Mujer,
                      el resultado no se sostiene aunque cada lectura
                      viniera con alta confianza.

        Los cuatro se multiplican a proposito: basta que uno sea malo para
        que el veredicto no se publique. Es coherente con la politica del
        sistema, que prefiere "Desconocido" a un dato equivocado.
        """
        if not muestras:
            return 0.0
        conf_media = sum(m.conf_genero for m in muestras) / len(muestras)
        f_muestras = 0.70 + 0.30 * min(1.0, len(muestras) / float(self._max))
        f_rostro = 0.85 + 0.15 * (con_rostro / float(len(muestras)))
        f_margen = 0.50 + 0.50 * min(1.0, margen)
        return float(conf_media * f_muestras * f_rostro * f_margen)

    # ── Mantenimiento ───────────────────────────────────────────────────

    def olvidar(self, track_id: int) -> None:
        """Libera el estado de un track sin emitir veredicto."""
        with self._lock:
            self._buffers.pop(track_id, None)
            self._descartadas.pop(track_id, None)

    def tracks_activos(self) -> int:
        with self._lock:
            return len(self._buffers)
