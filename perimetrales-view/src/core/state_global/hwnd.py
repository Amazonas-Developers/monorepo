"""Movido al nucleo compartido: `elde_core.capture.hwnd_state`.

Este archivo ya solo redirige, para que los imports existentes de este cliente
sigan funcionando sin tocarlos. Se elimina cuando el cliente se refactorice
(HITOS 5-7). Ver docs/refactor/04_NUCLEO_COMPARTIDO.md.
"""
import sys as _sys
from elde_core.capture import hwnd_state as _modulo

_sys.modules[__name__] = _modulo
