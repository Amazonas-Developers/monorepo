"""
src/analityc/core/estacionamiento_whatsapp.py — envío del PDF por WhatsApp.

Portado de ARUBA_DEFINITIVO (`amazonas365_reporte.enviar_documento_whatsapp`):
POST al bot 'ava' con el documento en base64.

    POST {ENDPOINT}/bot/imgV2/number=<destino>
    campos: my-file (base64 sin data:), my-text (caption), type, filename

`multipart` (por defecto) manda cada clave como campo de formulario
(express-fileupload, fieldSize 64 MB); `json` los manda en el cuerpo. El bot
sirve su certificado sin cadena intermedia, así que la verificación TLS va
apagada por defecto — mismo criterio que el emisor de imágenes del vigilante.

Este módulo NO genera el PDF (eso es `estacionamiento_reporte.py`) ni decide
cuándo mandarlo (eso es `estacionamiento_planificador.py`): solo lo entrega.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

#: Mismo bot que las alertas del vigilante; el GRUPO por defecto es el de
#: reportes de ARUBA. Todo configurable (regla 6).
ENDPOINT_POR_DEFECTO = 'https://72.68.60.254:4000'
GRUPO_POR_DEFECTO = '120363402589311344@g.us'
RUTA_ENVIO = '/bot/imgV2/number={numero}'


def endpoint() -> str:
    return (os.getenv('ESTACIONAMIENTO_BOT_URL') or ENDPOINT_POR_DEFECTO).strip()


def destino() -> str:
    return (os.getenv('ESTACIONAMIENTO_REPORTE_GRUPO')
            or GRUPO_POR_DEFECTO).strip()


def verificar_tls() -> bool:
    return os.getenv('ESTACIONAMIENTO_BOT_VERIFY_TLS', '0') == '1'


@dataclass
class ResultadoEnvio:
    """Qué pasó con el reporte: el PDF, a dónde fue y qué respondió el bot."""
    nombre_archivo: str
    caption: str
    pdf_bytes: bytes = b''
    url: str = ''
    guardado_en: Optional[str] = None
    enviado: bool = False
    http_status: Optional[int] = None
    http_body: Optional[str] = None
    error: Optional[str] = None
    metricas: Dict[str, Any] = field(default_factory=dict)


def enviar_documento(archivo_base64: str, nombre_archivo: str,
                     caption: str = '', tipo_mime: str = 'application/pdf',
                     destino_whatsapp: Optional[str] = None,
                     endpoint_base: Optional[str] = None,
                     formato: str = 'multipart',
                     mencion: Optional[str] = None,
                     verificar_ssl: Optional[bool] = None,
                     timeout: int = 90) -> requests.Response:
    """POST del documento al bot. Deja que la excepción de red suba: quien
    llama decide si eso es un fallo del reporte (ver `enviar_pdf`)."""
    base = (endpoint_base or endpoint()).rstrip('/')
    numero = destino_whatsapp or destino()
    url = base + RUTA_ENVIO.format(numero=numero)
    cuerpo: Dict[str, str] = {
        'my-file': archivo_base64,
        'my-text': caption or '',
        'type': tipo_mime,
        'filename': nombre_archivo,
    }
    if mencion:
        cuerpo['mentions'] = mencion
    verificar = verificar_tls() if verificar_ssl is None else verificar_ssl
    if formato == 'json':
        return requests.post(url, json=cuerpo, timeout=timeout,
                             verify=verificar)
    # (None, valor) fuerza multipart/form-data con cada clave como campo.
    campos = {k: (None, v) for k, v in cuerpo.items()}
    return requests.post(url, files=campos, timeout=timeout, verify=verificar)


def enviar_pdf(pdf_bytes: bytes, nombre_archivo: str, caption: str,
               guardar_en: Optional[str] = None, enviar: bool = True,
               metricas: Optional[Dict[str, Any]] = None,
               **kw: Any) -> ResultadoEnvio:
    """Guarda el PDF (si se pide) y lo manda al grupo. Nunca lanza.

    Devolver el resultado en vez de lanzar es lo que permite al planificador
    dejar el reporte en disco aunque el bot esté caído (o la VPN lo bloquee,
    que es el fallo real más frecuente en esta máquina).
    """
    if not nombre_archivo.lower().endswith('.pdf'):
        nombre_archivo += '.pdf'
    resultado = ResultadoEnvio(
        nombre_archivo=nombre_archivo, caption=caption, pdf_bytes=pdf_bytes,
        metricas=dict(metricas or {}),
        url=(kw.get('endpoint_base') or endpoint()).rstrip('/')
            + RUTA_ENVIO.format(numero=kw.get('destino_whatsapp') or destino()))

    if guardar_en:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(guardar_en)),
                        exist_ok=True)
            with open(guardar_en, 'wb') as fh:
                fh.write(pdf_bytes)
            resultado.guardado_en = guardar_en
        except OSError as exc:
            logger.warning('no se pudo guardar el reporte en %s: %s',
                           guardar_en, exc)

    if not enviar:
        return resultado

    try:
        respuesta = enviar_documento(
            archivo_base64=base64.b64encode(pdf_bytes).decode('ascii'),
            nombre_archivo=nombre_archivo, caption=caption, **kw)
        resultado.http_status = respuesta.status_code
        resultado.http_body = respuesta.text[:500]
        resultado.enviado = 200 <= respuesta.status_code < 300
        if not resultado.enviado:
            resultado.error = (f'HTTP {respuesta.status_code}: '
                               f'{respuesta.text[:300]}')
            logger.warning('el bot rechazó el reporte: %s', resultado.error)
        else:
            logger.info('reporte del estacionamiento enviado (%s, %d KB)',
                        nombre_archivo, len(pdf_bytes) // 1024)
    except requests.RequestException as exc:
        # Caso típico en esta máquina: la VPN bloquea la IP del bot
        # (WinError 10013). El PDF ya quedó en disco.
        resultado.error = f'fallo de red al enviar el reporte: {exc}'
        logger.warning('%s', resultado.error)
    except Exception as exc:                          # noqa: BLE001
        resultado.error = f'{type(exc).__name__}: {exc}'
        logger.exception('fallo inesperado enviando el reporte')
    return resultado
