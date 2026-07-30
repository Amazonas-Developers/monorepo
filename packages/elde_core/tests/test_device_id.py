"""
Pruebas del identificador estable de camara (H-11).

Verifican la propiedad que da sentido a todo el historico por zona: **el mismo
dispositivo fisico produce el mismo `device_id` en ejecuciones distintas**.
Antes de H-11 el valor era `uuid.uuid4()` por panel, asi que esta propiedad no
se cumplia nunca y los heatmaps se fragmentaban en un UUID por sesion.

No instancian el widget: `_device_id` y `_slug` solo leen atributos planos, asi
que se invocan sobre un objeto simulado. Asi la prueba corre sin Qt, sin
pantalla y sin servidor.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
RENDER_BOX = (RAIZ / "tienda_view" / "src" / "gui" / "components" /
              "render_box" / "render_box.py")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from elde_core.contracts import ClientType, Envelope, EventType  # noqa: E402


class PanelSimulado:
    """Lo minimo que `_device_id` necesita leer."""

    def __init__(self, serial="", canal="", titulo="", indice=0):
        self._dvr_device_serial = serial
        self._dvr_channel_id = canal
        self.title = titulo
        self.index = indice


def _cargar_funciones():
    """Extrae `_slug` y `_device_id` del archivo sin importar PySide6.

    Importar `render_box` de verdad arrastraria Qt, OpenCV y el resto del
    cliente. Aqui solo interesan dos metodos que no dependen de nada de eso,
    asi que se compila unicamente su codigo fuente."""
    fuente = RENDER_BOX.read_text(encoding='utf-8')
    trozos, dentro, indent = [], False, 0
    for linea in fuente.splitlines():
        despojada = linea.strip()
        if despojada.startswith(('def _slug(', 'def _device_id(')):
            dentro, indent = True, len(linea) - len(linea.lstrip())
            trozos.append(linea[indent:])
            continue
        if dentro:
            if despojada and (len(linea) - len(linea.lstrip())) <= indent:
                dentro = False
            else:
                trozos.append(linea[indent:] if linea.strip() else '')
    codigo = '\n'.join(trozos)
    ambito: dict = {}
    exec(compile(codigo, str(RENDER_BOX), 'exec'), ambito)
    return ambito['_slug'], ambito['_device_id']


_slug, _device_id = _cargar_funciones()

# `_device_id` invoca `self._slug(...)`, asi que el panel simulado tiene que
# ofrecerlo igual que la clase real (donde es un @staticmethod).
PanelSimulado._slug = staticmethod(_slug)


def test_el_mismo_canal_dvr_da_el_mismo_id():
    """LA propiedad de H-11: estable entre 'reinicios' de la aplicacion."""
    sesion1 = PanelSimulado(serial="J12345678", canal="2", indice=0)
    sesion2 = PanelSimulado(serial="J12345678", canal="2", indice=3)
    assert _device_id(sesion1) == _device_id(sesion2)
    assert _device_id(sesion1) == "dvr-J12345678-2"


def test_canales_distintos_dan_ids_distintos():
    a = PanelSimulado(serial="J12345678", canal="1")
    b = PanelSimulado(serial="J12345678", canal="2")
    c = PanelSimulado(serial="OTRO9999", canal="1")
    assert len({_device_id(a), _device_id(b), _device_id(c)}) == 3


def test_cae_al_titulo_de_la_ventana():
    p = PanelSimulado(titulo="iVMS-4200")
    assert _device_id(p) == "win-iVMS-4200"


def test_ultimo_recurso_por_posicion():
    assert _device_id(PanelSimulado(indice=0)) == "box-1"
    assert _device_id(PanelSimulado(indice=4)) == "box-5"


def test_el_dvr_manda_sobre_el_titulo():
    """Si hay canal DVR, es la identidad mas fiable y gana."""
    p = PanelSimulado(serial="J1", canal="3", titulo="iVMS-4200", indice=7)
    assert _device_id(p) == "dvr-J1-3"


def test_el_id_es_valido_como_nombre_de_archivo():
    """Los device_id acaban siendo `output/heatmap/<id>.png`."""
    sucio = PanelSimulado(titulo="C:\\ruta\\../etc passwd?*<>")
    ident = _device_id(sucio)
    for prohibido in '\\/:*?"<>| ':
        if prohibido == ':':
            continue          # los dos puntos si son validos en el contrato
        assert prohibido not in ident, f"{prohibido!r} en {ident!r}"
    assert '..' not in ident


def test_el_id_pasa_la_validacion_del_envelope():
    """Cierra el circulo: lo que genera el cliente lo acepta el contrato."""
    for p in (PanelSimulado(serial="J12345678", canal="2"),
              PanelSimulado(titulo="Camara del pasillo 3"),
              PanelSimulado(indice=2),
              PanelSimulado(titulo="C:\\ruta\\rara ../x")):
        env = Envelope(client_type=ClientType.TIENDA, site_id="lacomarca",
                       device_id=_device_id(p),
                       event_type=EventType.FRAME_INFERENCE)
        assert env.device_id == _device_id(p)


def test_slug_no_devuelve_cadena_vacia():
    assert _slug("") == "sin_nombre"
    assert _slug("///") == "sin_nombre"


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK    {nombre}")
            except Exception as exc:
                fallos += 1
                print(f"  FALLA {nombre}: {exc}")
    print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
    raise SystemExit(1 if fallos else 0)
