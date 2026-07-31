"""
Pruebas de la API de lectura (HITO 8/9).

Lo unico que merece prueba unitaria aqui es lo delicado en seguridad: el
saneado de los `device_id` que llegan por la URL. Acaban siendo nombres de
archivo (`analytics_report_*_<id>.json`, `heatmap/<id>.png`), asi que un id
malicioso no puede salirse de la carpeta. El resto de la API se verifica en
vivo contra el servidor real, que es donde de verdad se ve si responde.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from src.app.api_lectura import _saneado  # noqa: E402


def test_un_id_normal_pasa_intacto():
    for ident in ('dvr-J12345678-2', 'win-iVMS-4200', 'box-3'):
        assert _saneado(ident) == ident


def test_el_traversal_no_sobrevive():
    """`../../.env` como device_id no puede llegar al sistema de archivos."""
    for malicioso in ('../../.env', '..\\..\\secreto', 'a/../../b',
                      'con..puntos..seguidos', '/etc/passwd', 'C:\\Windows'):
        limpio = _saneado(malicioso)
        assert '..' not in limpio, f'{malicioso!r} -> {limpio!r}'
        assert '/' not in limpio and '\\' not in limpio


def test_vacio_y_basura_no_revientan():
    assert _saneado('') == ''
    assert _saneado(None) == ''
    assert _saneado('***???') == ''
