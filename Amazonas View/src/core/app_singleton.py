"""Movido al nucleo compartido: `elde_core.config.app_singleton`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITO 7).
"""
import sys as _sys
from elde_core.config import app_singleton as _modulo

_sys.modules[__name__] = _modulo
