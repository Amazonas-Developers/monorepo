"""
src/app/dashboard_tienda.py — Dashboard de TIENDA (marketing y consumo).

App FastAPI propia servida en el puerto 9030 desde el MISMO proceso del
servidor de inferencia (igual que el panel de VIGILANTE en 5333). Se levanta
desde iniciar_servidor_headless.py con iniciar_dashboard_tienda().

Que muestra:
  - Personas en el area (aforo en vivo), trafico, visitantes unicos.
  - Ranking de PASILLOS (camaras): el mas y el menos frecuentado.
  - Mapa de calor por pasillo (PNG que ya genera el analizador).
  - Genero y segmento de edad, global y cruzado por pasillo.
  - Franja horaria punta y valle, permanencia media y tasa de retorno.

Fuentes (las mismas que el dashboard de visitantes, reutilizadas de
.dashboard para no duplicar logica):
  1. Procesadores VIVOS -> aforo/entradas/unicos POR CAMARA.
  2. Disco -> output/heatmap/*.json (actividad historica por camara),
     output/captures/persons/*.json (genero/edad/hora), person_db.

Nota honesta sobre "consumo": aqui no hay datos de punto de venta. Todo lo
que se muestra son metricas de comportamiento (trafico, permanencia,
recurrencia, mix demografico). Ticket medio y conversion real necesitan
integrar el POS; la pagina lo dice explicitamente en vez de inventarlos.

CUIDADO al editar la plantilla: _HTML es una cadena RAW (r\"\"\"...\"\"\") a
proposito. En el dashboard de visitantes, al no ser raw, Python se comia los
\\n de las cadenas JavaScript y las partia en dos lineas, lo que rompia el
<script> COMPLETO con un SyntaxError y dejaba la pagina en "cargando...".
Con raw, lo que se escribe es lo que recibe el navegador.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .dashboard import (_ROOT, _all_persons, _captures_index, _dwell_by_person,
                        _heatmap_dir, _person_procs, _safe)

logger = logging.getLogger(__name__)

PUERTO_TIENDA = 9030

# Camaras de prueba/benchmark que dejaron artefactos en output/heatmap y no son
# pasillos reales de la tienda. Se excluyen del ranking (se informa cuantas).
_CAM_PRUEBA = re.compile(r'^(cam_bench_|fuga_|test_|bench_)', re.I)

# Orden natural de los segmentos de edad para que las barras no salten.
_ORDEN_EDAD = ['0-12', '13-17', '18-25', '26-35', '36-50', '51-65', '65+',
               'Desconocido']


def _alias_path() -> str:
    return os.path.join(_ROOT, 'config', 'pasillos.json')


def _leer_alias() -> Dict[str, str]:
    """Nombres legibles por camera_id, editables en config/pasillos.json.

    Los camera_id que manda el cliente son UUID del componente de video, que
    no dicen nada al usuario. Este archivo los traduce a "Pasillo 3 - Lacteos"
    sin tocar codigo."""
    try:
        with open(_alias_path(), encoding='utf-8') as f:
            data = json.load(f) or {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception:
        return {}


def _nombre_corto(cid: str) -> str:
    """Etiqueta de reserva cuando no hay alias: UUID abreviado."""
    cid = str(cid)
    return cid if len(cid) <= 12 else cid[:8] + '…'


# ── Pasillos (camaras) ───────────────────────────────────────────────────

def _pasillos_disco() -> Dict[str, Dict[str, Any]]:
    """Un pasillo por cada heatmap en disco.

    'muestras' es cuantas veces el analizador acumulo presencia en esa camara:
    es el mejor proxy de actividad historica que hay sin conexiones vivas."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in glob.glob(os.path.join(_heatmap_dir(), '*.json')):
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f) or {}
        except Exception:
            continue
        cid = str(d.get('camera_id') or stem)
        png = os.path.join(_heatmap_dir(), f"{stem}.png")
        out[cid] = {
            'id': cid,
            'muestras': int(d.get('muestras', 0) or 0),
            'zonas_calientes': d.get('zonas_calientes') or [],
            'heatmap': f"{stem}.png" if os.path.isfile(png) else None,
            'actualizado': float(d.get('actualizado', 0.0) or 0.0),
            'es_prueba': bool(_CAM_PRUEBA.match(cid)),
        }
    return out


def _pasillos_vivos() -> Dict[str, Dict[str, Any]]:
    """Metricas reales por camara de los procesadores conectados."""
    out: Dict[str, Dict[str, Any]] = {}
    for _cid, camera_id, proc in _person_procs():
        key = str(camera_id)
        zs = getattr(proc, '_zone_state', None) or {}
        rec = out.setdefault(key, {'entradas': 0, 'aforo': 0, 'unicos': 0})
        try:
            rec['entradas'] += int(getattr(proc, '_total_entries', 0) or 0)
            rec['aforo'] += sum(1 for st in zs.values()
                                if st.get('estado') == 'DENTRO')
            rec['unicos'] += len(getattr(proc, '_unique_entered', set()) or set())
        except Exception:
            continue
    return out


def _demografia_por_pasillo() -> Dict[str, Dict[str, Counter]]:
    """Genero y edad agrupados por la camara que hizo la captura.

    Solo tiene grano fino para capturas nuevas: las antiguas se guardaron con
    camera='cam' generico (arreglado en app.py, ahora usa el camera_id)."""
    out: Dict[str, Dict[str, Counter]] = defaultdict(
        lambda: {'genero': Counter(), 'edad': Counter(), 'total': 0})
    for c in _captures_index():
        cam = str(c.get('camera') or 'desconocida')
        rec = out[cam]
        rec['total'] += 1
        rec['genero'][c.get('gender') or 'Desconocido'] += 1
        rec['edad'][c.get('age_range') or 'Desconocido'] += 1
    return out


def _por_hora() -> Dict[str, int]:
    """Capturas por hora del dia (00..23) a partir del stem YYYYMMDD_HHMMSS."""
    horas: Counter = Counter()
    for c in _captures_index():
        ts = str(c.get('timestamp') or '')
        if len(ts) >= 11 and '_' in ts:
            hh = ts.split('_')[1][:2]
            if hh.isdigit():
                horas[hh] += 1
    return {f"{h:02d}": int(horas.get(f"{h:02d}", 0)) for h in range(24)}


def _construir_resumen() -> Dict[str, Any]:
    alias = _leer_alias()
    disco = _pasillos_disco()
    vivos = _pasillos_vivos()
    demo_cam = _demografia_por_pasillo()
    procs = _person_procs()
    live = bool(procs)

    # Union de camaras vistas en disco y en vivo.
    ids = set(disco) | set(vivos)
    pasillos: List[Dict[str, Any]] = []
    for cid in ids:
        d = disco.get(cid, {})
        v = vivos.get(cid, {})
        es_prueba = bool(d.get('es_prueba') or _CAM_PRUEBA.match(str(cid)))
        dem = demo_cam.get(cid, {})
        # Metrica de trafico: en vivo manda las entradas reales; si no hay
        # conexion se usan las muestras del heatmap como proxy historico.
        entradas = int(v.get('entradas', 0) or 0)
        muestras = int(d.get('muestras', 0) or 0)
        pasillos.append({
            'id': cid,
            'nombre': alias.get(cid) or _nombre_corto(cid),
            'tiene_alias': cid in alias,
            'es_prueba': es_prueba,
            'entradas': entradas,
            'aforo': int(v.get('aforo', 0) or 0),
            'unicos': int(v.get('unicos', 0) or 0),
            'muestras': muestras,
            'trafico': entradas if entradas else muestras,
            'metrica': 'entradas' if entradas else 'muestras',
            'zonas_calientes': d.get('zonas_calientes') or [],
            'heatmap': d.get('heatmap'),
            'actualizado': d.get('actualizado', 0.0),
            'capturas': int(dem.get('total', 0) or 0),
            'genero': dict(dem.get('genero', {})),
            'edad': dict(dem.get('edad', {})),
        })

    reales = [p for p in pasillos if not p['es_prueba']]
    reales.sort(key=lambda p: p['trafico'], reverse=True)
    con_datos = [p for p in reales if p['trafico'] > 0]
    mas = con_datos[0] if con_datos else None
    menos = con_datos[-1] if len(con_datos) > 1 else None

    # ── Global: personas, genero, edad, recurrencia ──
    personas = _all_persons()
    dwell = _dwell_by_person()
    gen: Counter = Counter()
    edad: Counter = Counter()
    for p in personas:
        gen[p.get('gender') or 'Desconocido'] += 1
        edad[p.get('age_range') or 'Desconocido'] += 1
    recurrentes = sum(1 for p in personas if int(p.get('visit_count', 1)) > 1)
    unicos_total = len(personas)
    tasa_retorno = (100.0 * recurrentes / unicos_total) if unicos_total else 0.0

    if live:
        aforo = sum(p['aforo'] for p in pasillos)
        trafico = sum(p['entradas'] for p in pasillos)
        vals = [d['dwell_s'] for d in dwell.values() if d['visitas_area'] > 0]
        permanencia = (sum(vals) / len(vals)) if vals else 0.0
    else:
        aforo = 0
        trafico = 0
        permanencia = 0.0
        # Sin conexiones, el trafico historico se toma de los informes.
        for path in glob.glob(os.path.join(_ROOT, 'output',
                                           'analytics_report_*.json')):
            try:
                with open(path, encoding='utf-8') as f:
                    r = json.load(f) or {}
                trafico += int(r.get('total_entradas', 0) or 0)
                if r.get('permanencia_media_s'):
                    permanencia = max(permanencia,
                                      float(r['permanencia_media_s']))
            except Exception:
                continue

    horas = _por_hora()
    activas = {h: n for h, n in horas.items() if n > 0}
    hora_punta = max(activas, key=lambda h: activas[h]) if activas else None
    hora_valle = min(activas, key=lambda h: activas[h]) if activas else None

    capturas = _captures_index()
    return {
        'status': 'ok',
        'live': live,
        'camaras_activas': len(procs),
        'kpis': {
            'personas_en_area': aforo,
            'trafico_total': trafico,
            'visitantes_unicos': unicos_total,
            'permanencia_media_s': round(permanencia, 1),
            'recurrentes': recurrentes,
            'nuevos': unicos_total - recurrentes,
            'tasa_retorno_pct': round(tasa_retorno, 1),
            'total_capturas': len(capturas),
            'pasillos_activos': len([p for p in reales if p['trafico'] > 0]),
            'pasillos_totales': len(reales),
        },
        'genero': dict(gen),
        'edad': dict(edad),
        'orden_edad': _ORDEN_EDAD,
        'pasillos': reales,
        'pasillos_prueba': len(pasillos) - len(reales),
        'mas_frecuentado': mas,
        'menos_frecuentado': menos,
        'horas': horas,
        'hora_punta': hora_punta,
        'hora_valle': hora_valle,
        'alias_configurables': _alias_path(),
        'timestamp': time.time(),
    }


# ── App ──────────────────────────────────────────────────────────────────

def crear_app() -> FastAPI:
    app = FastAPI(title='Dashboard de Tienda', docs_url=None, redoc_url=None)

    @app.get('/api/resumen')
    def api_resumen():
        try:
            return _construir_resumen()
        except Exception as exc:
            logger.exception('dashboard_tienda resumen error')
            return JSONResponse({'status': 'error', 'message': str(exc)},
                                status_code=500)

    @app.get('/api/heatmap/{nombre}.png')
    def api_heatmap(nombre: str):
        path = os.path.join(_heatmap_dir(), f"{_safe(nombre)}.png")
        if not os.path.isfile(path):
            return JSONResponse({'status': 'error', 'message': 'no existe'},
                                status_code=404)
        return FileResponse(path, media_type='image/png')

    @app.get('/', response_class=HTMLResponse)
    def pagina():
        return HTMLResponse(_HTML)

    return app


_lanzado = False
_lock = threading.Lock()


def _puerto_ocupado(puerto: int, host: str = '127.0.0.1') -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, puerto)) == 0


def iniciar_dashboard_tienda(puerto: int = PUERTO_TIENDA) -> bool:
    """Levanta el dashboard de tienda en un hilo demonio. Idempotente: si ya
    se lanzo en este proceso o el puerto esta ocupado, no hace nada."""
    global _lanzado
    with _lock:
        if _lanzado:
            return False
        if _puerto_ocupado(puerto):
            logger.info('dashboard de tienda: puerto %s ya ocupado', puerto)
            _lanzado = True
            return False
        _lanzado = True

    def _servir() -> None:
        try:
            import uvicorn
            uvicorn.run(crear_app(), host='0.0.0.0', port=puerto,
                        log_level='warning')
        except Exception:
            logger.exception('el dashboard de tienda no pudo iniciar')

    threading.Thread(target=_servir, name='dashboard-tienda',
                     daemon=True).start()
    return True


# ── Pagina (cadena RAW: ver el aviso de la cabecera del modulo) ───────────

_HTML = r"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard de Tienda</title>
<style>
:root{
 color-scheme:dark;
 --page:#0b0d10; --card:#14181d; --card2:#1a1f26; --border:#262d36;
 --txt:#e8edf3; --muted:#8b98a8; --accent:#00a8e8; --good:#2ecc71;
 --warn:#f39c12; --bad:#e74c3c; --f:#e91e8c; --m:#2196f3;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--txt);
 font:14px/1.5 "Segoe UI",system-ui,sans-serif}
a{color:var(--accent)}
header{position:sticky;top:0;z-index:10;background:rgba(11,13,16,.94);
 backdrop-filter:blur(8px);border-bottom:1px solid var(--border);
 padding:14px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{margin:0;font-size:18px;font-weight:650;letter-spacing:.2px}
#estado{font-size:12px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
 background:var(--muted);margin-right:5px;vertical-align:1px}
.dot.on{background:var(--good);box-shadow:0 0 8px var(--good)}
main{padding:20px;max-width:1500px;margin:0 auto}
section{margin-bottom:26px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.8px;
 color:var(--muted);margin:0 0 12px;font-weight:600}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;
 padding:14px 16px}
.kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.6px;
 color:var(--muted)}
.kpi .v{font-size:30px;font-weight:680;line-height:1.15;margin-top:4px}
.kpi .s{font-size:12px;color:var(--muted);margin-top:2px}
.kpi.hi .v{color:var(--accent)}
.two{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
/* destacados de pasillo */
.dest{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.dest .card{display:flex;gap:14px;align-items:center}
.dest .tag{font-size:11px;text-transform:uppercase;letter-spacing:.6px;
 padding:3px 8px;border-radius:20px;font-weight:650;white-space:nowrap}
.tag.top{background:rgba(46,204,113,.15);color:var(--good);
 border:1px solid rgba(46,204,113,.35)}
.tag.low{background:rgba(243,156,18,.15);color:var(--warn);
 border:1px solid rgba(243,156,18,.35)}
.dest .nm{font-size:17px;font-weight:640}
.dest .mt{font-size:12px;color:var(--muted)}
/* barras */
.bar-row{display:grid;grid-template-columns:150px 1fr 60px;gap:10px;
 align-items:center;margin-bottom:7px}
.bar-row .lbl{font-size:12px;color:var(--muted);overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.bar-track{background:var(--card2);border-radius:5px;height:16px;overflow:hidden}
.bar-fill{height:100%;border-radius:5px;transition:width .35s ease}
.bar-row .val{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
/* tabla */
.wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:640px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--border)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.5px;
 color:var(--muted);font-weight:600}
tbody tr:hover{background:var(--card2)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.pill{font-size:11px;padding:2px 7px;border-radius:20px;
 border:1px solid var(--border);color:var(--muted)}
/* heatmaps */
.hm{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.hm figure{margin:0;background:var(--card);border:1px solid var(--border);
 border-radius:10px;overflow:hidden}
.hm img{width:100%;display:block;background:#000;cursor:zoom-in}
.hm figcaption{padding:8px 12px;font-size:12px;color:var(--muted);
 display:flex;justify-content:space-between;gap:8px}
/* horas */
.horas{display:flex;align-items:flex-end;gap:3px;height:130px;
 padding-top:6px}
.horas .h{flex:1;display:flex;flex-direction:column;justify-content:flex-end;
 align-items:center;height:100%;gap:4px}
.horas .hb{width:100%;background:var(--accent);border-radius:3px 3px 0 0;
 min-height:2px;transition:height .35s ease}
.horas .h.punta .hb{background:var(--good)}
.horas .h.valle .hb{background:var(--warn)}
.horas .hl{font-size:9px;color:var(--muted)}
.empty{color:var(--muted);font-size:13px;padding:10px 0}
.nota{font-size:12px;color:var(--muted);line-height:1.6}
.nota b{color:var(--txt)}
/* lightbox */
#lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;
 align-items:center;justify-content:center;z-index:50;cursor:zoom-out}
#lb.on{display:flex}
#lb img{max-width:94vw;max-height:94vh}
@media(max-width:640px){
 .bar-row{grid-template-columns:110px 1fr 48px}
 .kpi .v{font-size:24px}
}
</style>
</head><body>
<header>
 <h1>🛒 Dashboard de Tienda</h1>
 <span id="estado"><span class="dot"></span>cargando…</span>
</header>
<main>

<section>
 <h2>Ahora mismo</h2>
 <div class="grid kpis" id="kpis"></div>
</section>

<section>
 <h2>Pasillos</h2>
 <div class="dest" id="dest"></div>
</section>

<section>
 <h2>Ranking de pasillos por afluencia</h2>
 <div class="card"><div id="rank"></div></div>
</section>

<section class="grid two">
 <div><h2>Genero</h2><div class="card"><div id="gen"></div></div></div>
 <div><h2>Segmento de edad</h2><div class="card"><div id="edad"></div></div></div>
</section>

<section>
 <h2>Afluencia por franja horaria</h2>
 <div class="card">
  <div class="horas" id="horas"></div>
  <div class="nota" id="horas-nota" style="margin-top:10px"></div>
 </div>
</section>

<section>
 <h2>Mapa de calor por pasillo</h2>
 <div class="hm" id="heatmaps"></div>
</section>

<section>
 <h2>Detalle por pasillo</h2>
 <div class="card wrap"><table>
  <thead><tr>
   <th>Pasillo</th><th class="n">Afluencia</th><th class="n">Personas ahora</th>
   <th class="n">Unicos</th><th class="n">Capturas</th><th>Perfil dominante</th>
  </tr></thead><tbody id="tbody"></tbody>
 </table></div>
</section>

<section>
 <h2>Lectura para marketing</h2>
 <div class="card nota" id="marketing"></div>
</section>

</main>
<div id="lb"><img alt="mapa de calor ampliado"></div>
<script>
const $ = s => document.querySelector(s);
const nf = n => (n == null ? '—' : Number(n).toLocaleString('es-VE'));

function dur(s){
 s = Math.round(Number(s) || 0);
 if (s < 60) return s + ' s';
 const m = Math.floor(s / 60), r = s % 60;
 if (m < 60) return m + ' min' + (r ? ' ' + r + ' s' : '');
 return Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
}
function kpi(l, v, s, hi){
 return '<div class="card kpi' + (hi ? ' hi' : '') + '">' +
  '<div class="l">' + l + '</div><div class="v">' + v + '</div>' +
  '<div class="s">' + (s || '') + '</div></div>';
}
function colorGen(k){
 if (/^h/i.test(k)) return 'var(--m)';
 if (/^m/i.test(k)) return 'var(--f)';
 return '#6b7785';
}
function bars(el, dist, colorFn, orden){
 const keys = Object.keys(dist || {});
 if (!keys.length){ el.innerHTML = '<div class="empty">Sin datos todavia.</div>'; return }
 keys.sort((a, b) => orden ? (orden.indexOf(a) - orden.indexOf(b))
                           : (dist[b] - dist[a]));
 const max = Math.max.apply(null, keys.map(k => dist[k]).concat([1]));
 const tot = keys.reduce((s, k) => s + dist[k], 0) || 1;
 el.innerHTML = keys.map(k => {
  const pct = Math.round(1000 * dist[k] / tot) / 10;
  return '<div class="bar-row" title="' + k + ': ' + dist[k] + ' (' + pct + '%)">' +
   '<div class="lbl">' + k + '</div>' +
   '<div class="bar-track"><div class="bar-fill" style="width:' +
    Math.max(2, 100 * dist[k] / max) + '%;background:' + colorFn(k) + '"></div></div>' +
   '<div class="val">' + dist[k] + '</div></div>';
 }).join('');
}
function dominante(p){
 const g = Object.entries(p.genero || {}).filter(x => x[0] !== 'Desconocido')
            .sort((a, b) => b[1] - a[1])[0];
 const e = Object.entries(p.edad || {}).filter(x => x[0] !== 'Desconocido')
            .sort((a, b) => b[1] - a[1])[0];
 if (!g && !e) return '<span class="pill">sin datos</span>';
 return (g ? g[0] : '?') + ' · ' + (e ? e[0] : '?');
}

async function refrescar(){
 let d;
 try {
  const r = await fetch('/api/resumen');
  d = await r.json();
  if (d.status !== 'ok') throw new Error(d.message || 'error');
 } catch (e) {
  $('#estado').innerHTML = '<span class="dot"></span>error: ' + e.message;
  return;
 }
 const k = d.kpis;

 $('#estado').innerHTML = '<span class="dot' + (d.live ? ' on' : '') + '"></span>' +
  (d.live ? 'EN VIVO · ' + d.camaras_activas + ' camara(s)'
          : 'sin conexiones activas · datos de disco') +
  ' · ' + new Date().toLocaleTimeString('es-VE');

 $('#kpis').innerHTML =
  kpi('Personas en el area', nf(k.personas_en_area), d.live ? 'aforo en vivo' : 'requiere conexion', true) +
  kpi('Trafico total', nf(k.trafico_total), 'entradas registradas') +
  kpi('Visitantes unicos', nf(k.visitantes_unicos), 'identidades Re-ID') +
  kpi('Permanencia media', dur(k.permanencia_media_s), 'tiempo dentro del area') +
  kpi('Tasa de retorno', k.tasa_retorno_pct + '%', nf(k.recurrentes) + ' recurrentes de ' + nf(k.visitantes_unicos)) +
  kpi('Pasillos con datos', nf(k.pasillos_activos) + ' / ' + nf(k.pasillos_totales), 'camaras reportando');

 // Destacados: mas y menos frecuentado
 const dst = [];
 if (d.mas_frecuentado){
  const p = d.mas_frecuentado;
  dst.push('<div class="card"><span class="tag top">Mas frecuentado</span>' +
   '<div><div class="nm">' + p.nombre + '</div><div class="mt">' +
   nf(p.trafico) + ' ' + p.metrica + ' · ' + nf(p.capturas) + ' capturas</div></div></div>');
 }
 if (d.menos_frecuentado){
  const p = d.menos_frecuentado;
  dst.push('<div class="card"><span class="tag low">Menos frecuentado</span>' +
   '<div><div class="nm">' + p.nombre + '</div><div class="mt">' +
   nf(p.trafico) + ' ' + p.metrica + ' · ' + nf(p.capturas) + ' capturas</div></div></div>');
 }
 $('#dest').innerHTML = dst.length ? dst.join('')
  : '<div class="card empty">Sin afluencia registrada por pasillo todavia.</div>';

 // Ranking
 const rk = {};
 (d.pasillos || []).forEach(p => { if (p.trafico > 0) rk[p.nombre] = p.trafico });
 bars($('#rank'), rk, () => 'var(--accent)');

 bars($('#gen'), d.genero, colorGen);
 bars($('#edad'), d.edad, () => 'var(--accent)', d.orden_edad);

 // Horas
 const hs = d.horas || {};
 const maxh = Math.max.apply(null, Object.values(hs).concat([1]));
 $('#horas').innerHTML = Object.keys(hs).sort().map(h => {
  const cls = (h === d.hora_punta) ? ' punta' : (h === d.hora_valle ? ' valle' : '');
  return '<div class="h' + cls + '" title="' + h + ':00 — ' + hs[h] + ' detecciones">' +
   '<div class="hb" style="height:' + (100 * hs[h] / maxh) + '%"></div>' +
   '<div class="hl">' + h + '</div></div>';
 }).join('');
 $('#horas-nota').innerHTML = d.hora_punta
  ? 'Hora punta: <b>' + d.hora_punta + ':00</b> (' + nf(hs[d.hora_punta]) +
    ' detecciones) · Hora valle: <b>' + d.hora_valle + ':00</b> (' +
    nf(hs[d.hora_valle]) + ')'
  : 'Todavia no hay capturas con hora para calcular la franja punta.';

 // Heatmaps
 const conMapa = (d.pasillos || []).filter(p => p.heatmap);
 $('#heatmaps').innerHTML = conMapa.length ? conMapa.map(p =>
  '<figure><img src="/api/heatmap/' + p.heatmap.replace(/\.png$/, '') +
   '.png?t=' + Date.now() + '" alt="mapa de calor de ' + p.nombre + '">' +
  '<figcaption><span>' + p.nombre + '</span><span>' +
   nf(p.zonas_calientes.length) + ' zonas calientes</span></figcaption></figure>'
 ).join('') : '<div class="card empty">Sin mapas de calor todavia. Se generan al analizar con el mapa de calor activado.</div>';

 // Tabla
 const ps = d.pasillos || [];
 $('#tbody').innerHTML = ps.length ? ps.map(p =>
  '<tr><td>' + p.nombre + (p.tiene_alias ? '' :
    ' <span class="pill">sin nombre</span>') + '</td>' +
  '<td class="n">' + nf(p.trafico) + '</td>' +
  '<td class="n">' + nf(p.aforo) + '</td>' +
  '<td class="n">' + nf(p.unicos) + '</td>' +
  '<td class="n">' + nf(p.capturas) + '</td>' +
  '<td>' + dominante(p) + '</td></tr>'
 ).join('') : '<tr><td colspan="6" class="empty">Sin pasillos detectados.</td></tr>';

 // Lectura de marketing
 const acc = [];
 if (d.mas_frecuentado && d.menos_frecuentado){
  acc.push('<b>' + d.mas_frecuentado.nombre + '</b> concentra la mayor afluencia: ' +
   'es donde mas rinde una promocion o un exhibidor. <b>' +
   d.menos_frecuentado.nombre + '</b> es el punto frio: revisar senalizacion, ' +
   'iluminacion o reubicar producto gancho.');
 }
 const gtop = Object.entries(d.genero || {}).filter(x => x[0] !== 'Desconocido')
               .sort((a, b) => b[1] - a[1])[0];
 const etop = Object.entries(d.edad || {}).filter(x => x[0] !== 'Desconocido')
               .sort((a, b) => b[1] - a[1])[0];
 if (gtop && etop){
  acc.push('Perfil dominante: <b>' + gtop[0] + '</b>, franja <b>' + etop[0] +
   '</b>. Orientar surtido, tono de comunicacion y musica a ese segmento.');
 }
 if (d.hora_punta){
  acc.push('Concentrar personal y degustaciones alrededor de las <b>' +
   d.hora_punta + ':00</b>; usar las <b>' + d.hora_valle +
   ':00</b> para reposicion sin estorbar al cliente.');
 }
 if (k.visitantes_unicos){
  acc.push('Recurrencia: <b>' + k.tasa_retorno_pct + '%</b> de los visitantes ' +
   'identificados repiten visita (' + nf(k.recurrentes) + ' de ' +
   nf(k.visitantes_unicos) + '). Es la base para medir fidelizacion.');
 }
 acc.push('<b>Consumo y ticket medio:</b> este sistema mide comportamiento ' +
  '(trafico, permanencia, recurrencia, perfil), no ventas. Para conversion ' +
  'y ticket real hay que cruzar estos datos con el punto de venta; no se ' +
  'muestran cifras de consumo porque no existen aqui.');
 if (d.pasillos_prueba){
  acc.push('<span class="pill">' + d.pasillos_prueba + ' camara(s) de prueba ocultas</span>');
 }
 acc.push('Los nombres de pasillo se editan en <b>' + d.alias_configurables + '</b>.');
 $('#marketing').innerHTML = acc.map(x => '<p style="margin:0 0 9px">' + x + '</p>').join('');
}

// Lightbox del mapa de calor
document.addEventListener('click', e => {
 if (e.target.tagName === 'IMG' && e.target.closest('.hm')){
  $('#lb img').src = e.target.src;
  $('#lb').classList.add('on');
 } else if (e.target.closest('#lb')){
  $('#lb').classList.remove('on');
 }
});

refrescar();
setInterval(refrescar, 5000);
</script>
</body></html>
"""
