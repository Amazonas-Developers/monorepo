"""
VIGILANTE-AMAZONAS — Sistema de videovigilancia inteligente perimetral.

Detección multiclase (persona, personal_seguridad, moto, carro, camioneta,
camion, objeto) + Re-Identificación de personas de interés (rostro ArcFace +
vestimenta OSNet/CLIP) + verificación VLM (Qwen2.5-VL) + alertas Socket.IO
hacia el cliente perimetrales-view.

Este __init__ se mantiene LIGERO a propósito: los modelos se cargan de forma
perezosa en cada módulo. El servidor ELDE (src/app/app.py) importa:

    from vigilante_amazonas.adaptador_websocket import VigilanteWS, get_vigilante_ws

y el puente del modo Perimetrales (src/analityc/core/puente_vigilante.py):

    from vigilante_amazonas.servicios import get_servicios
    from vigilante_amazonas.captura.fuente_rtsp import FrameCamara
    from vigilante_amazonas.deteccion.rastreador import DeteccionVig
"""

__version__ = "2.0.0"
