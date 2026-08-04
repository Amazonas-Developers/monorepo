import os
import sys
from pathlib import Path


def _asegurar_venv() -> None:
    """Si se lanzo con OTRO Python (p. ej. el global de Program Files), se
    re-lanza con el del venv. GOTCHA real (paso el 4-ago-2026): `python
    main.py` con el global levanta un servidor A MEDIAS que ocupa el 9000
    sin el stack completo — los clientes lo ven "offline" y el servidor
    bueno no puede arrancar. iniciar_servidor_headless.py ya tenia este
    guardian; main.py (la via con GUI) era el hueco por donde entraba."""
    base = Path(__file__).resolve().parent
    if os.name == "nt":
        venv_py = base / "venv" / "Scripts" / "python.exe"
    else:
        venv_py = base / "venv" / "bin" / "python"
    actual = Path(sys.executable).resolve()
    if venv_py.exists() and actual != venv_py.resolve():
        print(f"[AVISO] lanzado con {actual}; re-lanzando con el venv: {venv_py}")
        os.execv(str(venv_py), [str(venv_py)] + sys.argv)


_asegurar_venv()

# SERVER
from src.app.server import Server_services

#
# CORE 
from src.gui.q_application import AppSingletonGui

# GUI
from src.gui.window_main import Main_Window




def main():
    try:
        server = Server_services(port=9000)
        
        app = AppSingletonGui.initialize()
        main_window = Main_Window()
        
        main_window.btn_server_init.clicked.connect(server.start)
        main_window.btn_server_stop.clicked.connect(server.stop)
        
        main_window.show()
        app.exec()
    except Exception as e:
        print(e)



if __name__ == '__main__':
    sys.exit(main())