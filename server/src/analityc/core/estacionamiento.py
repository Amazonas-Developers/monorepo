"""
src/analityc/core/estacionamiento.py — Modo ESTACIONAMIENTO (4-ago-2026).

La óptica vehicular del motor vigilante, en SU PROPIO archivo junto al resto
de procesadores del servidor (regla del operador: un archivo por
funcionalidad, nada de módulos gigantes).

Reutiliza TODO el motor de VigilanteWS (detección 7 clases + ByteTrack +
rastreador de área con re-vinculación) y cambia la óptica:

  * Solo los VEHÍCULOS alertan (las personas se detectan y se dibujan, pero
    no generan tarjetas: en un estacionamiento la persona es contexto).
  * «estacionado»: vehículo quieto en el área más de
    ESTACIONAMIENTO_UMBRAL_SEG (config de vigilante, 5 min por defecto).
  * «pernocta»: vehículo que sigue dentro entrada la noche (portado de
    ARUBA_DEFINITIVO, `base_perimeter`: franja 19:00-06:00, una vez por
    vehículo). Es el dato que más pesa en el reporte diario.
  * La salida reporta el tiempo total que estuvo estacionado.
  * `metadata['ocupacion']` lleva cuántos vehículos hay AHORA en el área.
  * WhatsApp/Jarvis: mismo flujo del vigilante (toggle del pie + local de la
    cámara como encabezado).

## Lo que aporta cada módulo hermano (un archivo por funcionalidad)

    estacionamiento_registro.py      bitácora CSV diaria de eventos
    estacionamiento_reporte.py       el PDF (ReportLab)
    estacionamiento_whatsapp.py      entrega del PDF al bot
    estacionamiento_planificador.py  el reporte diario programado
    estacionamiento_placas.py        lectura de placas (ALPR, opcional)

Instancia PROPIA (no comparte el singleton del vigilante): cada modo tiene su
rastreador de área con su umbral y su lock — compartir el detector entre dos
locks distintos arriesgaría inferencias concurrentes sobre el mismo modelo.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.adaptador_websocket import VigilanteWS
from vigilante_amazonas.deteccion.mapeo_clases import CLASE_A_ID
from vigilante_amazonas.servicios.rastreador_area import (
    RastreadorArea, _formatear_permanencia)
from vigilante_amazonas.utilidades.registro import configurar_registro

from . import estacionamiento_placas as _placas
from . import estacionamiento_planificador as _planificador
from . import estacionamiento_registro as _bitacora

logger = configurar_registro(__name__)

#: class_id de las clases con placa (las que van a OCR).
_IDS_VEHICULO = {CLASE_A_ID[c] for c in ("moto", "carro", "camioneta",
                                         "camion") if c in CLASE_A_ID}


def adaptar_tarjeta(tarjeta: Any) -> dict[str, Any] | None:
    """La óptica de estacionamiento sobre una tarjeta del área. PURA a
    propósito: se prueba sin GPU ni motor.

    - Tarjeta que no sea de VEHÍCULO -> None (se descarta).
    - 'permanencia' -> 'estacionado', con descripción propia.
    - llegada / salida / merodeo / pernocta de vehículo -> pasan tal cual.
    """
    if not isinstance(tarjeta, dict):
        return None
    if str(tarjeta.get("clase_gruesa") or "").strip().lower() != "vehiculo":
        return None
    if str(tarjeta.get("event_type") or "").strip().lower() == "permanencia":
        adaptada = dict(tarjeta)
        adaptada["event_type"] = "estacionado"
        etiqueta = str(tarjeta.get("class_name") or "VEHÍCULO")
        duracion = _formatear_permanencia(
            float(tarjeta.get("permanencia_s") or 0.0))
        adaptada["description"] = f"{etiqueta} ESTACIONADO ({duracion})"
        return adaptada
    return tarjeta


def es_horario_de_pernocta(momento: Optional[datetime] = None) -> bool:
    """¿Estamos en la franja nocturna? (19:00-06:59 por defecto, ARUBA).

    La franja CRUZA la medianoche, así que la comparación es un OR, no un
    rango: `hora >= inicio or hora <= fin`.
    """
    ahora = momento or datetime.now()
    inicio = config.ESTACIONAMIENTO_PERNOCTA_DESDE
    fin = config.ESTACIONAMIENTO_PERNOCTA_HASTA
    return ahora.hour >= inicio or ahora.hour <= fin


class EstacionamientoWS(VigilanteWS):
    """VigilanteWS con la óptica de estacionamiento (ver módulo)."""

    def __init__(self) -> None:
        super().__init__()
        # Rastreador de área PROPIO con el umbral de "estacionado" (el del
        # vigilante sigue con AREA_ALERTA_PERMANENCIA_SEG global).
        from vigilante_amazonas.servicios.detector_merodeo import DetectorMerodeo
        self.area = RastreadorArea(
            DetectorMerodeo(),
            umbral_permanencia_seg=config.ESTACIONAMIENTO_UMBRAL_SEG)
        self._whatsapp_eventos = config.ESTACIONAMIENTO_EVENTOS_WHATSAPP
        self._whatsapp_clases = ("vehiculo",)

        # Estado para el reporte y la pernocta.
        self._lock_estado = threading.Lock()
        self._pernoctados: set[str] = set()      # global_id ya registrados
        self._ultimo_frame: Optional[np.ndarray] = None
        self._ultima_camara: str = ""
        self._ocupacion: Dict[str, int] = {}     # camara -> vehículos dentro
        self._placa_de_track: Dict[str, str] = {}
        self._ultimo_encolado: Dict[str, float] = {}

        self.lector_placas = _placas.lector(self._al_leer_placa)
        _planificador.planificador(self._contexto_del_reporte).iniciar()
        logger.info(
            "EstacionamientoWS listo: umbral estacionado = %.0f s; "
            "pernocta %02d:00-%02d:59; eventos WhatsApp = %s; placas = %s",
            config.ESTACIONAMIENTO_UMBRAL_SEG,
            config.ESTACIONAMIENTO_PERNOCTA_DESDE,
            config.ESTACIONAMIENTO_PERNOCTA_HASTA,
            ",".join(config.ESTACIONAMIENTO_EVENTOS_WHATSAPP),
            "sí" if self.lector_placas else "no")

    # ── Óptica del modo ──────────────────────────────────────────────
    def _filtrar_tarjeta(self, tarjeta: dict[str, Any]) -> dict[str, Any] | None:
        adaptada = adaptar_tarjeta(tarjeta)
        if adaptada is not None:
            self._anotar_en_bitacora(adaptada)
        return adaptada

    def _anotar_en_bitacora(self, tarjeta: Dict[str, Any]) -> None:
        """Cada tarjeta que sobrevive va a la bitácora CSV, que es de donde
        el reporte diario saca sus números."""
        camara = str(tarjeta.get("camera_name") or "")
        conteos = self._conteos_del_area(camara)
        with self._lock_estado:
            placa = self._placa_de_track.get(
                str(tarjeta.get("global_id") or ""), "")
        _bitacora.anotar(
            evento=str(tarjeta.get("event_type") or ""),
            clase=self._clase_propia(tarjeta),
            track_id=tarjeta.get("global_id") or "",
            tiempo_acumulado_s=float(tarjeta.get("permanencia_s") or 0.0),
            camara=camara,
            local=self._locales.get(camara, ""),
            placa=placa,
            conteos=conteos)

    def _conteos_del_area(self, camara: str) -> Dict[str, int]:
        """Las columnas por clase de la bitácora (Cars/Trucks/…), contadas
        del área AHORA. Sin esto salían todas en 0 y los scripts de ARUBA
        que leen esas columnas no verían nada."""
        conteos = {"vehiculos_area": 0, "cars": 0, "trucks": 0, "buses": 0,
                   "motorcycles": 0, "personas_dentro": 0, "personas_area": 0}
        try:
            for estado in self.area.objetos_dentro(camara):
                clase = str(estado.clase).lower()
                if clase in ("persona", "personal_seguridad"):
                    conteos["personas_area"] += 1
                    conteos["personas_dentro"] += 1
                    continue
                conteos["vehiculos_area"] += 1
                if clase == "carro":
                    conteos["cars"] += 1
                elif clase in ("camion", "camioneta"):
                    conteos["trucks"] += 1
                elif clase == "moto":
                    conteos["motorcycles"] += 1
        except Exception:                             # noqa: BLE001
            logger.debug("no se pudieron contar los objetos del área")
        return conteos

    @staticmethod
    def _clase_propia(tarjeta: Dict[str, Any]) -> str:
        """La tarjeta trae la etiqueta en mayúsculas con acentos ('CAMIÓN');
        la bitácora quiere la clase propia ('camion')."""
        etiqueta = str(tarjeta.get("class_name") or "").strip().lower()
        return {"camión": "camion", "camion": "camion", "carro": "carro",
                "moto": "moto", "camioneta": "camioneta",
                "persona": "persona"}.get(etiqueta, etiqueta)

    # ── Frame ────────────────────────────────────────────────────────
    def process_frame(self, img: np.ndarray, camera_id: str,
                      camera_name: str | None = None,
                      track_classes: list[int] | None = None,
                      draw: bool = True,
                      roi: Any = None,
                      roi_activate: bool = False,
                      establecimiento: str | None = None) -> tuple[np.ndarray, dict[str, Any]]:
        img_salida, metadata = super().process_frame(
            img, camera_id, camera_name=camera_name,
            track_classes=track_classes, draw=draw,
            roi=roi, roi_activate=roi_activate,
            establecimiento=establecimiento)
        camara = str(camera_name or camera_id)
        try:
            ocupacion = self.area.ocupacion(camara, "vehiculo")
            metadata["ocupacion"] = ocupacion
            with self._lock_estado:
                self._ocupacion[camara] = ocupacion
                self._ultimo_frame = img
                self._ultima_camara = camara
            # Pernocta y placas: después de la ocupación porque ambas la usan.
            pernoctas = self._revisar_pernocta(camara)
            if pernoctas:
                metadata.setdefault("alerts", []).extend(pernoctas)
            self._encolar_placas(img, metadata, camara)
            self._pegar_placas(metadata)
        except Exception:                    # noqa: BLE001 — nunca al frame loop
            logger.debug("extras de estacionamiento fallaron", exc_info=True)
        return img_salida, metadata

    # ── Pernocta ─────────────────────────────────────────────────────
    def _revisar_pernocta(self, camara: str) -> List[Dict[str, Any]]:
        """Vehículos que siguen dentro entrada la noche: UNA tarjeta por
        vehículo y por noche (portado de ARUBA).

        Exige que el vehículo esté CONFIRMADO (ya emitió su llegada) y que
        lleve al menos el umbral de «estacionado». Sin eso, la primera
        prueba en vivo alertó «PERNOCTA (lleva 0s)» ANTES que la llegada:
        un coche que solo cruza de noche no pernocta.
        """
        if not config.ESTACIONAMIENTO_PERNOCTA_ACTIVA:
            return []
        if not es_horario_de_pernocta():
            return []
        tarjetas: List[Dict[str, Any]] = []
        for estado in self.area.objetos_dentro(camara, "vehiculo"):
            if not estado.alertado_llegada:
                continue
            if estado.permanencia() < config.ESTACIONAMIENTO_UMBRAL_SEG:
                continue
            global_id = f"VIG-{camara}-T{estado.track_id}"
            with self._lock_estado:
                if global_id in self._pernoctados:
                    continue
                self._pernoctados.add(global_id)
            etiqueta = config.AREA_CLASE_ETIQUETA.get(
                estado.clase, estado.clase.upper())
            permanencia = estado.permanencia()
            tarjeta = {
                "event_type": "pernocta",
                "class_name": etiqueta,
                "clase_gruesa": "vehiculo",
                "global_id": global_id,
                "hora_llegada": estado.primera_vez,
                "hora_salida": None,
                "permanencia_s": round(permanencia, 1),
                "description": (f"{etiqueta} PERNOCTA en el área "
                                f"(lleva {_formatear_permanencia(permanencia)})"),
                "timestamp": time.time(),
                "image_base64": estado.crop_b64,
                "camera_name": camara,
                "camera_id": camara,
            }
            tarjetas.append(tarjeta)
            self._anotar_en_bitacora(tarjeta)
            if getattr(self, "_enviar_whatsapp", False):
                self._notificar_whatsapp(tarjeta)
            logger.info("pernocta: %s en '%s' (%s)", etiqueta, camara,
                        _formatear_permanencia(permanencia))
        return tarjetas

    def olvidar_pernoctas(self) -> int:
        """Vacía la memoria de pernoctas (al amanecer, para que la noche
        siguiente vuelva a contar). La llama el planificador tras el
        reporte; también sirve a mano."""
        with self._lock_estado:
            n = len(self._pernoctados)
            self._pernoctados.clear()
        return n

    # ── Placas ───────────────────────────────────────────────────────
    def _al_leer_placa(self, track_id: str, placa: str, confianza: float) -> None:
        with self._lock_estado:
            self._placa_de_track[str(track_id)] = placa
        logger.info("placa leída: %s (%.2f) para %s", placa, confianza,
                    track_id)

    def _encolar_placas(self, img: np.ndarray, metadata: Dict[str, Any],
                        camara: str) -> None:
        """Manda a OCR el recorte de cada vehículo rastreado, con throttle
        por track (el OCR cuesta y la placa no cambia)."""
        if self.lector_placas is None:
            return
        cada = config.ESTACIONAMIENTO_PLACA_CADA_SEG
        ahora = time.time()
        alto, ancho = img.shape[:2]
        for det in metadata.get("detections") or []:
            if not isinstance(det, dict):
                continue
            # Por class_id, no por texto de la etiqueta: la etiqueta cambia
            # (nombre de persona de interés, cronómetro) y el id no.
            if det.get("class_id") not in _IDS_VEHICULO:
                continue
            track = str(det.get("tracker_id") or "")
            clave = f"{camara}:{track}"
            with self._lock_estado:
                if track and self._placa_de_track.get(
                        f"VIG-{camara}-T{track}"):
                    continue          # ya tiene placa buena
                if ahora - self._ultimo_encolado.get(clave, 0.0) < cada:
                    continue
                self._ultimo_encolado[clave] = ahora
            caja = det.get("box") or []
            if len(caja) != 4:
                continue
            x1, y1, x2, y2 = (int(max(0, caja[0])), int(max(0, caja[1])),
                              int(min(ancho, caja[2])), int(min(alto, caja[3])))
            if x2 <= x1 or y2 <= y1:
                continue
            self.lector_placas.encolar(f"VIG-{camara}-T{track}",
                                       img[y1:y2, x1:x2].copy(), camara)

    def _pegar_placas(self, metadata: Dict[str, Any]) -> None:
        """Añade « · Placa ABC123» a la etiqueta del vehículo que la tenga
        (igual que ARUBA la pinta sobre la caja)."""
        if not self._placa_de_track:
            return
        camara = self._ultima_camara
        with self._lock_estado:
            placas = dict(self._placa_de_track)
        for det in metadata.get("detections") or []:
            if not isinstance(det, dict):
                continue
            placa = placas.get(f"VIG-{camara}-T{det.get('tracker_id')}")
            if placa and "Placa" not in str(det.get("label") or ""):
                det["label"] = f"{det.get('label', '')} · Placa {placa}"

    # ── Contexto del reporte ─────────────────────────────────────────
    def _contexto_del_reporte(self) -> Dict[str, Any]:
        """Lo que el planificador necesita y solo el modo sabe: cámara,
        local, foto reciente, ocupación y placas del período."""
        with self._lock_estado:
            camara = self._ultima_camara
            frame = self._ultimo_frame
            ocupacion = sum(self._ocupacion.values())
        ahora = datetime.now()
        contexto: Dict[str, Any] = {
            "camara": camara,
            "local": self._locales.get(camara, ""),
            "camaras": max(1, len(self._ocupacion)),
            "ocupacion": ocupacion,
            "foto_b64": _planificador.foto_a_base64(frame),
            "placas": _placas.placas_del_periodo(ahora - timedelta(hours=24),
                                                 ahora),
        }
        # Tras el reporte diario, la noche siguiente vuelve a contar pernocta.
        if not es_horario_de_pernocta(ahora):
            self.olvidar_pernoctas()
        return contexto


_instancia: EstacionamientoWS | None = None
_lock_instancia = threading.Lock()


def get_estacionamiento_ws() -> EstacionamientoWS:
    """Singleton por proceso (mismo contrato que get_vigilante_ws)."""
    global _instancia
    with _lock_instancia:
        if _instancia is None:
            _instancia = EstacionamientoWS()
        return _instancia
