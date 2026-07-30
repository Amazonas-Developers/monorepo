"""
mivolo_vendor - Copia local del codigo de arquitectura de MiVOLO.

Origen: https://github.com/WildChlamydia/MiVOLO (research only, uso interno).
Se copia aqui en lugar de instalarlo como dependencia por dos razones:

  1. El paquete oficial fija `timm==0.8.13.dev0`. Este servidor corre con
     timm 1.0.27 (lo necesita el resto del stack), y downgradearlo romperia
     produccion.
  2. Asi el runtime no depende de un repo clonado en una carpeta temporal.

Cambio aplicado sobre el original (unico, documentado en el propio archivo):
`MiVOLOModel.__init__` llamaba a `VOLO.__init__` con 21 argumentos POSICIONALES.
timm 1.0 inserto `pos_drop_rate` en la posicion 15, corriendo el resto: a
`norm_layer` le llegaba la tupla `("ca","ca")` y fallaba con "'tuple' object is
not callable". Ahora se pasan por NOMBRE, lo que ademas lo hace inmune a
futuros cambios de orden. Verificado: los 290 tensores del checkpoint oficial
cargan con `strict=True`.
"""

from .mivolo_model import MiVOLOModel  # noqa: F401
