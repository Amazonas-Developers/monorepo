"""
El payload que app.py arma para el executor debe encajar EXACTO con la
firma de `_full_frame_sync` (5-ago-2026).

Regresión real: al añadir `leer_placas` se metió en la tupla del payload
pero NO en la firma, y el servidor fallaba en CADA frame con
«_full_frame_sync() takes from 13 to 24 positional arguments but 25 were
given». Compilaba perfectamente; solo se veía ejecutando.

Esta prueba cuenta los elementos del payload en el código y los compara con
los parámetros de la función, para que la próxima vez salte aquí.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

RUTA_APP = RAIZ / 'server' / 'src' / 'app' / 'app.py'


def _elementos_del_payload() -> int:
    """Cuántos valores mete app.py en la tupla `frame_payload`."""
    arbol = ast.parse(RUTA_APP.read_text(encoding='utf-8'))
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Assign)
                and any(getattr(d, 'id', '') == 'frame_payload'
                        for d in nodo.targets)
                and isinstance(nodo.value, ast.Tuple)):
            return len(nodo.value.elts)
    raise AssertionError('no se encontró la tupla frame_payload en app.py')


def test_el_payload_encaja_con_la_firma():
    from src.app.app import _full_frame_sync
    parametros = list(inspect.signature(_full_frame_sync).parameters)
    n = _elementos_del_payload()
    assert n <= len(parametros), (
        f'el payload lleva {n} valores y _full_frame_sync acepta '
        f'{len(parametros)}: {parametros}')
    # Los dos ultimos que se añadieron deben estar en la firma.
    for nuevo in ('establecimiento', 'leer_placas'):
        assert nuevo in parametros, f'falta {nuevo} en _full_frame_sync'


def test_la_cadena_completa_acepta_leer_placas():
    """De app.py al modo: las tres firmas encadenadas deben aceptarlo."""
    from src.analityc.core.estacionamiento import EstacionamientoWS
    from src.app.app import _full_frame_sync, process_image_sync
    from vigilante_amazonas.adaptador_websocket import VigilanteWS
    for fn in (_full_frame_sync, process_image_sync,
               VigilanteWS.process_frame, EstacionamientoWS.process_frame):
        assert 'leer_placas' in inspect.signature(fn).parameters, fn
