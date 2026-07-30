"""
Panel lateral del cliente GESTOR DE VENTANAS (multimodo).

El armazon vive en `elde_core.ui.panel_alertas`; aqui solo esta el dominio.

## Por que TRES columnas y no dos

A diferencia de tienda (un solo modo) o perimetrales (vigilancia), este cliente
puede correr **cualquier** pipeline: su selector ofrece `Hummus`, `HummusVLM`,
`Autolavado`, `Perimetrales`, `PerimetralesMultiCam` y `Personal de
Amazonas`. Un panel de dos columnas fijas dejaria fuera la mitad de lo que
puede ver.

Las tres columnas cubren los tres tipos de suceso que emiten esos pipelines:

- **Personas** — perimetrales y `Personal de Amazonas` (HOMBRE/MUJER/NIÑO...).
- **Vehiculos** — perimetrales y Autolavado (CARRO/MOTO/CAMIONETA/CAMIÓN).
- **Eventos** — lo que no es ni persona ni vehiculo: pedidos y entregas de
  bandeja de Hummus, fases de lavado de Autolavado, capturas guardadas.
"""

from elde_core.ui.panel_alertas import Columna, PanelAlertas

COLUMNAS = [
    Columna('personas',  'Personas',  '👤', '#3498db'),
    Columna('vehiculos', 'Vehículos', '🚗', '#e67e22'),
    Columna('eventos',   'Eventos',   '⚙️', '#9b59b6'),
]

_CLASES_PERSONA = ('HOMBRE', 'MUJER', 'NIÑO', 'NINO', 'ADULTO', 'PERSONA')
_CLASES_VEHICULO = ('CARRO', 'AUTO', 'MOTO', 'CAMIONETA', 'CAMION', 'CAMIÓN',
                    'VEHICULO', 'VEHÍCULO', 'BUS', 'TAXI')


def clasificar(alerta: dict) -> str:
    """Reparte por tipo de entidad; lo demas va a Eventos."""
    clase = str(alerta.get('class_name') or '').strip().upper()
    gruesa = str(alerta.get('clase_gruesa') or '').strip().lower()

    # "objeto" (mochilas, bolsos, maletas) lo agrupa el servidor con personas
    # y ensuciaba la columna con detecciones que no son ninguna de las dos.
    if clase.startswith('OBJETO'):
        return ''

    if gruesa == 'vehiculo' or clase.startswith(_CLASES_VEHICULO):
        return 'vehiculos'
    if gruesa == 'persona' or clase.startswith(_CLASES_PERSONA):
        return 'personas'

    texto = f"{clase} {alerta.get('event_type', '')}".upper()
    if any(v in texto for v in _CLASES_VEHICULO):
        return 'vehiculos'
    if any(p in texto for p in _CLASES_PERSONA):
        return 'personas'
    return 'eventos'


class AlertsSidebar(PanelAlertas):
    """Conserva nombre y slot `add_alert` porque `main.py` ya los usa."""

    def __init__(self, parent=None, title='Alertas', max_alerts=200,
                 auto_clear_after=None):
        super().__init__(COLUMNAS, clasificar, titulo=title,
                         max_alertas=max_alerts, parent=parent)
