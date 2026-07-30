"""
planogram_editor.py - Editor visual del planograma de la tienda.

Dibuja sobre el frame de la camara que zona es cada cosa: PASILLOS
(poligonos), ANAQUELES (rectangulos, uno por producto) y la MAQUINA
CONSULTORA DE PRECIOS. Al guardar, sube el planograma al servidor
(``POST /retail/layout``), que es quien lo persiste y lo aplica.

Por que vive en el CLIENTE y no en el servidor: en esta arquitectura el
servidor nunca se conecta a la camara — es el cliente el que captura los
frames (ventana o DVR) y se los envia. El unico sitio donde hay una imagen
que mostrarle al operador para que dibuje es aqui.

Las coordenadas se normalizan a 0..1 (fraccion del ancho/alto del frame),
asi el planograma sigue siendo valido si cambia la resolucion.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

MODO_PASILLO = "pasillo"
MODO_ANAQUEL = "anaquel"
MODO_MAQUINA = "maquina"

_COLOR = {
    MODO_PASILLO: QColor(0, 200, 255),
    MODO_ANAQUEL: QColor(0, 220, 0),
    MODO_MAQUINA: QColor(255, 0, 255),
}

_QSS = """
QDialog { background: #1e1e1e; }
QLabel { color: #ddd; }
QListWidget { background: #252525; color: #ddd; border: 1px solid #444; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #2b2b2b; color: #fff;
    border: 1px solid #555; border-radius: 3px; padding: 4px; }
QPushButton { background: #333; color: #fff; border: 1px solid #555;
              border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background: #3d6fb0; }
QPushButton:checked { background: #3d6fb0; font-weight: bold; }
"""


class _ShelfDialog(QDialog):
    """Pide los datos de la seccion de anaquel recien dibujada.

    Permite crear un anaquel NUEVO o AÑADIR la seccion a uno existente (un
    mismo producto/categoria suele ocupar varias secciones de estante)."""

    NUEVO = "(nuevo anaquel)"

    def __init__(self, pasillos, existentes=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Seccion de anaquel")
        self.setStyleSheet(_QSS)
        lay = QFormLayout(self)

        # Anaquel destino: nuevo o uno ya definido (agrega otra seccion).
        self.destino = QComboBox()
        self.destino.addItem(self.NUEVO)
        self.destino.addItems(existentes or [])
        self.destino.currentIndexChanged.connect(self._toggle_campos)
        lay.addRow("Anaquel", self.destino)

        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Ej: Shampoos")
        self.pasillo = QComboBox()
        self.pasillo.addItem("(ninguno)")
        self.pasillo.addItems(pasillos)
        self.categoria = QLineEdit()
        self.categoria.setPlaceholderText("Ej: Cuidado capilar")
        self.sku = QLineEdit()
        self.precio = QDoubleSpinBox()
        self.precio.setRange(0.0, 1_000_000.0)
        self.precio.setDecimals(2)
        self._rows = [("Nombre *", self.nombre), ("Pasillo", self.pasillo),
                      ("Categoria", self.categoria), ("SKU", self.sku),
                      ("Precio", self.precio)]
        for etiqueta, widget in self._rows:
            lay.addRow(etiqueta, widget)

        self.aviso = QLabel("")
        self.aviso.setWordWrap(True)
        self.aviso.setStyleSheet("color:#7bc47b;")
        lay.addRow(self.aviso)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def _toggle_campos(self):
        es_nuevo = self.destino.currentIndex() == 0
        for _etq, w in self._rows:
            w.setEnabled(es_nuevo)
        self.aviso.setText("" if es_nuevo else
                           "Se añade esta seccion al anaquel seleccionado "
                           "(sus datos no cambian).")

    def datos(self):
        if self.destino.currentIndex() > 0:      # anaquel existente
            return {"modo": "existente",
                    "nombre": self.destino.currentText()}
        pas = self.pasillo.currentText()
        return {
            "modo": "nuevo",
            "nombre": self.nombre.text().strip(),
            "pasillo": "" if pas == "(ninguno)" else pas,
            "categoria": self.categoria.text().strip(),
            "sku": self.sku.text().strip(),
            "precio": float(self.precio.value()),
        }


class _Lienzo(QLabel):
    """Frame de la camara sobre el que se dibujan las zonas."""

    zona_terminada = Signal(str, object)   # modo, geometria en px del frame

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pix = pixmap
        self.setMinimumSize(640, 400)
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self.modo = MODO_PASILLO
        self.layout_data = {"pasillos": [], "anaqueles": [], "mobiliario": []}
        self._puntos: list = []          # poligono en curso (coords del widget)
        self._rect_ini = None
        self._rect_fin = None
        self._arrastrando = False

    # ── Mapeo widget <-> frame ───────────────────────────────────────

    def _destino(self) -> QRect:
        """Rect donde se pinta la imagen (respetando el aspecto)."""
        if self._pix.isNull():
            return QRect(0, 0, self.width(), self.height())
        escalada = self._pix.size().scaled(self.size(), Qt.KeepAspectRatio)
        x = (self.width() - escalada.width()) // 2
        y = (self.height() - escalada.height()) // 2
        return QRect(x, y, escalada.width(), escalada.height())

    def _a_norm(self, p: QPoint):
        """Punto del widget -> coords normalizadas 0..1 del frame."""
        d = self._destino()
        if d.width() <= 0 or d.height() <= 0:
            return 0.0, 0.0
        nx = (p.x() - d.x()) / d.width()
        ny = (p.y() - d.y()) / d.height()
        return round(min(1.0, max(0.0, nx)), 4), round(min(1.0, max(0.0, ny)), 4)

    def _a_widget(self, nx: float, ny: float) -> QPoint:
        d = self._destino()
        return QPoint(int(d.x() + nx * d.width()),
                      int(d.y() + ny * d.height()))

    # ── Raton ────────────────────────────────────────────────────────

    def _es_poligono(self) -> bool:
        """Pasillos y ANAQUELES se dibujan como poligono (para seguir un
        estante inclinado del gran-angular); solo la maquina de precios
        sigue siendo un rectangulo."""
        return self.modo in (MODO_PASILLO, MODO_ANAQUEL)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.RightButton:
            if self._es_poligono() and self._puntos:
                self._puntos.pop()
                self.update()
            return
        if ev.button() != Qt.LeftButton:
            return
        if self._es_poligono():
            self._puntos.append(ev.position().toPoint())
        else:
            self._arrastrando = True
            self._rect_ini = ev.position().toPoint()
            self._rect_fin = self._rect_ini
        self.update()

    def mouseMoveEvent(self, ev):
        if self._arrastrando:
            self._rect_fin = ev.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, ev):
        if not self._arrastrando or ev.button() != Qt.LeftButton:
            return
        self._arrastrando = False
        self._rect_fin = ev.position().toPoint()
        x1, y1 = self._a_norm(self._rect_ini)
        x2, y2 = self._a_norm(self._rect_fin)
        self._rect_ini = self._rect_fin = None
        rect = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
        if (rect[2] - rect[0]) < 0.01 or (rect[3] - rect[1]) < 0.01:
            self.update()
            return
        self.zona_terminada.emit(self.modo, rect)
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if self._es_poligono():
            self.cerrar_poligono()

    def cerrar_poligono(self):
        if not self._es_poligono() or len(self._puntos) < 3:
            return False
        poly = [list(self._a_norm(p)) for p in self._puntos]
        modo = self.modo
        self._puntos = []
        self.zona_terminada.emit(modo, poly)
        self.update()
        return True

    def cancelar_en_curso(self):
        self._puntos = []
        self._rect_ini = self._rect_fin = None
        self._arrastrando = False
        self.update()

    # ── Pintado ──────────────────────────────────────────────────────

    def paintEvent(self, ev):
        p = QPainter(self)
        d = self._destino()
        if not self._pix.isNull():
            p.drawPixmap(d, self._pix)

        fuente = QFont()
        fuente.setPointSize(8)
        fuente.setBold(True)
        p.setFont(fuente)

        for a in self.layout_data["pasillos"]:
            self._poly(p, a["poligono"], _COLOR[MODO_PASILLO], a["nombre"])
        for s in self.layout_data["anaqueles"]:
            # Multi-poligono (nuevo) o poligono unico o rect (antiguos).
            if s.get("poligonos"):
                for i, poly in enumerate(s["poligonos"]):
                    self._poly(p, poly, _COLOR[MODO_ANAQUEL],
                               s["nombre"] if i == 0 else "")
            elif s.get("poligono"):
                self._poly(p, s["poligono"], _COLOR[MODO_ANAQUEL], s["nombre"])
            elif s.get("rect"):
                self._rect(p, s["rect"], _COLOR[MODO_ANAQUEL], s["nombre"])
        for m in self.layout_data["mobiliario"]:
            self._rect(p, m["rect"], _COLOR[MODO_MAQUINA], m["nombre"])

        # Zona en curso
        p.setPen(QPen(QColor(255, 255, 255), 2, Qt.DashLine))
        if self._puntos:
            for i, pt in enumerate(self._puntos):
                p.drawEllipse(pt, 4, 4)
                if i:
                    p.drawLine(self._puntos[i - 1], pt)
        if self._arrastrando and self._rect_ini and self._rect_fin:
            p.drawRect(QRect(self._rect_ini, self._rect_fin))
        p.end()

    def _poly(self, p: QPainter, poly, color: QColor, nombre: str):
        if len(poly) < 2:
            return
        p.setPen(QPen(color, 2))
        pts = [self._a_widget(x, y) for x, y in poly]
        for i in range(len(pts)):
            p.drawLine(pts[i], pts[(i + 1) % len(pts)])
        p.drawText(pts[0] + QPoint(4, -4), nombre)

    def _rect(self, p: QPainter, rect, color: QColor, nombre: str):
        p.setPen(QPen(color, 2))
        a = self._a_widget(rect[0], rect[1])
        b = self._a_widget(rect[2], rect[3])
        p.drawRect(QRect(a, b))
        p.drawText(a + QPoint(3, -4), nombre)


class PlanogramEditor(QDialog):
    """Dialogo completo: lienzo + lista de zonas + guardar al servidor.

    Es NO MODAL a proposito: mientras esta abierto el video sigue corriendo
    detras con las zonas superpuestas (``zonas_cambiadas`` las va enviando al
    recuadro de la camara), asi el operador comprueba contra la escena real
    que cada rectangulo cae donde debe.
    """

    #: Emitida en cada alta/baja de zona -> el recuadro pinta el overlay.
    zonas_cambiadas = Signal(dict)

    def __init__(self, frame_jpeg: bytes, camera_id: str, camera_name: str,
                 layout_inicial: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Definir zonas de la tienda — {camera_name}")
        self.setStyleSheet(_QSS)
        self.resize(1180, 720)
        self.camera_id = camera_id
        self.resultado = None       # layout guardado (dict) si se acepto

        img = QImage.fromData(frame_jpeg)
        pix = QPixmap.fromImage(img)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # ── Lienzo ──
        izq = QVBoxLayout()
        self.lienzo = _Lienzo(pix)
        if layout_inicial:
            self.lienzo.layout_data = {
                "pasillos": list(layout_inicial.get("pasillos") or []),
                "anaqueles": list(layout_inicial.get("anaqueles") or []),
                "mobiliario": list(layout_inicial.get("mobiliario") or []),
            }
        self.lienzo.zona_terminada.connect(self._on_zona)
        izq.addWidget(self.lienzo, 1)

        self.lbl_ayuda = QLabel()
        self.lbl_ayuda.setWordWrap(True)
        izq.addWidget(self.lbl_ayuda)
        root.addLayout(izq, 3)

        # ── Panel derecho ──
        der = QVBoxLayout()
        der.addWidget(QLabel("<b>Que voy a dibujar</b>"))
        self.btns = {}
        for modo, texto in ((MODO_PASILLO, "Pasillo (poligono)"),
                            (MODO_ANAQUEL, "Anaquel / producto"),
                            (MODO_MAQUINA, "Maquina de precios")):
            b = QPushButton(texto)
            b.setCheckable(True)
            b.clicked.connect(lambda _c, m=modo: self._set_modo(m))
            der.addWidget(b)
            self.btns[modo] = b

        b_cerrar_poly = QPushButton("✓ Cerrar y nombrar")
        b_cerrar_poly.setStyleSheet(
            "QPushButton{background:#2e7d32;font-weight:bold;}"
            "QPushButton:hover{background:#3d9440;}")
        b_cerrar_poly.clicked.connect(self._cerrar_seccion)
        der.addWidget(b_cerrar_poly)
        b_cancelar = QPushButton("Cancelar zona en curso")
        b_cancelar.clicked.connect(self.lienzo.cancelar_en_curso)
        der.addWidget(b_cancelar)

        der.addWidget(QLabel("<b>Zonas definidas</b>"))
        self.lista = QListWidget()
        der.addWidget(self.lista, 1)
        b_borrar = QPushButton("Eliminar seleccionada")
        b_borrar.clicked.connect(self._borrar)
        der.addWidget(b_borrar)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Guardar en el servidor")
        bb.accepted.connect(self._guardar)
        bb.rejected.connect(self.reject)
        der.addWidget(bb)
        root.addLayout(der, 1)

        self._set_modo(MODO_PASILLO)
        self._refrescar_lista()

    # ── Modos ────────────────────────────────────────────────────────

    def _set_modo(self, modo: str):
        self.lienzo.modo = modo
        self.lienzo.cancelar_en_curso()
        for m, b in self.btns.items():
            b.setChecked(m == modo)
        ayuda = {
            MODO_PASILLO: "PASILLO: click en cada esquina del area del piso "
                          "por donde camina la gente. Doble click (o 'Cerrar "
                          "poligono') para terminarlo. Click derecho deshace "
                          "el ultimo punto.",
            MODO_ANAQUEL: "ANAQUEL: click en cada esquina del estante; luego "
                          "DOBLE CLICK o el boton verde '✓ Cerrar y nombrar' "
                          "para ponerle nombre. Para un anaquel con VARIAS "
                          "secciones, dibuja cada seccion y al nombrarla elige "
                          "'añadir al anaquel existente'. Click derecho "
                          "deshace el ultimo punto.",
            MODO_MAQUINA: "MAQUINA DE PRECIOS: arrastra un rectangulo sobre "
                          "el aparato (o sobre el piso delante de el). Sirve "
                          "para medir quien consulta precios antes de "
                          "decidir.",
        }[modo]
        self.lbl_ayuda.setText(f"<i>{ayuda}</i>")

    # ── Alta de zonas ────────────────────────────────────────────────

    def _nombres_pasillos(self):
        return [a["nombre"] for a in self.lienzo.layout_data["pasillos"]]

    def _on_zona(self, modo: str, geom):
        if modo == MODO_PASILLO:
            nombre, cat = self._pedir_texto(
                "Nuevo pasillo", "Nombre del pasillo (ej: Pasillo 3 - "
                                 "Limpieza):", "Categoria (opcional):")
            if not nombre:
                return
            self._quitar_por_nombre(nombre)
            self.lienzo.layout_data["pasillos"].append(
                {"nombre": nombre, "categoria": cat, "poligono": geom})
        elif modo == MODO_ANAQUEL:
            existentes = [s["nombre"]
                          for s in self.lienzo.layout_data["anaqueles"]]
            dlg = _ShelfDialog(self._nombres_pasillos(), existentes, self)
            if dlg.exec() != QDialog.Accepted:
                return
            d = dlg.datos()
            if not d.get("nombre"):
                return
            if d["modo"] == "existente":
                # Otra seccion del mismo anaquel: se añade a sus poligonos.
                for s in self.lienzo.layout_data["anaqueles"]:
                    if s["nombre"] == d["nombre"]:
                        s.setdefault("poligonos", []).append(geom)
                        break
            else:
                self._quitar_por_nombre(d["nombre"])
                d["poligonos"] = [geom]
                self.lienzo.layout_data["anaqueles"].append(d)
        else:
            nombre, _ = self._pedir_texto(
                "Maquina de precios", "Nombre de la maquina:", None)
            if not nombre:
                return
            self._quitar_por_nombre(nombre)
            self.lienzo.layout_data["mobiliario"].append(
                {"nombre": nombre, "tipo": "consulta_precio", "rect": geom})
        self._refrescar_lista()
        self.lienzo.update()

    def _cerrar_seccion(self):
        """Cierra el poligono en curso y abre el dialogo de nombre. Si aun no
        hay suficientes puntos, avisa (en vez de fallar en silencio)."""
        if len(self.lienzo._puntos) < 3:
            QMessageBox.information(
                self, "Cerrar zona",
                "Marca al menos 3 puntos (haz click en las esquinas de la "
                "zona) antes de cerrarla.")
            return
        self.lienzo.cerrar_poligono()

    def _pedir_texto(self, titulo, label1, label2):
        from PySide6.QtWidgets import QInputDialog
        t1, ok = QInputDialog.getText(self, titulo, label1)
        if not ok or not t1.strip():
            return None, ""
        t2 = ""
        if label2:
            t2, _ = QInputDialog.getText(self, titulo, label2)
        return t1.strip(), (t2 or "").strip()

    def _quitar_por_nombre(self, nombre: str):
        for k in ("pasillos", "anaqueles", "mobiliario"):
            self.lienzo.layout_data[k] = [
                z for z in self.lienzo.layout_data[k] if z["nombre"] != nombre]

    def layout_actual(self) -> dict:
        """Copia del planograma en edicion (coords normalizadas 0..1)."""
        return json.loads(json.dumps(self.lienzo.layout_data))

    def _refrescar_lista(self):
        # Avisar al recuadro de la camara para que actualice el overlay del
        # video en vivo con lo que hay dibujado ahora mismo.
        self.zonas_cambiadas.emit(self.layout_actual())
        self.lista.clear()
        for k, etiqueta, color in (
                ("pasillos", "Pasillo", _COLOR[MODO_PASILLO]),
                ("anaqueles", "Anaquel", _COLOR[MODO_ANAQUEL]),
                ("mobiliario", "Maquina", _COLOR[MODO_MAQUINA])):
            for z in self.lienzo.layout_data[k]:
                extra = ""
                if k == "anaqueles":
                    extra = f"  [{z.get('pasillo') or 'sin pasillo'}]"
                    n_sec = len(z.get("poligonos") or []) or 1
                    if n_sec > 1:
                        extra += f"  ({n_sec} secciones)"
                    if z.get("precio"):
                        extra += f"  {z['precio']:.2f}"
                it = QListWidgetItem(f"{etiqueta}: {z['nombre']}{extra}")
                it.setForeground(color)
                it.setData(Qt.UserRole, (k, z["nombre"]))
                self.lista.addItem(it)

    def _borrar(self):
        it = self.lista.currentItem()
        if it is None:
            return
        k, nombre = it.data(Qt.UserRole)
        self.lienzo.layout_data[k] = [
            z for z in self.lienzo.layout_data[k] if z["nombre"] != nombre]
        self._refrescar_lista()
        self.lienzo.update()

    # ── Guardar ──────────────────────────────────────────────────────

    def _guardar(self):
        data = self.lienzo.layout_data
        if not any(data.values()):
            QMessageBox.warning(self, "Planograma",
                                "No has definido ninguna zona todavia.")
            return
        huerfanos = [s["nombre"] for s in data["anaqueles"]
                     if not s.get("pasillo")]
        if huerfanos and data["pasillos"]:
            if QMessageBox.question(
                    self, "Planograma",
                    "Estos anaqueles no estan asignados a ningun pasillo:\n\n"
                    + "\n".join(f"  • {n}" for n in huerfanos[:8])
                    + "\n\nFuncionaran igual, pero no podras cruzar sus "
                      "ventas con el trafico del pasillo.\n\n¿Guardar asi?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        self.resultado = self.layout_actual()
        self.accept()
