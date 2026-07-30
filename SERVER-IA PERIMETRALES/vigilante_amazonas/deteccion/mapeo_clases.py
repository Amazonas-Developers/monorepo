"""
Mapeo de clases COCO -> clases propias de VIGILANTE-AMAZONAS.

Clases propias: persona, personal_seguridad, moto, carro, camioneta, camion,
objeto. `personal_seguridad` NO sale de aquí: la asigna el clasificador
secundario (Hito 3) sobre detecciones de `persona`.

LIMITACIONES DOCUMENTADAS (heurística camioneta/camión):
  COCO no distingue pickup de camión (ambos suelen caer en truck=7, y las
  pickups a veces en car=2). Se usa una heurística de tamaño relativo y
  aspecto de la caja:
    - truck con área relativa chica y caja alargada  -> camioneta (pickup)
    - truck grande o muy alto                        -> camion
    - bus (5)                                        -> camion (aprox.)
  Esto FALLA con camiones lejanos (área chica) y pickups muy cerca de la
  cámara. Si se necesita precisión real, el camino es un clasificador
  secundario de vehículos (existe models/vehiculos/yolo11s-cls.pt como base).
"""

from __future__ import annotations

import numpy as np

from vigilante_amazonas import config

# Orden fijo: el índice es el class_id que viaja al tracker y al cliente.
CLASES_PROPIAS: list[str] = [
    "persona", "personal_seguridad", "moto", "carro", "camioneta", "camion", "objeto",
]
CLASE_A_ID: dict[str, int] = {c: i for i, c in enumerate(CLASES_PROPIAS)}
ID_A_CLASE: dict[int, str] = {i: c for i, c in enumerate(CLASES_PROPIAS)}

# Nombres COCO de referencia (solo documentación/logs).
COCO_NOMBRES: dict[int, str] = {
    0: "person", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
    24: "backpack", 26: "handbag", 28: "suitcase",
}


def mapear_clase(cls_coco: int,
                 bbox_xyxy: tuple[float, float, float, float],
                 forma_frame: tuple[int, ...]) -> str | None:
    """Convierte una clase COCO en clase propia usando la heurística de caja.

    Devuelve None si la clase COCO no interesa (no debería pasar si el
    detector filtra con config.CLASES_COCO_USADAS).
    """
    if cls_coco == 0:
        return "persona"
    if cls_coco == 3:
        return "moto"
    if cls_coco in config.OBJETO_CLASES_COCO:
        return "objeto"
    if cls_coco == 5:
        return "camion"          # bus ~ porte de camión (limitación documentada)
    if cls_coco in (2, 7):
        x1, y1, x2, y2 = bbox_xyxy
        alto_frame, ancho_frame = int(forma_frame[0]), int(forma_frame[1])
        ancho = max(1.0, x2 - x1)
        alto = max(1.0, y2 - y1)
        aspecto: float = ancho / alto
        area_rel: float = (ancho * alto) / float(max(1, alto_frame * ancho_frame))
        if cls_coco == 2:
            return "carro"       # car -> carro (pickups mal clasificadas: limitación)
        # truck: chico y alargado -> camioneta; grande/alto -> camión.
        if area_rel < config.CAMIONETA_AREA_REL_MAX and aspecto >= config.CAMION_ASPECTO_MIN:
            return "camioneta"
        return "camion"
    return None


def color_de(clase: str) -> tuple[int, int, int]:
    """Color BGR de la clase para el visor/anotaciones."""
    return config.COLORES_CLASE.get(clase, (200, 200, 200))


def a_ids(clases: list[str]) -> np.ndarray:
    """Lista de clases propias -> array de class_id (para supervision)."""
    return np.array([CLASE_A_ID[c] for c in clases], dtype=int)
