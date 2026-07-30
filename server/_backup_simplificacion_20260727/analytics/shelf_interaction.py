"""
analytics/shelf_interaction.py - Interaccion persona <-> anaquel.

Detecta que alguien ALCANZA un estante y decide si TOMO un producto, lo
DEVOLVIO, o solo lo miro/toco. Es la senal base de toda la analitica de
ventas: sin esto solo se sabe que la gente pasa, no que hace.

Como funciona (sin necesidad de entrenar un clasificador de SKU):

  1. ``ShelfStockTracker`` mide el nivel de llenado de cada anaquel frame a
     frame, comparando su DENSIDAD DE BORDES contra la de una foto de
     referencia del estante lleno: cada producto aporta contornos y un
     estante vacio deja ver el fondo liso.

     CLAVE: el nivel se CONGELA mientras una persona ocluye el anaquel. Sin
     esto, un cliente parado delante del estante se leeria como "estante
     vacio" y dispararia alertas falsas todo el dia.

  2. ``ShelfInteractionDetector.reach_points`` decide si la mano de una
     persona entra en el rect del anaquel. Si hay modelo de pose disponible
     usa las MUNECAS (preciso); si no, aproxima la zona de alcance
     extendiendo lateralmente el bbox a la altura del pecho.

  3. La maquina de estados compara el llenado ESTABLE de antes del alcance
     contra el de despues (ya des-ocluido y asentado):

         delta <= -umbral  ->  TOMA      (se llevo algo del estante)
         delta >= +umbral  ->  DEVOLUCION (repuso algo)
         |delta| < umbral  ->  TOQUE     (lo miro y lo dejo igual)

  El PRODUCTO es el nombre del anaquel (planograma). Es el enfoque estandar
  en retail: la posicion define el SKU, y es mucho mas fiable que intentar
  reconocer visualmente un producto de 3 cm en una camara de techo.

Limitaciones conocidas (documentadas a proposito):
  - Si dos personas alcanzan el MISMO anaquel a la vez, el delta no se
    puede atribuir a una sola: el evento se marca ``ambiguo=True`` y no
    entra en las metricas de conversion.
  - Un producto muy pequeno respecto al anaquel puede no mover el llenado
    lo suficiente: se emite TOQUE. Subdividir el anaquel en ROIs mas
    pequenos mejora la sensibilidad.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import AnalyticsConfig
from .store_layout import Shelf, StoreLayout

logger = logging.getLogger(__name__)

# Tipos de evento
EVENT_GRAB = "AGARRE"        # inmediato: la mano entro en el ROI del anaquel
EVENT_PICKUP = "TOMA"        # al soltar: el nivel bajo (se llevo una unidad)
EVENT_RETURN = "DEVOLUCION"  # al soltar: el nivel subio (devolvio)
EVENT_TOUCH = "TOQUE"        # al soltar: sin cambio medible (solo lo toco)

# Estados de stock del anaquel
STATUS_OK = "OK"
STATUS_LOW = "BAJO"
STATUS_EMPTY = "VACIO"


class ShelfStockTracker:
    """Nivel de llenado por anaquel, robusto a oclusion por personas."""

    def __init__(self, layout: StoreLayout, config: AnalyticsConfig = None):
        cfg = config or AnalyticsConfig
        self._layout = layout
        self._cfg = cfg
        self._ref_dir = cfg.SHELF_REFERENCE_DIR
        self._th_low = float(cfg.STOCK_THRESHOLD_LOW)
        self._th_ok = float(cfg.STOCK_THRESHOLD_OK)
        self._stable_n = max(2, int(cfg.SHELF_STABLE_FRAMES))
        # nombre -> referencia del estante LLENO: {'bordes': densidad de
        # bordes, 'hist': histograma HSV}. Ver _fill_ratio.
        self._refs: Dict[str, Dict[str, Any]] = {}
        # nombre -> deque de lecturas recientes NO ocluidas
        self._readings: Dict[str, deque] = {}
        # nombre -> ultimo nivel ESTABLE (mediana de lecturas limpias)
        self._stable: Dict[str, float] = {}
        self._occluded: Dict[str, bool] = {}
        self._alerts: List[Dict[str, Any]] = []
        try:
            os.makedirs(self._ref_dir, exist_ok=True)
        except OSError as exc:
            logger.warning("No se pudo crear %s: %s", self._ref_dir, exc)
        self._load_references()

    # ── Referencias (estante lleno) ──────────────────────────────────

    def _ref_path(self, nombre: str) -> str:
        safe = "".join(c for c in nombre if c.isalnum() or c in "_- ")[:64]
        return os.path.join(self._ref_dir, f"{safe}.jpg")

    def _load_references(self) -> None:
        for s in self._layout.shelves:
            p = self._ref_path(s.nombre)
            if not os.path.exists(p):
                continue
            img = cv2.imread(p)
            if img is not None and img.size > 0:
                # La foto guardada es la bbox; se reconstruye la mascara del
                # poligono a ese tamano para medir solo el estante.
                mask = s.local_mask(img.shape[1], img.shape[0])
                self._refs[s.nombre] = self._reference_of(img, mask)
                logger.info("Referencia de anaquel cargada: %s", s.nombre)

    def set_reference(self, frame: np.ndarray, shelf_name: str) -> bool:
        """Guarda la foto del anaquel LLENO como referencia de nivel 1.0.

        Se llama cuando el operador confirma que el estante esta repuesto.
        """
        shelf = self._layout.shelf_by_name(shelf_name)
        if shelf is None:
            return False
        crop, mask = shelf.crop_and_mask(frame)
        if crop is None:
            return False
        self._refs[shelf_name] = self._reference_of(crop, mask)
        try:
            cv2.imwrite(self._ref_path(shelf_name), crop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo guardar referencia %s: %s",
                           shelf_name, exc)
        logger.info("Referencia de anaquel fijada: %s", shelf_name)
        return True

    def set_all_references(self, frame: np.ndarray) -> int:
        """Fija la referencia de TODOS los anaqueles con el frame actual."""
        n = 0
        for s in self._layout.shelves:
            if self.set_reference(frame, s.nombre):
                n += 1
        return n

    # ── Medicion por frame ───────────────────────────────────────────

    def update(self, frame: np.ndarray,
               person_boxes: List[Any]) -> List[Dict[str, Any]]:
        """Mide el llenado de cada anaquel ignorando los ocluidos.

        Args:
            frame: frame BGR completo.
            person_boxes: cajas (x1,y1,x2,y2) de las personas del frame.

        Returns:
            Alertas nuevas de anaquel VACIO.
        """
        if frame is None or frame.size == 0 or not self._layout.shelves:
            return []
        h, w = frame.shape[:2]
        alerts: List[Dict[str, Any]] = []
        occ_th = float(self._cfg.SHELF_OCCLUSION_IOA)

        for shelf in self._layout.shelves:
            rect = shelf.rect_px(w, h)
            if rect[2] - rect[0] < 4 or rect[3] - rect[1] < 4:
                continue
            ocluido = self._is_occluded(rect, person_boxes, occ_th)
            self._occluded[shelf.nombre] = ocluido
            if ocluido:
                # Nivel CONGELADO: no se mide con alguien delante.
                continue

            crop, mask = shelf.crop_and_mask(frame)
            if crop is None:
                continue
            nivel = self._fill_ratio(shelf.nombre, crop, mask)
            dq = self._readings.setdefault(
                shelf.nombre, deque(maxlen=self._stable_n * 2))
            dq.append(float(nivel))
            if len(dq) >= self._stable_n:
                estable = float(np.median(list(dq)[-self._stable_n:]))
                self._stable[shelf.nombre] = estable
                prev_status = shelf.status
                shelf.fill_ratio = estable
                shelf.status = self._status_for(estable)
                shelf.last_update = time.time()
                if (shelf.status == STATUS_EMPTY
                        and prev_status != STATUS_EMPTY):
                    alert = {
                        "anaquel": shelf.nombre,
                        "producto": shelf.nombre,
                        "pasillo": shelf.pasillo,
                        "estado": shelf.status,
                        "fill_ratio": round(estable, 3),
                        "timestamp": time.time(),
                    }
                    alerts.append(alert)
                    self._alerts.append(alert)
                    logger.warning("ANAQUEL VACIO: %s (%.0f%%)",
                                   shelf.nombre, estable * 100)
        return alerts

    def _status_for(self, nivel: float) -> str:
        if nivel >= self._th_ok:
            return STATUS_OK
        if nivel >= self._th_low:
            return STATUS_LOW
        return STATUS_EMPTY

    @staticmethod
    def _is_occluded(rect: Tuple[int, int, int, int],
                     person_boxes: List[Any], ioa_th: float) -> bool:
        """True si alguna persona cubre >= ioa_th del area del anaquel."""
        x1, y1, x2, y2 = rect
        area = float(max(1, (x2 - x1) * (y2 - y1)))
        for b in person_boxes or []:
            try:
                bx1, by1, bx2, by2 = [float(v) for v in b]
            except (TypeError, ValueError):
                continue
            iw = max(0.0, min(x2, bx2) - max(x1, bx1))
            ih = max(0.0, min(y2, by2) - max(y1, by1))
            if (iw * ih) / area >= ioa_th:
                return True
        return False

    def _fill_ratio(self, nombre: str, crop: np.ndarray,
                    mask: np.ndarray = None) -> float:
        """Nivel de llenado 0..1 del anaquel, medido SOLO dentro del poligono
        (la ``mask``), para no incluir la esquina del pasillo que cae en la
        bbox de un poligono inclinado.

        La senal principal es la DENSIDAD DE BORDES relativa a la del estante
        lleno: cada producto aporta contornos, y un estante vacio deja ver el
        fondo liso. Es lo que correlaciona con "cuantas unidades quedan".
        (El histograma HSV se conserva solo como desempate menor: quitar
        medio estante apenas lo mueve.)
        """
        ref = self._refs.get(nombre)
        bordes = self._edge_density(crop, mask)
        if ref is None:
            # Sin calibrar: un estante lleno ronda el 20% de bordes.
            return float(min(1.0, bordes / 0.20))
        ref_bordes = float(ref.get("bordes") or 0.0)
        if ref_bordes <= 1e-6:
            return 1.0
        nivel = bordes / ref_bordes
        # Matiz por color: si ademas cambio mucho el histograma, el estante
        # se vacio de verdad (y no es solo un cambio de iluminacion).
        try:
            corr = cv2.compareHist(ref["hist"], self._histogram(crop, mask),
                                   cv2.HISTCMP_CORREL)
            nivel = 0.85 * nivel + 0.15 * max(0.0, float(corr))
        except Exception:  # noqa: BLE001
            pass
        return float(max(0.0, min(1.0, nivel)))

    @classmethod
    def _reference_of(cls, crop: np.ndarray,
                      mask: np.ndarray = None) -> Dict[str, Any]:
        """Firma del estante LLENO: densidad de bordes + histograma (dentro
        de la mascara del poligono)."""
        return {"bordes": cls._edge_density(crop, mask),
                "hist": cls._histogram(crop, mask)}

    @staticmethod
    def _edge_density(crop: np.ndarray, mask: np.ndarray = None) -> float:
        """Fraccion de pixeles-borde DENTRO del poligono. Proporcional a
        cuanto producto hay. Con mascara, los bordes del borde del recorte no
        cuentan y el denominador es el area real del poligono."""
        if crop is None or crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        if mask is not None and mask.shape[:2] == edges.shape[:2]:
            area = float(np.count_nonzero(mask))
            if area < 1:
                return 0.0
            edges = cv2.bitwise_and(edges, edges, mask=mask)
            return float(np.count_nonzero(edges)) / area
        return float(np.count_nonzero(edges)) / float(max(1, edges.size))

    @staticmethod
    def _histogram(crop: np.ndarray, mask: np.ndarray = None) -> np.ndarray:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        m = mask if (mask is not None and mask.shape[:2] == hsv.shape[:2]) else None
        hist = cv2.calcHist([hsv], [0, 1], m, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    # ── Consultas ────────────────────────────────────────────────────

    def stable_level(self, nombre: str) -> Optional[float]:
        """Ultimo nivel estable (no ocluido) del anaquel, o None."""
        return self._stable.get(nombre)

    def is_occluded(self, nombre: str) -> bool:
        return bool(self._occluded.get(nombre, False))

    def get_stats(self) -> Dict[str, Any]:
        anaqueles = [s.state_dict() for s in self._layout.shelves]
        vacios = [a["nombre"] for a in anaqueles if a["estado"] == STATUS_EMPTY]
        bajos = [a["nombre"] for a in anaqueles if a["estado"] == STATUS_LOW]
        return {
            "anaqueles": anaqueles,
            "anaqueles_vacios": vacios,
            "anaqueles_bajos": bajos,
            "total_anaqueles": len(anaqueles),
            "con_referencia": len(self._refs),
            "alertas_totales": len(self._alerts),
        }

    def reset(self) -> None:
        self._readings.clear()
        self._stable.clear()
        self._occluded.clear()
        self._alerts.clear()


class _PoseHelper:
    """Estimador de pose OPCIONAL para localizar las munecas.

    Mejora mucho la precision del alcance (se sabe donde esta la mano en vez
    de suponerla). Carga perezosa: si el modelo no esta o falla, todo el
    sistema sigue con la heuristica del bbox.
    """

    # Indices COCO-17: 9 = muneca izquierda, 10 = muneca derecha
    WRIST_IDS = (9, 10)

    def __init__(self, weights: str, device: str = "cpu",
                 conf: float = 0.35):
        self._weights = weights
        self._device = device
        self._conf = float(conf)
        self._model = None
        self._failed = False

    @property
    def available(self) -> bool:
        return not self._failed

    def _ensure(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        try:
            from ultralytics import YOLO
            self._model = YOLO(self._weights)
            logger.info("Modelo de pose cargado para alcance de mano: %s",
                        self._weights)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Pose no disponible (%s); se usara la heuristica de bbox "
                "para el alcance a estantes.", exc)
            self._failed = True
            return False

    def wrists(self, frame: np.ndarray) -> List[Tuple[float, float]]:
        """Munecas visibles en el frame, en px."""
        if frame is None or frame.size == 0 or not self._ensure():
            return []
        try:
            res = self._model.predict(frame, conf=self._conf, verbose=False,
                                      device=self._device)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pose predict fallo: %s", exc)
            return []
        pts: List[Tuple[float, float]] = []
        for r in res:
            kp = getattr(r, "keypoints", None)
            if kp is None or kp.xy is None:
                continue
            arr = kp.xy.cpu().numpy() if hasattr(kp.xy, "cpu") else np.asarray(kp.xy)
            for person in arr:
                for wid in self.WRIST_IDS:
                    if wid < len(person):
                        x, y = float(person[wid][0]), float(person[wid][1])
                        if x > 0 and y > 0:
                            pts.append((x, y))
        return pts


class ShelfInteractionDetector:
    """Detecta TOMA / DEVOLUCION / TOQUE de producto por persona y anaquel."""

    def __init__(self, layout: StoreLayout, stock: ShelfStockTracker,
                 config: AnalyticsConfig = None, device: str = "cpu"):
        cfg = config or AnalyticsConfig
        self._layout = layout
        self._stock = stock
        self._cfg = cfg
        self._min_frames = max(1, int(cfg.REACH_MIN_FRAMES))
        self._settle_frames = max(1, int(cfg.REACH_SETTLE_FRAMES))
        self._pickup_delta = float(cfg.PICKUP_FILL_DELTA)
        self._reach_margin = float(cfg.REACH_MARGIN_RATIO)
        self._pose: Optional[_PoseHelper] = None
        if cfg.REACH_USE_POSE:
            self._pose = _PoseHelper(cfg.POSE_MODEL_PATH, device,
                                     cfg.POSE_CONF)
        # (pid, anaquel) -> estado del alcance en curso
        self._active: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # alcances terminados esperando a que el anaquel se asiente
        self._pending: List[Dict[str, Any]] = []
        self._events: List[Dict[str, Any]] = []

    # ── Zona de alcance ──────────────────────────────────────────────

    def reach_points(self, track: Dict[str, Any], frame: np.ndarray,
                      wrists: List[Tuple[float, float]]
                      ) -> List[Tuple[float, float]]:
        """Puntos candidatos a "mano" de esta persona.

        Con pose: las munecas detectadas DENTRO (o justo al borde) del bbox
        de la persona. Sin pose: los dos extremos laterales del tercio
        superior del bbox, extendidos por REACH_MARGIN_RATIO -- que es
        donde cae la mano de alguien que estira el brazo a un estante.
        """
        box = track.get("box")
        if box is None:
            return []
        try:
            x1, y1, x2, y2 = [float(v) for v in box]
        except (TypeError, ValueError):
            return []
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)

        if wrists:
            pad = bw * 0.5
            dentro = [(wx, wy) for wx, wy in wrists
                      if (x1 - pad) <= wx <= (x2 + pad)
                      and (y1 - bh * 0.1) <= wy <= (y2 + bh * 0.1)]
            if dentro:
                return dentro

        # Heuristica: altura del hombro/pecho (~30% desde arriba), brazo
        # extendido a izquierda y derecha.
        ry = y1 + bh * 0.30
        ext = bw * self._reach_margin
        return [(x1 - ext, ry), (x2 + ext, ry),
                ((x1 + x2) / 2.0, ry)]

    # ── Actualizacion por frame ──────────────────────────────────────

    def update(self, frame: np.ndarray, tracks: Dict[Any, Dict[str, Any]],
               frame_idx: int = 0) -> List[Dict[str, Any]]:
        """Procesa alcances y resuelve los eventos.

        Returns:
            - AGARRE: inmediato, cuando la mano entra en el ROI del anaquel.
            - TOMA / DEVOLUCION / TOQUE: al soltar, segun el delta de llenado.
        """
        if frame is None or frame.size == 0 or not self._layout.shelves:
            return []
        h, w = frame.shape[:2]
        now = time.time()
        eventos: List[Dict[str, Any]] = []

        wrists: List[Tuple[float, float]] = []
        if (self._pose is not None and self._pose.available
                and frame_idx % max(1, int(self._cfg.POSE_EVERY_N)) == 0):
            wrists = self._pose.wrists(frame)

        # Se acepta el contacto si la mano cae dentro de CUALQUIER parte del
        # anaquel o muy cerca de su borde (margen en px).
        margen = float(self._cfg.REACH_TOUCH_MARGIN_PX)

        # 1) Que (persona, anaquel) estan en contacto ahora.
        #    CLAVE = (track_id, anaquel), NO el persistent_id: el pid cambia
        #    de 'prov:X' al uuid cuando el rostro se confirma, y con clave
        #    por pid ese flip cerraba y reabria el alcance -> DOBLE agarre
        #    del mismo contacto. El track_id de ByteTrack es estable mientras
        #    la persona este a la vista; el pid VIGENTE se guarda en el
        #    estado y es el que sale en los eventos.
        tocando: set = set()
        pid_por_par: Dict[Tuple[Any, str], Dict[str, Any]] = {}
        for track in tracks.values():
            pid = track.get("persistent_id")
            if not pid:
                continue
            pid = str(pid)
            tkey = track.get("track_id")
            if tkey is None:
                tkey = pid          # fallback (llamadas sin track_id)
            pts = self.reach_points(track, frame, wrists)
            if not pts:
                continue
            for shelf in self._layout.shelves:
                if any(shelf.dist_to_point(px, py, w, h) <= margen
                       for px, py in pts):
                    key = (tkey, shelf.nombre)
                    tocando.add(key)
                    pid_por_par[key] = track

        # 2) Abrir / mantener alcances. Al CONFIRMARSE la mano dentro del ROI
        #    (abierto), se emite YA un AGARRE (senal inmediata "metio la mano
        #    en el anaquel X"), sin esperar a medir el delta de llenado.
        for key in tocando:
            pid_actual = str(pid_por_par[key].get("persistent_id"))
            st = self._active.get(key)
            if st is None:
                # Nivel ANTES: el ultimo estable medido sin oclusion.
                self._active[key] = {
                    "frames": 1,
                    "inicio": now,
                    "fill_before": self._stock.stable_level(key[1]),
                    "abierto": False,
                    "grab_emitido": False,
                    "pid": pid_actual,
                }
            else:
                st["frames"] += 1
                st["pid"] = pid_actual   # pid VIGENTE (puede confirmarse)
                if not st["abierto"] and st["frames"] >= self._min_frames:
                    st["abierto"] = True
                if st["abierto"] and not st["grab_emitido"]:
                    st["grab_emitido"] = True
                    ev = self._grab_event(pid_actual, key[1], now)
                    if ev is not None:
                        eventos.append(ev)
                        self._events.append(ev)

        # 3) Cerrar alcances terminados -> pasan a "pendiente de asentar"
        for key in list(self._active.keys()):
            if key in tocando:
                continue
            st = self._active.pop(key)
            if not st.get("abierto"):
                continue  # roce de 1-2 frames: ruido, se descarta
            _tkey, shelf_name = key
            pid = st.get("pid", "")
            # Ambiguo si otra persona tocaba el MISMO anaquel a la vez.
            otros = [k for k in list(self._active.keys()) + list(tocando)
                     if k[1] == shelf_name and k[0] != _tkey]
            self._pending.append({
                "persistent_id": pid,
                "anaquel": shelf_name,
                "fill_before": st.get("fill_before"),
                "inicio": st["inicio"],
                "fin": now,
                "duracion": max(0.0, now - st["inicio"]),
                "settle": 0,
                "ambiguo": bool(otros),
            })

        # 4) Resolver pendientes cuyo anaquel ya se estabilizo des-ocluido
        aun_pendientes = []
        for p in self._pending:
            if self._stock.is_occluded(p["anaquel"]):
                # Sigue alguien delante: no se puede medir todavia.
                if now - p["fin"] <= float(self._cfg.REACH_RESOLVE_TIMEOUT_S):
                    aun_pendientes.append(p)
                continue
            p["settle"] += 1
            if p["settle"] < self._settle_frames:
                aun_pendientes.append(p)
                continue
            ev = self._resolve(p, now)
            if ev is not None:
                eventos.append(ev)
                self._events.append(ev)
        self._pending = aun_pendientes
        return eventos

    def _grab_event(self, pid: str, shelf_name: str,
                    now: float) -> Optional[Dict[str, Any]]:
        """Evento AGARRE: la mano de la persona entro en el ROI del anaquel."""
        shelf = self._layout.shelf_by_name(shelf_name)
        if shelf is None:
            return None
        return {
            "evento": EVENT_GRAB,
            "persistent_id": pid,
            "producto": shelf.nombre,
            "anaquel": shelf.nombre,
            "pasillo": shelf.pasillo,
            "categoria": shelf.categoria,
            "sku": shelf.sku,
            "precio": shelf.precio,
            "timestamp": now,
        }

    def _resolve(self, p: Dict[str, Any],
                 now: float) -> Optional[Dict[str, Any]]:
        """Decide TOMA / DEVOLUCION / TOQUE por el delta de llenado."""
        shelf = self._layout.shelf_by_name(p["anaquel"])
        if shelf is None:
            return None
        after = self._stock.stable_level(p["anaquel"])
        before = p.get("fill_before")
        if after is None or before is None:
            # Sin medicion fiable: se registra el contacto, no el resultado.
            tipo, delta = EVENT_TOUCH, 0.0
        else:
            delta = float(after) - float(before)
            if delta <= -self._pickup_delta:
                tipo = EVENT_PICKUP
            elif delta >= self._pickup_delta:
                tipo = EVENT_RETURN
            else:
                tipo = EVENT_TOUCH
        return {
            "evento": tipo,
            "persistent_id": p["persistent_id"],
            "producto": shelf.nombre,
            "anaquel": shelf.nombre,
            "pasillo": shelf.pasillo,
            "categoria": shelf.categoria,
            "sku": shelf.sku,
            "precio": shelf.precio,
            "duracion_s": round(float(p["duracion"]), 2),
            "delta_llenado": round(float(delta), 4),
            "nivel_antes": (round(float(before), 3)
                            if before is not None else None),
            "nivel_despues": (round(float(after), 3)
                              if after is not None else None),
            "ambiguo": bool(p.get("ambiguo")),
            "timestamp": now,
        }

    # ── Consultas ────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        n = {t: 0 for t in (EVENT_GRAB, EVENT_PICKUP, EVENT_RETURN,
                            EVENT_TOUCH)}
        for e in self._events:
            if e["evento"] in n:
                n[e["evento"]] += 1
        return {
            "total_agarres": n[EVENT_GRAB],
            "total_tomas": n[EVENT_PICKUP],
            "total_devoluciones": n[EVENT_RETURN],
            "total_toques": n[EVENT_TOUCH],
            "alcances_en_curso": len(self._active),
            "pendientes_de_resolver": len(self._pending),
            "usa_pose": bool(self._pose is not None
                             and self._pose.available),
        }

    def recent_events(self, n: int = 20) -> List[Dict[str, Any]]:
        return self._events[-n:]

    def reset(self) -> None:
        self._active.clear()
        self._pending.clear()
        self._events.clear()
