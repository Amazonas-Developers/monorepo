"""
analizador_pendientes.py - Analisis a posteriori de las capturas.

Repasa las fotos que quedaron sin genero ("Analizando…") y las vuelve a
estudiar. La ventaja frente al analisis en vivo es que aqui NO hay prisa:
una captura guardada admite mucho mas computo que un frame en tiempo real,
donde cada milisegundo se resta del FPS.

Por eso este analizador es deliberadamente mas caro que el del pipeline:

  1. TTA: analiza varias variantes de la misma foto (original, espejo y
     dos reencuadres) y las hace votar. Una sola pasada puede fallar por
     el encuadre; cuatro que coinciden es una señal mucho mas firme.
  2. Entrada dual: si se detecta rostro, se lo pasa a MiVOLO junto al
     cuerpo, que es su modo mas certero.
  3. VLM opcional: para lo que siga dudoso se puede pedir una segunda
     opinion a Qwen2.5-VL. Va detras de un interruptor porque cuesta
     segundos por foto.

Actualiza el sidecar JSON del servidor, el del cliente y re-anota la foto
del cliente, para que la imagen y el texto no se contradigan.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import AnalyticsConfig

logger = logging.getLogger(__name__)

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", "..", ".."))

# Pregunta para los recortes que el filtro geometrico dio por no-personas.
# A diferencia de la de `verificador_vlm`, NO da por hecho que haya alguien:
# preguntarlo primero evita que el modelo se invente un genero sobre una
# franja de pared o el marco de una puerta.
_PREGUNTA_DESCARTE = (
    "Esta imagen es un recorte de una camara de seguridad. Puede contener "
    "una persona o ser solo un trozo de pared, una puerta, una sombra o un "
    "recorte defectuoso.\n"
    "Responde UNICAMENTE con un objeto JSON valido, sin texto adicional, "
    "con esta forma exacta:\n"
    '{"hay_persona": "si|no", '
    '"genero": "hombre|mujer|desconocido", '
    '"categoria_edad": "nino|adolescente|adulto|adulto mayor|desconocido"}\n'
    'Si no hay ninguna persona, responde "no" y pon "desconocido" en el '
    "resto. Si hay una persona pero esta de espaldas o no se distingue su "
    'cara, responde "si" y pon "desconocido" en lo que no puedas '
    "determinar. No inventes."
)


def _abs(ruta: str) -> str:
    return ruta if os.path.isabs(ruta) else os.path.join(_RAIZ, ruta)


# Preferencia del operador sobre el VLM, persistida en disco para que
# sobreviva a los reinicios del servidor. Si el archivo no existe manda
# `AnalyticsConfig.REANALISIS_USAR_VLM`, que viene activado.
_ARCHIVO_VLM = _abs(os.path.join("output", "vlm_reanalisis.txt"))


def vlm_activo() -> bool:
    """¿Debe usarse el VLM en el reanalisis?"""
    try:
        with open(_ARCHIVO_VLM, encoding="utf-8") as fichero:
            valor = fichero.read().strip().lower()
        if valor in ("1", "true", "si", "on"):
            return True
        if valor in ("0", "false", "no", "off"):
            return False
    except (OSError, ValueError):
        pass
    return bool(getattr(AnalyticsConfig, "REANALISIS_USAR_VLM", True))


def fijar_vlm(activo: bool) -> bool:
    """Guarda la preferencia. Devuelve el valor que queda vigente."""
    try:
        os.makedirs(os.path.dirname(_ARCHIVO_VLM), exist_ok=True)
        with open(_ARCHIVO_VLM, "w", encoding="utf-8") as fichero:
            fichero.write("1" if activo else "0")
    except OSError as exc:
        logger.warning("No se pudo guardar la preferencia del VLM: %s", exc)
    return vlm_activo()


class AnalizadorPendientes:
    """Reanaliza las capturas sin demografia. Corre en su propio hilo."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hilo: Optional[threading.Thread] = None
        self._cancelar = threading.Event()
        self._estado: Dict[str, Any] = {
            "ejecutando": False, "total": 0, "procesadas": 0,
            "resueltas": 0, "sin_resolver": 0, "inicio": None,
            "fin": None, "ultimo_error": "", "usa_vlm": False,
        }

    # ── Consulta ────────────────────────────────────────────────────────

    def estado(self) -> Dict[str, Any]:
        with self._lock:
            datos = dict(self._estado)
        pendientes = self.contar_pendientes()
        datos["pendientes_ahora"] = pendientes
        if datos["total"]:
            datos["progreso_pct"] = round(
                100.0 * datos["procesadas"] / datos["total"], 1)
        else:
            datos["progreso_pct"] = 0.0
        return datos

    @staticmethod
    def _sidecars() -> List[str]:
        return sorted(glob.glob(os.path.join(
            _abs(AnalyticsConfig.CAPTURE_DIR), "persons", "*.json")))

    @classmethod
    def contar_pendientes(cls) -> int:
        """Capturas que aun tiene sentido reanalizar."""
        return sum(1 for ruta in cls._sidecars() if not cls._tiene_genero(ruta))

    # ── Ejecucion ───────────────────────────────────────────────────────

    def lanzar(self, usar_vlm: Optional[bool] = None,
               limite: int = 0) -> Tuple[bool, str]:
        """Arranca el repaso en segundo plano. (aceptado, mensaje).

        `usar_vlm=None` toma el valor de la configuracion
        (`REANALISIS_USAR_VLM`, activo por defecto).
        """
        if usar_vlm is None:
            usar_vlm = vlm_activo()
        with self._lock:
            if self._estado["ejecutando"]:
                return False, "Ya hay un analisis en curso."
        pendientes = self.contar_pendientes()
        if pendientes == 0:
            return False, "No hay capturas pendientes."
        self._cancelar.clear()
        # `ejecutando` se marca AQUI, no dentro del hilo: si no, entre el
        # `lanzar()` y el arranque real hay una ventana en la que el estado
        # dice "no ejecutando, 0 de 0" y quien sondea (dashboard o cliente)
        # concluye que ya termino antes de empezar.
        total = pendientes if limite <= 0 else min(pendientes, limite)
        with self._lock:
            self._estado.update({
                "ejecutando": True, "total": total, "procesadas": 0,
                "resueltas": 0, "sin_resolver": 0, "inicio": time.time(),
                "fin": None, "ultimo_error": "", "usa_vlm": bool(usar_vlm),
            })
        self._hilo = threading.Thread(
            target=self._trabajar, args=(usar_vlm, limite),
            daemon=True, name="analizador-pendientes")
        self._hilo.start()
        return True, f"Analizando {total} captura(s) en segundo plano."

    def cancelar(self) -> None:
        self._cancelar.set()

    def _trabajar(self, usar_vlm: bool, limite: int) -> None:
        """Bucle principal del hilo. Nunca propaga excepciones."""
        try:
            from .estimador_edad_genero import obtener_estimador
            estimador = obtener_estimador()
            if not estimador.disponible:
                with self._lock:
                    self._estado["ultimo_error"] = (
                        "El estimador (MiVOLO) no esta disponible.")
                return

            pendientes = [r for r in self._sidecars()
                          if not self._tiene_genero(r)]
            if limite > 0:
                pendientes = pendientes[:limite]
            # El estado ya lo dejo preparado `lanzar()`; aqui solo se
            # ajusta el total al recuento real de esta pasada.
            with self._lock:
                self._estado["total"] = len(pendientes)

            for sidecar in pendientes:
                if self._cancelar.is_set():
                    break
                resuelta = False
                try:
                    resuelta = self._analizar_una(sidecar, estimador, usar_vlm)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Fallo al reanalizar %s: %s",
                                   os.path.basename(sidecar), exc)
                    with self._lock:
                        self._estado["ultimo_error"] = str(exc)[:200]
                with self._lock:
                    self._estado["procesadas"] += 1
                    if resuelta:
                        self._estado["resueltas"] += 1
                    else:
                        self._estado["sin_resolver"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("El analizador de pendientes fallo")
            with self._lock:
                self._estado["ultimo_error"] = str(exc)[:200]
        finally:
            with self._lock:
                self._estado["ejecutando"] = False
                self._estado["fin"] = time.time()

    @staticmethod
    def _tiene_genero(sidecar: str) -> bool:
        """La captura ya esta resuelta y no hay que volver sobre ella.

        Ademas de las que tienen genero, cuentan como resueltas aquellas
        en las que el VLM ya MIRO la imagen y dictamino que no hay ninguna
        persona: sin esto, cada pasada volveria a preguntarle por las
        mismas franjas de pared.
        """
        try:
            with open(sidecar, encoding="utf-8") as fichero:
                datos = json.load(fichero) or {}
            return bool(datos.get("gender")) or bool(datos.get("no_es_persona"))
        except (OSError, json.JSONDecodeError):
            return True        # ilegible: no tocarlo

    # ── Analisis de una captura ─────────────────────────────────────────

    def _analizar_una(self, sidecar: str, estimador: Any,
                      usar_vlm: bool) -> bool:
        """Reanaliza una captura. True si se le pudo asignar genero."""
        stem = os.path.basename(sidecar)[:-5]
        base = _abs(AnalyticsConfig.CAPTURE_DIR)
        jpg = os.path.join(base, "persons", f"{stem}.jpg")
        imagen = cv2.imread(jpg)
        if imagen is None:
            return False

        # ── Recortes que la geometria da por no-personas ──
        # El filtro es una heuristica de proporciones (ancho/alto): barata
        # y necesaria en vivo, pero se equivoca con personas de perfil, muy
        # recortadas o pegadas al borde del encuadre. Aqui, sin FPS que
        # sostener, se le pide al VLM que MIRE la imagen antes de darla por
        # perdida: decide si hay persona y, si la hay, genero y edad.
        from .estimador_edad_genero import crop_es_persona_plausible
        valido, motivo = crop_es_persona_plausible(imagen)
        if not valido:
            if usar_vlm:
                veredicto = self._consultar_vlm_descarte(imagen)
                if veredicto is not None:
                    if veredicto.get("hay_persona") and veredicto.get("genero"):
                        self._escribir(stem, {
                            "gender": veredicto["genero"],
                            "age_range": self._edad_desde_categoria(
                                veredicto.get("categoria_edad")),
                            # Sin respaldo de MiVOLO: se marca como flojo a
                            # proposito, para que se distinga en el dashboard.
                            "conf_genero": 0.60,
                            "solo_cuerpo": True,
                            "reanalisis": True,
                            "origen_demografia": "vlm_rescate",
                            "n_muestras": 0,
                            "filtro_geometrico": motivo,
                        }, quitar=["motivo_sin_demografia",
                                   "detalle_desacuerdo"])
                        return True
                    if veredicto.get("hay_persona") is False:
                        # Caso cerrado: deja de ser un pendiente eterno.
                        self._escribir(stem, {
                            "motivo_sin_demografia":
                                "el VLM confirma que no es una persona",
                            "reanalisis": True,
                            "filtro_geometrico": motivo,
                            "no_es_persona": True})
                        return False
                    # Hay alguien, pero el VLM no distingue quien (de
                    # espaldas, muy lejos...). El motivo geometrico se
                    # quedaria mintiendo sobre lo que de verdad paso.
                    self._escribir(stem, {
                        "motivo_sin_demografia":
                            "hay una persona, pero no se le distingue "
                            "la cara ni el cuerpo lo suficiente",
                        "reanalisis": True,
                        "filtro_geometrico": motivo,
                        "revisado_por_vlm": True})
                    return False
            self._escribir(stem, {"motivo_sin_demografia": f"descartado: {motivo}",
                                  "reanalisis": True})
            return False

        # ── Rostro (si lo hay) para la entrada dual ──
        rostro = None
        rostro_jpg = os.path.join(base, "faces", f"{stem}.jpg")
        if os.path.isfile(rostro_jpg):
            rostro = cv2.imread(rostro_jpg)

        # ── TTA: varias vistas de la misma foto, y votan ──
        muestras = []
        for variante, cara in self._variantes(imagen, rostro):
            m = estimador.estimar(0, variante, cara)
            if m is not None and m.es_valida():
                muestras.append(m)
        if not muestras:
            # MiVOLO no saco nada. Antes se descartaba aqui mismo; ahora,
            # si el VLM esta activo, se le deja intentarlo: es justo el
            # caso en el que aporta, porque "mira" la escena de otra forma.
            if usar_vlm:
                solo_vlm = self._consultar_vlm(imagen)
                if solo_vlm is not None and solo_vlm.get("genero"):
                    self._escribir(stem, {
                        "gender": solo_vlm["genero"],
                        "age_range": self._edad_desde_categoria(
                            solo_vlm.get("categoria_edad")),
                        "conf_genero": 0.60,   # sin respaldo de MiVOLO
                        "solo_cuerpo": rostro is None,
                        "reanalisis": True,
                        "origen_demografia": "vlm",
                        "n_muestras": 0,
                    }, quitar=["motivo_sin_demografia"])
                    return True
            self._escribir(stem, {
                "motivo_sin_demografia": "el modelo no pudo decidir",
                "reanalisis": True})
            return False

        genero, confianza, edad = self._votar(muestras)

        # ── Segunda opinion del VLM si sigue flojo ──
        origen = "mivolo_tta"
        if usar_vlm and confianza < 0.70:
            resultado_vlm = self._consultar_vlm(imagen)
            if resultado_vlm is not None and resultado_vlm.get("genero"):
                if resultado_vlm["genero"] == genero:
                    confianza = min(0.95, confianza + 0.15)   # coinciden
                    origen = "mivolo_tta+vlm"
                else:
                    # Discrepan: se deja constancia y NO se publica un dato
                    # en el que los dos modelos no se ponen de acuerdo.
                    self._escribir(stem, {
                        "motivo_sin_demografia": "modelos en desacuerdo",
                        "reanalisis": True,
                        "detalle_desacuerdo":
                            f"mivolo={genero} vlm={resultado_vlm['genero']}"})
                    return False

        if confianza < 0.55:
            self._escribir(stem, {
                "motivo_sin_demografia": f"confianza baja ({confianza:.2f})",
                "reanalisis": True})
            return False

        self._escribir(stem, {
            "gender": genero,
            "age_range": edad,
            "conf_genero": round(confianza, 3),
            "solo_cuerpo": rostro is None,
            "reanalisis": True,
            "origen_demografia": origen,
            "n_muestras": len(muestras),
        }, quitar=["motivo_sin_demografia", "detalle_desacuerdo"])
        return True

    @staticmethod
    def _edad_desde_categoria(categoria: Optional[str]) -> str:
        """Traduce la categoria gruesa del VLM al bucket del esquema.

        Al VLM se le piden categorias amplias a proposito (a esta calidad
        de imagen, pedirle un año exacto seria inventar precision), asi
        que se mapea al bucket mas representativo de cada tramo.
        """
        return {
            "nino": "0-12",
            "adolescente": "13-17",
            "adulto": "26-35",
            "adulto mayor": "65+",
        }.get(str(categoria or "").strip().lower(), "Desconocido")

    @staticmethod
    def _variantes(imagen: np.ndarray,
                   rostro: Optional[np.ndarray]) -> List[Tuple[np.ndarray, Any]]:
        """Vistas de la misma captura para el TTA.

        Espejo y reencuadres suaves: cambian el encuadre sin inventar
        informacion, que es justo lo que hace fallar a una pasada unica.
        """
        alto, ancho = imagen.shape[:2]
        recorte = imagen[int(alto * 0.03):int(alto * 0.97),
                         int(ancho * 0.04):int(ancho * 0.96)]
        vistas = [imagen, cv2.flip(imagen, 1)]
        if recorte.size:
            vistas.append(recorte)
            vistas.append(cv2.flip(recorte, 1))
        caras = [rostro, (cv2.flip(rostro, 1) if rostro is not None else None)]
        return [(v, caras[i % 2]) for i, v in enumerate(vistas)]

    @staticmethod
    def _votar(muestras: List[Any]) -> Tuple[str, float, str]:
        """Voto ponderado por confianza; edad por bucket modal."""
        pesos: Dict[str, float] = {}
        for m in muestras:
            pesos[m.genero] = pesos.get(m.genero, 0.0) + m.conf_genero
        genero = max(pesos, key=pesos.get)
        total = sum(pesos.values()) or 1.0
        # Confianza = cuota del ganador, moderada por el acuerdo entre vistas
        cuota = pesos[genero] / total
        coincidencias = sum(1 for m in muestras if m.genero == genero)
        acuerdo = coincidencias / len(muestras)
        confianza = float(cuota * (0.6 + 0.4 * acuerdo))

        edades = Counter(m.rango_edad for m in muestras
                         if m.genero == genero)
        edad = edades.most_common(1)[0][0] if edades else "Desconocido"
        return genero, confianza, edad

    def _consultar_vlm_descarte(
            self, imagen: np.ndarray) -> Optional[Dict[str, Any]]:
        """Pregunta al VLM si el recorte contiene una persona, y quien es.

        Se usa solo con los recortes que el filtro geometrico rechazo. La
        pregunta de `verificador_vlm` no sirve aqui porque da por hecho
        que hay alguien ("Observa a la persona..."), y eso induce al
        modelo a inventarse un genero sobre una franja de pared.

        Devuelve None si el modelo no esta disponible o su respuesta no se
        puede interpretar; en ese caso el llamador conserva el motivo
        geometrico original.
        """
        try:
            import json as _json
            import re as _re

            from ..multimodal_router import get_multimodal_router
            from .verificador_vlm import _CATEGORIAS, _GENEROS

            respuesta = get_multimodal_router().vqa(
                imagen, _PREGUNTA_DESCARTE, max_new_tokens=90)
            if not respuesta:
                return None
            trozo = _re.search(r"\{.*?\}", respuesta, _re.DOTALL)
            if not trozo:
                return None
            datos = _json.loads(trozo.group(0))
            if not isinstance(datos, dict):
                return None

            crudo = str(datos.get("hay_persona", "")).strip().lower()
            if crudo in ("si", "sí", "true", "yes"):
                hay = True
            elif crudo in ("no", "false"):
                hay = False
            else:
                return None

            categoria = str(datos.get("categoria_edad", "")).strip().lower()
            return {
                "hay_persona": hay,
                "genero": _GENEROS.get(
                    str(datos.get("genero", "")).strip().lower()),
                "categoria_edad": categoria if categoria in _CATEGORIAS
                else None,
            }
        except (ValueError, TypeError) as exc:
            logger.warning("Respuesta ilegible del VLM en un descarte: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("El VLM no pudo revisar un descarte: %s", exc)
            return None

    def _consultar_vlm(self, imagen: np.ndarray) -> Optional[Dict[str, Any]]:
        """Segunda opinion del VLM. None si no esta disponible o falla."""
        try:
            from .verificador_vlm import _PREGUNTA, _parsear
            from ..multimodal_router import get_multimodal_router
            respuesta = get_multimodal_router().vqa(
                imagen, _PREGUNTA, max_new_tokens=80)
            resultado = _parsear(respuesta, 0)
            if resultado is None:
                return None
            return {"genero": resultado.genero,
                    "categoria_edad": resultado.categoria_edad}
        except Exception as exc:  # noqa: BLE001
            logger.warning("El VLM no pudo opinar: %s", exc)
            return None

    # ── Escritura ───────────────────────────────────────────────────────

    def _escribir(self, stem: str, campos: Dict[str, Any],
                  quitar: Optional[List[str]] = None) -> None:
        """Actualiza el sidecar del servidor, el del cliente y la foto.

        Se re-anota la imagen del cliente para que el banner grabado en el
        pixel no siga diciendo "Analizando…" cuando el texto ya dice otra
        cosa.
        """
        base = _abs(AnalyticsConfig.CAPTURE_DIR)
        servidor = os.path.join(base, "persons", f"{stem}.json")
        meta: Dict[str, Any] = {}
        if os.path.isfile(servidor):
            try:
                with open(servidor, encoding="utf-8") as fichero:
                    meta = json.load(fichero) or {}
            except (OSError, json.JSONDecodeError):
                meta = {}
        meta.update(campos)
        for clave in (quitar or []):
            meta.pop(clave, None)
        meta["demo_final"] = True

        try:
            with open(servidor, "w", encoding="utf-8") as fichero:
                json.dump(meta, fichero, ensure_ascii=False)
        except OSError as exc:
            logger.debug("No se pudo escribir %s: %s", servidor, exc)

        # ── Copia del cliente: JSON + foto re-anotada ──
        carpeta = getattr(AnalyticsConfig, "CAPTURE_CLIENT_DIR", "") or ""
        if not carpeta:
            return
        try:
            os.makedirs(carpeta, exist_ok=True)
            with open(os.path.join(carpeta, f"{stem}.json"), "w",
                      encoding="utf-8") as fichero:
                json.dump({**meta, "file": f"{stem}.jpg"}, fichero,
                          ensure_ascii=False)
            self._reanotar_foto(stem, meta, base, carpeta)
        except Exception as exc:  # noqa: BLE001
            logger.debug("No se pudo actualizar la copia del cliente: %s", exc)

    @staticmethod
    def _reanotar_foto(stem: str, meta: Dict[str, Any],
                       base: str, carpeta: str) -> None:
        """Vuelve a dibujar el banner de la foto del cliente."""
        cuerpo = cv2.imread(os.path.join(base, "persons", f"{stem}.jpg"))
        if cuerpo is None:
            return
        rostro_jpg = os.path.join(base, "faces", f"{stem}.jpg")
        rostro = cv2.imread(rostro_jpg) if os.path.isfile(rostro_jpg) else None

        # Se reutiliza el dibujado del pipeline para que las fotos
        # reanalizadas sean indistinguibles de las demas.
        import types
        from ..person_amazona_inference import PersonAmazonas
        auxiliar = types.SimpleNamespace(
            _capture_ts_legible=PersonAmazonas._capture_ts_legible,
            _compose_face_zoom_panel=PersonAmazonas._compose_face_zoom_panel)
        anotar = types.MethodType(PersonAmazonas._annotate_capture_image,
                                  auxiliar)
        imagen = anotar(cuerpo, meta, face_zoom=rostro)
        destino = os.path.join(carpeta, f"{stem}.jpg")
        temporal = os.path.join(carpeta, f"{stem}.tmp.jpg")
        if cv2.imwrite(temporal, imagen, [cv2.IMWRITE_JPEG_QUALITY, 88]):
            os.replace(temporal, destino)


# ── Instancia compartida ────────────────────────────────────────────────

_analizador: Optional[AnalizadorPendientes] = None
_lock_creacion = threading.Lock()


def obtener_analizador() -> AnalizadorPendientes:
    """Analizador compartido por todo el proceso."""
    global _analizador
    if _analizador is None:
        with _lock_creacion:
            if _analizador is None:
                _analizador = AnalizadorPendientes()
    return _analizador
