"""
src/analityc/core/estacionamiento_planificador.py — el reporte diario.

Portado de ARUBA_DEFINITIVO (`report_scheduler.ReportScheduler`): un hilo que
cada día, a la hora fijada, resume las últimas 24 h de la bitácora, arma el
PDF y lo manda al grupo de WhatsApp; el PDF queda además en disco.

Como en ARUBA: los LUNES sale el reporte completo (gráficas, foto de la
cámara, observaciones) y el resto de los días el simple (dos tablas).
Configurable con `ESTACIONAMIENTO_REPORTE_DIA_COMPLETO`.

## Dos cosas que aquí se hacen mejor que en el original

1. **El nombre del sitio no se adivina con OCR.** ARUBA le pasaba
   `pytesseract` a la esquina del frame y, si fallaba, PREGUNTABA POR
   TERMINAL con 2 minutos de espera — inviable en un servidor desatendido.
   ELDE ya sabe cómo se llama cada cámara y a qué local pertenece (el select
   "Local" del cliente), así que el sitio sale de ahí.
2. **El reporte se genera aunque el bot esté caído**: el PDF se guarda
   primero y el envío es lo último (ver `estacionamiento_whatsapp`).
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import estacionamiento_registro as _registro
from . import estacionamiento_whatsapp as _whatsapp
from .estacionamiento_reporte import construir_pdf, slug

logger = logging.getLogger(__name__)

#: `server/src/analityc/core/…` -> parents[3] es `server/`.
_RAIZ_SERVIDOR = Path(__file__).resolve().parents[3]


def carpeta_reportes() -> Path:
    propia = (os.getenv('ESTACIONAMIENTO_REPORTES_DIR') or '').strip()
    return (Path(propia) if propia
            else _RAIZ_SERVIDOR / 'output' / 'estacionamiento' / 'reportes')


def _entero(nombre: str, defecto: int) -> int:
    try:
        return int((os.getenv(nombre) or '').strip() or defecto)
    except ValueError:
        return defecto


class PlanificadorReportes:
    """Hilo que dispara el reporte diario. Nunca tumba al servidor."""

    def __init__(self, proveedor_contexto: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        # El modo le pasa una función que devuelve {camara, local, foto_b64,
        # ocupacion}: así el planificador no conoce a VigilanteWS por dentro.
        self._contexto = proveedor_contexto
        self._parar = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self.ultimo_resultado: Optional[_whatsapp.ResultadoEnvio] = None
        self.reportes_emitidos = 0

    # ── configuración ────────────────────────────────────────────────
    @staticmethod
    def hora() -> int:
        return max(0, min(23, _entero('ESTACIONAMIENTO_REPORTE_HORA', 16)))

    @staticmethod
    def minuto() -> int:
        return max(0, min(59, _entero('ESTACIONAMIENTO_REPORTE_MINUTO', 0)))

    @staticmethod
    def habilitado() -> bool:
        return (os.getenv('ESTACIONAMIENTO_REPORTE_ACTIVO', '1').strip()
                not in ('0', 'false', 'no'))

    @staticmethod
    def dia_completo() -> int:
        """Día de la semana con reporte COMPLETO (0=lunes, como ARUBA)."""
        return _entero('ESTACIONAMIENTO_REPORTE_DIA_COMPLETO', 0)

    # ── ciclo de vida ────────────────────────────────────────────────
    def iniciar(self) -> bool:
        if not self.habilitado():
            logger.info('planificador del reporte de estacionamiento '
                        'DESACTIVADO (ESTACIONAMIENTO_REPORTE_ACTIVO=0)')
            return False
        if self._hilo and self._hilo.is_alive():
            return True
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True,
                                      name='estacionamiento-reporte')
        self._hilo.start()
        logger.info('reporte diario del estacionamiento programado a las '
                    '%02d:%02d', self.hora(), self.minuto())
        return True

    def detener(self) -> None:
        self._parar.set()

    def _bucle(self) -> None:
        while not self._parar.is_set():
            espera = self._segundos_hasta_la_hora()
            logger.info('próximo reporte del estacionamiento en %d min',
                        int(espera // 60))
            # Espera interrumpible: `Event.wait` corta en cuanto se detiene.
            if self._parar.wait(espera):
                return
            try:
                self.generar_ahora()
            except Exception:                         # noqa: BLE001
                logger.exception('fallo generando el reporte programado')
            # Evita disparar dos veces dentro del mismo minuto.
            self._parar.wait(61)

    def _segundos_hasta_la_hora(self) -> float:
        ahora = datetime.now()
        objetivo = ahora.replace(hour=self.hora(), minute=self.minuto(),
                                 second=0, microsecond=0)
        if objetivo <= ahora:
            objetivo += timedelta(days=1)
        return max(1.0, (objetivo - ahora).total_seconds())

    # ── generación ───────────────────────────────────────────────────
    def generar_ahora(self, horas: int = 24, enviar: bool = True,
                      formato: Optional[str] = None) -> _whatsapp.ResultadoEnvio:
        """Arma y manda el reporte de las últimas `horas`. Reutilizable a
        mano (botón/API) además de por el temporizador."""
        ahora = datetime.now()
        desde = ahora - timedelta(hours=horas)

        # Lo que esté en el buffer debe estar en el CSV ANTES de contar.
        _registro.volcar()
        metricas = _registro.contar_periodo(desde, ahora)

        contexto: Dict[str, Any] = {}
        if self._contexto is not None:
            try:
                contexto = self._contexto() or {}
            except Exception:                         # noqa: BLE001
                logger.exception('el proveedor de contexto del reporte falló')

        sitio = (contexto.get('local') or contexto.get('camara')
                 or 'Estacionamiento')
        es_completo = ((formato or '').lower() == 'completo'
                       or (formato is None
                           and ahora.weekday() == self.dia_completo()))
        codigo = f'EST-{ahora:%Y%m%d-%H%M}'

        pdf, derivadas = construir_pdf(
            metricas,
            cliente=contexto.get('cliente') or 'Amazonas 365',
            sitio=sitio,
            periodo_desde=desde, periodo_hasta=ahora,
            franja_horaria=f'Últimas {horas} horas',
            camaras_monitoreadas=str(contexto.get('camaras') or 1),
            codigo_reporte=codigo,
            imagen_camara_base64=contexto.get('foto_b64') if es_completo else None,
            imagen_camara_etiqueta=(f'{contexto.get("camara", "")} '
                                    f'({ahora:%H:%M:%S})').strip(),
            observaciones=contexto.get('observaciones', ''),
            conclusiones=contexto.get('conclusiones', ''),
            ocupacion_actual=contexto.get('ocupacion'),
            placas=contexto.get('placas'),
            formato='completo' if es_completo else 'simple',
            emitido_en=ahora)

        nombre = f'Reporte_Estacionamiento_{slug(sitio)}_{ahora:%Y%m%d_%H%M}.pdf'
        destino = carpeta_reportes() / nombre
        caption = self._caption(sitio, desde, ahora, derivadas, metricas)

        resultado = _whatsapp.enviar_pdf(
            pdf, nombre_archivo=nombre, caption=caption,
            guardar_en=str(destino), enviar=enviar, metricas=derivadas)
        self.ultimo_resultado = resultado
        self.reportes_emitidos += 1
        logger.info('reporte del estacionamiento %s: enviado=%s http=%s '
                    'archivo=%s', codigo, resultado.enviado,
                    resultado.http_status, resultado.guardado_en)
        return resultado

    @staticmethod
    def _caption(sitio: str, desde: datetime, hasta: datetime,
                 derivadas: Dict[str, Any], crudas: Dict[str, int]) -> str:
        partes = ['*Reporte de Estacionamiento — Amazonas 365*',
                  f'Sitio: {sitio}',
                  f'Período: {desde:%d/%m/%Y %H:%M} — {hasta:%d/%m/%Y %H:%M}',
                  f'Vehículos: {crudas.get("vehiculo_entrada", 0)} entraron · '
                  f'{crudas.get("vehiculo_permanencia", 0)} estacionados · '
                  f'{crudas.get("vehiculo_salida", 0)} salieron']
        if crudas.get('vehiculo_pernocta'):
            partes.append(f'Pernoctaron: {crudas["vehiculo_pernocta"]}')
        return '\n'.join(partes)


_planificador: Optional[PlanificadorReportes] = None
_lock = threading.Lock()


def planificador(proveedor_contexto: Optional[Callable[[], Dict[str, Any]]] = None
                 ) -> PlanificadorReportes:
    """Singleton por proceso (el modo lo arranca al construirse)."""
    global _planificador
    with _lock:
        if _planificador is None:
            _planificador = PlanificadorReportes(proveedor_contexto)
        elif proveedor_contexto is not None:
            _planificador._contexto = proveedor_contexto
        return _planificador


def foto_a_base64(frame: Any) -> Optional[str]:
    """Frame BGR -> JPEG en base64 para la 'vista de la cámara'."""
    try:
        import cv2
        if frame is None or getattr(frame, 'size', 0) == 0:
            return None
        ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(jpg.tobytes()).decode() if ok else None
    except Exception:                                 # noqa: BLE001
        return None
