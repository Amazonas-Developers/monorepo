"""
src/app/whatsapp_alertas.py — Reenvio de alertas a WhatsApp para CUALQUIER
pipeline, no solo VigilanteAmazonas.

## El problema que resuelve

El interruptor «Enviar por WhatsApp» existia unicamente en `perimetrales-view`,
y el servidor solo lo consumia en `VigilanteWS`: `app.py` fijaba
`processor._enviar_whatsapp` y su propio comentario reconocia que era
«inofensivo para el resto de procesadores» — es decir, en los demas pipelines
el flag no hacia nada.

Al llevar el interruptor a los otros clientes, un boton que no envia nada seria
peor que no tenerlo. Este modulo cierra ese hueco: reenvia las alertas de
cualquier procesador que las publique en `metadata["alerts"]`.

## Que pipelines se benefician

| Pipeline | Publica `alerts` | Resultado |
|---|---|---|
| `VigilanteAmazonas` | si (via su adaptador) | ya funcionaba; se respeta su ruta propia |
| `Hummus`, `Misters` | si | **ahora funciona** |
| `Perimetrales*` (base_perimeter) | si | **ahora funciona** |
| `Personal de Amazonas` (tienda) | **no** | el interruptor viaja pero no hay alertas que enviar |

Lo ultimo es una limitacion real del pipeline de tienda, no de este modulo: su
metadata publica `detections`, `demographics`, `heatmap` y `analytics_report`,
pero ninguna alerta. Definir que evento de tienda merece un WhatsApp (¿entrada
de persona? ¿aforo por encima de un umbral?) es una decision de producto, no
algo que se pueda deducir del codigo.

## Diseno

- **No decide el "si"**: solo actua cuando el cliente activo su interruptor.
- **No bloquea**: `EmisorWhatsApp` publica en un hilo demonio y trae anti-flood
  por clave, asi que no hay que anadir throttling aqui.
- **Nunca lanza**: un fallo de notificacion jamas puede tumbar la inferencia.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_emisor: Optional[Any] = None
_intentado = False


def _obtener_emisor():
    """Carga perezosa del emisor. Si VIGILANTE no esta disponible, se anota una
    vez y el reenvio queda inactivo sin molestar mas."""
    global _emisor, _intentado
    if _intentado:
        return _emisor
    _intentado = True
    try:
        from vigilante_amazonas.servicios.emisor_whatsapp import EmisorWhatsApp
        _emisor = EmisorWhatsApp()
    except Exception as exc:
        logger.info("reenvio a WhatsApp no disponible (%s)", exc)
        _emisor = None
    return _emisor


def _texto(alerta: Dict[str, Any], camara: str) -> str:
    """Titular del mensaje. Se mantiene el formato de VIGILANTE para que los
    mensajes del grupo sigan siendo homogeneos."""
    clase = str(alerta.get('class_name') or alerta.get('event_type')
                or 'deteccion')
    desc = str(alerta.get('description') or '').strip()
    ts = str(alerta.get('timestamp') or '').strip()
    partes = [f"🚨 {clase.upper()}"]
    if camara:
        partes.append(f"cámara {camara}")
    titulo = " · ".join(partes)
    if desc:
        titulo += f"\n{desc}"
    if ts:
        titulo += f"\n🕒 {ts}"
    return titulo


def reenviar(metadata: Any, camera_name: str = '', camera_id: str = '') -> int:
    """Reenvia a WhatsApp las alertas que traiga `metadata`.

    Devuelve cuantas se encolaron. Nunca lanza: si algo falla, se registra y se
    sigue, porque esto corre dentro del bucle de inferencia.
    """
    try:
        if not isinstance(metadata, dict):
            return 0
        alertas = metadata.get('alerts') or []
        if not alertas:
            return 0
        emisor = _obtener_emisor()
        if emisor is None:
            return 0

        enviadas = 0
        for alerta in alertas:
            if not isinstance(alerta, dict):
                continue
            # El recorte de la deteccion es mas util que el frame completo;
            # si no viene, se usa la imagen de la alerta.
            imagen = (alerta.get('crop_image')
                      or alerta.get('image_base64') or '')
            if not imagen:
                continue
            # La clave alimenta el anti-flood del emisor: misma camara + mismo
            # tipo de evento no se repite dentro del periodo de enfriamiento.
            clave = f"{camera_id or camera_name}|{alerta.get('event_type') or alerta.get('class_name') or ''}"
            try:
                if emisor.enviar_b64(imagen, _texto(alerta, camera_name),
                                     clave=clave):
                    enviadas += 1
            except Exception as exc:
                logger.debug("no se pudo reenviar una alerta: %s", exc)
        return enviadas
    except Exception as exc:
        logger.debug("reenvio a WhatsApp fallo: %s", exc)
        return 0
