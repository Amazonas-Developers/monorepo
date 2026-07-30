"""
Sesion de credenciales de Hik-Connect, en memoria y solo mientras dura.

## Por que existe

Las claves de Hik-Connect estaban **escritas en el codigo** de `get_url.py` y
tambien en el `.env` de algun cliente. Eso las publico en GitHub durante meses
(HALLAZGOS.md H-13). Un archivo de configuracion se comparte, se copia y se
sube; una credencial no debe vivir ahi.

El modelo nuevo:

1. La App Key y el Secret se **escriben en el cliente**, en el panel de
   dispositivos. En ningun otro sitio.
2. Se **guardan cifradas** en el almacen del propio cliente
   (`DVRRepository`, Fernet derivado del hardware de la maquina), igual que
   ya se hacia con el resto de credenciales de equipo.
3. Al **conectar**, se publican en el entorno del proceso para que el resto
   del codigo (scripts sueltos, utilidades) pueda usarlas sin volver a
   pedirlas.
4. Al **cerrar sesion**, se borran del entorno.

Lo importante del punto 3: viven en `os.environ` del **proceso en marcha**, no
en un `.env` del disco. Cuando el cliente se cierra, desaparecen con el. Nada
que se pueda commitear por descuido.

## Aviso

Esto protege de la fuga por repositorio, que es la que ocurrio. **No** protege
de alguien con acceso a la maquina: el almacen cifrado se descifra con una
clave derivada del propio hardware, asi que en ese equipo es legible. Para eso
haria falta una boveda del sistema operativo, que es otra discusion.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

CLAVE_ENV = 'hik_app_key'
SECRETO_ENV = 'hik_app_secret'


def iniciar(app_key: str, app_secret: str) -> bool:
    """Publica las credenciales en el entorno del proceso.

    Se llama tras una conexion correcta. Devuelve False si alguna viene vacia:
    dejar el entorno a medias es peor que no tocarlo."""
    clave = (app_key or '').strip()
    secreto = (app_secret or '').strip()
    if not clave or not secreto:
        return False
    os.environ[CLAVE_ENV] = clave
    os.environ[SECRETO_ENV] = secreto
    return True


def cerrar() -> None:
    """Borra las credenciales del entorno. Idempotente."""
    for var in (CLAVE_ENV, SECRETO_ENV):
        os.environ.pop(var, None)


def activa() -> bool:
    """Hay una sesion con credenciales publicadas."""
    return bool(os.environ.get(CLAVE_ENV) and os.environ.get(SECRETO_ENV))


def credenciales() -> Tuple[Optional[str], Optional[str]]:
    """La pareja actual, o (None, None) si no hay sesion."""
    if not activa():
        return None, None
    return os.environ.get(CLAVE_ENV), os.environ.get(SECRETO_ENV)
