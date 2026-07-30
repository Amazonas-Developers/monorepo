"""
Servicios de VIGILANTE-AMAZONAS: clasificador de seguridad, Re-ID, VLM y
alertas. `get_servicios()` es el punto de entrada COMPARTIDO que usan tanto
el pipeline propio (motor de detección) como el puente del modo Perimetrales
del servidor ELDE (src/analityc/core/puente_vigilante.py).

Se completa por hitos; ver nucleo_servicios.py.
"""

from vigilante_amazonas.servicios.nucleo_servicios import Servicios, get_servicios

__all__ = ["Servicios", "get_servicios"]
