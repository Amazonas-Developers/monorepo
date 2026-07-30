"""
src/app/captura_contrato.py — Captura de payloads reales del websocket.

Paso 2 del plan de migracion del HITO 2. Sirve para dos cosas:

  1. Cumplir el criterio de aceptacion del HITO 3: los esquemas del contrato
     tienen que validar los payloads REALES del sistema actual, no los que
     uno se imagina leyendo el codigo.
  2. Ser la unica red de seguridad de los HITOS 5-7: no hay ni un test en los
     5 proyectos, asi que estos payloads son la referencia con la que comparar
     el comportamiento antes y despues del refactor.

**Esta APAGADA por defecto.** Se enciende con la variable de entorno
`ELDE_CAPTURA_PAYLOADS=1`. Sin ella, `registrar()` retorna en la primera linea
y el coste es una comparacion booleana por mensaje.

Dos decisiones importantes:

- **No guarda una copia por mensaje.** Un frame de video son cientos de KB y
  llegan ~25 por segundo: guardarlos todos llenaria el disco en minutos. Lo
  que se guarda es *una muestra por forma distinta de mensaje*, identificada
  por la firma de sus claves y tipos.
- **No guarda binarios ni cadenas largas.** Las imagenes y los blobs se
  sustituyen por un marcador con su tamano. Ademas de ahorrar espacio, evita
  que un payload con datos personales acabe en disco.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict

_ACTIVA = os.getenv('ELDE_CAPTURA_PAYLOADS', '').strip() in ('1', 'true', 'si')
_DESTINO = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')),
    'output', 'contrato')

_lock = threading.Lock()
_vistas: Dict[str, int] = {}          # firma -> nº de veces vista
_MAX_CADENA = 120                     # a partir de aqui, se resume


def activa() -> bool:
    return _ACTIVA


def _limpiar(valor: Any, prof: int = 0) -> Any:
    """Sustituye binarios y cadenas largas por un marcador con su tamano."""
    if prof > 6:
        return '«…profundidad maxima…»'
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return f'«binario: {len(valor)} bytes»'
    if isinstance(valor, str):
        if len(valor) > _MAX_CADENA:
            return f'«cadena de {len(valor)} caracteres»'
        return valor
    if isinstance(valor, dict):
        return {k: _limpiar(v, prof + 1) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        # Con una muestra de los 3 primeros basta para deducir el esquema.
        rec = [_limpiar(v, prof + 1) for v in list(valor)[:3]]
        if len(valor) > 3:
            rec.append(f'«… y {len(valor) - 3} mas»')
        return rec
    return valor


def _firma(valor: Any, prof: int = 0) -> str:
    """Huella de la ESTRUCTURA (claves y tipos), ignorando los valores.

    Dos frames distintos de la misma camara comparten firma; un mensaje con
    una clave nueva genera una firma nueva y por tanto se guarda."""
    if prof > 6:
        return '…'
    if isinstance(valor, dict):
        return '{' + ','.join(
            f'{k}:{_firma(v, prof + 1)}' for k, v in sorted(valor.items())) + '}'
    if isinstance(valor, (list, tuple)):
        return f'[{_firma(valor[0], prof + 1) if valor else ""}]'
    return type(valor).__name__


def registrar(direccion: str, tipo_cliente: str, payload: Any) -> None:
    """Guarda una muestra si esta forma de mensaje no se habia visto.

    `direccion` es 'entrante' o 'saliente'. Nunca lanza: si algo falla, la
    captura se pierde pero el servidor sigue."""
    if not _ACTIVA:
        return
    try:
        firma = hashlib.sha256(
            f'{direccion}|{tipo_cliente}|{_firma(payload)}'.encode()
        ).hexdigest()[:16]

        with _lock:
            visto = _vistas.get(firma, 0)
            _vistas[firma] = visto + 1
            if visto:                      # ya teniamos una muestra de esta forma
                return

        os.makedirs(_DESTINO, exist_ok=True)
        registro = {
            'firma': firma,
            'direccion': direccion,
            'tipo_cliente': tipo_cliente,
            'capturado': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'claves_raiz': sorted(payload.keys())
                           if isinstance(payload, dict) else None,
            'muestra': _limpiar(payload),
        }
        with open(os.path.join(_DESTINO, 'payloads.jsonl'), 'a',
                  encoding='utf-8') as f:
            f.write(json.dumps(registro, ensure_ascii=False, default=str) + '\n')
    except Exception:
        pass                               # la captura jamas rompe el servidor


def resumen() -> Dict[str, Any]:
    """Cuantas formas distintas se han visto y con que frecuencia."""
    with _lock:
        return {'formas_distintas': len(_vistas),
                'mensajes_por_forma': dict(_vistas)}
