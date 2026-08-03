"""
Pruebas de la compuerta de confianza POR CLASE del detector (3-ago-2026).

Medido sobre 208 fotos reales: el umbral unico 0.25 reconocia el 25% de las
motos (mediana 0.16) y el 27% de los carros nocturnos. La compuerta vive en
config.CONF_POR_CLASE y se aplica en el detector tras mapear la clase.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))

from vigilante_amazonas import config  # noqa: E402
from vigilante_amazonas.deteccion.mapeo_clases import CLASES_PROPIAS  # noqa: E402


def test_todas_las_clases_del_detector_tienen_umbral():
    # personal_seguridad no sale del detector (la asigna el clasificador
    # secundario sobre `persona`), asi que no necesita umbral propio.
    for clase in CLASES_PROPIAS:
        if clase == 'personal_seguridad':
            continue
        assert clase in config.CONF_POR_CLASE, clase


def test_el_suelo_es_el_minimo_de_las_compuertas():
    """El predict corre a CONF_DETECCION: si alguna clase pidiera MENOS que
    el suelo, su compuerta seria letra muerta (el modelo ya la descarto)."""
    assert config.CONF_DETECCION <= min(config.CONF_POR_CLASE.values())


def test_umbral_de_clase_resuelve_y_cae_al_suelo():
    assert config.umbral_de_clase('moto') == config.CONF_POR_CLASE['moto']
    assert config.umbral_de_clase('persona') == \
        config.CONF_POR_CLASE['persona']
    # Clase desconocida -> el suelo global (comportamiento conservador).
    assert config.umbral_de_clase('lo-que-sea') == config.CONF_DETECCION


def test_la_moto_ya_no_queda_fuera():
    """El motivo de la obra: una moto tipica de estas camaras (conf ~0.16)
    debe pasar su compuerta; con el umbral unico 0.25 no pasaba."""
    assert 0.16 >= config.umbral_de_clase('moto')
    assert 0.16 < 0.25    # documenta el antes


def test_el_tracker_no_recorta_lo_que_el_detector_dejo_pasar():
    """La activacion del tracker no puede ser MAS estricta que la compuerta
    mas baja: re-mataria a las motos que el detector acaba de rescatar."""
    assert config.TRACK_ACTIVATION_UMBRAL <= \
        min(config.CONF_POR_CLASE.values())


def test_nacer_un_track_no_exige_mas_que_la_activacion():
    """GOTCHA de supervision: ByteTrack pone det_thresh = activacion + 0.1
    para INICIAR tracks. Con 0.25 historico, un carro de 0.32 no podia
    nacer (pedia 0.35). El rastreador lo neutraliza; esta prueba lo fija."""
    from vigilante_amazonas.deteccion.rastreador import RastreadorCamara
    r = RastreadorCamara()
    assert r._tracker.det_thresh <= config.TRACK_ACTIVATION_UMBRAL + 1e-9
