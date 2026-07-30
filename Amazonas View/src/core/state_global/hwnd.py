"""Movido al nucleo compartido: `elde_core.capture.hwnd_state`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITO 7).
"""
import sys as _sys
from elde_core.capture import hwnd_state as _modulo

_sys.modules[__name__] = _modulo
