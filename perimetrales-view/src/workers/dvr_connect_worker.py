"""Movido al nucleo compartido: `elde_core.dvr.connect_worker`.

Redirige para que los imports existentes sigan funcionando. Se elimina al
refactorizar el cliente (HITOS 5-7).
"""
import sys as _sys
from elde_core.dvr import connect_worker as _modulo

_sys.modules[__name__] = _modulo
