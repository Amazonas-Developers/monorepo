"""Movido al nucleo compartido: `elde_core.capture.list_windows`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITO 7).
"""
import sys as _sys
from elde_core.capture import list_windows as _modulo

_sys.modules[__name__] = _modulo
