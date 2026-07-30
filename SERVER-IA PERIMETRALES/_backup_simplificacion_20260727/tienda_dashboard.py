"""
webapp/tienda_dashboard.py - Dashboard INDEPENDIENTE de TIENDA (puerto 5030).

Panel propio de analitica de supermercado para SEGURIDAD y MARKETING. No
reutiliza la pagina de "Rostros Reconocidos" de otro proyecto: es una app
FastAPI aparte, con su propia UI y sus propios endpoints.

Lee DIRECTAMENTE los archivos que produce el sistema (desacoplado del proceso
de inferencia; nunca lo bloquea):

  output/retail/<cam>.json        trafico de pasillos, permanencia, ventas,
                                  demografia, cajas, reposicion, merodeo,
                                  evaluaciones, eventos recientes
  output/person_db/persons.pkl    rostros unicos + genero + edad + visitas
  output/person_db/faces/<uid>.jpg
  output/heatmap/<cam>.png|.json  mapa de calor por camara + zonas calientes
  output/detecciones/<tipo>/...   evidencia fotografica de eventos

Arranque:
    venv\\Scripts\\python.exe webapp\\tienda_dashboard.py [puerto]
"""
from __future__ import annotations

import glob
import json
import os
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response

# ── Rutas (relativas a la raiz del servidor) ─────────────────────────
ROOT = Path(__file__).resolve().parent.parent          # .../SERVER-IA PERIMETRALES
OUT = ROOT / "output"
RETAIL_DIR = OUT / "retail"
DB_PATH = OUT / "person_db" / "persons.pkl"
FACES_DIR = OUT / "person_db" / "faces"
HEATMAP_DIR = OUT / "heatmap"
DETECC_DIR = OUT / "detecciones"

app = FastAPI(title="ELDE Tienda - Dashboard")


# ── Carga de datos (defensiva: todo puede no existir aun) ────────────

def _load_retail_reports() -> List[Dict[str, Any]]:
    """Todos los reportes de retail (uno por camara)."""
    out = []
    for p in sorted(glob.glob(str(RETAIL_DIR / "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            d["_archivo"] = os.path.basename(p)
            out.append(d)
        except Exception:
            continue
    return out


def _load_person_db() -> List[Dict[str, Any]]:
    """Personas unicas de la galeria biometrica (rostro + genero + edad)."""
    if not DB_PATH.is_file():
        return []
    try:
        with open(DB_PATH, "rb") as f:
            payload = pickle.load(f)
        db = payload.get("db", {}) if isinstance(payload, dict) else {}
    except Exception:
        return []
    personas = []
    for uid, rec in db.items():
        personas.append({
            "uuid": uid,
            "gender": rec.get("gender") or "Desconocido",
            "age_range": rec.get("age_range") or "Desconocido",
            "visit_count": int(rec.get("visit_count", 1)),
            "first_seen": float(rec.get("first_seen", 0.0)),
            "last_seen": float(rec.get("last_seen", 0.0)),
            "face": (FACES_DIR / f"{uid}.jpg").is_file(),
        })
    personas.sort(key=lambda p: p["last_seen"], reverse=True)
    return personas


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── API ──────────────────────────────────────────────────────────────

@app.get("/api/tienda/resumen")
def api_resumen():
    """KPIs agregados de toda la tienda (seguridad + marketing)."""
    reps = _load_retail_reports()
    personas = _load_person_db()
    kpi = {
        "camaras_activas": len(reps),
        "rostros_unicos": len(personas),
        "visitantes_pasillos": 0,
        "permanencia_media_s": 0.0,
        "agarres_cliente": 0,
        "evaluaciones": 0,
        "evaluando_ahora": 0,
        "cajas_en_piso": 0,
        "reposiciones": 0,
        "merodeo_activo": 0,
        "productos_llevados": 0,
        "ingreso_estimado": 0.0,
        "anaqueles_vacios": 0,
    }
    perm_acc, perm_n = 0.0, 0
    for r in reps:
        tp = r.get("trafico_pasillos") or {}
        for p in tp.get("pasillos") or []:
            kpi["visitantes_pasillos"] += int(p.get("visitantes_unicos", 0))
            pm = _num(p.get("permanencia_media_s"))
            if pm > 0:
                perm_acc += pm
                perm_n += 1
        ev = r.get("evaluacion") or {}
        kpi["agarres_cliente"] += int(ev.get("agarres_cliente", 0))
        kpi["evaluaciones"] += int(ev.get("evaluaciones_totales", 0))
        kpi["evaluando_ahora"] += int(ev.get("evaluando_ahora", 0))
        kpi["cajas_en_piso"] += int(
            (r.get("cajas") or {}).get("cajas_en_piso_ahora", 0))
        kpi["reposiciones"] += int(
            (r.get("reposicion_empleados") or {}).get(
                "reposiciones_totales", 0))
        mer = r.get("merodeo") or {}
        kpi["merodeo_activo"] += len(mer.get("pasillos_con_merodeo") or [])
        ventas = (r.get("ventas") or {}).get("resumen") or {}
        kpi["productos_llevados"] += int(ventas.get("productos_llevados", 0))
        kpi["ingreso_estimado"] += _num(
            (r.get("ventas") or {}).get("ingreso_estimado_total"))
        kpi["anaqueles_vacios"] += len(
            (r.get("stock") or {}).get("anaqueles_vacios") or [])
    kpi["permanencia_media_s"] = round(perm_acc / perm_n, 1) if perm_n else 0.0
    kpi["ingreso_estimado"] = round(kpi["ingreso_estimado"], 2)
    kpi["actualizado"] = time.strftime("%H:%M:%S")
    return kpi


@app.get("/api/tienda/demografia")
def api_demografia():
    """Distribucion de genero y edad de los visitantes UNICOS (person DB) +
    segmentos de marketing (de los reportes de retail)."""
    personas = _load_person_db()
    genero: Dict[str, int] = {}
    edad: Dict[str, int] = {}
    for p in personas:
        genero[p["gender"]] = genero.get(p["gender"], 0) + 1
        edad[p["age_range"]] = edad.get(p["age_range"], 0) + 1
    # Segmentos de marketing (Nino/Hombre/Mujer/Anciano + conversion)
    segmentos: Dict[str, Dict[str, Any]] = {}
    for r in _load_retail_reports():
        for s in (((r.get("ventas") or {}).get("demografia") or {})
                  .get("segmentos") or []):
            k = s.get("segmento", "Desconocido")
            acc = segmentos.setdefault(k, {"personas": 0, "llevados": 0,
                                           "ingreso": 0.0})
            acc["personas"] += int(s.get("personas", 0))
            acc["llevados"] += int(s.get("productos_llevados", 0))
            acc["ingreso"] += _num(s.get("ingreso_estimado"))
    return {"total_unicos": len(personas), "genero": genero, "edad": edad,
            "segmentos": segmentos}


@app.get("/api/tienda/pasillos")
def api_pasillos():
    """Trafico + permanencia por pasillo, con la camara de origen."""
    filas = []
    resumen_global = {"mas_transitado": None, "mayor_concentracion": None}
    mejor_vis = -1
    for r in _load_retail_reports():
        cam = r.get("camera_id", r.get("_archivo", "?"))
        tp = r.get("trafico_pasillos") or {}
        for p in tp.get("pasillos") or []:
            fila = {
                "camara": cam,
                "pasillo": p.get("pasillo", "?"),
                "visitantes_unicos": int(p.get("visitantes_unicos", 0)),
                "ocupacion_actual": int(p.get("ocupacion_actual", 0)),
                "ocupacion_pico": int(p.get("ocupacion_pico", 0)),
                "permanencia_media_s": _num(p.get("permanencia_media_s")),
                "densidad_actual": _num(p.get("densidad_actual")),
            }
            filas.append(fila)
            if fila["visitantes_unicos"] > mejor_vis:
                mejor_vis = fila["visitantes_unicos"]
                resumen_global["mas_transitado"] = fila["pasillo"]
    filas.sort(key=lambda x: x["visitantes_unicos"], reverse=True)
    return {"pasillos": filas, "resumen": resumen_global}


@app.get("/api/tienda/merodeo")
def api_merodeo():
    """Personas merodeando (permanencia alta en pasillo) — seguridad."""
    out = []
    umbral = 90.0
    for r in _load_retail_reports():
        cam = r.get("camera_id", r.get("_archivo", "?"))
        mer = r.get("merodeo") or {}
        umbral = mer.get("umbral_s", umbral)
        for p in mer.get("pasillos_con_merodeo") or []:
            out.append({"camara": cam, **p})
    out.sort(key=lambda x: _num(x.get("permanencia_media_s")), reverse=True)
    return {"umbral_s": umbral, "merodeo": out}


@app.get("/api/tienda/rostros")
def api_rostros(limit: int = 60):
    """Galeria de rostros unicos con genero/edad/visitas."""
    personas = _load_person_db()
    return {"total": len(personas), "personas": personas[:limit]}


@app.get("/api/tienda/rostro/{uuid}.jpg")
def api_rostro(uuid: str):
    # Seguridad: solo nombres simples (evita path traversal)
    safe = "".join(c for c in uuid if c.isalnum() or c in "_-")
    p = FACES_DIR / f"{safe}.jpg"
    if p.is_file():
        return FileResponse(str(p), media_type="image/jpeg")
    return Response(status_code=404)


@app.get("/api/tienda/heatmaps")
def api_heatmaps():
    """Camaras con mapa de calor disponible + zonas calientes."""
    out = []
    for png in sorted(glob.glob(str(HEATMAP_DIR / "*.png"))):
        name = os.path.splitext(os.path.basename(png))[0]
        info = {"camara": name, "img": f"/api/tienda/heatmap/{name}.png",
                "zonas_calientes": [], "muestras": 0}
        jp = HEATMAP_DIR / f"{name}.json"
        if jp.is_file():
            try:
                meta = json.loads(jp.read_text(encoding="utf-8"))
                info["zonas_calientes"] = meta.get("zonas_calientes", [])
                info["muestras"] = meta.get("muestras", 0)
                info["camera_name"] = meta.get("camera_name", name)
            except Exception:
                pass
        out.append(info)
    return {"heatmaps": out}


@app.get("/api/tienda/heatmap/{name}.png")
def api_heatmap_img(name: str):
    safe = "".join(c for c in name if c.isalnum() or c in "_-")
    p = HEATMAP_DIR / f"{safe}.png"
    if p.is_file():
        return FileResponse(str(p), media_type="image/png")
    return Response(status_code=404)


_EVENT_LABELS = {
    "cliente_agarra_producto": ("Cliente agarra producto", "#2ecc71"),
    "persona_evaluando": ("Persona evaluando producto", "#00c8ff"),
    "reposicion_iniciada": ("Empleado reponiendo", "#f1c40f"),
    "caja_detectada": ("Caja en el piso", "#b06a2c"),
    "caja_obstruccion": ("Caja obstruyendo", "#e67e22"),
    "anaquel_vacio": ("Anaquel vacio", "#e74c3c"),
    "consulta_precio": ("Consulta de precio", "#9b59b6"),
}


@app.get("/api/tienda/eventos")
def api_eventos(limit: int = 40):
    """Feed de eventos de seguridad/operacion con su foto (de detecciones)."""
    evs = []
    for tipo_dir in sorted(glob.glob(str(DETECC_DIR / "*"))):
        if not os.path.isdir(tipo_dir):
            continue
        tipo = os.path.basename(tipo_dir)
        label, color = _EVENT_LABELS.get(tipo, (tipo, "#888"))
        for jpg in glob.glob(os.path.join(tipo_dir, "*", "*.jpg")):
            try:
                mt = os.path.getmtime(jpg)
            except OSError:
                continue
            rel = os.path.relpath(jpg, DETECC_DIR).replace("\\", "/")
            evs.append({"tipo": tipo, "label": label, "color": color,
                        "ts": mt, "hora": time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(mt)),
                        "foto": f"/api/tienda/evento_foto/{rel}"})
    evs.sort(key=lambda e: e["ts"], reverse=True)
    return {"eventos": evs[:limit]}


@app.get("/api/tienda/evento_foto/{ruta:path}")
def api_evento_foto(ruta: str):
    # Seguridad: resolver DENTRO de detecciones y verificar que no escapa.
    p = (DETECC_DIR / ruta).resolve()
    try:
        p.relative_to(DETECC_DIR.resolve())
    except ValueError:
        return Response(status_code=403)
    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return FileResponse(str(p), media_type="image/jpeg")
    return Response(status_code=404)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_PAGE)


# ── Pagina (autocontenida: HTML + CSS + JS inline) ───────────────────
_PAGE = r"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ELDE Tienda - Dashboard</title>
<style>
  :root{--bg:#0f1115;--card:#1a1d24;--card2:#20242d;--bd:#2a2f3a;--tx:#e6e9ef;
        --mut:#8b93a3;--ac:#00c8ff;--ok:#2ecc71;--warn:#f1c40f;--bad:#e74c3c;}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);
    font-family:Segoe UI,Roboto,system-ui,sans-serif;font-size:14px}
  header{display:flex;align-items:center;gap:12px;padding:14px 22px;
    background:linear-gradient(180deg,#191d26,#12151b);border-bottom:1px solid var(--bd);
    position:sticky;top:0;z-index:5}
  header h1{font-size:17px;margin:0;font-weight:700;letter-spacing:.3px}
  header .tag{color:var(--mut);font-size:12px}
  header .upd{margin-left:auto;color:var(--mut);font-size:12px}
  .wrap{padding:18px 22px;max-width:1500px;margin:0 auto}
  .kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
  .kpi{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px 16px}
  .kpi .v{font-size:26px;font-weight:800;line-height:1.1}
  .kpi .l{color:var(--mut);font-size:12px;margin-top:4px}
  .kpi.alert .v{color:var(--bad)} .kpi.good .v{color:var(--ok)} .kpi.warn .v{color:var(--warn)}
  h2{font-size:14px;color:var(--mut);text-transform:uppercase;letter-spacing:1px;
     margin:26px 0 10px;font-weight:700}
  .grid{display:grid;gap:14px}
  .g2{grid-template-columns:1fr 1fr} .g3{grid-template-columns:2fr 1fr}
  @media(max-width:900px){.g2,.g3{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px}
  .card h3{margin:0 0 12px;font-size:14px}
  .bar{display:flex;align-items:center;gap:8px;margin:6px 0}
  .bar .n{width:120px;color:var(--mut);font-size:12px;text-align:right;flex:none}
  .bar .t{flex:1;background:#12151b;border-radius:5px;overflow:hidden;height:18px}
  .bar .t>span{display:block;height:100%;background:linear-gradient(90deg,#00a8e8,#00c8ff)}
  .bar .c{width:42px;font-size:12px;font-weight:700}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--bd)}
  th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase}
  .pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700}
  .faces{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px}
  .face{background:var(--card2);border:1px solid var(--bd);border-radius:8px;overflow:hidden;text-align:center}
  .face img{width:100%;height:96px;object-fit:cover;background:#000}
  .face .m{padding:5px 4px;font-size:11px} .face .m b{color:var(--tx)}
  .hm{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
  .hm figure{margin:0;background:var(--card2);border:1px solid var(--bd);border-radius:10px;overflow:hidden}
  .hm img{width:100%;display:block;background:#000}
  .hm figcaption{padding:8px 10px;font-size:12px;color:var(--mut)}
  .feed{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
  .ev{background:var(--card2);border:1px solid var(--bd);border-radius:8px;overflow:hidden}
  .ev img{width:100%;height:100px;object-fit:cover;background:#000}
  .ev .m{padding:6px 8px;font-size:11px}
  .empty{color:var(--mut);font-style:italic;padding:8px 2px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
</style></head>
<body>
<header>
  <span style="font-size:20px">🛒</span>
  <div><h1>ELDE Tienda</h1><div class="tag">Analitica de supermercado · seguridad + marketing</div></div>
  <div class="upd" id="upd">—</div>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>

  <div class="grid g2" style="margin-top:6px">
    <div class="card"><h3>👥 Genero (visitantes unicos)</h3><div id="genero"></div></div>
    <div class="card"><h3>🎂 Edad (visitantes unicos)</h3><div id="edad"></div></div>
  </div>

  <h2>🚶 Trafico y permanencia por pasillo</h2>
  <div class="card"><div id="pasillos"></div></div>

  <h2>🕵️ Merodeo en pasillos (seguridad)</h2>
  <div class="card"><div id="merodeo"></div></div>

  <div class="grid g3">
    <div><h2>🔥 Mapa de calor</h2><div class="card"><div class="hm" id="heat"></div></div></div>
    <div><h2>🧑 Segmentos (marketing)</h2><div class="card"><div id="segmentos"></div></div></div>
  </div>

  <h2>🙂 Rostros detectados</h2>
  <div class="card"><div class="faces" id="rostros"></div></div>

  <h2>🚨 Eventos recientes (evidencia)</h2>
  <div class="card"><div class="feed" id="eventos"></div></div>
</div>

<script>
const $ = id => document.getElementById(id);
const j = async u => { try{ const r=await fetch(u); return await r.json(); }catch(e){ return null; } };
const fmtSec = s => { s=Math.round(s||0); return s>=60 ? (s/60|0)+"m "+(s%60)+"s" : s+"s"; };

function bars(el, obj, palette){
  const ents = Object.entries(obj||{}).sort((a,b)=>b[1]-a[1]);
  if(!ents.length){ el.innerHTML='<div class="empty">Sin datos aun</div>'; return; }
  const max = Math.max(...ents.map(e=>e[1]))||1;
  el.innerHTML = ents.map(([k,v])=>`<div class="bar"><div class="n">${k}</div>
    <div class="t"><span style="width:${Math.round(v/max*100)}%;${palette?('background:'+palette(k)):''}"></span></div>
    <div class="c">${v}</div></div>`).join('');
}
const gcolor = k => ({Hombre:'#2e86de',Mujer:'#e84393',Desconocido:'#7f8c8d'})[k]||'#00c8ff';

async function tick(){
  const k = await j('/api/tienda/resumen');
  if(k){
    $('upd').textContent = 'Actualizado '+ (k.actualizado||'');
    const K=(v,l,cls='')=>`<div class="kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
    $('kpis').innerHTML =
      K(k.rostros_unicos,'Rostros unicos','good')+
      K(k.visitantes_pasillos,'Visitantes en pasillos')+
      K(fmtSec(k.permanencia_media_s),'Permanencia media')+
      K(k.agarres_cliente,'Agarres de producto')+
      K(k.evaluando_ahora,'Evaluando ahora')+
      K(k.merodeo_activo,'Merodeo activo', k.merodeo_activo? 'alert':'')+
      K(k.cajas_en_piso,'Cajas en piso', k.cajas_en_piso? 'warn':'')+
      K(k.anaqueles_vacios,'Anaqueles vacios', k.anaqueles_vacios? 'alert':'')+
      K(k.productos_llevados,'Productos llevados')+
      K('$'+k.ingreso_estimado,'Ingreso estimado','good');
  }
  const d = await j('/api/tienda/demografia');
  if(d){ bars($('genero'), d.genero, gcolor); bars($('edad'), d.edad);
    const segs=Object.entries(d.segmentos||{});
    $('segmentos').innerHTML = segs.length ? segs.map(([s,o])=>
      `<div class="bar"><div class="n">${s}</div><div class="t"><span style="width:${Math.min(100,o.personas*10)}%"></span></div><div class="c">${o.personas}</div></div>`
      ).join('') : '<div class="empty">Sin datos de compra aun</div>';
  }
  const pa = await j('/api/tienda/pasillos');
  if(pa){ const r=pa.pasillos||[];
    $('pasillos').innerHTML = r.length ? `<table><tr><th>Pasillo</th><th>Camara</th>
      <th>Visitantes</th><th>Ahora</th><th>Pico</th><th>Permanencia</th></tr>`+
      r.map(p=>`<tr><td><b>${p.pasillo}</b></td><td>${p.camara}</td>
      <td>${p.visitantes_unicos}</td><td>${p.ocupacion_actual}</td><td>${p.ocupacion_pico}</td>
      <td>${fmtSec(p.permanencia_media_s)}</td></tr>`).join('')+`</table>` :
      '<div class="empty">Sin pasillos definidos / sin trafico aun. Define pasillos en el cliente (menu Tienda).</div>';
  }
  const me = await j('/api/tienda/merodeo');
  if(me){ const r=me.merodeo||[];
    $('merodeo').innerHTML = r.length ? `<table><tr><th></th><th>Pasillo</th><th>Camara</th>
      <th>Permanencia media</th><th>Pico personas</th></tr>`+
      r.map(m=>`<tr><td><span class="dot" style="background:var(--bad)"></span></td>
      <td><b>${m.pasillo}</b></td><td>${m.camara}</td>
      <td>${fmtSec(m.permanencia_media_s)}</td><td>${m.ocupacion_pico||0}</td></tr>`).join('')+`</table>` :
      `<div class="empty">Sin merodeo detectado (umbral ${me.umbral_s}s de permanencia).</div>`;
  }
  const h = await j('/api/tienda/heatmaps');
  if(h){ const r=h.heatmaps||[];
    $('heat').innerHTML = r.length ? r.map(x=>`<figure>
      <img src="${x.img}?t=${Date.now()}" alt="heatmap ${x.camara}">
      <figcaption>${x.camera_name||x.camara} · ${x.muestras} muestras · ${(x.zonas_calientes||[]).length} zonas calientes</figcaption>
      </figure>`).join('') : '<div class="empty">Sin mapas de calor aun (se generan con la IA activa).</div>';
  }
  const ro = await j('/api/tienda/rostros');
  if(ro){ const r=ro.personas||[];
    $('rostros').innerHTML = r.length ? r.map(p=>`<div class="face">
      ${p.face?`<img src="/api/tienda/rostro/${p.uuid}.jpg" loading="lazy">`:`<div style="height:96px;display:flex;align-items:center;justify-content:center;background:#000;color:#555">sin foto</div>`}
      <div class="m"><b>${p.gender}</b> · ${p.age_range}<br><span style="color:var(--mut)">${p.visit_count} visita(s)</span></div>
      </div>`).join('') : '<div class="empty">Sin rostros registrados aun.</div>';
  }
  const ev = await j('/api/tienda/eventos');
  if(ev){ const r=ev.eventos||[];
    $('eventos').innerHTML = r.length ? r.map(e=>`<div class="ev">
      <img src="${e.foto}" loading="lazy" alt="${e.label}">
      <div class="m"><span class="dot" style="background:${e.color}"></span><b>${e.label}</b><br>
      <span style="color:var(--mut)">${e.hora}</span></div></div>`).join('') :
      '<div class="empty">Sin eventos registrados aun.</div>';
  }
}
tick(); setInterval(tick, 5000);
</script>
</body></html>"""


if __name__ == "__main__":
    import sys
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 5030
    print(f"Dashboard de TIENDA en http://localhost:{puerto}")
    print(f"  Lee datos de: {OUT}")
    uvicorn.run(app, host="0.0.0.0", port=puerto, log_level="warning")
