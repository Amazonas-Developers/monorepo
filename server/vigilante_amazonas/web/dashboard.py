"""
Dashboard web de VIGILANTE-AMAZONAS (FastAPI, UI 100% en español).

Funciones:
  - CRUD de personas de interés (nombre, descripción, nivel, activo).
  - Subida de fotos de rostro/vestimenta: el embedding se calcula AL SUBIR y
    la galería en memoria se recarga en caliente (sin reiniciar nada).
    Si tipo=rostro y no se detecta rostro en la foto -> 422 con mensaje claro.
  - Historial de alertas con snapshot y filtros por cámara/persona/fecha.
  - Visor MJPEG de depuración por cámara (/debug/<camara>) si el motor de
    detección propio está corriendo.

Se sirve con uvicorn en config.PUERTO_API (ver main.py).
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro
from vigilante_amazonas.web.base_datos import get_bd

logger = configurar_registro(__name__)

_ESTATICOS: Path = Path(__file__).resolve().parent / "estaticos"


class PersonaEntrada(BaseModel):
    nombre: str
    descripcion: str = ""
    nivel: str = "medio"
    activo: bool = True


class PersonaParche(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    nivel: str | None = None
    activo: bool | None = None


def crear_app(motor_deteccion: Any = None) -> FastAPI:
    """Construye la app. `motor_deteccion` (opcional) habilita /debug/<cam>."""
    app = FastAPI(title="VIGILANTE-AMAZONAS", docs_url="/docs")
    app.state.motor = motor_deteccion

    def _servicios() -> Any:
        from vigilante_amazonas.servicios import get_servicios
        return get_servicios()

    # ------------------------------------------------------------------ UI
    @app.get("/", response_class=HTMLResponse)
    async def raiz() -> str:
        return (_ESTATICOS / "index.html").read_text(encoding="utf-8")

    app.mount("/estaticos", StaticFiles(directory=str(_ESTATICOS)), name="estaticos")

    @app.get("/galeria/{persona_id}/{archivo}")
    async def foto_galeria(persona_id: int, archivo: str) -> FileResponse:
        ruta = (config.RUTA_GALERIA / str(persona_id) / archivo).resolve()
        if not str(ruta).startswith(str(config.RUTA_GALERIA.resolve())) or not ruta.exists():
            raise HTTPException(404, "foto no encontrada")
        return FileResponse(ruta)

    @app.get("/snapshots/{archivo}")
    async def snapshot(archivo: str) -> FileResponse:
        ruta = (config.RUTA_SNAPSHOTS / archivo).resolve()
        if not str(ruta).startswith(str(config.RUTA_SNAPSHOTS.resolve())) or not ruta.exists():
            raise HTTPException(404, "snapshot no encontrado")
        return FileResponse(ruta)

    # ------------------------------------------------------------- personas
    # OJO: los endpoints que tocan SQLite, los servicios de IA o la GPU se
    # declaran SIN async a propósito. FastAPI ejecuta las funciones síncronas
    # en un threadpool; si fueran `async def` con trabajo bloqueante dentro
    # (p. ej. get_servicios() cargando modelos ~60 s), congelarían el event
    # loop y con él TODO el dashboard.
    @app.get("/api/personas")
    def listar_personas() -> list[dict[str, Any]]:
        bd = get_bd()
        personas = bd.personas()
        for p in personas:
            fotos = bd.fotos(p["id"])
            p["fotos_rostro"] = sum(1 for f in fotos if f["tipo"] == "rostro")
            p["fotos_vestimenta"] = sum(1 for f in fotos if f["tipo"] == "vestimenta")
        return personas

    @app.post("/api/personas", status_code=201)
    def crear_persona(datos: PersonaEntrada) -> dict[str, Any]:
        try:
            pid = get_bd().crear_persona(datos.nombre, datos.descripcion,
                                         datos.nivel, datos.activo)
        except Exception as exc:
            raise HTTPException(422, f"no se pudo crear: {exc}")
        return {"id": pid, "mensaje": f"persona '{datos.nombre}' registrada"}

    @app.patch("/api/personas/{persona_id}")
    def editar_persona(persona_id: int, datos: PersonaParche) -> dict[str, str]:
        campos = {k: v for k, v in datos.model_dump().items() if v is not None}
        if "activo" in campos:
            campos["activo"] = int(campos["activo"])
        get_bd().actualizar_persona(persona_id, **campos)
        _recargar_galeria(_servicios())
        return {"mensaje": "persona actualizada y galería recargada"}

    @app.delete("/api/personas/{persona_id}")
    def borrar_persona(persona_id: int) -> dict[str, str]:
        bd = get_bd()
        for foto in bd.fotos(persona_id):
            _borrar_archivo(config.RUTA_GALERIA / str(persona_id) / Path(foto["ruta"]).name)
        bd.borrar_persona(persona_id)
        _recargar_galeria(_servicios())
        return {"mensaje": "persona eliminada y galería recargada"}

    # ------------------------------------------------------------------ fotos
    @app.get("/api/personas/{persona_id}/fotos")
    def fotos_de(persona_id: int) -> list[dict[str, Any]]:
        fotos = get_bd().fotos(persona_id)
        return [{"id": f["id"], "tipo": f["tipo"],
                 "url": f"/galeria/{persona_id}/{Path(f['ruta']).name}",
                 "backend": f["backend"], "creado": f["creado"]} for f in fotos]

    @app.post("/api/personas/{persona_id}/fotos/{tipo}", status_code=201)
    async def subir_foto(persona_id: int, tipo: str,
                         archivo: UploadFile = File(...)) -> dict[str, Any]:
        if tipo not in ("rostro", "vestimenta"):
            raise HTTPException(422, "tipo debe ser 'rostro' o 'vestimenta'")
        bd = get_bd()
        if bd.persona(persona_id) is None:
            raise HTTPException(404, "persona no existe")

        contenido = await archivo.read()
        arr = np.frombuffer(contenido, dtype=np.uint8)
        imagen = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if imagen is None:
            raise HTTPException(422, "el archivo no es una imagen válida")

        # El cálculo del embedding usa la GPU y puede tardar (la PRIMERA foto
        # además carga los modelos, ~1 min). Va a un hilo aparte para no
        # bloquear el event loop: si no, el dashboard entero se congelaría.
        return await run_in_threadpool(_procesar_foto, persona_id, tipo, imagen)

    def _procesar_foto(persona_id: int, tipo: str,
                       imagen: np.ndarray) -> dict[str, Any]:
        """Trabajo pesado de subir_foto (corre en threadpool)."""
        bd = get_bd()
        servicios = _servicios()
        motor = servicios.motor_reid
        if motor is None:
            raise HTTPException(503, "motor de Re-ID no disponible")

        # Embedding INMEDIATO al subir (validación incluida).
        if tipo == "rostro":
            salida = motor.rostro.embeber(imagen)
            if salida is None:
                raise HTTPException(
                    422, "no se detectó un rostro utilizable en la foto "
                         f"(mínimo {config.ROSTRO_MIN_PX}px de lado); use otra imagen")
            embedding, backend = salida[0], "arcface"
        else:
            embs = motor.vestimenta.embeber([imagen])
            if embs.shape[0] == 0:
                raise HTTPException(422, "no se pudo procesar la imagen de vestimenta")
            embedding, backend = embs[0], motor.vestimenta.backend

        carpeta = config.RUTA_GALERIA / str(persona_id)
        carpeta.mkdir(parents=True, exist_ok=True)
        nombre = f"{tipo}_{int(time.time() * 1000)}.jpg"
        cv2.imwrite(str(carpeta / nombre), imagen)
        foto_id = bd.agregar_foto(persona_id, tipo, str(carpeta / nombre),
                                  embedding, backend)
        resumen = _recargar_galeria(servicios)
        return {"id": foto_id, "url": f"/galeria/{persona_id}/{nombre}",
                "mensaje": f"foto de {tipo} registrada; galería en caliente {resumen}"}

    @app.delete("/api/fotos/{foto_id}")
    def borrar_foto(foto_id: int) -> dict[str, str]:
        ruta = get_bd().borrar_foto(foto_id)
        if ruta:
            _borrar_archivo(Path(ruta))
        _recargar_galeria(_servicios())
        return {"mensaje": "foto eliminada y galería recargada"}

    # ----------------------------------------------------------------- alertas
    @app.get("/api/alertas")
    def historial(camara: str | None = None, persona: str | None = None,
                        fecha: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
        alertas = get_bd().alertas(camara, persona, fecha, limite)
        for a in alertas:
            if a.get("snapshot"):
                a["snapshot_url"] = f"/snapshots/{Path(a['snapshot']).name}"
        return alertas

    # ------------------------------------------------------------------ estado
    @app.get("/api/estado")
    def estado(cargar_modelos: bool = False) -> dict[str, Any]:
        """Estado del sistema.

        Por defecto NO fuerza la carga de los modelos de IA: si aún no están
        cargados devuelve `servicios_cargados: false` y responde al instante
        (así el panel abre rápido). Con ?cargar_modelos=1 los inicializa.
        """
        motor = app.state.motor
        bd = get_bd()
        from vigilante_amazonas.servicios import nucleo_servicios

        cargados = nucleo_servicios._instancia is not None
        base: dict[str, Any] = {
            "servicios_cargados": cargados,
            "vestimenta_backend": config.VESTIMENTA_BACKEND,
            "camaras": bd.camaras(),
            "fps": dict(getattr(motor, "fps_por_camara", {}) or {}),
            "niveles": config.NIVELES_ALERTA,
        }
        if not cargados and not cargar_modelos:
            # Conteo de la galería desde la BD (sin tocar la GPU).
            fotos = bd.fotos(solo_personas_activas=True)
            base.update({
                "galeria": {
                    "rostros": sum(1 for f in fotos if f["tipo"] == "rostro"),
                    "vestimentas": sum(1 for f in fotos if f["tipo"] == "vestimenta"),
                },
                "seguridad_disponible": None,
                "vlm_disponible": None,
                "alertas_disponible": None,
            })
            return base

        servicios = _servicios()
        galeria = (servicios.motor_reid.galeria.tamano
                   if servicios.motor_reid is not None else {})
        base.update({
            "servicios_cargados": True,
            "galeria": galeria,
            "seguridad_disponible": bool(getattr(servicios.seguridad, "disponible", False)),
            "vlm_disponible": bool(getattr(servicios.vlm, "disponible", False)),
            "alertas_disponible": servicios.alertas is not None,
        })
        return base

    # ------------------------------------------------------------- visor MJPEG
    @app.get("/debug/{camara}")
    async def visor(camara: str) -> StreamingResponse:
        motor = app.state.motor
        if motor is None:
            raise HTTPException(503, "el motor de detección propio no está corriendo")

        async def generador():
            while True:
                motor.solicitar_visor(camara)
                jpeg = motor.ultimo_jpeg.get(camara)
                if jpeg:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           + jpeg + b"\r\n")
                await asyncio.sleep(1.0 / max(1, config.FPS_PROCESAMIENTO))

        return StreamingResponse(generador(),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    return app


def _recargar_galeria(servicios: Any) -> dict[str, int]:
    if servicios.motor_reid is not None:
        return servicios.motor_reid.galeria.recargar()
    return {}


def _borrar_archivo(ruta: Path) -> None:
    try:
        if ruta.exists():
            ruta.unlink()
    except Exception:
        logger.warning(f"no se pudo borrar el archivo {ruta}")
