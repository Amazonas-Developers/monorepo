"""
estimador_edad_genero.py - Estimador de edad y genero con MiVOLO v2.

Es la RUTA PRINCIPAL del sistema, no un fallback: sobre los crops de estas
camaras el 79 % de las personas no muestra rostro utilizable (auditoria del
Hito 1 y banco del Hito 3), asi que el modo CUERPO es obligatorio.

Modelo: `iitolstykh/mivolo_v2` (mivolo_d1_384), checkpoint oficial en
`models/classifiers/mivolo_v2.safetensors`. Entrada de 6 canales formada
por dos crops de 384x384 concatenados: [rostro | cuerpo]. Cuando no hay
rostro, la mitad correspondiente va en ceros ANTES de normalizar, que es
justo lo que hace el repo oficial para el caso "sin cara".

Decisiones tomadas y por que:
  * PyTorch en vez de ONNX: la exportacion falla porque el `col2im` del
    outlook attention de VOLO no tiene soporte estable (opset 17 no lo
    trae; en 18 el exportador no resuelve su `output_size` dinamico).
    Torch ya esta en produccion para YOLO, asi que no anade dependencia.
  * FP16 en GPU: el propio config del modelo declara `torch_dtype: float16`.
  * Se reporta RANGO de edad, nunca el ano exacto: con esta calidad de
    entrada un numero puntual seria falsa precision.

Uso:
    estimador = EstimadorEdadGenero()
    muestra = estimador.estimar(track_id=7, cuerpo=crop_bgr, rostro=None)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# src/analityc/core/analytics/ -> raiz del repo
_RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", "..", ".."))
_CHECKPOINT = os.path.join(_RAIZ, "models", "classifiers",
                           "mivolo_v2.safetensors")
# ONNX (preferido): no arrastra timm ni el codigo vendorizado al runtime.
# Los pesos van en el .onnx.data hermano; onnxruntime lo resuelve solo.
_ONNX = os.path.join(_RAIZ, "models", "classifiers", "mivolo_v2.onnx")

# ── Filtro de crop: que ES una persona plausible ────────────────────────
# MiVOLO clasifica CUALQUIER imagen: a un trozo de pared le pone "Hombre
# 26-35" con toda naturalidad. Medido sobre los crops reales, un 7 % no son
# personas (franjas de marco de puerta, fragmentos), asi que hay que
# descartarlos ANTES de estimar o contaminan las estadisticas.
# Una persona de pie completa ronda un alto/ancho de 2.0-3.2.
_RATIO_MIN: float = 1.2      # mas ancho que alto -> no es alguien de pie
_RATIO_MAX: float = 4.0      # franja vertical -> marco/pared, no persona
_ANCHO_MIN: int = 55         # por debajo no hay informacion corporal util
_ALTO_MIN: int = 100


def crop_es_persona_plausible(imagen: Optional[np.ndarray]
                              ) -> Tuple[bool, str]:
    """¿El recorte puede ser una persona? Devuelve (valido, motivo).

    Filtro deliberadamente barato y geometrico: no pretende reconocer
    personas (de eso ya se encarga YOLO), solo descartar los recortes
    degenerados que el detector deja pasar y que el estimador etiquetaria
    igualmente como si fueran gente.
    """
    if imagen is None or getattr(imagen, "size", 0) == 0:
        return False, "crop vacio"
    alto, ancho = imagen.shape[:2]
    if ancho < _ANCHO_MIN:
        return False, f"ancho {ancho}px < {_ANCHO_MIN}"
    if alto < _ALTO_MIN:
        return False, f"alto {alto}px < {_ALTO_MIN}"
    ratio = alto / float(ancho)
    if ratio > _RATIO_MAX:
        return False, f"franja vertical (alto/ancho {ratio:.1f})"
    if ratio < _RATIO_MIN:
        return False, f"crop achatado (alto/ancho {ratio:.1f})"
    return True, ""

# Constantes del config.json oficial del checkpoint. NO tocar sin
# comprobarlas contra ese archivo: de ellas depende la desnormalizacion
# de la edad.
_ENTRADA: int = 384
_MEDIA: Tuple[float, float, float] = (0.485, 0.456, 0.406)
_DESV: Tuple[float, float, float] = (0.229, 0.224, 0.225)
_EDAD_MIN: float = 0.0
_EDAD_MAX: float = 122.0
_EDAD_MEDIA: float = 61.0

# Buckets de edad. Se mantienen EXACTAMENTE los que ya usa el sistema
# (AnalyticsConfig.AGE_RANGES) para no romper el contrato con el cliente,
# que muestra `age_range` tal cual.
_BUCKETS: Sequence[Tuple[int, int, str]] = (
    (0, 12, "0-12"), (13, 17, "13-17"), (18, 25, "18-25"),
    (26, 35, "26-35"), (36, 50, "36-50"), (51, 65, "51-65"),
    (66, 200, "65+"),
)


def bucket_de_edad(anios: float) -> str:
    """Convierte una edad en anos al bucket del esquema existente."""
    try:
        v = int(round(float(anios)))
    except (TypeError, ValueError):
        return "Desconocido"
    for minimo, maximo, etiqueta in _BUCKETS:
        if minimo <= v <= maximo:
            return etiqueta
    return "65+" if v > 65 else "Desconocido"


@dataclass
class MuestraDemografica:
    """Una lectura demografica de UN frame para UN track.

    No es el veredicto de la persona: eso lo decide la agregacion por
    track (Hito 5) juntando varias de estas.
    """

    track_id: int
    rango_edad: str
    genero: str                      # "Hombre" | "Mujer" | "Desconocido"
    conf_genero: float
    conf_edad: float
    solo_cuerpo: bool                # True si se estimo sin rostro
    timestamp: float = field(default_factory=time.time)
    edad_anios: float = 0.0          # valor crudo, para depurar
    motivo_descarte: str = ""        # por que no se estimo (si aplica)

    def es_valida(self) -> bool:
        """True si la muestra puede entrar en la votacion."""
        return self.genero in ("Hombre", "Mujer")

    @classmethod
    def descartada(cls, track_id: int, motivo: str) -> "MuestraDemografica":
        """Muestra vacia con el motivo, para no perder la trazabilidad.

        Un `None` mudo no distingue "no se pudo" de "no se intento"; ese
        fue precisamente el punto ciego que hizo invisible el bug original.
        """
        return cls(track_id=int(track_id), rango_edad="Desconocido",
                   genero="Desconocido", conf_genero=0.0, conf_edad=0.0,
                   solo_cuerpo=True, motivo_descarte=motivo)


def _letterbox(imagen: np.ndarray, lado: int = _ENTRADA,
               color: Tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    """Redimensiona a `lado`x`lado` CONSERVANDO el aspecto, con relleno.

    Copiado del repo oficial (`mivolo/data/misc.py: class_letterbox`). Es
    crucial: un resize directo deforma los crops de cuerpo (aqui rondan
    1:2.5) y el modelo pierde bastante puntería — medido, no supuesto.
    """
    alto, ancho = imagen.shape[:2]
    if alto == lado and ancho == lado:
        return imagen
    r = min(lado / alto, lado / ancho)
    nuevo = (int(round(ancho * r)), int(round(alto * r)))
    dw = (lado - nuevo[0]) / 2
    dh = (lado - nuevo[1]) / 2
    if (ancho, alto) != nuevo:
        imagen = cv2.resize(imagen, nuevo, interpolation=cv2.INTER_LINEAR)
    arriba, abajo = int(round(dh - 0.1)), int(round(dh + 0.1))
    izq, der = int(round(dw - 0.1)), int(round(dw + 0.1))
    return cv2.copyMakeBorder(imagen, arriba, abajo, izq, der,
                              cv2.BORDER_CONSTANT, value=color)


def _preparar(imagen: Optional[np.ndarray]) -> np.ndarray:
    """Crop BGR -> tensor CHW normalizado (float32).

    `None` produce la entrada de "ausente" tal y como la genera el repo
    oficial: ceros ANTES de normalizar (que tras normalizar NO son ceros,
    sino -media/desv). Replicarlo mal degrada el resultado en silencio.
    """
    media = np.array(_MEDIA, dtype=np.float32)
    desv = np.array(_DESV, dtype=np.float32)
    if imagen is None or getattr(imagen, "size", 0) == 0:
        vacio = np.zeros((_ENTRADA, _ENTRADA, 3), dtype=np.float32)
        return ((vacio - media) / desv).transpose(2, 0, 1)
    lb = _letterbox(imagen)
    rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return ((rgb - media) / desv).transpose(2, 0, 1)


class EstimadorEdadGenero:
    """Envoltorio de MiVOLO v2 con carga unica y inferencia por lotes.

    Es seguro compartir una instancia entre camaras: la inferencia va
    protegida por un lock (PyTorch no garantiza reentrada sobre el mismo
    modulo) y el modelo se carga una sola vez.
    """

    def __init__(self, ruta_checkpoint: str = _CHECKPOINT,
                 dispositivo: Optional[str] = None,
                 usar_fp16: bool = True,
                 preferir_onnx: bool = False) -> None:
        self._preferir_onnx = bool(preferir_onnx)
        self._ruta = ruta_checkpoint
        self._lock = threading.RLock()
        self._modelo: Any = None
        self._sesion: Any = None          # sesion onnxruntime
        self._entrada_onnx: str = ""
        self._backend: str = "ninguno"    # "onnx" | "pytorch"
        self._dispositivo: str = "cpu"
        self._fp16: bool = False
        self._cargar(dispositivo, usar_fp16)

    # ── Carga ───────────────────────────────────────────────────────────

    def _cargar(self, dispositivo: Optional[str], usar_fp16: bool) -> None:
        """Prepara el motor de inferencia.

        Se prefiere PYTORCH por velocidad: en FP16 mide ~21 ms/crop frente
        a los ~48 ms del ONNX en FP32 (medido con lote 8 en la RTX 5060 Ti).
        El ONNX queda como respaldo para maquinas sin timm o sin el codigo
        vendorizado; produce los mismos numeros (diferencia maxima 9e-05).
        Con `preferir_onnx=True` se invierte el orden.
        """
        if self._preferir_onnx:
            if self._cargar_onnx():
                return
            self._cargar_pytorch(dispositivo, usar_fp16)
            return
        self._cargar_pytorch(dispositivo, usar_fp16)
        if self._modelo is None:
            self._cargar_onnx()

    def _cargar_onnx(self) -> bool:
        """Backend preferido: onnxruntime (sin timm ni codigo vendorizado)."""
        if not os.path.isfile(_ONNX):
            return False
        try:
            import onnxruntime as ort
            from ..person_amazona_inference import _build_onnx_providers
            self._sesion = ort.InferenceSession(
                _ONNX, providers=_build_onnx_providers())
            self._entrada_onnx = self._sesion.get_inputs()[0].name
            proveedores = self._sesion.get_providers()
            self._dispositivo = ("cuda" if "CUDAExecutionProvider" in
                                 proveedores else "cpu")
            self._backend = "onnx"
            if self._dispositivo == "cpu":
                # Degradacion SILENCIOSA a CPU: onnxruntime no lanza, solo
                # va 40 veces mas lento (971 ms/crop frente a 23 ms). Suele
                # ser que no encuentra las DLLs de CUDA, o que activar
                # TensorRT sin sus librerias tumba tambien el provider CUDA.
                logger.error(
                    "MiVOLO v2 quedo en CPU (~40x mas lento). Providers: %s. "
                    "Revisa la instalacion de onnxruntime-gpu o desactiva "
                    "ENABLE_TENSORRT; se recomienda el backend PyTorch.",
                    proveedores)
            else:
                logger.info("MiVOLO v2 cargado (ONNX, %s)", proveedores[0])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo cargar el ONNX de MiVOLO (%s); "
                           "se intenta con PyTorch", exc)
            self._sesion = None
            return False

    def _cargar_pytorch(self, dispositivo: Optional[str],
                        usar_fp16: bool) -> None:
        """Respaldo: PyTorch + timm con la arquitectura vendorizada."""
        if not os.path.isfile(self._ruta):
            logger.warning(
                "MiVOLO no disponible: falta %s. La estimacion en modo "
                "cuerpo quedara inactiva.", self._ruta)
            return
        try:
            import torch
            import timm
            from safetensors.torch import load_file
            # Registra 'mivolo_d1_384' en timm al importarse.
            from . import mivolo_vendor  # noqa: F401

            if dispositivo is None:
                dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
            self._dispositivo = dispositivo
            self._fp16 = bool(usar_fp16 and dispositivo != "cpu")

            modelo = timm.create_model("mivolo_d1_384", pretrained=False,
                                       num_classes=3, in_chans=6)
            pesos = {clave.replace("mivolo.model.", ""): valor
                     for clave, valor in load_file(self._ruta).items()}
            modelo.load_state_dict(pesos, strict=True)
            modelo.eval().to(dispositivo)
            if self._fp16:
                modelo = modelo.half()
            self._modelo = modelo
            self._torch = torch
            self._backend = "pytorch"
            logger.info("MiVOLO v2 cargado (PyTorch %s, %s) desde %s",
                        dispositivo, "FP16" if self._fp16 else "FP32",
                        os.path.basename(self._ruta))
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo cargar MiVOLO v2: %s: %s",
                         type(exc).__name__, exc, exc_info=True)
            self._modelo = None

    @property
    def disponible(self) -> bool:
        """True si el modelo esta listo para estimar."""
        return self._modelo is not None or self._sesion is not None

    @property
    def backend(self) -> str:
        """Motor en uso: 'onnx', 'pytorch' o 'ninguno'."""
        return self._backend

    @property
    def dispositivo(self) -> str:
        return self._dispositivo

    # ── Inferencia ──────────────────────────────────────────────────────

    def estimar_lote(
        self,
        peticiones: Sequence[Tuple[int, np.ndarray, Optional[np.ndarray]]],
    ) -> List[MuestraDemografica]:
        """Estima un LOTE de (track_id, cuerpo, rostro_opcional).

        Procesar varias personas de un mismo frame en un solo `forward`
        es bastante mas barato que una a una.
        """
        if not self.disponible or not peticiones:
            return []

        # ── Filtro previo: descartar lo que no puede ser una persona ──
        # Si no se filtra aqui, el modelo etiqueta paredes y marcos de
        # puerta como gente (comprobado en los crops reales).
        validas: List[Tuple[int, np.ndarray, Optional[np.ndarray]]] = []
        resultado: List[MuestraDemografica] = []
        indices: List[int] = []
        for peticion in peticiones:
            track_id, cuerpo, _rostro = peticion
            ok, motivo = crop_es_persona_plausible(cuerpo)
            if ok:
                indices.append(len(resultado))
                resultado.append(None)          # hueco a rellenar
                validas.append(peticion)
            else:
                resultado.append(
                    MuestraDemografica.descartada(track_id, motivo))
        if not validas:
            return resultado

        try:
            with self._lock:
                lote = np.stack([
                    np.concatenate([_preparar(rostro), _preparar(cuerpo)])
                    for _tid, cuerpo, rostro in validas
                ]).astype(np.float32)
                if self._backend == "onnx":
                    salida = self._sesion.run(
                        None, {self._entrada_onnx: lote})[0]
                else:
                    torch = self._torch
                    tensor = torch.from_numpy(lote).to(self._dispositivo)
                    if self._fp16:
                        tensor = tensor.half()
                    with torch.no_grad():
                        salida = self._modelo(tensor).float().cpu().numpy()
        except Exception as exc:  # noqa: BLE001
            logger.error("Fallo la inferencia de MiVOLO: %s: %s",
                         type(exc).__name__, exc)
            for i, (track_id, _c, _r) in zip(indices, validas):
                resultado[i] = MuestraDemografica.descartada(
                    track_id, f"excepcion: {type(exc).__name__}")
            return resultado

        for i, (track_id, _cuerpo, rostro), fila in zip(indices, validas,
                                                        salida):
            resultado[i] = self._interpretar(track_id, fila,
                                             solo_cuerpo=rostro is None)
        return resultado

    def estimar(self, track_id: int, cuerpo: np.ndarray,
                rostro: Optional[np.ndarray] = None
                ) -> Optional[MuestraDemografica]:
        """Estima UNA persona.

        `rostro` es OPCIONAL y conviene pasarlo siempre que exista, aunque
        no supere los gates estrictos de la rama facial: MiVOLO admite
        entrada dual (cara + cuerpo) y con ella acierta mas que con el
        cuerpo solo. Devuelve None unicamente si el modelo no esta listo.
        """
        resultado = self.estimar_lote([(track_id, cuerpo, rostro)])
        return resultado[0] if resultado else None

    def _interpretar(self, track_id: int, salida: np.ndarray,
                     solo_cuerpo: bool) -> MuestraDemografica:
        """Convierte la salida cruda del modelo en una muestra.

        La cabeza da 3 valores: [logit_hombre, logit_mujer, edad_normalizada].
        La edad se desnormaliza con los mismos min/max/media del config
        oficial con los que se entreno.
        """
        logits = np.asarray(salida[:2], dtype=np.float64)
        exp = np.exp(logits - logits.max())
        probs = exp / max(exp.sum(), 1e-9)
        indice = int(np.argmax(probs))
        genero = ("Hombre", "Mujer")[indice]
        conf_genero = float(probs[indice])

        edad = float(salida[2]) * (_EDAD_MAX - _EDAD_MIN) + _EDAD_MEDIA
        edad = float(np.clip(edad, _EDAD_MIN, _EDAD_MAX))

        # Sin rostro la edad es inherentemente mas gruesa: se refleja en la
        # confianza para que la agregacion por track la pondere menos.
        conf_edad = 0.55 if solo_cuerpo else 0.80

        return MuestraDemografica(
            track_id=int(track_id),
            rango_edad=bucket_de_edad(edad),
            genero=genero,
            conf_genero=round(conf_genero, 4),
            conf_edad=conf_edad,
            solo_cuerpo=bool(solo_cuerpo),
            edad_anios=round(edad, 1),
        )


# ── Instancia compartida ────────────────────────────────────────────────

_estimador: Optional[EstimadorEdadGenero] = None
_lock_creacion = threading.Lock()


def obtener_estimador() -> EstimadorEdadGenero:
    """Devuelve el estimador compartido (se carga al primer uso)."""
    global _estimador
    if _estimador is None:
        with _lock_creacion:
            if _estimador is None:
                _estimador = EstimadorEdadGenero()
    return _estimador
