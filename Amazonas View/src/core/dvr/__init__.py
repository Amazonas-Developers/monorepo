"""Movido al nucleo compartido: `elde_core.dvr`."""
import sys as _sys
import elde_core.dvr as _modulo

_sys.modules[__name__] = _modulo
