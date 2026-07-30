"""
analytics/box_monitor.py - Cajas de mercancia en el piso del pasillo.

Detecta cajas de carton / mercancia DEJADAS EN EL SUELO del pasillo y
cronometra cuanto tiempo permanece cada una, desde que aparece hasta que la
retiran. Sirve para dos cosas de operacion:

  * Obstruccion: una caja bloqueando el pasillo demasiado tiempo (molesta al
    cliente, riesgo de tropiezo) -> alerta configurable.
  * Reposicion: es la senal base de ``restock_detector`` (empleado + caja +
    reposiciones al anaquel).

Como distingue una caja-en-el-suelo del resto (clave, porque en una farmacia
TODO el estante son cajas de producto):

  1. Vocabulario abierto (YOLO-World del multimodal_router) con terminos de
     caja de carton. Throttled (una caja no aparece entre frames).
  2. El PIE de la caja (centro-inferior del bbox) debe caer dentro de un
     PASILLO del planograma -> esta en el piso, no en un estante. Sin
     pasillos definidos cae a una heuristica de "mitad inferior del frame".
  3. Tamano minimo -> descarta los productos pequenos de estante.
  4. ESTATICA y PERSISTENTE: solo cuenta si se queda quieta un rato. Una
     caja que se mueve la lleva alguien (no es una caja dejada) y se
     descarta; asi tampoco se cuenta a alguien que cruza cargando una caja.

Limitaciones documentadas:
  - Si una persona tapa la caja mas de ``BOX_REMOVAL_GRACE_S`` seguidos, se
    dara por retirada y, al reaparecer, contara como una caja nueva.
  - La deteccion open-vocab no es perfecta; los filtros de piso+tamano+
    estatica son la defensa. Si hay falsos positivos, sube BOX_DETECT_CONF y
    BOX_MIN_AREA_FRAC.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import AnalyticsConfig
from .store_layout import StoreLayout

logger = logging.getLogger(__name__)


class _Box:
    """Una caja detectada en el piso y su cronometro."""

    __slots__ = ("id", "box", "aisle", "first_seen", "last_seen",
                 "confirmed", "alerted", "_pos_hist")

    def __init__(self, box_id: int, box, aisle: Optional[str], ts: float):
        self.id = box_id
        self.box = [float(v) for v in box]
        self.aisle = aisle
        self.first_seen = ts
        self.last_seen = ts
        self.confirmed = False
        self.alerted = False
        self._pos_hist = deque(maxlen=12)
        self._pos_hist.append((self.center, ts))

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2.0,
                (self.box[1] + self.box[3]) / 2.0)

    @property
    def w(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def h(self) -> float:
        return self.box[3] - self.box[1]

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.first_seen)

    def is_static(self, max_move: float) -> bool:
        """True si apenas se movio en el historial reciente."""
        if len(self._pos_hist) < 2:
            return False
        xs = [c[0] for c, _ in self._pos_hist]
        ys = [c[1] for c, _ in self._pos_hist]
        return (max(xs) - min(xs)) <= max_move and (max(ys) - min(ys)) <= max_move


class AisleBoxMonitor:
    """Detecta y cronometra cajas dejadas en el piso del pasillo."""

    def __init__(self, layout: StoreLayout, config: AnalyticsConfig = None,
                 device: Any = 0):
        cfg = config or AnalyticsConfig
        self._cfg = cfg
        self._layout = layout
        self._device = device
        self._enabled = bool(cfg.BOX_DETECT_ENABLED)
        self._every_n = max(1, int(cfg.BOX_DETECT_EVERY_N))
        self._terms = list(cfg.BOX_DETECT_TERMS)
        self._conf = float(cfg.BOX_DETECT_CONF)
        self._min_area = float(cfg.BOX_MIN_AREA_FRAC)
        self._confirm_s = float(cfg.BOX_CONFIRM_S)
        self._grace_s = float(cfg.BOX_REMOVAL_GRACE_S)
        self._alert_after = float(cfg.BOX_ALERT_AFTER_S)
        self._floor_y = float(cfg.BOX_FLOOR_MIN_Y_FRAC)
        self._router = None
        self._router_failed = False      # solo informativo (diagnostico)
        self._router_retry_ts = 0.0      # proximo intento de carga del router
        self._boxes: List[_Box] = []
        self._next_id = 1
        self._finished: List[Dict[str, Any]] = []   # cajas ya retiradas
        self._total_detected = 0
        self._w = 0
        self._h = 0
        # Diagnostico: sin esto es imposible saber POR QUE no se ve una caja
        # (¿no la detecta YOLO-World, o la detecta y un filtro la tira?).
        self._raw_dets = 0                  # detecciones crudas del router
        self._best_conf = 0.0               # mejor confianza vista (cruda)
        self._rej = {"conf_baja": 0, "pequena": 0, "fuera_pasillo": 0,
                     "carrito": 0}
        self._warned_detect_fail = False
        self._logged_first_det = False
        self._last_dump_ts = 0.0

    @property
    def available(self) -> bool:
        """Activa mientras este habilitada en config. Un fallo del router NO
        la mata: se reintenta con backoff (la deteccion de cajas debe estar
        SIEMPRE activa; un tropiezo transitorio no puede apagarla para
        siempre como antes)."""
        return self._enabled

    def _ensure_router(self):
        if self._router is not None:
            return self._router
        now = time.time()
        if now < self._router_retry_ts:
            return None
        try:
            from ..multimodal_router import get_multimodal_router
            self._router = get_multimodal_router(device=self._device)
            if self._router_failed:
                logger.info("Router YOLO-World recuperado; deteccion de "
                            "cajas operativa de nuevo")
            self._router_failed = False
        except Exception as exc:  # noqa: BLE001
            self._router_retry_ts = now + 60.0   # reintenta en 1 min
            if not self._router_failed:
                logger.warning("YOLO-World no disponible (%s); la deteccion "
                               "de cajas reintentara cada 60s", exc)
                self._router_failed = True
        return self._router

    # ── Ciclo por frame ──────────────────────────────────────────────

    def update(self, frame: np.ndarray, frame_idx: int = 0,
               exclude_boxes: List[Any] = None,
               now: float = None) -> List[Dict[str, Any]]:
        """Detecta cajas, actualiza cronometros y finaliza las retiradas.

        Args:
            frame: frame BGR.
            frame_idx: contador para el throttle de deteccion.
            exclude_boxes: bboxes a excluir (p.ej. carritos) para no
                confundirlos con cajas.
            now: reloj inyectable (para test); por defecto time.time().

        Returns:
            Eventos: caja_detectada, caja_retirada, caja_obstruccion.
        """
        if not self.available or frame is None or frame.size == 0:
            return []
        now = time.time() if now is None else now
        self._h, self._w = frame.shape[:2]
        eventos: List[Dict[str, Any]] = []

        if frame_idx % self._every_n == 0:
            dets = self._detect(frame, exclude_boxes or [])
            self._match(dets, now)

        # Confirmar / alertar / finalizar
        for b in list(self._boxes):
            # Confirmar cuando lleva ESTATICA el tiempo minimo.
            if (not b.confirmed and b.elapsed(now) >= self._confirm_s
                    and b.is_static(self._static_max_move())):
                b.confirmed = True
                self._total_detected += 1
                eventos.append(self._event("caja_detectada", b, now))

            # Obstruccion: presente demasiado tiempo.
            if (b.confirmed and not b.alerted
                    and b.elapsed(now) >= self._alert_after):
                b.alerted = True
                eventos.append(self._event("caja_obstruccion", b, now))

            # Retirada: no se ve hace mas del periodo de gracia.
            if now - b.last_seen > self._grace_s:
                self._boxes.remove(b)
                if b.confirmed:
                    ev = self._event("caja_retirada", b, b.last_seen)
                    ev["duracion_total_s"] = round(b.last_seen - b.first_seen, 1)
                    self._finished.append(ev)
                    del self._finished[:-200]
                    eventos.append(ev)
        return eventos

    # Conf DIAGNOSTICA: el router se consulta con este piso bajisimo y el
    # umbral real (BOX_DETECT_CONF) se aplica AQUI. Asi sabemos que puntua
    # YOLO-World aunque quede por debajo del umbral -> "mejor_conf_vista"
    # responde de un vistazo si el problema es el umbral o el detector.
    _DIAG_CONF = 0.02

    def _detect(self, frame: np.ndarray,
                exclude: List[Any]) -> List[List[float]]:
        router = self._ensure_router()
        if router is None:
            return []
        try:
            raw = router.detect(frame, self._terms, conf=self._DIAG_CONF)
        except Exception as exc:  # noqa: BLE001
            # A nivel warning (una vez): si esto falla en silencio, "no
            # detecta cajas" es indistinguible de "no hay cajas".
            if not self._warned_detect_fail:
                logger.warning("Deteccion de cajas FALLANDO (se reintenta "
                               "cada frame de deteccion): %s", exc)
                self._warned_detect_fail = True
            return []
        self._warned_detect_fail = False   # exito: re-avisar si vuelve a caer
        self._raw_dets += len(raw)
        if raw:
            self._best_conf = max(self._best_conf,
                                  max(float(d.get("confidence", 0.0))
                                      for d in raw))
        if raw and not self._logged_first_det:
            self._logged_first_det = True
            logger.info("YOLO-World ve %d candidato(s) a caja; mejor "
                        "conf=%.3f (umbral=%.2f)", len(raw),
                        self._best_conf, self._conf)
        frame_area = float(max(1, self._w * self._h))
        out: List[List[float]] = []
        for d in raw:
            box = d.get("box")
            if not box or len(box) < 4:
                continue
            if float(d.get("confidence", 0.0)) < self._conf:
                self._rej["conf_baja"] += 1
                continue
            x1, y1, x2, y2 = [float(v) for v in box]
            area = (x2 - x1) * (y2 - y1)
            if area / frame_area < self._min_area:
                self._rej["pequena"] += 1     # producto de estante: muy chico
                continue
            if not self._on_floor(x1, x2, y2):
                self._rej["fuera_pasillo"] += 1   # sobre un estante
                continue
            if self._overlaps_any([x1, y1, x2, y2], exclude):
                self._rej["carrito"] += 1     # excluido explicitamente
                continue
            # Dedupe: varios TERMINOS pueden matchear la MISMA caja (p.ej.
            # "cardboard box" y "carton box") -> sin esto se crearian dos
            # _Box en el mismo sitio y eventos duplicados.
            if any(self._iou(o, [x1, y1, x2, y2]) > 0.6 for o in out):
                continue
            out.append([x1, y1, x2, y2])
        self._maybe_debug_dump(frame, raw, out)
        return out

    def _maybe_debug_dump(self, frame: np.ndarray, raw: List[Dict[str, Any]],
                          accepted: List[List[float]]) -> None:
        """Imagen anotada con TODOS los candidatos (score) para diagnostico.

        Escribe output/debug_cajas/<camara>.jpg cada BOX_DEBUG_DUMP_EVERY_S:
        rojo = candidato crudo (con su confianza), verde = aceptado por los
        filtros. Con esto se ve EN LA IMAGEN REAL que puntua YOLO-World a
        cada caja del piso y que filtro descarta el resto. 0 = apagado.
        Defensivo: nunca lanza.
        """
        every = float(getattr(self._cfg, "BOX_DEBUG_DUMP_EVERY_S", 0) or 0)
        if every <= 0:
            return
        now = time.time()
        if now - self._last_dump_ts < every:
            return
        self._last_dump_ts = now
        try:
            import cv2
            from .store_layout import safe_name
            img = frame.copy()
            for d in raw:
                b = d.get("box") or []
                if len(b) < 4:
                    continue
                x1, y1, x2, y2 = [int(v) for v in b]
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
                cv2.putText(img, f"{d.get('confidence', 0):.2f}",
                            (x1 + 2, max(12, y1 - 3)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
            for b in accepted:
                x1, y1, x2, y2 = [int(v) for v in b]
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(img,
                        f"crudos={len(raw)} aceptados={len(accepted)} "
                        f"umbral={self._conf:.2f} mejor={self._best_conf:.3f}",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 255, 255), 2)
            out_dir = os.path.join("output", "debug_cajas")
            os.makedirs(out_dir, exist_ok=True)
            name = safe_name(getattr(self._layout, "camera_id", None))
            tmp = os.path.join(out_dir, f"{name}.tmp.jpg")
            dst = os.path.join(out_dir, f"{name}.jpg")
            if cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 80]):
                os.replace(tmp, dst)
        except Exception as exc:  # noqa: BLE001
            logger.debug("debug dump de cajas fallo: %s", exc)

    def _on_floor(self, x1: float, x2: float, y2: float) -> bool:
        """La BASE de la caja pisa el pasillo (planograma) o, sin pasillos
        definidos, la mitad inferior del frame.

        Se prueban TRES puntos de la base (centro y esquinas inferiores):
        una caja arrimada al estante suele tener el centro de su base justo
        FUERA del poligono del pasillo dibujado; basta con que una esquina
        lo pise para aceptarla.
        """
        puntos = ((x1 + x2) / 2.0, x1, x2)
        if self._layout.aisles:
            return any(
                self._layout.aisle_at(px, y2, self._w, self._h) is not None
                for px in puntos)
        return (y2 / max(1, self._h)) >= self._floor_y

    def _aisle_of(self, x1: float, x2: float, y2: float) -> Optional[str]:
        """Pasillo de la caja, probando los mismos 3 puntos de la base."""
        if not self._layout.aisles:
            return None
        for px in ((x1 + x2) / 2.0, x1, x2):
            a = self._layout.aisle_at(px, y2, self._w, self._h)
            if a is not None:
                return a.nombre
        return None

    def _static_max_move(self) -> float:
        """Movimiento maximo (px) para considerar una caja quieta."""
        return float(self._cfg.BOX_STATIC_MAX_MOVE_FRAC) * \
            float(max(1, max(self._w, self._h)))

    def _match(self, dets: List[List[float]], now: float) -> None:
        """Asocia detecciones a cajas existentes por IoU (son estaticas ->
        IoU alto); las no asociadas abren una caja candidata nueva."""
        usados: set = set()
        for det in dets:
            mejor, mejor_iou = None, 0.30
            for b in self._boxes:
                if id(b) in usados:
                    continue
                iou = self._iou(b.box, det)
                if iou > mejor_iou:
                    mejor, mejor_iou = b, iou
            if mejor is not None:
                mejor.box = det
                mejor.last_seen = now
                mejor._pos_hist.append((mejor.center, now))
                usados.add(id(mejor))
            else:
                nb = _Box(self._next_id, det,
                          self._aisle_of(det[0], det[2], det[3]), now)
                self._next_id += 1
                self._boxes.append(nb)

    @staticmethod
    def _overlaps_any(a: List[float], otros: List[Any],
                      ioa_th: float = 0.5) -> bool:
        area = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
        for b in otros or []:
            try:
                bx = [float(v) for v in b]
            except (TypeError, ValueError):
                continue
            iw = max(0.0, min(a[2], bx[2]) - max(a[0], bx[0]))
            ih = max(0.0, min(a[3], bx[3]) - max(a[1], bx[1]))
            if (iw * ih) / area >= ioa_th:
                return True
        return False

    @staticmethod
    def _iou(a: List[float], b: List[float]) -> float:
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        if inter <= 0:
            return 0.0
        ua = ((a[2] - a[0]) * (a[3] - a[1])
              + (b[2] - b[0]) * (b[3] - b[1]) - inter)
        return inter / ua if ua > 0 else 0.0

    def _event(self, tipo: str, b: _Box, now: float) -> Dict[str, Any]:
        cx, cy = b.center
        return {
            "evento": tipo,
            "caja_id": b.id,
            "pasillo": b.aisle,
            "x": round(cx / max(1, self._w), 4),
            "y": round(cy / max(1, self._h), 4),
            "segundos_presente": round(b.elapsed(now), 1),
            "timestamp": now,
        }

    # ── Consultas ────────────────────────────────────────────────────

    def confirmed_boxes(self) -> List[Dict[str, Any]]:
        """Cajas CONFIRMADAS ahora (llevan el tiempo minimo estaticas)."""
        return [{"id": b.id, "box": list(b.box), "aisle": b.aisle,
                 "center": b.center} for b in self._boxes if b.confirmed]

    def tracked_box_regions(self) -> List[Dict[str, Any]]:
        """TODAS las cajas en seguimiento (confirmadas o no). La usa
        restock_detector: alguien pegado a una caja debe excluirse de la
        analitica de compra desde el primer frame, sin esperar los segundos
        de confirmacion del cronometro (la reposicion tiene su propia
        confirmacion por permanencia + alcances)."""
        return [{"id": b.id, "box": list(b.box), "aisle": b.aisle,
                 "center": b.center} for b in self._boxes]

    def boxes_overlay(self, now: float = None) -> List[Dict[str, Any]]:
        """Cajas confirmadas para pintar en el overlay (con su cronometro)."""
        now = time.time() if now is None else now
        return [{"box": [round(v, 1) for v in b.box],
                 "segundos": round(b.elapsed(now), 1),
                 "obstruccion": bool(b.alerted)}
                for b in self._boxes if b.confirmed]

    def get_stats(self, now: float = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        activas = [b for b in self._boxes if b.confirmed]
        durs = [f["duracion_total_s"] for f in self._finished
                if "duracion_total_s" in f]
        return {
            "activo": self.available,
            "cajas_activas": [{
                "caja_id": b.id, "pasillo": b.aisle,
                "segundos_presente": round(b.elapsed(now), 1),
                "obstruccion": bool(b.alerted),
            } for b in activas],
            "cajas_en_piso_ahora": len(activas),
            "obstrucciones_activas": sum(1 for b in activas if b.alerted),
            "total_cajas_detectadas": self._total_detected,
            "cajas_retiradas": len(self._finished),
            "duracion_media_s": round(float(np.mean(durs)), 1) if durs else 0.0,
            "duracion_max_s": round(float(max(durs)), 1) if durs else 0.0,
            "umbral_obstruccion_s": self._alert_after,
            # Diagnostico: responde "¿por que no veo cajas?". Si crudas=0,
            # YOLO-World no las ve (bajar conf / revisar router); si crudas>0
            # y rechazos altos, un filtro las esta tirando (area / pasillo).
            "diagnostico": {
                "detecciones_crudas": int(self._raw_dets),
                "mejor_conf_vista": round(float(self._best_conf), 3),
                "umbral_conf": float(self._conf),
                "rechazos": dict(self._rej),
                "router_fallando": bool(self._warned_detect_fail),
            },
        }

    def recent_finished(self, n: int = 20) -> List[Dict[str, Any]]:
        return self._finished[-n:]

    def reset(self) -> None:
        self._boxes.clear()
        self._finished.clear()
        self._total_detected = 0
        self._next_id = 1
        self._raw_dets = 0
        self._best_conf = 0.0
        self._rej = {"conf_baja": 0, "pequena": 0, "fuera_pasillo": 0,
                     "carrito": 0}
