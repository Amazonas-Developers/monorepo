"""Movido al nucleo compartido: `elde_core.capture.capture_worker`.

OJO, este archivo es DISTINTO del resto de alias: `render_box.init_loop` no lo
importa, lo **ejecuta como script** en un subproceso:

    QProcess.start(sys.executable, ["src/workers/capture_woker.py", hwnd])

Con el alias de modulo a secas (`sys.modules[__name__] = _modulo`) el
subproceso terminaba al instante con exit 0 **sin capturar nada**: al correr
como script `__name__` vale `"__main__"`, pero el modulo del nucleo se importa
con su nombre real, asi que su guarda `if __name__ == "__main__"` NUNCA se
ejecutaba. Efecto visible: el boton Play no hacia nada — ni error, ni frames.
(H-23, detectado en el rodaje real del 31-jul-2026.)

Por eso aqui, ademas de redirigir para quien lo importe, se replica la guarda
de script llamando a `ejecutar_worker`, que el nucleo ya expone.
"""
import sys as _sys

from elde_core.capture import capture_worker as _modulo
from elde_core.capture.capture_worker import ejecutar_worker

if __name__ == "__main__":
    # Modo script: capturar la ventana que indique el argumento.
    if len(_sys.argv) < 2:
        _sys.exit(1)
    try:
        ejecutar_worker(int(_sys.argv[1]))
    except (ValueError, TypeError):
        _sys.exit(1)
else:
    # Modo import: alias transparente al modulo del nucleo.
    _sys.modules[__name__] = _modulo
