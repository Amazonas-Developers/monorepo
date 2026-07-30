"""
analytics - Deteccion de personas con genero y rango de edad.

Submodulos:
    demographics       - Clasificacion de genero y rango de edad
    face_reidentifier  - Identidad persistente por rostro (ArcFace)
    body_reidentifier  - 2a senal de identidad por cuerpo/vestimenta (OSNet)
    people_counter     - Conteo unico de personas
    heatmap            - Mapa de calor de ocupacion/transito
    config             - Parametros configurables
"""

from .config import AnalyticsConfig
from .demographics import DemographicsClassifier
from .face_reidentifier import FaceReidentifier
from .body_reidentifier import BodyReidentifier
from .people_counter import PeopleCounter
from .heatmap import HeatmapAccumulator
