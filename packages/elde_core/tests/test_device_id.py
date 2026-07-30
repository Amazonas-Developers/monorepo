"""
Pruebas del identificador estable de camara (H-11).

Verifican la propiedad que da sentido a todo el historico por zona: **el mismo
dispositivo fisico produce el mismo `device_id` en ejecuciones distintas**.
Antes de H-11 el valor era `uuid.uuid4()` por panel, asi que esta propiedad no
se cumplia nunca y los heatmaps se fragmentaban en un UUID por sesion.

Hasta el 30-jul-2026 estas pruebas extraian las funciones del `render_box.py`
de tienda leyendo su codigo fuente, porque solo ese cliente tenia el arreglo.
Ahora la logica esta en el nucleo y la comparten los cuatro, asi que se prueba
donde vive. Las dos ultimas pruebas son las que vigilan que siga siendo asi.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
CLIENTES = ('tienda', 'perimetrales', 'managers', 'amazonas')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from elde_core.contracts import ClientType, Envelope, EventType  # noqa: E402
from elde_core.ui.identidad_camara import (device_id, nombre_visible,  # noqa: E402
                                           slug)


def _render_box(cliente: str) -> Path:
    return (RAIZ / 'clients' / cliente / 'src' / 'gui' / 'components' /
            'render_box' / 'render_box.py')


def test_el_mismo_canal_dvr_da_el_mismo_id():
    """LA propiedad de H-11: estable entre 'reinicios' de la aplicacion."""
    uno = device_id(serie_dvr='J12345678', canal_dvr='2', indice=0)
    dos = device_id(serie_dvr='J12345678', canal_dvr='2', indice=3)
    assert uno == dos
    assert uno == 'dvr-J12345678-2'


def test_canales_distintos_dan_ids_distintos():
    ids = {device_id(serie_dvr='J12345678', canal_dvr='1'),
           device_id(serie_dvr='J12345678', canal_dvr='2'),
           device_id(serie_dvr='OTRO9999', canal_dvr='1')}
    assert len(ids) == 3


def test_cae_al_titulo_de_la_ventana():
    assert device_id(titulo_ventana='iVMS-4200') == 'win-iVMS-4200'


def test_ultimo_recurso_por_posicion():
    assert device_id(indice=0) == 'box-1'
    assert device_id(indice=4) == 'box-5'


def test_el_dvr_manda_sobre_el_titulo():
    """Si hay canal DVR, es la identidad mas fiable y gana."""
    assert device_id(serie_dvr='J1', canal_dvr='3',
                     titulo_ventana='iVMS-4200', indice=7) == 'dvr-J1-3'


def test_el_id_es_valido_como_nombre_de_archivo():
    """Los device_id acaban siendo `output/heatmap/<id>.png`."""
    ident = device_id(titulo_ventana='C:\\ruta\\../etc passwd?*<>')
    for prohibido in '\\/:*?"<>| ':
        if prohibido == ':':
            continue          # los dos puntos si son validos en el contrato
        assert prohibido not in ident, f'{prohibido!r} en {ident!r}'
    assert '..' not in ident


def test_el_id_pasa_la_validacion_del_envelope():
    """Cierra el circulo: lo que genera el cliente lo acepta el contrato."""
    for ident in (device_id(serie_dvr='J12345678', canal_dvr='2'),
                  device_id(titulo_ventana='Camara del pasillo 3'),
                  device_id(indice=2),
                  device_id(titulo_ventana='C:\\ruta\\rara ../x')):
        env = Envelope(client_type=ClientType.TIENDA, site_id='lacomarca',
                       device_id=ident, event_type=EventType.FRAME_INFERENCE)
        assert env.device_id == ident


def test_slug_no_devuelve_cadena_vacia():
    assert slug('') == 'sin_nombre'
    assert slug('///') == 'sin_nombre'


def test_nombre_visible_sigue_el_mismo_orden_de_fuentes():
    assert nombre_visible(nombre_dvr='Pasillo 3', titulo_ventana='x') == 'Pasillo 3'
    assert nombre_visible(titulo_ventana='iVMS-4200') == 'iVMS-4200'
    assert nombre_visible(indice=2) == 'Camara 3'


# ---------------------------------------------------------------------------
# Los dos guardias: que los CUATRO clientes usen esto y no una copia propia.
# ---------------------------------------------------------------------------

def test_ningun_cliente_manda_el_uuid_de_sesion_como_camera_id():
    """La regresion concreta de H-11.

    `component_key` es un `uuid.uuid4()` por panel. Si vuelve a viajar como
    `camera_id`, el historico por camara deja de acumularse otra vez.
    """
    culpables = []
    for cliente in CLIENTES:
        archivo = _render_box(cliente)
        if not archivo.is_file():
            culpables.append(f'{cliente}: no existe {archivo}')
            continue
        texto = archivo.read_text(encoding='utf-8', errors='replace')
        for n, linea in enumerate(texto.splitlines(), 1):
            if re.search(r'["\']camera_id["\']\s*:\s*self\.component_key',
                         linea):
                culpables.append(f'{cliente}:{n}')
    assert not culpables, ('vuelve a viajar el uuid de sesion como camera_id: '
                           + ', '.join(culpables))


def test_los_cuatro_clientes_delegan_en_el_nucleo():
    """Que nadie se quede una copia propia de la logica de identidad.

    Es como empezo H-11: el arreglo existia en un cliente y los otros tres
    seguian con el fallo.
    """
    faltan = []
    for cliente in CLIENTES:
        archivo = _render_box(cliente)
        if not archivo.is_file():
            faltan.append(f'{cliente}: no existe el archivo')
            continue
        texto = archivo.read_text(encoding='utf-8', errors='replace')
        if 'identidad_camara' not in texto:
            faltan.append(f'{cliente}: no importa el nucleo')
        elif '_identidad.device_id(' not in texto:
            faltan.append(f'{cliente}: no llama a _identidad.device_id()')
    assert not faltan, 'clientes con identidad propia: ' + ', '.join(faltan)
