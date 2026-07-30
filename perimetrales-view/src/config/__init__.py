"""Configuracion del cliente de PERIMETRALES.

Un solo sitio del que sale todo lo configurable. La regla 6 del refactor
prohibe hardcodear IPs, puertos y rutas; aqui se centralizan y se validan al
arrancar.
"""

from .ajustes import Ajustes, cargar

__all__ = ['Ajustes', 'cargar']
