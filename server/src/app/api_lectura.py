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
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

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
        if png.name.endswith('.tmp.png'):
            continue
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
            'horas_historico': _horas_de_historico(ident),
            'historico': f'/api/v1/heatmaps/{ident}/historico',
        })
    return {'total': len(fuera),
            'huerfanos': sum(1 for h in fuera if h['huerfano']),
            'heatmaps': fuera}


# ── Historico de mapas de calor (1-ago-2026) ─────────────────────────────
#
# El acumulador guarda un snapshot POR HORA en heatmap/history/<device>/
# (PNG + JSON + grilla cruda .npz, con retencion configurada). Estos
# endpoints lo exponen para que los dashboards muestren "como se movio la
# tienda ayer a las 18h" y no solo el acumulado vigente.

def _carpeta_historico(ident: str) -> Path:
    return _salida() / 'heatmap' / 'history' / ident


def _horas_de_historico(ident: str) -> int:
    try:
        return sum(1 for f in _carpeta_historico(ident).glob('*.png')
                   if not f.name.endswith('.tmp.png'))
    except OSError:
        return 0


@router.get('/heatmaps/{device_id}/historico')
def historico_de_heatmap(device_id: str) -> Dict[str, Any]:
    """Las horas guardadas de esa camara, mas recientes primero."""
    ident = _saneado(device_id)
    carpeta = _carpeta_historico(ident)
    if not carpeta.is_dir():
        return {'device_id': ident, 'total': 0, 'horas': []}
    filas = []
    for png in sorted(carpeta.glob('*.png'), reverse=True):
        if png.name.endswith('.tmp.png'):
            continue
        sello = png.stem                      # AAAA-MM-DD_HH
        meta: Dict[str, Any] = {}
        try:
            with open(png.with_suffix('.json'), encoding='utf-8') as f:
                meta = json.load(f) or {}
        except (OSError, ValueError):
            pass
        filas.append({
            'stamp': sello,
            'muestras': meta.get('muestras'),
            'zonas_calientes': meta.get('zonas_calientes'),
            'bytes': png.stat().st_size,
            'url': f'/api/v1/heatmaps/{ident}/historico/{sello}.png',
        })
    return {'device_id': ident, 'total': len(filas), 'horas': filas}


@router.get('/heatmaps/{device_id}/historico/{sello}.png')
def foto_de_historico(device_id: str, sello: str):
    """Sirve el PNG de una hora del historico. Sin salidas de carpeta."""
    ident = _saneado(device_id)
    limpio = _saneado(sello)
    carpeta = _carpeta_historico(ident).resolve()
    destino = (carpeta / f'{limpio}.png').resolve()
    if carpeta not in destino.parents or not destino.is_file():
        raise HTTPException(status_code=404, detail='no encontrada')
    return FileResponse(str(destino))


# ── Alertas de perimetrales ──────────────────────────────────────────────
#
# Las escribe el CLIENTE perimetral al recibir cada alerta: un .jpg con un
# sidecar .json al lado (clase, evento, camara, epoch, descripcion...). El
# panel :5333 ya las muestra; esta capa las expone ademas por /api/v1 para el
# dashboard de producto, con lo que a aquel le falta: fechas, texto libre y
# paginacion.

_IMAGENES_ALERTA = ('.jpg', '.jpeg', '.png')

#: Cache incremental del indice de sidecars: releer ~1.300 JSON en cada
#: peticion castiga al servidor de inferencia, que es quien sirve esto.
_CACHE_ALERTAS: Dict[str, Any] = {
    'marca': 0.0, 'carpeta': '', 'por_archivo': {}, 'filas': []}


def _carpeta_alertas() -> Path:
    """La MISMA carpeta que usa el panel :5333 (una sola fuente de verdad).

    Prioridad: `VIGILANTE_SCREENSHOTS` (regla 6, y lo que permite cliente y
    servidor en maquinas distintas) > el resolutor de vigilante > la ruta del
    monorepo. El import es perezoso: este modulo debe poder importarse sin el
    paquete vigilante (pruebas, instalaciones parciales).
    """
    explicita = (os.getenv('VIGILANTE_SCREENSHOTS') or '').strip()
    if explicita:
        return Path(explicita)
    try:
        from vigilante_amazonas.web.panel import carpeta_screenshots
        return carpeta_screenshots()
    except Exception:
        return _RAIZ.parent / 'clients' / 'perimetrales' / 'screenshots'


def _llano(texto: Any) -> str:
    """Minusculas y sin acentos: 'CAMIÓN' y 'camion' deben ser lo mismo."""
    plano = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in plano if not unicodedata.combining(c)).lower()


def _epoch_de(cadena: str, fin_de_dia: bool = False) -> float:
    """'2026-07-31', '2026-07-31 17:53' o con segundos -> epoch local.

    Con solo la fecha, `fin_de_dia` decide si es el principio (desde) o el
    final (hasta) del dia: asi `desde=hasta=2026-07-31` cubre el dia entero.
    """
    limpia = (cadena or '').strip().replace('T', ' ')
    for formato in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            momento = datetime.strptime(limpia, formato)
            epoch = momento.timestamp()
            if formato == '%Y-%m-%d' and fin_de_dia:
                epoch += 86399.0
            return epoch
        except ValueError:
            continue
    raise HTTPException(
        status_code=422,
        detail=f'fecha invalida: {cadena!r} (use AAAA-MM-DD u '
               'AAAA-MM-DD HH:MM[:SS])')


def _fila_alerta(carpeta: Path, nombre: str) -> Dict[str, Any]:
    ruta = carpeta / nombre
    try:
        with open(ruta.with_suffix('.json'), encoding='utf-8') as f:
            meta = json.load(f) or {}
    except (OSError, ValueError):
        meta = {}
    return {
        'archivo': nombre,
        'url': f'/api/v1/alertas/foto/{nombre}',
        'clase': meta.get('clase') or '',
        'clase_gruesa': meta.get('clase_gruesa') or '',
        'evento': meta.get('evento') or '',
        'camara': meta.get('camara') or '',
        'timestamp': meta.get('timestamp') or '',
        'epoch': meta.get('epoch'),
        'global_id': meta.get('global_id') or '',
        'permanencia_s': meta.get('permanencia_s'),
        'descripcion': meta.get('descripcion') or '',
        # Local de la camara (3-ago-2026): los sidecars nuevos lo traen; los
        # viejos no, y queda vacio.
        'establecimiento': meta.get('establecimiento') or '',
        # Sin sidecar no hay clase ni fecha fiables; se marca para que la UI
        # y los totales no lo cuenten como si se supiera.
        'sin_metadatos': not bool(meta),
    }


def _filas_alertas() -> List[Dict[str, Any]]:
    """Indice completo, mas recientes primero. Solo reparsea lo que cambio."""
    ttl = float(os.getenv('ELDE_ALERTAS_CACHE_SEG', '5'))
    carpeta = _carpeta_alertas()
    ahora = time.time()
    if (_CACHE_ALERTAS['carpeta'] == str(carpeta)
            and ahora - _CACHE_ALERTAS['marca'] < ttl):
        return _CACHE_ALERTAS['filas']
    if not carpeta.is_dir():
        _CACHE_ALERTAS.update(marca=ahora, carpeta=str(carpeta),
                              por_archivo={}, filas=[])
        return []
    try:
        nombres = [n for n in os.listdir(carpeta)
                   if n.lower().endswith(_IMAGENES_ALERTA)]
    except OSError:
        return _CACHE_ALERTAS['filas']
    conocidos: Dict[str, Any] = _CACHE_ALERTAS['por_archivo']
    vigentes: Dict[str, Any] = {}
    for nombre in nombres:
        try:
            mtime = os.path.getmtime(carpeta / nombre)
        except OSError:
            continue                      # borrado entre el listado y el stat
        previo = conocidos.get(nombre)
        if previo and previo[0] == mtime:
            vigentes[nombre] = previo
        else:
            vigentes[nombre] = (mtime, _fila_alerta(carpeta, nombre))
    # El nombre empieza por AAAAMMDD_HHMMSS: orden lexicografico inverso ==
    # cronologico inverso, tambien para las filas sin sidecar (sin epoch).
    filas = [vigentes[n][1] for n in sorted(vigentes, reverse=True)]
    _CACHE_ALERTAS.update(marca=ahora, carpeta=str(carpeta),
                          por_archivo=vigentes, filas=filas)
    return filas


#: Etiqueta del segmento de camaras que NO tienen local asignado.
SIN_LOCAL = '(sin local)'


def _locales_por_camara() -> Dict[str, str]:
    """{camera_name: establecimiento} segun el registro de dispositivos.

    Es el respaldo para las alertas VIEJAS (sidecar sin `establecimiento`):
    la camara hereda su local ACTUAL, que es como el operador piensa en
    ellas («las de la Carpinteria»)."""
    fuera: Dict[str, str] = {}
    for disp in _registro.dispositivos():
        nombre = str(disp.get('camera_name') or '').strip()
        local = str(disp.get('establecimiento') or '').strip()
        if nombre and local:
            fuera[nombre] = local
    return fuera


def _local_de(fila: Dict[str, Any], mapa: Dict[str, str]) -> str:
    return fila.get('establecimiento') or mapa.get(fila.get('camara') or '', '')


@router.get('/alertas')
def listar_alertas(
        evento: Optional[str] = Query(
            None, description='llegada | salida | permanencia | merodeo'),
        clase: Optional[str] = Query(
            None, description='CARRO, MOTO, PERSONA... (sin distinguir '
                              'mayusculas ni acentos)'),
        clase_gruesa: Optional[str] = Query(
            None, description='persona | vehiculo'),
        camara: Optional[str] = Query(None),
        establecimiento: Optional[str] = Query(
            None, description=f'local de la camara; "{SIN_LOCAL}" para las '
                              'camaras sin local asignado'),
        desde: Optional[str] = Query(
            None, description='AAAA-MM-DD u AAAA-MM-DD HH:MM[:SS]'),
        hasta: Optional[str] = Query(None, description='igual que desde'),
        q: Optional[str] = Query(
            None, description='texto libre sobre descripcion, clase, camara, '
                              'evento y global_id'),
        limite: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Buscador de alertas del perimetro, con facetas para el desglose.

    Las facetas se calculan sobre el conjunto FILTRADO: son los numeros que
    acompañan a lo que se esta viendo. Para los totales globales se llama sin
    filtros. Cada alerta sale con `local` (su establecimiento EFECTIVO: el del
    sidecar o, si es vieja, el actual de su camara segun el registro).
    """
    mapa_locales = _locales_por_camara()
    filas = _filas_alertas()
    if evento:
        buscado = _llano(evento)
        filas = [f for f in filas if _llano(f['evento']) == buscado]
    if clase:
        buscado = _llano(clase)
        filas = [f for f in filas if _llano(f['clase']) == buscado]
    if clase_gruesa:
        buscado = _llano(clase_gruesa)
        filas = [f for f in filas if _llano(f['clase_gruesa']) == buscado]
    if camara:
        buscado = _llano(camara)
        filas = [f for f in filas if _llano(f['camara']) == buscado]
    if establecimiento:
        if establecimiento == SIN_LOCAL:
            filas = [f for f in filas if not _local_de(f, mapa_locales)]
        else:
            buscado = _llano(establecimiento)
            filas = [f for f in filas
                     if _llano(_local_de(f, mapa_locales)) == buscado]
    if desde is not None:
        corte = _epoch_de(desde)
        filas = [f for f in filas if (f['epoch'] or 0) >= corte]
    if hasta is not None:
        corte = _epoch_de(hasta, fin_de_dia=True)
        filas = [f for f in filas if f['epoch'] is not None
                 and f['epoch'] <= corte]
    if q:
        buscado = _llano(q)
        filas = [f for f in filas if buscado in _llano(
            ' '.join((f['descripcion'], f['clase'], f['camara'], f['evento'],
                      f['global_id'], f['archivo'])))]
    return {
        'total': len(filas),
        'total_capturas': len(_filas_alertas()),
        'offset': offset,
        'limite': limite,
        'facetas': {
            'por_clase': dict(Counter(
                f['clase'] for f in filas if f['clase']).most_common()),
            'por_evento': dict(Counter(
                f['evento'] for f in filas if f['evento']).most_common()),
            'por_camara': dict(Counter(
                f['camara'] for f in filas if f['camara']).most_common()),
            'por_establecimiento': dict(Counter(
                _local_de(f, mapa_locales) or SIN_LOCAL
                for f in filas).most_common()),
            'por_clase_gruesa': dict(Counter(
                f['clase_gruesa'] for f in filas
                if f['clase_gruesa']).most_common()),
        },
        # Copias: las filas cacheadas no se mutan (el cache es compartido).
        'alertas': [{**f, 'local': _local_de(f, mapa_locales)}
                    for f in filas[offset:offset + limite]],
    }


@router.get('/alertas/foto/{archivo}')
def foto_de_alerta(archivo: str):
    """Sirve la foto de una alerta. Bloquea cualquier salto de directorio."""
    nombre = _saneado(archivo, 200)
    if not nombre.lower().endswith(_IMAGENES_ALERTA):
        raise HTTPException(status_code=404, detail='no encontrada')
    carpeta = _carpeta_alertas().resolve()
    destino = (carpeta / nombre).resolve()
    if carpeta not in destino.parents or not destino.is_file():
        raise HTTPException(status_code=404, detail='no encontrada')
    return FileResponse(str(destino))


# ── Capturas de personas (tienda / amazonas) ─────────────────────────────

@router.get('/capturas')
def listar_capturas(
        limite: int = Query(60, ge=1, le=500)) -> Dict[str, Any]:
    """Capturas de personas con genero/edad, mas recientes primero.

    Delega en el MISMO calculo que `/dashboard/api/captures` (una sola fuente
    de verdad) y añade las URL de imagen para que la pagina no tenga que
    conocer las rutas del dashboard viejo.
    """
    from .dashboard import dashboard_captures
    datos = dashboard_captures(limit=limite)
    for c in datos.get('capturas') or []:
        stem = _saneado(c.get('stem') or '')
        c['url'] = f'/dashboard/img/capture/{stem}.jpg'
        c['url_miniatura'] = (f'/dashboard/img/capface/{stem}.jpg'
                              if c.get('has_face') else c['url'])
    return datos


# ── Ranking de pasillos (trafico por camara) ─────────────────────────────

@router.get('/ranking-pasillos')
def ranking_pasillos(
        site_id: Optional[str] = Query(None),
        client_type: Optional[str] = Query(
            None, description='tienda | perimetrales | amazonas | managers'),
) -> Dict[str, Any]:
    """Que camara (pasillo) ve mas trafico y cual menos.

    Cruza el registro de dispositivos con el informe de analitica mas
    reciente de cada uno. Las camaras sin analitica van al final con
    `entradas: null`: existir existen, pero aun no midieron nada.
    """
    filas: List[Dict[str, Any]] = []
    for disp in _registro.dispositivos(site_id=site_id,
                                       client_type=client_type):
        ident = disp['device_id']
        entradas = visitantes = permanencia = None
        informes = _informes(ident)
        if informes:
            try:
                with open(informes[0], encoding='utf-8') as f:
                    datos = json.load(f) or {}
                entradas = int(datos.get('total_entradas') or 0)
                visitantes = int(datos.get('visitantes_unicos') or 0)
                permanencia = float(datos.get('permanencia_media_s') or 0.0)
            except (OSError, ValueError):
                logger.warning('informe ilegible para %s', ident)
        filas.append({
            'device_id': ident,
            'camera_name': disp.get('camera_name'),
            'site_id': disp.get('site_id'),
            'client_type': disp.get('client_type'),
            'frames': disp.get('frames'),
            'entradas': entradas,
            'visitantes_unicos': visitantes,
            'permanencia_media_s': permanencia,
            'heatmap': _hay_heatmap(ident),
        })
    con_datos = [f for f in filas if f['entradas'] is not None]
    con_datos.sort(key=lambda f: (f['entradas'], f['visitantes_unicos'] or 0),
                   reverse=True)
    sin_datos = [f for f in filas if f['entradas'] is None]
    return {
        'total': len(filas),
        'ranking': con_datos + sin_datos,
        'mas_concurrido': con_datos[0] if con_datos else None,
        # Con una sola camara medida no hay "menos concurrido": seria la misma.
        'menos_concurrido': con_datos[-1] if len(con_datos) > 1 else None,
    }


# ── Estado ───────────────────────────────────────────────────────────────

def _resumen_whatsapp() -> Dict[str, Any]:
    """Contadores del reenvio a WhatsApp.

    El envio era una CAJA NEGRA: si no llegaba un mensaje no habia forma de
    distinguir "el interruptor esta apagado" de "no hubo alertas" o "el bot
    fallo". Los tres casos se ven aqui (H-25).
    """
    try:
        from vigilante_amazonas.servicios.emisor_whatsapp import get_emisor_whatsapp
        em = get_emisor_whatsapp()
        return {
            'disponible': True,
            'enviados': em.enviados,
            'descartados_antiflood': em.descartados,
            'fallidos': em.fallidos,
        }
    except Exception as exc:
        return {'disponible': False, 'motivo': str(exc)[:120]}


@router.get('/estado')
def estado() -> Dict[str, Any]:
    """Lo que un dashboard necesita saber del servidor sin estar dentro de el."""
    return {
        'contrato': _validacion.resumen(),
        'registro': _registro.resumen(),
        'whatsapp': _resumen_whatsapp(),
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
    # El dashboard propio de tienda (9030) se retiro el 31-jul-2026: lo
    # sustituye /dashboards/tienda/. Se publica con puerto None para que
    # ninguna pagina pinte un enlace a un puerto que ya no escucha.
    fuera['tienda'] = {'ruta': '/dashboards/tienda/', 'puerto': None}
    try:
        # Importable sin tocar sys.path: app.py ya importa este paquete, asi
        # que en el proceso del servidor esta resuelto o no existe.
        from vigilante_amazonas import config as _vigilante
        fuera['vigilante'] = {'ruta': '/', 'puerto': _vigilante.PUERTO_API}
    except Exception:
        fuera['vigilante'] = {'ruta': '/', 'puerto': None}
    return {'paneles': fuera}
