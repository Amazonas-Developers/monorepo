"""
src/gui/components/discovery_dialog.py
Diálogo "Buscar dispositivos en la red".

Lanza el descubrimiento (SADP + ONVIF + barrido TCP) en un hilo aparte para no
congelar la GUI, muestra los equipos encontrados en una tabla y, al elegir uno,
emite sus datos para que el formulario DVR se rellene solo.

Uso:
    dlg = DiscoveryDialog(parent)
    if dlg.exec() and dlg.seleccionado:
        d = dlg.seleccionado      # DispositivoRed
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..dvr import discovery as D


class _DiscoveryWorker(QThread):
    """Corre el descubrimiento fuera del hilo GUI."""
    sig_estado = Signal(str, int, int)          # texto, hechos, total
    sig_listo = Signal(list)                    # list[DispositivoRed]

    def __init__(self, incluir_barrido: bool, parent=None):
        super().__init__(parent)
        self._incluir_barrido = incluir_barrido
        self._cancelado = False

    def cancelar(self) -> None:
        self._cancelado = True

    def run(self) -> None:
        try:
            encontrados = D.descubrir(
                incluir_barrido=self._incluir_barrido,
                progreso=lambda t, h=0, tot=0: self.sig_estado.emit(t, h, tot),
                cancelado=lambda: self._cancelado,
            )
        except Exception as e:  # noqa: BLE001
            self.sig_estado.emit(f"Error en la búsqueda: {e}", 0, 0)
            encontrados = []
        self.sig_listo.emit(encontrados)


class DiscoveryDialog(QDialog):
    """Ventana de búsqueda de DVR/NVR/cámaras IP en la red local."""

    _COLUMNAS = ["IP", "Marca", "Modelo", "MAC", "Puertos", "Detectado por"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Buscar dispositivos en la red")
        self.resize(820, 460)
        self.seleccionado: D.DispositivoRed | None = None
        self._encontrados: list[D.DispositivoRed] = []
        self._worker: _DiscoveryWorker | None = None
        self._construir_ui()
        # Arrancar la búsqueda al abrir: es lo que el usuario espera.
        self._buscar()

    # ------------------------------------------------------------------ UI
    def _construir_ui(self) -> None:
        self.setStyleSheet("""
            QDialog{background:#2b2b2b;color:#eee}
            QLabel{color:#eee}
            QTableWidget{background:#1e1e1e;color:#eee;gridline-color:#3a3a3a;
                         selection-background-color:#00A8E8;border:1px solid #444}
            QHeaderView::section{background:#383838;color:#eee;border:0;
                                 padding:6px;font-weight:bold}
            QPushButton{background:#3d6fb0;color:#fff;font-weight:bold;
                        border:none;border-radius:4px;padding:7px 16px}
            QPushButton:hover{background:#4d84cc}
            QPushButton:disabled{background:#555;color:#999}
            QPushButton#sec{background:#4a4a4a}
            QCheckBox{color:#eee}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        titulo = QLabel("Dispositivos encontrados en la red local")
        titulo.setStyleSheet("font-size:15px;font-weight:bold")
        root.addWidget(titulo)

        ayuda = QLabel(
            "Se buscan DVR, NVR y cámaras IP por SADP (Hikvision), ONVIF "
            "(cualquier marca) y escaneo de puertos. Elige uno para rellenar "
            "el formulario automáticamente.")
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet("color:#aaa;font-size:12px")
        root.addWidget(ayuda)

        self.tabla = QTableWidget(0, len(self._COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(self._COLUMNAS)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.verticalHeader().setVisible(False)
        cab = self.tabla.horizontalHeader()
        cab.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        cab.setSectionResizeMode(2, QHeaderView.Stretch)
        self.tabla.doubleClicked.connect(self._usar_seleccion)
        root.addWidget(self.tabla, 1)

        self.barra = QProgressBar()
        self.barra.setRange(0, 0)
        self.barra.setFixedHeight(6)
        self.barra.setTextVisible(False)
        root.addWidget(self.barra)

        self.lbl_estado = QLabel("Iniciando búsqueda…")
        self.lbl_estado.setStyleSheet("color:#9fd4ff;font-size:12px")
        root.addWidget(self.lbl_estado)

        opciones = QHBoxLayout()
        self.chk_barrido = QCheckBox("Escanear todo el rango de la red (más lento, más completo)")
        self.chk_barrido.setChecked(True)
        self.chk_barrido.setToolTip(
            "Además del multicast, prueba los puertos típicos en cada IP de la\n"
            "red. Útil si el switch o la VLAN bloquean el multicast.")
        self.chk_solo_video = QCheckBox("Solo equipos de videovigilancia")
        self.chk_solo_video.setChecked(True)
        self.chk_solo_video.setToolTip(
            "Oculta PCs, routers y servidores. Se consideran equipos de vídeo\n"
            "los que responden a SADP/ONVIF o exponen RTSP (554) o un SDK\n"
            "de fabricante (8000 / 37777).")
        self.chk_solo_video.toggled.connect(self._repintar_tabla)
        opciones.addWidget(self.chk_barrido)
        opciones.addWidget(self.chk_solo_video)
        opciones.addStretch()
        root.addLayout(opciones)

        fila = QHBoxLayout()
        fila.addStretch()

        self.btn_buscar = QPushButton("🔄  Buscar de nuevo")
        self.btn_buscar.setObjectName("sec")
        self.btn_buscar.clicked.connect(self._buscar)
        self.btn_usar = QPushButton("✓  Usar este dispositivo")
        self.btn_usar.setEnabled(False)
        self.btn_usar.clicked.connect(self._usar_seleccion)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("sec")
        btn_cerrar.clicked.connect(self.reject)
        fila.addWidget(self.btn_buscar)
        fila.addWidget(self.btn_usar)
        fila.addWidget(btn_cerrar)
        root.addLayout(fila)

        self.tabla.itemSelectionChanged.connect(
            lambda: self.btn_usar.setEnabled(bool(self.tabla.selectedItems())))

    # -------------------------------------------------------------- lógica
    def _buscar(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.tabla.setRowCount(0)
        self._encontrados = []
        self.btn_buscar.setEnabled(False)
        self.btn_usar.setEnabled(False)
        self.barra.setRange(0, 0)
        self.barra.setVisible(True)
        self.lbl_estado.setText("Buscando dispositivos…")
        self._worker = _DiscoveryWorker(self.chk_barrido.isChecked(), self)
        self._worker.sig_estado.connect(self._on_estado)
        self._worker.sig_listo.connect(self._on_listo)
        self._worker.start()

    def _on_estado(self, texto: str, hechos: int, total: int) -> None:
        self.lbl_estado.setText(texto)
        if total:
            self.barra.setRange(0, total)
            self.barra.setValue(hechos)
        else:
            self.barra.setRange(0, 0)

    def _on_listo(self, encontrados: list) -> None:
        self._encontrados = encontrados
        self.barra.setVisible(False)
        self.btn_buscar.setEnabled(True)
        self._repintar_tabla()

    def _visibles(self) -> list:
        """Dispositivos a mostrar según el filtro «solo equipos de vídeo»."""
        if self.chk_solo_video.isChecked():
            return [d for d in self._encontrados if d.es_equipo_video]
        return list(self._encontrados)

    def _repintar_tabla(self) -> None:
        visibles = self._visibles()
        self._visibles_cache = visibles
        self.tabla.setRowCount(len(visibles))
        for fila, d in enumerate(visibles):
            puertos = ", ".join(str(p) for p in d.puertos_abiertos) or (
                str(d.puerto_http) if d.puerto_http else "—")
            valores = [d.ip, d.marca or "—", d.modelo or d.nombre or "—",
                       d.mac or "—", puertos, "+".join(sorted(d.origen))]
            for col, texto in enumerate(valores):
                self.tabla.setItem(fila, col, QTableWidgetItem(texto))
        ocultos = len(self._encontrados) - len(visibles)
        if visibles:
            extra = f" ({ocultos} equipo(s) sin señales de vídeo oculto(s))" if ocultos else ""
            self.lbl_estado.setText(
                f"{len(visibles)} dispositivo(s) encontrado(s){extra}. "
                "Selecciona uno y pulsa «Usar este dispositivo».")
            self.tabla.selectRow(0)
        elif self._encontrados:
            self.lbl_estado.setText(
                f"Ninguno de los {len(self._encontrados)} equipos detectados "
                "parece de videovigilancia. Desmarca el filtro para verlos todos.")
        else:
            self.lbl_estado.setText(
                "No se encontró ningún dispositivo. Comprueba que estén en la "
                "misma red y que el firewall permita UDP 37020/3702.")

    def _usar_seleccion(self) -> None:
        visibles = getattr(self, "_visibles_cache", self._visibles())
        fila = self.tabla.currentRow()
        if 0 <= fila < len(visibles):
            self.seleccionado = visibles[fila]
            self.accept()

    def closeEvent(self, evento):  # noqa: N802 (API de Qt)
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancelar()
            self._worker.wait(1500)
        super().closeEvent(evento)
