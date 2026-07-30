"""Movido al nucleo compartido: `elde_core.ui.modal_msm`.

Redirige para que los imports existentes de este cliente sigan funcionando.
Se elimina al refactorizar el cliente (HITOS 5-7).
"""
import sys as _sys
from elde_core.ui import modal_msm as _modulo

_sys.modules[__name__] = _modulo
