"""
analytics/cart_tracker.py - Carritos y cestas de compra.

Localiza los carritos/cestas del local, los asigna a su duena/dueno, y
detecta cuando esa persona mete la mano en el suyo. Combinado con los
eventos de ``shelf_interaction`` es lo que permite afirmar "agarro el
producto X y lo puso en el carrito" en vez de solo "toco el estante X".

Deteccion: vocabulario ABIERTO con el YOLO-World que el proyecto ya carga
(``multimodal_router``), asi que no hay que entrenar ni descargar un modelo
nuevo, y no se ocupa VRAM extra si el router ya estaba en memoria. Corre
cada ``CART_DETECT_EVERY_N`` frames (un carrito no aparece y desaparece
entre frames) para que el coste sea despreciable frente al detector de
personas.

Asociacion carrito -> persona: el carrito se asigna a la persona cuyo punto
de pie esta mas cerca, dentro de un radio proporcional a la altura de esa
persona (asi funciona igual de lejos que de cerca en perspectiva). La
asignacion es PEGAJOSA con TTL: si el carrito deja de detectarse un par de
segundos (tapado por la propia persona, algo habitual) no se pierde la
relacion.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import AnalyticsConfig

logger = logging.getLogger(__name__)

DEPOSIT_CART = "EN_CARRITO"


class _Cart:
    """Un carrito/cesta detectado y su dueno asignado."""

    __slots__ = ("box", "label", "confidence", "owner", "last_seen",
                 "owner_score")

    def __init__(self, box, label: str, confidence: float, ts: float):
        self.box = [float(v) for v in box]
        self.label = str(label)
        self.confidence = float(confidence)
        self.owner: Optional[str] = None
        self.owner_score: float = 0.0
        self.last_seen: float = ts

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2.0,
                (self.box[1] + self.box[3]) / 2.0)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        w = (self.box[2] - self.box[0]) * margin
        h = (self.box[3] - self.box[1]) * margin
        return ((self.box[0] - w) <= x <= (self.box[2] + w)
                and (self.box[1] - h) <= y <= (self.box[3] + h))


class CartTracker:
    """Detecta carritos/cestas, los asigna a personas y ve los depositos."""

    def __init__(self, config: AnalyticsConfig = None, device: Any = 0):
        cfg = config or AnalyticsConfig
        self._cfg = cfg
        self._device = device
        self._enabled = bool(cfg.CART_DETECT_ENABLED)
        self._every_n = max(1, int(cfg.CART_DETECT_EVERY_N))
        self._terms = list(cfg.CART_DETECT_TERMS)
        self._conf = float(cfg.CART_DETECT_CONF)
        self._ttl = float(cfg.CART_ASSOC_TTL_S)
        self._deposit_frames = max(1, int(cfg.CART_DEPOSIT_MIN_FRAMES))
        self._router = None
        self._router_failed = False
        self._carts: List[_Cart] = []
        # pid -> frames consecutivos con la mano dentro de su carrito
        self._hand_frames: Dict[str, int] = {}
        self._deposits: List[Dict[str, Any]] = []
        self._last_detect_ts: float = 0.0

    @property
    def available(self) -> bool:
        return self._enabled and not self._router_failed

    def _ensure_router(self):
        if self._router is not None or self._router_failed:
            return self._router
        try:
            from ..multimodal_router import get_multimodal_router
            self._router = get_multimodal_router(device=self._device)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Deteccion de carritos desactivada (YOLO-World no "
                "disponible: %s). Los productos tomados se contaran como "
                "'se lo lleva' por no-devolucion.", exc)
            self._router_failed = True
        return self._router

    # ── Actualizacion por frame ──────────────────────────────────────

    def update(self, frame: np.ndarray, tracks: Dict[Any, Dict[str, Any]],
               frame_idx: int = 0,
               hand_points: Dict[str, List[Tuple[float, float]]] = None
               ) -> List[Dict[str, Any]]:
        """Re-detecta (throttled), reasigna duenos y detecta depositos.

        Args:
            frame: frame BGR.
            tracks: ``active_tracks`` (usa ``box`` y ``persistent_id``).
            frame_idx: contador de frames para el throttle.
            hand_points: {persistent_id: [(x, y), ...]} zonas de mano de esa
                persona (las calcula ShelfInteractionDetector).

        Returns:
            Eventos de deposito confirmados este frame.
        """
        if not self.available or frame is None or frame.size == 0:
            return []
        now = time.time()

        if frame_idx % self._every_n == 0:
            self._detect(frame, now)

        # Caducar carritos no vistos hace rato
        self._carts = [c for c in self._carts
                       if now - c.last_seen <= self._ttl]
        self._assign_owners(tracks)
        return self._detect_deposits(hand_points or {}, now)

    def _detect(self, frame: np.ndarray, now: float) -> None:
        router = self._ensure_router()
        if router is None:
            return
        try:
            dets = router.detect(frame, self._terms, conf=self._conf)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Deteccion de carritos fallo: %s", exc)
            return
        self._last_detect_ts = now
        nuevos: List[_Cart] = []
        for d in dets:
            cart = _Cart(d["box"], d.get("label", "carrito"),
                         d.get("confidence", 0.0), now)
            # Reusar el dueno del carrito previo mas solapado (continuidad).
            prev = self._best_overlap(cart)
            if prev is not None:
                cart.owner = prev.owner
                cart.owner_score = prev.owner_score
            nuevos.append(cart)
        if nuevos:
            self._carts = nuevos
        else:
            # Sin detecciones: conservar los vigentes (oclusion momentanea).
            pass

    def _best_overlap(self, cart: _Cart) -> Optional[_Cart]:
        mejor, mejor_iou = None, 0.30
        for c in self._carts:
            iou = self._iou(c.box, cart.box)
            if iou > mejor_iou:
                mejor, mejor_iou = c, iou
        return mejor

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

    def _assign_owners(self, tracks: Dict[Any, Dict[str, Any]]) -> None:
        """Asigna cada carrito a la persona mas cercana (radio ~ su altura)."""
        personas: List[Tuple[str, float, float, float]] = []
        for t in tracks.values():
            pid, box = t.get("persistent_id"), t.get("box")
            if not pid or box is None:
                continue
            try:
                x1, y1, x2, y2 = [float(v) for v in box]
            except (TypeError, ValueError):
                continue
            personas.append((str(pid), (x1 + x2) / 2.0, y2,
                             max(1.0, y2 - y1)))
        if not personas:
            return
        radio_k = float(self._cfg.CART_ASSOC_RADIUS_RATIO)
        for cart in self._carts:
            cx, cy = cart.center
            mejor, mejor_d = None, None
            for pid, px, py, ph in personas:
                d = float(np.hypot(cx - px, cy - py))
                if d > ph * radio_k:
                    continue
                if mejor_d is None or d < mejor_d:
                    mejor, mejor_d = pid, d
            if mejor is not None:
                cart.owner = mejor
                cart.owner_score = 1.0 / (1.0 + (mejor_d or 0.0))

    def _detect_deposits(self, hand_points: Dict[str, List[Tuple[float, float]]],
                         now: float) -> List[Dict[str, Any]]:
        """Confirma que la mano de alguien entro en SU carrito."""
        eventos: List[Dict[str, Any]] = []
        margen = float(self._cfg.CART_DEPOSIT_MARGIN_RATIO)
        activos: set = set()
        for cart in self._carts:
            if not cart.owner:
                continue
            pts = hand_points.get(cart.owner) or []
            if any(cart.contains(px, py, margen) for px, py in pts):
                activos.add(cart.owner)
                n = self._hand_frames.get(cart.owner, 0) + 1
                self._hand_frames[cart.owner] = n
                if n == self._deposit_frames:
                    ev = {
                        "evento": DEPOSIT_CART,
                        "persistent_id": cart.owner,
                        "contenedor": cart.label,
                        "timestamp": now,
                    }
                    eventos.append(ev)
                    self._deposits.append(ev)
        for pid in list(self._hand_frames.keys()):
            if pid not in activos:
                self._hand_frames.pop(pid, None)
        return eventos

    # ── Consultas ────────────────────────────────────────────────────

    def cart_of(self, pid: str) -> Optional[_Cart]:
        for c in self._carts:
            if c.owner == str(pid):
                return c
        return None

    def has_cart(self, pid: str) -> bool:
        return self.cart_of(pid) is not None

    def boxes(self) -> List[Dict[str, Any]]:
        """Carritos vigentes para el overlay del cliente."""
        return [{"box": [round(v, 1) for v in c.box], "label": c.label,
                 "owner": c.owner, "confidence": round(c.confidence, 2)}
                for c in self._carts]

    def get_stats(self) -> Dict[str, Any]:
        con_dueno = [c for c in self._carts if c.owner]
        return {
            "activo": self.available,
            "carritos_detectados": len(self._carts),
            "carritos_con_dueno": len(con_dueno),
            "depositos_totales": len(self._deposits),
            "ultima_deteccion": self._last_detect_ts,
        }

    def reset(self) -> None:
        self._carts.clear()
        self._hand_frames.clear()
        self._deposits.clear()
