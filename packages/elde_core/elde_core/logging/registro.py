"""
Registro (logging) unificado del ecosistema ELDE.

Por que existe
--------------
Al inventariar el 30-jul-2026, los CUATRO clientes tenian **cero** logging:
114 llamadas a `print()` entre ellos y ni un solo `getLogger`. El servidor si
usaba `logging`, con su propio formato. Resultado: cuando algo fallaba en un
cliente no quedaba rastro, y comparar lo que hicieron dos clientes ante el
mismo evento era imposible porque no habia nada que comparar.

El HITO 8 (servidor unico, registro de dispositivos) necesita justo eso:
trazas de los cuatro clientes en el mismo formato, con el mismo reloj y con la
identidad del emisor delante.

Que hace
--------
- Un formato unico que lleva `client_type` y `site_id`, para que las lineas de
  cuatro clientes distintos se puedan ordenar y comparar entre si.
- Archivo rotatorio en UTF-8 mas consola tolerante: los `print` con emoji
  (📄 ✅ 🔥) reventaban el arranque en consolas cp1252, y el mismo problema
  afectaria al log.
- Las excepciones no capturadas se registran ANTES de que el proceso muera.
  Es la clase de fallo que dejo a `tienda_view` cerrandose sin dejar rastro
  (H-01).

Regla 6: nada incrustado. Nivel, carpeta, tamaño y numero de archivos salen del
entorno, con valores por defecto razonables.

    ELDE_LOG_NIVEL     DEBUG|INFO|WARNING|ERROR      (por defecto INFO)
    ELDE_LOG_DIR       carpeta de destino            (por defecto <raiz>/logs)
    ELDE_LOG_MB        tamaño por archivo, en MB     (por defecto 5)
    ELDE_LOG_COPIAS    archivos rotados a conservar  (por defecto 3)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional

#: Prefijo comun. Todo logger del ecosistema cuelga de aqui, asi que subir o
#: bajar el nivel de `elde` afecta a todo de golpe sin tocar el logger raiz
#: (que es de la aplicacion, no nuestro).
RAIZ = 'elde'

_configurado = False


def _entero(nombre: str, por_defecto: int) -> int:
    try:
        valor = int((os.getenv(nombre) or '').strip())
    except ValueError:
        return por_defecto
    return valor if valor > 0 else por_defecto


def _nivel() -> int:
    nombre = (os.getenv('ELDE_LOG_NIVEL') or 'INFO').strip().upper()
    return getattr(logging, nombre, logging.INFO)


def _carpeta(raiz_proyecto: Optional[Path]) -> Path:
    explicita = (os.getenv('ELDE_LOG_DIR') or '').strip()
    if explicita:
        return Path(explicita)
    base = raiz_proyecto or Path.cwd()
    return Path(base) / 'logs'


def obtener(nombre: str) -> logging.Logger:
    """Devuelve el logger de un modulo. `obtener(__name__)` es lo habitual."""
    return logging.getLogger(f'{RAIZ}.{nombre}')


def configurar(client_type: str,
               site_id: str,
               raiz_proyecto: Optional[Path] = None,
               capturar_excepciones: bool = True,
               tambien_raiz: bool = False) -> logging.Logger:
    """Deja el registro listo. Idempotente: llamarlo dos veces no duplica.

    `client_type` y `site_id` son los mismos del contrato del HITO 3, y van en
    cada linea. Ese es el punto: sin ellos, juntar los logs de cuatro clientes
    da un amasijo en el que no se sabe quien dijo que.

    `tambien_raiz` es para el SERVIDOR. Sus 24 modulos usan
    `logging.getLogger(__name__)`, que no cuelga de `elde`, y reescribirlos
    seria redisenar, no refactorizar. Con esta opcion los mismos manejadores se
    enganchan tambien al logger raiz, asi que el servidor entero pasa al formato
    comun sin tocar ni uno de esos modulos.
    """
    global _configurado
    log = logging.getLogger(RAIZ)
    if _configurado:
        return log

    log.setLevel(_nivel())
    log.propagate = False        # no duplicar en el logger raiz de la app

    formato = logging.Formatter(
        fmt=('%(asctime)s | %(levelname)-7s | ' + f'{client_type}/{site_id}'
             + ' | %(name)s | %(message)s'),
        datefmt='%Y-%m-%d %H:%M:%S')

    # --- Consola -----------------------------------------------------------
    # `errors='replace'` porque una consola cp1252 no puede escribir emoji y
    # un UnicodeEncodeError dentro del logger tumbaria justo el mensaje que
    # explica el fallo que se estaba registrando.
    consola = logging.StreamHandler(sys.stderr)
    consola.setFormatter(formato)
    log.addHandler(consola)

    # --- Archivo rotatorio -------------------------------------------------
    # Si la carpeta no se puede crear (permisos, disco lleno) se sigue con la
    # consola: quedarse sin log de archivo es malo, no arrancar es peor.
    try:
        carpeta = _carpeta(raiz_proyecto)
        carpeta.mkdir(parents=True, exist_ok=True)
        archivo = logging.handlers.RotatingFileHandler(
            carpeta / f'{client_type}.log',
            maxBytes=_entero('ELDE_LOG_MB', 5) * 1024 * 1024,
            backupCount=_entero('ELDE_LOG_COPIAS', 3),
            encoding='utf-8')
        archivo.setFormatter(formato)
        log.addHandler(archivo)
    except OSError as e:
        log.warning('sin log de archivo (%s); solo consola', e)

    if tambien_raiz:
        # Los mismos manejadores, no copias: una sola rotacion del archivo.
        # Ademas deja el logger raiz CON manejadores, lo que convierte en
        # inofensivo el `logging.basicConfig()` que el servidor ya hacia
        # (basicConfig no toca nada si la raiz ya esta configurada).
        raiz = logging.getLogger()
        raiz.setLevel(log.level)
        for h in log.handlers:
            raiz.addHandler(h)

    if capturar_excepciones:
        _instalar_excepthook(log)

    _configurado = True
    log.info('registro iniciado — nivel %s', logging.getLevelName(log.level))
    return log


def _instalar_excepthook(log: logging.Logger) -> None:
    """Registra lo que mate al proceso, sin cambiar lo que ocurre despues.

    Se encadena al excepthook anterior en vez de sustituirlo: si alguien ya
    habia puesto el suyo (Qt, un depurador), sigue ejecutandose igual.
    """
    anterior = sys.excepthook

    def _hook(tipo, valor, traza):
        if not issubclass(tipo, KeyboardInterrupt):
            log.critical('excepcion no capturada',
                         exc_info=(tipo, valor, traza))
        anterior(tipo, valor, traza)

    sys.excepthook = _hook
