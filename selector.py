"""
selector.py - Selector de sistemas ELDE (hub de arranque).

Ventana unica que, al abrir, muestra los sistemas disponibles y lanza el
que elijas. NO acopla el codigo de ninguno: solo INVOCA el arranque propio
de cada sistema (su .bat o su python), como procesos independientes. Cerrar
el selector no cierra lo que ya lanzaste.

Se ejecuta con el Python GLOBAL (que ya tiene PySide6), asi el selector no
depende del venv de ningun 'view'.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Servidor de inferencia ───────────────────────────────────────────
# El servidor headless (SERVER-IA PERIMETRALES) escucha en el puerto 9000 y
# sirve a los clientes ELDE (Tienda, Gestor, Perimetrales). Solo el servidor
# LOCAL (el que corre en esta maquina) se puede arrancar desde aqui.
SERVER_DIR = os.path.join(ROOT, "SERVER-IA PERIMETRALES")
SERVER_PY = os.path.join(SERVER_DIR, "venv", "Scripts", "python.exe")
SERVER_ENTRY = "iniciar_servidor_headless.py"
SERVER_PORT = 9000

# ── Servidores a los que se puede conectar el cliente ────────────────
# El .171 corre en ESTA maquina (local=True -> se puede arrancar aqui).
# El .141 es remoto (local=False -> solo conectar; no se arranca aqui).
# Al lanzar un cliente se le pasa server_ws_url = ws://<host>:9000/ws, que
# todos los clientes leen del entorno (con fallback a .171).
SERVERS = [
    {"label": "Este equipo — 72.68.60.171 (local)", "host": "72.68.60.171",
     "local": True},
    {"label": "Remoto — 72.68.60.141", "host": "72.68.60.141",
     "local": False},
]


def _ws_url(host: str) -> str:
    return f"ws://{host}:{SERVER_PORT}/ws"


def _port_up(port: int, host: str = "127.0.0.1") -> bool:
    """True si algo escucha en host:port (servidor ya en linea)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def _server_reachable(srv: dict) -> bool:
    """El servidor elegido esta escuchando? Para el local se prueba por
    loopback (mas fiable desde esta misma maquina) ademas de su IP."""
    if srv.get("local"):
        return (_port_up(SERVER_PORT, "127.0.0.1")
                or _port_up(SERVER_PORT, srv["host"]))
    return _port_up(SERVER_PORT, srv["host"])


def _start_server() -> str:
    """Arranca el servidor headless compartido en su propia consola.
    Devuelve 'ok' o un mensaje de error (no lanza)."""
    if not os.path.exists(SERVER_PY):
        return f"No se encontro el venv del servidor: {SERVER_PY}"
    if not os.path.exists(os.path.join(SERVER_DIR, SERVER_ENTRY)):
        return f"No se encontro {SERVER_ENTRY} en {SERVER_DIR}"
    try:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        subprocess.Popen(
            [SERVER_PY, SERVER_ENTRY, str(SERVER_PORT)],
            cwd=SERVER_DIR, env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"Error al arrancar el servidor: {e}"


def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


# ── Catalogo de sistemas ─────────────────────────────────────────────
# Cada uno define COMO se arranca de forma independiente:
#   - 'bat' : ruta a un .bat propio (se abre en su ventana; auto-eleva si
#             el .bat lo hace). Preferido.
#   - si no hay .bat: se usa el venv del sistema (o el python global) sobre
#             su src/main.py.
# Editar esta lista es todo lo que hace falta para agregar/quitar sistemas.
def _sistema(nombre, emoji, desc, carpeta, color, bat=None, needs_server=False):
    d = os.path.join(ROOT, carpeta)
    bat_path = os.path.join(d, bat) if bat else None
    return {
        "nombre": nombre, "emoji": emoji, "desc": desc, "color": color,
        "carpeta": d, "bat": bat_path, "needs_server": needs_server,
        "venv_py": os.path.join(d, "venv", "Scripts", "python.exe"),
        "entry": os.path.join(d, "src", "main.py"),
    }


SISTEMAS = [
    # TIENDA: sistema completo (servidor + dashboard + cliente) via el
    # lanzador maestro de la raiz ELDE.
    # Tienda: su propio .bat ya levanta servidor+dashboard+cliente, por eso
    # needs_server=False (no hay que arrancar el servidor por separado).
    {"nombre": "Tienda (completo)", "emoji": "🛒", "color": "#00c8ff",
     "desc": "Analitica de supermercado: servidor + dashboard + cliente.",
     "carpeta": ROOT, "bat": os.path.join(ROOT, "INICIAR_TIENDA.bat"),
     "needs_server": False,
     "venv_py": os.path.join(ROOT, "tienda_view", "venv", "Scripts",
                             "python.exe"),
     "entry": os.path.join(ROOT, "tienda_view", "src", "main.py")},

    _sistema("Gestor de ventanas", "🖥️",
             "Cliente oficial (windows_managers_view). Arranca el servidor "
             "compartido si hace falta.",
             "windows_managers_view", "#2ecc71",
             bat="INICIAR_CLIENTE.bat", needs_server=True),

    _sistema("Perimetrales", "🛡️",
             "Cliente de vigilancia perimetral. Arranca el servidor "
             "compartido si hace falta.",
             "perimetrales-view", "#e67e22",
             bat="INICIAR_CLIENTE.bat", needs_server=True),

    # Amazonas View: proyecto aparte con su propio backend/jarvis; no usa el
    # servidor compartido -> needs_server=False.
    _sistema("Amazonas View", "📹",
             "Cliente Amazonas View (proyecto aparte, backend propio).",
             "Amazonas View", "#9b59b6"),
]


def _disponible(s) -> bool:
    """Un sistema esta disponible si tiene un .bat, o un entrypoint con
    algun python (su venv o el global)."""
    if s.get("bat") and os.path.exists(s["bat"]):
        return True
    return os.path.exists(s.get("entry", ""))


def _lanzar(s, env_extra=None) -> str:
    """Arranca el sistema en un proceso INDEPENDIENTE. env_extra inyecta
    variables (p.ej. server_ws_url con el servidor elegido). Devuelve un
    texto de estado. Nunca lanza (captura y reporta)."""
    try:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        if env_extra:
            env.update(env_extra)
        if s.get("bat") and os.path.exists(s["bat"]):
            # Abrir el .bat en su propia ventana (permite su UAC/consola).
            # El entorno (server_ws_url) se hereda: cmd -> start -> bat -> python.
            subprocess.Popen(
                ["cmd", "/c", "start", "", os.path.basename(s["bat"])],
                cwd=os.path.dirname(s["bat"]), env=env)
            return f"Iniciando «{s['nombre']}» (via {os.path.basename(s['bat'])})…"
        # Sin .bat: correr src/main.py con el venv del sistema o el global.
        entry = s.get("entry")
        if not entry or not os.path.exists(entry):
            return f"No se encontro como arrancar «{s['nombre']}»."
        py = s["venv_py"] if os.path.exists(s.get("venv_py", "")) else sys.executable
        subprocess.Popen([py, "src\\main.py"], cwd=s["carpeta"], env=env)
        avisa = "" if os.path.exists(s.get("venv_py", "")) else \
            " (sin venv propio: usando Python global)"
        return f"Iniciando «{s['nombre']}»{avisa}…"
    except Exception as e:  # noqa: BLE001
        return f"Error al iniciar «{s['nombre']}»: {e}"


class Selector(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ELDE — Selector de sistemas")
        self.resize(760, 460)
        self.setStyleSheet("""
            QWidget{background:#0f1115;color:#e6e9ef;
                    font-family:'Segoe UI',sans-serif}
            QFrame#card{background:#1a1d24;border:1px solid #2a2f3a;
                        border-radius:12px}
            QLabel#emoji{font-size:40px}
            QLabel#nombre{font-size:16px;font-weight:800}
            QLabel#desc{color:#8b93a3;font-size:12px}
            QPushButton#go{background:#00a8e8;color:#fff;font-weight:800;
                        border:none;border-radius:7px;padding:8px 0}
            QPushButton#go:hover{background:#33bff0}
            QPushButton#go:disabled{background:#3a3f4a;color:#888}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(4)

        title = QLabel("ELDE · Selector de sistemas")
        title.setStyleSheet("font-size:20px;font-weight:800")
        root.addWidget(title)
        sub = QLabel("Elige el servidor y que sistema abrir. Cada sistema "
                     "arranca por separado (no estan ligados).")
        sub.setStyleSheet("color:#8b93a3;font-size:12px")
        root.addWidget(sub)

        # ── Selector de servidor ─────────────────────────────────────
        srv_row = QHBoxLayout()
        srv_row.setContentsMargins(0, 10, 0, 0)
        srv_lbl = QLabel("🖧  Servidor:")
        srv_lbl.setStyleSheet("font-size:13px;font-weight:800")
        self.cbo_server = QComboBox()
        for sv in SERVERS:
            self.cbo_server.addItem(sv["label"])
        self.cbo_server.setCursor(Qt.PointingHandCursor)
        self.cbo_server.setStyleSheet(
            "QComboBox{background:#1a1d24;color:#e6e9ef;border:1px solid "
            "#2a2f3a;border-radius:6px;padding:5px 10px;font-size:13px}"
            "QComboBox QAbstractItemView{background:#1a1d24;color:#e6e9ef;"
            "selection-background-color:#00a8e8}")
        srv_row.addWidget(srv_lbl)
        srv_row.addWidget(self.cbo_server, 1)
        root.addLayout(srv_row)

        grid = QGridLayout()
        grid.setContentsMargins(0, 14, 0, 8)
        grid.setSpacing(14)
        cols = 2
        for i, s in enumerate(SISTEMAS):
            grid.addWidget(self._card(s), i // cols, i % cols)
        root.addLayout(grid, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#2ecc71;font-size:12px")
        root.addWidget(self.status)

    def _card(self, s) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        em = QLabel(s["emoji"])
        em.setObjectName("emoji")
        top.addWidget(em)
        col = QVBoxLayout()
        col.setSpacing(2)
        nom = QLabel(s["nombre"])
        nom.setObjectName("nombre")
        nom.setStyleSheet(f"font-size:16px;font-weight:800;color:{s['color']}")
        col.addWidget(nom)
        desc = QLabel(s["desc"])
        desc.setObjectName("desc")
        desc.setWordWrap(True)
        col.addWidget(desc)
        top.addLayout(col, 1)
        lay.addLayout(top)

        disponible = _disponible(s)
        btn = QPushButton("▶  Iniciar" if disponible else "No disponible")
        btn.setObjectName("go")
        btn.setEnabled(disponible)
        if disponible:
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, sis=s: self._on_go(sis))
        else:
            btn.setToolTip(f"No se encontro el arranque en:\n{s['carpeta']}")
        lay.addWidget(btn)
        return card

    def _set_status(self, text, color="#2ecc71"):
        self.status.setStyleSheet(f"color:{color};font-size:12px")
        self.status.setText(text)

    def _current_server(self) -> dict:
        idx = max(0, self.cbo_server.currentIndex())
        return SERVERS[idx]

    def _es_tienda(self, s) -> bool:
        bat = s.get("bat") or ""
        return os.path.basename(bat).upper().startswith("INICIAR_TIENDA")

    def _on_go(self, s):
        srv = self._current_server()
        # Todos los clientes leen server_ws_url del entorno (fallback .171).
        env_extra = {"server_ws_url": _ws_url(srv["host"])}

        # Sistemas que NO usan el servidor compartido de forma gestionada:
        #  - Tienda: su .bat ya levanta su servidor local; el cliente usa la
        #    URL elegida igual.
        #  - Amazonas View: backend propio (la seleccion no le aplica).
        if not s.get("needs_server"):
            nota = ""
            if self._es_tienda(s) and not srv["local"]:
                nota = ("  (Nota: Tienda completo tambien arranca su propio "
                        "servidor local)")
            self._set_status(_lanzar(s, env_extra) + nota)
            return

        # Cliente que necesita servidor: usar el servidor elegido.
        if _server_reachable(srv):
            self._set_status(
                f"Servidor {srv['host']} en linea. " + _lanzar(s, env_extra))
            return

        # No responde. Si es REMOTO no se puede arrancar desde aqui.
        if not srv["local"]:
            self._set_status(
                f"El servidor remoto {srv['host']} no responde; abriendo el "
                "cliente igual (se reconectara solo). " + _lanzar(s, env_extra),
                "#f1c40f")
            return

        # Es LOCAL y no esta arriba: arrancarlo aqui una sola vez.
        r = _start_server()
        if r != "ok":
            self._set_status(r + "  Abriendo el cliente igual: "
                             + _lanzar(s, env_extra), "#e74c3c")
            return

        self._pending = s
        self._pending_env = env_extra
        self._pending_srv = srv
        self._elapsed = 0
        self._set_status(
            f"Servidor local {srv['host']} no detectado -> arrancandolo "
            "(carga modelos, ~1-3 min). El cliente se abrira al estar listo…",
            "#f1c40f")
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_server)
        self._timer.start(2000)

    def _poll_server(self):
        self._elapsed += 2
        if _server_reachable(self._pending_srv):
            self._timer.stop()
            self._set_status("Servidor listo. "
                             + _lanzar(self._pending, self._pending_env))
        elif self._elapsed >= 200:
            self._timer.stop()
            self._set_status(
                "El servidor tarda mas de lo normal; abriendo el cliente igual "
                "(se reconectara solo). "
                + _lanzar(self._pending, self._pending_env), "#f1c40f")
        else:
            self._set_status(
                f"Esperando al servidor… ({self._elapsed}s, hasta ~200s)",
                "#f1c40f")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    w = Selector()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
