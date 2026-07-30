"""
analytics/restock_detector.py - Reposicion de mercancia por empleados.

Emite el evento "empleado repone mercancia / abastece anaquel" y lo
cronometra. En una camara cenital gran-angular NO se puede reconocer el
gesto fino de "sacar un producto de la caja y ponerlo en el estante"; se
INFIERE de la co-ocurrencia de senales que juntas solo se dan al reponer:

    caja en el piso  +  persona pegada a la caja un rato  +  esa persona
    alcanzando el estante repetidamente  (y, como refuerzo, el nivel del
    anaquel SUBIENDO)

Un cliente normal no se queda junto a una caja de mercancia alcanzando el
estante una y otra vez; un repositor si. Esa es la firma.

Identificar al "empleado": por defecto es CONDUCTUAL (quien hace lo de
arriba es tratado como repositor, se etiquete o no el uniforme). Si
``SELLER_COLOR_ENABLED`` esta activo, un match de color de uniforme sube la
confianza (``empleado_verificado``), pero no es obligatorio, porque a este
angulo el uniforme no se distingue de forma fiable.

Efecto colateral IMPORTANTE que se corrige aparte: mientras alguien repone,
sus "alcances" al estante devuelven producto (el nivel sube). Sin filtrar,
el pipeline los contaria como clientes devolviendo decenas de articulos e
inflaria las metricas de compra. El orquestador consulta
``active_restocker_pids()`` y NO pasa esos eventos a la analitica de compra.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import AnalyticsConfig
from .shelf_interaction import EVENT_GRAB, EVENT_RETURN
from .store_layout import StoreLayout

logger = logging.getLogger(__name__)


class _Session:
    """Una sesion de reposicion en curso de una persona."""

    __slots__ = ("pid", "box_id", "aisle", "start_ts", "last_near_ts",
                 "reaches", "returns", "shelves", "opened", "open_ts",
                 "verified", "closed")

    def __init__(self, pid: str, box_id: int, aisle: Optional[str],
                 ts: float):
        self.pid = pid
        self.box_id = box_id
        self.aisle = aisle
        self.start_ts = ts
        self.last_near_ts = ts
        self.reaches = 0
        self.returns = 0
        self.shelves: set = set()
        self.opened = False
        self.open_ts: Optional[float] = None
        self.verified = False
        self.closed = False


class RestockDetector:
    """Detecta y cronometra reposiciones (empleado + caja + anaquel)."""

    def __init__(self, layout: StoreLayout, config: AnalyticsConfig = None):
        cfg = config or AnalyticsConfig
        self._cfg = cfg
        self._layout = layout
        self._near_factor = float(cfg.RESTOCK_NEAR_FACTOR)
        self._min_dwell = float(cfg.RESTOCK_MIN_DWELL_S)
        self._min_reaches = int(cfg.RESTOCK_MIN_REACHES)
        self._grace = float(cfg.RESTOCK_END_GRACE_S)
        self._require_uniform = bool(cfg.RESTOCK_REQUIRE_UNIFORM)
        self._sessions: Dict[str, _Session] = {}
        self._finished: List[Dict[str, Any]] = []
        self._near_now: set = set()   # pids pegados a una caja ESTE frame
        # track_id -> ultimo pid (migrar la sesion cuando prov -> uuid, para
        # no abrir una segunda sesion de la misma persona al confirmarse).
        self._tid_pid: Dict[Any, str] = {}
        self._total = 0
        self._w = 0
        self._h = 0

    # ── Ciclo por frame ──────────────────────────────────────────────

    def update(self, tracks: Dict[Any, Dict[str, Any]],
               confirmed_boxes: List[Dict[str, Any]],
               shelf_events: List[Dict[str, Any]],
               frame_shape: Tuple[int, int] = None,
               uniform_check=None,
               now: float = None) -> List[Dict[str, Any]]:
        """Correlaciona cajas, personas y alcances -> sesiones de reposicion.

        Args:
            tracks: active_tracks (usa box y persistent_id).
            confirmed_boxes: cajas confirmadas de AisleBoxMonitor.
            shelf_events: eventos de interaccion con anaquel de este frame.
            frame_shape: (h, w) del frame, para las distancias.
            uniform_check: callable(track) -> bool (opcional) que dice si el
                torso de esa persona matchea el color de uniforme.
            now: reloj inyectable (test).

        Returns:
            Eventos: reposicion_iniciada, reposicion_finalizada.
        """
        now = time.time() if now is None else now
        if frame_shape:
            self._h, self._w = frame_shape[0], frame_shape[1]
        eventos: List[Dict[str, Any]] = []

        # 0) Migracion de identidad: si el pid de un track cambio (el rostro
        #    del repositor se confirmo -> prov:X pasa a uuid), la sesion en
        #    curso se RENOMBRA en vez de abrir una segunda de la misma
        #    persona (que re-emitiria 'reposicion_iniciada').
        vistos: Dict[Any, str] = {}
        for track in tracks.values():
            tid = track.get("track_id")
            pid = track.get("persistent_id")
            if tid is None or not pid:
                continue
            pid = str(pid)
            vistos[tid] = pid
            old = self._tid_pid.get(tid)
            if (old and old != pid and old in self._sessions
                    and pid not in self._sessions):
                s = self._sessions.pop(old)
                s.pid = pid
                self._sessions[pid] = s
        self._tid_pid = vistos

        # 1) Que personas estan PEGADAS a una caja confirmada ahora.
        near: Dict[str, Dict[str, Any]] = {}   # pid -> {box_id, aisle, track}
        for track in tracks.values():
            pid = track.get("persistent_id")
            box = track.get("box")
            if not pid or box is None:
                continue
            # OJO: se aceptan ids PROVISIONALES ('prov:...'). Un empleado
            # reponiendo esta DE ESPALDAS a la camara (mirando el estante):
            # su rostro casi nunca se confirma, asi que exigir identidad
            # confirmada = no detectar reposiciones jamas. La senal es
            # conductual y el id provisional es estable mientras vive el track.
            pid = str(pid)
            b = self._nearest_box(box, confirmed_boxes)
            if b is not None:
                near[pid] = {"box_id": b["id"], "aisle": b["aisle"],
                             "track": track}
        self._near_now = set(near.keys())

        # 2) Abrir/actualizar sesiones para las personas pegadas a una caja.
        for pid, info in near.items():
            s = self._sessions.get(pid)
            if s is None or s.closed:
                s = _Session(pid, info["box_id"], info["aisle"], now)
                self._sessions[pid] = s
            s.last_near_ts = now
            s.box_id = info["box_id"]
            if info["aisle"]:
                s.aisle = info["aisle"]
            if uniform_check is not None and not s.verified:
                try:
                    s.verified = bool(uniform_check(info["track"]))
                except Exception:  # noqa: BLE001
                    pass

        # 3) Contar los alcances al estante de quienes tienen sesion.
        #    Los alcances se cuentan con el AGARRE INMEDIATO (mano dentro del
        #    ROI), no con los eventos de resultado (TOMA/DEVOLUCION/TOQUE):
        #    esos solo se resuelven cuando el anaquel se DES-OCLUYE, y un
        #    empleado reponiendo lo tapa todo el rato -> nunca llegarian a
        #    tiempo para confirmar la sesion. EVENT_RETURN (cuando al fin se
        #    resuelve) refina la estimacion de articulos repuestos.
        for ev in shelf_events or []:
            pid = str(ev.get("persistent_id") or "")
            s = self._sessions.get(pid)
            if s is None or s.closed:
                continue
            tipo = ev.get("evento")
            if tipo == EVENT_GRAB:
                s.reaches += 1
                if ev.get("anaquel"):
                    s.shelves.add(ev["anaquel"])
            elif tipo == EVENT_RETURN:
                s.returns += 1   # nivel subio: producto puesto en el estante
                if ev.get("anaquel"):
                    s.shelves.add(ev["anaquel"])

        # 4) Confirmar (abrir) las sesiones que cumplen la firma.
        for s in self._sessions.values():
            if s.opened or s.closed:
                continue
            dwell_ok = (now - s.start_ts) >= self._min_dwell
            reaches_ok = s.reaches >= self._min_reaches
            uniform_ok = (not self._require_uniform) or s.verified
            if dwell_ok and reaches_ok and uniform_ok:
                s.opened = True
                s.open_ts = now
                self._total += 1
                eventos.append(self._event("reposicion_iniciada", s, now))

        # 5) Cerrar sesiones cuya persona ya no esta junto a la caja.
        for pid in list(self._sessions.keys()):
            s = self._sessions[pid]
            if s.closed:
                # Limpiar sesiones cerradas hace rato.
                if now - s.last_near_ts > self._grace * 3:
                    self._sessions.pop(pid, None)
                continue
            if pid in near:
                continue
            if now - s.last_near_ts <= self._grace:
                continue
            s.closed = True
            if s.opened:
                ev = self._event("reposicion_finalizada", s, s.last_near_ts)
                ev["duracion_s"] = round(s.last_near_ts - (s.open_ts
                                                           or s.start_ts), 1)
                self._finished.append(ev)
                del self._finished[:-200]
                eventos.append(ev)
        return eventos

    def _nearest_box(self, person_box,
                     boxes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Caja confirmada mas cercana al PIE de la persona, dentro de un
        radio proporcional al tamano de la caja. None si ninguna."""
        try:
            x1, y1, x2, y2 = [float(v) for v in person_box]
        except (TypeError, ValueError):
            return None
        fx, fy = (x1 + x2) / 2.0, y2
        mejor, mejor_d = None, None
        for b in boxes:
            cx, cy = b["center"]
            bw = abs(b["box"][2] - b["box"][0])
            bh = abs(b["box"][3] - b["box"][1])
            radio = max(bw, bh) * self._near_factor
            d = float(np.hypot(fx - cx, fy - cy))
            if d <= radio and (mejor_d is None or d < mejor_d):
                mejor, mejor_d = b, d
        return mejor

    def _event(self, tipo: str, s: _Session, now: float) -> Dict[str, Any]:
        return {
            "evento": tipo,
            "persistent_id": s.pid,
            "caja_id": s.box_id,
            "pasillo": s.aisle,
            "anaqueles": sorted(s.shelves),
            "articulos_estimados": int(s.returns) or int(s.reaches),
            "reposiciones_al_estante": int(s.reaches),
            "empleado_verificado": bool(s.verified),
            "segundos": round(now - (s.open_ts or s.start_ts), 1),
            "timestamp": now,
        }

    # ── Consultas ────────────────────────────────────────────────────

    def active_restocker_pids(self) -> set:
        """persistent_id que estan reponiendo AHORA (sesion abierta y no
        cerrada)."""
        return {pid for pid, s in self._sessions.items()
                if s.opened and not s.closed}

    def near_box_pids(self) -> set:
        """persistent_id pegados a una caja ESTE frame (aun sin confirmar la
        reposicion). El orquestador tambien los excluye de la analitica de
        compra: alguien pegado a una caja de mercancia alcanzando el estante
        es casi seguro un empleado, no un cliente. Esto evita que los
        primeros alcances (antes de confirmarse la reposicion) se cuenten
        como una compra."""
        return set(self._near_now)

    def get_stats(self, now: float = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        activas = [s for s in self._sessions.values()
                   if s.opened and not s.closed]
        durs = [f["duracion_s"] for f in self._finished if "duracion_s" in f]
        return {
            "reposiciones_en_curso": [{
                "persistent_id": s.pid, "pasillo": s.aisle,
                "anaqueles": sorted(s.shelves),
                "segundos": round(now - (s.open_ts or s.start_ts), 1),
                "empleado_verificado": bool(s.verified),
            } for s in activas],
            "reposiciones_activas": len(activas),
            "reposiciones_totales": self._total,
            "duracion_media_s": round(float(np.mean(durs)), 1) if durs else 0.0,
            "duracion_total_s": round(float(np.sum(durs)), 1) if durs else 0.0,
        }

    def recent_finished(self, n: int = 20) -> List[Dict[str, Any]]:
        return self._finished[-n:]

    def reset(self) -> None:
        self._sessions.clear()
        self._finished.clear()
        self._tid_pid.clear()
        self._total = 0
