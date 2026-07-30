from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QPixmap, QMouseEvent, QPainter, QBrush, QPen, QColor


# Colores BGR-independientes para Qt (RGB)
_BLUE  = QColor(40, 120, 255)   # Toma de orden
_RED   = QColor(255, 60, 60)    # Entrega de plato


class Interactive_imageLabel(QLabel):

    # Personas + Toma de orden + Entrega de plato
    point_change = Signal(list, bool, list, bool, list, bool)

    def __init__(self, parent=None,
                 roi=None,
                 roi_active=False,
                 order_zone=None,
                 order_zone_active=False,
                 delivery_zone=None,
                 delivery_zone_active=False,
                 **kwargs):
        super().__init__(parent)
        self.setMouseTracking(True)

        # Compatibilidad con dicts que vienen como '*_activate'
        roi_active_val            = kwargs.get('roi_activate', roi_active)
        order_zone_active_val     = kwargs.get('order_zone_activate', order_zone_active)
        delivery_zone_active_val  = kwargs.get('delivery_zone_activate', delivery_zone_active)

        # ROI de personas (amarillo)
        self.show_points = bool(roi_active_val)
        self.points_hidden = False           # toggle visual global
        self.points_editable = True          # compat
        self.points = self.list_to_qpoints(roi or [])

        # ROI de Toma de orden (azul)
        self.order_zone = self.list_to_qpoints(order_zone or [])
        self.order_zone_active = bool(order_zone_active_val)

        # ROI de Entrega de plato (rojo)
        self.delivery_zone = self.list_to_qpoints(delivery_zone or [])
        self.delivery_zone_active = bool(delivery_zone_active_val)

        # Qué conjunto de puntos edita el mouse: 'roi' | 'order' | 'delivery'
        self.edit_target = 'roi'

        self.active_point_index = -1
        self.point_radius = 10
        self.current_pixmap = QPixmap()

        if not self.show_points:
            self.hide_points()

    # ------------------------------------------------------------
    # CONTROL DE VISIBILIDAD
    # ------------------------------------------------------------
    def hide_points(self):
        self.show_points = False
        self.update()

    def show_points_fn(self):
        self.show_points = True
        self.update()

    def toggle_points(self):
        self.show_points = not self.show_points
        self.update()

    def toggle_points_visibility(self):
        """Oculta/muestra TODO el overlay sin desactivar los ROI."""
        self.points_hidden = not self.points_hidden
        self.update()

    def toggle_order_zone(self, state: bool = None):
        if state is None:
            self.order_zone_active = not self.order_zone_active
        else:
            self.order_zone_active = bool(state)
        self.update()

    def toggle_delivery_zone(self, state: bool = None):
        if state is None:
            self.delivery_zone_active = not self.delivery_zone_active
        else:
            self.delivery_zone_active = bool(state)
        self.update()

    def set_edit_target(self, target: str):
        if target in ('roi', 'order', 'delivery'):
            self.edit_target = target
            self.active_point_index = -1
            self.update()

    # ------------------------------------------------------------
    # PIXMAP
    # ------------------------------------------------------------
    def setPixmap(self, pixmap):
        self.current_pixmap = pixmap
        super().setPixmap(pixmap)
        self.update()

    # ------------------------------------------------------------
    # CONVERSIÓN DE COORDENADAS (0-1000 ↔ pixeles)
    # ------------------------------------------------------------
    def get_scaled_point(self, point_percentage: QPoint) -> QPoint:
        width = self.width()
        height = self.height()
        x = int(point_percentage.x() * width / 1000)
        y = int(point_percentage.y() * height / 1000)
        return QPoint(x, y)

    def get_percentage_point(self, point_pixel: QPoint) -> QPoint:
        width = self.width()
        height = self.height()
        if width == 0 or height == 0:
            return QPoint(0, 0)
        x = int(point_pixel.x() * 1000 / width)
        y = int(point_pixel.y() * 1000 / height)
        x = max(0, min(x, 1000))
        y = max(0, min(y, 1000))
        return QPoint(x, y)

    # ------------------------------------------------------------
    # DIBUJO
    # ------------------------------------------------------------
    def _draw_polygon(self, painter: QPainter, pts, line_color: QColor,
                      fill_color: QColor, target_name: str):
        if not pts:
            return
        last = None
        for i, pt in enumerate(pts):
            pix = self.get_scaled_point(pt)
            if last is not None:
                painter.setPen(QPen(line_color, 2, Qt.SolidLine))
                painter.drawLine(last, pix)
            last = pix
            color = Qt.white if (self.edit_target == target_name
                                 and i == self.active_point_index) else fill_color
            painter.setPen(QPen(Qt.black, 2))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(pix, self.point_radius, self.point_radius)
        if last is not None and len(pts) > 1:
            first = self.get_scaled_point(pts[0])
            painter.setPen(QPen(line_color, 2, Qt.SolidLine))
            painter.drawLine(last, first)

    def paintEvent(self, event):
        super().paintEvent(event)

        if self.points_hidden:
            return
        any_visible = self.show_points or self.order_zone_active or self.delivery_zone_active
        if not any_visible:
            return
        if self.width() < 1 or self.height() < 1 or self.current_pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # ROI de personas (amarillo, líneas blancas punteadas)
        if self.show_points and self.points:
            last_p = None
            for i, p_perc in enumerate(self.points):
                p_pix = self.get_scaled_point(p_perc)
                if last_p is not None:
                    painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
                    painter.drawLine(last_p, p_pix)
                last_p = p_pix
                color = Qt.red if i == self.active_point_index and self.edit_target == 'roi' else Qt.yellow
                painter.setPen(QPen(Qt.black, 2))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(p_pix, self.point_radius, self.point_radius)
            if last_p is not None and len(self.points) > 1:
                first_p = self.get_scaled_point(self.points[0])
                painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
                painter.drawLine(last_p, first_p)

        # Toma de orden (azul)
        if self.order_zone_active:
            self._draw_polygon(painter, self.order_zone, _BLUE, _BLUE, 'order')

        # Entrega de plato (rojo)
        if self.delivery_zone_active:
            self._draw_polygon(painter, self.delivery_zone, _RED, _RED, 'delivery')

        painter.end()

    # ------------------------------------------------------------
    # EVENTOS DEL MOUSE
    # ------------------------------------------------------------
    def _interactive(self) -> bool:
        if self.points_hidden:
            return False
        return self.show_points or self.order_zone_active or self.delivery_zone_active

    def mousePressEvent(self, event: QMouseEvent):
        if not self._interactive():
            return super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            sets = [
                ('roi',      self.points,         self.show_points),
                ('order',    self.order_zone,     self.order_zone_active),
                ('delivery', self.delivery_zone,  self.delivery_zone_active),
            ]
            # Prioriza el conjunto seleccionado en el combo
            sets.sort(key=lambda s: 0 if s[0] == self.edit_target else 1)
            for name, pts, active in sets:
                if not active or not pts:
                    continue
                for i, pt in enumerate(pts):
                    p_pix = self.get_scaled_point(pt)
                    dx = event.pos().x() - p_pix.x()
                    dy = event.pos().y() - p_pix.y()
                    if (dx * dx + dy * dy) ** 0.5 < self.point_radius:
                        self.edit_target = name
                        self.active_point_index = i
                        self.update()
                        return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._interactive():
            return super().mouseMoveEvent(event)
        if self.active_point_index != -1 and event.buttons() & Qt.LeftButton:
            new_pos = event.pos()
            new_pos.setX(max(0, min(new_pos.x(), self.width())))
            new_pos.setY(max(0, min(new_pos.y(), self.height())))
            new_perc = self.get_percentage_point(new_pos)
            target = self.edit_target
            if target == 'roi':
                self.points[self.active_point_index] = new_perc
            elif target == 'order':
                self.order_zone[self.active_point_index] = new_perc
            elif target == 'delivery':
                self.delivery_zone[self.active_point_index] = new_perc
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if not self._interactive():
            return super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.active_point_index != -1:
            self.active_point_index = -1
            self.update()
            self.point_change.emit(
                self.qpoints_to_list(self.points),         self.show_points,
                self.qpoints_to_list(self.order_zone),     self.order_zone_active,
                self.qpoints_to_list(self.delivery_zone),  self.delivery_zone_active,
            )
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------
    # COORDENADAS EN PIXELES ORIGINALES
    # ------------------------------------------------------------
    def _coords_pixels(self, qpoints, image_width, image_height):
        if image_width <= 0 or image_height <= 0:
            return []
        return [
            [int(p.x() * image_width / 1000), int(p.y() * image_height / 1000)]
            for p in qpoints
        ]

    def get_coordinates(self, image_width: int, image_height: int):
        return self._coords_pixels(self.points, image_width, image_height)

    def get_order_zone_coordinates(self, image_width: int, image_height: int):
        return self._coords_pixels(self.order_zone, image_width, image_height)

    def get_delivery_zone_coordinates(self, image_width: int, image_height: int):
        return self._coords_pixels(self.delivery_zone, image_width, image_height)

    # ------------------------------------------------------------
    # SERIALIZACIÓN
    # ------------------------------------------------------------
    def qpoints_to_list(self, qpoints):
        return [[p.x(), p.y()] for p in qpoints]

    def list_to_qpoints(self, data):
        return [QPoint(c[0], c[1]) for c in (data or [])]

    def set_roi(self, data):
        self.points = self.list_to_qpoints(data)
        self.update()

    def set_order_zone(self, data):
        self.order_zone = self.list_to_qpoints(data)
        self.update()

    def set_delivery_zone(self, data):
        self.delivery_zone = self.list_to_qpoints(data)
        self.update()
