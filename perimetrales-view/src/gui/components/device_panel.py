"""
src/gui/components/device_panel.py
Panel completo DVR para la pestaña "Dispositivos".

Izquierda: formulario agregar/editar
Derecha:   lista de dispositivos guardados

Señales:
    devices_updated → emitida al guardar o eliminar (actualiza sidebar)
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QGroupBox, QScrollArea, QFrame, QFileDialog,
    QProgressBar, QTextEdit, QSplitter, QMessageBox, QInputDialog,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QFont

from models.dvr_storage                       import DVRRepository, DVRDevice
from workers.dvr_connect_worker               import DVRConnectWorker
from core.dvr                                 import DVRContext, DeviceInfo
from core.dvr.hikconnect_channel_encoder     import HikConnectChannelEncoder, ChannelTypeDetector
from .channel_row                            import ChannelRow


# ── Fila de un dispositivo en la lista ───────────────────────

class _DeviceRow(QFrame):
    sig_edit   = Signal(str)
    sig_delete = Signal(str)

    def __init__(self, device: DVRDevice, parent=None):
        super().__init__(parent)
        self._id = device.id
        self.setObjectName("DeviceRow")
        self.setFixedHeight(58)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)

        dot = QLabel("●")
        dot.setObjectName("RowDot")
        dot.setFixedWidth(14)

        col = QVBoxLayout()
        col.setSpacing(1)
        self.lbl_name = QLabel(device.alias or device.host)
        self.lbl_name.setObjectName("RowName")
        self.lbl_sub = QLabel(
            f"{device.brand}  ·  {device.host}:{device.port}"
            f"  ·  {device.num_channels} canal(es)"
        )
        self.lbl_sub.setObjectName("RowSub")
        col.addWidget(self.lbl_name)
        col.addWidget(self.lbl_sub)

        btn_e = QPushButton("✏")
        btn_e.setObjectName("BtnRowIcon")
        btn_e.setFixedSize(26, 26)
        btn_e.setToolTip("Editar")
        btn_e.clicked.connect(lambda: self.sig_edit.emit(self._id))

        btn_d = QPushButton("🗑")
        btn_d.setObjectName("BtnRowDel")
        btn_d.setFixedSize(26, 26)
        btn_d.setToolTip("Eliminar")
        btn_d.clicked.connect(lambda: self.sig_delete.emit(self._id))

        layout.addWidget(dot)
        layout.addLayout(col, stretch=1)
        layout.addWidget(btn_e)
        layout.addWidget(btn_d)

    def refresh(self, device: DVRDevice):
        self._id = device.id
        self.lbl_name.setText(device.alias or device.host)
        if device.brand == "Hik-Connect":
            brand_display = "Hik-Connect"
        else:
            brand_display = device.brand
        self.lbl_sub.setText(
            f"{brand_display}  ·  {device.host or 'cloud'}:{device.port}"
            f"  ·  {device.num_channels} canal(es)"
        )


# ── Panel principal ───────────────────────────────────────────

class DevicePanel(QWidget):
    devices_updated = Signal()

    # Texto del combo de región que significa "probar todas las regiones".
    _REGION_AUTO = "Automático (probar todas)"

    # Tipos de conexión (texto del combo) y la marca que le corresponde.
    _TIPO_IP = "Conexión por IP"
    _TIPO_HC = "Hik-Connect (empresa)"
    _TIPO_EZ = "EZVIZ / Hik-Connect (cuenta personal)"
    _MARCA_POR_TIPO = {_TIPO_HC: "Hik-Connect", _TIPO_EZ: "EZVIZ"}

    def _marca_nube(self) -> str:
        """Marca de nube del tipo elegido, o '' si es conexión por IP."""
        return self._MARCA_POR_TIPO.get(
            self.cb_connection_type.currentText(), "")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._repo        = DVRRepository()
        self._rows: dict[str, _DeviceRow] = {}
        self._editing_id: str | None = None
        self._worker:     DVRConnectWorker | None = None
        # Workers que no pararon a tiempo: se conservan referenciados
        # hasta que terminen solos (destruir un QThread vivo aborta la app).
        self._huerfanos: list = []
        self._last_info:  DeviceInfo | None = None

        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()
        self._apply_styles()
        self._load_devices()

    # ── UI ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.addWidget(self._build_form())
        splitter.addWidget(self._build_list())
        splitter.setSizes([420, 560])
        root.addWidget(splitter)

    def _build_form(self) -> QWidget:
        w = QWidget()
        w.setObjectName("FormPanel")
        w.setAttribute(Qt.WA_StyledBackground, True)
        root = QVBoxLayout(w)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        self._form_title = QLabel("Agregar dispositivo DVR")
        self._form_title.setObjectName("FormTitle")
        root.addWidget(self._form_title)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("FormSep"); root.addWidget(sep)

        # Tipo de conexión
        grp_type = QGroupBox("Tipo de conexión")
        f_type   = QFormLayout(grp_type)
        f_type.setSpacing(8); f_type.setContentsMargins(12, 12, 12, 12)
        self.cb_connection_type = QComboBox()
        # Tres vías, porque Hikvision tiene dos nubes distintas:
        #  • Hik-Connect  -> HikCentral Connect / Teams (cuentas de empresa)
        #  • EZVIZ        -> Hik-Connect de consumo (la cuenta de la app móvil)
        self.cb_connection_type.addItems(
            [self._TIPO_IP, self._TIPO_HC, self._TIPO_EZ])
        self.cb_connection_type.currentTextChanged.connect(self._on_connection_type_changed)
        f_type.addRow("Tipo:", self.cb_connection_type)
        # Descubrimiento en red: busca DVR/NVR/cámaras IP y rellena el
        # formulario con la IP/puerto/marca del equipo elegido.
        self.btn_discover = QPushButton("🔍  Buscar dispositivos en la red")
        self.btn_discover.setObjectName("BtnTest")
        self.btn_discover.setMinimumHeight(30)
        self.btn_discover.setToolTip(
            "Detecta automáticamente DVR, NVR y cámaras IP conectados a la red\n"
            "(Hikvision por SADP, cualquier marca por ONVIF y escaneo de puertos).")
        self.btn_discover.clicked.connect(self._discover_devices)
        f_type.addRow("", self.btn_discover)
        root.addWidget(grp_type)

        # Nombre
        grp_id = QGroupBox("Identificación")
        f_id   = QFormLayout(grp_id)
        f_id.setSpacing(8); f_id.setContentsMargins(12, 12, 12, 12)
        self.txt_alias = QLineEdit()
        self.txt_alias.setPlaceholderText("Ej: Oficina Principal, Sucursal Norte…")
        f_id.addRow("Nombre del dispositivo:", self.txt_alias)
        root.addWidget(grp_id)

        # Conexión
        self.grp_conn = QGroupBox("Conexión")
        f_conn   = QFormLayout(self.grp_conn)
        f_conn.setSpacing(8); f_conn.setContentsMargins(12, 12, 12, 12)

        self.cb_brand = QComboBox()
        self.cb_brand.addItems(DVRContext.brands())
        self.cb_brand.currentTextChanged.connect(self._on_brand_changed)

        self.txt_host = QLineEdit()
        self.txt_host.setPlaceholderText("192.168.1.64")

        self.txt_port = QLineEdit("80")
        self.txt_port.setFixedWidth(70)

        host_row = QHBoxLayout()
        host_row.addWidget(self.txt_host)
        host_row.addWidget(QLabel(":"))
        host_row.addWidget(self.txt_port)

        port_row = QHBoxLayout(); port_row.setSpacing(4)
        for p in ["80", "8080", "8000", "37777"]:
            b = QPushButton(p)
            b.setObjectName("BtnQuick"); b.setFixedHeight(22)
            b.clicked.connect(lambda _, port=p: self.txt_port.setText(port))
            port_row.addWidget(b)

        f_conn.addRow("Tipo / Marca:", self.cb_brand)
        f_conn.addRow("IP / Host:", host_row)
        f_conn.addRow("Puerto rápido:", port_row)
        root.addWidget(self.grp_conn)

        # Credenciales
        self.grp_cred = QGroupBox("Credenciales")
        f_cred   = QFormLayout(self.grp_cred)
        f_cred.setSpacing(8); f_cred.setContentsMargins(12, 12, 12, 12)

        self.lbl_user = QLabel("Usuario:")
        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("admin")
        self.lbl_pass = QLabel("Contraseña:")
        self.txt_pass = QLineEdit()
        self.txt_pass.setEchoMode(QLineEdit.Password)
        self.txt_pass.setPlaceholderText("••••••••")
        self.txt_pass.returnPressed.connect(self._test_connection)

        # Para Hik-Connect
        self.lbl_appkey = QLabel("App Key:")
        self.txt_appkey = QLineEdit()
        self.txt_appkey.setPlaceholderText("Tu App Key de Hik-Connect")
        self.lbl_appsecret = QLabel("App Secret:")
        self.txt_appsecret = QLineEdit()
        self.txt_appsecret.setEchoMode(QLineEdit.Password)
        self.txt_appsecret.setPlaceholderText("••••••••")
        self.txt_appsecret.returnPressed.connect(self._test_connection)

        # Región del servidor de Hik-Connect. Cada cuenta vive en una región;
        # "Automático" prueba todas (funciona siempre, tarda un poco más).
        # También admite escribir un dominio propio (cuentas OEM/privadas).
        self.lbl_region = QLabel("Región:")
        self.cb_region = QComboBox()
        self.cb_region.setEditable(True)
        self.cb_region.addItems([
            self._REGION_AUTO,
            "isa.hikcentralconnect.com  (Internacional / Asia)",
            "ius.hikcentralconnect.com  (América)",
            "ieu.hikcentralconnect.com  (Europa)",
            "isgp.hikcentralconnect.com  (Singapur)",
        ])
        self.cb_region.setToolTip(
            "Región del servidor donde vive la cuenta.\n"
            "Con «Automático» se prueban todas hasta que una responda.\n"
            "También puedes escribir un dominio propio.")

        # Reutilizar las credenciales por defecto del .env sin bloquear nada:
        # el formulario admite CUALQUIER App Key/Secret.
        self.btn_env_creds = QPushButton("↺  Usar credenciales del .env")
        self.btn_env_creds.setObjectName("BtnQuick")
        self.btn_env_creds.setFixedHeight(24)
        self.btn_env_creds.setToolTip(
            "Rellena App Key y Secret con los valores de hik_app_key /\n"
            "hik_app_secret del .env. Puedes escribir otros para conectarte\n"
            "con una cuenta distinta.")
        self.btn_env_creds.clicked.connect(
            lambda: self._prefill_hik_env(forzar=True))

        f_cred.addRow(self.lbl_user, self.txt_user)
        f_cred.addRow(self.lbl_pass, self.txt_pass)
        f_cred.addRow(self.lbl_appkey, self.txt_appkey)
        f_cred.addRow(self.lbl_appsecret, self.txt_appsecret)
        # Código de verificación del equipo (etiqueta del DVR / menú
        # Hik-Connect → Verification Code). Solo necesario si el equipo tiene
        # el cifrado de stream activo; si no, se deja vacío.
        self.lbl_vericode = QLabel("Cód. verificación:")
        self.txt_vericode = QLineEdit()
        self.txt_vericode.setPlaceholderText("Solo si el equipo cifra el vídeo")
        self.txt_vericode.setToolTip(
            "Código de verificación del equipo (6-12 letras o números,\n"
            "distingue mayúsculas). Aparece en la etiqueta del DVR y en su\n"
            "menú Hik-Connect / Guarding Vision → Verification Code.\n"
            "Déjalo vacío si el equipo no tiene el cifrado activado.")
        self.txt_vericode.returnPressed.connect(self._test_connection)

        f_cred.addRow(self.lbl_region, self.cb_region)
        f_cred.addRow(self.lbl_vericode, self.txt_vericode)
        f_cred.addRow("", self.btn_env_creds)
        root.addWidget(self.grp_cred)

        # SDK (solo visible para estrategias nativas)
        self.grp_sdk = QGroupBox("SDK nativo")
        f_sdk = QVBoxLayout(self.grp_sdk)
        f_sdk.setContentsMargins(12, 10, 12, 10); f_sdk.setSpacing(6)
        self.txt_sdk = QLineEdit()
        self.txt_sdk.setPlaceholderText("src/sdk/hikvision/HCNetSDK.dll")
        btn_browse = QPushButton("📂  Buscar DLL…")
        btn_browse.setObjectName("BtnQuick"); btn_browse.setFixedHeight(26)
        btn_browse.clicked.connect(self._browse_sdk)
        f_sdk.addWidget(self.txt_sdk)
        f_sdk.addWidget(btn_browse)
        self.grp_sdk.setVisible(False)
        root.addWidget(self.grp_sdk)

        # Probar conexión
        self.btn_test = QPushButton("🔌  Probar conexión")
        self.btn_test.setObjectName("BtnTest")
        self.btn_test.setMinimumHeight(36)
        self.btn_test.clicked.connect(self._test_connection)
        root.addWidget(self.btn_test)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("LogBox")
        self.log_box.setMaximumHeight(90)
        self.log_box.setVisible(False)
        root.addWidget(self.log_box)

        root.addStretch()

        btn_row = QHBoxLayout()
        self.btn_clear = QPushButton("Limpiar")
        self.btn_clear.setObjectName("BtnSecondary")
        self.btn_clear.clicked.connect(self._clear_form)

        # Botón logout (solo visible para Hik-Connect)
        self.btn_logout = QPushButton("🔓  Cerrar sesión")
        self.btn_logout.setObjectName("BtnSecondary")
        self.btn_logout.setVisible(False)
        self.btn_logout.clicked.connect(self._logout_account)

        self.btn_save = QPushButton("✅  Guardar dispositivo")
        self.btn_save.setObjectName("BtnSave")
        self.btn_save.setMinimumHeight(36)
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save_device)

        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_logout)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)
        # Inicializar visibilidad
        self._on_connection_type_changed(self.cb_connection_type.currentText())
        return w

    def _build_list(self) -> QWidget:
        w = QWidget()
        w.setObjectName("ListPanel")
        w.setAttribute(Qt.WA_StyledBackground, True)
        root = QVBoxLayout(w)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        title = QLabel("Dispositivos registrados")
        title.setObjectName("ListTitle")
        root.addWidget(title)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("FormSep"); root.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._list_inner  = QWidget()
        self._list_layout = QVBoxLayout(self._list_inner)
        self._list_layout.setSpacing(6)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_inner)
        root.addWidget(scroll)

        # Área de canales (inicialmente oculta)
        self._channels_group = QGroupBox("Canales disponibles")
        self._channels_group.setVisible(False)
        channels_layout = QVBoxLayout(self._channels_group)
        channels_layout.setContentsMargins(0, 0, 0, 0)
        channels_layout.setSpacing(2)
        
        self._channels_scroll = QScrollArea()
        self._channels_scroll.setWidgetResizable(True)
        self._channels_scroll.setFrameShape(QFrame.NoFrame)
        self._channels_scroll.setMaximumHeight(150)
        
        self._channels_inner = QWidget()
        self._channels_list_layout = QVBoxLayout(self._channels_inner)
        self._channels_list_layout.setContentsMargins(0, 0, 0, 0)
        self._channels_list_layout.setSpacing(4)
        self._channels_list_layout.addStretch()
        
        self._channels_scroll.setWidget(self._channels_inner)
        channels_layout.addWidget(self._channels_scroll)
        root.addWidget(self._channels_group)

        self._lbl_empty = QLabel(
            "Aún no hay dispositivos.\n"
            "Completa el formulario, prueba la conexión\n"
            "y guarda el dispositivo."
        )
        self._lbl_empty.setObjectName("EmptyLabel")
        self._lbl_empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self._lbl_empty)
        return w

    # ── Carga inicial ─────────────────────────────────────────

    def _load_devices(self):
        for dev in self._repo.all():
            self._add_row(dev)
        self._update_empty()

    def _add_row(self, dev: DVRDevice):
        row = _DeviceRow(dev)
        row.sig_edit.connect(self._load_for_edit)
        row.sig_delete.connect(self._delete_device)
        idx = self._list_layout.count() - 1
        self._list_layout.insertWidget(idx, row)
        self._rows[dev.id] = row

    # ── Eventos formulario ────────────────────────────────────

    def _on_connection_type_changed(self, conn_type: str):
        # Ambas nubes (Hik-Connect y EZVIZ) usan el mismo formulario:
        # AppKey + AppSecret + región, sin IP ni puerto.
        marca = self._MARCA_POR_TIPO.get(conn_type, "")
        is_hikconnect = bool(marca)
        es_ezviz = marca == "EZVIZ"
        # Mostrar/ocultar grupos
        self.grp_conn.setVisible(not is_hikconnect)
        self.grp_sdk.setVisible(not is_hikconnect)
        # Campos IP: usuario y contraseña
        self.lbl_user.setVisible(not is_hikconnect)
        self.txt_user.setVisible(not is_hikconnect)
        self.lbl_pass.setVisible(not is_hikconnect)
        self.txt_pass.setVisible(not is_hikconnect)
        # Campos Hik-Connect: App Key y App Secret
        self.lbl_appkey.setVisible(is_hikconnect)
        self.txt_appkey.setVisible(is_hikconnect)
        self.lbl_appsecret.setVisible(is_hikconnect)
        self.txt_appsecret.setVisible(is_hikconnect)
        self.lbl_region.setVisible(is_hikconnect)
        self.cb_region.setVisible(is_hikconnect)
        self.lbl_vericode.setVisible(is_hikconnect)
        self.txt_vericode.setVisible(is_hikconnect)
        self.btn_env_creds.setVisible(is_hikconnect)
        if is_hikconnect and not es_ezviz:
            # Comodidad: si los campos están vacíos, ofrecer las del .env.
            # No bloquea nada: se puede escribir cualquier otra cuenta.
            self._prefill_hik_env(forzar=False)
        # El descubrimiento es solo para equipos en la red local (no aplica a
        # la nube de Hik-Connect, que se resuelve con App Key/Secret).
        self.btn_discover.setVisible(not is_hikconnect)
        # Las credenciales del .env son las de HikCentral (empresa); para EZVIZ
        # el usuario trae las suyas de open.ezvizlife.com.
        self.btn_env_creds.setVisible(is_hikconnect and not es_ezviz)
        self._poblar_regiones(es_ezviz)
        if es_ezviz:
            titulo = "Iniciar sesión en EZVIZ / Hik-Connect"
        elif is_hikconnect:
            titulo = "Iniciar sesión desde Hik-Connect"
        else:
            titulo = "Agregar dispositivo DVR"
        self._form_title.setText(titulo)

    # ── Hik-Connect: credenciales y región ────────────────────

    def _prefill_hik_env(self, forzar: bool = False) -> None:
        """Vuelca las credenciales del .env en el formulario.

        `forzar=False` solo rellena los campos vacíos (comodidad al abrir);
        `forzar=True` sobrescribe (botón «Usar credenciales del .env»). En
        ningún caso limita: se puede escribir cualquier otra App Key/Secret.
        """
        import os
        clave = (os.getenv("hik_app_key") or "").strip()
        secreto = (os.getenv("hik_app_secret") or "").strip()
        if not clave and not secreto:
            if forzar:
                self._log("No hay hik_app_key / hik_app_secret en el .env.",
                          error=True)
                self.log_box.setVisible(True)
            return
        if clave and (forzar or not self.txt_appkey.text().strip()):
            self.txt_appkey.setText(clave)
        if secreto and (forzar or not self.txt_appsecret.text().strip()):
            self.txt_appsecret.setText(secreto)

    def _poblar_regiones(self, es_ezviz: bool) -> None:
        """Carga en el combo los servidores de la nube elegida.

        Las dos nubes tienen dominios distintos, así que la lista cambia según
        el tipo de conexión. En ambos casos se permite escribir uno propio.
        """
        actual = self.cb_region.currentText()
        if es_ezviz:
            from core.dvr.ezviz import REGIONES
            opciones = [self._REGION_AUTO] + [
                f"{base.replace('https://', '')}  ({etiqueta})"
                for base, etiqueta in REGIONES]
        else:
            opciones = [
                self._REGION_AUTO,
                "isa.hikcentralconnect.com  (Internacional / Asia)",
                "ius.hikcentralconnect.com  (América)",
                "ieu.hikcentralconnect.com  (Europa)",
                "isgp.hikcentralconnect.com  (Singapur)",
            ]
        if [self.cb_region.itemText(i)
                for i in range(self.cb_region.count())] == opciones:
            return                       # ya está poblado con lo correcto
        self.cb_region.blockSignals(True)
        self.cb_region.clear()
        self.cb_region.addItems(opciones)
        # Conservar la elección previa si sigue teniendo sentido.
        self.cb_region.setCurrentText(
            actual if actual in opciones else self._REGION_AUTO)
        self.cb_region.blockSignals(False)

    def _region_host(self) -> str:
        """Dominio elegido en el combo de región ('' = automático).

        Se acepta texto libre (cuentas OEM); se limpia el comentario entre
        paréntesis y el esquema http(s):// si el usuario lo pega entero.
        """
        texto = self.cb_region.currentText().strip()
        if not texto or texto == self._REGION_AUTO:
            return ""
        dominio = texto.split("(")[0].strip()
        return dominio.replace("https://", "").replace("http://", "").strip(" /")

    def _set_region(self, host: str) -> None:
        """Selecciona en el combo la región guardada de una cuenta."""
        if not host:
            self.cb_region.setCurrentText(self._REGION_AUTO)
            return
        for i in range(self.cb_region.count()):
            if self.cb_region.itemText(i).startswith(host):
                self.cb_region.setCurrentIndex(i)
                return
        self.cb_region.setCurrentText(host)

    # ── Descubrimiento en la red ──────────────────────────────

    def _discover_devices(self):
        """Abre el buscador de red y vuelca el equipo elegido en el formulario."""
        from .discovery_dialog import DiscoveryDialog
        dlg = DiscoveryDialog(self)
        if not dlg.exec() or dlg.seleccionado is None:
            return
        d = dlg.seleccionado
        self.cb_connection_type.setCurrentText(self._TIPO_IP)
        self.txt_host.setText(d.ip)
        self.txt_port.setText(str(d.puerto_ui))
        indice = self.cb_brand.findText(d.marca_ui)
        if indice != -1:
            self.cb_brand.setCurrentIndex(indice)
        if not self.txt_alias.text().strip():
            self.txt_alias.setText(d.modelo or d.nombre or f"Dispositivo {d.ip}")
        if not self.txt_user.text().strip():
            self.txt_user.setText("admin")
        self._log(f"✔ {d.descripcion} en {d.ip}:{d.puerto_ui} — "
                  "escribe la contraseña y pulsa «Probar conexión».")
        self.txt_pass.setFocus()

    def _on_brand_changed(self, brand: str):
        self.txt_port.setText(str(DVRContext.default_port(brand)))
        self.grp_sdk.setVisible(DVRContext.needs_sdk(brand))

    def _browse_sdk(self):
        brand = self.cb_brand.currentText()
        f = "HCNetSDK (HCNetSDK.dll)" if "Hikvision" in brand else "NetSDK (dhnetsdk.dll)"
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar DLL", "", f";;Todos (*)")
        if path:
            self.txt_sdk.setText(path)

    # ── Prueba de conexión ────────────────────────────────────

    def _test_connection(self):
        marca_nube = self._marca_nube()
        if marca_nube:
            appkey = self.txt_appkey.text().strip()
            appsecret = self.txt_appsecret.text().strip()
            if not appkey or not appsecret:
                self._log("⚠ Ingresa App Key y App Secret.", error=True)
                return
            brand = marca_nube
            # host = dominio de la región ('' = probar todas). HikConnectStrategy
            # lo usa como base preferente para pedir el token.
            host = self._region_host()
            port = 443
            username = appkey
            password = appsecret
            sdk_path = ""
        else:
            host = self.txt_host.text().strip()
            port = self.txt_port.text().strip()
            if not host or not port:
                self._log("⚠ Ingresa IP y puerto.", error=True)
                return
            brand = self.cb_brand.currentText()
            username = self.txt_user.text().strip()
            password = self.txt_pass.text()
            sdk_path = self.txt_sdk.text().strip()

        self._set_loading(True)
        self.log_box.setVisible(True)
        self.log_box.clear()
        self._log(f"Conectando a {brand}…")

        # `quit()` NO interrumpe un run() bloqueado en I/O de red. Si aqui
        # se soltase la referencia, el recolector destruiria un QThread VIVO
        # y **Qt aborta el proceso**: ese era el "se cierra solo al agregar un
        # DVR". Se desconectan sus senales y se conserva referenciado hasta
        # que termine por su cuenta.
        anterior, self._worker = self._worker, None
        if anterior is not None and anterior.isRunning():
            for senal in (anterior.success, anterior.error):
                try:
                    senal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            anterior.quit()
            if not anterior.wait(1500):
                self._huerfanos.append(anterior)
                anterior.finished.connect(
                    lambda w=anterior: self._retirar_huerfano(w))
            else:
                anterior.deleteLater()
        elif anterior is not None:
            anterior.deleteLater()

        self._worker = DVRConnectWorker(
            brand    = brand,
            host     = host,
            port     = port,
            username = username,
            password = password,
            sdk_path = sdk_path,
            verification_code = self.txt_vericode.text().strip(),
        )
        self._worker.success.connect(self._on_connect_ok)
        self._worker.error.connect(self._on_connect_err)
        self._worker.start()

    def _retirar_huerfano(self, worker) -> None:
        """Suelta un worker antiguo cuando por fin termina."""
        try:
            self._huerfanos.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def closeEvent(self, event):
        """Espera a los hilos vivos antes de cerrar el panel."""
        for worker in [self._worker] + list(getattr(self, "_huerfanos", [])):
            if worker is not None and worker.isRunning():
                worker.quit()
                worker.wait(2000)
        super().closeEvent(event)

    def _on_connect_ok(self, info: DeviceInfo):
        self._last_info = info
        self._set_loading(False)
        self._log(f"✅ {info.brand} {info.model}  ·  Serie: {info.serial_number}")
        self._log(f"   Firmware: {info.firmware_version}  ·  {info.num_video_channels} canales")
        for ch in info.channels:
            estado = ch.rtsp_main or ("🔒 cifrado" if ch.extra.get("stream_encrypt_enable")
                                      else "sin stream")
            self._log(f"   ▶ Canal {ch.id}: {ch.name}  —  {estado}")
        self.btn_save.setEnabled(True)
        self._pedir_codigo_si_hace_falta(info)

    def _pedir_codigo_si_hace_falta(self, info: DeviceInfo) -> None:
        """Si hay canales cifrados y aún no se dio el código, lo pide y reintenta.

        El equipo puede tener activado el cifrado de stream; en ese caso la nube
        no entrega vídeo sin el código de verificación (el de la etiqueta del
        DVR / menú Hik-Connect). Se pregunta UNA vez por intento de conexión.
        """
        if self.txt_vericode.text().strip():
            return                                   # ya se probó con código
        cifrados = [c for c in info.channels
                    if c.extra.get("stream_encrypt_enable") and not c.rtsp_main]
        if not cifrados:
            return
        nombres = ", ".join(c.name for c in cifrados[:4])
        if len(cifrados) > 4:
            nombres += f" y {len(cifrados) - 4} más"
        codigo, ok = QInputDialog.getText(
            self, "Código de verificación requerido",
            f"{len(cifrados)} canal(es) tienen el vídeo cifrado y no se pueden "
            f"ver sin el código del equipo:\n\n  {nombres}\n\n"
            "Escribe el código de verificación (6-12 letras o números, "
            "distingue mayúsculas).\nLo encuentras en la etiqueta del DVR o en "
            "su menú Hik-Connect / Guarding Vision → Verification Code.\n\n"
            "Si el equipo no tiene el cifrado activado, deja el campo vacío y "
            "cancela.",
            QLineEdit.Normal, "")
        codigo = (codigo or "").strip()
        if not ok or not codigo:
            self._log("Sin código: los canales cifrados quedarán sin vídeo.")
            return
        self.txt_vericode.setText(codigo)
        self._log(f"Reintentando con el código de verificación…")
        self._test_connection()

    def _on_connect_err(self, msg: str):
        self._last_info = None
        self._set_loading(False)
        self._log(msg, error=True)
        self.btn_save.setEnabled(False)

    def _set_loading(self, loading: bool):
        self.progress.setVisible(loading)
        self.btn_test.setEnabled(not loading)
        self.btn_test.setText("⏳ Probando…" if loading else "🔌  Probar conexión")

    def _log(self, msg: str, error: bool = False):
        color = "#ff6b6b" if error else "#58a6ff"
        self.log_box.append(f'<span style="color:{color}">{msg}</span>')

    # ── Guardar ───────────────────────────────────────────────

    def _save_device(self):
        marca_nube = self._marca_nube()
        alias = self.txt_alias.text().strip()
        if not alias:
            if marca_nube:
                # Varias cuentas pueden convivir: el alias debe distinguirlas.
                # Se usa el modelo detectado o, si no hay, el final del App Key.
                clave = self.txt_appkey.text().strip()
                detalle = (self._last_info.model if self._last_info
                           and self._last_info.model else "")
                if not detalle and clave:
                    detalle = f"···{clave[-6:]}"
                alias = f"{marca_nube} {detalle}".strip()
            else:
                alias = f"{self.txt_host.text().strip()}:{self.txt_port.text().strip()}"

        dev = self._repo.get(self._editing_id) if self._editing_id else DVRDevice()
        if dev is None:
            dev = DVRDevice(id=self._editing_id)

        if marca_nube:
            dev.brand    = marca_nube
            dev.host     = self._region_host()   # '' = región automática
            dev.port     = 443
            dev.verification_code = self.txt_vericode.text().strip()
            dev.username = self.txt_appkey.text().strip()
            dev.password = self.txt_appsecret.text()
            dev.sdk_path = ""
        else:
            dev.brand    = self.cb_brand.currentText()
            dev.host     = self.txt_host.text().strip()
            dev.port     = int(self.txt_port.text().strip() or "80")
            dev.username = self.txt_user.text().strip()
            dev.password = self.txt_pass.text()
            dev.sdk_path = self.txt_sdk.text().strip()

        dev.alias = alias

        if self._last_info:
            dev.num_channels = self._last_info.num_video_channels
            dev.channels     = [ch.to_dict() for ch in self._last_info.channels]

        if self._editing_id:
            self._repo.update(dev)
            row = self._rows.get(dev.id)
            if row:
                row.refresh(dev)
        else:
            self._repo.add(dev)
            self._add_row(dev)

        self._update_empty()
        self._clear_form()
        self.devices_updated.emit()

    # ── Editar / eliminar ─────────────────────────────────────

    def _load_for_edit(self, device_id: str):
        dev = self._repo.get(device_id)
        if not dev:
            return
        self._editing_id = device_id
        self._last_info  = None
        self.txt_alias.setText(dev.alias)
        
        # Mostrar/cargar canales
        self._show_device_channels(dev)
        
        if dev.brand in ("Hik-Connect", "EZVIZ"):
            self.cb_connection_type.setCurrentText(
                self._TIPO_EZ if dev.brand == "EZVIZ" else self._TIPO_HC)
            self.txt_appkey.setText(dev.username)
            self.txt_appsecret.setText(dev.password)
            self.txt_vericode.setText(getattr(dev, "verification_code", ""))
            self._set_region(dev.host)     # región guardada de ESTA cuenta
            self.btn_logout.setVisible(True)
            self._form_title.setText(f"Sesión {dev.brand}")
        else:
            self.cb_connection_type.setCurrentText(self._TIPO_IP)
            idx = self.cb_brand.findText(dev.brand)
            if idx >= 0:
                self.cb_brand.setCurrentIndex(idx)
            self.txt_host.setText(dev.host)
            self.txt_port.setText(str(dev.port))
            self.txt_user.setText(dev.username)
            self.txt_pass.setText(dev.password)
            self.txt_sdk.setText(dev.sdk_path)
            self.btn_logout.setVisible(False)
            self._form_title.setText("Editar dispositivo")
        self.btn_save.setEnabled(True)
        self.log_box.setVisible(False)

    def _show_device_channels(self, dev: DVRDevice):
        """Muestra los canales del dispositivo en la lista."""
        # Limpiar canales anteriores
        while self._channels_list_layout.count() > 1:  # > 1 porque el último es stretch
            item = self._channels_list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if not dev.channels:
            self._channels_group.setVisible(False)
            return

        self._channels_group.setVisible(True)
        
        for ch in dev.channels:
            ch_dict = ch if isinstance(ch, dict) else (ch.to_dict() if hasattr(ch, 'to_dict') else {})
            if not ch_dict:
                continue
            
            # Preparar datos del canal para drag&drop
            if dev.brand == "Hik-Connect":
                channel_data = HikConnectChannelEncoder.encode_channel(
                    device_alias          = dev.alias or "Cuenta HC",
                    channel_name          = ch_dict.get("name", ""),
                    rtsp_main             = ch_dict.get("rtsp_main", ""),
                    rtsp_sub              = ch_dict.get("rtsp_sub", ""),
                    channel_id            = ch_dict.get("id", ""),
                    resource_id           = ch_dict.get("resource_id", ch_dict.get("id", "")),
                    device_serial         = ch_dict.get("device_serial", "") or dev.username,
                    stream_encrypt_enable = ch_dict.get("stream_encrypt_enable", False),
                )
            else:
                channel_data = {
                    "device_alias":   dev.alias or dev.host,
                    "channel_name":   ch_dict.get("name", ""),
                    "rtsp_main":      ch_dict.get("rtsp_main", ""),
                    "rtsp_sub":       ch_dict.get("rtsp_sub", ""),
                    "is_hikconnect":  False,
                }
            
            row = ChannelRow(dev.id, channel_data)
            self._channels_list_layout.insertWidget(
                self._channels_list_layout.count() - 1, row  # Antes del stretch
            )

    def _delete_device(self, device_id: str):
        dev = self._repo.get(device_id)
        if not dev:
            return
        reply = QMessageBox.question(
            self, "Eliminar dispositivo",
            f"¿Eliminar '{dev.display_label()}'?\nEsta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._repo.remove(device_id)
        row = self._rows.pop(device_id, None)
        if row:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._update_empty()
        self.devices_updated.emit()

    def _logout_account(self):
        """Cierra sesión de una cuenta Hik-Connect."""
        if not self._editing_id:
            return
        dev = self._repo.get(self._editing_id)
        if not dev or dev.brand != "Hik-Connect":
            return
        
        reply = QMessageBox.question(
            self, "Cerrar sesión Hik-Connect",
            f"¿Desconectar '{dev.display_label()}'?\n"
            "Se eliminarán todos los canales asociados.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        
        # Eliminar la cuenta
        self._delete_device(self._editing_id)
        self._clear_form()
        self._log("✅ Sesión cerrada correctamente", error=False)

    def _clear_form(self):
        self._editing_id = None
        self._last_info  = None
        self.cb_connection_type.setCurrentText(self._TIPO_IP)
        self.txt_alias.clear()
        self.txt_host.clear()
        self.txt_port.setText("80")
        self.txt_user.setText("admin")
        self.txt_pass.clear()
        self.txt_appkey.clear()
        self.txt_appsecret.clear()
        self.txt_vericode.clear()
        self.cb_region.setCurrentText(self._REGION_AUTO)
        self.txt_sdk.clear()
        self.cb_brand.setCurrentIndex(0)
        self.btn_save.setEnabled(False)
        self.btn_logout.setVisible(False)
        self._channels_group.setVisible(False)
        self.log_box.setVisible(False)
        self.log_box.clear()
        self._form_title.setText("Agregar dispositivo DVR")

    def _update_empty(self):
        self._lbl_empty.setVisible(len(self._rows) == 0)

    def get_repo(self) -> DVRRepository:
        return self._repo

    # ── Métodos públicos para los canales ──────────────────────

    def get_device_channels(self, device_id: str) -> list[dict]:
        """
        Retorna lista de canales del dispositivo en formato drag&drop.
        Para Hik-Connect, codifica con metadatos de encriptación.
        """
        dev = self._repo.get(device_id)
        if not dev:
            return []
        
        channels = []
        for ch in dev.channels:
            if isinstance(ch, dict):
                ch_dict = ch
            else:
                ch_dict = ch.to_dict() if hasattr(ch, 'to_dict') else {}
            
            # Si es Hik-Connect, codificar apropiadamente
            if dev.brand == "Hik-Connect":
                encoded = HikConnectChannelEncoder.encode_channel(
                    device_alias          = dev.alias or "Cuenta HC",
                    channel_name          = ch_dict.get("name", f"Canal {len(channels)+1}"),
                    rtsp_main             = ch_dict.get("rtsp_main", ""),
                    rtsp_sub              = ch_dict.get("rtsp_sub", ""),
                    channel_id            = ch_dict.get("id", ""),
                    resource_id           = ch_dict.get("resource_id", ch_dict.get("id", "")),
                    device_serial         = ch_dict.get("device_serial", "") or dev.username,
                    stream_encrypt_enable = ch_dict.get("stream_encrypt_enable", False),
                )
                channels.append(encoded)
            else:
                # Para IP, usar directamente
                channels.append({
                    "device_alias":   dev.alias or dev.host,
                    "channel_name":   ch_dict.get("name", ""),
                    "rtsp_main":      ch_dict.get("rtsp_main", ""),
                    "rtsp_sub":       ch_dict.get("rtsp_sub", ""),
                    "is_hikconnect":  False,
                })
        return channels

    def logout_hikconnect(self, device_id: str) -> bool:
        """
        Elimina una cuenta Hik-Connect y todos sus canales asociados.
        """
        dev = self._repo.get(device_id)
        if dev and dev.brand == "Hik-Connect":
            self._delete_device(device_id)
            return True
        return False

    # ── Estilos ───────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget { background:#0d1117; color:#c9d1d9;
                font-family:'Segoe UI',sans-serif; font-size:13px; }
            #FormPanel  { background:#0d1117; border-right:1px solid #21262d; }
            #ListPanel  { background:#0d1117; }
            #FormTitle, #ListTitle { font-size:15px; font-weight:700; color:#e6edf3; }
            #FormSep    { color:#21262d; }
            QGroupBox   { border:1px solid #30363d; border-radius:6px; margin-top:8px;
                color:#8b949e; font-size:11px; font-weight:600; }
            QGroupBox::title { subcontrol-origin:margin; left:10px;
                background:#0d1117; padding:0 4px; }
            QLineEdit, QComboBox { background:#161b22; border:1px solid #30363d;
                border-radius:5px; padding:6px 8px; color:#e6edf3; }
            QLineEdit:focus, QComboBox:focus { border-color:#58a6ff; }
            QComboBox QAbstractItemView { background:#161b22; color:#c9d1d9;
                selection-background-color:#21262d; }
            QLabel { color:#8b949e; font-size:12px; }
            #BtnTest { background:#1f2d3d; color:#58a6ff; border:1px solid #1f6feb;
                border-radius:6px; font-size:13px; font-weight:600; }
            #BtnTest:hover { background:#1f6feb; color:#fff; }
            #BtnTest:disabled { background:#161b22; color:#6e7681; border-color:#30363d; }
            #BtnSave { background:#238636; color:#fff; border:none;
                border-radius:6px; font-size:13px; font-weight:600; }
            #BtnSave:hover { background:#2ea043; }
            #BtnSave:disabled { background:#161b22; color:#6e7681; }
            #BtnSecondary { background:#21262d; color:#8b949e; border:1px solid #30363d;
                border-radius:6px; padding:6px 14px; }
            #BtnQuick { background:#161b22; color:#6e7681; border:1px solid #30363d;
                border-radius:4px; font-size:11px; }
            #BtnQuick:hover { background:#21262d; color:#c9d1d9; }
            #LogBox { background:#0a0e13; border:1px solid #21262d; border-radius:5px;
                color:#c9d1d9; font-family:'Courier New',monospace; font-size:11px; }
            QProgressBar { border:none; background:#21262d; border-radius:2px; }
            QProgressBar::chunk { background:#1f6feb; border-radius:2px; }
            #DeviceRow { background:#161b22; border:1px solid #21262d; border-radius:7px; }
            #DeviceRow:hover { border-color:#30363d; }
            #RowDot  { color:#3fb950; font-size:13px; }
            #RowName { font-size:13px; font-weight:600; color:#e6edf3; }
            #RowSub  { font-size:11px; color:#6e7681; }
            #BtnRowIcon { background:#21262d; border:1px solid #30363d;
                border-radius:4px; font-size:12px; color:#8b949e; }
            #BtnRowIcon:hover { background:#30363d; color:#c9d1d9; }
            #BtnRowDel { background:#21262d; border:1px solid #30363d;
                border-radius:4px; font-size:12px; color:#8b949e; }
            #BtnRowDel:hover { background:#2d1212; color:#ff6b6b; border-color:#5a1a1a; }
            #EmptyLabel { color:#6e7681; font-size:13px; padding:30px; }
            QScrollArea { border:none; background:transparent; }
            QScrollBar:vertical { background:#0d1117; width:6px; border-radius:3px; }
            QScrollBar::handle:vertical { background:#30363d; border-radius:3px; }
            QSplitter::handle { background:#21262d; }
        """)
