"""
Pruebas del segmento por ESTABLECIMIENTO (3-ago-2026).

El dashboard de perimetrales se segmenta por el local elegido en el select
"Local" de cada camara. El local EFECTIVO de una alerta es el del sidecar
(alertas nuevas) o, como respaldo, el local ACTUAL de su camara segun el
registro de dispositivos (alertas viejas). Tambien acota la busqueda VLM.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from src.app import api_lectura as api  # noqa: E402
from src.app import busqueda_vlm as vlm  # noqa: E402

_DEFAULTS = dict(evento=None, clase=None, clase_gruesa=None, camara=None,
                 establecimiento=None, desde=None, hasta=None, q=None,
                 limite=60, offset=0)


def _buscar(**kw):
    return api.listar_alertas(**{**_DEFAULTS, **kw})


def _alerta(carpeta: Path, nombre: str, **meta) -> None:
    (carpeta / nombre).write_bytes(b'\xff\xd8fake')
    (carpeta / nombre).with_suffix('.json').write_text(
        json.dumps(meta, ensure_ascii=False), encoding='utf-8')


def _montar(carpeta: Path):
    """Tres alertas: sidecar CON local, sidecar viejo con camara mapeada, y
    camara sin local por ningun lado."""
    _alerta(carpeta, '20260803_100000_000_a.jpg', clase='CARRO',
            camara='cam-carpi', epoch=1.0, establecimiento='Carpinteria')
    _alerta(carpeta, '20260803_110000_000_b.jpg', clase='PERSONA',
            camara='cam-carpi', epoch=2.0)          # viejo: hereda del registro
    _alerta(carpeta, '20260803_120000_000_c.jpg', clase='MOTO',
            camara='cam-libre', epoch=3.0)          # nadie le asigno local
    os.environ['VIGILANTE_SCREENSHOTS'] = str(carpeta)
    api._CACHE_ALERTAS['marca'] = 0.0


def _desmontar():
    os.environ.pop('VIGILANTE_SCREENSHOTS', None)
    api._CACHE_ALERTAS['marca'] = 0.0


def test_filtro_y_faceta_por_establecimiento():
    real = api._registro.dispositivos
    api._registro.dispositivos = lambda **kw: [
        {'device_id': 'dvr-1', 'camera_name': 'cam-carpi',
         'establecimiento': 'Carpinteria'}]
    with tempfile.TemporaryDirectory() as tmp:
        _montar(Path(tmp))
        try:
            # El local del sidecar Y el heredado del registro cuentan juntos.
            carpi = _buscar(establecimiento='Carpinteria')
            assert carpi['total'] == 2, carpi['total']
            # Cada alerta sale con su local EFECTIVO (el viejo tambien).
            assert all(a['local'] == 'Carpinteria' for a in carpi['alertas'])
            # Las camaras sin local caen en el segmento "(sin local)".
            libres = _buscar(establecimiento=api.SIN_LOCAL)
            assert libres['total'] == 1
            assert libres['alertas'][0]['clase'] == 'MOTO'
            # La faceta alimenta las pestañas dinamicas del dashboard.
            todas = _buscar()
            assert todas['facetas']['por_establecimiento'] == {
                'Carpinteria': 2, api.SIN_LOCAL: 1}
            # Un local desconocido no revienta: simplemente 0.
            assert _buscar(establecimiento='Fantasma')['total'] == 0
        finally:
            _desmontar()
            api._registro.dispositivos = real


def test_vlm_acotada_al_establecimiento():
    real = api._registro.dispositivos
    api._registro.dispositivos = lambda **kw: [
        {'device_id': 'dvr-1', 'camera_name': 'cam-carpi',
         'establecimiento': 'Carpinteria'}]
    with tempfile.TemporaryDirectory() as tmp:
        _montar(Path(tmp))
        try:
            assert len(vlm._fotos('alertas', 10)) == 3
            assert len(vlm._fotos('alertas', 10, 'Carpinteria')) == 2
            assert len(vlm._fotos('alertas', 10, api.SIN_LOCAL)) == 1
            assert vlm._fotos('alertas', 10, 'Fantasma') == []
        finally:
            _desmontar()
            api._registro.dispositivos = real


def test_registro_guarda_y_limpia_el_local():
    from src.app import registro_dispositivos as reg
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['ELDE_REGISTRO_DISPOSITIVOS'] = str(
            Path(tmp) / 'registro.json')
        try:
            mensaje = {'site_id': 'sitio-prueba', 'client_type': 'perimetrales',
                       'type_inference': 'VigilanteAmazonas',
                       'data': {'camera_id': 'dvr-PRUEBA-LOCAL-1',
                                'camera_name': 'cam-prueba',
                                'establecimiento': 'Carpinteria'}}
            reg.anotar(mensaje)
            fila = next(f for f in reg.dispositivos()
                        if f['device_id'] == 'dvr-PRUEBA-LOCAL-1')
            assert fila['establecimiento'] == 'Carpinteria'
            # Devolver la camara a "sin local" TAMBIEN debe reflejarse.
            mensaje['data']['establecimiento'] = None
            reg.anotar(mensaje)
            fila = next(f for f in reg.dispositivos()
                        if f['device_id'] == 'dvr-PRUEBA-LOCAL-1')
            assert fila['establecimiento'] == ''
        finally:
            os.environ.pop('ELDE_REGISTRO_DISPOSITIVOS', None)
            with reg._lock:
                reg._dispositivos.pop('sitio-prueba/dvr-PRUEBA-LOCAL-1', None)
                reg._sucio = False
