"""
Pruebas del registro de dispositivos (HITO 8).

Lo que se fija: que acumule por dispositivo ESTABLE y no por sesion, que
sobreviva a un reinicio del servidor, que prefiera el `client_type` declarado
al deducido, y que no lance jamas — esta en el camino de cada frame y una
excepcion ahi tumbaria la conexion de video.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from src.app import registro_dispositivos as reg  # noqa: E402


def _limpio(tmp: Path):
    """Registro vacio apuntando a un archivo desechable.

    `_ultimo_volcado` se pone en 'ahora', no en 0: con 0 el PRIMER frame vuelca
    a disco (0 esta a mas de 30 s de ahora), y entonces el volcado explicito de
    la prueba ya no tendria nada sucio que escribir. En produccion ese primer
    volcado inmediato se quiere —el dispositivo nuevo aparece en el archivo sin
    esperar—, pero aqui hace la prueba no determinista.
    """
    import os
    import time
    os.environ['ELDE_REGISTRO_DISPOSITIVOS'] = str(tmp / 'registro.json')
    os.environ['ELDE_REGISTRO_SEGUNDOS'] = '9999'   # sin volcados automaticos
    reg._dispositivos.clear()
    reg._sucio = False
    reg._ultimo_volcado = time.time()


def _frame(device_id='dvr-J12345678-2', client_type='tienda',
           site_id='tienda-principal', pipeline='Personal de Amazonas',
           camera_name='Pasillo 3'):
    return {
        'event': 'inference',
        'type_inference': pipeline,
        'component_key': 'clave-de-enrutado',
        'client_type': client_type,
        'site_id': site_id,
        'data': {'image': 'xxx', 'camera_id': device_id,
                 'camera_name': camera_name},
    }


def test_acumula_por_dispositivo_y_no_por_sesion():
    """LA razon de ser del registro.

    Antes de H-11 el `camera_id` era un uuid4 por sesion: cien frames de la
    misma camara en dos arranques habrian dado DOS dispositivos.
    """
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        for _ in range(5):
            reg.anotar(_frame())
        reg.anotar(_frame())            # "otro arranque", mismo device_id
        filas = reg.dispositivos()
    assert len(filas) == 1, f'deberia haber 1 dispositivo, hay {len(filas)}'
    assert filas[0]['frames'] == 6
    assert filas[0]['device_id'] == 'dvr-J12345678-2'


def test_el_mismo_id_en_dos_sitios_son_dos_dispositivos():
    """`box-1` es un id valido y nada raro: dos tiendas distintas pueden tener
    cada una su recuadro 1. La clave es (site_id, device_id)."""
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        reg.anotar(_frame(device_id='box-1', site_id='tienda-norte'))
        reg.anotar(_frame(device_id='box-1', site_id='tienda-sur'))
        assert len(reg.dispositivos()) == 2
        assert len(reg.sitios()) == 2


def test_prefiere_el_client_type_declarado_al_deducido():
    """La deduccion por pipeline falla justo en los clientes multimodo:
    `Perimetrales` lanzado desde el gestor de ventanas NO es el cliente
    perimetral."""
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        reg.anotar(_frame(client_type='managers', pipeline='Perimetrales'))
        assert reg.dispositivos()[0]['client_type'] == 'managers'


def test_si_no_lo_declara_lo_deduce_del_pipeline():
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        m = _frame(pipeline='Perimetrales')
        del m['client_type']
        reg.anotar(m)
        assert reg.dispositivos()[0]['client_type'] in ('perimetrales',
                                                        'desconocido')


def test_sobrevive_a_un_reinicio_del_servidor():
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        reg.anotar(_frame())
        assert reg.volcar() is True
        reg._dispositivos.clear()       # como si el proceso hubiera muerto
        assert reg.cargar() == 1
        filas = reg.dispositivos()
    assert len(filas) == 1 and filas[0]['frames'] == 1


def test_el_volcado_es_atomico_y_deja_json_valido():
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        reg.anotar(_frame())
        reg.volcar()
        destino = Path(d) / 'registro.json'
        datos = json.loads(destino.read_text(encoding='utf-8'))
        assert not (Path(d) / 'registro.json.tmp').exists(), \
            'el temporal debe haberse renombrado, no quedarse ahi'
    assert datos['total'] == 1
    assert datos['dispositivos'][0]['device_id'] == 'dvr-J12345678-2'


def test_detecta_la_regresion_de_h11():
    """Si vuelve a llegar un uuid de sesion como camera_id, hay que enterarse.

    Es el aviso que faltaba: sin el, el registro se llenaria de dispositivos
    fantasma en silencio, igual que se lleno `output/` de archivos con uuid.
    """
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        reg.anotar(_frame(device_id='18dbc565-37d4-4c2e-ad92-48df7b9f41a5'))
        r = reg.resumen()
    assert r['ids_inestables'] == 1
    assert r['ejemplo_inestable'].startswith('18dbc565')


def test_un_id_estable_no_se_confunde_con_un_uuid():
    for bueno in ('dvr-J12345678-2', 'win-iVMS-4200', 'box-3',
                  'win-Camara-del-pasillo-3-larga-de-mas-de-treinta-y-seis'):
        assert not reg._parece_uuid(bueno), bueno


def test_nunca_lanza_con_basura():
    """Esta en el camino de cada frame: una excepcion aqui tumba la conexion."""
    with tempfile.TemporaryDirectory() as d:
        _limpio(Path(d))
        for basura in ({}, {'data': None}, {'data': {}}, {'data': 'texto'},
                       {'data': {'camera_id': ''}},
                       {'data': {'camera_id': None}},
                       {'data': {'camera_id': 123}}):
            reg.anotar(basura)          # no debe lanzar
        assert reg.dispositivos() == [] or all(
            f['device_id'] for f in reg.dispositivos())
