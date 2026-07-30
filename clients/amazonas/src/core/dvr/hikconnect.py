"""Movido al nucleo compartido: `elde_core.dvr.hikconnect`.

Este cliente conservaba su propia copia del stack DVR, mas antigua que la del
nucleo (por ejemplo `hikconnect.py`: 218 lineas frente a 580). Solo la usaba
para `ChannelTypeDetector`, mientras el panel de Dispositivos —que ya viene del
nucleo— usaba la version buena: dos stacks distintos conviviendo.

La del nucleo es superconjunto compatible: mismas clases y metodos, mas
parametros opcionales con valor por defecto.
"""
import sys as _sys
from elde_core.dvr import hikconnect as _modulo

_sys.modules[__name__] = _modulo
