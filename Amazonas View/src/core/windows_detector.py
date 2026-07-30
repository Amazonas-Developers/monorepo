"""Movido al nucleo compartido: `elde_core.capture.windows_detector`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITO 7).
"""
import sys as _sys
from elde_core.capture import windows_detector as _modulo

_sys.modules[__name__] = _modulo
