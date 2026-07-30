"""
Panel de capturas que lee del SERVIDOR por HTTP.

## Por que por HTTP y no de una carpeta

`Amazonas View` ya tenia un panel asi, pero leyendo la carpeta `capture/` que
el servidor escribe en disco. Eso funciona **solo si cliente y servidor estan
en la misma maquina**, y ademas dependia de `CAPTURE_CLIENT_DIR`, una ruta
absoluta escrita a mano y apuntando a Amazonas View: por eso `tienda_view` no
tenia carpeta de capturas y su panel no podia mostrar nada.

Este panel pide las capturas a la API que el servidor ya expone:

    GET  /dashboard/api/captures?limit=N     -> lista con genero, edad, hora
    GET  /dashboard/img/capture/<stem>.jpg   -> la foto

Con eso funciona igual con el servidor en local o en otra IP —que es el caso
real hoy—, sin ninguna ruta configurada y sin depender de compartir disco.

## Coste

Una peticion cada `intervalo_s` y una descarga por foto nueva. Las fotos ya
vistas no se vuelven a pedir: se lleva el registro de los `stem` mostrados.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import QByteArray, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

_MINIATURA = 64
MAX_TARJETAS = 120


def base_http_del_websocket(url_ws: str) -> str:
    """`ws://host:9000/ws` -> `http://host:9000`.

    Se deriva de la URL a la que el cliente YA esta conectado en vez de pedir
    otra configuracion: si el servidor cambia de sitio, el panel le sigue."""
    base = (url_ws or '').replace('wss://', 'https://').replace('ws://', 'http://')
    if '/ws' in base:
        base = base.rsplit('/ws', 1)[0]
    return base.rstrip('/') or 'http://127.0.0.1:9000'


class _Descarga(QThread):
    """Pide la lista de capturas y las fotos nuevas, fuera del hilo de la UI.

    Va en un hilo aparte porque una peticion HTTP a un servidor remoto puede
    tardar segundos y congelaria la ventana."""

    listo = Signal(list)          # [(meta: dict, jpeg: bytes|None)]

    def __init__(self, base: str, vistos: set, limite: int, parent=None):
        super().__init__(parent)
        self._base = base
        self._vistos = set(vistos)
        self._limite = limite

    def run(self):
        try:
            import requests
            r = requests.get(f'{self._base}/dashboard/api/captures',
                             params={'limit': self._limite}, timeout=10)
            datos = r.json()
            if datos.get('status') != 'ok':
                self.listo.emit([])
                return
            nuevas = []
            for meta in datos.get('capturas', []):
                stem = str(meta.get('stem') or '')
                if not stem or stem in self._vistos:
                    continue
                jpeg = None
                try:
                    ri = requests.get(
                        f'{self._base}/dashboard/img/capture/{stem}.jpg',
                        timeout=10)
                    if ri.status_code == 200:
                        jpeg = ri.content
                except Exception:
                    pass
                nuevas.append((meta, jpeg))
            self.listo.emit(nuevas)
        except Exception:
            self.listo.emit([])     # sin servidor, el panel se queda como esta


class _TarjetaCaptura(QFrame):
    """Una persona: foto, genero y edad, hora y numero de visita."""

    def __init__(self, meta: dict, jpeg: Optional[bytes], parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            'QFrame { background:#1e1e1e; border-left:3px solid #00a8e8;'
            ' border-radius:4px; }'
            'QFrame:hover { background:#282828; }')
        fila = QHBoxLayout(self)
        fila.setContentsMargins(6, 5, 6, 5)
        fila.setSpacing(8)

        if jpeg:
            pix = QPixmap()
            if pix.loadFromData(QByteArray(jpeg)):
                foto = QLabel()
                foto.setFixedSize(_MINIATURA, _MINIATURA)
                foto.setScaledContents(True)
                foto.setPixmap(pix)
                foto.setStyleSheet('border-radius:3px;')
                fila.addWidget(foto)

        texto = QVBoxLayout()
        texto.setSpacing(1)

        genero = str(meta.get('gender') or '').strip()
        edad = str(meta.get('age_range') or '').strip()
        # Sin demografia la captura sigue siendo util (hubo una persona), pero
        # se dice claramente en vez de mostrar un hueco.
        titulo = ' · '.join(x for x in (genero, edad) if x) or 'Sin identificar'
        lbl = QLabel(titulo)
        lbl.setStyleSheet('color:#00a8e8; font-weight:bold; font-size:11px;')
        texto.addWidget(lbl)

        visitas = meta.get('visitas')
        if isinstance(visitas, int) and visitas > 1:
            v = QLabel(f'Visita nº {visitas} · recurrente')
            v.setStyleSheet('color:#2ecc71; font-size:10px;')
            texto.addWidget(v)

        pie = []
        ts = str(meta.get('timestamp') or '')
        if len(ts) >= 15 and '_' in ts:          # 20260730_090000
            h = ts.split('_')[1]
            pie.append(f'🕒 {h[:2]}:{h[2:4]}:{h[4:6]}')
        cam = str(meta.get('camera') or '').strip()
        if cam:
            pie.append(f'📹 {cam}')
        if pie:
            p = QLabel('   '.join(pie))
            p.setStyleSheet('color:#777; font-size:9px;')
            texto.addWidget(p)

        fila.addLayout(texto, stretch=1)


class PanelCapturas(QWidget):
    """Ultimas personas detectadas, servidas por el servidor.

    `url_ws` es la URL del websocket a la que el cliente esta conectado; de
    ahi se deriva la del servidor HTTP."""

    def __init__(self, url_ws: str = '', titulo: str = 'Personas detectadas',
                 intervalo_s: int = 8, limite: int = 40, parent=None):
        super().__init__(parent)
        self._base = base_http_del_websocket(
            url_ws or os.getenv('server_ws_url', ''))
        self._limite = limite
        self._vistos: set = set()
        self._hilo: Optional[_Descarga] = None

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(4, 4, 4, 4)
        raiz.setSpacing(5)

        cab = QHBoxLayout()
        t = QLabel(titulo)
        t.setStyleSheet('color:#eee; font-weight:bold; font-size:12px;')
        cab.addWidget(t)
        cab.addStretch()
        self.contador = QLabel('0')
        self.contador.setStyleSheet(
            'color:#fff; background:#00a8e8; border-radius:7px; padding:0 6px;'
            ' font-size:10px; font-weight:bold;')
        cab.addWidget(self.contador)
        raiz.addLayout(cab)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            'QScrollArea { border:none; background:transparent; }')
        cont = QWidget()
        cont.setStyleSheet('background:transparent;')
        self.lista = QVBoxLayout(cont)
        self.lista.setContentsMargins(0, 0, 0, 0)
        self.lista.setSpacing(4)
        self.lista.setAlignment(Qt.AlignTop)

        self.vacio = QLabel('Esperando detecciones del servidor…')
        self.vacio.setAlignment(Qt.AlignCenter)
        self.vacio.setWordWrap(True)
        self.vacio.setStyleSheet('color:#666; font-size:10px; padding:14px 6px;')
        self.lista.addWidget(self.vacio)

        self.scroll.setWidget(cont)
        raiz.addWidget(self.scroll, stretch=1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refrescar)
        self._timer.start(max(2, intervalo_s) * 1000)
        QTimer.singleShot(500, self.refrescar)

    def refrescar(self) -> None:
        """Pide las capturas nuevas. Si ya hay una peticion en curso, espera:
        con el servidor lento, encadenar peticiones solo empeora las cosas."""
        if self._hilo is not None and self._hilo.isRunning():
            return
        self._hilo = _Descarga(self._base, self._vistos, self._limite, self)
        self._hilo.listo.connect(self._pintar)
        self._hilo.start()

    def _pintar(self, nuevas: List[tuple]) -> None:
        if not nuevas:
            return
        if self.vacio is not None:
            self.vacio.setVisible(False)
        # Las mas recientes primero: la API ya las devuelve ordenadas, asi que
        # se insertan en orden inverso para que la ultima quede arriba.
        for meta, jpeg in reversed(nuevas):
            stem = str(meta.get('stem') or '')
            if not stem or stem in self._vistos:
                continue
            self._vistos.add(stem)
            self.lista.insertWidget(0, _TarjetaCaptura(meta, jpeg))
        self.contador.setText(str(len(self._vistos)))
        while self.lista.count() > MAX_TARJETAS + 1:
            item = self.lista.takeAt(self.lista.count() - 1)
            w = item.widget() if item else None
            if w is not None and w is not self.vacio:
                w.deleteLater()

    def closeEvent(self, event):
        """Parar el hilo antes de que Qt destruya el panel.

        Un QThread destruido mientras corre hace que Qt aborte el proceso
        (0xc0000409): es el mismo fallo que tumbaba a los clientes al cerrar
        (HALLAZGOS.md H-01), asi que aqui se previene desde el principio."""
        try:
            self._timer.stop()
            if self._hilo is not None and self._hilo.isRunning():
                self._hilo.wait(3000)
        except Exception:
            pass
        super().closeEvent(event)
