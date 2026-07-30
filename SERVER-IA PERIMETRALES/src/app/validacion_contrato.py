"""
src/app/validacion_contrato.py — Validacion del contrato en el servidor.

Paso 5 del plan de migracion del HITO 2. Conecta los esquemas de
`elde_core.contracts` al bucle del websocket, **detras de la capa de
compatibilidad**, de modo que el servidor entiende a la vez el formato antiguo
y el nuevo.

## Por que arranca en modo observacion

Rechazar de entrada seria romper los 4 clientes en produccion el mismo dia. El
comportamiento se controla con `ELDE_VALIDAR_CONTRATO`:

- `observar` (**por defecto**) — valida, registra lo que rechazaria y **deja
  pasar el mensaje**. Coste: una validacion Pydantic por frame. No cambia el
  comportamiento del servidor en absoluto.
- `estricto` — descarta el mensaje invalido y responde con un error claro. Es
  el objetivo del HITO 8, cuando los registros de `observar` esten limpios y
  los 4 clientes migrados.
- `apagado` — ni valida. Para descartar la validacion como sospechosa si algun
  dia hay que diagnosticar un problema de rendimiento.

Esa progresion es la que permite activar la validacion sin una ventana de
caida: primero se mira, luego se corta.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import Counter
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MODO = (os.getenv('ELDE_VALIDAR_CONTRATO', 'observar') or 'observar').strip().lower()
if MODO not in ('observar', 'estricto', 'apagado'):
    logger.warning("ELDE_VALIDAR_CONTRATO=%r no reconocido; se usa 'observar'",
                   MODO)
    MODO = 'observar'

SITE_ID = os.getenv('ELDE_SITE_ID', 'sitio-unico').strip() or 'sitio-unico'

_lock = threading.Lock()
_stats: Counter = Counter()
# Un mismo error se repite 25 veces por segundo: se registra el primero de cada
# clase y despues solo se cuenta.
_ya_registrado: set = set()

try:
    from elde_core.contracts import (POR_EVENTO, Envelope, desde_antiguo,
                                     es_formato_antiguo)
    DISPONIBLE = True
except Exception as exc:                      # el servidor debe arrancar igual
    logger.warning("contrato no disponible (%s); la validacion queda apagada",
                   exc)
    DISPONIBLE = False


def activa() -> bool:
    return DISPONIBLE and MODO != 'apagado'


def _anota(clave: str, detalle: str = '') -> None:
    with _lock:
        _stats[clave] += 1
        nuevo = clave not in _ya_registrado
        if nuevo:
            _ya_registrado.add(clave)
    if nuevo and detalle:
        # Solo la primera vez de cada clase de error, para no inundar el log.
        logger.warning("contrato [%s] %s: %s", MODO, clave, detalle[:400])


def revisar(mensaje: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Valida un mensaje entrante.

    Devuelve `(aceptar, motivo)`. En modo `observar`, `aceptar` es siempre
    True: el motivo se registra pero el mensaje sigue su curso. Nunca lanza —
    un fallo de la propia validacion no puede tumbar una conexion.
    """
    if not activa():
        return True, None
    try:
        # ── 1. Traducir el formato antiguo, si lo es ──
        if es_formato_antiguo(mensaje):
            env, err = desde_antiguo(mensaje, site_id=SITE_ID)
            if env is None:
                _anota('antiguo_intraducible', err or '')
                return (MODO != 'estricto'), err
            _anota('traducido_del_formato_antiguo')
        else:
            try:
                env = Envelope(**mensaje)
            except Exception as exc:
                _anota('envelope_invalido', str(exc))
                return (MODO != 'estricto'), f"envelope invalido: {exc}"
            _anota('envelope_nuevo')

        # ── 2. Validar el payload contra el modelo de su evento ──
        modelo = POR_EVENTO.get(getattr(env.event_type, 'value',
                                        str(env.event_type)))
        if modelo is None:
            _anota('evento_sin_modelo', str(env.event_type))
            return True, None
        try:
            modelo(**env.payload)
        except Exception as exc:
            clave = f'payload_invalido:{env.pipeline}'
            _anota(clave, str(exc))
            return (MODO != 'estricto'), f"payload invalido: {exc}"

        _anota('valido')
        return True, None
    except Exception as exc:                  # la validacion jamas rompe nada
        _anota('error_interno_validacion', str(exc))
        return True, None


def resumen() -> Dict[str, Any]:
    """Contadores para exponer por `/health` y decidir cuando pasar a
    `estricto`: cuando `payload_invalido:*` y `antiguo_intraducible` lleven
    dias a cero, cortar ya no rompe nada."""
    with _lock:
        datos = dict(_stats)
    total = sum(datos.values()) or 1
    problemas = sum(v for k, v in datos.items()
                    if k.startswith(('payload_invalido', 'envelope_invalido',
                                     'antiguo_intraducible')))
    return {
        'modo': MODO,
        'disponible': DISPONIBLE,
        'site_id': SITE_ID,
        'contadores': datos,
        'mensajes_con_problema_pct': round(100.0 * problemas / total, 2),
        'listo_para_estricto': problemas == 0 and total > 1,
    }
