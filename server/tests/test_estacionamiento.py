"""
Pruebas del modo ESTACIONAMIENTO (4-ago-2026).

La optica vehicular del motor vigilante: solo alertan vehiculos,
'permanencia' se vuelve 'estacionado', la salida lleva el tiempo total y el
metadata publica la ocupacion. Aqui se prueba todo lo que no necesita GPU;
el ciclo completo llegada->estacionado->salida se verifica en vivo.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from vigilante_amazonas import config  # noqa: E402
from vigilante_amazonas.adaptador_estacionamiento import adaptar_tarjeta  # noqa: E402
from vigilante_amazonas.servicios.formato_whatsapp import texto_alerta  # noqa: E402
from vigilante_amazonas.servicios.rastreador_area import (  # noqa: E402
    RastreadorArea, _EstadoObjeto)


def _tarjeta(**extra):
    base = {'event_type': 'llegada', 'class_name': 'CARRO',
            'clase_gruesa': 'vehiculo', 'camera_name': 'c1',
            'permanencia_s': 330.0, 'description': 'x',
            'timestamp': 1785900000.0}
    base.update(extra)
    return base


def test_las_personas_no_alertan_en_estacionamiento():
    assert adaptar_tarjeta(_tarjeta(class_name='PERSONA',
                                    clase_gruesa='persona')) is None
    assert adaptar_tarjeta(_tarjeta(clase_gruesa='')) is None
    assert adaptar_tarjeta('basura') is None
    assert adaptar_tarjeta(None) is None


def test_permanencia_se_vuelve_estacionado():
    fuera = adaptar_tarjeta(_tarjeta(event_type='permanencia'))
    assert fuera['event_type'] == 'estacionado'
    assert fuera['description'] == 'CARRO ESTACIONADO (5m30s)'
    # La tarjeta original NO se muta (el area la comparte con otros).
    original = _tarjeta(event_type='permanencia')
    adaptar_tarjeta(original)
    assert original['event_type'] == 'permanencia'


def test_llegada_salida_y_merodeo_de_vehiculo_pasan_tal_cual():
    for evento in ('llegada', 'salida', 'merodeo'):
        fuera = adaptar_tarjeta(_tarjeta(event_type=evento))
        assert fuera is not None and fuera['event_type'] == evento


def test_whatsapp_titular_estacionado():
    texto = texto_alerta(_tarjeta(event_type='estacionado'),
                         local='Carpinteria')
    assert texto.split('\n')[0] == \
        '🚨 Carpinteria · 🚗 CARRO estacionado (5m30s)'


def test_contrato_conoce_estacionamiento():
    from elde_core.contracts.compat import (CLIENTE_POR_PIPELINE,
                                            PIPELINE_ANTIGUO)
    from elde_core.contracts.envelope import ClientType, Pipeline
    assert PIPELINE_ANTIGUO['Estacionamiento'] is Pipeline.ESTACIONAMIENTO
    assert CLIENTE_POR_PIPELINE[Pipeline.ESTACIONAMIENTO] is \
        ClientType.PERIMETRALES
    assert len(Pipeline) == 9


def test_config_del_modo():
    assert config.ESTACIONAMIENTO_UMBRAL_SEG > 0
    assert 'estacionado' in config.ESTACIONAMIENTO_EVENTOS_WHATSAPP


def test_umbral_de_permanencia_por_instancia():
    propio = RastreadorArea(umbral_permanencia_seg=42.0)
    assert propio._umbral_permanencia_seg() == 42.0
    global_ = RastreadorArea()
    assert global_._umbral_permanencia_seg() == \
        config.AREA_ALERTA_PERMANENCIA_SEG


def test_ocupacion_cuenta_solo_vehiculos_vigentes():
    r = RastreadorArea()
    ahora = time.time()
    r._por_camara['c'] = {
        1: _EstadoObjeto(track_id=1, clase='persona',
                         primera_vez=ahora - 60, ultima_vez=ahora),
        2: _EstadoObjeto(track_id=2, clase='carro',
                         primera_vez=ahora - 400, ultima_vez=ahora),
        3: _EstadoObjeto(track_id=3, clase='moto',
                         primera_vez=ahora - 900,
                         ultima_vez=ahora - 999),      # ya no se ve: fuera
    }
    assert r.ocupacion('c') == 2                       # persona + carro
    assert r.ocupacion('c', 'vehiculo') == 1           # solo el carro
    assert r.ocupacion('sin-camara') == 0
