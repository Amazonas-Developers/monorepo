"""
src/app/busqueda_vlm.py — búsqueda en lenguaje natural sobre las fotos (FASE 6
de los dashboards de producto).

«Búscame el carro rojo» desde el dashboard: el texto llega aquí, se decide el
motor (grounding YOLO-World si la consulta es de detección, VQA Qwen2.5-VL si
es pregunta abierta) y se recorren las N fotos más recientes del ámbito
(alertas del perímetro o capturas de personas) en un HILO de fondo. El
navegador crea el trabajo y SONDEA su estado: la VLM tarda 20-30 s por imagen
y bloquear al servidor de inferencia no es una opción.

## Por qué vive en /dashboard/api y no en /api/v1

`/api/v1` es SOLO lectura por regla (HITO 8). Encender el buscador y lanzar
una búsqueda son acciones, así que van donde ya viven las acciones del
dashboard. Las páginas leen datos de /api/v1 y accionan aquí.

## La GPU es una

El mismo silicio atiende la inferencia en vivo. Por eso: una búsqueda a la
vez (409 si hay otra en curso), interruptor apagado por defecto (el botón
«VLM» del dashboard lo enciende), y topes de fotos distintos por motor:
YOLO-World revisa decenas en segundos, el VQA solo unas pocas.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/dashboard/api', tags=['vlm-busqueda'])

#: `server/src/app/busqueda_vlm.py` -> parents[2] es `server/`.
_RAIZ = Path(__file__).resolve().parents[2]


# ── Interruptor (el botón «VLM» de los dashboards) ───────────────────────

def _ruta_interruptor() -> Path:
    propia = (os.getenv('ELDE_VLM_BUSCADOR_ARCHIVO') or '').strip()
    return Path(propia) if propia else _RAIZ / 'output' / 'vlm_buscador.txt'


def buscador_activo() -> bool:
    """Apagado por defecto: la GPU es del tiempo real hasta que alguien
    enciende el buscador a sabiendas."""
    try:
        return _ruta_interruptor().read_text(encoding='utf-8').strip() == 'on'
    except OSError:
        return False


def _fijar_buscador(activo: bool) -> None:
    ruta = _ruta_interruptor()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text('on' if activo else 'off', encoding='utf-8')


# ── De la consulta al motor ──────────────────────────────────────────────
#
# YOLO-World codifica las clases con CLIP, que entiende MUCHO mejor inglés,
# y el router extrae mal «búscame» (el acento rompe su substring). Aquí se
# normaliza sin acentos, se detecta la intención y se traduce el vocabulario
# del dominio; lo que no esté en el diccionario viaja tal cual.

_PALABRAS_DETECCION = (
    'buscame', 'busca', 'buscar', 'detecta', 'detectar', 'encuentra',
    'encontrar', 'localiza', 'localizar', 'ubica', 'ubicar', 'muestrame',
    'marca', 'senala', 'cuantos', 'cuantas', 'find', 'detect', 'locate',
    'todas las', 'todos los',
)

_RELLENO = {'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas', 'de',
            'del', 'me', 'al', 'a', 'que', 'hay', 'en', 'todos', 'todas',
            'todo', 'toda', 'the', 'all', 'y', 'o', 'con'}

_VOCABULARIO = {
    'carro': 'car', 'carros': 'cars', 'auto': 'car', 'autos': 'cars',
    'coche': 'car', 'coches': 'cars', 'camioneta': 'pickup truck',
    'camionetas': 'pickup trucks', 'camion': 'truck', 'camiones': 'trucks',
    'moto': 'motorcycle', 'motos': 'motorcycles',
    'motocicleta': 'motorcycle', 'bicicleta': 'bicycle',
    'persona': 'person', 'personas': 'people', 'gente': 'people',
    'hombre': 'man', 'hombres': 'men', 'mujer': 'woman', 'mujeres': 'women',
    'nino': 'child', 'nina': 'child', 'ninos': 'children',
    'perro': 'dog', 'perros': 'dogs', 'gato': 'cat',
    'mochila': 'backpack', 'bolso': 'bag', 'maleta': 'suitcase',
    'casco': 'helmet', 'gorra': 'cap', 'sombrero': 'hat',
    'arma': 'gun', 'pistola': 'gun', 'cuchillo': 'knife',
    'rojo': 'red', 'roja': 'red', 'rojos': 'red', 'rojas': 'red',
    'azul': 'blue', 'azules': 'blue', 'verde': 'green', 'verdes': 'green',
    'negro': 'black', 'negra': 'black', 'negros': 'black', 'negras': 'black',
    'blanco': 'white', 'blanca': 'white', 'blancos': 'white',
    'blancas': 'white', 'amarillo': 'yellow', 'amarilla': 'yellow',
    'amarillos': 'yellow', 'amarillas': 'yellow', 'gris': 'gray',
    'grises': 'gray', 'naranja': 'orange', 'morado': 'purple',
    'rosado': 'pink', 'rosa': 'pink', 'marron': 'brown', 'marrones': 'brown',
    'cafe': 'brown', 'plateado': 'silver', 'dorado': 'gold',
}

_COLORES_EN = {'red', 'blue', 'green', 'black', 'white', 'yellow', 'gray',
               'orange', 'purple', 'pink', 'brown', 'silver', 'gold'}


def _llano(texto: Any) -> str:
    plano = unicodedata.normalize('NFD', str(texto or ''))
    return ''.join(c for c in plano if not unicodedata.combining(c)).lower()


def termino_de_busqueda(consulta: str) -> Optional[str]:
    """El término open-vocab en inglés, o None si es pregunta abierta (VQA).

    'búscame el carro rojo' -> 'red car' (CLIP quiere el adjetivo delante).
    """
    llano = _llano(consulta)
    corte, palabra = -1, None
    for kw in _PALABRAS_DETECCION:
        idx = llano.find(kw)
        if idx != -1 and (corte == -1 or idx < corte):
            corte, palabra = idx, kw
    if palabra is None:
        return None
    resto = llano[corte + len(palabra):]
    utiles = [p for p in re.findall(r'[a-z0-9]+', resto) if p not in _RELLENO]
    if not utiles:
        return None
    traducidas = [_VOCABULARIO.get(p, p) for p in utiles]
    colores = [p for p in traducidas if p in _COLORES_EN]
    objetos = [p for p in traducidas if p not in _COLORES_EN]
    return ' '.join(colores + objetos) or None


def es_afirmativa(respuesta: str) -> bool:
    """'Sí, se ve un carro rojo…' -> True. Tolera acentos, comillas y signos."""
    limpia = _llano(respuesta).lstrip(' \t\'"¡!¿?*-')
    return bool(re.match(r'si\b', limpia))


# ── Fotos de cada ámbito ─────────────────────────────────────────────────

def _fotos(ambito: str, limite: int) -> List[Dict[str, str]]:
    """Las `limite` fotos más recientes: [{archivo, ruta, url, nota}]."""
    if ambito == 'alertas':
        from .api_lectura import _carpeta_alertas, _filas_alertas
        carpeta = _carpeta_alertas()
        return [{
            'archivo': f['archivo'],
            'ruta': str(carpeta / f['archivo']),
            'url': f['url'],
            'nota': ' · '.join(x for x in (f['clase'], f['evento'],
                                           f['camara'], f['timestamp']) if x),
        } for f in _filas_alertas()[:limite]]
    if ambito == 'capturas':
        from .dashboard import _captures_dir, dashboard_captures
        base = Path(_captures_dir()) / 'persons'
        fuera = []
        for c in dashboard_captures(limit=limite).get('capturas') or []:
            stem = str(c.get('stem') or '')
            if not stem:
                continue
            fuera.append({
                'archivo': f'{stem}.jpg',
                'ruta': str(base / f'{stem}.jpg'),
                'url': f'/dashboard/img/capture/{stem}.jpg',
                'nota': ' · '.join(x for x in (c.get('gender'),
                                               c.get('age_range'),
                                               c.get('timestamp')) if x),
            })
        return fuera
    raise HTTPException(status_code=422,
                        detail="ambito debe ser 'alertas' o 'capturas'")


# ── Trabajos (una búsqueda a la vez) ─────────────────────────────────────

_TRABAJOS: Dict[str, Dict[str, Any]] = {}
_CANDADO = threading.Lock()
_MAX_GUARDADOS = 8


def _hay_busqueda_corriendo() -> bool:
    return any(t['estado'] in ('en_cola', 'corriendo')
               for t in _TRABAJOS.values())


def _recortar_viejos() -> None:
    termina = [i for i, t in sorted(_TRABAJOS.items(),
                                    key=lambda kv: kv[1]['creado'])
               if t['estado'] in ('terminado', 'error')]
    while len(termina) and len(_TRABAJOS) >= _MAX_GUARDADOS:
        _TRABAJOS.pop(termina.pop(0), None)


def _correr(ident: str, fotos: List[Dict[str, str]]) -> None:
    trabajo = _TRABAJOS[ident]
    trabajo['estado'] = 'corriendo'
    inicio = time.time()
    try:
        import cv2

        from ..analityc.core.multimodal_router import get_multimodal_router
        rt = get_multimodal_router()
        conf = float(os.getenv('ELDE_VLM_BUSQUEDA_CONF', '0.25'))
        for i, foto in enumerate(fotos):
            img = cv2.imread(foto['ruta'])
            if img is None:
                trabajo['hechas'] = i + 1
                continue
            if trabajo['termino']:
                dets = rt.detect(img, [trabajo['termino']], conf=conf)
                if dets:
                    mejor = max(d['confidence'] for d in dets)
                    trabajo['resultados'].append({
                        **foto,
                        'detalle': f"{len(dets)} coincidencia(s) · "
                                   f"confianza {mejor:.2f}",
                        'confianza': round(mejor, 3)})
            else:
                pregunta = (f"{trabajo['consulta']}\n"
                            "Responde en UNA línea que empiece con SI o NO, "
                            "y después el porqué, brevísimo.")
                respuesta = rt.vqa(img, pregunta, max_new_tokens=48)
                if es_afirmativa(respuesta):
                    trabajo['resultados'].append(
                        {**foto, 'detalle': respuesta[:200]})
            trabajo['hechas'] = i + 1
        if trabajo['termino']:
            trabajo['resultados'].sort(
                key=lambda r: r.get('confianza', 0), reverse=True)
        trabajo['estado'] = 'terminado'
    except Exception as exc:                       # noqa: BLE001
        logger.exception('la búsqueda VLM %s reventó', ident)
        trabajo['estado'] = 'error'
        trabajo['error'] = f'{type(exc).__name__}: {exc}'[:300]
    finally:
        trabajo['duracion_s'] = round(time.time() - inicio, 1)


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get('/vlm-buscador')
def estado_buscador() -> Dict[str, Any]:
    """Si el buscador está encendido y con qué modelo VQA respondería."""
    fuera: Dict[str, Any] = {'activo': buscador_activo(),
                             'ocupado': _hay_busqueda_corriendo()}
    try:
        from ..analityc.core.multimodal_router import get_multimodal_router
        fuera['modelo'] = get_multimodal_router().current_vqa()
    except Exception:
        fuera['modelo'] = None
    return fuera


@router.post('/vlm-buscador')
def cambiar_buscador(activo: bool = True) -> Dict[str, Any]:
    _fijar_buscador(bool(activo))
    return {'activo': buscador_activo(),
            'mensaje': ('Buscador VLM encendido: las búsquedas usan la GPU '
                        'compartida con la inferencia.' if activo else
                        'Buscador VLM apagado.')}


@router.post('/vlm-busqueda')
def crear_busqueda(
        consulta: str = Query(..., min_length=2, max_length=300),
        ambito: str = Query('alertas'),
        limite: Optional[int] = Query(None, ge=1, le=200),
) -> Dict[str, Any]:
    """Crea la búsqueda y devuelve su id; el estado se sondea por GET."""
    if not buscador_activo():
        raise HTTPException(
            status_code=409,
            detail='el buscador VLM está apagado: enciéndelo con el botón VLM')
    with _CANDADO:
        if _hay_busqueda_corriendo():
            raise HTTPException(
                status_code=409,
                detail='ya hay una búsqueda en curso (la GPU es una): '
                       'espera a que termine')
        termino = termino_de_busqueda(consulta)
        if termino:
            tope = int(os.getenv('ELDE_VLM_BUSQUEDA_FOTOS_DETECT', '80'))
        else:
            tope = int(os.getenv('ELDE_VLM_BUSQUEDA_FOTOS_VQA', '6'))
        fotos = _fotos(ambito, min(limite or tope, tope))
        if not fotos:
            raise HTTPException(status_code=404,
                                detail=f'no hay fotos en el ámbito {ambito}')
        ident = uuid.uuid4().hex[:12]
        _recortar_viejos()
        _TRABAJOS[ident] = {
            'id': ident, 'consulta': consulta, 'ambito': ambito,
            'modo': 'deteccion' if termino else 'pregunta',
            'termino': termino, 'estado': 'en_cola', 'hechas': 0,
            'total': len(fotos), 'resultados': [], 'error': None,
            'creado': time.time(), 'duracion_s': None,
        }
    threading.Thread(target=_correr, args=(ident, fotos), daemon=True,
                     name=f'vlm-busqueda-{ident}').start()
    trabajo = _TRABAJOS[ident]
    return {'id': ident, 'modo': trabajo['modo'],
            'termino': termino, 'total': trabajo['total']}


@router.get('/vlm-busqueda/{ident}')
def ver_busqueda(ident: str) -> Dict[str, Any]:
    limpio = re.sub(r'[^0-9a-f]', '', str(ident or ''))[:32]
    trabajo = _TRABAJOS.get(limpio)
    if not trabajo:
        raise HTTPException(status_code=404,
                            detail=f'búsqueda desconocida: {limpio}')
    # Copia sin la ruta de disco: el navegador solo necesita la URL.
    fuera = dict(trabajo)
    fuera['resultados'] = [{k: v for k, v in r.items() if k != 'ruta'}
                           for r in trabajo['resultados']]
    return fuera
