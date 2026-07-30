"""
Panel de VIGILANTE-AMAZONAS (:5333) — detecciones, totales y control del VLM.

Sustituye al dashboard de galería que vivía en :8090, pero NO lo tira: la
gestión de personas de interés (altas, bajas, fotos de referencia, historial)
sigue siendo imprescindible para el Re-ID, así que aquella app se monta entera
bajo `/gestion`. Un solo puerto, ninguna función perdida.

Qué muestra:
  * Totales de PERSONAS y VEHÍCULOS, separados, sobre todo el histórico.
  * Galería de las capturas que guarda el cliente en `screenshots/`.
  * Interruptor del VLM verificador (el mismo que el botón del cliente).

Las capturas las escribe el CLIENTE (perimetrales-view) al recibir cada
alerta. La ruta se configura con `VIGILANTE_SCREENSHOTS`; por defecto se busca
`perimetrales-view/screenshots` junto al servidor. Si el cliente corre en otra
máquina hay que apuntar esa variable a una carpeta compartida.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

# Extensiones que se sirven de la carpeta de capturas.
_IMAGENES = (".jpg", ".jpeg", ".png")


def _modelo_corto(id_modelo: str) -> str:
    """'Qwen/Qwen2.5-VL-3B-Instruct' -> '3B'."""
    coincidencia = re.search(r"(\d+\.?\d*)B", id_modelo or "", re.IGNORECASE)
    return f"{coincidencia.group(1)}B" if coincidencia else ""


def carpeta_screenshots() -> Path:
    """Carpeta de capturas del cliente."""
    explicita = (os.getenv("VIGILANTE_SCREENSHOTS") or "").strip()
    if explicita:
        return Path(explicita)
    # <ELDE>/clients/perimetrales/screenshots, hermano de server/.
    return config.RUTA_CLIENTE_VIEW / "screenshots"


def _leer_sidecar(jpg: Path) -> Dict[str, Any]:
    try:
        with open(jpg.with_suffix(".json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _indice(limite: int = 0) -> List[Dict[str, Any]]:
    """Capturas más recientes primero, con lo que diga su sidecar."""
    carpeta = carpeta_screenshots()
    if not carpeta.is_dir():
        return []
    try:
        nombres = sorted((n for n in os.listdir(carpeta)
                          if n.lower().endswith(_IMAGENES)), reverse=True)
    except OSError:
        return []
    if limite > 0:
        nombres = nombres[:limite]
    salida: List[Dict[str, Any]] = []
    for nombre in nombres:
        jpg = carpeta / nombre
        meta = _leer_sidecar(jpg)
        salida.append({
            "archivo": nombre,
            "clase": meta.get("clase") or "",
            "clase_gruesa": meta.get("clase_gruesa") or "",
            "evento": meta.get("evento") or "",
            "camara": meta.get("camara") or "",
            "timestamp": meta.get("timestamp") or "",
            "global_id": meta.get("global_id") or "",
            "permanencia_s": meta.get("permanencia_s"),
            # Sin sidecar no se puede saber la clase: el nombre del archivo
            # viene saneado y pierde los acentos. Se marca para no contarlo
            # mal en los totales.
            "sin_metadatos": not bool(meta),
        })
    return salida


def crear_panel(motor_deteccion: Any = None) -> FastAPI:
    """App del panel (:5333) con la gestión del Re-ID montada en /gestion."""
    app = FastAPI(title="VIGILANTE-AMAZONAS · Panel", docs_url="/docs")

    # ── API ──────────────────────────────────────────────────────────
    @app.get("/api/resumen")
    def resumen() -> Dict[str, Any]:
        """Totales por clase gruesa y por clase concreta."""
        registros = _indice()
        gruesas = Counter(r["clase_gruesa"] for r in registros
                          if r["clase_gruesa"] in ("persona", "vehiculo"))
        clases = Counter(r["clase"] for r in registros if r["clase"])
        eventos = Counter(r["evento"] for r in registros if r["evento"])
        camaras = Counter(r["camara"] for r in registros if r["camara"])
        personas = gruesas.get("persona", 0)
        vehiculos = gruesas.get("vehiculo", 0)
        return {
            "estado": "ok",
            "personas": personas,
            "vehiculos": vehiculos,
            "total": personas + vehiculos,
            "capturas": len(registros),
            "sin_metadatos": sum(1 for r in registros if r["sin_metadatos"]),
            "por_clase": dict(clases.most_common()),
            "por_evento": dict(eventos.most_common()),
            "por_camara": dict(camaras.most_common(12)),
            "carpeta": str(carpeta_screenshots()),
        }

    @app.get("/api/detecciones")
    def detecciones(limite: int = 200, gruesa: str = "") -> Dict[str, Any]:
        """Galería. `gruesa` filtra por 'persona' o 'vehiculo'."""
        registros = _indice()
        gruesa = (gruesa or "").strip().lower()
        if gruesa in ("persona", "vehiculo"):
            registros = [r for r in registros if r["clase_gruesa"] == gruesa]
        total = len(registros)
        if limite > 0:
            registros = registros[:limite]
        return {"estado": "ok", "total": total, "detecciones": registros}

    @app.get("/img/{archivo}")
    def imagen(archivo: str):
        """Sirve una captura. Bloquea cualquier salto de directorio."""
        carpeta = carpeta_screenshots().resolve()
        destino = (carpeta / archivo).resolve()
        if carpeta not in destino.parents or not destino.is_file():
            return JSONResponse({"estado": "error",
                                 "mensaje": "no encontrada"}, status_code=404)
        return FileResponse(str(destino))

    @app.get("/api/vlm")
    def vlm_estado() -> Dict[str, Any]:
        """Si el VLM verificador está encendido, y si llegó a cargar."""
        from vigilante_amazonas.servicios.verificador_vlm import (
            obtener_vlm, vlm_activo)
        vivo = obtener_vlm()
        return {
            "estado": "ok",
            "activo": vlm_activo(),
            "cargado": bool(vivo and vivo.cargado),
            "presente": vivo is not None,
            # Nombre corto: el completo ("Qwen2.5-VL-3B-Instruct") no cabe
            # en el boton del cliente.
            "modelo": _modelo_corto(config.VLM_MODELO_ID),
            "atendidas": getattr(vivo, "consultas_atendidas", 0),
            "descartadas": getattr(vivo, "consultas_descartadas", 0),
        }

    @app.post("/api/vlm")
    def vlm_cambiar(activo: bool = True) -> Dict[str, Any]:
        """Enciende o apaga el VLM. La preferencia sobrevive al reinicio."""
        from vigilante_amazonas.servicios.verificador_vlm import fijar_vlm
        vigente = fijar_vlm(bool(activo))
        return {
            "estado": "ok",
            "activo": vigente,
            "mensaje": ("VLM activado: verificará las detecciones dudosas."
                        if vigente else
                        "VLM desactivado: la zona gris se resuelve sin él."),
        }

    # ── UI ───────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def raiz() -> str:
        return _PAGINA

    # ── Gestión de personas de interés (lo que servía :8090) ─────────
    # Se monta entera: da de alta personas, sube fotos de referencia y
    # consulta el historial. Sin esto el Re-ID se queda sin galería.
    try:
        from vigilante_amazonas.web.dashboard import crear_app
        app.mount("/gestion", crear_app(motor_deteccion))
    except Exception:
        logger.exception("no se pudo montar la gestión de personas en /gestion")

    return app


_PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VIGILANTE · Panel</title>
<style>
:root{--fondo:#0b0e14;--sup:#131720;--elev:#1a1f2b;--borde:#232936;
 --texto:#e6e9ef;--suave:#9aa4b8;--tenue:#6b7488;
 --persona:#3d9dff;--vehiculo:#00d4aa;--acento:#3d9dff}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--texto);
 font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
header{background:var(--sup);border-bottom:1px solid var(--borde);
 padding:14px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:600}
.crece{flex:1}
.btn{background:var(--elev);color:var(--texto);border:1px solid var(--borde);
 border-radius:7px;padding:7px 13px;cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--acento);color:var(--acento)}
.btn:disabled{opacity:.5;cursor:default}
main{padding:20px;max-width:1500px;margin:0 auto}
.tarjetas{display:grid;gap:14px;margin-bottom:22px;
 grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.kpi{background:var(--sup);border:1px solid var(--borde);border-radius:11px;
 padding:16px 18px}
.kpi .n{font-size:30px;font-weight:700;line-height:1.1}
.kpi .r{color:var(--suave);font-size:12px;margin-top:4px}
.filtros{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;
 align-items:center}
.gal{display:grid;gap:12px;
 grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
.item{background:var(--sup);border:1px solid var(--borde);border-radius:10px;
 overflow:hidden;text-decoration:none;color:inherit;display:block}
.item:hover{border-color:var(--acento)}
.item img{width:100%;height:180px;object-fit:cover;display:block;
 background:var(--fondo)}
.pie{padding:8px 10px}
.pie b{font-size:12px}
.pie small{color:var(--tenue);font-size:10px;display:block;margin-top:3px}
.vacio{color:var(--tenue);text-align:center;padding:50px}
@media(prefers-color-scheme:light){
 :root{--fondo:#f6f7f9;--sup:#fff;--elev:#f0f2f5;--borde:#dde1e7;
  --texto:#1a1d23;--suave:#5a6274;--tenue:#8b93a5}}
</style></head><body>
<header>
 <h1>🛡️ VIGILANTE · Panel</h1>
 <span class="crece"></span>
 <span id="vlm-info" style="color:var(--tenue);font-size:12px"></span>
 <button id="btn-vlm" class="btn">VLM</button>
 <a class="btn" href="/gestion/" target="_blank">Personas de interés</a>
</header>
<main>
 <div class="tarjetas">
  <div class="kpi"><div class="n" id="k-per" style="color:var(--persona)">0</div>
   <div class="r">Personas</div></div>
  <div class="kpi"><div class="n" id="k-veh" style="color:var(--vehiculo)">0</div>
   <div class="r">Vehículos</div></div>
  <div class="kpi"><div class="n" id="k-tot">0</div>
   <div class="r">Total detecciones</div></div>
  <div class="kpi"><div class="n" id="k-cam">0</div>
   <div class="r">Cámaras</div></div>
 </div>
 <div class="filtros">
  <button class="btn" data-f="">Todas</button>
  <button class="btn" data-f="persona">Personas</button>
  <button class="btn" data-f="vehiculo">Vehículos</button>
  <span class="crece"></span>
  <span id="conteo" style="color:var(--tenue);font-size:12px"></span>
 </div>
 <div class="gal" id="gal"></div>
 <div class="vacio" id="vacio" hidden>Todavía no hay capturas.</div>
</main>
<script>
const $=s=>document.querySelector(s);
let filtro='';
async function cargarResumen(){
 try{
  const d=await (await fetch('/api/resumen')).json();
  $('#k-per').textContent=d.personas;
  $('#k-veh').textContent=d.vehiculos;
  $('#k-tot').textContent=d.total;
  $('#k-cam').textContent=Object.keys(d.por_camara||{}).length;
 }catch(e){}
}
async function cargarGaleria(){
 try{
  const d=await (await fetch('/api/detecciones?limite=200&gruesa='+filtro)).json();
  $('#conteo').textContent=d.total+' captura(s)';
  $('#vacio').hidden=d.detecciones.length>0;
  $('#gal').innerHTML=d.detecciones.map(r=>{
   const color=r.clase_gruesa==='vehiculo'?'var(--vehiculo)':'var(--persona)';
   return `<a class="item" href="/img/${encodeURIComponent(r.archivo)}"
     target="_blank"><img loading="lazy"
     src="/img/${encodeURIComponent(r.archivo)}" alt="">
     <div class="pie"><b style="color:${color}">${r.clase||'—'}</b>
     <small>${r.evento||''} ${r.camara?'· '+r.camara:''}</small>
     <small>${r.timestamp||''}</small></div></a>`;
  }).join('');
 }catch(e){}
}
async function cargarVlm(){
 try{
  const d=await (await fetch('/api/vlm')).json();
  const b=$('#btn-vlm');
  b.dataset.activo=d.activo?'1':'';
  b.textContent='VLM '+d.modelo+': '+(d.activo?'ON':'OFF');
  b.style.color=d.activo?'var(--vehiculo)':'var(--tenue)';
  b.style.borderColor=d.activo?'var(--vehiculo)':'var(--borde)';
  $('#vlm-info').textContent = d.presente
    ? (d.cargado?'modelo cargado':'cargando…')
    : 'no cargado (sin VRAM o deshabilitado en config)';
 }catch(e){}
}
$('#btn-vlm').addEventListener('click',async()=>{
 const b=$('#btn-vlm'); const nuevo=b.dataset.activo?'false':'true';
 b.disabled=true;
 try{ await fetch('/api/vlm?activo='+nuevo,{method:'POST'}); await cargarVlm(); }
 catch(e){} finally{ b.disabled=false; }
});
document.querySelectorAll('[data-f]').forEach(b=>{
 b.addEventListener('click',()=>{ filtro=b.dataset.f; cargarGaleria(); });
});
function todo(){ cargarResumen(); cargarGaleria(); cargarVlm(); }
todo(); setInterval(todo,5000);
</script></body></html>"""
