"""
src/app/dashboard.py — Dashboard web de Analitica de Visitantes.

Pagina unica (GET /dashboard) + API JSON servidas por el MISMO proceso
FastAPI del servidor de inferencia. Muestra: KPIs de trafico (entradas,
visitantes unicos, aforo, permanencia media), distribucion por genero y
segmento de edad, mapa de calor por camara, tabla de personas (con # de
veces detectada via Re-ID y permanencia) y la galeria de capturas.

Fuentes de datos (en orden de preferencia):
  1. Procesadores VIVOS ('Personal de Amazonas') via app._iter_person_processors
     -> metricas en tiempo real (zone_state, Re-ID en memoria).
  2. Artefactos en disco (sobreviven reinicios): output/person_db/persons.pkl,
     output/captures/persons/*.json, output/heatmap/*.png,
     output/analytics_report_*.json.

Todo es defensivo: sin conexiones activas el dashboard sigue funcionando
con lo que haya en disco.
"""

import glob
import json
import logging
import os
import pickle
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..analityc.core.analytics.config import AnalyticsConfig

logger = logging.getLogger(__name__)

router = APIRouter()

# Raiz del proyecto: src/app/dashboard.py -> ../../
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

_SAFE_NAME = re.compile(r'[^A-Za-z0-9_\-.]')


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(_ROOT, path)


def _captures_dir() -> str:
    return _abs(AnalyticsConfig.CAPTURE_DIR)


def _heatmap_dir() -> str:
    return _abs(AnalyticsConfig.HEATMAP_DIR)


def _person_db_dir() -> str:
    return os.path.dirname(_abs(AnalyticsConfig.REID_DB_PATH))


def _safe(name: Any) -> str:
    """Sanitiza un nombre de archivo (sin separadores de ruta)."""
    return _SAFE_NAME.sub('', str(name))[:160]


# ── Acceso a los procesadores vivos ──────────────────────────────────────

def _person_procs() -> list:
    """[(client_id, camera_id, cam_proc)] de 'Personal de Amazonas' activos.
    Import perezoso para evitar el import circular con app.py."""
    try:
        from .app import _iter_person_processors
        return list(_iter_person_processors())
    except Exception:
        return []


def _live_reid():
    """El FaceReidentifier vivo (compartido entre camaras), o None."""
    for _cid, _cam, proc in _person_procs():
        r = getattr(proc, '_reidentifier', None)
        if r is not None:
            return r
    return None


def _persons_from_disk() -> List[Dict[str, Any]]:
    """Personas desde output/person_db/persons.pkl (modo sin conexion)."""
    path = _abs(AnalyticsConfig.REID_DB_PATH)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'rb') as f:
            payload = pickle.load(f)
        db = payload.get('db', {}) if isinstance(payload, dict) else {}
        faces_dir = os.path.join(_person_db_dir(), 'faces')
        out = []
        for uid, rec in db.items():
            out.append({
                'uuid': str(uid),
                'gender': rec.get('gender') or 'Desconocido',
                'age_range': rec.get('age_range') or 'Desconocido',
                'demo_confidence': float(rec.get('demo_confidence', 0.0)),
                'first_seen': float(rec.get('first_seen', 0.0)),
                'last_seen': float(rec.get('last_seen', 0.0)),
                'visit_count': int(rec.get('visit_count', 1)),
                'face_image_available': os.path.isfile(
                    os.path.join(faces_dir, f"{uid}.jpg")),
            })
        return out
    except Exception as exc:
        logger.debug("dashboard: no se pudo leer persons.pkl: %s", exc)
        return []


def _all_persons() -> List[Dict[str, Any]]:
    reid = _live_reid()
    if reid is not None:
        try:
            return reid.get_all_persons()
        except Exception:
            pass
    return _persons_from_disk()


def _dwell_by_person() -> Dict[str, Dict[str, Any]]:
    """Permanencia y visitas al AREA por persistent_id, agregadas entre
    todas las camaras vivas (incluye el intervalo abierto si sigue dentro)."""
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    for _cid, _cam, proc in _person_procs():
        zs = getattr(proc, '_zone_state', None) or {}
        for pid, st in list(zs.items()):
            try:
                t = float(st.get('tiempo_total_dentro', 0.0))
                if st.get('estado') == 'DENTRO' and st.get('entry_time'):
                    t += max(0.0, now - float(st['entry_time']))
                rec = out.setdefault(str(pid), {
                    'dwell_s': 0.0, 'visitas_area': 0, 'dentro': False})
                rec['dwell_s'] += t
                rec['visitas_area'] += int(st.get('conteo_entradas', 0))
                rec['dentro'] = rec['dentro'] or st.get('estado') == 'DENTRO'
            except Exception:
                continue
    return out


def _reports_from_disk() -> List[Dict[str, Any]]:
    out = []
    for p in glob.glob(os.path.join(_ROOT, 'output',
                                    'analytics_report_*.json')):
        try:
            with open(p, encoding='utf-8') as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def _captures_index(limit: int = 0) -> List[Dict[str, Any]]:
    """Capturas (sidecars JSON) mas recientes primero."""
    pdir = os.path.join(_captures_dir(), 'persons')
    if not os.path.isdir(pdir):
        return []
    try:
        files = [f for f in os.listdir(pdir) if f.endswith('.json')]
    except Exception:
        return []
    files.sort(reverse=True)  # el stem empieza con YYYYMMDD_HHMMSS
    if limit > 0:
        files = files[:limit]
    fdir = os.path.join(_captures_dir(), 'faces')
    out = []
    for fn in files:
        stem = fn[:-5]
        jpg = os.path.join(pdir, f"{stem}.jpg")
        if not os.path.isfile(jpg):
            continue
        meta: Dict[str, Any] = {}
        try:
            with open(os.path.join(pdir, fn), encoding='utf-8') as f:
                meta = json.load(f) or {}
        except Exception:
            pass
        out.append({
            'stem': stem,
            'gender': meta.get('gender'),
            'age_range': meta.get('age_range'),
            'camera': meta.get('camera'),
            'timestamp': meta.get('timestamp'),
            'person_uuid': meta.get('person_uuid'),
            'visitas': meta.get('visitas'),
            'has_face': os.path.isfile(os.path.join(fdir, f"{stem}.jpg")),
            # Estado del reanalisis: distingue "aun pendiente" de "el VLM ya
            # lo miro y no hay nadie", y marca lo que rescato el VLM.
            'no_es_persona': bool(meta.get('no_es_persona')),
            'revisado_por_vlm': bool(meta.get('revisado_por_vlm')),
            'origen_demografia': meta.get('origen_demografia'),
            'motivo': meta.get('motivo_sin_demografia'),
        })
    return out


# ── API JSON ─────────────────────────────────────────────────────────────

@router.get("/dashboard/api/summary")
def dashboard_summary():
    """KPIs + distribuciones, en vivo si hay conexiones y si no, de disco."""
    try:
        procs = _person_procs()
        persons = _all_persons()
        by_uid = {p['uuid']: p for p in persons}
        gen_dist: Counter = Counter()
        age_dist: Counter = Counter()
        live = bool(procs)
        if live:
            entradas = salidas = aforo = 0
            unicos: set = set()
            for _cid, _cam, proc in procs:
                entradas += int(getattr(proc, '_total_entries', 0))
                salidas += int(getattr(proc, '_total_exits', 0))
                zs = getattr(proc, '_zone_state', None) or {}
                aforo += sum(1 for st in zs.values()
                             if st.get('estado') == 'DENTRO')
                unicos |= set(getattr(proc, '_unique_entered', set()) or set())
            dwell = _dwell_by_person()
            vals = [d['dwell_s'] for d in dwell.values()
                    if d['visitas_area'] > 0]
            permanencia = (sum(vals) / len(vals)) if vals else 0.0
            base_uids = unicos if unicos else set(by_uid)
            for uid in base_uids:
                rec = by_uid.get(uid)
                gen_dist[(rec or {}).get('gender') or 'Desconocido'] += 1
                age_dist[(rec or {}).get('age_range') or 'Desconocido'] += 1
            visitantes = len(unicos)
        else:
            reports = _reports_from_disk()
            entradas = sum(int(r.get('total_entradas', 0)) for r in reports)
            salidas = sum(int(r.get('total_salidas', 0)) for r in reports)
            aforo = 0
            visitantes = sum(int(r.get('visitantes_unicos', 0))
                             for r in reports)
            vals = [float(r.get('permanencia_media_s', 0.0))
                    for r in reports if r.get('permanencia_media_s')]
            permanencia = (sum(vals) / len(vals)) if vals else 0.0
            for p in persons:
                gen_dist[p.get('gender') or 'Desconocido'] += 1
                age_dist[p.get('age_range') or 'Desconocido'] += 1
        return {
            'status': 'ok',
            'live': live,
            'camaras_activas': len(procs),
            'trafico_total_entradas': entradas,
            'total_salidas': salidas,
            'visitantes_unicos': visitantes,
            'aforo_actual': aforo,
            'permanencia_media_s': round(permanencia, 1),
            'galeria_total': len(persons),
            'total_capturas': len(_captures_index()),
            'distribucion_genero': dict(gen_dist),
            'distribucion_edad': dict(age_dist),
            'timestamp': time.time(),
        }
    except Exception as exc:
        logger.exception("dashboard_summary error")
        return JSONResponse({'status': 'error', 'message': str(exc)},
                            status_code=500)


@router.get("/dashboard/api/persons")
def dashboard_persons(limit: int = 200):
    """Personas de la galeria Re-ID con # de veces detectada y permanencia."""
    try:
        persons = _all_persons()
        dwell = _dwell_by_person()
        for p in persons:
            d = dwell.get(p['uuid'], {})
            p['dwell_s'] = round(float(d.get('dwell_s', 0.0)), 1)
            p['visitas_area'] = int(d.get('visitas_area', 0))
            p['dentro'] = bool(d.get('dentro', False))
        persons.sort(key=lambda p: p.get('last_seen', 0.0), reverse=True)
        return {'status': 'ok', 'total': len(persons),
                'personas': persons[:max(1, int(limit))]}
    except Exception as exc:
        logger.exception("dashboard_persons error")
        return JSONResponse({'status': 'error', 'message': str(exc)},
                            status_code=500)


@router.get("/dashboard/api/captures")
def dashboard_captures(limit: int = 200):
    try:
        return {'status': 'ok', 'total': len(_captures_index()),
                'capturas': _captures_index(limit=max(1, int(limit)))}
    except Exception as exc:
        logger.exception("dashboard_captures error")
        return JSONResponse({'status': 'error', 'message': str(exc)},
                            status_code=500)


@router.post("/dashboard/api/analizar-pendientes")
def dashboard_analizar_pendientes(usar_vlm: Optional[bool] = None,
                                  limite: int = 0):
    """Reanaliza en segundo plano las capturas que quedaron sin genero.

    Sobre una foto ya guardada se puede gastar mucho mas computo que en
    vivo: se aplican varias vistas (TTA) y, opcionalmente, una segunda
    opinion del VLM.
    """
    try:
        from ..analityc.core.analytics.analizador_pendientes import (
            obtener_analizador)
        # `usar_vlm=None` -> lo decide la configuracion (activo por defecto).
        aceptado, mensaje = obtener_analizador().lanzar(
            usar_vlm=usar_vlm, limite=int(limite))
        return {"status": "ok" if aceptado else "sin_cambios",
                "mensaje": mensaje,
                "analisis": obtener_analizador().estado()}
    except Exception as exc:
        logger.exception("analizar_pendientes error")
        return JSONResponse({"status": "error", "message": str(exc)},
                            status_code=500)


@router.get("/dashboard/api/vlm")
def dashboard_vlm_estado():
    """Estado del VLM para el reanalisis: si esta activo y que modelo usa."""
    try:
        from ..analityc.core.analytics.analizador_pendientes import vlm_activo
        modelo = "3b"
        ruta = os.path.join(_ROOT, "output", "vlm_model.txt")
        if os.path.isfile(ruta):
            with open(ruta, encoding="utf-8") as fichero:
                modelo = fichero.read().strip() or "3b"
        return {"status": "ok", "activo": vlm_activo(), "modelo": modelo}
    except Exception as exc:
        logger.exception("vlm_estado error")
        return JSONResponse({"status": "error", "message": str(exc)},
                            status_code=500)


@router.post("/dashboard/api/vaciar-detecciones")
def dashboard_vaciar_detecciones(confirmar: bool = False):
    """Vacia TODAS las detecciones acumuladas.

    No destruye: MUEVE todo a `output/papelera/<fecha_hora>/`, conservando
    la estructura de carpetas. Vaciar es una accion de un solo clic sobre
    trabajo que puede ser de semanas; que dependa de que el usuario lea un
    dialogo es demasiado fragil. Recuperarlo es copiar la carpeta de vuelta.

    Se lleva las capturas (servidor y cliente), la galeria de identidades
    del Re-ID y los mapas de calor. Exige `confirmar=true`: una peticion
    suelta no debe poder vaciar el historico.
    """
    if not confirmar:
        return JSONResponse(
            {"status": "error",
             "mensaje": "Falta confirmar=true."},
            status_code=400)

    import shutil

    sello = time.strftime("%Y%m%d_%H%M%S")
    papelera = os.path.join(_ROOT, "output", "papelera", sello)
    movidos: Dict[str, int] = {}
    errores: List[str] = []

    def _apartar(origen: str, etiqueta: str) -> None:
        """Mueve el contenido de `origen` a la papelera, con su nombre."""
        n = 0
        if os.path.isdir(origen):
            destino = os.path.join(papelera, etiqueta)
            for nombre in os.listdir(origen):
                completo = os.path.join(origen, nombre)
                try:
                    os.makedirs(destino, exist_ok=True)
                    if os.path.isdir(completo):
                        n += sum(len(f) for _r, _d, f in os.walk(completo))
                    else:
                        n += 1
                    shutil.move(completo, os.path.join(destino, nombre))
                except (OSError, shutil.Error) as exc:
                    errores.append(f"{etiqueta}/{nombre}: {exc}")
        movidos[etiqueta] = n

    capturas = _captures_dir()
    _apartar(os.path.join(capturas, 'persons'), 'capturas')
    _apartar(os.path.join(capturas, 'faces'), 'rostros')

    # La copia que ve el cliente en su carpeta `capture/`.
    cliente = getattr(AnalyticsConfig, 'CAPTURE_CLIENT_DIR', '') or ''
    if cliente:
        _apartar(_abs(cliente), 'capturas_cliente')

    # Galeria de identidades: sin esto, las personas ya vistas seguirian
    # reconociendose y el conteo no empezaria de cero.
    db = _person_db_dir()
    _apartar(os.path.join(db, 'faces'), 'rostros_reid')
    pkl = _abs(AnalyticsConfig.REID_DB_PATH)
    movidos['galeria_reid'] = 0
    if os.path.isfile(pkl):
        try:
            os.makedirs(papelera, exist_ok=True)
            shutil.move(pkl, os.path.join(papelera,
                                          os.path.basename(pkl)))
            movidos['galeria_reid'] = 1
        except (OSError, shutil.Error) as exc:
            errores.append(f"persons.pkl: {exc}")

    # El directorio de mapas de calor es TODO dato derivado: imagenes, los
    # .json con el calor acumulado por camara y los subdirectorios bg/ e
    # history/. Filtrar por extension dejaba el historico dentro y el mapa
    # reaparecia con los datos viejos.
    _apartar(_heatmap_dir(), 'mapas_de_calor')

    # Que el Re-ID vivo olvide tambien lo que tiene en memoria; si no, el
    # historico volveria a aparecer en cuanto guardase.
    reid = _live_reid()
    if reid is not None:
        fn = getattr(reid, 'reset', None)
        if callable(fn):
            try:
                fn()
                movidos['reid_en_memoria'] = 1
            except Exception as exc:  # noqa: BLE001
                errores.append(f"reid.reset: {exc}")

    total = sum(v for k, v in movidos.items() if k != 'reid_en_memoria')
    logger.warning("Detecciones vaciadas -> %s : %s (errores: %d)",
                   papelera, movidos, len(errores))
    return {"status": "ok", "borrados": movidos, "total": total,
            "papelera": papelera, "errores": errores[:10],
            "mensaje": f"Se apartaron {total} archivos. "
                       f"Recuperables en output/papelera/{sello}"}


@router.post("/dashboard/api/vlm")
def dashboard_vlm_cambiar(activo: bool = True):
    """Enciende o apaga el VLM del reanalisis. La preferencia persiste.

    Apagarlo NO descarga el modelo que ya estuviera en memoria; lo que
    hace es que los proximos reanalisis no lo consulten.
    """
    try:
        from ..analityc.core.analytics.analizador_pendientes import fijar_vlm
        vigente = fijar_vlm(bool(activo))
        return {"status": "ok", "activo": vigente,
                "mensaje": ("VLM activado: se consultara en los casos dudosos."
                            if vigente else
                            "VLM desactivado: solo se usara MiVOLO.")}
    except Exception as exc:
        logger.exception("vlm_cambiar error")
        return JSONResponse({"status": "error", "message": str(exc)},
                            status_code=500)


@router.get("/dashboard/api/analisis-estado")
def dashboard_analisis_estado():
    """Progreso del reanalisis en curso (o del ultimo terminado)."""
    try:
        from ..analityc.core.analytics.analizador_pendientes import (
            obtener_analizador)
        return {"status": "ok", "analisis": obtener_analizador().estado()}
    except Exception as exc:
        logger.exception("analisis_estado error")
        return JSONResponse({"status": "error", "message": str(exc)},
                            status_code=500)


@router.post("/dashboard/api/analisis-cancelar")
def dashboard_analisis_cancelar():
    try:
        from ..analityc.core.analytics.analizador_pendientes import (
            obtener_analizador)
        obtener_analizador().cancelar()
        return {"status": "ok", "mensaje": "Cancelacion solicitada."}
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)},
                            status_code=500)


@router.get("/dashboard/api/telemetria")
def dashboard_telemetria():
    """Resumen EN VIVO de la telemetria demografica (Hito 2).

    Devuelve el reparto de tracks por motivo final: cuantos acabaron con
    demografia y por que fallaron los demas. Los tracks todavia en escena
    se informan aparte (`tracks_en_curso`): aun no tienen veredicto, asi
    que no entran en el reparto de motivos.
    """
    try:
        from ..analityc.core.analytics.telemetria_demografica import (
            obtener_telemetria)
        tel = obtener_telemetria()
        tel.volcar()                      # persistir lo pendiente
        resumen = tel.resumen()

        # Tracks vivos: aun sin veredicto, por eso van fuera del reparto.
        en_curso = 0
        for _cid, _cam, proc in _person_procs():
            demo = getattr(proc, '_demographics', None)
            acc = getattr(demo, '_accum', None) if demo is not None else None
            if acc:
                en_curso += len(acc)
        resumen['tracks_en_curso'] = en_curso
        return {'status': 'ok', 'telemetria': resumen}
    except Exception as exc:
        logger.exception("dashboard_telemetria error")
        return JSONResponse({'status': 'error', 'message': str(exc)},
                            status_code=500)


@router.get("/dashboard/api/heatmaps")
def dashboard_heatmaps():
    """Snapshot vigente del mapa de calor por camara (PNGs de primer nivel)."""
    try:
        hdir = _heatmap_dir()
        out = []
        if os.path.isdir(hdir):
            for fn in sorted(os.listdir(hdir)):
                if not fn.endswith('.png') or fn.endswith('.tmp.png'):
                    continue
                p = os.path.join(hdir, fn)
                if not os.path.isfile(p):
                    continue
                out.append({'name': fn, 'camera': fn[:-4],
                            'mtime': os.path.getmtime(p)})
        return {'status': 'ok', 'heatmaps': out}
    except Exception as exc:
        logger.exception("dashboard_heatmaps error")
        return JSONResponse({'status': 'error', 'message': str(exc)},
                            status_code=500)


# ── Imagenes ─────────────────────────────────────────────────────────────

def _serve(path: str):
    if os.path.isfile(path):
        return FileResponse(path)
    return JSONResponse({'status': 'error', 'message': 'no existe'},
                        status_code=404)


@router.get("/dashboard/img/capture/{stem}.jpg")
def img_capture(stem: str):
    return _serve(os.path.join(_captures_dir(), 'persons',
                               f"{_safe(stem)}.jpg"))


@router.get("/dashboard/img/capface/{stem}.jpg")
def img_capture_face(stem: str):
    return _serve(os.path.join(_captures_dir(), 'faces',
                               f"{_safe(stem)}.jpg"))


@router.get("/dashboard/img/face/{uid}.jpg")
def img_face(uid: str):
    return _serve(os.path.join(_person_db_dir(), 'faces',
                               f"{_safe(uid)}.jpg"))


@router.get("/dashboard/img/heatmap/{name}.png")
def img_heatmap(name: str):
    return _serve(os.path.join(_heatmap_dir(), f"{_safe(name)}.png"))


# ── Pagina ───────────────────────────────────────────────────────────────

_HTML = """<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analitica de Visitantes</title>
<style>
:root{color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--border:rgba(255,255,255,.10);
 --ink:#ffffff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;
 --blue:#3987e5;--magenta:#d55181;--good:#0ca30c}
*{box-sizing:border-box;margin:0}
body{background:var(--page);color:var(--ink);
 font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:18px}
h1{font-size:18px;font-weight:600}
h2{font-size:13px;font-weight:600;color:var(--ink2);margin:0 0 10px}
header{display:flex;align-items:baseline;gap:12px;margin-bottom:16px;flex-wrap:wrap}
#estado{font-size:12px;color:var(--muted)}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:12px}
.card{background:var(--surface);border:1px solid var(--border);
 border-radius:8px;padding:14px}
.kpi .v{font-size:26px;font-weight:650;margin-top:2px}
.kpi .l{font-size:11px;color:var(--muted);text-transform:uppercase;
 letter-spacing:.4px}
.kpi .s{font-size:11px;color:var(--ink2);margin-top:2px}
.two{grid-template-columns:1fr 1fr;margin-bottom:12px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
.bar-row{display:grid;grid-template-columns:110px 1fr 46px;gap:8px;
 align-items:center;margin:6px 0}
.bar-row .lbl{font-size:12px;color:var(--ink2);text-align:right;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{background:transparent;height:16px;position:relative}
.bar-fill{height:16px;border-radius:0 4px 4px 0;min-width:2px}
.bar-row .val{font-size:12px;color:var(--ink);
 font-variant-numeric:tabular-nums}
.section{margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:var(--muted);text-align:left;font-weight:500;padding:6px 8px;
 border-bottom:1px solid var(--grid);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:middle;
 font-variant-numeric:tabular-nums}
tr:hover td{background:rgba(255,255,255,.03)}
.face{width:40px;height:40px;border-radius:50%;object-fit:cover;
 background:#111;display:block}
.face-ph{width:40px;height:40px;border-radius:50%;background:#242423;
 display:flex;align-items:center;justify-content:center;color:var(--muted)}
.badge{display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px}
.badge.dentro{background:rgba(12,163,12,.15);color:var(--good);
 border:1px solid rgba(12,163,12,.4)}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
 gap:10px}
.gal a{display:block;background:var(--surface);border:1px solid var(--border);
 border-radius:8px;overflow:hidden;text-decoration:none;color:var(--ink2)}
.gal img{width:100%;height:170px;object-fit:cover;display:block;
 background:#111}
.gal .cap{padding:6px 8px;font-size:11px;line-height:1.35}
.gal .cap b{color:var(--ink);font-weight:600}
.heat{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
 gap:10px}
.heat figure{background:var(--surface);border:1px solid var(--border);
 border-radius:8px;overflow:hidden}
.heat img{width:100%;display:block}
.heat figcaption{padding:6px 10px;font-size:11px;color:var(--muted)}
.empty{color:var(--muted);font-size:12px;padding:10px 0}
.scroll-x{overflow-x:auto}
a.plain{color:var(--blue)}
.btn{background:var(--surface);color:var(--ink);border:1px solid var(--border);
 border-radius:6px;padding:6px 14px;font:inherit;font-size:12px;cursor:pointer}
.btn:hover{border-color:var(--blue);color:var(--blue)}
.btn-peligro{color:var(--muted)}
.btn-peligro:hover{border-color:#d0453e;color:#d0453e}
.btn:disabled{opacity:.5;cursor:default;border-color:var(--border);
 color:var(--muted)}
#analisis-info{font-size:12px;color:var(--ink2)}
.barra{height:4px;background:var(--grid);border-radius:2px;overflow:hidden;
 width:120px;display:inline-block;vertical-align:middle;margin-left:6px}
.barra i{display:block;height:100%;background:var(--blue);width:0}
</style></head><body>
<header>
 <h1>📊 Analitica de Visitantes</h1>
 <span id="estado">cargando…</span>
</header>

<div class="grid kpis" id="kpis"></div>

<div class="grid two">
 <div class="card"><h2>Genero (visitantes unicos)</h2><div id="chart-genero"></div></div>
 <div class="card"><h2>Segmentos de edad</h2><div id="chart-edad"></div></div>
</div>

<div class="card section"><h2>Mapa de calor por camara</h2>
 <div class="heat" id="heatmaps"></div></div>

<div class="card section"><h2>Personas reconocidas (Re-ID)</h2>
 <div class="scroll-x"><table id="tabla-personas">
  <thead><tr><th></th><th>Genero</th><th>Edad</th>
  <th>Veces detectada</th><th>Visitas al area</th><th>Permanencia</th>
  <th>Primera vez</th><th>Ultima vez</th><th></th></tr></thead>
  <tbody></tbody></table></div>
 <div class="empty" id="personas-empty" hidden>Sin personas registradas todavia.</div>
</div>

<div class="card section">
 <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
             margin-bottom:10px">
  <h2 style="margin:0">Galeria de capturas
   <span id="cap-count" style="color:var(--muted);font-weight:400"></span></h2>
  <span style="flex:1"></span>
  <span id="analisis-info"></span>
  <button id="btn-vlm" class="btn" title="Segunda opinion en los casos
dudosos. Mas lento, pero resuelve mas.">VLM</button>
  <button id="btn-analizar" class="btn">Analizar pendientes</button>
  <button id="btn-vaciar" class="btn btn-peligro"
          title="Aparta TODAS las capturas, la galeria de identidades y los
mapas de calor a output/papelera/. Recuperable.">Vaciar detecciones</button>
 </div>
 <div class="gal" id="galeria"></div>
 <div class="empty" id="galeria-empty" hidden>Sin capturas todavia.</div>
</div>

<script>
const $=s=>document.querySelector(s);
const GEN_COLOR={Hombre:'var(--blue)',Mujer:'var(--magenta)',
                 Desconocido:'var(--muted)'};
const AGE_ORDER=['0-12','13-17','18-25','26-35','36-50','51-65','65+',
 'Nino','Joven','Adulto','Mayor','Desconocido'];
function fmtDur(s){s=Math.round(s||0);
 if(s<60)return s+' s';const m=Math.floor(s/60),r=s%60;
 if(m<60)return m+'m '+r+'s';return Math.floor(m/60)+'h '+(m%60)+'m'}
function fmtTs(t){if(!t)return '—';
 return new Date(t*1000).toLocaleString('es-VE',
  {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}
function kpi(l,v,s){return `<div class="card kpi"><div class="l">${l}</div>
 <div class="v">${v}</div><div class="s">${s||''}</div></div>`}
function bars(el,dist,colorFn,order){
 const keys=Object.keys(dist);
 if(!keys.length){el.innerHTML='<div class="empty">Sin datos todavia.</div>';return}
 keys.sort((a,b)=>order?(order.indexOf(a)-order.indexOf(b))
                       :(dist[b]-dist[a]));
 const max=Math.max(...keys.map(k=>dist[k]),1);
 el.innerHTML=keys.map(k=>`<div class="bar-row" title="${k}: ${dist[k]}">
  <div class="lbl">${k}</div>
  <div class="bar-track"><div class="bar-fill"
   style="width:${Math.max(2,100*dist[k]/max)}%;
   background:${colorFn(k)}"></div></div>
  <div class="val">${dist[k]}</div></div>`).join('')}

async function refreshSummary(){
 try{
  const r=await fetch('/dashboard/api/summary');const d=await r.json();
  if(d.status!=='ok')throw new Error(d.message);
  $('#estado').textContent=(d.live?`EN VIVO · ${d.camaras_activas} camara(s)`
    :'sin conexiones activas (datos de disco)')+
    ' · actualizado '+new Date().toLocaleTimeString('es-VE');
  $('#kpis').innerHTML=
   kpi('Trafico total',d.trafico_total_entradas,'entradas al area')+
   kpi('Visitantes unicos',d.visitantes_unicos,'personas distintas en el area')+
   kpi('En el area ahora',d.aforo_actual,'aforo confirmado')+
   kpi('Permanencia media',fmtDur(d.permanencia_media_s),'por visitante')+
   kpi('Galeria Re-ID',d.galeria_total,'personas registradas')+
   kpi('Capturas',d.total_capturas,'fotos guardadas');
  bars($('#chart-genero'),d.distribucion_genero,
       k=>GEN_COLOR[k]||'var(--muted)');
  bars($('#chart-edad'),d.distribucion_edad,
       ()=>'var(--blue)',AGE_ORDER);
 }catch(e){$('#estado').textContent='error: '+e.message}
}
async function refreshPersons(){
 try{
  const r=await fetch('/dashboard/api/persons?limit=200');
  const d=await r.json();if(d.status!=='ok')return;
  const tb=$('#tabla-personas tbody');
  $('#personas-empty').hidden=d.personas.length>0;
  tb.innerHTML=d.personas.map(p=>{
   const face=p.face_image_available
    ?`<img class="face" loading="lazy"
       src="/dashboard/img/face/${p.uuid}.jpg?t=${Math.round(p.last_seen)}">`
    :'<div class="face-ph">👤</div>';
   return `<tr><td>${face}</td>
    <td style="color:${GEN_COLOR[p.gender]||'var(--ink)'}">${p.gender}</td>
    <td>${p.age_range}</td>
    <td>${p.visit_count}</td>
    <td>${p.visitas_area||'—'}</td>
    <td>${p.dwell_s?fmtDur(p.dwell_s):'—'}</td>
    <td>${fmtTs(p.first_seen)}</td><td>${fmtTs(p.last_seen)}</td>
    <td>${p.dentro?'<span class="badge dentro">DENTRO</span>':''}</td>
   </tr>`}).join('');
 }catch(e){console.error(e)}
}
async function refreshCaptures(){
 try{
  const r=await fetch('/dashboard/api/captures?limit=120');
  const d=await r.json();if(d.status!=='ok')return;
  $('#cap-count').textContent=` — ${d.total} en total`;
  $('#galeria-empty').hidden=d.capturas.length>0;
  $('#galeria').innerHTML=d.capturas.map(c=>{
   // Tres estados distintos: resuelta, cerrada por el VLM ("no hay nadie
   // en la foto") y realmente pendiente. Antes las dos ultimas se veian
   // igual y todo parecia quedarse en "Analizando...".
   let linea;
   if(c.gender){
    const marca=c.origen_demografia==='vlm_rescate'?' 🤖':'';
    linea=`<b style="color:${GEN_COLOR[c.gender]||'var(--ink)'}">`+
          `${c.gender}</b> · ${c.age_range||'—'}${marca}`;
   }else if(c.no_es_persona){
    linea=`<b style="color:var(--muted)">No es una persona</b>`;
   }else if(c.revisado_por_vlm){
    linea=`<b style="color:var(--muted)">Persona sin identificar</b>`;
   }else{
    linea=`<b>Analizando…</b>`;
   }
   const ts=c.timestamp?c.timestamp.replace(
     /^(\\d{4})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})$/,
     '$3/$2 $4:$5:$6'):'';
   // Miniatura = ROSTRO ampliado si se detecto (es lo que permite juzgar
   // edad/genero); si no hay cara, el recorte de la persona. El click
   // siempre abre la foto completa (cuerpo + primer plano del rostro).
   const thumb=c.has_face?`/dashboard/img/capface/${c.stem}.jpg`
                         :`/dashboard/img/capture/${c.stem}.jpg`;
   return `<a href="/dashboard/img/capture/${c.stem}.jpg" target="_blank"
     title="Abrir foto completa">
    <img loading="lazy" src="${thumb}">
    <div class="cap">${linea}<br>${c.camera||''} · ${ts}
     ${c.visitas>1?` · ${c.visitas} visitas`:''}</div></a>`}).join('');
 }catch(e){console.error(e)}
}
async function refreshHeatmaps(){
 try{
  const r=await fetch('/dashboard/api/heatmaps');const d=await r.json();
  if(d.status!=='ok')return;
  if(!d.heatmaps.length){
   $('#heatmaps').innerHTML='<div class="empty">Sin mapa de calor todavia '
    +'(se genera cuando hay actividad).</div>';return}
  $('#heatmaps').innerHTML=d.heatmaps.map(h=>
   `<figure><img src="/dashboard/img/heatmap/${h.camera}.png?t=${Math.round(h.mtime)}">
    <figcaption>${h.camera}</figcaption></figure>`).join('');
 }catch(e){console.error(e)}
}
const $vaciar=$('#btn-vaciar');
$vaciar.addEventListener('click', async()=>{
 // Dos preguntas a proposito: la primera se acepta por inercia, la
 // segunda obliga a leer que es lo que se va a perder.
 // OJO: _HTML es una cadena Python normal (no raw), asi que un salto de linea
 // para JS se escribe con DOBLE barra invertida. Con una sola, Python lo
 // convierte en un salto real dentro de la cadena JS y eso es un SyntaxError
 // que tumba el <script> COMPLETO (la pagina se quedaba en "cargando…").
 if(!confirm('Se van a vaciar TODAS las detecciones:\\n\\n'+
   '  - todas las capturas (servidor y cliente)\\n'+
   '  - la galeria de identidades del Re-ID\\n'+
   '  - los mapas de calor\\n\\n'+
   'No se destruyen: se mueven a output/papelera/ con la fecha, '+
   'por si hiciera falta recuperarlas.')) return;
 $vaciar.disabled=true; const previo=$vaciar.textContent;
 $vaciar.textContent='Vaciando…';
 try{
  const r=await fetch('/dashboard/api/vaciar-detecciones?confirmar=true',
                      {method:'POST'});
  const d=await r.json();
  if(d.status==='ok'){
   $vaciar.textContent='Vaciado';
   alert('Listo: '+d.total+' archivos apartados.\\n\\n'+
         'Copia de seguridad en:\\n'+d.papelera);
   tick(); refreshHeatmaps();
  }else{
   alert('No se pudo vaciar: '+(d.mensaje||'error'));
  }
 }catch(e){ alert('No se pudo contactar con el servidor: '+e); }
 finally{ $vaciar.disabled=false; $vaciar.textContent=previo; }
});

const $btn=$('#btn-analizar'), $info=$('#analisis-info'), $vlm=$('#btn-vlm');
async function estadoVlm(){
 try{
  const r=await fetch('/dashboard/api/vlm'); const d=await r.json();
  if(d.status!=='ok')return;
  pintarVlm(d.activo, d.modelo);
 }catch(e){console.error(e)}
}
function pintarVlm(activo, modelo){
 $vlm.dataset.activo = activo ? '1' : '';
 $vlm.textContent = 'VLM ' + (modelo ? modelo+' ' : '') + (activo?'ON':'OFF');
 $vlm.style.color = activo ? 'var(--good)' : 'var(--muted)';
 $vlm.style.borderColor = activo ? 'var(--good)' : 'var(--border)';
}
$vlm.addEventListener('click', async()=>{
 const nuevo = $vlm.dataset.activo ? 'false' : 'true';
 $vlm.disabled = true;
 try{
  const r=await fetch('/dashboard/api/vlm?activo='+nuevo,{method:'POST'});
  const d=await r.json();
  pintarVlm(d.activo);
  $info.textContent = d.mensaje || '';
 }catch(e){console.error(e)}
 $vlm.disabled = false;
});
async function estadoAnalisis(){
 try{
  const r=await fetch('/dashboard/api/analisis-estado');
  const d=await r.json(); if(d.status!=='ok')return;
  const a=d.analisis;
  if(a.ejecutando){
   $btn.disabled=true; $btn.textContent='Analizando…';
   $info.innerHTML=`${a.procesadas}/${a.total} · ${a.resueltas} resueltas`+
    `<span class="barra"><i style="width:${a.progreso_pct}%"></i></span>`;
   setTimeout(estadoAnalisis,1200);
  }else{
   $btn.disabled=false;
   $btn.textContent=a.pendientes_ahora?`Analizar ${a.pendientes_ahora} pendientes`
                                      :'Analizar pendientes';
   $info.textContent = a.total
     ? `ultimo: ${a.resueltas} resueltas de ${a.total}`
     : (a.pendientes_ahora?`${a.pendientes_ahora} sin clasificar`:'');
   if(a.total) refreshCaptures();
  }
 }catch(e){console.error(e)}
}
$btn.addEventListener('click',async()=>{
 $btn.disabled=true; $btn.textContent='Iniciando…';
 try{
  await fetch('/dashboard/api/analizar-pendientes',{method:'POST'});
 }catch(e){console.error(e)}
 estadoAnalisis();
});
function tick(){refreshSummary();refreshPersons();refreshCaptures()}
tick();refreshHeatmaps();estadoAnalisis();estadoVlm();
setInterval(tick,5000);setInterval(refreshHeatmaps,10000);
</script>
</body></html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    return HTMLResponse(_HTML)
