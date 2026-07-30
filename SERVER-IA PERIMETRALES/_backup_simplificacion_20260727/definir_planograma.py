"""
tools/definir_planograma.py - Dibuja el planograma de una camara con el raton.

    ┌──────────────────────────────────────────────────────────────────┐
    │ LO NORMAL ES NO USAR ESTA HERRAMIENTA.                           │
    │                                                                  │
    │ En el despliegue habitual el SERVIDOR NO SE CONECTA A LA CAMARA: │
    │ es el cliente (windows_managers_view) quien captura los frames y │
    │ se los envia. Por eso el planograma se dibuja DESDE EL CLIENTE:  │
    │                                                                  │
    │     Tienda ▾ → "Definir zonas de la tienda…"                     │
    │                                                                  │
    │ Eso lo sube al servidor via POST /retail/layout y queda igual    │
    │ que si se hubiera generado aqui.                                 │
    └──────────────────────────────────────────────────────────────────┘

Esta herramienta sirve para los casos en que SI hay imagen en la maquina
del servidor: una foto de la escena, un video grabado, una camara RTSP
accesible desde el servidor, o el snapshot que deja el heatmap.

Genera el JSON que consume la analitica de supermercado
(``config/store_layout/<camera_id>.json``): pasillos, anaqueles y la maquina
consultora de precios.

Uso:

    # Desde una foto o un video de la escena (lo mas comun aqui)
    python tools/definir_planograma.py --camera cam1 --fuente escena.jpg

    # Desde una camara RTSP accesible DESDE EL SERVIDOR
    python tools/definir_planograma.py --camera cam1 --fuente rtsp://user:pass@ip/stream

    # Sobre el ultimo snapshot que dejo el heatmap de esa camara
    # (lo escribe el servidor con los frames que le manda el cliente,
    #  asi que existe aunque el servidor no vea la camara)
    python tools/definir_planograma.py --camera cam1

Controles:

    1        modo PASILLO   (poligono: click por vertice, ENTER para cerrar)
    2        modo ANAQUEL   (rectangulo: arrastra con el boton izquierdo)
    3        modo MAQUINA DE PRECIOS (rectangulo)
    click izq  anade vertice / empieza rectangulo
    ENTER    cierra el poligono en curso y pide su nombre por consola
    z        deshace la ultima zona
    g        guarda el planograma
    r        recarga el frame de la camara
    q / ESC  salir (pregunta si guardar)

Las coordenadas se guardan NORMALIZADAS 0..1 para que el planograma siga
siendo valido si cambia la resolucion de la camara.
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from src.analityc.core.analytics.config import AnalyticsConfig  # noqa: E402
from src.analityc.core.analytics.store_layout import (  # noqa: E402
    Fixture, StoreLayout,
)

MODO_PASILLO = "pasillo"
MODO_ANAQUEL = "anaquel"
MODO_MAQUINA = "maquina"

VENTANA = "Planograma - 1:pasillo 2:anaquel 3:maquina | ENTER cierra | g guarda | q sale"


class Editor:
    def __init__(self, layout: StoreLayout, frame: np.ndarray):
        self.layout = layout
        self.frame = frame
        self.h, self.w = frame.shape[:2]
        self.modo = MODO_PASILLO
        self.puntos: list = []
        self.arrastrando = False
        self.rect_ini = None
        self.rect_fin = None

    # ── Raton ────────────────────────────────────────────────────────

    def on_mouse(self, event, x, y, flags, _param):
        if self.modo == MODO_PASILLO:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.puntos.append((x, y))
            elif event == cv2.EVENT_RBUTTONDOWN and self.puntos:
                self.puntos.pop()
            return
        # Rectangulo (anaquel / maquina)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.arrastrando = True
            self.rect_ini = (x, y)
            self.rect_fin = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.arrastrando:
            self.rect_fin = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.arrastrando:
            self.arrastrando = False
            self.rect_fin = (x, y)
            self._cerrar_rect()

    # ── Alta de zonas ────────────────────────────────────────────────

    def _norm_rect(self):
        x1, y1 = self.rect_ini
        x2, y2 = self.rect_fin
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if (x2 - x1) < 8 or (y2 - y1) < 8:
            return None
        return [round(x1 / self.w, 4), round(y1 / self.h, 4),
                round(x2 / self.w, 4), round(y2 / self.h, 4)]

    def _cerrar_rect(self):
        rect = self._norm_rect()
        self.rect_ini = self.rect_fin = None
        if rect is None:
            print("  (rectangulo demasiado pequeno, descartado)")
            return
        if self.modo == MODO_MAQUINA:
            nombre = input("  Nombre de la maquina de precios: ").strip()
            if not nombre:
                return
            self.layout.add_fixture(nombre, rect, Fixture.TIPO_CONSULTA_PRECIO)
            print(f"  + Maquina '{nombre}'")
            return
        nombre = input("  Nombre del PRODUCTO del anaquel: ").strip()
        if not nombre:
            return
        pasillo = input("  Pasillo al que pertenece (ENTER si ninguno): ").strip()
        categoria = input("  Categoria (ENTER para omitir): ").strip()
        sku = input("  SKU (ENTER para omitir): ").strip()
        precio_txt = input("  Precio unitario (ENTER = 0): ").strip()
        try:
            precio = float(precio_txt) if precio_txt else 0.0
        except ValueError:
            precio = 0.0
        self.layout.add_shelf(nombre, rect, pasillo, categoria, precio, sku)
        print(f"  + Anaquel '{nombre}' ({precio})")

    def cerrar_poligono(self):
        if self.modo != MODO_PASILLO or len(self.puntos) < 3:
            print("  (hacen falta al menos 3 puntos para un pasillo)")
            return
        poly = [[round(x / self.w, 4), round(y / self.h, 4)]
                for x, y in self.puntos]
        nombre = input("  Nombre del PASILLO: ").strip()
        if nombre:
            categoria = input("  Categoria del pasillo (ENTER omite): ").strip()
            self.layout.add_aisle(nombre, poly, categoria)
            print(f"  + Pasillo '{nombre}' ({len(poly)} vertices)")
        self.puntos = []

    def deshacer(self):
        for coll, etiqueta in ((self.layout.fixtures, "maquina"),
                               (self.layout.shelves, "anaquel"),
                               (self.layout.aisles, "pasillo")):
            if coll:
                z = coll.pop()
                print(f"  - Eliminado {etiqueta} '{z.nombre}'")
                return
        print("  (nada que deshacer)")

    # ── Render ───────────────────────────────────────────────────────

    def render(self) -> np.ndarray:
        img = self.frame.copy()
        for a in self.layout.aisles:
            poly = a.polygon_px(self.w, self.h)
            cv2.polylines(img, [poly], True, (0, 200, 255), 2)
            x, y = int(poly[:, 0].min()), int(poly[:, 1].min())
            cv2.putText(img, a.nombre, (x + 4, max(14, y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 2)
        for s in self.layout.shelves:
            x1, y1, x2, y2 = s.rect_px(self.w, self.h)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(img, s.nombre, (x1 + 3, max(12, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        for f in self.layout.fixtures:
            x1, y1, x2, y2 = f.rect_px(self.w, self.h)
            cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 255), 2)
            cv2.putText(img, f.nombre, (x1 + 3, max(12, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1)

        # Zona en curso
        if self.puntos:
            for p in self.puntos:
                cv2.circle(img, p, 4, (255, 255, 255), -1)
            if len(self.puntos) > 1:
                cv2.polylines(img, [np.array(self.puntos, np.int32)], False,
                              (255, 255, 255), 2)
        if self.arrastrando and self.rect_ini and self.rect_fin:
            cv2.rectangle(img, self.rect_ini, self.rect_fin, (255, 255, 255), 2)

        etiqueta = {MODO_PASILLO: "PASILLO (poligono)",
                    MODO_ANAQUEL: "ANAQUEL (rectangulo)",
                    MODO_MAQUINA: "MAQUINA DE PRECIOS"}[self.modo]
        cv2.rectangle(img, (0, 0), (self.w, 30), (0, 0, 0), -1)
        cv2.putText(img, f"MODO: {etiqueta}   |   pasillos:"
                         f"{len(self.layout.aisles)} anaqueles:"
                         f"{len(self.layout.shelves)} maquinas:"
                         f"{len(self.layout.fixtures)}",
                    (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        return img


def leer_frame(fuente: str, camera_id: str):
    """Obtiene un frame de la fuente, o del snapshot de fondo del heatmap."""
    if not fuente:
        bg = os.path.join(AnalyticsConfig.HEATMAP_DIR, "bg",
                          f"{camera_id}.jpg")
        if os.path.exists(bg):
            img = cv2.imread(bg)
            if img is not None:
                print(f"Frame tomado del snapshot del heatmap: {bg}")
                return img
        print("ERROR: sin --fuente y sin snapshot previo en "
              f"{bg}. Arranca el servidor un momento o pasa --fuente.")
        return None

    if os.path.exists(fuente) and fuente.lower().endswith(
            (".jpg", ".jpeg", ".png", ".bmp")):
        return cv2.imread(fuente)

    cap = cv2.VideoCapture(fuente)
    if not cap.isOpened():
        print(f"ERROR: no se pudo abrir la fuente: {fuente}")
        return None
    frame = None
    for _ in range(10):  # descartar los primeros (buffer/autoexposicion)
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", required=True,
                    help="camera_id (nombre del JSON del planograma)")
    ap.add_argument("--fuente", default="",
                    help="RTSP/video/imagen. Por defecto usa el snapshot "
                         "de fondo que deja el heatmap de esa camara.")
    args = ap.parse_args()

    frame = leer_frame(args.fuente, args.camera)
    if frame is None or frame.size == 0:
        return 1

    layout = StoreLayout(args.camera)
    editor = Editor(layout, frame)
    print(f"\nPlanograma: {layout.path}")
    print("Controles: 1 pasillo | 2 anaquel | 3 maquina | ENTER cierra "
          "poligono | z deshace | g guarda | q sale\n")

    cv2.namedWindow(VENTANA, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(VENTANA, editor.on_mouse)
    guardado = True

    while True:
        cv2.imshow(VENTANA, editor.render())
        k = cv2.waitKey(20) & 0xFF
        if k == ord("1"):
            editor.modo, editor.puntos = MODO_PASILLO, []
            print("MODO pasillo")
        elif k == ord("2"):
            editor.modo, editor.puntos = MODO_ANAQUEL, []
            print("MODO anaquel")
        elif k == ord("3"):
            editor.modo, editor.puntos = MODO_MAQUINA, []
            print("MODO maquina de precios")
        elif k in (13, 10):  # ENTER
            editor.cerrar_poligono()
            guardado = False
        elif k == ord("z"):
            editor.deshacer()
            guardado = False
        elif k == ord("g"):
            if layout.save():
                print(f"GUARDADO -> {layout.path}")
                guardado = True
            else:
                print("ERROR al guardar")
        elif k in (ord("q"), 27):
            if not guardado:
                if input("Hay cambios sin guardar. Guardar? [S/n] ") \
                        .strip().lower() in ("", "s", "si", "y"):
                    layout.save()
                    print(f"GUARDADO -> {layout.path}")
            break
        if k != 255:
            guardado = guardado and k not in (13, 10, ord("z"))

    cv2.destroyAllWindows()
    print(f"\nResumen: {len(layout.aisles)} pasillos, "
          f"{len(layout.shelves)} anaqueles, {len(layout.fixtures)} maquinas")
    print("Siguiente paso: con los estantes REPUESTOS y sin gente delante, "
          "llama a capture_shelf_references() para calibrar el 100% de "
          "llenado de cada anaquel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
