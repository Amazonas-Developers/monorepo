"""
utils/overlay.py - Funciones de dibujo en frame para analitica retail.

Paneles semi-transparentes con contadores demograficos, eficiencia
de vendedores, indicadores de stock y banner de premios.
"""

import cv2
import numpy as np
import time
from typing import Dict, List, Optional, Tuple


# ── Colores ──
COLOR_WHITE   = (255, 255, 255)
COLOR_BLACK   = (0, 0, 0)
COLOR_BLUE    = (255, 180, 0)
COLOR_PINK    = (180, 0, 255)
COLOR_CYAN    = (0, 255, 180)
COLOR_YELLOW  = (0, 255, 255)
COLOR_GREEN   = (0, 200, 0)
COLOR_RED     = (0, 0, 255)
COLOR_ORANGE  = (0, 165, 255)
COLOR_GOLD    = (0, 215, 255)
COLOR_GRAY    = (120, 120, 120)

# Semaforo de stock
STOCK_COLORS = {
    "OK":      COLOR_GREEN,
    "BAJO":    COLOR_ORANGE,
    "AGOTADO": COLOR_RED,
}

FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SMALL = 0.45
FONT_MEDIUM = 0.55
FONT_LARGE = 0.7
FONT_XLARGE = 0.9


def _draw_panel(image: np.ndarray, x: int, y: int, w: int, h: int,
                alpha: float = 0.7, color: tuple = COLOR_BLACK) -> np.ndarray:
    """Dibuja un panel semi-transparente."""
    overlay = image.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, -1)
    cv2.addWeighted(overlay, alpha, image, 1 - alpha, 0, image)
    return image


def draw_demographics_panel(image: np.ndarray, counts: Dict, x: int = 10, y: int = 10) -> np.ndarray:
    """Panel lateral izquierdo: contadores demograficos.

    Args:
        counts: {"gender": {"Hombre": N, "Mujer": N}, "age": {"0-12": N, ...}, "total_classified": N}
    """
    gender = counts.get("gender", {})
    age = counts.get("age", {})
    total = counts.get("total_classified", 0)

    lines = [
        f"Demograficas ({total})",
        f"  Hombres: {gender.get('Hombre', 0)}",
        f"  Mujeres: {gender.get('Mujer', 0)}",
        "",
        "Rango de Edad:",
    ]

    age_ranges = ["0-12", "13-17", "18-25", "26-35", "36-50", "51-65", "65+"]
    for ar in age_ranges:
        count = age.get(ar, 0)
        if count > 0:
            lines.append(f"  {ar}: {count}")

    line_h = 20
    panel_h = 12 + line_h * len(lines) + 12
    panel_w = 200

    image = _draw_panel(image, x, y, panel_w, panel_h, 0.75)

    ty = y + 12 + 14
    for i, line in enumerate(lines):
        if line == "":
            ty += 6
            continue
        color = COLOR_WHITE if i == 0 else (200, 200, 200)
        scale = FONT_MEDIUM if i == 0 else FONT_SMALL
        thickness = 2 if i == 0 else 1
        cv2.putText(image, line, (x + 8, ty), FONT, scale, color, thickness)
        ty += line_h

    return image


def draw_people_total(image: np.ndarray, total_unique: int,
                      active_now: int, x: int = -1, y: int = 10) -> np.ndarray:
    """Parte superior central: personas totales y activas ahora.

    Si x=-1, centra automaticamente.
    """
    combined = f"Personas totales: {total_unique}  |  Activas: {active_now}"
    (tw, th), _ = cv2.getTextSize(combined, FONT, FONT_MEDIUM, 2)

    if x == -1:
        x = (image.shape[1] - tw - 24) // 2

    panel_w = tw + 24
    panel_h = th + 20

    image = _draw_panel(image, x, y, panel_w, panel_h, 0.75)
    cv2.putText(image, combined, (x + 12, y + th + 10), FONT, FONT_MEDIUM, COLOR_WHITE, 2)

    return image
