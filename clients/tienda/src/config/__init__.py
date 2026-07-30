"""
Configuracion del cliente de TIENDA.

Un solo sitio del que sale todo lo que antes estaba escrito en el codigo. La
regla 6 del plan de refactorizacion prohibe hardcodear IPs, puertos, rutas y
umbrales; hasta el HITO 5 el cliente llevaba la IP del servidor repetida en
`windows_main.py` y en `window_bar.py` como valor por defecto.

Todo se puede sobrescribir por variables de entorno o por el `.env` del
cliente, sin tocar codigo.
"""

from .ajustes import Ajustes, cargar

__all__ = ['Ajustes', 'cargar']
