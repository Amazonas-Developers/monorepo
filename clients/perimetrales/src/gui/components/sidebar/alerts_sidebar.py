"""
Panel lateral del cliente PERIMETRALES.

El armazon (tarjeta, columna con contador, scroll y los tiempos de llegada,
salida y permanencia) vive en `elde_core.ui.panel_alertas`. Aqui solo esta el
dominio: **dos columnas, Vehiculos y Personas, y como se decide en cual cae
cada deteccion**.

Antes este archivo tenia 481 lineas propias, duplicando un armazon que los
otros clientes tambien copiaban. Se migro al nucleo **despues** de anadirle
alli el soporte de tiempos, que era lo unico que este panel tenia de mas: sin
eso, migrarlo habria perdido el dato mas util de la vigilancia.

## Contrato de la alerta (metadata.alerts[] del servidor)

    {
      "event_type":   "llegada" | "salida" | "intrusion" | "escalamiento",
      "class_name":   "CARRO|MOTO|CAMIONETA|CAMIÓN|HOMBRE|MUJER|NIÑO|...",
      "clase_gruesa": "vehiculo" | "persona" | "objeto",
      "global_id":    "G-0012",             # identidad unica entre camaras
      "hora_llegada": epoch | iso,
      "hora_salida":  epoch | iso | null,   # null = sigue en el sitio
      "permanencia_s": float,
      "camera_name":  str,  "camera_id": str,
      "description":  str,  "timestamp": epoch | iso,
      "image_base64" / "crop_image": b64,
      "screenshot_path": str                # al pulsar, abre la carpeta
    }

Es tolerante con el formato antiguo: si no vienen los tiempos, la tarjeta
simplemente no los pinta.
"""

from PySide6.QtCore import Signal

from elde_core.ui.panel_alertas import Columna, PanelAlertas

COLUMNAS = [
    Columna('vehiculos', 'Vehículos', '🚗', '#e67e22'),
    Columna('personas',  'Personas',  '👤', '#3498db'),
]

_CLASES_PERSONA = ('HOMBRE', 'MUJER', 'NIÑO', 'NINO', 'ADULTO', 'PERSONA')
_CLASES_VEHICULO = ('CARRO', 'AUTO', 'MOTO', 'CAMIONETA', 'CAMION', 'CAMIÓN',
                    'VEHICULO', 'VEHÍCULO', 'BUS', 'TAXI')


def clasificar(alerta: dict) -> str:
    """Vehiculos o Personas. Cadena vacia = se descarta."""
    clase = str(alerta.get('class_name') or '').strip().upper()
    gruesa = str(alerta.get('clase_gruesa') or '').strip().lower()

    # «objeto» (mochilas, bolsos, maletas) no se muestra: el servidor lo agrupa
    # con personas y ensuciaba la columna con detecciones que no son ni una
    # persona ni un vehiculo.
    if clase.startswith('OBJETO'):
        return ''

    if gruesa == 'vehiculo' or clase.startswith(_CLASES_VEHICULO):
        return 'vehiculos'
    if gruesa == 'persona' or clase.startswith(_CLASES_PERSONA):
        return 'personas'

    # Sin clase reconocible: heuristica por texto y, si no, Personas. En
    # vigilancia es preferible mostrarlo de mas que perderlo.
    texto = f"{clase} {alerta.get('event_type', '')}".upper()
    if any(v in texto for v in _CLASES_VEHICULO):
        return 'vehiculos'
    return 'personas'


class AlertsSidebar(PanelAlertas):
    """Conserva el nombre, el slot `add_alert` y la senal `new_alert` porque
    `main.py` ya los usa."""

    new_alert = Signal(dict)

    def __init__(self, parent=None, title='Alertas perimetrales',
                 max_alerts=200, auto_clear_after=None):
        super().__init__(COLUMNAS, clasificar, titulo=title,
                         max_alertas=max_alerts, parent=parent)
        self.new_alert.connect(self.add_alert)
