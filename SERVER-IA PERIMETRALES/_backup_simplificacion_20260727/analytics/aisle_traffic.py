"""
analytics/aisle_traffic.py - Afluencia y concentracion por pasillo.

Responde las preguntas de negocio:

  * En que pasillo hay MAS fluencia de personas (y en cual menos).
  * Donde esta la MAYOR concentracion de personas ahora mismo y donde la
    menor.
  * Cuanto tiempo se queda la gente en cada pasillo (permanencia), quien
    (genero/edad) y cuantos son personas DISTINTAS y no pasadas repetidas.

Dos metricas distintas que se confunden facilmente y aqui se separan:

  - AFLUENCIA (trafico): cuantas personas distintas pasaron por el pasillo
    a lo largo de la sesion. Es acumulada. "El pasillo 3 es el mas
    transitado del dia".
  - CONCENTRACION (densidad): cuantas personas hay AHORA por unidad de
    area del pasillo. Es instantanea y se normaliza por el area del
    poligono, porque si no un pasillo grande siempre "gana". "Ahora mismo
    la gente esta amontonada en el pasillo 5".

El conteo de personas usa ``persistent_id`` (identidad biometrica), asi
que alguien que entra, sale y vuelve al mismo pasillo cuenta como UN
visitante unico con DOS visitas. Las transiciones se confirman con
histeresis para que nadie parado en el borde genere entradas fantasma.
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import AnalyticsConfig
from .store_layout import Aisle, StoreLayout

logger = logging.getLogger(__name__)


class _AisleStats:
    """Acumulador de metricas de un pasillo."""

    def __init__(self, nombre: str):
        self.nombre = nombre
        self.visitas: int = 0                 # entradas confirmadas
        self.unicos: set = set()              # persistent_id distintos
        self.tiempo_total: float = 0.0        # suma de permanencias (s)
        self.ocupacion: int = 0               # personas dentro ahora
        self.ocupacion_pico: int = 0
        self.densidad: float = 0.0            # personas / 1% del frame
        self.densidad_pico: float = 0.0
        self.area_rel: float = 0.0            # area del poligono / area frame
        self.genero: Counter = Counter()
        self.edad: Counter = Counter()
        # Estado por persona dentro del pasillo: pid -> entry_ts
        self._dentro: Dict[str, float] = {}

    def permanencia_media(self) -> float:
        return (self.tiempo_total / self.visitas) if self.visitas else 0.0


class AisleTraffic:
    """Mide trafico y concentracion por pasillo del planograma."""

    def __init__(self, layout: StoreLayout, config: AnalyticsConfig = None):
        cfg = config or AnalyticsConfig
        self._layout = layout
        self._hysteresis = max(1, int(cfg.AISLE_HYSTERESIS_FRAMES))
        self._min_dwell = float(cfg.AISLE_MIN_DWELL_S)
        self._stats: Dict[str, _AisleStats] = {}
        # (pasillo, pid) -> {'pending': str|None, 'frames': int}
        self._pending: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # track_id -> ultimo pid visto (para migrar estado cuando el pid
        # provisional se confirma a uuid: anti doble-conteo).
        self._tid_pid: Dict[Any, str] = {}
        self._area_cache: Dict[str, Tuple[Tuple[int, int], float]] = {}
        self._started = time.time()

    # ── Actualizacion por frame ──────────────────────────────────────

    def update(self, tracks: Dict[Any, Dict[str, Any]], frame_w: int,
               frame_h: int,
               demo_getter=None) -> List[Dict[str, Any]]:
        """Asigna cada persona a un pasillo y actualiza las metricas.

        Args:
            tracks: ``active_tracks`` del inferidor. De cada track se usa
                ``box`` (para el punto de pie) y ``persistent_id``.
            frame_w, frame_h: tamano del frame en px.
            demo_getter: callable(track) -> {'gender', 'age_range'} | None,
                para el desglose demografico por pasillo.

        Returns:
            Lista de eventos de entrada a pasillo confirmados este frame.
        """
        aisles = list(self._layout.aisles)
        if not aisles or frame_w <= 0 or frame_h <= 0:
            return []

        # MIGRACION de identidad (anti doble-conteo): cuando el rostro de un
        # track se confirma, su pid cambia de 'prov:<tid>' al uuid biometrico
        # definitivo. Sin migrar, la MISMA persona apareceria dos veces (una
        # como prov y otra como uuid). Se detecta por track_id y se renombran
        # sus llaves de estado (dwell en curso e histeresis pendiente).
        self._migrate_flipped_pids(tracks)

        for a in aisles:
            st = self._stats.get(a.nombre)
            if st is None:
                st = _AisleStats(a.nombre)
                self._stats[a.nombre] = st
            st.area_rel = self._area_rel(a, frame_w, frame_h)

        # 1) Observacion: que personas estan en que pasillo AHORA.
        #    Una persona esta en un pasillo si su punto de PIE cae dentro.
        ocupantes: Dict[str, set] = defaultdict(set)
        demo_por_pid: Dict[str, Dict[str, str]] = {}
        for track in tracks.values():
            pid = track.get("persistent_id")
            box = track.get("box")
            if not pid or box is None:
                continue
            try:
                x1, _y1, x2, y2 = [float(v) for v in box]
            except (TypeError, ValueError):
                continue
            fx, fy = (x1 + x2) / 2.0, min(y2, frame_h - 1)
            a = self._layout.aisle_at(fx, fy, frame_w, frame_h)
            if a is None:
                continue
            ocupantes[a.nombre].add(str(pid))
            if demo_getter is not None and str(pid) not in demo_por_pid:
                d = demo_getter(track)
                if d:
                    demo_por_pid[str(pid)] = d

        # 2) Ocupacion / densidad instantanea + histeresis de entrada/salida.
        eventos: List[Dict[str, Any]] = []
        now = time.time()
        for a in aisles:
            st = self._stats[a.nombre]
            dentro_ahora = ocupantes.get(a.nombre, set())
            st.ocupacion = len(dentro_ahora)
            st.ocupacion_pico = max(st.ocupacion_pico, st.ocupacion)
            # Densidad normalizada: personas por cada 1% de area del frame.
            # Sin esto, un pasillo el doble de grande parece el doble de
            # concurrido con la misma aglomeracion.
            st.densidad = (st.ocupacion / (st.area_rel * 100.0)
                           if st.area_rel > 0 else 0.0)
            st.densidad_pico = max(st.densidad_pico, st.densidad)

            # Entradas: pid que aparece dentro y aun no estaba registrado.
            for pid in dentro_ahora:
                if pid in st._dentro:
                    continue
                if self._confirm(a.nombre, pid, "DENTRO"):
                    st._dentro[pid] = now
                    st.visitas += 1
                    # UNICOS solo con identidad CONFIRMADA (uuid biometrico).
                    # Un id provisional cambia cada vez que el track se rompe:
                    # contarlo inflaria los visitantes unicos con la misma
                    # persona repetida. Si se confirma despues, la migracion
                    # de arriba lo agrega en ese momento.
                    if not pid.startswith("prov:"):
                        st.unicos.add(pid)
                    d = demo_por_pid.get(pid)
                    if d:
                        st.genero[d.get("gender") or "Desconocido"] += 1
                        st.edad[d.get("age_range") or "Desconocido"] += 1
                    eventos.append({
                        "evento": "entrada_pasillo", "pasillo": a.nombre,
                        "persistent_id": pid, "timestamp": now,
                    })

            # Salidas: pid registrado que ya no esta dentro.
            for pid in list(st._dentro.keys()):
                if pid in dentro_ahora:
                    continue
                if self._confirm(a.nombre, pid, "FUERA"):
                    dwell = max(0.0, now - st._dentro.pop(pid))
                    if dwell >= self._min_dwell:
                        st.tiempo_total += dwell
                    else:
                        # Paso de largo: no cuenta como visita real.
                        st.visitas = max(0, st.visitas - 1)
        return eventos

    def _migrate_flipped_pids(self, tracks: Dict[Any, Dict[str, Any]]) -> None:
        """Renombra el estado de un track cuyo pid cambio (prov -> uuid).

        La visita en curso y la histeresis pendiente pasan al nuevo pid; si
        el nuevo pid es un uuid confirmado y la persona esta DENTRO de un
        pasillo, entra a `unicos` (dedup automatico por set: si ese uuid ya
        habia visitado el pasillo antes, no suma de nuevo).
        """
        vistos: Dict[Any, str] = {}
        for track in tracks.values():
            tid = track.get("track_id")
            pid = track.get("persistent_id")
            if tid is None or not pid:
                continue
            pid = str(pid)
            vistos[tid] = pid
            old = self._tid_pid.get(tid)
            if old is None or old == pid:
                continue
            # pid del track cambio -> migrar estado old -> pid
            for st in self._stats.values():
                if old in st._dentro:
                    st._dentro[pid] = st._dentro.pop(old)
                    if not pid.startswith("prov:"):
                        st.unicos.add(pid)
            for key in list(self._pending.keys()):
                if key[1] == old:
                    self._pending[(key[0], pid)] = self._pending.pop(key)
        # Solo interesan los tracks vivos (el flip ocurre en un track vivo).
        self._tid_pid = vistos

    def _confirm(self, pasillo: str, pid: str, nuevo: str) -> bool:
        """Histeresis: confirma un cambio de estado tras N frames seguidos."""
        key = (pasillo, pid)
        p = self._pending.get(key)
        if p is None or p.get("pending") != nuevo:
            self._pending[key] = {"pending": nuevo, "frames": 1}
            return self._hysteresis <= 1
        p["frames"] += 1
        if p["frames"] >= self._hysteresis:
            self._pending.pop(key, None)
            return True
        return False

    def _area_rel(self, aisle: Aisle, frame_w: int, frame_h: int) -> float:
        """Area del poligono como fraccion del area del frame (cacheada)."""
        cached = self._area_cache.get(aisle.nombre)
        if cached is not None and cached[0] == (frame_w, frame_h):
            return cached[1]
        try:
            poly = aisle.polygon_px(frame_w, frame_h)
            area = abs(cv2.contourArea(poly.astype(np.int32)))
            rel = area / float(max(1, frame_w * frame_h))
        except Exception:  # noqa: BLE001
            rel = 0.0
        self._area_cache[aisle.nombre] = ((frame_w, frame_h), rel)
        return rel

    # ── Consultas ────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Metricas por pasillo + rankings de afluencia y concentracion."""
        pasillos = []
        for nombre, st in self._stats.items():
            pasillos.append({
                "pasillo": nombre,
                "visitantes_unicos": len(st.unicos),
                "visitas": int(st.visitas),
                "ocupacion_actual": int(st.ocupacion),
                "ocupacion_pico": int(st.ocupacion_pico),
                "densidad_actual": round(st.densidad, 3),
                "densidad_pico": round(st.densidad_pico, 3),
                "area_relativa": round(st.area_rel, 4),
                "permanencia_media_s": round(st.permanencia_media(), 1),
                "tiempo_total_s": round(st.tiempo_total, 1),
                "genero": dict(st.genero),
                "edad": dict(st.edad),
            })
        if not pasillos:
            return {"pasillos": [], "resumen": {}}

        por_afluencia = sorted(
            pasillos, key=lambda p: (p["visitantes_unicos"], p["visitas"]),
            reverse=True)
        # Concentracion AHORA: solo tiene sentido si hay alguien dentro.
        con_gente = [p for p in pasillos if p["ocupacion_actual"] > 0]
        por_densidad = sorted(pasillos, key=lambda p: p["densidad_actual"],
                              reverse=True)
        resumen = {
            "pasillo_mas_transitado": por_afluencia[0]["pasillo"],
            "pasillo_menos_transitado": por_afluencia[-1]["pasillo"],
            "mayor_concentracion": (por_densidad[0]["pasillo"]
                                    if con_gente else None),
            "menor_concentracion": (
                min(con_gente, key=lambda p: p["densidad_actual"])["pasillo"]
                if len(con_gente) > 1 else None),
            "personas_en_pasillos": sum(
                p["ocupacion_actual"] for p in pasillos),
            "ranking_afluencia": [p["pasillo"] for p in por_afluencia],
            "pasillos_sin_visitas": [p["pasillo"] for p in pasillos
                                     if p["visitantes_unicos"] == 0],
        }
        return {"pasillos": pasillos, "resumen": resumen,
                "desde": self._started}

    def occupancy_map(self) -> Dict[str, int]:
        """{pasillo: personas dentro ahora} para el overlay."""
        return {n: st.ocupacion for n, st in self._stats.items()}

    def aisle_of(self, pid: str) -> Optional[str]:
        """Pasillo en el que esta esa persona ahora, o None."""
        for nombre, st in self._stats.items():
            if str(pid) in st._dentro:
                return nombre
        return None

    def reset(self) -> None:
        self._stats.clear()
        self._pending.clear()
        self._tid_pid.clear()
        self._started = time.time()
