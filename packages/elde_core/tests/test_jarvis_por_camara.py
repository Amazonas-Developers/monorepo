"""
Pruebas del envio de novedades POR CAMARA (1-ago-2026).

Cada recuadro del cliente perimetral puede apuntar sus alertas a un
establecimiento distinto. Aqui se prueba la parte del nucleo: como se
resuelve el nombre guardado contra la lista de Jarvis, sin red ni GUI
(la instancia se crea con __new__ para no disparar el login del
constructor).
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'elde_core')) if (RAIZ / 'elde_core').is_dir() \
    else None
sys.path.insert(0, str(RAIZ))

from elde_core.transport.jarvis_api import Jarvis_api  # noqa: E402


def _api_sin_red() -> Jarvis_api:
    api = Jarvis_api.__new__(Jarvis_api)
    api.list_of_establishments = [
        {'name': 'La Comarca', '_id': 'id-comarca'},
        {'name': 'Bodega Central', '_id': 'id-bodega'},
        {'name': 'Comarca Norte', '_id': 'id-norte'},
    ]
    api.selected_establishment = {'name': 'La Comarca', '_id': 'id-comarca'}
    return api


def test_busca_exacto_antes_que_parcial():
    api = _api_sin_red()
    # 'la comarca' coincide EXACTO con 'La Comarca' aunque 'Comarca Norte'
    # tambien la contiene: el exacto manda.
    assert api.buscar_establecimiento('la comarca')['_id'] == 'id-comarca'
    assert api.buscar_establecimiento('LA COMARCA')['_id'] == 'id-comarca'
    # Parcial cuando no hay exacto.
    assert api.buscar_establecimiento('bodega')['_id'] == 'id-bodega'
    assert api.buscar_establecimiento('norte')['_id'] == 'id-norte'
    assert api.buscar_establecimiento('no existe') is None
    assert api.buscar_establecimiento('') is None
    assert api.buscar_establecimiento(None) is None


def test_resolver_destino_cae_al_global():
    api = _api_sin_red()
    # Nombre valido -> ese local, NO el global.
    assert api._resolver_destino('Bodega Central')['_id'] == 'id-bodega'
    # Dict ya resuelto -> tal cual (es lo que pasa entre los callbacks
    # asincronos: se resuelve una vez y viaja resuelto).
    propio = {'name': 'Otro', '_id': 'id-otro'}
    assert api._resolver_destino(propio) is propio
    # Sin establecimiento o nombre desconocido -> el seleccionado global
    # (comportamiento historico del selector del pie).
    assert api._resolver_destino(None)['_id'] == 'id-comarca'
    assert api._resolver_destino('')['_id'] == 'id-comarca'
    assert api._resolver_destino('fantasma')['_id'] == 'id-comarca'


def test_sin_global_y_sin_nombre_no_hay_destino():
    api = _api_sin_red()
    api.selected_establishment = None
    assert api._resolver_destino(None) is None
    assert api._resolver_destino('fantasma') is None
    # Pero un nombre VALIDO si resuelve aunque no haya global.
    assert api._resolver_destino('bodega')['_id'] == 'id-bodega'
