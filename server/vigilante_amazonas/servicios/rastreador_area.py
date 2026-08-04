"""
Rastreador de PERMANENCIA en el área (contador de tiempo + alertas de
entrada/salida) para las 7 clases de VIGILANTE-AMAZONAS.

Para cada cámara mantiene el estado de los objetos (tracks de ByteTrack) que
están dentro del "área":
  - Área = ROI que el cliente dibuja y activa; si no hay ROI activo, el área
    es TODO el campo visual de la cámara.
  - Al confirmarse un track nuevo dentro del área (tras AREA_MIN_FRAMES_LLEGADA
    frames) => tarjeta de LLEGADA para el AlertsSidebar.
  - Mientras está dentro => se calcula su permanencia en vivo (contador que se
    dibuja sobre su caja).
  - Cuando no se vuelve a ver en AREA_TTL_SALIDA_SEG => tarjeta de SALIDA con
    la permanencia total.

El punto de prueba de "dentro del área" es la BASE del bbox (centro-inferior:
los pies de la persona / el apoyo del vehículo), estándar en videovigilancia.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.deteccion.rastreador import DeteccionVig
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


@dataclass
class _EstadoObjeto:
    """Estado de un objeto (track) dentro del área de una cámara."""
    track_id: int
    clase: str
    primera_vez: float
    ultima_vez: float
    frames_vistos: int = 1
    alertado_llegada: bool = False
    alertado_permanencia: bool = False
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    crop_b64: str = ""          # última foto buena (se reutiliza en la salida)

    def permanencia(self, ahora: float | None = None) -> float:
        return (ahora if ahora is not None else time.time()) - self.primera_vez


def _formatear_permanencia(segundos: float) -> str:
    """Segundos -> '12s' | '1m03s' | '1h02m' para el cronómetro de la caja."""
    s = int(max(0.0, segundos))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class RastreadorArea:
    """Permanencia + alertas de entrada/salida por área, por cámara.

    Thread-safe hacia afuera bajo el lock de VigilanteWS (el llamador
    serializa; esta clase no toma locks propios).
    """

    def __init__(self, detector_merodeo: Any = None,
                 umbral_permanencia_seg: float | None = None) -> None:
        # camara -> {track_id: _EstadoObjeto}
        self._por_camara: dict[str, dict[int, _EstadoObjeto]] = {}
        # Colaborador opcional (DetectorMerodeo): en cada LLEGADA confirmada
        # registra la visita por apariencia y puede devolver tarjeta "merodeo".
        self._merodeo = detector_merodeo
        # Umbral de la alerta de permanencia PROPIO de esta instancia (el modo
        # Estacionamiento usa el suyo); None = el global de config.
        self._umbral_permanencia = umbral_permanencia_seg

    def _umbral_permanencia_seg(self) -> float:
        return (self._umbral_permanencia if self._umbral_permanencia is not None
                else config.AREA_ALERTA_PERMANENCIA_SEG)

    # ------------------------------------------------------------------ API
    def actualizar(self, camara: str, frame: np.ndarray,
                   dets: list[DeteccionVig],
                   roi_poligono: np.ndarray | None
                   ) -> tuple[dict[int, str], list[dict[str, Any]]]:
        """Procesa las detecciones de UN frame.

        Devuelve:
          contadores: {track_id: "12s"} permanencia en vivo para el dibujo.
          tarjetas:   alertas de LLEGADA/SALIDA para metadata['alerts'].
        """
        if not config.AREA_PERMANENCIA_HABILITADA:
            return {}, []

        ahora = time.time()
        estados = self._por_camara.setdefault(camara, {})
        tarjetas: list[dict[str, Any]] = []
        contadores: dict[int, str] = {}
        vistos_ahora: set[int] = set()
        en_area: list[DeteccionVig] = []
        # Foto del frame COMPLETO anotada (elipses + etiquetas); se genera una
        # sola vez por frame y solo si alguna tarjeta la necesita.
        foto_cache: list[str] = []

        def _foto() -> str:
            if not foto_cache:
                foto_cache.append(self._foto_area(frame, en_area, contadores))
            return foto_cache[0]

        for d in dets:
            if d.clase not in config.AREA_CLASES_VIGILADAS:
                continue
            if not self._dentro_del_area(d.bbox, roi_poligono):
                continue
            vistos_ahora.add(d.track_id)
            en_area.append(d)
            estado = estados.get(d.track_id)
            if estado is None:
                # RE-VINCULACIÓN: ¿este track "nuevo" es en realidad un objeto
                # ya conocido que perdió su ID (parpadeo de detección)? Si su
                # caja solapa con un estado que dejó de verse hace poco y es
                # de la misma familia, hereda identidad, contador y alertas.
                estado = self._revincular(estados, d, ahora)
            if estado is None:
                estado = _EstadoObjeto(
                    track_id=d.track_id, clase=d.clase,
                    primera_vez=ahora, ultima_vez=ahora, bbox=d.bbox)
                estados[d.track_id] = estado
            else:
                estado.clase = d.clase           # persona -> personal_seguridad
                estado.ultima_vez = ahora
                estado.frames_vistos += 1
                estado.bbox = d.bbox

            if config.AREA_CONTADOR_EN_CAJA:
                contadores[d.track_id] = _formatear_permanencia(
                    estado.permanencia(ahora))

        # 2a pasada: las tarjetas se arman DESPUÉS de conocer todos los
        # objetos del área (la foto debe marcar a TODOS, no solo al primero).
        for d in en_area:
            estado = estados[d.track_id]

            # Confirmar LLEGADA tras varios frames (filtra tracks fugaces).
            if (not estado.alertado_llegada
                    and estado.frames_vistos >= config.AREA_MIN_FRAMES_LLEGADA):
                estado.alertado_llegada = True
                estado.crop_b64 = _foto()
                if config.AREA_ALERTA_LLEGADA:
                    tarjetas.append(self._tarjeta(camara, estado, "llegada", ahora))
                # MERODEO: registrar la visita por apariencia; si este mismo
                # aspecto acumuló varias entradas, sale la tarjeta extra.
                if self._merodeo is not None:
                    x1, y1, x2, y2 = d.bbox
                    alto_f, ancho_f = frame.shape[:2]
                    recorte = frame[max(0, y1):min(alto_f, y2),
                                    max(0, x1):min(ancho_f, x2)]
                    t_merodeo = self._merodeo.registrar_llegada(
                        camara, estado.clase, recorte, d.track_id)
                    if t_merodeo:
                        tarjetas.append(t_merodeo)

            # Alerta de PERMANENCIA al acumular el umbral (una vez por objeto).
            if (estado.alertado_llegada and not estado.alertado_permanencia
                    and self._umbral_permanencia_seg() > 0
                    and estado.permanencia(ahora) >= self._umbral_permanencia_seg()):
                estado.alertado_permanencia = True
                estado.crop_b64 = _foto()        # foto actual (situación vigente)
                tarjetas.append(self._tarjeta(camara, estado, "permanencia", ahora))

        tarjetas.extend(self._purgar(camara, ahora, vistos_ahora))
        return contadores, tarjetas

    def olvidar_camara(self, camara: str) -> None:
        self._por_camara.pop(camara, None)

    def ocupacion(self, camara: str, clase_gruesa: str | None = None) -> int:
        """Cuántos objetos están AHORA dentro del área de esa cámara.

        `clase_gruesa` ('vehiculo' | 'persona') filtra por familia — es la
        ocupación que el modo Estacionamiento publica en el metadata."""
        ahora = time.time()
        n = 0
        for est in self._por_camara.get(camara, {}).values():
            if ahora - est.ultima_vez > config.AREA_TTL_SALIDA_SEG:
                continue
            if (clase_gruesa and config.AREA_CLASE_GRUESA.get(
                    est.clase, "persona") != clase_gruesa):
                continue
            n += 1
        return n

    # ------------------------------------------------------------------ interno
    def _purgar(self, camara: str, ahora: float,
                vistos_ahora: set[int]) -> list[dict[str, Any]]:
        """Objetos no vistos en AREA_TTL_SALIDA_SEG -> tarjeta de SALIDA."""
        estados = self._por_camara.get(camara, {})
        salidos: list[dict[str, Any]] = []
        for tid in list(estados.keys()):
            if tid in vistos_ahora:
                continue
            estado = estados[tid]
            if ahora - estado.ultima_vez < config.AREA_TTL_SALIDA_SEG:
                continue
            del estados[tid]
            permanencia = estado.ultima_vez - estado.primera_vez
            if (estado.alertado_llegada and config.AREA_ALERTA_SALIDA
                    and permanencia >= config.AREA_MIN_PERMANENCIA_SALIDA_SEG):
                salidos.append(self._tarjeta(camara, estado, "salida", ahora))
        return salidos

    def _revincular(self, estados: dict[int, _EstadoObjeto], d: DeteccionVig,
                    ahora: float) -> _EstadoObjeto | None:
        """Busca un estado 'huérfano' (su track dejó de verse hace poco) que
        solape con la detección nueva y sea de la misma familia; si existe,
        lo re-indexa bajo el nuevo track_id y lo devuelve (identidad,
        contador y banderas de alerta se conservan)."""
        familia = config.AREA_CLASE_GRUESA.get(d.clase, "persona")
        mejor: _EstadoObjeto | None = None
        mejor_iou = config.AREA_REVINCULACION_IOU
        for est in estados.values():
            # Solo estados que NO se están viendo ahora mismo (huérfanos).
            if ahora - est.ultima_vez < config.AREA_REVINCULACION_GAP_SEG:
                continue
            if config.AREA_CLASE_GRUESA.get(est.clase, "persona") != familia:
                continue
            iou = self._iou(d.bbox, est.bbox)
            if iou >= mejor_iou:
                mejor, mejor_iou = est, iou
        if mejor is None:
            return None
        # Re-indexar bajo el ID nuevo (el viejo desaparece sin emitir salida).
        del estados[mejor.track_id]
        logger.info(
            f"re-vinculado: track {mejor.track_id} -> {d.track_id} "
            f"({mejor.clase}, IoU {mejor_iou:.2f}); el contador continúa",
            extra={"datos": {"anterior": mejor.track_id, "nuevo": d.track_id,
                             "clase": mejor.clase, "iou": round(mejor_iou, 2)}})
        mejor.track_id = d.track_id
        estados[d.track_id] = mejor
        return mejor

    @staticmethod
    def _iou(a: tuple[int, int, int, int],
             b: tuple[int, int, int, int]) -> float:
        """Intersección sobre unión de dos cajas xyxy."""
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        if inter <= 0:
            return 0.0
        area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
        area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _dentro_del_area(bbox: tuple[int, int, int, int],
                         roi_poligono: np.ndarray | None) -> bool:
        """True si la BASE del bbox (centro-inferior) está en el área."""
        if roi_poligono is None:
            return True      # sin ROI: todo el frame es el área
        x1, y1, x2, y2 = bbox
        punto = (float((x1 + x2) / 2.0), float(y2))
        return cv2.pointPolygonTest(roi_poligono, punto, False) >= 0

    @staticmethod
    def _recortar_b64(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> str:
        x1, y1, x2, y2 = bbox
        alto, ancho = frame.shape[:2]
        crop = frame[max(0, y1):min(alto, y2), max(0, x1):min(ancho, x2)]
        if crop.size == 0:
            return ""
        ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(jpg.tobytes()).decode() if ok else ""

    def _foto_area(self, frame: np.ndarray, dets: list[DeteccionVig],
                   contadores: dict[int, str]) -> str:
        """Foto de la alerta: frame COMPLETO (sin zoom) con TODOS los objetos
        vigilados marcados con ELIPSE + etiqueta identificadora (clase, #track
        y cronómetro si lo hay). Si ALERTA_FOTO_COMPLETA=False, cae al recorte
        del primer objeto (comportamiento anterior)."""
        if not config.ALERTA_FOTO_COMPLETA:
            return self._recortar_b64(frame, dets[0].bbox) if dets else ""
        escena = frame.copy()
        for d in dets:
            x1, y1, x2, y2 = d.bbox
            color = config.COLORES_CLASE.get(d.clase, (200, 200, 200))
            # Elipse "marcador de suelo" (estilo Supervision): media elipse en
            # la base del objeto, ancho el del bbox.
            centro = (int((x1 + x2) / 2), y2)
            ejes = (max(8, int((x2 - x1) / 2)), max(6, int((x2 - x1) / 7)))
            cv2.ellipse(escena, centro, ejes, 0.0, -45.0, 235.0, color, 3,
                        cv2.LINE_AA)
            etiqueta = f"{d.clase} #{d.track_id}"
            if d.track_id in contadores:
                etiqueta += f" {contadores[d.track_id]}"
            (tw, th), _ = cv2.getTextSize(etiqueta, cv2.FONT_HERSHEY_SIMPLEX,
                                          0.55, 1)
            ty = max(th + 6, y1)
            cv2.rectangle(escena, (x1, ty - th - 6), (x1 + tw + 6, ty), color, -1)
            cv2.putText(escena, etiqueta, (x1 + 3, ty - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1,
                        cv2.LINE_AA)
        # Reescalar si excede el ancho máximo (payload razonable).
        alto, ancho = escena.shape[:2]
        if ancho > config.ALERTA_FOTO_MAX_ANCHO:
            factor = config.ALERTA_FOTO_MAX_ANCHO / ancho
            escena = cv2.resize(escena, None, fx=factor, fy=factor)
        ok, jpg = cv2.imencode(".jpg", escena, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(jpg.tobytes()).decode() if ok else ""

    @staticmethod
    def _tarjeta(camara: str, estado: _EstadoObjeto, evento: str,
                 ahora: float) -> dict[str, Any]:
        """Arma la tarjeta con el contrato del AlertsSidebar del cliente."""
        clase_gruesa = config.AREA_CLASE_GRUESA.get(estado.clase, "persona")
        etiqueta = config.AREA_CLASE_ETIQUETA.get(estado.clase, estado.clase.upper())
        permanencia = estado.ultima_vez - estado.primera_vez
        if evento == "llegada":
            descripcion = f"{etiqueta} ENTRÓ al área"
            hora_salida: float | None = None
            ts = estado.primera_vez
        elif evento == "permanencia":
            descripcion = (f"{etiqueta} lleva "
                           f"{_formatear_permanencia(permanencia)} en el área")
            hora_salida = None
            ts = ahora
        else:
            descripcion = (f"{etiqueta} SALIÓ del área "
                           f"(permaneció {_formatear_permanencia(permanencia)})")
            hora_salida = estado.ultima_vez
            ts = estado.ultima_vez
        return {
            "event_type": evento,
            "class_name": etiqueta,
            "clase_gruesa": clase_gruesa,
            "global_id": f"VIG-{camara}-T{estado.track_id}",
            "hora_llegada": estado.primera_vez,
            "hora_salida": hora_salida,
            "permanencia_s": round(permanencia, 1),
            "description": descripcion,
            "timestamp": ts,
            "image_base64": estado.crop_b64,
            "camera_name": camara,
            "camera_id": camara,
        }
