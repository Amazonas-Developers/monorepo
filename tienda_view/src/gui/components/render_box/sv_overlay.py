"""Overlay de Supervision (Roboflow) para el MODO DIRECTO del cliente.

El servidor manda las detecciones (modo directo, baja latencia) y aqui se
convierten en un ``sv.Detections`` para anotarlas sobre el frame numpy BGR:
cajas redondeadas / elipses, etiquetas, ESTELAS de movimiento (por tracker_id),
HEATMAP en vivo y CONTEO en zonas (PolygonZone) usando el ROI/zonas que el
cliente ya define.

Es un modulo aislado y tolerante a fallos: si Supervision no esta disponible o
algo falla, `available` queda False / `annotate` devuelve el frame sin tocar y
el cliente cae a su dibujo con QPainter.
"""

import numpy as np

try:
    import supervision as sv
    _SV_OK = True
except Exception:  # pragma: no cover - entorno sin supervision
    _SV_OK = False


def _palette():
    # RGB, coherente con category_colors del servidor (Hombres/Mujeres/Ninos/Personas).
    return sv.ColorPalette(colors=[
        sv.Color(0, 180, 255),    # 0 Hombres  (azul)
        sv.Color(255, 0, 180),    # 1 Mujeres  (magenta)
        sv.Color(180, 255, 0),    # 2 Ninos    (verde-lima)
        sv.Color(255, 255, 0),    # 3 Personas (amarillo)
    ])


class SupervisionOverlay:
    """Anota un frame BGR con las detecciones del servidor usando Supervision."""

    available = _SV_OK

    def __init__(self):
        if not _SV_OK:
            return
        pal = _palette()
        self._pal = pal
        self.style = "round"          # "round" | "ellipse"
        self.show_trace = True
        self.show_heatmap = False
        self._round = sv.RoundBoxAnnotator(color=pal, thickness=2)
        self._ellipse = sv.EllipseAnnotator(color=pal, thickness=2)
        self._label = sv.LabelAnnotator(
            color=pal, text_color=sv.Color.WHITE, text_scale=0.5,
            text_thickness=1, text_padding=4,
            text_position=sv.Position.TOP_LEFT, smart_position=True)
        self._trace = sv.TraceAnnotator(
            color=pal, position=sv.Position.BOTTOM_CENTER,
            trace_length=40, thickness=2)
        self._heatmap = sv.HeatMapAnnotator(opacity=0.5, radius=35)
        self._zone_cache = {}         # poligono -> (PolygonZone, PolygonZoneAnnotator)

    # ---------------------------------------------------------------
    @staticmethod
    def _to_detections(detections):
        """Lista del servidor [{box,label,confidence,class_id,tracker_id}] ->
        (sv.Detections, labels)."""
        xyxy, conf, cls, tid, labels = [], [], [], [], []
        for i, d in enumerate(detections or []):
            b = d.get("box")
            if not b or len(b) < 4:
                continue
            xyxy.append([float(b[0]), float(b[1]), float(b[2]), float(b[3])])
            conf.append(float(d.get("confidence", 0.0) or 0.0))
            cls.append(int(d.get("class_id", 3)))
            tid.append(int(d.get("tracker_id", i)))
            labels.append(str(d.get("label", "")))
        if not xyxy:
            return sv.Detections.empty(), []
        det = sv.Detections(
            xyxy=np.array(xyxy, dtype=float),
            confidence=np.array(conf, dtype=float),
            class_id=np.array(cls, dtype=int),
            tracker_id=np.array(tid, dtype=int),
        )
        return det, labels

    def _zone(self, polygon_px):
        key = tuple(tuple(int(v) for v in p) for p in polygon_px)
        z = self._zone_cache.get(key)
        if z is None:
            if len(self._zone_cache) > 64:
                self._zone_cache.clear()
            zone = sv.PolygonZone(
                polygon=np.array(polygon_px, dtype=np.int64),
                triggering_anchors=(sv.Position.BOTTOM_CENTER,))
            za = sv.PolygonZoneAnnotator(
                zone=zone, color=sv.Color(255, 215, 0), thickness=2,
                text_scale=0.6, text_thickness=2, opacity=0.10)
            z = (zone, za)
            self._zone_cache[key] = z
        return z

    def reset_heatmap(self):
        if _SV_OK:
            self._heatmap = sv.HeatMapAnnotator(opacity=0.5, radius=35)

    def reset_trace(self):
        """Limpia las estelas acumuladas (recrea el TraceAnnotator)."""
        if _SV_OK:
            self._trace = sv.TraceAnnotator(
                color=self._pal, position=sv.Position.BOTTOM_CENTER,
                trace_length=40, thickness=2)

    def reset_all(self):
        """Reinicia el estado visual acumulado: estelas + heatmap."""
        self.reset_trace()
        self.reset_heatmap()

    # ---------------------------------------------------------------
    def annotate(self, frame_bgr, detections, zones=None):
        """frame_bgr: numpy BGR. detections: lista del servidor. zones: lista de
        poligonos [[x,y],...] en pixeles del frame. Devuelve el frame anotado."""
        if not _SV_OK or frame_bgr is None:
            return frame_bgr
        det, labels = self._to_detections(detections)
        scene = frame_bgr
        n = len(det)
        if self.show_heatmap and n:
            try:
                scene = self._heatmap.annotate(scene, det)
            except Exception:
                pass
        if self.show_trace and n:
            try:
                scene = self._trace.annotate(scene, det)
            except Exception:
                pass
        if n:
            try:
                ann = self._ellipse if self.style == "ellipse" else self._round
                scene = ann.annotate(scene, det)
                scene = self._label.annotate(scene, det, labels=labels)
            except Exception:
                pass
        for poly in (zones or []):
            if not poly or len(poly) < 3:
                continue
            try:
                zone, za = self._zone(poly)
                zone.trigger(det)
                scene = za.annotate(scene, label=str(int(zone.current_count)))
            except Exception:
                pass
        return scene
