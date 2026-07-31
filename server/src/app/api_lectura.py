"""
src/app/api_lectura.py — API REST de LECTURA del servidor.

Paso 11 del plan del HITO 2, y lo que desbloquea el HITO 9.

## Por que hace falta

Hoy los dashboards leen el estado del servidor **por dentro**: mismo proceso,
mismas estructuras en memoria, y `dashboard.py` va directo al sistema de
archivos con rutas que conoce de memoria. Por eso `dashboards/` esta vacia: no
se pueden sacar de ahi mientras la unica forma de leer sea estar dentro.

Esta API es la puerta por la que van a leer cuando vivan aparte.

## Reglas

- **Solo lectura.** Ni un `POST`. Los endpoints que modifican algo (vaciar
  detecciones, disparar analisis) se quedan donde estan, en `/dashboard/api/`:
  esto no es un traslado, es una capa nueva y con menos permisos.
- **Prefijo con version** (`/api/v1`). El dia que cambie la forma de una
  respuesta habra un `/api/v2` y los dashboards viejos seguiran vivos, que es
  la misma idea que el `event_version` del contrato.
- **Nada de rutas de disco hacia fuera.** Un `device_id` que llega por la URL
  se sanea antes de tocar el sistema de archivos: acaba siendo un nombre de
  archivo, y ese es justo el motivo de que `slug()` prohiba `..` y las barras.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from . import registro_dispositivos as _registro
from . import validacion_contrato as _validacion

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/v1', tags=['lectura'])

#: `server/src/app/api_lectura.py` -> parents[2] es `server/`.
_RAIZ = Path(__file__).resolve().parents[2]

#: Mismo criterio que `elde_core.ui.identidad_camara.slug`: lo que llegue por
#: la URL tiene que ser inofensivo como nombre de archivo.
_SEGURO = re.compile(r'[^A-Za-z0-9_\-.]')


def _saneado(nombre: str, limite: int = 160) -> str:
    limpio = _SEGURO.sub('', str(nombre or ''))
    while '..' in limpio:
        limpio = limpio.replace('..', '.')
    return limpio.strip('._-')[:limite]


def _salida() -> Path:
    return _RAIZ / 'output'


# ── Dispositivos y sitios ────────────────────────────────────────────────

@router.get('/dispositivos')
def listar_dispositivos(
        site_id: Optional[str] = Query(None, description='filtra por sitio'),
        client_type: Optional[str] = Query(
            None, description='tienda | perimetrales | amazonas | managers'),
) -> Dict[str, Any]:
    """Que camaras conoce el servidor, de haberlas visto enviar frames."""
    filas = _registro.dispositivos(site_id=site_id, client_type=client_type)
    return {'total': len(filas), 'dispositivos': filas}


@router.get('/sitios')
def listar_sitios() -> Dict[str, Any]:
    filas = _registro.sitios()
    return {'total': len(filas), 'sitios': filas}


@router.get('/dispositivos/{device_id}')
def ver_dispositivo(device_id: str) -> Dict[str, Any]:
    ident = _saneado(device_id)
    for fila in _registro.dispositivos():
        if fila['device_id'] == ident:
            return {**fila,
                    'analitica': _hay_analitica(ident),
                    'heatmap': _hay_heatmap(ident)}
    raise HTTPException(status_code=404,
                        detail=f'dispositivo desconocido: {ident}')


# ── Analitica acumulada ──────────────────────────────────────────────────

def _informes(device_id: str) -> List[str]:
    """Los informes de un dispositivo.

    Se llaman `analytics_report_client_<id_conexion>_<device_id>.json`. El
    `id_conexion` cambia en cada sesion, asi que un mismo dispositivo puede
    tener varios: se ordenan por fecha y manda el mas reciente.
    """
    patron = os.path.join(str(_salida()),
                          f'analytics_report_client_*_{device_id}.json')
    return sorted(glob.glob(patron), key=os.path.getmtime, reverse=True)


def _hay_analitica(device_id: str) -> bool:
    return bool(_informes(device_id))


def _hay_heatmap(device_id: str) -> bool:
    return (_salida() / 'heatmap' / f'{device_id}.png').is_file()


@router.get('/analitica/{device_id}')
def analitica_de_dispositivo(device_id: str) -> Dict[str, Any]:
    """El informe mas reciente de esa camara."""
    ident = _saneado(device_id)
    informes = _informes(ident)
    if not informes:
        raise HTTPException(
            status_code=404,
            detail=f'sin analitica para {ident}. Puede que la camara aun no '
                   'haya enviado frames en esta instalacion.')
    try:
        with open(informes[0], encoding='utf-8') as f:
            datos = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning('informe ilegible %s: %s', informes[0], exc)
        raise HTTPException(status_code=500,
                            detail='el informe existe pero no se pudo leer')
    return {
        'device_id': ident,
        'archivo': os.path.basename(informes[0]),
        'sesiones_disponibles': len(informes),
        'datos': datos,
    }


@router.get('/heatmaps')
def listar_heatmaps() -> Dict[str, Any]:
    """Mapas de calor disponibles, uno por dispositivo.

    Se cruzan con el registro para decir tambien **de quien** es cada uno. Los
    huerfanos —heatmap sin dispositivo conocido— son casi siempre de antes de
    H-11, cuando el nombre del archivo era el uuid de sesion.
    """
    carpeta = _salida() / 'heatmap'
    if not carpeta.is_dir():
        return {'total': 0, 'heatmaps': []}
    conocidos = {f['device_id']: f for f in _registro.dispositivos()}
    fuera = []
    for png in sorted(carpeta.glob('*.png')):
        ident = png.stem
        fila = conocidos.get(ident)
        fuera.append({
            'device_id': ident,
            'bytes': png.stat().st_size,
            'modificado': int(png.stat().st_mtime),
            'site_id': fila['site_id'] if fila else None,
            'camera_name': fila['camera_name'] if fila else None,
            'huerfano': fila is None,
            'url': f'/dashboard/img/heatmap/{ident}.png',
        })
    return {'total': len(fuera),
            'huerfanos': sum(1 for h in fuera if h['huerfano']),
            'heatmaps': fuera}


# ── Estado ───────────────────────────────────────────────────────────────

@router.get('/estado')
def estado() -> Dict[str, Any]:
    """Lo que un dashboard necesita saber del servidor sin estar dentro de el."""
    return {
        'contrato': _validacion.resumen(),
        'registro': _registro.resumen(),
    }


# ── Resumen para los dashboards (HITO 9) ─────────────────────────────────

@router.get('/resumen')
def resumen() -> Dict[str, Any]:
    """KPIs y distribuciones (visitantes, genero, edad, permanencia).

    Delega en el MISMO calculo que `/dashboard/api/summary` en vez de
    reimplementarlo: una sola fuente de verdad para los numeros, dos puertas
    para leerlos. El import es perezoso porque `dashboard.py` arrastra la
    configuracion de analitica y este modulo debe poder importarse solo.
    """
    from .dashboard import dashboard_summary
    datos = dashboard_summary()
    return {'resumen': datos, 'registro': _registro.resumen()}


@router.get('/paneles')
def paneles() -> Dict[str, Any]:
    """Donde viven los paneles auxiliares, para que NINGUNA pagina estatica
    lleve un puerto escrito (regla 6): el navegador pregunta aqui y construye
    los enlaces con el host por el que ya esta hablando.

    Cada entrada se resuelve con tolerancia: si un panel no esta disponible en
    esta instalacion, aparece con `puerto: None` y el dashboard lo oculta.
    """
    fuera: Dict[str, Any] = {
        # El dashboard general de visitantes vive en ESTE mismo proceso.
        'visitantes': {'ruta': '/dashboard', 'puerto': None},
    }
    try:
        from .dashboard_tienda import PUERTO_TIENDA
        fuera['tienda'] = {'ruta': '/', 'puerto': PUERTO_TIENDA}
    except Exception:
        fuera['tienda'] = {'ruta': '/', 'puerto': None}
    try:
        # Importable sin tocar sys.path: app.py ya importa este paquete, asi
        # que en el proceso del servidor esta resuelto o no existe.
        from vigilante_amazonas import config as _vigilante
        fuera['vigilante'] = {'ruta': '/', 'puerto': _vigilante.PUERTO_API}
    except Exception:
        fuera['vigilante'] = {'ruta': '/', 'puerto': None}
    return {'paneles': fuera}
