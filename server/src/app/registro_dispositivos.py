"""
src/app/registro_dispositivos.py — Que camaras existen, segun lo visto.

Paso 11 del plan del HITO 2. Hasta ahora el servidor no sabia **que
dispositivos hay**: procesaba frames sueltos y escribia archivos cuyo nombre
salia del `camera_id` que trajera el mensaje. Se puede ver en `output/`:

    analytics_log_18dbc565-37d4-4c2e-ad92-48df7b9f41a5.jsonl   <- uuid por sesion
    analytics_report_client_2589894109200_win-iVMS-4200.json   <- id estable

Los primeros son de antes de H-11: cada arranque del cliente inventaba una
camara nueva. **Por eso este registro no se podia construir antes**: habria
acumulado un dispositivo distinto por sesion, que es exactamente lo que se
queria evitar. Con `device_id` ya estable (H-11 cerrado en los cuatro
clientes), acumular tiene sentido.

## Que guarda

Una fila por `(site_id, device_id)`: quien lo envia (`client_type`), con que
pipeline, como se llama la camara para las personas, cuando se vio por primera
y ultima vez, y cuantos frames trajo.

## Que NO hace

No decide nada ni toca el dominio. Solo observa el mismo mensaje que ya pasa
por la validacion, y **nunca lanza**: un fallo aqui no puede tumbar una
conexion de video. Es la misma regla que siguen `captura_contrato` y
`validacion_contrato`.

## Persistencia en archivo, no en base de datos

El HITO 2 dejo la base de datos como decision de este hito. Se mantiene el
archivo: son decenas de dispositivos, no millones, la escritura esta
amortiguada y meter un motor de base de datos aqui seria la segunda migracion
grande a la vez. Cuando el volumen lo pida, este modulo es el unico sitio que
habria que cambiar.

Configuracion (regla 6):

    ELDE_REGISTRO_DISPOSITIVOS   ruta del archivo (por defecto
                                 <server>/output/registro_dispositivos.json)
    ELDE_REGISTRO_SEGUNDOS       cada cuanto se vuelca a disco (por defecto 30)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: `server/src/app/registro_dispositivos.py` -> parents[2] es `server/`.
_RAIZ_SERVIDOR = Path(__file__).resolve().parents[2]

SITE_ID_POR_DEFECTO = (os.getenv('ELDE_SITE_ID', 'sitio-unico').strip()
                       or 'sitio-unico')


def _ruta() -> Path:
    explicita = (os.getenv('ELDE_REGISTRO_DISPOSITIVOS') or '').strip()
    if explicita:
        return Path(explicita)
    return _RAIZ_SERVIDOR / 'output' / 'registro_dispositivos.json'


def _cada_cuanto() -> float:
    try:
        v = float((os.getenv('ELDE_REGISTRO_SEGUNDOS') or '').strip())
    except ValueError:
        return 30.0
    return v if v > 0 else 30.0


try:
    from elde_core.contracts.compat import CLIENTE_POR_PIPELINE
    _DEDUCIBLE = True
except Exception as exc:                    # el servidor arranca igual
    CLIENTE_POR_PIPELINE = {}
    _DEDUCIBLE = False
    logger.warning('contrato no disponible (%s); el registro no podra deducir '
                   'el client_type de los clientes que no lo declaren', exc)

_lock = threading.Lock()
_dispositivos: Dict[str, Dict[str, Any]] = {}
_ultimo_volcado = 0.0
_sucio = False


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _texto(valor: Any, limite: int = 96) -> str:
    return str(valor or '').strip()[:limite]


def _client_type(mensaje: Dict[str, Any], pipeline: str) -> str:
    """Quien envia. Se prefiere lo DECLARADO a lo deducido.

    La deduccion por pipeline es una aproximacion que falla justo en los
    clientes multimodo: `Perimetrales` lanzado desde el gestor de ventanas se
    etiquetaba como si fuera el cliente perimetral. Desde el HITO 3 los cuatro
    lo declaran; la deduccion solo cubre a quien no se haya actualizado.
    """
    declarado = _texto(mensaje.get('client_type'), 32).lower()
    if declarado:
        return declarado
    deducido = CLIENTE_POR_PIPELINE.get(pipeline)
    return getattr(deducido, 'value', str(deducido)) if deducido else 'desconocido'


def anotar(mensaje: Dict[str, Any], type_inference: str = '') -> None:
    """Anota un frame entrante. Nunca lanza."""
    try:
        datos = mensaje.get('data')
        if not isinstance(datos, dict):
            return
        device_id = _texto(datos.get('camera_id'))
        if not device_id:
            return

        pipeline = _texto(mensaje.get('type_inference') or type_inference, 48)
        site_id = _texto(mensaje.get('site_id'), 64) or SITE_ID_POR_DEFECTO
        clave = f'{site_id}/{device_id}'
        ahora = _ahora()

        with _lock:
            fila = _dispositivos.get(clave)
            if fila is None:
                fila = {
                    'device_id': device_id,
                    'site_id': site_id,
                    'client_type': _client_type(mensaje, pipeline),
                    'pipelines': [],
                    'camera_name': '',
                    'primera_vez': ahora,
                    'ultima_vez': ahora,
                    'frames': 0,
                }
                _dispositivos[clave] = fila
                logger.info('dispositivo nuevo: %s (%s, %s)',
                            clave, fila['client_type'], pipeline or 's/pipeline')

            fila['ultima_vez'] = ahora
            fila['frames'] += 1
            if pipeline and pipeline not in fila['pipelines']:
                fila['pipelines'].append(pipeline)
            nombre = _texto(datos.get('camera_name'), 64)
            if nombre:
                fila['camera_name'] = nombre
            # Si el cliente empieza a declararse, deja de valer lo deducido.
            declarado = _texto(mensaje.get('client_type'), 32).lower()
            if declarado and fila['client_type'] != declarado:
                fila['client_type'] = declarado

            global _sucio
            _sucio = True

        _volcar_si_toca()
    except Exception as exc:                 # el registro jamas rompe nada
        logger.debug('registro_dispositivos.anotar: %s', exc)


def _volcar_si_toca() -> None:
    global _ultimo_volcado
    ahora = time.time()
    if ahora - _ultimo_volcado < _cada_cuanto():
        return
    _ultimo_volcado = ahora
    volcar()


def volcar() -> bool:
    """Escribe el registro a disco. Devuelve si se escribio algo.

    Escritura atomica: primero a un temporal y luego `replace`. Si el proceso
    muere a media escritura, el archivo anterior sigue entero — perder el
    registro por un corte seria perder el historico de que camaras existen.
    """
    global _sucio
    with _lock:
        if not _sucio:
            return False
        instantanea = list(_dispositivos.values())
        _sucio = False
    try:
        destino = _ruta()
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_suffix('.json.tmp')
        contenido = {
            'generado': _ahora(),
            'total': len(instantanea),
            'dispositivos': sorted(
                instantanea, key=lambda d: (d['site_id'], d['device_id'])),
        }
        temporal.write_text(
            json.dumps(contenido, ensure_ascii=False, indent=2),
            encoding='utf-8')
        temporal.replace(destino)
        return True
    except OSError as exc:
        logger.warning('no se pudo volcar el registro de dispositivos: %s', exc)
        with _lock:
            _sucio = True          # que lo reintente el siguiente
        return False


def cargar() -> int:
    """Recupera el registro del disco al arrancar. Devuelve cuantos leyo."""
    try:
        origen = _ruta()
        if not origen.is_file():
            return 0
        datos = json.loads(origen.read_text(encoding='utf-8'))
        filas = datos.get('dispositivos') or []
        with _lock:
            for fila in filas:
                if not isinstance(fila, dict):
                    continue
                device_id = _texto(fila.get('device_id'))
                if not device_id:
                    continue
                site_id = _texto(fila.get('site_id'), 64) or SITE_ID_POR_DEFECTO
                fila.setdefault('pipelines', [])
                fila.setdefault('frames', 0)
                _dispositivos[f'{site_id}/{device_id}'] = fila
        logger.info('registro de dispositivos: %d recuperados de %s',
                    len(filas), origen)
        return len(filas)
    except (OSError, ValueError) as exc:
        logger.warning('no se pudo leer el registro de dispositivos: %s', exc)
        return 0


def dispositivos(site_id: Optional[str] = None,
                 client_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Los dispositivos conocidos, con filtros opcionales."""
    with _lock:
        filas = [dict(f) for f in _dispositivos.values()]
    if site_id:
        filas = [f for f in filas if f['site_id'] == site_id]
    if client_type:
        filas = [f for f in filas if f['client_type'] == client_type]
    return sorted(filas, key=lambda d: (d['site_id'], d['device_id']))


def sitios() -> List[Dict[str, Any]]:
    """Los sitios vistos, con cuantos dispositivos tiene cada uno."""
    agregado: Dict[str, Dict[str, Any]] = {}
    for fila in dispositivos():
        s = agregado.setdefault(fila['site_id'], {
            'site_id': fila['site_id'], 'dispositivos': 0,
            'client_types': [], 'ultima_vez': ''})
        s['dispositivos'] += 1
        if fila['client_type'] not in s['client_types']:
            s['client_types'].append(fila['client_type'])
        if fila['ultima_vez'] > s['ultima_vez']:
            s['ultima_vez'] = fila['ultima_vez']
    return sorted(agregado.values(), key=lambda s: s['site_id'])


def resumen() -> Dict[str, Any]:
    """Para `/health`."""
    filas = dispositivos()
    inestables = [f['device_id'] for f in filas if _parece_uuid(f['device_id'])]
    return {
        'dispositivos': len(filas),
        'sitios': len({f['site_id'] for f in filas}),
        'archivo': str(_ruta()),
        # Si esto deja de ser 0, alguien volvio a mandar el uuid de sesion
        # como camera_id y H-11 ha reaparecido.
        'ids_inestables': len(inestables),
        'ejemplo_inestable': inestables[0] if inestables else None,
    }


def _parece_uuid(device_id: str) -> bool:
    """Un `device_id` con pinta de `uuid4` es la regresion de H-11.

    Los ids estables empiezan por `dvr-`, `win-` o `box-`. Un uuid4 en canonico
    mide 36 caracteres con guiones en las posiciones 8, 13, 18 y 23.
    """
    if len(device_id) != 36:
        return False
    return [i for i, c in enumerate(device_id) if c == '-'] == [8, 13, 18, 23]
