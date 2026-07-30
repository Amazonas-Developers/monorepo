"""
Verificador VLM (Hito 6): Qwen2.5-VL en DEVICE_VLM, SIEMPRE no bloqueante.

- Hilo dedicado con cola acotada (VLM_COLA_MAX): el pipeline de detección
  nunca espera al VLM. Consultas más viejas que VLM_TIMEOUT_SEG se descartan.
- ModelManager consciente de VRAM: en modo "auto" el modelo solo se carga si
  hay GPU dedicada (cuda:2 real) o VRAM libre >= VLM_VRAM_MINIMA_GB.
- El modelo se carga DENTRO del hilo (el arranque del sistema no se bloquea);
  mientras carga, las consultas esperan en la cola.
- Entrada: crop actual + fotos de referencia de la galería (desde SQLite).
  Salida: JSON {"misma_persona": bool, "confianza": 0-1, "razon": "..."} que
  se entrega al callback del MotorReID.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

# Prompt validado en el Hito 6: pedir descripción por pasos antes del
# veredicto elimina el sesgo del 3B (con prompt directo respondía siempre lo
# mismo); la regla escéptica evita confirmar sin rostro visible.
_PROMPT_TAREA: str = (
    "Tarea de verificación de identidad. Paso 1: describe a la persona de la "
    "imagen A (¿se le ve el rostro?, ropa, pelo). Paso 2: describe a la(s) "
    "persona(s) de la(s) imagen(es) de referencia. Paso 3: compara. "
    "REGLAS: si el rostro NO es claramente visible en alguna imagen, o hay "
    "cualquier duda razonable, responde false. Sé escéptico: un parecido "
    "superficial NO basta. {criterio} Termina con una línea JSON: "
    '{{"misma_persona": true|false, "confianza": 0.0-1.0, "razon": "..."}}')


@dataclass
class ConsultaVLM:
    crop: np.ndarray
    persona_id: int
    tipo: str                      # "rostro" | "vestimenta"
    callback: Callable[[dict[str, Any]], None]
    creada: float = field(default_factory=time.time)


def _vram_libre_gb(dispositivo: str) -> float:
    try:
        import torch
        libre, _ = torch.cuda.mem_get_info(config.indice_de(dispositivo))
        return libre / 1024**3
    except Exception:
        return 0.0


def _ruta_preferencia() -> str:
    """Archivo donde se recuerda si el VLM esta encendido."""
    import os
    return os.path.join(str(config.RUTA_BASE), "vlm_activo.txt")


def vlm_activo() -> bool:
    """Preferencia guardada. Encendido mientras no se diga lo contrario."""
    try:
        with open(_ruta_preferencia(), encoding="utf-8") as fichero:
            return fichero.read().strip() not in ("0", "no", "false")
    except OSError:
        return True


def fijar_vlm(activo: bool) -> bool:
    """Enciende o apaga el VLM y deja la preferencia escrita.

    Actua sobre el verificador YA cargado (no lo descarga ni lo recarga):
    apagarlo solo hace que deje de aceptar consultas, asi que volver a
    encenderlo es inmediato y no cuesta otra carga de 3 GB.
    """
    import os
    activo = bool(activo)
    try:
        os.makedirs(os.path.dirname(_ruta_preferencia()), exist_ok=True)
        with open(_ruta_preferencia(), "w", encoding="utf-8") as fichero:
            fichero.write("1" if activo else "0")
    except OSError as exc:
        logger.warning(f"no se pudo guardar la preferencia del VLM: {exc}")
    vivo = obtener_vlm()
    if vivo is not None:
        vivo.activo = activo
    logger.info("VLM %s por peticion del cliente",
                "ACTIVADO" if activo else "DESACTIVADO")
    return activo


_vlm_vivo: "VerificadorVLM | None" = None


def obtener_vlm() -> "VerificadorVLM | None":
    """El verificador en marcha, si lo hay. Lo usa el endpoint del panel."""
    return _vlm_vivo


def crear_vlm() -> "VerificadorVLM | None":
    """Fábrica usada por el núcleo de servicios; None si está deshabilitado."""
    modo = config.VLM_HABILITADO.lower()
    if modo == "no":
        logger.info("VLM deshabilitado por configuración (VLM_HABILITADO='no')")
        return None
    dispositivo = config.resolver_dispositivo(config.DEVICE_VLM)
    if modo == "auto":
        import torch
        gpu_dedicada = (torch.cuda.device_count() >
                        config.indice_de(config.DEVICE_DETECCION) + 1)
        libre = _vram_libre_gb(dispositivo)
        if not gpu_dedicada and libre < config.VLM_VRAM_MINIMA_GB:
            logger.warning(
                f"VLM en 'auto' NO se carga: sin GPU dedicada y solo "
                f"{libre:.1f} GB libres (< {config.VLM_VRAM_MINIMA_GB}); "
                f"la zona gris se maneja con ZONA_GRIS_SIN_VLM_EMITIR="
                f"{config.ZONA_GRIS_SIN_VLM_EMITIR}")
            return None
    vlm = VerificadorVLM(dispositivo)
    vlm.activo = vlm_activo()          # respeta lo que dejo dicho el usuario
    vlm.start()
    global _vlm_vivo
    _vlm_vivo = vlm
    return vlm


class VerificadorVLM(threading.Thread):
    """Hilo consumidor de la cola de verificaciones."""

    def __init__(self, dispositivo: str) -> None:
        super().__init__(name="verificador-vlm", daemon=True)
        self.dispositivo: str = dispositivo
        self.disponible: bool = True      # acepta consultas (aunque aún cargue)
        # Interruptor del usuario, independiente de `disponible`: el modelo
        # sigue cargado, simplemente se dejan de aceptar consultas. Asi
        # volver a encenderlo no cuesta otra carga de varios GB.
        self.activo: bool = True
        self.cargado: bool = False
        self._cola: "queue.Queue[ConsultaVLM | None]" = queue.Queue(
            maxsize=config.VLM_COLA_MAX)
        self._modelo = None
        self._procesador = None
        self._listo_en: float = 0.0
        self.consultas_atendidas: int = 0
        self.consultas_descartadas: int = 0

    # ------------------------------------------------------------------ API
    def encolar(self, crop: np.ndarray, persona_id: int, tipo: str,
                callback: Callable[[dict[str, Any]], None]) -> bool:
        """Encola una verificación; NUNCA bloquea al llamador."""
        if not self.activo:
            # Apagado por el usuario. Se contesta igualmente para que el
            # llamador resuelva por su cuenta en vez de quedarse colgado.
            callback({"misma_persona": False, "confianza": 0.0,
                      "razon": "el VLM está desactivado"})
            return False
        try:
            self._cola.put_nowait(ConsultaVLM(crop=crop.copy(),
                                              persona_id=persona_id,
                                              tipo=tipo, callback=callback))
            return True
        except queue.Full:
            self.consultas_descartadas += 1
            logger.warning("cola del VLM llena: consulta descartada",
                           extra={"datos": {"persona_id": persona_id}})
            callback({"misma_persona": False, "confianza": 0.0,
                      "razon": "cola del VLM llena; verificación descartada"})
            return False

    def detener(self) -> None:
        self.disponible = False
        try:
            self._cola.put_nowait(None)
        except queue.Full:
            pass

    # ------------------------------------------------------------------ hilo
    def run(self) -> None:
        try:
            self._cargar_modelo()
        except Exception as exc:
            self.disponible = False
            logger.error(f"VLM no pudo cargar y queda deshabilitado: {exc}")
            self._vaciar_cola("el VLM no pudo cargar")
            return
        while True:
            consulta = self._cola.get()
            if consulta is None:
                break
            # La antigüedad se mide desde que el modelo quedó LISTO: las
            # consultas que esperaron la carga inicial no se descartan.
            if time.time() - max(consulta.creada, self._listo_en) > config.VLM_TIMEOUT_SEG:
                self.consultas_descartadas += 1
                consulta.callback({"misma_persona": False, "confianza": 0.0,
                                   "razon": "consulta vencida en cola"})
                continue
            try:
                veredicto = self._verificar(consulta)
            except Exception as exc:
                logger.exception("fallo verificando en el VLM")
                veredicto = {"misma_persona": False, "confianza": 0.0,
                             "razon": f"error del VLM: {exc}"}
            self.consultas_atendidas += 1
            try:
                consulta.callback(veredicto)
            except Exception:
                logger.exception("callback de verificación falló")

    # ------------------------------------------------------------------ carga
    def _cargar_modelo(self) -> None:
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        t0 = time.time()
        logger.info(f"cargando VLM {config.VLM_MODELO_ID} en {self.dispositivo}…")
        # low_cpu_mem_usage + device_map: los shards van DIRECTO a la GPU sin
        # materializar el modelo entero en RAM (evita el error 1455 de Windows
        # "archivo de paginación demasiado pequeño").
        self._modelo = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            config.VLM_MODELO_ID, dtype=torch.bfloat16,
            low_cpu_mem_usage=True, device_map={"": self.dispositivo},
            local_files_only=True).eval()
        # min/max_pixels acotan el costo visual (crops chicos, no fotos 4K).
        self._procesador = AutoProcessor.from_pretrained(
            config.VLM_MODELO_ID, local_files_only=True,
            min_pixels=64 * 28 * 28, max_pixels=768 * 28 * 28)
        self.cargado = True
        self._listo_en = time.time()
        logger.info(
            f"VLM listo en {time.time() - t0:.0f}s "
            f"(VRAM libre: {_vram_libre_gb(self.dispositivo):.1f} GB)",
            extra={"datos": {"modelo": config.VLM_MODELO_ID,
                             "dispositivo": self.dispositivo}})

    # -------------------------------------------------------------- inferencia
    def _verificar(self, consulta: ConsultaVLM) -> dict[str, Any]:
        from PIL import Image

        referencias = self._fotos_referencia(consulta.persona_id, consulta.tipo)
        if not referencias:
            return {"misma_persona": False, "confianza": 0.0,
                    "razon": "sin fotos de referencia en la galería"}

        contenido: list[dict[str, Any]] = [
            {"type": "text", "text": "Imagen A (captura de cámara):"},
            {"type": "image", "image": Image.fromarray(
                cv2.cvtColor(consulta.crop, cv2.COLOR_BGR2RGB))}]
        for i, ref in enumerate(referencias, start=1):
            contenido.append({"type": "text",
                              "text": f"Imagen de referencia {i}:"})
            contenido.append({"type": "image", "image": Image.fromarray(
                cv2.cvtColor(ref, cv2.COLOR_BGR2RGB))})
        criterio = ("Céntrate en el ROSTRO." if consulta.tipo == "rostro"
                    else "Céntrate en la VESTIMENTA (ropa, colores, complexión); "
                         "aquí el rostro visible no es obligatorio si la "
                         "vestimenta coincide con exactitud.")
        contenido.append({"type": "text",
                          "text": _PROMPT_TAREA.format(criterio=criterio)})
        mensajes = [{"role": "user", "content": contenido}]

        texto = self._procesador.apply_chat_template(
            mensajes, tokenize=False, add_generation_prompt=True)
        imagenes = [c["image"] for c in contenido if c["type"] == "image"]
        entradas = self._procesador(text=[texto], images=imagenes,
                                    return_tensors="pt").to(self.dispositivo)
        import torch
        with torch.no_grad():
            salida = self._modelo.generate(**entradas,
                                           max_new_tokens=config.VLM_MAX_TOKENS,
                                           do_sample=False)
        generado = salida[0][entradas["input_ids"].shape[1]:]
        respuesta = self._procesador.decode(generado, skip_special_tokens=True)
        return self._parsear(respuesta)

    def _fotos_referencia(self, persona_id: int, tipo: str) -> list[np.ndarray]:
        """Lee del disco hasta VLM_MAX_REFERENCIAS fotos de la persona."""
        from vigilante_amazonas.web.base_datos import get_bd
        fotos = [f for f in get_bd().fotos(persona_id) if f["tipo"] == tipo]
        if not fotos:   # sin fotos del tipo pedido: usar cualquiera
            fotos = get_bd().fotos(persona_id)
        imagenes: list[np.ndarray] = []
        for f in fotos[:config.VLM_MAX_REFERENCIAS]:
            img = cv2.imread(str(Path(f["ruta"])))
            if img is not None:
                imagenes.append(img)
        return imagenes

    @staticmethod
    def _parsear(respuesta: str) -> dict[str, Any]:
        """Extrae el JSON de la respuesta del modelo (tolerante a ruido)."""
        encontrado = re.search(r"\{.*\}", respuesta, re.DOTALL)
        if encontrado:
            try:
                doc = json.loads(encontrado.group(0))
                return {"misma_persona": bool(doc.get("misma_persona", False)),
                        "confianza": max(0.0, min(1.0, float(doc.get("confianza", 0.0)))),
                        "razon": str(doc.get("razon", ""))}
            except Exception:
                pass
        # Heurística de respaldo si el modelo no devolvió JSON parseable.
        positiva = bool(re.search(r"misma[_ ]persona.{0,12}(true|sí|si)",
                                  respuesta, re.IGNORECASE))
        return {"misma_persona": positiva, "confianza": 0.5 if positiva else 0.0,
                "razon": f"respuesta no estructurada: {respuesta[:120]}"}

    def _vaciar_cola(self, razon: str) -> None:
        while True:
            try:
                consulta = self._cola.get_nowait()
            except queue.Empty:
                return
            if consulta is not None:
                consulta.callback({"misma_persona": False, "confianza": 0.0,
                                   "razon": razon})
