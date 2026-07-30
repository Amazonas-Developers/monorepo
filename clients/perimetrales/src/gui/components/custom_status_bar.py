import os

from PySide6.QtWidgets import (QStatusBar, QLabel, QWidget, QHBoxLayout,
                               QComboBox, QCheckBox, QPushButton)
from PySide6.QtCore import Slot, Qt, Signal, QUrl, QTimer
from PySide6.QtGui import QDesktopServices

from .custon_btn.btn_footer import BtnIco
from core.dashboard_url import url_dashboard
from gui.styles import tema


# Un solo estilo para los dos interruptores: antes el de WhatsApp iba en
# gris apagado y parecia deshabilitado aun estando operativo.
_ESTILO_CHECK = f"""
    QCheckBox {{ color: {tema.TEXTO_SUAVE}; font-size: 11px;
                 spacing: 6px; background: transparent; }}
    QCheckBox:hover {{ color: {tema.TEXTO}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px;
        border: 1px solid {tema.BORDE}; border-radius: 3px;
        background-color: {tema.ELEVADO}; }}
    QCheckBox::indicator:checked {{
        background-color: {tema.EXITO}; border-color: {tema.EXITO}; }}
"""


class CustomStatusBar(QStatusBar):
    
    
    inference_type_selected = Signal(str)
    
    
    def __init__(self, 
                list_establishment=[],
                type_inference_default=None,
                selected_establishment_default=None
        ):
        
        super().__init__(parent=None)
        print(list_establishment)
        self.list_establishment =  list_establishment
        self.selected_establishment_default=selected_establishment_default
        self.type_inference_default=type_inference_default
        self.setup_ui()
        
        
    def setup_ui(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(35)
        self.setObjectName('FooterBar')
        self.setStyleSheet(
            f"QStatusBar {{ background-color:{tema.SUPERFICIE};"
            f" color:{tema.TEXTO_SUAVE};"
            f" border-top:1px solid {tema.BORDE}; }}"
            f"QStatusBar QLabel {{ color:{tema.TEXTO_SUAVE};"
            " font-size:11px; background:transparent; }}")
        
        container = QWidget()
        self.container_layout = QHBoxLayout(container)
        self.container_layout.setSpacing(20)
        self.container_layout.setContentsMargins(0,0,0,0)
        "inserción______⤵️_______"
        self.addPermanentWidget(container)

        """____Interruptor de ENVÍO a Jarvis (activar/desactivar la API)___
        Controla si las alertas del sidebar se reenvían a Jarvis365 como
        novedades. El estado se persiste (ver main.py); el checkbox solo es
        la UI."""
        self.chk_envio_jarvis = QCheckBox('Enviar a Jarvis')
        self.chk_envio_jarvis.setChecked(True)
        self.chk_envio_jarvis.setToolTip(
            'Activar/desactivar el envío de alertas a la API de Jarvis365.\n'
            'Desactivado: las alertas siguen viéndose en el panel lateral,\n'
            'pero NO se envían novedades al establecimiento.')
        self.chk_envio_jarvis.setStyleSheet(_ESTILO_CHECK)
        self.chk_envio_jarvis.toggled.connect(self._on_envio_jarvis_toggled)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.chk_envio_jarvis)

        """____Interruptor de ENVÍO por WHATSAPP (bot 'ava')___
        Habilita/deshabilita el reenvío de CADA alerta como imagen a un grupo
        de WhatsApp (el envío lo hace el servidor VigilanteAmazonas). Es GLOBAL
        para todas las cámaras. El estado se persiste (ver main.py); el checkbox
        es solo la UI. Por defecto DESACTIVADO (no envía hasta que lo actives)."""
        self.chk_envio_whatsapp = QCheckBox('Enviar por WhatsApp')
        self.chk_envio_whatsapp.setChecked(False)
        self.chk_envio_whatsapp.setToolTip(
            'Activar/desactivar el envío de alertas por WhatsApp.\n'
            'Desactivado: las alertas siguen viéndose en el panel lateral,\n'
            'pero NO se envían imágenes al grupo de WhatsApp.')
        self.chk_envio_whatsapp.setStyleSheet(_ESTILO_CHECK)
        self.chk_envio_whatsapp.toggled.connect(self._on_envio_whatsapp_toggled)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.chk_envio_whatsapp)

        """____Selector de ESTABLECIMIENTO Jarvis (destino de las alertas)___
        Se crea SIEMPRE (aunque la lista llegue vacía): los establecimientos
        se cargan de forma asíncrona tras el login de Jarvis y el combo se
        rellena con cargar_establecimientos() cuando la lista llega."""
        self.selector_establishment = QComboBox()
        self.selector_establishment.setMinimumWidth(180)
        self.selector_establishment.setToolTip(
            'Establecimiento de Jarvis365 al que se envían las alertas')
        self.selector_establishment.addItem('Cargando establecimientos…')
        self.selector_establishment.setEnabled(False)
        "inserción______⤵️_______"
        self.container_layout.addWidget(QLabel("Alertas a:"))
        self.container_layout.addWidget(self.selector_establishment)

        """____Indicador del server___"""
        self.msg_label = QLabel('Selecione el tipo de inferencia --->')
        self .indicator = QLabel('●')
        self.indicator.setStyleSheet('color: gray;')
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.indicator)
        self.container_layout.addWidget(self.msg_label)
        self.container_layout.addStretch()

        # VigilanteAmazonas = deteccion de 7 clases + Re-ID de personas de
        # interes + verificador VLM (panel en http://<servidor>:5333/).
        #
        # Es el UNICO modo que se ofrece: los antiguos "Perimetrales" y
        # "PerimetralesMultiCam" existen solo como .pyc compilados, sin
        # codigo fuente, asi que no se pueden depurar ni corregir. Quien
        # los necesite en un despliegue viejo puede reactivarlos con
        # PERIMETRALES_MODOS_LEGADO=1.
        self.layout_selector = QComboBox()
        modos = ['Seleccione...', 'VigilanteAmazonas']
        if os.getenv('PERIMETRALES_MODOS_LEGADO', '') == '1':
            modos[1:1] = ['Perimetrales', 'PerimetralesMultiCam']
        self.layout_selector.addItems(modos)
        
        if self.type_inference_default is not None:
            index_inference = self.layout_selector.findText(self.type_inference_default)
            # Solo restaurar si la inferencia guardada sigue existiendo en la
            # lista (configs viejas pueden traer modos de otros despliegues).
            if index_inference != -1:
                self.layout_selector.setCurrentIndex(index_inference)
                self.layout_selector.setDisabled(True)
            
            
        self.layout_selector.currentTextChanged.connect(self._on_selector_changed)
        "inserción______⤵️_______"
        self.container_layout.addWidget(QLabel("Tipos de inferencias:")) # Etiqueta opcional
        self.container_layout.addWidget(self.layout_selector)
        
        self.btn_stopconection = BtnIco(ico_path='resource/finish_connection.png', title='Cerrar conexión con el servidor', h=25, w=25)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_stopconection)

        """____Interruptor del VLM verificador___
        Enciende/apaga el VLM (Qwen2.5-VL) que verifica en vivo las
        detecciones dudosas del Re-ID. El estado real lo tiene el SERVIDOR:
        este boton lo consulta al abrir y lo conmuta por HTTP, asi que
        refleja lo que de verdad esta pasando, no una copia local."""
        self.btn_vlm = QPushButton('VLM')
        self.btn_vlm.setCursor(Qt.PointingHandCursor)
        self.btn_vlm.setFixedHeight(24)
        self.btn_vlm.setCheckable(True)
        self.btn_vlm.setToolTip(
            'VLM verificador: da una segunda opinión sobre las\n'
            'identificaciones dudosas. Más lento, pero acierta más.')
        self.btn_vlm.setStyleSheet(tema.boton())
        self.btn_vlm.clicked.connect(self._alternar_vlm)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_vlm)
        # El estado lo tiene el servidor: se consulta al arrancar.
        QTimer.singleShot(900, self._consultar_vlm)

        """____Boton: GALERIA DE PERSONAS DE INTERES (dashboard web)___
        Abre en el navegador el panel donde se registran las personas y se
        suben sus fotos de rostro/vestimenta. La URL se deduce del servidor
        configurado (server_ws_url) -> http://<host>:5333/gestion/."""
        self.btn_galeria_personas = BtnIco(
            ico_path='resource/person.png',
            title='Personas de interés: registrar y subir fotos '
                  '(rostro / vestimenta)', h=25, w=25)
        self.btn_galeria_personas.clicked.connect(self._abrir_dashboard)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_galeria_personas)
        
        """____Boton: PANEL DE DETECCIONES (:5333)___
        Totales de personas y vehiculos + galeria de las capturas."""
        self.btn_panel = QPushButton('Panel')
        self.btn_panel.setCursor(Qt.PointingHandCursor)
        self.btn_panel.setFixedHeight(24)
        self.btn_panel.setToolTip(
            'Abrir el panel de detecciones en el navegador:\n'
            'totales de personas y vehículos, y galería de capturas.')
        self.btn_panel.setStyleSheet(tema.boton())
        self.btn_panel.clicked.connect(self._abrir_panel)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_panel)

        """____Boton para selección de render_BOX___"""
        self.btn_layout = BtnIco(ico_path='resource/layout.png', title='Divisiones de ventanas: (3x3, 2x2, etc.)')
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_layout)
    
    
    
    def _on_selector_changed(self, text):
        if text != 'Seleccione...':
            self.inference_type_selected.emit(text)
            self.layout_selector.setDisabled(True)


    def _abrir_dashboard(self):
        """Abre la gestión de personas de interés en el navegador.

        Vive dentro del panel (:5333/gestion): antes era un servidor aparte
        en :8090 y ahora comparte puerto para no tener dos tableros.
        """
        url = f"{url_dashboard()}/gestion/"
        try:
            abierto = QDesktopServices.openUrl(QUrl(url))
        except Exception:
            abierto = False
        if abierto:
            self.showMessage(f'Abriendo la galería de personas: {url}', 5000)
        else:
            # No se pudo lanzar el navegador: mostrar la URL para copiarla.
            self.showMessage(f'Abra manualmente la galería en: {url}', 12000)
        print(f'[dashboard] {url}')


    def _on_envio_jarvis_toggled(self, activo):
        """Retroalimentación visual del interruptor de envío a Jarvis."""
        if activo:
            self.chk_envio_jarvis.setStyleSheet('''
                QCheckBox { color: white; }
                QCheckBox::indicator { width: 14px; height: 14px; }
            ''')
            self.showMessage('Envío de alertas a Jarvis ACTIVADO', 4000)
        else:
            self.chk_envio_jarvis.setStyleSheet('''
                QCheckBox { color: #999; }
                QCheckBox::indicator { width: 14px; height: 14px; }
            ''')
            self.showMessage('Envío de alertas a Jarvis DESACTIVADO '
                             '(el panel lateral sigue mostrando alertas)', 5000)


    def _on_envio_whatsapp_toggled(self, activo):
        """Retroalimentación visual del interruptor de envío por WhatsApp."""
        if activo:
            self.chk_envio_whatsapp.setStyleSheet('''
                QCheckBox { color: white; }
                QCheckBox::indicator { width: 14px; height: 14px; }
            ''')
            self.showMessage('Envío de alertas por WhatsApp ACTIVADO', 4000)
        else:
            self.chk_envio_whatsapp.setStyleSheet('''
                QCheckBox { color: #999; }
                QCheckBox::indicator { width: 14px; height: 14px; }
            ''')
            self.showMessage('Envío de alertas por WhatsApp DESACTIVADO '
                             '(el panel lateral sigue mostrando alertas)', 5000)


    @Slot(bool, str)
    def update_ui(self, is_connected, message):
        if is_connected:
            self.showMessage('Conexión establecida con el servidor', 3000)
            self.indicator.setStyleSheet('color: #4eff2b; font-weight: bold;')
            self.msg_label.setStyleSheet('color: #4eff2b; font-weight: bold;')
            self.layout_selector.setEnabled(False)
        else:
            self.showMessage('Conexión perdida con el servidor', 3000)
            self.indicator.setStyleSheet('color: #8B0000; font-weight: bold;')
            self.msg_label.setStyleSheet('color: white; font-weight: bold;')
            self.layout_selector.setEnabled(True)
        self.msg_label.setText(message)
       
    
    @Slot(str)
    def receive_message(self, mesagge):
        self.showMessage(mesagge, 3000)


    def cargar_establecimientos(self, nombres, seleccionado=None):
        """Rellena el selector cuando la lista de Jarvis llega (async).

        `nombres`: lista de nombres de establecimientos.
        `seleccionado`: nombre a dejar elegido (guardado previo o el
        auto-seleccionado por Jarvis_api); si no está en la lista, el primero.
        Bloquea señales durante el llenado para no disparar
        currentTextChanged de forma programática.
        """
        self.selector_establishment.blockSignals(True)
        self.selector_establishment.clear()
        if not nombres:
            self.selector_establishment.addItem('Sin establecimientos')
            self.selector_establishment.setEnabled(False)
            self.selector_establishment.blockSignals(False)
            return
        self.selector_establishment.addItems(list(nombres))
        indice = self.selector_establishment.findText(seleccionado or '')
        self.selector_establishment.setCurrentIndex(indice if indice != -1 else 0)
        self.selector_establishment.setEnabled(True)
        self.selector_establishment.blockSignals(False)

    # ── VLM verificador ──────────────────────────────────────────────

    def _servidor_http(self) -> str:
        """URL base del panel, derivada de la del servidor configurado."""
        return url_dashboard()

    def _pintar_vlm(self, activo: bool, modelo: str = "",
                    aviso: str = "") -> None:
        """Refleja en el boton el estado que reporta el servidor."""
        self._vlm_activo = bool(activo)
        self.btn_vlm.setChecked(bool(activo))
        sufijo = f" {modelo}" if modelo else ""
        self.btn_vlm.setText(f"VLM{sufijo}: {'ON' if activo else 'OFF'}")
        color = tema.EXITO if activo else tema.TEXTO_TENUE
        self.btn_vlm.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {color};"
            f" border: 1px solid {color if activo else tema.BORDE};"
            f" border-radius: {tema.RADIO_SM}; padding: 3px 10px;"
            f" font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {tema.ACENTO_FONDO}; }}")
        if aviso:
            self.btn_vlm.setToolTip(aviso)

    def _consultar_vlm(self) -> None:
        """Lee del servidor si el VLM esta encendido (al arrancar)."""
        import json as _json
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(
                    f"{self._servidor_http()}/api/vlm", timeout=6) as resp:
                datos = _json.loads(resp.read().decode("utf-8"))
            aviso = ""
            if not datos.get("presente"):
                aviso = ("El servidor no cargó el VLM (sin VRAM suficiente\n"
                         "o deshabilitado en su configuración).")
            elif not datos.get("cargado"):
                aviso = "El VLM esta cargando el modelo..."
            self._pintar_vlm(datos.get("activo", True),
                             datos.get("modelo", ""), aviso)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._pintar_vlm(False, "", f"Sin conexion con el panel: {exc}")
            print(f"[VLM] no se pudo consultar el estado: {exc}")

    def _alternar_vlm(self) -> None:
        """Enciende o apaga el VLM en el servidor."""
        import json as _json
        import urllib.error
        import urllib.request
        nuevo = not getattr(self, "_vlm_activo", True)
        self.btn_vlm.setEnabled(False)
        try:
            peticion = urllib.request.Request(
                f"{self._servidor_http()}/api/vlm?activo="
                f"{'true' if nuevo else 'false'}", method="POST")
            with urllib.request.urlopen(peticion, timeout=8) as resp:
                datos = _json.loads(resp.read().decode("utf-8"))
            self._pintar_vlm(datos.get("activo", nuevo))
            print(f"[VLM] {datos.get('mensaje', '')}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # No se cambio nada en el servidor: el boton no debe mentir.
            self._pintar_vlm(getattr(self, "_vlm_activo", True), "",
                             f"No se pudo cambiar: {exc}")
            print(f"[VLM] no se pudo cambiar: {exc}")
        finally:
            self.btn_vlm.setEnabled(True)

    def _abrir_panel(self) -> None:
        """Abre el panel de detecciones (:5333) en el navegador."""
        url = url_dashboard()
        try:
            abierto = QDesktopServices.openUrl(QUrl(url))
        except Exception:  # noqa: BLE001
            abierto = False
        self.showMessage(
            f'Abriendo el panel: {url}' if abierto
            else f'Abra manualmente el panel en: {url}', 8000)
