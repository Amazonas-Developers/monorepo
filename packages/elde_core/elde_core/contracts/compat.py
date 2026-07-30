"""
Capa de compatibilidad: traduce el formato antiguo al envelope nuevo.

Sin esto, el dia que el servidor empiece a validar dejarian de funcionar los
4 clientes a la vez. Con esto, el servidor acepta las dos formas y se pueden
migrar los clientes de uno en uno (HITOS 5 a 7).

Se retira cuando los 4 esten migrados. El contador `sin_migrar()` dice cuando
ya no hace falta: si lleva dias a cero, no queda ningun cliente antiguo.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .envelope import ClientType, Envelope, EventType, Pipeline

logger = logging.getLogger(__name__)

# `type_inference` de hoy -> pipeline normalizado.
PIPELINE_ANTIGUO: Dict[str, Pipeline] = {
    "Personal de Amazonas": Pipeline.PERSONAL_AMAZONAS,
    "Perimetrales": Pipeline.PERIMETRALES,
    "PerimetralesBoTSORT": Pipeline.PERIMETRALES_BOTSORT,
    "PerimetralesMultiCam": Pipeline.PERIMETRALES_MULTICAM,
    "VigilanteAmazonas": Pipeline.VIGILANTE_AMAZONAS,
    "Autolavado": Pipeline.AUTOLAVADO,
    "Hummus": Pipeline.HUMMUS,
    "Misters": Pipeline.MISTERS,
}

# El formato antiguo no dice que aplicacion habla, asi que se deduce del
# pipeline. Es una aproximacion aceptable SOLO durante la migracion: en cuanto
# un cliente manda envelope nuevo, `client_type` viene explicito.
CLIENTE_POR_PIPELINE: Dict[Pipeline, ClientType] = {
    Pipeline.PERSONAL_AMAZONAS: ClientType.TIENDA,
    Pipeline.PERIMETRALES: ClientType.PERIMETRALES,
    Pipeline.PERIMETRALES_BOTSORT: ClientType.PERIMETRALES,
    Pipeline.PERIMETRALES_MULTICAM: ClientType.PERIMETRALES,
    Pipeline.VIGILANTE_AMAZONAS: ClientType.PERIMETRALES,
    Pipeline.AUTOLAVADO: ClientType.AMAZONAS,
    Pipeline.HUMMUS: ClientType.AMAZONAS,
    Pipeline.MISTERS: ClientType.AMAZONAS,
}

# Renombrados de clave: antiguo -> nuevo. Vacio a proposito por ahora; los
# nombres actuales del payload se conservan tal cual para que la migracion sea
# de una sola variable (el envelope) y no de treinta.
RENOMBRES: Dict[str, str] = {}

SITE_ID_POR_DEFECTO = "sitio-unico"

_contador: Counter = Counter()


def es_formato_antiguo(mensaje: Dict[str, Any]) -> bool:
    """El formato antiguo se reconoce por `type_inference` en la raiz y la
    ausencia de `event_type`."""
    return ('event_type' not in mensaje) and ('type_inference' in mensaje)


def desde_antiguo(mensaje: Dict[str, Any],
                  site_id: str = SITE_ID_POR_DEFECTO
                  ) -> Tuple[Optional[Envelope], Optional[str]]:
    """Convierte un mensaje antiguo en Envelope.

    Devuelve `(envelope, None)` o `(None, motivo)`. No lanza: un mensaje que
    no se puede traducir se registra y se descarta, nunca tumba la conexion.
    """
    try:
        crudo = str(mensaje.get('type_inference') or '')
        pipeline = PIPELINE_ANTIGUO.get(crudo)
        if pipeline is None:
            return None, f"type_inference desconocido: {crudo!r}"

        datos = mensaje.get('data')
        if not isinstance(datos, dict):
            return None, "campo 'data' ausente o no es un objeto"

        # `camera_id` es el device_id de hoy; si falta, se cae a
        # `component_key`, que es el mismo uuid4 (ver H-11).
        device = (datos.get('camera_id') or mensaje.get('component_key') or '')
        device = str(device).strip()
        if not device:
            return None, "sin camera_id ni component_key: no hay device_id"
        # Los uuid4 traen guiones, validos para el envelope; los nombres con
        # espacios no, asi que se normalizan igual que hara el cliente nuevo.
        device = device.replace(' ', '_')[:96]

        for viejo, nuevo in RENOMBRES.items():
            if viejo in datos:
                datos[nuevo] = datos.pop(viejo)

        # Si el cliente YA declara quien es, se le cree. Deducirlo del pipeline
        # (CLIENTE_POR_PIPELINE) solo es una aproximacion para los clientes que
        # todavia no lo mandan: falla, por ejemplo, con un cliente multimodo
        # que pida `Perimetrales`. A partir del HITO 5 tienda lo declara.
        declarado = str(mensaje.get('client_type') or '').strip().lower()
        try:
            tipo = (ClientType(declarado) if declarado
                    else CLIENTE_POR_PIPELINE.get(pipeline, ClientType.TIENDA))
        except ValueError:
            tipo = CLIENTE_POR_PIPELINE.get(pipeline, ClientType.TIENDA)

        # Lo mismo con el sitio: el del cliente manda sobre el del servidor,
        # porque un servidor puede atender varias sucursales a la vez.
        sitio = str(mensaje.get('site_id') or '').strip() or site_id

        env = Envelope(
            client_type=tipo,
            site_id=sitio,
            device_id=device,
            event_type=EventType.FRAME_INFERENCE,
            event_version=1,
            timestamp_utc=datetime.now(timezone.utc),
            pipeline=pipeline,
            payload=datos,
        )
        _contador[crudo] += 1
        return env, None
    except Exception as exc:                       # nunca romper la conexion
        return None, f"error traduciendo el formato antiguo: {exc}"


def hacia_antiguo(env: Envelope, mensaje_original: Dict[str, Any]
                  ) -> Dict[str, Any]:
    """Reconstruye la respuesta en el formato que el cliente antiguo espera.

    Hoy el servidor devuelve el MISMO sobre que recibio con `data` sustituido
    (`app.py:785`), asi que basta con respetar esa forma."""
    salida = dict(mensaje_original)
    salida['data'] = env.payload
    return salida


def sin_migrar() -> Dict[str, int]:
    """Cuantos mensajes antiguos se han traducido, por `type_inference`.

    Sirve para saber cuando se puede retirar esta capa: si lleva dias vacio,
    no queda ningun cliente sin migrar."""
    return dict(_contador)
