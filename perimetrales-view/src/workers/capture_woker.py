"""Movido al nucleo compartido: `elde_core.capture.capture_worker`.

Redirige para que los imports existentes sigan funcionando. Se elimina al
terminar la migracion del cliente.
"""
import sys as _sys
from elde_core.capture import capture_worker as _modulo

_sys.modules[__name__] = _modulo
