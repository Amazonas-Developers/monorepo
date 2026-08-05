"""
Pruebas del refinador de clase fina de vehículos (`aruba.pt`), 5-ago-2026.

Lo que se puede probar sin GPU: el mapeo de clases, el IoU, la votación por
track y que el contrato `refinar()` sea el mismo del vigilante (para poder
sustituirlo sin tocar `VigilanteWS`). La calidad del modelo se MIDIÓ aparte
contra fotos reales — ver la cabecera del módulo y H-29.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))

from src.analityc.core.estacionamiento_vehiculos import (  # noqa: E402
    ARUBA_A_PROPIA, CLASES_REFINABLES, RefinadorAruba, iou)
from vigilante_amazonas.deteccion.rastreador import DeteccionVig  # noqa: E402


def _det(track_id: int, clase: str, bbox=(10, 10, 60, 60)) -> DeteccionVig:
    return DeteccionVig(track_id=track_id, bbox=bbox, conf=0.9, clase=clase,
                        cls_coco=2)


def test_el_mapeo_cubre_las_cuatro_clases_del_modelo():
    assert set(ARUBA_A_PROPIA) == {'car', 'van', 'truck', 'bus'}
    assert ARUBA_A_PROPIA['car'] == 'carro'
    assert ARUBA_A_PROPIA['van'] == 'camioneta'
    # ELDE no tiene clase autobús: bus cae en camión, la MISMA aproximación
    # que ya hacía el mapeo COCO (bus=5 -> camion). Sin regresión.
    assert ARUBA_A_PROPIA['bus'] == 'camion'
    # Todo destino es una clase que el refinador puede tocar.
    assert set(ARUBA_A_PROPIA.values()) <= set(CLASES_REFINABLES)


def test_las_personas_nunca_se_reetiquetan():
    assert 'persona' not in CLASES_REFINABLES
    assert 'personal_seguridad' not in CLASES_REFINABLES
    assert 'moto' not in CLASES_REFINABLES     # el modelo no conoce motos


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert round(iou((0, 0, 10, 10), (5, 0, 15, 10)), 2) == 0.33


def test_apagado_por_defecto_y_no_toca_nada():
    """Sin la variable de entorno el refinador queda inactivo y `refinar`
    devuelve las detecciones TAL CUAL (medido: en estas cámaras empeora)."""
    os.environ.pop('ESTACIONAMIENTO_MODELO_VEHICULOS_ACTIVO', None)
    ref = RefinadorAruba()
    assert ref.disponible is False
    dets = [_det(1, 'carro'), _det(2, 'camion')]
    assert ref.refinar('c1', 1, None, dets) is dets
    assert [d.clase for d in dets] == ['carro', 'camion']


def test_la_votacion_estabiliza_la_etiqueta():
    """La clase más votada del track manda: la etiqueta no parpadea entre
    frames aunque el modelo dude en alguno."""
    ref = RefinadorAruba()
    ref._votos[('c1', 7)] = __import__('collections').deque(
        ['camioneta', 'camioneta', 'camion'], maxlen=5)
    d = _det(7, 'carro')
    ref._aplicar('c1', [d])
    assert d.clase == 'camioneta'
    assert ref.reetiquetados == 1
    # Sin votos, no se toca.
    otro = _det(8, 'carro')
    ref._aplicar('c1', [otro])
    assert otro.clase == 'carro'


def test_olvidar_camara_limpia_solo_esa():
    ref = RefinadorAruba()
    ref._votos[('c1', 1)] = ['carro']
    ref._votos[('c2', 1)] = ['carro']
    ref.olvidar_camara('c1')
    assert ('c1', 1) not in ref._votos
    assert ('c2', 1) in ref._votos


def test_mismo_contrato_que_el_refinador_del_vigilante():
    """Es lo que permite sustituirlo sin tocar VigilanteWS."""
    import inspect
    from vigilante_amazonas.deteccion.clasificador_vehiculos import (
        RefinadorVehiculos)
    propio = inspect.signature(RefinadorAruba.refinar).parameters
    original = inspect.signature(RefinadorVehiculos.refinar).parameters
    assert list(propio) == list(original)
