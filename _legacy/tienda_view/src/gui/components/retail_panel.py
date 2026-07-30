"""
retail_panel.py - Panel de analitica de supermercado.

Muestra, para una camara, lo que el servidor calcula en
``metadata['retail']`` y ``metadata['retail_report']``:

  * Resumen    - aforo por pasillo, mayor/menor concentracion, conversion.
  * Pasillos   - afluencia, permanencia y demografia de cada pasillo.
  * Productos  - agarrados / devueltos / llevados, conversion e ingreso.
  * Decisiones - duelos entre productos ("A gano a B, N veces").
  * Reposicion - anaqueles vacios o bajos que hay que reponer YA.
  * Clientes   - segmentacion Nino/Adolescente/Hombre/Mujer/Anciano.

Se refresca solo con cada frame que llega del servidor: el widget es
pasivo, ``actualizar(stats, report)`` le pasa los datos ya calculados. No
hace peticiones por su cuenta salvo el boton de exportar.
"""
from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
    QWidget,
)

_QSS = """
QDialog { background: #1e1e1e; }
QLabel { color: #ddd; }
QTabWidget::pane { border: 1px solid #444; background: #252525; }
QTabBar::tab { background: #333; color: #ccc; padding: 6px 14px;
               border: 1px solid #444; border-bottom: none; }
QTabBar::tab:selected { background: #3d6fb0; color: #fff; font-weight: bold; }
QTableWidget { background: #252525; color: #ddd; gridline-color: #3a3a3a;
               selection-background-color: #3d6fb0; border: none; }
QHeaderView::section { background: #333; color: #fff; padding: 5px;
                       border: 1px solid #444; font-weight: bold; }
QPushButton { background: #333; color: #fff; border: 1px solid #555;
              border-radius: 4px; padding: 6px 14px; }
QPushButton:hover { background: #3d6fb0; }
"""

# Colores de estado de anaquel (mismo criterio que el overlay del servidor)
_COLOR_ESTADO = {
    "VACIO": QColor(200, 60, 60),
    "BAJO": QColor(200, 140, 40),
    "OK": QColor(60, 160, 80),
}


def _fmt_secs(s) -> str:
    """Segundos -> 'Xm YYs' (o 'YYs' si es menos de un minuto)."""
    try:
        s = int(round(float(s)))
    except (TypeError, ValueError):
        return "—"
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def _tabla(headers) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setAlternatingRowColors(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t


def _llenar(tabla: QTableWidget, filas) -> None:
    """Vuelca una lista de listas en la tabla. Un elemento puede ser
    ``(texto, QColor)`` para colorear esa celda."""
    tabla.setRowCount(len(filas))
    for r, fila in enumerate(filas):
        for c, val in enumerate(fila):
            color = None
            if isinstance(val, tuple):
                val, color = val
            item = QTableWidgetItem(str(val))
            if color is not None:
                item.setForeground(color)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            if c > 0:
                item.setTextAlignment(Qt.AlignCenter)
            tabla.setItem(r, c, item)


class RetailPanel(QDialog):
    """Ventana de analitica de tienda de UNA camara."""

    def __init__(self, camera_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Analitica de tienda — {camera_name}")
        self.setStyleSheet(_QSS)
        self.resize(980, 620)
        self._report = {}
        self._camera_name = camera_name

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.lbl_estado = QLabel("Esperando datos del servidor…")
        f = QFont()
        f.setPointSize(10)
        f.setBold(True)
        self.lbl_estado.setFont(f)
        root.addWidget(self.lbl_estado)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # Resumen
        self.w_resumen = QWidget()
        self.lay_resumen = QVBoxLayout(self.w_resumen)
        self.lay_resumen.setAlignment(Qt.AlignTop)
        self.lbl_resumen = QLabel("—")
        self.lbl_resumen.setWordWrap(True)
        self.lbl_resumen.setTextFormat(Qt.RichText)
        self.lay_resumen.addWidget(self.lbl_resumen)
        self.tabs.addTab(self.w_resumen, "Resumen")

        self.t_pasillos = _tabla([
            "Pasillo", "Personas ahora", "Visitantes unicos", "Visitas",
            "Pico", "Densidad", "Permanencia media"])
        self.tabs.addTab(self.t_pasillos, "Pasillos")

        self.t_productos = _tabla([
            "Producto", "Pasillo", "Precio", "Toques", "Agarrados",
            "Devueltos", "Se lo llevan", "Conversion", "Ingreso est."])
        self.tabs.addTab(self.t_productos, "Productos")

        self.t_duelos = _tabla(["Elegido", "Descartado", "Veces"])
        self.tabs.addTab(self.t_duelos, "Decisiones")

        self.t_stock = _tabla(["Anaquel", "Pasillo", "Nivel", "Estado"])
        self.tabs.addTab(self.t_stock, "Stock")

        self.t_clientes = _tabla([
            "Segmento", "Personas", "Agarrados", "Se llevan", "Conversion",
            "Ingreso est.", "Top productos"])
        self.tabs.addTab(self.t_clientes, "Clientes")

        # Cajas en el piso + reposicion de empleados
        self.w_cajas = QWidget()
        lay_cajas = QVBoxLayout(self.w_cajas)
        lay_cajas.setContentsMargins(0, 0, 0, 0)
        lay_cajas.addWidget(QLabel("<b>Cajas en el piso ahora</b>"))
        self.t_cajas = _tabla(["Caja", "Pasillo", "Tiempo presente",
                               "Estado"])
        lay_cajas.addWidget(self.t_cajas)
        lay_cajas.addWidget(QLabel("<b>Reposiciones de empleados</b>"))
        self.t_reposicion = _tabla(["Empleado", "Pasillo", "Anaqueles",
                                    "Estado", "Duracion"])
        lay_cajas.addWidget(self.t_reposicion)
        self.tabs.addTab(self.w_cajas, "Cajas / Reposicion")

        # Botonera
        botones = QHBoxLayout()
        botones.addStretch(1)
        b_exp = QPushButton("Exportar JSON…")
        b_exp.clicked.connect(self._exportar)
        botones.addWidget(b_exp)
        b_cerrar = QPushButton("Cerrar")
        b_cerrar.clicked.connect(self.close)
        botones.addWidget(b_cerrar)
        root.addLayout(botones)

    # ── Actualizacion ────────────────────────────────────────────────

    def actualizar(self, stats: dict, report: dict = None) -> None:
        """Refresca el panel con la metadata del ultimo frame."""
        stats = stats or {}
        if not stats.get("activo"):
            motivo = stats.get("motivo", "analitica de tienda inactiva")
            ruta = stats.get("planograma", "")
            self.lbl_estado.setText(f"⚠ Inactiva: {motivo}")
            # Las CAJAS en el piso corren aunque no haya planograma: mostrar
            # su estado y su tabla para que no parezca que "no hace nada".
            cajas_st = stats.get("cajas") or {}
            extra_cajas = ""
            if cajas_st.get("activo"):
                extra_cajas = (
                    "<p style='color:#7bc47b'><b>La deteccion de CAJAS en el "
                    "piso sigue activa</b> (no necesita planograma): "
                    f"{cajas_st.get('cajas_en_piso_ahora', 0)} en el piso "
                    f"ahora, {cajas_st.get('total_cajas_detectadas', 0)} "
                    "detectadas en total.</p>")
                self._pintar_cajas(stats)
            self.lbl_resumen.setText(extra_cajas +
                "<p>La analitica de tienda necesita un <b>planograma</b> que "
                "diga que es cada zona del encuadre (pasillos, anaqueles y "
                "la maquina de precios).</p>"
                "<p><b>1.</b> Enciende el analisis IA de esta camara.</p>"
                "<p><b>2.</b> Menu <b>Tienda ▾ → Definir zonas de la "
                "tienda…</b> y dibuja sobre la imagen: pasillos (poligonos), "
                "anaqueles (un rectangulo <i>por producto</i>) y la maquina "
                "de precios.</p>"
                "<p><b>3.</b> Con los estantes repuestos y sin gente delante, "
                "<b>Tienda ▾ → Calibrar anaqueles</b> para fijar el 100% de "
                "llenado.</p>"
                f"<p style='color:#888'>El planograma se guardara en el "
                f"servidor como:<br><code>{ruta}</code></p>")
            return

        if report:
            self._report = report

        self.lbl_estado.setText(
            f"✔ Activa — actualizado {time.strftime('%H:%M:%S')}")
        self._pintar_resumen(stats)
        self._pintar_pasillos(stats)
        self._pintar_stock(stats)
        self._pintar_cajas(stats)
        if report:
            ventas = report.get("ventas") or {}
            self._pintar_productos(ventas.get("productos") or [])
            self._pintar_duelos(ventas.get("duelos") or [])
            self._pintar_clientes(
                (ventas.get("demografia") or {}).get("segmentos") or [],
                (ventas.get("consulta_precios") or {}))

    def _pintar_resumen(self, stats: dict) -> None:
        pas = (stats.get("pasillos") or {}).get("resumen") or {}
        compras = stats.get("compras") or {}
        anaq = stats.get("anaqueles") or {}
        inter = stats.get("interacciones") or {}
        carts = stats.get("carritos") or {}
        vacios = anaq.get("anaqueles_vacios") or []

        def li(k, v):
            return f"<li><b>{k}:</b> {v}</li>"

        html = ["<h3 style='color:#6ba3e8'>Trafico</h3><ul>"]
        html.append(li("Personas en pasillos ahora",
                       pas.get("personas_en_pasillos", 0)))
        html.append(li("Pasillo mas transitado",
                       pas.get("pasillo_mas_transitado") or "—"))
        html.append(li("Pasillo menos transitado",
                       pas.get("pasillo_menos_transitado") or "—"))
        html.append(li("Mayor concentracion ahora",
                       pas.get("mayor_concentracion") or "— (nadie dentro)"))
        html.append(li("Menor concentracion ahora",
                       pas.get("menor_concentracion") or "—"))
        sin = pas.get("pasillos_sin_visitas") or []
        if sin:
            html.append(li("Pasillos SIN visitas", ", ".join(sin)))
        html.append("</ul>")

        html.append("<h3 style='color:#6ba3e8'>Comportamiento de compra</h3><ul>")
        html.append(li("Productos agarrados",
                       compras.get("productos_tomados", 0)))
        html.append(li("Devueltos al estante",
                       compras.get("productos_devueltos", 0)))
        html.append(li("Se los llevaron",
                       compras.get("productos_llevados", 0)))
        conv = compras.get("conversion_global", 0.0)
        html.append(li("Conversion global", f"{conv * 100:.0f}%"))
        html.append(li("Producto mas agarrado",
                       compras.get("producto_mas_tomado") or "—"))
        html.append(li("Comparaciones entre productos",
                       compras.get("comparaciones", 0)))
        html.append(li("Consultas de precio",
                       compras.get("consultas_precio", 0)))
        html.append(li("Solo tocaron (sin agarrar)",
                       inter.get("total_toques", 0)))
        ev_stats = stats.get("evaluacion") or {}
        html.append(li("Agarres de cliente (mano en anaquel)",
                       ev_stats.get("agarres_cliente", 0)))
        html.append(li("Clientes evaluando ahora",
                       ev_stats.get("evaluando_ahora", 0)))
        html.append("</ul>")

        html.append("<h3 style='color:#6ba3e8'>Operacion</h3><ul>")
        if vacios:
            html.append("<li style='color:#e06666'><b>REPONER YA:</b> "
                        + ", ".join(vacios) + "</li>")
        else:
            html.append("<li style='color:#7bc47b'>Ningun anaquel vacio</li>")
        bajos = anaq.get("anaqueles_bajos") or []
        if bajos:
            html.append("<li style='color:#e0a44a'><b>Nivel bajo:</b> "
                        + ", ".join(bajos) + "</li>")
        html.append(li("Anaqueles calibrados",
                       f"{anaq.get('con_referencia', 0)} de "
                       f"{anaq.get('total_anaqueles', 0)}"))
        if carts.get("activo"):
            html.append(li("Carritos detectados",
                           carts.get("carritos_detectados", 0)))
        else:
            html.append("<li style='color:#999'>Deteccion de carritos "
                        "inactiva (se asume que se llevan lo no devuelto)</li>")
        if not inter.get("usa_pose"):
            html.append("<li style='color:#999'>Alcance de mano estimado por "
                        "bbox (activa la pose en el servidor para mas "
                        "precision)</li>")
        html.append("</ul>")

        # Cajas + reposicion
        cajas = stats.get("cajas") or {}
        rep = stats.get("reposicion") or {}
        html.append("<h3 style='color:#6ba3e8'>Cajas y reposicion</h3><ul>")
        diag = cajas.get("diagnostico") or {}
        if not cajas.get("activo", True):
            html.append("<li style='color:#e06666'><b>Deteccion de cajas "
                        "INACTIVA</b>: YOLO-World no cargo en el servidor "
                        "(revisar log del servidor).</li>")
        elif diag.get("router_fallando"):
            html.append("<li style='color:#e06666'><b>Deteccion de cajas "
                        "FALLANDO</b> en el servidor (ver su log).</li>")
        elif (diag.get("detecciones_crudas", 0) == 0
              and cajas.get("total_cajas_detectadas", 0) == 0):
            html.append("<li style='color:#999'>YOLO-World aun no ve "
                        "candidatos a caja. Revisar la imagen de "
                        "diagnostico en el servidor: "
                        "<code>output/debug_cajas/&lt;camara&gt;.jpg</code>"
                        "</li>")
        else:
            rech = diag.get("rechazos") or {}
            desc = ", ".join(f"{k}: {v}" for k, v in rech.items() if v)
            if desc and cajas.get("total_cajas_detectadas", 0) == 0:
                mejor = diag.get("mejor_conf_vista", 0)
                umbral = diag.get("umbral_conf", 0)
                html.append(
                    f"<li style='color:#e0a44a'>Candidatos vistos pero "
                    f"filtrados ({desc}). Mejor confianza vista: "
                    f"<b>{mejor:.3f}</b> (umbral {umbral:.2f}"
                    + (" — BAJAR BOX_DETECT_CONF" if 0 < mejor < umbral
                       else "")
                    + "). Ver <code>output/debug_cajas/&lt;camara&gt;.jpg"
                    "</code> en el servidor.</li>")
        n_cajas = cajas.get("cajas_en_piso_ahora", 0)
        n_obs = cajas.get("obstrucciones_activas", 0)
        if n_obs:
            html.append(f"<li style='color:#e06666'><b>{n_obs} caja(s) "
                        f"obstruyendo el pasillo</b> (mas de "
                        f"{int(cajas.get('umbral_obstruccion_s', 0)) // 60} "
                        f"min en el piso)</li>")
        html.append(li("Cajas en el piso ahora", n_cajas))
        html.append(li("Cajas detectadas (total)",
                       cajas.get("total_cajas_detectadas", 0)))
        if cajas.get("cajas_retiradas"):
            html.append(li("Permanencia media de una caja",
                           _fmt_secs(cajas.get("duracion_media_s", 0))))
        n_rep = rep.get("reposiciones_activas", 0)
        if n_rep:
            html.append(f"<li style='color:#7bc47b'><b>{n_rep} "
                        f"reposicion(es) en curso</b></li>")
        html.append(li("Reposiciones registradas",
                       rep.get("reposiciones_totales", 0)))
        html.append("</ul>")

        # Personal registrado por foto
        per = stats.get("personal") or {}
        html.append("<h3 style='color:#6ba3e8'>Personal</h3><ul>")
        if per.get("fotos_cargadas"):
            html.append(li("Personal registrado (por foto)",
                           ", ".join(per.get("personal_registrado") or [])
                           or "—"))
            html.append(li("Verificados en escena ahora",
                           per.get("verificados_en_escena", 0)))
            html.append("<li style='color:#999'>El personal verificado queda "
                        "EXCLUIDO de las metricas de clientes.</li>")
        else:
            html.append("<li style='color:#999'>Sin fotos de personal. Sube "
                        "fotos a <code>config/personal/&lt;nombre&gt;.jpg</code> "
                        "en el servidor: nadie se etiqueta como empleado ni "
                        "se excluye de metricas sin su foto.</li>")
        html.append("</ul>")
        self.lbl_resumen.setText("".join(html))

    def _pintar_pasillos(self, stats: dict) -> None:
        pasillos = (stats.get("pasillos") or {}).get("pasillos") or []
        pasillos = sorted(pasillos,
                          key=lambda p: p.get("visitantes_unicos", 0),
                          reverse=True)
        _llenar(self.t_pasillos, [[
            p.get("pasillo", "?"),
            p.get("ocupacion_actual", 0),
            p.get("visitantes_unicos", 0),
            p.get("visitas", 0),
            p.get("ocupacion_pico", 0),
            f"{p.get('densidad_actual', 0):.2f}",
            f"{p.get('permanencia_media_s', 0):.0f} s",
        ] for p in pasillos])

    def _pintar_stock(self, stats: dict) -> None:
        anaqueles = (stats.get("anaqueles") or {}).get("anaqueles") or []
        orden = {"VACIO": 0, "BAJO": 1, "OK": 2}
        anaqueles = sorted(anaqueles,
                           key=lambda a: orden.get(a.get("estado"), 3))
        _llenar(self.t_stock, [[
            a.get("nombre", "?"),
            a.get("pasillo", ""),
            f"{a.get('fill_ratio', 0) * 100:.0f}%",
            (a.get("estado", "?"),
             _COLOR_ESTADO.get(a.get("estado"), QColor(200, 200, 200))),
        ] for a in anaqueles])

    def _pintar_cajas(self, stats: dict) -> None:
        cajas = (stats.get("cajas") or {}).get("cajas_activas") or []
        _llenar(self.t_cajas, [[
            f"#{c.get('caja_id', '?')}",
            c.get("pasillo") or "—",
            _fmt_secs(c.get("segundos_presente", 0)),
            (("OBSTRUCCION" if c.get("obstruccion") else "en piso"),
             QColor(200, 60, 60) if c.get("obstruccion")
             else QColor(200, 170, 90)),
        ] for c in cajas])

        rep = stats.get("reposicion") or {}
        activas = rep.get("reposiciones_en_curso") or []
        _llenar(self.t_reposicion, [[
            # Solo dice "empleado" con foto de personal verificada.
            ((r.get("empleado_nombre") or "empleado ✓")
             if r.get("empleado_verificado") else "persona (sin foto)"),
            r.get("pasillo") or "—",
            ", ".join(r.get("anaqueles") or []) or "—",
            ("REPONIENDO", QColor(90, 180, 220)),
            _fmt_secs(r.get("segundos", 0)),
        ] for r in activas])

    def _pintar_productos(self, productos) -> None:
        _llenar(self.t_productos, [[
            p.get("producto", "?"),
            p.get("pasillo", ""),
            f"{p.get('precio', 0):.2f}",
            p.get("toques", 0),
            p.get("tomas", 0),
            p.get("devoluciones", 0),
            p.get("llevados", 0),
            (f"{p.get('tasa_conversion', 0) * 100:.0f}%",
             QColor(120, 200, 120) if p.get("tasa_conversion", 0) >= 0.5
             else QColor(220, 140, 60)),
            f"{p.get('ingreso_estimado', 0):.2f}",
        ] for p in productos])

    def _pintar_duelos(self, duelos) -> None:
        _llenar(self.t_duelos, [[
            (d.get("elegido", "?"), QColor(120, 200, 120)),
            (d.get("descartado", "?"), QColor(220, 120, 120)),
            d.get("veces", 0),
        ] for d in duelos])

    def _pintar_clientes(self, segmentos, precios: dict) -> None:
        _llenar(self.t_clientes, [[
            s.get("segmento", "?"),
            s.get("personas", 0),
            s.get("productos_tomados", 0),
            s.get("productos_llevados", 0),
            f"{s.get('conversion', 0) * 100:.0f}%",
            f"{s.get('ingreso_estimado', 0):.2f}",
            ", ".join(f"{t['producto']} ({t['veces']})"
                      for t in (s.get("top_productos") or [])[:3]) or "—",
        ] for s in segmentos])
        # La sensibilidad al precio se aprecia mejor junto a la demografia.
        if precios:
            self.tabs.setTabText(
                self.tabs.indexOf(self.t_clientes),
                f"Clientes ({precios.get('consultas_totales', 0)} consultas "
                f"de precio)")

    # ── Exportar ─────────────────────────────────────────────────────

    def _exportar(self) -> None:
        if not self._report:
            QMessageBox.information(self, "Exportar",
                                    "Aun no hay reporte que exportar.")
            return
        nombre = (self._camera_name or "camara").replace(" ", "_")
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Guardar reporte de tienda",
            f"retail_{nombre}_{time.strftime('%Y%m%d_%H%M')}.json",
            "JSON (*.json)")
        if not ruta:
            return
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(self._report, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Exportar",
                                    f"Reporte guardado en:\n{ruta}")
        except OSError as e:
            QMessageBox.warning(self, "Exportar", f"No se pudo guardar: {e}")
