"""
El payload del frame acepta `establecimiento` (3-ago-2026) sin aflojar el
modo estricto: es el local de la camara que encabeza su mensaje de WhatsApp.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'elde_core')) if (RAIZ / 'elde_core').is_dir() \
    else None
sys.path.insert(0, str(RAIZ))

from elde_core.contracts.eventos import FrameInference  # noqa: E402


def test_el_frame_acepta_establecimiento():
    frame = FrameInference(image=b'\xff\xd8x', establecimiento='La Comarca')
    assert frame.establecimiento == 'La Comarca'
    # Opcional: sin el, sigue valiendo (clientes que no lo mandan).
    assert FrameInference(image=b'\xff\xd8x').establecimiento is None


def test_estricto_sigue_rechazando_lo_desconocido():
    try:
        FrameInference(image=b'\xff\xd8x', local_de_la_camara='X')
        assert False, 'una clave desconocida debe rechazarse (extra=forbid)'
    except Exception:
        pass
