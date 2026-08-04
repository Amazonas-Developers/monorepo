"""
Texto de los mensajes de WhatsApp de VIGILANTE (3-ago-2026).

Modulo PURO a proposito (sin motor, sin modelos): el formato del mensaje se
prueba sin GPU. Lo pedido por el operador contra el formato anterior:

    🚨 VIGILANTE · 🚶 PERSONA ENTRÓ AL PERÍMETRO · cámara iVMS-4200
    PERSONA ENTRÓ al área          <- redundante con el titular
    🕒 1785778995.2827132          <- epoch crudo

1. "VIGILANTE" se sustituye por el LOCAL de la camara (el select
   "Local" del recuadro, que viaja en el payload del frame); sin local,
   por el nombre de la camara.
2. La hora va legible (HH:MM:SS), no el epoch.
3. Sin redundancia: la descripcion solo aparece cuando aporta algo que el
   titular no dice (el detalle del merodeo).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


def hora_legible(ts: Any) -> str:
    """'1785778995.28' -> '18:23:15' (hora local). Tolerante: un texto ya
    formateado pasa tal cual; vacio -> la hora actual."""
    crudo = str(ts or '').strip()
    if not crudo:
        return time.strftime('%H:%M:%S')
    try:
        return time.strftime('%H:%M:%S', time.localtime(float(crudo)))
    except (ValueError, OverflowError, OSError):
        return crudo


def _duracion(segundos: Any) -> str:
    """111.6 -> '1m52s'; sin dato -> ''."""
    try:
        s = int(round(float(segundos)))
    except (TypeError, ValueError):
        return ''
    if s < 0:
        return ''
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s // 60}m{s % 60:02d}s'
    return f'{s // 3600}h{(s % 3600) // 60:02d}m'


def _titular(evento: str, clase: str, permanencia_s: Any) -> str:
    if evento == 'llegada':
        return f'{clase} entró al perímetro'
    if evento == 'salida':
        dur = _duracion(permanencia_s)
        base = f'{clase} salió del perímetro'
        return f'{base} (permaneció {dur})' if dur else base
    if evento == 'permanencia':
        dur = _duracion(permanencia_s)
        base = f'{clase} permanece en el área'
        return f'{base} ({dur})' if dur else base
    if evento == 'estacionado':
        dur = _duracion(permanencia_s)
        base = f'{clase} estacionado'
        return f'{base} ({dur})' if dur else base
    if evento == 'merodeo':
        return f'MERODEO: {clase}'
    if evento in ('alerta', 'intrusion', 'intrusión'):
        return f'persona de interés: {clase}'
    return f'{evento}: {clase}' if evento else clase


def texto_alerta(tarjeta: Dict[str, Any], local: Optional[str] = '') -> str:
    """El mensaje completo para WhatsApp a partir de la tarjeta de alerta."""
    clase = str(tarjeta.get('class_name') or 'detección').strip()
    cam = str(tarjeta.get('camera_name') or '').strip()
    gruesa = str(tarjeta.get('clase_gruesa') or '').strip().lower()
    evento = str(tarjeta.get('event_type') or '').strip().lower()
    icono = '🚗' if gruesa == 'vehiculo' else '🚶'
    encabezado = (local or '').strip() or cam or 'Alerta'

    lineas = [f'🚨 {encabezado} · {icono} '
              f'{_titular(evento, clase, tarjeta.get("permanencia_s"))}']
    # La camara en linea propia SOLO cuando el encabezado es el local (si no,
    # ya es el encabezado y repetirla seria la redundancia de antes).
    if cam and (local or '').strip():
        lineas.append(f'📹 {cam}')
    # El detalle del merodeo aporta lo que el titular no dice («entró 4 veces
    # al área en 8 min»); el resto de descripciones repiten el titular.
    if evento == 'merodeo':
        desc = str(tarjeta.get('description') or '').strip()
        if desc:
            lineas.append(desc)
    lineas.append(f'🕒 {hora_legible(tarjeta.get("timestamp"))}')
    return '\n'.join(lineas)
