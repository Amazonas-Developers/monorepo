from PySide6.QtWidgets import QStatusBar, QLabel, QWidget, QHBoxLayout, QComboBox, QCheckBox
from PySide6.QtCore import Slot, Qt, Signal

from .custon_btn.btn_footer import BtnIco
from .custon_btn.btn_footer import BtnIco


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
        self.setStyleSheet('''
            QStatusBar { background-color: #424242; color: white; }
        ''')
        
        container = QWidget()
        self.container_layout = QHBoxLayout(container)
        self.container_layout.setSpacing(20)
        self.container_layout.setContentsMargins(0,0,0,0)
        "inserción______⤵️_______"
        self.addPermanentWidget(container)
        
        
        
        """______Lista de clientes_______"""
        if len(self.list_establishment) > 0:
            new_list = []
            
            for iteration in self.list_establishment:
                new_list.append(iteration['name'])

            list_label = QLabel('Selecione el cliente: ')
            self.selector_establishment = QComboBox()
            self.selector_establishment.addItem('Seleccione...')
            self.selector_establishment.addItems(new_list)
            self.container_layout.addWidget(list_label)
            self.container_layout.addWidget(self.selector_establishment)
            self.container_layout.addStretch()
            
            if self.selected_establishment_default is not None:
                
                index_establishment = self.selector_establishment.findText(self.selected_establishment_default)
                if index_establishment != -1: self.selector_establishment.setCurrentIndex(index_establishment)
            '''
            type_inference_default=None,
            selected_establishment_default=None
            '''
        
        
        """____Indicador del server___"""
        self.msg_label = QLabel('Selecione el tipo de inferencia --->')
        self .indicator = QLabel('●')
        self.indicator.setStyleSheet('color: gray;')
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.indicator)
        self.container_layout.addWidget(self.msg_label)
        self.container_layout.addStretch()
        
        # Cliente DEDICADO A TIENDA: solo el modo de analitica de
        # supermercado ('Personal de Amazonas' = PersonAmazonas + retail).
        # Se limita el selector para que el operador no elija por error un
        # modo de otro negocio (Hummus, Autolavado, Perimetrales...).
        self.layout_selector = QComboBox()
        self.layout_selector.addItems(['Seleccione...', 'Personal de Amazonas'])

        # Por defecto seleccionar el modo tienda si no viene otro fijado.
        if self.type_inference_default is None:
            self.type_inference_default = 'Personal de Amazonas'

        if self.type_inference_default is not None:
            index_inference = self.layout_selector.findText(self.type_inference_default)
            if index_inference < 0:                 # modo no soportado aqui
                index_inference = self.layout_selector.findText('Personal de Amazonas')
            self.layout_selector.setCurrentIndex(max(0, index_inference))
            self.layout_selector.setDisabled(True)
            
            
        self.layout_selector.currentTextChanged.connect(self._on_selector_changed)
        "inserción______⤵️_______"
        self.container_layout.addWidget(QLabel("Tipos de inferencias:")) # Etiqueta opcional
        self.container_layout.addWidget(self.layout_selector)

        """____Interruptor de ENVÍO por WHATSAPP (bot 'ava')___
        Reenvia cada alerta como imagen a un grupo de WhatsApp; el envio lo
        hace el servidor. Es GLOBAL para todas las camaras y su estado se
        persiste (ver main.py). Por defecto DESACTIVADO."""
        self.chk_envio_whatsapp = QCheckBox('Enviar por WhatsApp')
        self.chk_envio_whatsapp.setChecked(False)
        self.chk_envio_whatsapp.setToolTip(
            'Activar/desactivar el envío de alertas por WhatsApp.\n'
            'Desactivado: las alertas siguen viéndose en el panel lateral,\n'
            'pero NO se envían imágenes al grupo de WhatsApp.')
        self.chk_envio_whatsapp.setStyleSheet(
            'QCheckBox { color: #999; }'
            'QCheckBox::indicator { width: 14px; height: 14px; }')
        self.chk_envio_whatsapp.toggled.connect(self._on_envio_whatsapp_toggled)
        self.container_layout.addWidget(self.chk_envio_whatsapp)

        self.btn_stopconection = BtnIco(ico_path='resource/finish_connection.png', title='Cerrar conexión con el servidor', h=25, w=25)
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_stopconection)

        """____Boton para selección de render_BOX___"""
        self.btn_layout = BtnIco(ico_path='resource/layout.png', title='Divisiones de ventanas: (3x3, 2x2, etc.)')
        "inserción______⤵️_______"
        self.container_layout.addWidget(self.btn_layout)

    def modo_seleccionado(self) -> str:
        """El modo de inferencia que REALMENTE muestra el selector, ya
        normalizado a los que este cliente soporta.

        Existe porque el valor persistido (`last_inference`) puede ser de otro
        negocio: en ese caso el selector cae a 'Personal de Amazonas' pero
        quien arranca la conexion seguia usando el valor crudo, asi que la
        pantalla decia una cosa y el websocket enviaba otra (H-14)."""
        texto = self.layout_selector.currentText()
        return '' if texto in ('', 'Seleccione...') else texto
    
    
    
    def _on_selector_changed(self, text):
        if text != 'Seleccione...':
            self.inference_type_selected.emit(text)
            self.layout_selector.setDisabled(True)
        
    
    
    

    def _on_envio_whatsapp_toggled(self, activo):
        """Retroalimentacion visual del interruptor de envio por WhatsApp."""
        if activo:
            self.chk_envio_whatsapp.setStyleSheet(
                'QCheckBox { color: white; }'
                'QCheckBox::indicator { width: 14px; height: 14px; }')
            self.showMessage('Envío de alertas por WhatsApp ACTIVADO', 4000)
        else:
            self.chk_envio_whatsapp.setStyleSheet(
                'QCheckBox { color: #999; }'
                'QCheckBox::indicator { width: 14px; height: 14px; }')
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