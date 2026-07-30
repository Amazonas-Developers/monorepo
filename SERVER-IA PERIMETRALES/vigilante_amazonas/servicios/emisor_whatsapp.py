"""
Emisor de alertas por WhatsApp (bot 'ava') para VIGILANTE-AMAZONAS.

Cada alerta (tarjeta del AlertsSidebar) puede reenviarse como IMAGEN a un grupo
de WhatsApp a través del bot HTTP:

    POST https://<host>:4000/bot/imgV2/number=<id>@g.us
    JSON { "my-file": <base64>, "type": "image/jpg", "my-text": <caption> }

Reglas:
  - El envío SOLO ocurre si el cliente perimetrales-view activó su toggle; ese
    flag lo pasa VigilanteWS por frame (self._enviar_whatsapp). Este emisor no
    decide "si"; solo "cómo" (destino + anti-flood).
  - NO bloqueante: cada POST corre en un hilo daemon; jamás frena la detección.
  - Anti-flood: cooldown por clave (misma persona/evento/cámara) + intervalo
    global mínimo entre cualquier par de envíos.

El destino, los timeouts y los tiempos de anti-flood viven en config.py
(sobrescribibles por variables de entorno).
"""

from __future__ import annotations

import base64
import threading
import time
from typing import Optional

import cv2
import httpx
import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)


class EmisorWhatsApp:
    """Reenvía imágenes de alerta al bot de WhatsApp, thread-safe y no bloqueante."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ultimo_por_clave: dict[str, float] = {}
        self._ultimo_global: float = 0.0
        self.enviados: int = 0
        self.descartados: int = 0   # por anti-flood
        self.fallidos: int = 0

    # ------------------------------------------------------------------ API
    def enviar_b64(self, imagen_b64: str, texto: str, clave: str = "") -> bool:
        """Encola un envío (no bloqueante). Devuelve False si el anti-flood lo
        descartó o no hay imagen; True si se lanzó el POST en segundo plano."""
        if not imagen_b64:
            return False
        ahora = time.time()
        with self._lock:
            if ahora - self._ultimo_global < config.WHATSAPP_INTERVALO_GLOBAL_SEG:
                self.descartados += 1
                return False
            if clave:
                ult = self._ultimo_por_clave.get(clave, 0.0)
                if ahora - ult < config.WHATSAPP_COOLDOWN_SEG:
                    self.descartados += 1
                    return False
                self._ultimo_por_clave[clave] = ahora
                # Poda para que el dict no crezca sin límite.
                if len(self._ultimo_por_clave) > 512:
                    viejo = ahora - config.WHATSAPP_COOLDOWN_SEG * 4
                    self._ultimo_por_clave = {
                        k: v for k, v in self._ultimo_por_clave.items() if v > viejo}
            self._ultimo_global = ahora
        hilo = threading.Thread(target=self._post, args=(imagen_b64, texto),
                                name="whatsapp-envio", daemon=True)
        hilo.start()
        return True

    def enviar_bgr(self, frame_bgr: np.ndarray, texto: str, clave: str = "") -> bool:
        """Igual que enviar_b64 pero recibe una imagen OpenCV (BGR)."""
        if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            return False
        ok, jpg = cv2.imencode(".jpg", frame_bgr,
                               [cv2.IMWRITE_JPEG_QUALITY, config.SNAPSHOT_JPEG_CALIDAD])
        if not ok:
            return False
        return self.enviar_b64(base64.b64encode(jpg.tobytes()).decode(), texto, clave)

    # ------------------------------------------------------------- interno
    @staticmethod
    def _reducir(imagen_b64: str, margen_texto: int) -> str:
        """Reencoge la foto hasta que el cuerpo POST quepa en el limite.

        El bot responde 413 Payload Too Large con las imagenes tal cual las
        produce el pipeline (~110 KB). Se baja primero la resolucion y
        despues la calidad JPEG, en pasos, y se para en cuanto cabe. Si algo
        falla se devuelve la original: mas vale intentar el envio que
        perder la alerta.
        """
        tope = config.WHATSAPP_MAX_PAYLOAD_KB * 1024 - margen_texto
        if tope <= 0 or len(imagen_b64) <= tope:
            return imagen_b64
        try:
            crudo = np.frombuffer(base64.b64decode(imagen_b64), dtype=np.uint8)
            imagen = cv2.imdecode(crudo, cv2.IMREAD_COLOR)
            if imagen is None:
                return imagen_b64
        except Exception:  # noqa: BLE001
            return imagen_b64

        lado = int(config.WHATSAPP_MAX_LADO_PX)
        for _ in range(4):                     # 640 -> 512 -> 409 -> 327
            alto, ancho = imagen.shape[:2]
            escala = min(1.0, lado / float(max(alto, ancho)))
            vista = (cv2.resize(imagen, (max(1, int(ancho * escala)),
                                         max(1, int(alto * escala))),
                                interpolation=cv2.INTER_AREA)
                     if escala < 1.0 else imagen)
            for calidad in (80, 70, 60, 50, 40):
                ok, jpg = cv2.imencode(".jpg", vista,
                                       [cv2.IMWRITE_JPEG_QUALITY, calidad])
                if not ok:
                    continue
                b64 = base64.b64encode(jpg.tobytes()).decode()
                if len(b64) <= tope:
                    logger.debug("foto reducida a %dx%d q%d (%d KB de b64)",
                                 vista.shape[1], vista.shape[0], calidad,
                                 len(b64) // 1024)
                    return b64
            lado = int(lado * 0.8)
        logger.warning("no se pudo reducir la foto por debajo de %d KB; "
                       "se envia igualmente", config.WHATSAPP_MAX_PAYLOAD_KB)
        return b64

    def _post(self, imagen_b64: str, texto: str) -> None:
        imagen_b64 = self._reducir(imagen_b64, len(texto.encode("utf-8")) + 200)
        payload = {"my-text": texto, "my-file": imagen_b64, "type": "image/jpg"}
        try:
            with httpx.Client(verify=config.WHATSAPP_VERIFY_TLS,
                              timeout=config.WHATSAPP_TIMEOUT_SEG) as cliente:
                r = cliente.post(config.WHATSAPP_BOT_URL, json=payload,
                                 headers={"Content-Type": "application/json"})
                r.raise_for_status()
            with self._lock:
                self.enviados += 1
            logger.info("alerta enviada a WhatsApp (HTTP %s)", r.status_code)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.fallidos += 1
            logger.warning("no se pudo enviar la alerta a WhatsApp: %s", e)


# ---------------------------------------------------------------- singleton
_instancia: Optional[EmisorWhatsApp] = None
_lock_instancia = threading.Lock()


def get_emisor_whatsapp() -> EmisorWhatsApp:
    """Singleton por proceso."""
    global _instancia
    with _lock_instancia:
        if _instancia is None:
            _instancia = EmisorWhatsApp()
        return _instancia
