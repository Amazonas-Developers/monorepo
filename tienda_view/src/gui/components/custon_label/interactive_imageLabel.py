"""Movido al nucleo compartido: `elde_core.ui.interactive_image_label`.

Redirige para que los imports existentes sigan funcionando. Se elimina al
refactorizar el cliente (HITOS 5-7).

`Amazonas View` NO usa este alias: conserva su propia version, mas antigua,
con otra firma de `__init__` y sin las zonas de pedido/entrega. Adoptar el
superconjunto le cambiaria la interfaz que su render_box ya llama.
"""
import sys as _sys
from elde_core.ui import interactive_image_label as _modulo

_sys.modules[__name__] = _modulo
