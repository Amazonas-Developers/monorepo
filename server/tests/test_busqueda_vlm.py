"""
Pruebas de la búsqueda VLM (FASE 6 de los dashboards de producto).

Se prueba TODO lo que no necesita GPU: la extracción/traducción del término
('búscame el carro rojo' -> 'red car'), el sí/no del VQA, el interruptor, el
listado de fotos por ámbito y los rechazos (apagado, ocupado, sin fotos). El
camino feliz con modelos de verdad se verifica en vivo con curl, que es donde
se ve si YOLO-World encuentra el carro.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from fastapi import HTTPException  # noqa: E402

from src.app import busqueda_vlm as vlm  # noqa: E402


def test_buscame_el_carro_rojo_se_vuelve_red_car():
    # El acento de «búscame» rompía el extractor del router; este no.
    assert vlm.termino_de_busqueda('búscame el carro rojo') == 'red car'
    assert vlm.termino_de_busqueda('buscame el carro rojo') == 'red car'
    assert vlm.termino_de_busqueda('detecta las motos') == 'motorcycles'
    assert vlm.termino_de_busqueda('encuentra una persona con mochila') == \
        'person backpack'
    assert vlm.termino_de_busqueda('cuantas personas hay') == 'people'
    # Colores delante (CLIP es adjetivo-nombre) aunque vengan detrás.
    assert vlm.termino_de_busqueda('localiza camionetas blancas') == \
        'white pickup trucks'


def test_pregunta_abierta_no_es_deteccion():
    for abierta in ('¿qué ambiente se ve?', 'describe la escena',
                    '¿está lloviendo?'):
        assert vlm.termino_de_busqueda(abierta) is None, abierta
    # Palabra de detección pero sin objeto: mejor VQA que detectar "nada".
    assert vlm.termino_de_busqueda('busca') is None


def test_el_si_del_vqa_tolera_acentos_y_signos():
    for si in ('Sí, hay un carro rojo.', 'SI', '  "Sí": se ve claramente',
               '¡Sí! al fondo'):
        assert vlm.es_afirmativa(si), si
    for no in ('No, no se ve.', 'Sin duda no hay nada', 'Siendo estrictos no',
               '', 'NO'):
        assert not vlm.es_afirmativa(no), no


def test_interruptor_apagado_por_defecto_y_persistente():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['ELDE_VLM_BUSCADOR_ARCHIVO'] = str(Path(tmp) / 'sw.txt')
        try:
            assert vlm.buscador_activo() is False      # sin archivo: apagado
            vlm._fijar_buscador(True)
            assert vlm.buscador_activo() is True
            vlm._fijar_buscador(False)
            assert vlm.buscador_activo() is False
        finally:
            os.environ.pop('ELDE_VLM_BUSCADOR_ARCHIVO', None)


def test_fotos_de_alertas_y_ambito_invalido():
    from src.app import api_lectura as api
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        (carpeta / '20260731_120000_000_a.jpg').write_bytes(b'\xff\xd8x')
        (carpeta / '20260731_120000_000_a.json').write_text(
            '{"clase": "CARRO", "evento": "llegada", "camara": "c1", '
            '"epoch": 1.0, "timestamp": "t"}', encoding='utf-8')
        os.environ['VIGILANTE_SCREENSHOTS'] = str(carpeta)
        api._CACHE_ALERTAS['marca'] = 0.0
        try:
            fotos = vlm._fotos('alertas', 10)
            assert len(fotos) == 1
            assert fotos[0]['url'].startswith('/api/v1/alertas/foto/')
            assert Path(fotos[0]['ruta']).is_file()
            assert 'CARRO' in fotos[0]['nota']
            try:
                vlm._fotos('otra_cosa', 10)
                assert False, 'un ámbito inválido debe dar 422'
            except HTTPException as e:
                assert e.status_code == 422
        finally:
            os.environ.pop('VIGILANTE_SCREENSHOTS', None)
            api._CACHE_ALERTAS['marca'] = 0.0


def test_apagado_ocupado_y_sin_fotos_se_rechazan():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['ELDE_VLM_BUSCADOR_ARCHIVO'] = str(Path(tmp) / 'sw.txt')
        os.environ['VIGILANTE_SCREENSHOTS'] = str(Path(tmp) / 'vacia')
        from src.app import api_lectura as api
        api._CACHE_ALERTAS['marca'] = 0.0
        try:
            # Apagado -> 409 con instrucción clara.
            try:
                vlm.crear_busqueda(consulta='busca el carro', ambito='alertas',
                                   limite=None)
                assert False
            except HTTPException as e:
                assert e.status_code == 409 and 'apagado' in e.detail

            vlm._fijar_buscador(True)
            # Otra búsqueda corriendo -> 409 (la GPU es una).
            vlm._TRABAJOS['ocupada'] = {'estado': 'corriendo',
                                        'creado': time.time()}
            try:
                vlm.crear_busqueda(consulta='busca el carro', ambito='alertas',
                                   limite=None)
                assert False
            except HTTPException as e:
                assert e.status_code == 409 and 'en curso' in e.detail
            finally:
                vlm._TRABAJOS.pop('ocupada', None)

            # Sin fotos en el ámbito -> 404, no un hilo buscando en nada.
            try:
                vlm.crear_busqueda(consulta='busca el carro', ambito='alertas',
                                   limite=None)
                assert False
            except HTTPException as e:
                assert e.status_code == 404
        finally:
            os.environ.pop('ELDE_VLM_BUSCADOR_ARCHIVO', None)
            os.environ.pop('VIGILANTE_SCREENSHOTS', None)
            api._CACHE_ALERTAS['marca'] = 0.0


def test_ver_busqueda_sanea_el_id_y_oculta_rutas():
    vlm._TRABAJOS['abc123'] = {
        'id': 'abc123', 'estado': 'terminado', 'creado': time.time(),
        'hechas': 1, 'total': 1, 'consulta': 'x', 'modo': 'deteccion',
        'termino': 'car', 'error': None, 'duracion_s': 1.0,
        'resultados': [{'archivo': 'a.jpg', 'url': '/x/a.jpg',
                        'ruta': 'C:/secreta/a.jpg', 'detalle': 'd'}]}
    try:
        fuera = vlm.ver_busqueda('abc123')
        assert fuera['resultados'][0]['url'] == '/x/a.jpg'
        assert 'ruta' not in fuera['resultados'][0]
        try:
            vlm.ver_busqueda('../../abc123')
            # El saneado deja 'abc123' (solo hex), así que ESTO debe existir.
        except HTTPException:
            assert False, 'el saneado debía dejar el id hex limpio'
        try:
            vlm.ver_busqueda('zzz')
            assert False
        except HTTPException as e:
            assert e.status_code == 404
    finally:
        vlm._TRABAJOS.pop('abc123', None)
