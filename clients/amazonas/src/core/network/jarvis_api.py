"""Movido al nucleo compartido: `elde_core.transport.jarvis_api`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITO 7).
"""
import sys as _sys
from elde_core.transport import jarvis_api as _modulo

_sys.modules[__name__] = _modulo
