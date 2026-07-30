"""
analytics/store_layout.py - Planograma de la tienda por camara.

Define QUE hay en cada parte del encuadre. Es la BASE de toda la analitica
de retail: sin planograma el sistema puede decir "hay 4 personas", pero no
"hay 4 personas en el pasillo de lacteos mirando la leche entera".

Tres tipos de zona:

  * ``Aisle`` (pasillo)  - poligono. Mide afluencia/concentracion.
  * ``Shelf`` (anaquel)  - rectangulo. Es la unidad de PRODUCTO: el nombre
    del anaquel ES el SKU/categoria que contiene (enfoque de planograma,
    el estandar en retail). Ademas se le mide el nivel de llenado para
    detectar estantes vacios y para inferir si alguien TOMO o DEVOLVIO.
  * ``Fixture`` (mobiliario) - rectangulo con ``tipo``: 'consulta_precio'
    (la maquina consultora), 'caja', 'entrada', 'promocion'...

Coordenadas: se aceptan en PIXELES o NORMALIZADAS 0..1. Si todos los
valores de una figura son <= 1.0 se interpretan como normalizadas y se
escalan al tamano real del frame. Lo normalizado es lo recomendable: el
planograma sobrevive a un cambio de resolucion de la camara.

Persistencia: un JSON por camara en ``config/store_layout/<camera>.json``.
Se puede editar a mano o construir desde la UI (add_aisle/add_shelf/...).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .config import AnalyticsConfig

logger = logging.getLogger(__name__)


def safe_name(value: Any) -> str:
    """Sanitiza un id (puede ser UUID) para usarlo como nombre de archivo."""
    s = str(value if value is not None else "default")
    return "".join(c for c in s if c.isalnum() or c in "_-")[:64] or "default"


def _is_normalized(points: Sequence[Sequence[float]]) -> bool:
    """True si todas las coordenadas caen en 0..1 (planograma relativo)."""
    try:
        return all(0.0 <= float(c) <= 1.0 for p in points for c in p[:2])
    except (TypeError, ValueError, IndexError):
        return False


# ── Zonas ────────────────────────────────────────────────────────────

class Aisle:
    """Pasillo: poligono donde se mide afluencia de personas."""

    def __init__(self, nombre: str, poligono: Sequence[Sequence[float]],
                 categoria: str = ""):
        self.nombre = str(nombre)
        self.categoria = str(categoria or "")
        self._raw = [[float(p[0]), float(p[1])] for p in poligono]
        self._norm = _is_normalized(self._raw)
        self._cache_wh: Optional[Tuple[int, int]] = None
        self._cache_poly: Optional[np.ndarray] = None

    def polygon_px(self, frame_w: int, frame_h: int) -> np.ndarray:
        """Poligono en pixeles Nx2 int32 para el tamano de frame dado."""
        if self._cache_poly is not None and self._cache_wh == (frame_w, frame_h):
            return self._cache_poly
        if self._norm:
            pts = [[p[0] * frame_w, p[1] * frame_h] for p in self._raw]
        else:
            pts = list(self._raw)
        poly = np.array(pts, np.int32)
        self._cache_wh = (frame_w, frame_h)
        self._cache_poly = poly
        return poly

    def contains(self, x: float, y: float, frame_w: int, frame_h: int) -> bool:
        """True si el punto (px) cae dentro del pasillo."""
        poly = self.polygon_px(frame_w, frame_h).reshape((-1, 1, 2))
        return cv2.pointPolygonTest(poly, (int(x), int(y)), False) >= 0

    def to_dict(self) -> Dict[str, Any]:
        return {"nombre": self.nombre, "categoria": self.categoria,
                "poligono": self._raw}


def _rect_to_poly(rect: Sequence[float]) -> List[List[float]]:
    """[x1,y1,x2,y2] -> 4 esquinas de poligono."""
    x1, y1, x2, y2 = [float(v) for v in rect[:4]]
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _parse_shelf_parts(geom: Sequence) -> List[List[List[float]]]:
    """Normaliza la geometria de un anaquel a una LISTA DE POLIGONOS.

    Acepta, por retrocompatibilidad:
      - rect: [x1, y1, x2, y2]                         -> 1 poligono (4 esq.)
      - poligono unico: [[x, y], [x, y], ...]          -> 1 poligono
      - multi-poligono: [[[x, y], ...], [[x, y], ...]] -> N poligonos
    """
    pts = list(geom)
    if not pts:
        return []
    # rect: 4 escalares
    if len(pts) == 4 and all(np.isscalar(v) for v in pts):
        return [[[float(c) for c in p] for p in _rect_to_poly(pts)]]
    first = pts[0]
    # poligono unico: el primer elemento es un punto [x, y] (2 escalares)
    if (isinstance(first, (list, tuple)) and len(first) == 2
            and all(np.isscalar(v) for v in first)):
        return [[[float(p[0]), float(p[1])] for p in pts]]
    # multi-poligono: cada elemento es una lista de puntos
    parts = []
    for poly in pts:
        parts.append([[float(p[0]), float(p[1])] for p in poly])
    return parts


class Shelf:
    """Anaquel/estante = un PRODUCTO o CATEGORIA del planograma.

    Se define como uno o VARIOS POLIGONOS (un mismo producto/categoria suele
    ocupar varias secciones de estante). Cada "parte" es un poligono, para
    seguir estantes inclinados/curvos de una camara gran-angular. Por
    retrocompatibilidad acepta tambien un ``rect`` o un poligono unico.

    ``nombre`` es la etiqueta que se reporta en la analitica. ``fill_ratio``
    es su nivel de llenado 0..1 (1 = lleno) medido sobre TODAS las partes;
    ``status`` deriva de los umbrales de config.
    """

    def __init__(self, nombre: str, geom: Sequence, pasillo: str = "",
                 categoria: str = "", precio: float = 0.0, sku: str = ""):
        self.nombre = str(nombre)
        self.pasillo = str(pasillo or "")
        self.categoria = str(categoria or "")
        self.sku = str(sku or "")
        self.precio = float(precio or 0.0)
        # Lista de poligonos (partes). Retrocompat con rect/poligono unico.
        self._parts_raw = _parse_shelf_parts(geom)
        flat = [p for part in self._parts_raw for p in part]
        self._norm = _is_normalized(flat) if flat else True
        self._cache_wh: Optional[Tuple[int, int]] = None
        self._cache_parts: Optional[List[np.ndarray]] = None
        # Estado de stock (lo actualiza ShelfStockTracker)
        self.fill_ratio: float = 1.0
        self.status: str = "OK"
        self.last_update: float = 0.0

    @property
    def n_parts(self) -> int:
        return len(self._parts_raw)

    def polygons_px(self, frame_w: int, frame_h: int) -> List[np.ndarray]:
        """Lista de poligonos (partes) del anaquel en px, cada uno Nx2 int32."""
        if self._cache_parts is not None and self._cache_wh == (frame_w, frame_h):
            return self._cache_parts
        parts = []
        for raw in self._parts_raw:
            if self._norm:
                pts = [[p[0] * frame_w, p[1] * frame_h] for p in raw]
            else:
                pts = list(raw)
            parts.append(np.array(pts, np.int32))
        self._cache_wh = (frame_w, frame_h)
        self._cache_parts = parts
        return parts

    def polygon_px(self, frame_w: int, frame_h: int) -> np.ndarray:
        """Primera parte (compat con codigo que espera un solo poligono)."""
        parts = self.polygons_px(frame_w, frame_h)
        return parts[0] if parts else np.empty((0, 2), np.int32)

    def rect_px(self, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        """Caja contenedora (bbox) que engloba TODAS las partes, en px."""
        parts = self.polygons_px(frame_w, frame_h)
        if not parts:
            return 0, 0, 0, 0
        allp = np.concatenate(parts, axis=0)
        x1, y1 = int(allp[:, 0].min()), int(allp[:, 1].min())
        x2, y2 = int(allp[:, 0].max()), int(allp[:, 1].max())
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(frame_w, x2), min(frame_h, y2)
        return x1, y1, x2, y2

    def contains(self, x: float, y: float, frame_w: int, frame_h: int) -> bool:
        """True si el punto (px) cae dentro de CUALQUIER parte del anaquel."""
        for poly in self.polygons_px(frame_w, frame_h):
            if cv2.pointPolygonTest(poly.reshape((-1, 1, 2)),
                                    (int(x), int(y)), False) >= 0:
                return True
        return False

    def dist_to_point(self, x: float, y: float, frame_w: int,
                      frame_h: int) -> float:
        """Distancia (px) del punto al anaquel: 0 si esta dentro de alguna
        parte; si no, la menor distancia a los bordes de las partes."""
        best = None
        for poly in self.polygons_px(frame_w, frame_h):
            d = cv2.pointPolygonTest(poly.reshape((-1, 1, 2)),
                                     (float(x), float(y)), True)
            if d >= 0:
                return 0.0
            best = -d if best is None else min(best, -d)
        return float(best) if best is not None else 1e9

    def crop_and_mask(self, frame: np.ndarray):
        """(crop_bbox, mascara) del anaquel, o (None, None).

        `crop_bbox` es el recorte de la bbox que engloba todas las partes;
        `mascara` es 255 dentro de CUALQUIER parte (en coords del crop). Asi
        la medicion mira solo los pixeles reales del estante (union de las
        secciones) y no los huecos entre ellas ni la esquina del pasillo.
        """
        if frame is None or frame.size == 0:
            return None, None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.rect_px(w, h)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None, None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None
        mask = np.zeros((y2 - y1, x2 - x1), np.uint8)
        polys = [(poly - np.array([[x1, y1]], np.int32)).astype(np.int32)
                 for poly in self.polygons_px(w, h)]
        cv2.fillPoly(mask, polys, 255)
        return crop, mask

    def crop(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Recorte rectangular (bbox) del anaquel, o None si queda vacio."""
        crop, _ = self.crop_and_mask(frame)
        return crop

    def local_mask(self, crop_w: int, crop_h: int) -> np.ndarray:
        """Mascara (union de partes) escalada a un recorte de (crop_w, crop_h).

        Independiente de la resolucion: re-crea la mascara de una foto de
        referencia guardada en disco (cuyo tamano = su propia bbox union), de
        forma consistente con la mascara en vivo."""
        flat = np.array([p for part in self._parts_raw for p in part],
                        dtype=np.float64)
        mask = np.zeros((crop_h, crop_w), np.uint8)
        if flat.size == 0:
            return mask
        minx, miny = flat.min(axis=0)
        maxx, maxy = flat.max(axis=0)
        bw = (maxx - minx) or 1.0
        bh = (maxy - miny) or 1.0
        sx, sy = max(1, crop_w - 1), max(1, crop_h - 1)
        polys = []
        for part in self._parts_raw:
            arr = (np.array(part, np.float64) - [minx, miny]) / [bw, bh]
            polys.append((arr * [sx, sy]).astype(np.int32))
        cv2.fillPoly(mask, polys, 255)
        return mask

    def center_px(self, frame_w: int, frame_h: int) -> Tuple[int, int]:
        allp = np.concatenate(self.polygons_px(frame_w, frame_h), axis=0)
        return (int(allp[:, 0].mean()), int(allp[:, 1].mean()))

    def add_part(self, geom: Sequence) -> None:
        """Agrega otra seccion (poligono) al mismo anaquel."""
        self._parts_raw.extend(_parse_shelf_parts(geom))
        flat = [p for part in self._parts_raw for p in part]
        self._norm = _is_normalized(flat) if flat else True
        self._cache_wh = None
        self._cache_parts = None

    def to_dict(self) -> Dict[str, Any]:
        return {"nombre": self.nombre, "poligonos": self._parts_raw,
                "pasillo": self.pasillo, "categoria": self.categoria,
                "sku": self.sku, "precio": self.precio}

    def state_dict(self) -> Dict[str, Any]:
        return {"nombre": self.nombre, "pasillo": self.pasillo,
                "categoria": self.categoria, "sku": self.sku,
                "precio": self.precio,
                "fill_ratio": round(float(self.fill_ratio), 3),
                "estado": self.status}


class Fixture:
    """Mobiliario puntual: maquina consultora de precios, caja, promocion."""

    TIPO_CONSULTA_PRECIO = "consulta_precio"
    TIPO_CAJA = "caja"
    TIPO_PROMOCION = "promocion"
    TIPO_ENTRADA = "entrada"

    def __init__(self, nombre: str, rect: Sequence[float],
                 tipo: str = TIPO_CONSULTA_PRECIO):
        self.nombre = str(nombre)
        self.tipo = str(tipo or self.TIPO_CONSULTA_PRECIO)
        self._raw = [float(v) for v in rect[:4]]
        self._norm = _is_normalized([self._raw[:2], self._raw[2:4]])

    def rect_px(self, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = self._raw
        if self._norm:
            x1, x2 = x1 * frame_w, x2 * frame_w
            y1, y2 = y1 * frame_h, y2 * frame_h
        x1, x2 = sorted((int(x1), int(x2)))
        y1, y2 = sorted((int(y1), int(y2)))
        return (max(0, x1), max(0, y1), min(frame_w, x2), min(frame_h, y2))

    def to_dict(self) -> Dict[str, Any]:
        return {"nombre": self.nombre, "tipo": self.tipo, "rect": self._raw}


# ── Planograma ───────────────────────────────────────────────────────

class StoreLayout:
    """Planograma de una camara: pasillos + anaqueles + mobiliario.

    Thread-safe para lectura/escritura desde la UI mientras corre el loop
    de inferencia. Se carga de ``config/store_layout/<camera>.json`` y se
    guarda con ``save()``.
    """

    def __init__(self, camera_id: Any = None, base_dir: str = None):
        self.camera_id = camera_id
        self._base_dir = base_dir or AnalyticsConfig.STORE_LAYOUT_DIR
        self._lock = threading.RLock()
        self.aisles: List[Aisle] = []
        self.shelves: List[Shelf] = []
        self.fixtures: List[Fixture] = []
        self._loaded_from: Optional[str] = None
        self.load()

    # ── Persistencia ─────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return os.path.join(self._base_dir,
                            f"{safe_name(self.camera_id)}.json")

    def load(self) -> bool:
        """Carga el planograma del JSON de esta camara.

        Si no existe el archivo de la camara, prueba ``default.json`` (util
        para desplegar un layout comun a varias camaras iguales). Nunca
        lanza: un planograma ausente solo desactiva la analitica de zona.
        """
        candidates = [self.path,
                      os.path.join(self._base_dir, "default.json")]
        for p in candidates:
            try:
                if not os.path.exists(p):
                    continue
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.from_dict(data)
                self._loaded_from = p
                logger.info(
                    "Planograma cargado (%s): %d pasillos, %d anaqueles, "
                    "%d mobiliario", p, len(self.aisles), len(self.shelves),
                    len(self.fixtures))
                return True
            except Exception as exc:  # noqa: BLE001 - degradar, no crashear
                logger.warning("No se pudo cargar planograma %s: %s", p, exc)
        logger.info(
            "Sin planograma para camara %s (esperado en %s). La analitica "
            "de pasillos/anaqueles queda inactiva hasta definirlo.",
            self.camera_id, self.path)
        return False

    def save(self) -> bool:
        """Escribe el planograma a disco de forma atomica."""
        with self._lock:
            data = self.to_dict()
        try:
            os.makedirs(self._base_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo guardar planograma: %s", exc)
            return False

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Reemplaza el planograma con el contenido de un dict/JSON."""
        aisles, shelves, fixtures = [], [], []
        for a in data.get("pasillos", []) or []:
            try:
                aisles.append(Aisle(a["nombre"], a["poligono"],
                                    a.get("categoria", "")))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Pasillo invalido en planograma: %s", exc)
        for s in data.get("anaqueles", []) or []:
            try:
                # Multi-poligono (nuevo) o poligono unico o rect (retrocompat).
                geom = s.get("poligonos") or s.get("poligono") or s.get("rect")
                if geom is None:
                    raise KeyError("poligonos/poligono/rect")
                shelves.append(Shelf(
                    s["nombre"], geom, s.get("pasillo", ""),
                    s.get("categoria", ""), s.get("precio", 0.0),
                    s.get("sku", "")))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Anaquel invalido en planograma: %s", exc)
        for f_ in data.get("mobiliario", []) or []:
            try:
                fixtures.append(Fixture(
                    f_["nombre"], f_["rect"],
                    f_.get("tipo", Fixture.TIPO_CONSULTA_PRECIO)))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Mobiliario invalido en planograma: %s", exc)
        with self._lock:
            self.aisles, self.shelves, self.fixtures = aisles, shelves, fixtures

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": str(self.camera_id),
            "actualizado": time.time(),
            "pasillos": [a.to_dict() for a in self.aisles],
            "anaqueles": [s.to_dict() for s in self.shelves],
            "mobiliario": [f.to_dict() for f in self.fixtures],
        }

    # ── Edicion en runtime (desde la UI) ─────────────────────────────

    def add_aisle(self, nombre: str, poligono, categoria: str = "") -> Aisle:
        a = Aisle(nombre, poligono, categoria)
        with self._lock:
            self.aisles = [x for x in self.aisles if x.nombre != a.nombre]
            self.aisles.append(a)
        return a

    def add_shelf(self, nombre: str, rect, pasillo: str = "",
                  categoria: str = "", precio: float = 0.0,
                  sku: str = "") -> Shelf:
        s = Shelf(nombre, rect, pasillo, categoria, precio, sku)
        with self._lock:
            self.shelves = [x for x in self.shelves if x.nombre != s.nombre]
            self.shelves.append(s)
        return s

    def add_fixture(self, nombre: str, rect,
                    tipo: str = Fixture.TIPO_CONSULTA_PRECIO) -> Fixture:
        f_ = Fixture(nombre, rect, tipo)
        with self._lock:
            self.fixtures = [x for x in self.fixtures if x.nombre != f_.nombre]
            self.fixtures.append(f_)
        return f_

    def remove(self, nombre: str) -> bool:
        """Elimina cualquier zona por nombre. True si borro algo."""
        with self._lock:
            n0 = len(self.aisles) + len(self.shelves) + len(self.fixtures)
            self.aisles = [x for x in self.aisles if x.nombre != nombre]
            self.shelves = [x for x in self.shelves if x.nombre != nombre]
            self.fixtures = [x for x in self.fixtures if x.nombre != nombre]
            n1 = len(self.aisles) + len(self.shelves) + len(self.fixtures)
        return n1 < n0

    def clear(self) -> None:
        with self._lock:
            self.aisles, self.shelves, self.fixtures = [], [], []

    # ── Consultas ────────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        return not (self.aisles or self.shelves or self.fixtures)

    def aisle_at(self, x: float, y: float, frame_w: int,
                 frame_h: int) -> Optional[Aisle]:
        """Pasillo que contiene el punto (px), o None. El primero que matchea
        (los pasillos no deberian solaparse)."""
        for a in self.aisles:
            if a.contains(x, y, frame_w, frame_h):
                return a
        return None

    def price_checkers(self) -> List[Fixture]:
        return [f for f in self.fixtures
                if f.tipo == Fixture.TIPO_CONSULTA_PRECIO]

    def shelves_of(self, pasillo: str) -> List[Shelf]:
        return [s for s in self.shelves if s.pasillo == pasillo]

    def shelf_by_name(self, nombre: str) -> Optional[Shelf]:
        for s in self.shelves:
            if s.nombre == nombre:
                return s
        return None
