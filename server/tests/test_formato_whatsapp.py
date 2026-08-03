"""
Pruebas del formato de mensajes de WhatsApp (3-ago-2026).

Lo pedido por Richard contra el formato anterior: el encabezado es el LOCAL
de la camara (no "VIGILANTE"), la hora va legible (no el epoch crudo) y sin
texto redundante. El modulo es puro a proposito: se prueba sin GPU ni motor.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))

from vigilante_amazonas.servicios.formato_whatsapp import (  # noqa: E402
    hora_legible, texto_alerta)

_EPOCH = 1785778995.2827132
_HORA = time.strftime('%H:%M:%S', time.localtime(_EPOCH))


def _tarjeta(**extra):
    base = {
        'event_type': 'llegada', 'class_name': 'PERSONA',
        'clase_gruesa': 'persona', 'camera_name': 'iVMS-4200',
        'description': 'PERSONA ENTRÓ al área',
        'timestamp': str(_EPOCH), 'global_id': 'VIG-1',
    }
    base.update(extra)
    return base


def test_encabezado_es_el_local_y_sin_redundancia():
    texto = texto_alerta(_tarjeta(), local='Carpinteria')
    lineas = texto.split('\n')
    assert lineas[0] == '🚨 Carpinteria · 🚶 PERSONA entró al perímetro'
    assert lineas[1] == '📹 iVMS-4200'
    assert lineas[2] == f'🕒 {_HORA}'
    assert len(lineas) == 3
    assert 'VIGILANTE' not in texto
    # La descripcion "PERSONA ENTRÓ al área" era redundante: fuera.
    assert 'al área' not in texto
    # El epoch crudo tampoco puede aparecer.
    assert str(_EPOCH) not in texto


def test_sin_local_encabeza_la_camara_sin_duplicarla():
    texto = texto_alerta(_tarjeta(), local='')
    lineas = texto.split('\n')
    assert lineas[0] == '🚨 iVMS-4200 · 🚶 PERSONA entró al perímetro'
    # La camara YA es el encabezado: no va repetida en linea propia.
    assert len(lineas) == 2
    assert lineas[1] == f'🕒 {_HORA}'


def test_vehiculo_salida_lleva_permanencia():
    texto = texto_alerta(_tarjeta(
        event_type='salida', class_name='CARRO', clase_gruesa='vehiculo',
        permanencia_s=111.6), local='Bodega Central')
    assert '🚗' in texto
    assert 'CARRO salió del perímetro (permaneció 1m52s)' in texto


def test_merodeo_conserva_su_detalle():
    texto = texto_alerta(_tarjeta(
        event_type='merodeo', class_name='CAMIÓN', clase_gruesa='vehiculo',
        description='MERODEO: CAMIÓN entró 4 veces al área en 8 min'),
        local='Carpinteria')
    assert 'MERODEO: CAMIÓN' in texto.split('\n')[0]
    # El detalle del merodeo SI aporta (veces y ventana): se conserva.
    assert 'entró 4 veces al área en 8 min' in texto


def test_hora_legible_tolerante():
    assert hora_legible(str(_EPOCH)) == _HORA
    assert hora_legible(_EPOCH) == _HORA
    assert hora_legible('18:00:00') == '18:00:00'   # ya formateada: pasa
    assert re.fullmatch(r'\d{2}:\d{2}:\d{2}', hora_legible(''))
    assert re.fullmatch(r'\d{2}:\d{2}:\d{2}', hora_legible(None))
