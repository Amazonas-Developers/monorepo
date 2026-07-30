"""
analytics/retail_analytics.py - Orquestador de la analitica de supermercado.

Un unico punto de entrada que el pipeline de inferencia llama una vez por
frame. Coordina:

    store_layout      planograma (pasillos, anaqueles, maquina de precios)
    aisle_traffic     afluencia y concentracion por pasillo
    shelf_interaction nivel de anaqueles + toma / devolucion de producto
    cart_tracker      carritos y cestas, y depositos en ellos
    shopper_journey   decision de compra, duelos entre productos, segmentos

Aqui vive ademas la deteccion de PROXIMIDAD A LA MAQUINA CONSULTORA DE
PRECIOS, que es una zona del planograma con permanencia minima (acercarse
de paso no es consultar; hay que quedarse unos segundos delante).

Todo es degradable: si no hay planograma definido, el orquestador se apaga
solo y el resto del pipeline (personas, rostros, demografia, heatmap) sigue
funcionando exactamente igual. Ningun fallo de aqui puede tumbar el frame
loop: ``update`` atrapa y loguea.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .aisle_traffic import AisleTraffic
from .box_monitor import AisleBoxMonitor
from .cart_tracker import CartTracker
from .config import AnalyticsConfig
from .restock_detector import RestockDetector
from .shelf_interaction import (
    ShelfInteractionDetector, ShelfStockTracker, STATUS_EMPTY, STATUS_LOW,
    EVENT_GRAB,
)
from .shopper_journey import ShopperJourney
from .staff_gallery import StaffGallery
from .store_layout import Fixture, StoreLayout, safe_name

logger = logging.getLogger(__name__)


class RetailAnalytics:
    """Analitica de supermercado para UNA camara."""

    def __init__(self, camera_id: Any = None, device: Any = 0,
                 config: AnalyticsConfig = None,
                 demographics=None, analytics_logger=None,
                 reidentifier=None, staff_yunet_path: str = None):
        cfg = config or AnalyticsConfig
        self._cfg = cfg
        self.camera_id = camera_id
        self._demographics = demographics
        self._logger = analytics_logger
        self._enabled = bool(cfg.RETAIL_ANALYTICS_ENABLED)
        # Personal registrado por FOTO: sin foto subida nadie se etiqueta
        # como empleado/seguridad, y quien matchee queda fuera de las
        # metricas de clientes.
        self.staff = StaffGallery(reidentifier,
                                  yunet_model_path=staff_yunet_path)

        self.layout = StoreLayout(camera_id)
        self.aisles = AisleTraffic(self.layout, cfg)
        self.stock = ShelfStockTracker(self.layout, cfg)
        self.shelves = ShelfInteractionDetector(self.layout, self.stock, cfg,
                                                device=device)
        self.carts = CartTracker(cfg, device=device)
        self.journey = ShopperJourney(cfg)
        # Cajas en el piso + reposicion de empleados.
        self.boxes = AisleBoxMonitor(self.layout, cfg, device=device)
        self.restock = RestockDetector(self.layout, cfg)

        # Precargar YOLO-World en SEGUNDO PLANO (lo usan cajas y carritos).
        # Sin esto, la primera deteccion lo carga DENTRO del frame loop y
        # congela el video ~10-15s (mas si tiene que descargar el peso).
        # OJO: con `_enabled` (no `enabled`): las cajas corren tambien SIN
        # planograma, asi que el router hace falta igual.
        if self._enabled and (cfg.BOX_DETECT_ENABLED
                              or cfg.CART_DETECT_ENABLED):
            self._preload_world_async(device)

        # Estado de la maquina consultora de precios:
        # (pid, maquina) -> {'inicio': ts, 'contado': bool}
        self._price_zone: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Evaluacion de producto: (pid, anaquel) -> {'inicio','alertado','last'}
        self._eval_zone: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._eval_total = 0
        self._grab_total = 0   # agarres de CLIENTE dentro del ROI de anaquel
        self._events: List[Dict[str, Any]] = []
        self._last_tracks: Dict[Any, Dict[str, Any]] = {}
        self._last_report_ts = 0.0
        self._last_saved_ts = 0.0
        self._cached_report: Optional[Dict[str, Any]] = None
        # Fotos de deteccion: ultimo disparo por tipo de evento (throttle) y
        # ultima pasada de retencion.
        self._snap_last: Dict[str, float] = {}
        self._snap_prune_ts = 0.0
        # UNA foto por (tipo, persona) mientras siga en escena:
        # (tipo, pid) -> ts de la foto; pid -> ultima vez vista.
        self._snap_pid_done: Dict[Tuple[str, str], float] = {}
        self._snap_pid_seen: Dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        """Activo solo si hay planograma: sin zonas no hay nada que medir."""
        return self._enabled and not self.layout.is_empty

    @staticmethod
    def _preload_world_async(device: Any) -> None:
        """Carga YOLO-World en un hilo demonio (defensivo: nunca lanza)."""
        import threading

        def _load():
            try:
                from ..multimodal_router import get_multimodal_router
                get_multimodal_router(device=device).warmup(("world",))
                logger.info("YOLO-World precargado (cajas/carritos listos)")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Precarga de YOLO-World fallo: %s "
                               "(cajas/carritos quedaran inactivos)", exc)

        threading.Thread(target=_load, name="yoloworld-preload",
                         daemon=True).start()

    # ── Ciclo por frame ──────────────────────────────────────────────

    def update(self, frame: np.ndarray, tracks: Dict[Any, Dict[str, Any]],
               frame_idx: int = 0) -> List[Dict[str, Any]]:
        """Ejecuta la analitica de retail de este frame.

        Con planograma corre el pipeline completo. SIN planograma, la
        deteccion de CAJAS en el piso sigue corriendo igual (siempre
        activa): las cajas no dependen de zonas dibujadas para existir.

        Args:
            frame: frame BGR limpio (sin overlays).
            tracks: ``active_tracks`` del inferidor.
            frame_idx: contador de frames (para los throttles internos).

        Returns:
            Lista de eventos de negocio generados este frame.
        """
        if not self._enabled or frame is None or frame.size == 0:
            return []
        try:
            if self.enabled:
                return self._update(frame, tracks or {}, frame_idx)
            return self._update_boxes_only(frame, tracks or {}, frame_idx)
        except Exception as exc:  # noqa: BLE001 - nunca tumbar el frame loop
            logger.error("Analitica retail fallo: %s", exc, exc_info=True)
            return []

    def _update_boxes_only(self, frame: np.ndarray,
                           tracks: Dict[Any, Dict[str, Any]],
                           frame_idx: int) -> List[Dict[str, Any]]:
        """Sin planograma: SOLO cajas en el piso (deteccion siempre activa).

        Sin pasillos dibujados el filtro de piso cae a la heuristica de
        "mitad inferior del frame" (ver AisleBoxMonitor._on_floor). Los
        carritos se actualizan igualmente para excluir sus bboxes y no
        confundir un carrito con una caja.
        """
        self._last_tracks = tracks
        eventos: List[Dict[str, Any]] = []
        for ev in self.boxes.update(frame, frame_idx):
            eventos.append(ev)
            if self._logger is not None:
                self._logger.log(ev["evento"], ev)
        if eventos:
            self._snapshot_events(frame, eventos)
            self._events.extend(eventos)
            del self._events[:-500]
        return eventos

    def _update(self, frame: np.ndarray, tracks: Dict[Any, Dict[str, Any]],
                frame_idx: int) -> List[Dict[str, Any]]:
        h, w = frame.shape[:2]
        eventos: List[Dict[str, Any]] = []
        self._last_tracks = tracks   # lo usa draw() para el aviso REPONIENDO

        # active_tracks esta indexado POR track_id pero el track no lo lleva
        # dentro; se inyecta para poder consultar su demografia cacheada.
        for tid, track in tracks.items():
            track.setdefault("track_id", tid)

        # 0) PERSONAL registrado por foto: refrescar matches. Quien matchee
        #    una foto subida queda FUERA de todas las metricas de clientes.
        #    Sin fotos, este set esta vacio y nadie se etiqueta personal.
        self.staff.refresh()
        staff_pids = self.staff.staff_pids()

        # 1) Nivel de anaqueles (congelado bajo oclusion de personas)
        person_boxes = [t.get("box") for t in tracks.values()
                        if t.get("box") is not None]
        for alerta in self.stock.update(frame, person_boxes):
            alerta["evento"] = "anaquel_vacio"
            eventos.append(alerta)
            if self._logger is not None:
                self._logger.log("anaquel_vacio", alerta)

        # 2) Afluencia por pasillo (+ demografia de quien lo recorre).
        #    SIN el personal: un guardia patrullando todo el dia no debe
        #    convertir su ronda en "el pasillo mas transitado".
        tracks_clientes = {
            tid: t for tid, t in tracks.items()
            if str(t.get("persistent_id")) not in staff_pids}
        eventos.extend(self.aisles.update(tracks_clientes, w, h,
                                          self._demo_of))

        # 3) Recorrido de cada persona presente: segmento y pasillo actual
        #    (el personal no es un comprador: fuera del journey).
        active_pids: set = set()
        for track in tracks_clientes.values():
            pid = track.get("persistent_id")
            if not pid or str(pid).startswith("prov:"):
                continue
            pid = str(pid)
            active_pids.add(pid)
            demo = self._demo_of(track) or {}
            self.journey.touch_person(
                pid, demo.get("gender"), demo.get("age_range"),
                self.aisles.aisle_of(pid))

        # 4) Carritos: asignacion a dueno y depositos.
        depositos = self.carts.update(frame, tracks, frame_idx,
                                      self._hand_points(frame, tracks))

        # 5) Cajas de mercancia en el piso (+ cronometro de permanencia).
        #    OJO: los carritos YA NO se excluyen de la deteccion de cajas.
        #    El diablito con el que reponen los empleados suele detectarse
        #    como "trolley", y excluir sus cajas apiladas dejaba la
        #    reposicion SIN su senal principal (caja junto al empleado).
        #    Un carrito metalico puntua bajo como "cardboard" y ademas lo
        #    frenan los filtros de estatica/piso.
        for ev in self.boxes.update(frame, frame_idx):
            eventos.append(ev)
            if self._logger is not None:
                self._logger.log(ev["evento"], ev)

        # 6) Interaccion con anaqueles. AGARRE = inmediato (mano dentro del
        #    ROI); TOMA/DEVOLUCION/TOQUE = al soltar (por delta de llenado).
        shelf_events = self.shelves.update(frame, tracks, frame_idx)
        grab_events = [e for e in shelf_events
                       if e.get("evento") == EVENT_GRAB]
        outcome_events = [e for e in shelf_events
                          if e.get("evento") != EVENT_GRAB]

        # 7) Reposicion: caja + persona pegada + alcances al estante. Recibe
        #    TODOS los eventos: los AGARRES inmediatos cuentan alcances (los
        #    de resultado no se resuelven mientras el repositor ocluye el
        #    anaquel); EVENT_RETURN refina el conteo. "Verificado" = matchea
        #    una FOTO de personal subida (o el color de uniforme si esta
        #    activo); la conducta sola NUNCA etiqueta a alguien de empleado.
        for ev in self.restock.update(
                tracks, self.boxes.tracked_box_regions(), shelf_events,
                frame_shape=(h, w),
                uniform_check=self._staff_or_uniform_check(frame)):
            nombre = self.staff.name_for(ev.get("persistent_id"))
            if nombre:
                ev["empleado_nombre"] = nombre
                ev["empleado_verificado"] = True
            eventos.append(ev)
            if self._logger is not None:
                self._logger.log(ev["evento"], ev)
        # Excluir de COMPRAS/agarre-cliente al PERSONAL registrado, a quien
        # repone y a quien esta pegado a una caja (aunque la reposicion aun
        # no se haya confirmado): sus alcances no son de cliente.
        no_clientes = (staff_pids
                       | self.restock.active_restocker_pids()
                       | self.restock.near_box_pids())

        # 8) AGARRE de CLIENTE dentro del ROI del anaquel: senal inmediata
        #    "un cliente agarro un producto en el anaquel X". Se excluye a
        #    empleados/repositores.
        for ev in grab_events:
            if str(ev.get("persistent_id") or "") in no_clientes:
                continue
            self._grab_total += 1
            demo = self._demo_by_pid(tracks, ev.get("persistent_id"))
            ev = {**ev, "evento": "cliente_agarra_producto",
                  "genero": demo.get("gender"), "edad": demo.get("age_range")}
            eventos.append(ev)
            if self._logger is not None:
                self._logger.log("cliente_agarra_producto", ev)

        # 9) Analitica de COMPRA: solo de clientes. Los alcances de un
        #    empleado reponiendo NO cuentan como compra (si no, un repositor
        #    refilando el estante se leeria como un cliente devolviendo
        #    decenas de articulos e inflaria todo).
        for ev in outcome_events:
            if str(ev.get("persistent_id") or "") in no_clientes:
                continue
            self.journey.on_shelf_event(ev)
            eventos.append(ev)
            if self._logger is not None:
                self._logger.log("interaccion_anaquel", ev)

        for dep in depositos:
            confirmado = self.journey.on_cart_deposit(dep)
            if confirmado is not None:
                eventos.append(confirmado)
                if self._logger is not None:
                    self._logger.log("producto_al_carrito", confirmado)

        # 8b) Evaluacion de producto: cliente plantado de frente al estante
        #     (o que toca/agarra). Excluye a repositores y a quien esta
        #     pegado a una caja (esos no son clientes evaluando).
        eventos.extend(self._update_evaluations(
            tracks, shelf_events, w, h, no_clientes))

        # 9) Maquina consultora de precios (solo clientes: un empleado
        #    usando la consultora no es una senal de compra)
        eventos.extend(self._update_price_checkers(
            {tid: t for tid, t in tracks.items()
             if str(t.get("persistent_id")) not in no_clientes}, w, h))

        # 10) Cerrar recorridos de quienes ya se fueron (decide lo no devuelto)
        for fin in self.journey.close_idle(active_pids):
            eventos.append(fin)
            if self._logger is not None:
                self._logger.log("fin_recorrido", fin)

        if eventos:
            # Foto de evidencia ANTES de encolar: adjunta 'foto' al evento.
            self._snapshot_events(frame, eventos)
            self._events.extend(eventos)
            del self._events[:-500]  # cola acotada
        return eventos

    def _hand_points(self, frame: np.ndarray,
                     tracks: Dict[Any, Dict[str, Any]]
                     ) -> Dict[str, List[Tuple[float, float]]]:
        """Zonas de mano por persona, reusando la logica del detector de
        alcance (asi el carrito y el anaquel usan exactamente el mismo
        criterio de "donde esta la mano")."""
        out: Dict[str, List[Tuple[float, float]]] = {}
        for track in tracks.values():
            pid = track.get("persistent_id")
            if not pid:
                continue
            pts = self.shelves.reach_points(track, frame, [])
            if pts:
                out[str(pid)] = pts
        return out

    def _uniform_check(self, frame: np.ndarray):
        """Devuelve un callable(track)->bool que dice si el torso de la
        persona matchea el color de uniforme (SELLER_COLOR_HSV), o None si la
        deteccion por uniforme esta desactivada. Es un refuerzo opcional para
        confirmar que el repositor es un empleado; la senal principal es
        conductual (caja + alcances)."""
        if not getattr(self._cfg, "SELLER_COLOR_ENABLED", False):
            return None
        lo = np.array(self._cfg.SELLER_COLOR_HSV_LOWER, np.uint8)
        hi = np.array(self._cfg.SELLER_COLOR_HSV_UPPER, np.uint8)

        def check(track) -> bool:
            box = track.get("box")
            if box is None or frame is None or frame.size == 0:
                return False
            try:
                x1, y1, x2, y2 = [int(v) for v in box]
            except (TypeError, ValueError):
                return False
            # Tercio superior del cuerpo = torso (donde va el uniforme).
            ty2 = y1 + int((y2 - y1) * 0.4)
            hh, ww = frame.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, ty2 = min(ww, x2), min(hh, ty2)
            if x2 - x1 < 4 or ty2 - y1 < 4:
                return False
            torso = frame[y1:ty2, x1:x2]
            if torso.size == 0:
                return False
            hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, lo, hi)
            return (np.count_nonzero(mask) / float(mask.size)) >= 0.30

        return check

    def _update_evaluations(self, tracks: Dict[Any, Dict[str, Any]],
                            shelf_events: List[Dict[str, Any]],
                            w: int, h: int,
                            no_clientes: set) -> List[Dict[str, Any]]:
        """Emite "persona_evaluando" cuando un cliente se planta de frente a
        un anaquel un rato, o toca/agarra un producto.

        - "De frente" se aproxima por PROXIMIDAD del pie al anaquel sostenida
          EVAL_MIN_DWELL_S (a este angulo no se puede leer la mirada; un
          cliente parado frente al estante es la mejor senal disponible).
        - Un evento de estante (toma/toque/devolucion) de un cliente es una
          evaluacion inmediata (agarro el producto).
        - Una sola alerta por (persona, anaquel) y visita; al alejarse y
          volver, puede alertar de nuevo.
        """
        if not self._cfg.EVAL_ENABLED or not self.layout.shelves:
            return []
        now = time.time()
        min_dwell = float(self._cfg.EVAL_MIN_DWELL_S)
        near_k = float(self._cfg.EVAL_NEAR_FACTOR)
        grace = float(self._cfg.EVAL_END_GRACE_S)
        eventos: List[Dict[str, Any]] = []

        # Agarres/toques de clientes -> evaluacion inmediata (fuerza dwell).
        grabbed: set = set()
        for ev in shelf_events or []:
            pid = str(ev.get("persistent_id") or "")
            if pid and pid not in no_clientes:
                grabbed.add((pid, ev.get("anaquel")))

        presentes: set = set()
        for track in tracks.values():
            pid = track.get("persistent_id")
            box = track.get("box")
            if (not pid or box is None or str(pid).startswith("prov:")
                    or str(pid) in no_clientes):
                continue
            pid = str(pid)
            try:
                x1, y1, x2, y2 = [float(v) for v in box]
            except (TypeError, ValueError):
                continue
            fx, fy = (x1 + x2) / 2.0, y2            # pie
            radio = max(1.0, (y2 - y1)) * near_k
            for shelf in self.layout.shelves:
                agarro = (pid, shelf.nombre) in grabbed
                if not agarro and self._dist_to_shelf(
                        shelf, fx, fy, w, h) > radio:
                    continue
                key = (pid, shelf.nombre)
                presentes.add(key)
                st = self._eval_zone.get(key)
                if st is None:
                    st = {"inicio": now, "alertado": False, "last": now}
                    self._eval_zone[key] = st
                st["last"] = now
                dwell = now - st["inicio"]
                if not st["alertado"] and (agarro or dwell >= min_dwell):
                    st["alertado"] = True
                    self._eval_total += 1
                    demo = self._demo_of(track) or {}
                    ev = {
                        "evento": "persona_evaluando",
                        "persistent_id": pid,
                        "anaquel": shelf.nombre,
                        "producto": shelf.nombre,
                        "pasillo": shelf.pasillo,
                        "categoria": shelf.categoria,
                        "genero": demo.get("gender"),
                        "edad": demo.get("age_range"),
                        "agarro_producto": bool(agarro),
                        "segundos_frente": round(dwell, 1),
                        "timestamp": now,
                    }
                    eventos.append(ev)
                    if self._logger is not None:
                        self._logger.log("persona_evaluando", ev)

        # Cerrar evaluaciones cuya persona ya no esta de frente (tras gracia).
        for key in list(self._eval_zone.keys()):
            if key in presentes:
                continue
            if now - self._eval_zone[key]["last"] > grace:
                self._eval_zone.pop(key, None)
        return eventos

    @staticmethod
    def _dist_to_shelf(shelf, px: float, py: float, w: int, h: int) -> float:
        """Distancia (px) del punto al anaquel (0 si dentro de alguna parte)."""
        return shelf.dist_to_point(px, py, w, h)

    # ── Fotos de deteccion (evidencia por evento) ────────────────────

    def _snapshot_events(self, frame: np.ndarray,
                         eventos: List[Dict[str, Any]]) -> None:
        """Guarda una FOTO anotada por cada evento fotografiable y adjunta
        su ruta absoluta en ``ev['foto']``.

        output/detecciones/<evento>/<YYYY-MM-DD>/<HH-MM-SS>_<detalle>.jpg
        Con throttle por tipo (SNAPSHOT_MIN_INTERVAL_S) y retencion por
        dias. Defensivo: un fallo de disco jamas tumba el frame loop.
        """
        cfg = self._cfg
        if not getattr(cfg, "SNAPSHOT_EVENTS_ENABLED", False):
            return
        tipos = set(getattr(cfg, "SNAPSHOT_EVENT_TYPES", ()) or ())
        now = time.time()

        # UNA foto por (tipo, persona): registrar quien esta en escena y
        # re-armar a quien lleve SNAPSHOT_PERSON_RESET_S sin verse (salio
        # del ROI; si vuelve es una visita nueva y merece foto nueva).
        uno_por_persona = bool(getattr(cfg, "SNAPSHOT_ONE_PER_PERSON", False))
        if uno_por_persona:
            for t in self._last_tracks.values():
                p = t.get("persistent_id")
                if p:
                    self._snap_pid_seen[str(p)] = now
            reset_s = float(getattr(cfg, "SNAPSHOT_PERSON_RESET_S", 20.0))
            ausentes = {p for p, ts in self._snap_pid_seen.items()
                        if now - ts > reset_s}
            if ausentes:
                self._snap_pid_done = {
                    k: v for k, v in self._snap_pid_done.items()
                    if k[1] not in ausentes}
                for p in ausentes:
                    self._snap_pid_seen.pop(p, None)

        for ev in eventos:
            tipo = str(ev.get("evento") or "")
            if tipo not in tipos:
                continue
            pid = str(ev.get("persistent_id") or "")
            if uno_por_persona and pid and (tipo, pid) in self._snap_pid_done:
                continue    # esta persona ya tiene SU foto de este evento
            if (now - self._snap_last.get(tipo, 0.0)
                    < float(cfg.SNAPSHOT_MIN_INTERVAL_S)):
                continue
            self._snap_last[tipo] = now
            try:
                path = self._write_snapshot(frame, ev, tipo, now)
                if path:
                    ev["foto"] = path
                    if uno_por_persona and pid:
                        self._snap_pid_done[(tipo, pid)] = now
            except Exception as exc:  # noqa: BLE001
                logger.debug("snapshot de evento fallo: %s", exc)
        self._prune_snapshots(now)

    def _write_snapshot(self, frame: np.ndarray, ev: Dict[str, Any],
                        tipo: str, now: float) -> Optional[str]:
        """Escribe la foto anotada. Devuelve la ruta ABSOLUTA o None."""
        img = frame.copy()
        h, w = img.shape[:2]

        # Persona del evento (si la hay): caja amarilla.
        pid = str(ev.get("persistent_id") or "")
        if pid:
            for t in self._last_tracks.values():
                if str(t.get("persistent_id")) != pid or not t.get("box"):
                    continue
                x1, y1, x2, y2 = [int(v) for v in t["box"]]
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 220, 255), 2)
                break

        # Anaquel implicado: su(s) poligono(s) en verde.
        anaquel = ev.get("anaquel")
        if anaquel:
            s = self.layout.shelf_by_name(anaquel)
            if s is not None:
                cv2.polylines(img, s.polygons_px(w, h), True, (0, 255, 0), 2)

        # Caja implicada (eventos de caja): rectangulo marron.
        if tipo.startswith("caja"):
            cid = ev.get("caja_id")
            for b in self.boxes.tracked_box_regions():
                if b.get("id") == cid and b.get("box"):
                    x1, y1, x2, y2 = [int(v) for v in b["box"]]
                    cv2.rectangle(img, (x1, y1), (x2, y2), (19, 69, 139), 3)
                    break

        # Texto del evento (banda superior).
        detalle = anaquel or ev.get("pasillo") or ev.get("maquina") or ""
        texto = f"{tipo}" + (f" | {detalle}" if detalle else "")
        cv2.rectangle(img, (0, 0), (w, 26), (0, 0, 0), -1)
        cv2.putText(img, texto[:90], (8, 19), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 255, 255), 1)

        fecha = time.strftime("%Y-%m-%d", time.localtime(now))
        hora = time.strftime("%H-%M-%S", time.localtime(now))
        sufijo = safe_name(detalle) if detalle else safe_name(self.camera_id)
        out_dir = os.path.join(self._cfg.SNAPSHOT_DIR, safe_name(tipo), fecha)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.abspath(
            os.path.join(out_dir, f"{hora}_{sufijo}.jpg"))
        tmp = path + ".tmp.jpg"
        ok = cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY,
                                    int(self._cfg.SNAPSHOT_JPEG_QUALITY)])
        if not ok:
            return None
        os.replace(tmp, path)
        return path

    def _prune_snapshots(self, now: float) -> None:
        """Borra carpetas de fecha con mas de SNAPSHOT_KEEP_DAYS. Corre a lo
        sumo una vez por hora. Defensivo: nunca lanza."""
        if now - self._snap_prune_ts < 3600.0:
            return
        self._snap_prune_ts = now
        try:
            base = self._cfg.SNAPSHOT_DIR
            keep = max(1, int(self._cfg.SNAPSHOT_KEEP_DAYS))
            cutoff = now - keep * 86400.0
            if not os.path.isdir(base):
                return
            import shutil
            for tipo in os.listdir(base):
                tdir = os.path.join(base, tipo)
                if not os.path.isdir(tdir):
                    continue
                for fecha in os.listdir(tdir):
                    try:
                        # OJO: en Windows mktime lanza OverflowError (no
                        # ValueError) con fechas fuera de rango; sin
                        # atraparlo, UNA carpeta rara abortaba TODA la
                        # retencion en silencio.
                        ts = time.mktime(time.strptime(fecha, "%Y-%m-%d"))
                        if ts < cutoff:
                            shutil.rmtree(os.path.join(tdir, fecha),
                                          ignore_errors=True)
                    except (ValueError, OverflowError, OSError):
                        continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("retencion de snapshots fallo: %s", exc)

    def _staff_or_uniform_check(self, frame: np.ndarray):
        """callable(track)->bool: la persona es PERSONAL verificado.

        Verificado = su persistent_id matchea una FOTO de personal subida,
        o (si esta activo) el color de uniforme. Sin fotos y sin uniforme
        configurado, nadie se verifica -> las reposiciones se reportan como
        'persona' (nunca como 'empleado')."""
        uniform = self._uniform_check(frame)

        def check(track) -> bool:
            if self.staff.name_for(track.get("persistent_id")):
                return True
            return bool(uniform(track)) if uniform is not None else False

        return check

    def _update_price_checkers(self, tracks: Dict[Any, Dict[str, Any]],
                               w: int, h: int) -> List[Dict[str, Any]]:
        """Detecta permanencia delante de la maquina consultora de precios."""
        maquinas = self.layout.price_checkers()
        if not maquinas:
            return []
        now = time.time()
        min_dwell = float(self._cfg.PRICE_CHECK_MIN_DWELL_S)
        margen = float(self._cfg.PRICE_CHECK_MARGIN_RATIO)
        eventos: List[Dict[str, Any]] = []
        presentes: set = set()

        for track in tracks.values():
            pid = track.get("persistent_id")
            box = track.get("box")
            if not pid or box is None or str(pid).startswith("prov:"):
                continue
            pid = str(pid)
            try:
                x1, y1, x2, y2 = [float(v) for v in box]
            except (TypeError, ValueError):
                continue
            fx, fy = (x1 + x2) / 2.0, y2  # punto de pie
            for m in maquinas:
                mx1, my1, mx2, my2 = m.rect_px(w, h)
                dx = (mx2 - mx1) * margen
                dy = (my2 - my1) * margen
                ax1, ay1 = mx1 - dx, my1 - dy
                ax2, ay2 = mx2 + dx, my2 + dy
                # "Estar en la maquina" = el pie cae en su zona, O el cuerpo
                # se solapa con ella. Lo segundo hace que funcione tanto si
                # el operador dibujo el AREA DEL PISO delante de la maquina
                # como si dibujo LA MAQUINA en la pared: en camaras frontales
                # el pie de quien la usa queda por debajo del aparato.
                pie_dentro = (ax1 <= fx <= ax2 and ay1 <= fy <= ay2)
                solapa = (x1 < ax2 and x2 > ax1 and y1 < ay2 and y2 > ay1)
                if not (pie_dentro or solapa):
                    continue
                key = (pid, m.nombre)
                presentes.add(key)
                st = self._price_zone.get(key)
                if st is None:
                    self._price_zone[key] = {"inicio": now, "contado": False}
                    continue
                dwell = now - st["inicio"]
                if not st["contado"] and dwell >= min_dwell:
                    st["contado"] = True
                    ev = {
                        "evento": "consulta_precio", "persistent_id": pid,
                        "maquina": m.nombre, "duracion_s": round(dwell, 1),
                        "timestamp": now,
                    }
                    self.journey.on_price_check(ev)
                    eventos.append(ev)
                    if self._logger is not None:
                        self._logger.log("consulta_precio", ev)

        # Cerrar los que se fueron: acumular la duracion final
        for key in list(self._price_zone.keys()):
            if key in presentes:
                continue
            st = self._price_zone.pop(key)
            if st.get("contado"):
                continue
        return eventos

    def _demo_of(self, track: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Genero/edad ya calculados para ese track (sin re-clasificar)."""
        if self._demographics is None:
            return None
        tid = track.get("track_id") or track.get("tracker_id")
        if tid is None:
            return None
        try:
            return self._demographics.get_cached(int(tid))
        except Exception:  # noqa: BLE001
            return None

    def _demo_by_pid(self, tracks: Dict[Any, Dict[str, Any]],
                     pid: Any) -> Dict[str, str]:
        """Demografia del track cuyo persistent_id coincide (o {})."""
        for t in tracks.values():
            if str(t.get("persistent_id")) == str(pid):
                return self._demo_of(t) or {}
        return {}

    # ── Configuracion en runtime ─────────────────────────────────────

    def define_aisle(self, nombre: str, poligono, categoria: str = "") -> bool:
        self.layout.add_aisle(nombre, poligono, categoria)
        return self.layout.save()

    def define_shelf(self, nombre: str, rect, pasillo: str = "",
                     categoria: str = "", precio: float = 0.0,
                     sku: str = "") -> bool:
        self.layout.add_shelf(nombre, rect, pasillo, categoria, precio, sku)
        return self.layout.save()

    def define_price_checker(self, nombre: str, rect) -> bool:
        self.layout.add_fixture(nombre, rect, Fixture.TIPO_CONSULTA_PRECIO)
        return self.layout.save()

    def remove_zone(self, nombre: str) -> bool:
        ok = self.layout.remove(nombre)
        if ok:
            self.layout.save()
        return ok

    def capture_shelf_references(self, frame: np.ndarray) -> int:
        """Fija la foto de "estante lleno" de todos los anaqueles.

        Se llama con la tienda recien repuesta y SIN clientes delante: es lo
        que calibra el 100% de llenado contra el que se miden los faltantes.
        """
        return self.stock.set_all_references(frame)

    # ── Salidas ──────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Resumen liviano para el metadata de cada frame."""
        if not self.enabled:
            # Sin planograma la analitica de zonas esta inactiva, pero las
            # CAJAS siguen corriendo: sus stats y eventos viajan igual para
            # que el cliente alerte obstrucciones y muestre el estado.
            return {"activo": False, "motivo": "sin planograma definido",
                    "planograma": self.layout.path,
                    "cajas": self.boxes.get_stats(),
                    "eventos_recientes": self._events[-10:]}
        # Nombre del personal (verificado por foto) en las reposiciones.
        rep_stats = self.restock.get_stats()
        for r in rep_stats.get("reposiciones_en_curso") or []:
            nombre = self.staff.name_for(r.get("persistent_id"))
            if nombre:
                r["empleado_nombre"] = nombre
                r["empleado_verificado"] = True
        return {
            "activo": True,
            "pasillos": self.aisles.get_stats(),
            "anaqueles": self.stock.get_stats(),
            "interacciones": self.shelves.get_stats(),
            "carritos": self.carts.get_stats(),
            "compras": self.journey.get_stats(),
            "cajas": self.boxes.get_stats(),
            "reposicion": rep_stats,
            "personal": self.staff.get_stats(),
            "evaluacion": {
                "evaluando_ahora": sum(
                    1 for s in self._eval_zone.values() if s.get("alertado")),
                "evaluaciones_totales": int(self._eval_total),
                "agarres_cliente": int(self._grab_total),
            },
            "eventos_recientes": self._events[-10:],
        }

    def get_report(self, force: bool = False) -> Dict[str, Any]:
        """Reporte completo de marketing (cacheado y throttled)."""
        if not self.enabled:
            return {"activo": False}
        now = time.time()
        if (not force and self._cached_report is not None
                and now - self._last_report_ts
                < float(self._cfg.RETAIL_REPORT_EVERY_S)):
            return self._cached_report
        self._last_report_ts = now
        aisle_stats = self.aisles.get_stats()
        stock_stats = self.stock.get_stats()
        self._cached_report = {
            "activo": True,
            "camera_id": str(self.camera_id),
            "generado": now,
            "generado_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trafico_pasillos": aisle_stats,
            "stock": {
                "anaqueles_vacios": stock_stats["anaqueles_vacios"],
                "anaqueles_bajos": stock_stats["anaqueles_bajos"],
                "detalle": stock_stats["anaqueles"],
            },
            "ventas": self.journey.full_report(),
            "interacciones": self.shelves.get_stats(),
            "carritos": self.carts.get_stats(),
            "cajas": {
                **self.boxes.get_stats(),
                "historial": self.boxes.recent_finished(),
            },
            "reposicion_empleados": {
                **self.restock.get_stats(),
                "historial": self.restock.recent_finished(),
            },
            # Para el dashboard de tienda (seguridad + marketing):
            "evaluacion": {
                "evaluando_ahora": sum(
                    1 for s in self._eval_zone.values()
                    if s.get("alertado")),
                "evaluaciones_totales": int(self._eval_total),
                "agarres_cliente": int(self._grab_total),
            },
            # Merodeo: personas con permanencia ALTA en un pasillo (posible
            # vigilancia / interes sostenido). Umbral configurable.
            "merodeo": self._merodeo_stats(aisle_stats),
            "personal": self.staff.get_stats(),
            "eventos_recientes": self._events[-30:],
        }
        return self._cached_report

    def _merodeo_stats(self, aisle_stats: Dict[str, Any]) -> Dict[str, Any]:
        """Personas 'merodeando': permanencia media del pasillo por encima del
        umbral (seguridad) + pasillos con alta ocupacion sostenida."""
        umbral = float(getattr(self._cfg, "MERODEO_DWELL_S", 90.0))
        pasillos = (aisle_stats or {}).get("pasillos") or []
        sospechosos = [
            {"pasillo": p.get("pasillo"),
             "permanencia_media_s": p.get("permanencia_media_s", 0),
             "visitantes_unicos": p.get("visitantes_unicos", 0),
             "ocupacion_pico": p.get("ocupacion_pico", 0)}
            for p in pasillos
            if float(p.get("permanencia_media_s", 0) or 0) >= umbral
        ]
        sospechosos.sort(key=lambda x: x["permanencia_media_s"], reverse=True)
        return {"umbral_s": umbral, "pasillos_con_merodeo": sospechosos,
                "hay_merodeo": bool(sospechosos)}

    def save_report(self, path: str = None) -> bool:
        """Persiste el reporte a JSON (lo consume el dashboard)."""
        if not self.enabled:
            return False
        try:
            path = path or os.path.join(
                self._cfg.RETAIL_REPORT_DIR,
                f"{safe_name(self.camera_id)}.json")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.get_report(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            self._last_saved_ts = self._last_report_ts
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("No se pudo guardar reporte retail: %s", exc)
            return False

    def maybe_save_report(self, path: str = None) -> bool:
        """Guarda el reporte SOLO si se regenero desde el ultimo guardado.

        Pensado para llamarse en cada frame sin coste: el reporte se
        recalcula cada RETAIL_REPORT_EVERY_S, y solo entonces se escribe.
        """
        if not self.enabled or self._last_report_ts <= self._last_saved_ts:
            return False
        return self.save_report(path)

    # ── Overlay ──────────────────────────────────────────────────────

    def draw(self, image: np.ndarray) -> np.ndarray:
        """Pinta el overlay de retail.

        Las CAJAS en el piso se pintan SIEMPRE (esten o no las zonas del
        planograma definidas); pasillos/anaqueles/maquinas solo con
        planograma.
        """
        if image is None or image.size == 0:
            return image
        if self.enabled:
            image = self._draw_zonas(image)
        return self._draw_cajas(image)

    def _draw_zonas(self, image: np.ndarray) -> np.ndarray:
        """Pasillos, anaqueles (por estado), maquinas de precio y carritos."""
        try:
            h, w = image.shape[:2]
            ocupacion = self.aisles.occupancy_map()

            for a in self.layout.aisles:
                poly = a.polygon_px(w, h)
                n = ocupacion.get(a.nombre, 0)
                color = (0, 200, 255) if n else (120, 120, 120)
                cv2.polylines(image, [poly], True, color, 2)
                x, y = int(poly[:, 0].min()), int(poly[:, 1].min())
                cv2.putText(image, f"{a.nombre}: {n}", (x + 4, max(14, y - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            for s in self.layout.shelves:
                if s.status == STATUS_EMPTY:
                    color = (0, 0, 255)
                elif s.status == STATUS_LOW:
                    color = (0, 165, 255)
                else:
                    color = (0, 255, 0)
                partes = s.polygons_px(w, h)
                cv2.polylines(image, partes, True, color, 2)
                x1, y1, _x2, _y2 = s.rect_px(w, h)
                cv2.putText(image, f"{s.nombre} {s.fill_ratio:.0%}",
                            (x1 + 3, max(12, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

            for f_ in self.layout.fixtures:
                x1, y1, x2, y2 = f_.rect_px(w, h)
                cv2.rectangle(image, (x1, y1), (x2, y2), (255, 0, 255), 2)
                cv2.putText(image, f_.nombre, (x1 + 3, max(12, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1)

            for c in self.carts.boxes():
                x1, y1, x2, y2 = [int(v) for v in c["box"]]
                cv2.rectangle(image, (x1, y1), (x2, y2), (255, 200, 0), 2)
                cv2.putText(image, c["label"], (x1 + 3, max(12, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Overlay retail (zonas) fallo: %s", exc)
        return image

    def _draw_cajas(self, image: np.ndarray) -> np.ndarray:
        """Cajas en el piso + aviso REPONIENDO. Corre SIEMPRE."""
        try:
            # Cajas en el piso: marron, con su cronometro. Rojo si obstruye.
            restockers = self.restock.active_restocker_pids()
            for b in self.boxes.boxes_overlay():
                x1, y1, x2, y2 = [int(v) for v in b["box"]]
                color = (0, 0, 200) if b["obstruccion"] else (19, 69, 139)
                cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
                seg = int(b["segundos"])
                txt = f"Caja {seg // 60}m{seg % 60:02d}s"
                cv2.putText(image, txt, (x1 + 3, max(12, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)

            # Aviso de reposicion en curso (junto a quien repone).
            if restockers:
                for track in getattr(self, "_last_tracks", {}).values():
                    if str(track.get("persistent_id")) not in restockers:
                        continue
                    bx = track.get("box")
                    if not bx:
                        continue
                    x1, y1 = int(bx[0]), int(bx[1])
                    cv2.putText(image, "REPONIENDO", (x1, max(24, y1 - 18)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 215, 255), 2)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Overlay retail (cajas) fallo: %s", exc)
        return image

    def reset(self) -> None:
        self.aisles.reset()
        self.stock.reset()
        self.shelves.reset()
        self.carts.reset()
        self.journey.reset()
        self.boxes.reset()
        self.restock.reset()
        self._price_zone.clear()
        self._eval_zone.clear()
        self._eval_total = 0
        self._grab_total = 0
        self._events.clear()
        self._cached_report = None
