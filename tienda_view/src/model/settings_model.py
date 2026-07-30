"""Movido al nucleo compartido: `elde_core.config.settings_model`.

Este archivo ya solo redirige, para que los imports existentes de este cliente
sigan funcionando sin tocarlos. Se elimina cuando el cliente se refactorice
(HITOS 5-7). Ver docs/refactor/04_NUCLEO_COMPARTIDO.md.
"""
import sys as _sys
from elde_core.config import settings_model as _modulo

_sys.modules[__name__] = _modulo
