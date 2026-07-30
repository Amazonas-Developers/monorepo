"""Movido al nucleo compartido: `elde_core.dvr.hikvision_sdk`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITOS 5-7).
"""
import sys as _sys
from elde_core.dvr import hikvision_sdk as _modulo

_sys.modules[__name__] = _modulo
