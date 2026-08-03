"""
Pruebas de /api/v1/alertas, /alertas/foto y /ranking-pasillos (FASE 1 de los
dashboards de producto).

Sin pytest: funciones `test_*` con asserts planos, cada una monta su propia
carpeta temporal de alertas via `VIGILANTE_SCREENSHOTS` (la misma variable que
usa el panel :5333, una sola fuente de verdad) y resetea el cache del indice.

Las funciones del router se llaman DIRECTO, no via TestClient, asi que hay que
pasar todos los parametros: los defaults son objetos `Query` de FastAPI y
serian verdaderos en los `if`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from fastapi import HTTPException  # noqa: E402

from src.app import api_lectura as api  # noqa: E402

_DEFAULTS = dict(evento=None, clase=None, clase_gruesa=None, camara=None,
                 establecimiento=None, desde=None, hasta=None, q=None,
                 limite=60, offset=0)


def _buscar(**kw):
    return api.listar_alertas(**{**_DEFAULTS, **kw})


def _usar_carpeta(carpeta: Path) -> None:
    os.environ['VIGILANTE_SCREENSHOTS'] = str(carpeta)
    api._CACHE_ALERTAS['marca'] = 0.0


def _soltar_carpeta() -> None:
    os.environ.pop('VIGILANTE_SCREENSHOTS', None)
    api._CACHE_ALERTAS['marca'] = 0.0


def _alerta(carpeta: Path, nombre: str, **meta) -> None:
    """Un .jpg minimo con su sidecar. `meta=None` deja el jpg huerfano."""
    (carpeta / nombre).write_bytes(b'\xff\xd8fake-jpeg')
    if meta.pop('_sin_sidecar', False):
        return
    (carpeta / nombre).with_suffix('.json').write_text(
        json.dumps(meta, ensure_ascii=False), encoding='utf-8')


def test_lista_ordena_y_construye_url():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260731_100000_000_llegada_CARRO_cam.jpg',
                clase='CARRO', clase_gruesa='vehiculo', evento='llegada',
                camara='iVMS-4200', epoch=1785500000.0, timestamp='t1',
                global_id='VIG-1', descripcion='CARRO LLEGÓ')
        _alerta(carpeta, '20260731_110000_000_salida_MOTO_cam.jpg',
                clase='MOTO', clase_gruesa='vehiculo', evento='salida',
                camara='SmartPSS', epoch=1785503600.0, timestamp='t2',
                global_id='VIG-2', descripcion='MOTO SALIÓ')
        _usar_carpeta(carpeta)
        try:
            fuera = _buscar()
            assert fuera['total'] == 2
            # Mas reciente primero (orden lexicografico inverso del nombre).
            assert fuera['alertas'][0]['clase'] == 'MOTO'
            assert fuera['alertas'][1]['url'] == (
                '/api/v1/alertas/foto/'
                '20260731_100000_000_llegada_CARRO_cam.jpg')
            assert fuera['facetas']['por_clase'] == {'CARRO': 1, 'MOTO': 1}
            assert fuera['facetas']['por_clase_gruesa'] == {'vehiculo': 2}
        finally:
            _soltar_carpeta()


def test_filtros_ignoran_mayusculas_y_acentos():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260731_100000_000_a.jpg', clase='CAMIÓN',
                clase_gruesa='vehiculo', evento='llegada', camara='c1',
                epoch=1.0, descripcion='x')
        _alerta(carpeta, '20260731_110000_000_b.jpg', clase='PERSONA',
                clase_gruesa='persona', evento='merodeo', camara='c2',
                epoch=2.0, descripcion='y')
        _usar_carpeta(carpeta)
        try:
            # 'camion' (sin acento, minusculas) debe encontrar 'CAMIÓN'.
            assert _buscar(clase='camion')['total'] == 1
            assert _buscar(evento='MERODEO')['total'] == 1
            assert _buscar(clase_gruesa='persona')['total'] == 1
            assert _buscar(camara='C1')['total'] == 1
            assert _buscar(clase='bicicleta')['total'] == 0
        finally:
            _soltar_carpeta()


def test_fechas_cubren_el_dia_entero():
    hoy_10 = time.mktime((2026, 7, 30, 10, 0, 0, 0, 0, -1))
    hoy_23 = time.mktime((2026, 7, 30, 23, 30, 0, 0, 0, -1))
    ayer = time.mktime((2026, 7, 29, 9, 0, 0, 0, 0, -1))
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260730_100000_000_a.jpg', clase='A', epoch=hoy_10)
        _alerta(carpeta, '20260730_233000_000_b.jpg', clase='B', epoch=hoy_23)
        _alerta(carpeta, '20260729_090000_000_c.jpg', clase='C', epoch=ayer)
        _usar_carpeta(carpeta)
        try:
            # desde=hasta=el mismo dia -> el dia ENTERO (las 23:30 entran).
            dia = _buscar(desde='2026-07-30', hasta='2026-07-30')
            assert dia['total'] == 2, dia['total']
            assert _buscar(desde='2026-07-30 12:00')['total'] == 1
            assert _buscar(hasta='2026-07-29')['total'] == 1
            try:
                _buscar(desde='31/07/2026')
                assert False, 'una fecha invalida debe dar 422'
            except HTTPException as e:
                assert e.status_code == 422
        finally:
            _soltar_carpeta()


def test_texto_libre_busca_en_descripcion():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260730_100000_000_a.jpg', clase='CARRO',
                epoch=1.0, descripcion='CARRO SALIÓ del área',
                global_id='VIG-cam-T7')
        _alerta(carpeta, '20260730_110000_000_b.jpg', clase='PERSONA',
                epoch=2.0, descripcion='PERSONA merodeando')
        _usar_carpeta(carpeta)
        try:
            assert _buscar(q='salio del area')['total'] == 1   # sin acentos
            assert _buscar(q='vig-cam-t7')['total'] == 1       # global_id
            assert _buscar(q='inexistente')['total'] == 0
        finally:
            _soltar_carpeta()


def test_paginacion_no_altera_el_total():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        for i in range(5):
            _alerta(carpeta, f'20260730_10000{i}_000_a{i}.jpg',
                    clase='CARRO', epoch=float(i))
        _usar_carpeta(carpeta)
        try:
            pagina = _buscar(limite=2, offset=2)
            assert pagina['total'] == 5
            assert len(pagina['alertas']) == 2
            resto = _buscar(limite=2, offset=4)
            assert len(resto['alertas']) == 1
        finally:
            _soltar_carpeta()


def test_jpg_sin_sidecar_se_lista_pero_marcado():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260730_100000_000_huerfana.jpg', _sin_sidecar=True)
        _alerta(carpeta, '20260730_110000_000_b.jpg', clase='CARRO',
                epoch=time.time())
        _usar_carpeta(carpeta)
        try:
            fuera = _buscar()
            assert fuera['total'] == 2
            huerfana = fuera['alertas'][1]
            assert huerfana['sin_metadatos'] is True
            # Sin epoch no puede pasar un filtro de fechas: mejor fuera que
            # colada en un dia que no le consta.
            assert _buscar(desde='2000-01-01')['total'] == 1
        finally:
            _soltar_carpeta()


def test_la_foto_no_permite_salirse_de_la_carpeta():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260730_100000_000_a.jpg', clase='CARRO', epoch=1.0)
        (carpeta.parent / 'fuera.jpg').write_bytes(b'secreto')
        _usar_carpeta(carpeta)
        try:
            servida = api.foto_de_alerta('20260730_100000_000_a.jpg')
            # resolve(): en Windows el tempdir llega en formato corto (8.3).
            assert Path(servida.path).resolve().parent == carpeta.resolve()
            for malicioso in ('../fuera.jpg', '..\\fuera.jpg', 'no_existe.jpg',
                              'a.txt', ''):
                try:
                    api.foto_de_alerta(malicioso)
                    assert False, f'{malicioso!r} debio dar 404'
                except HTTPException as e:
                    assert e.status_code == 404
        finally:
            _soltar_carpeta()
            (carpeta.parent / 'fuera.jpg').unlink(missing_ok=True)


def test_el_cache_ve_archivos_nuevos_al_vencer():
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        _alerta(carpeta, '20260730_100000_000_a.jpg', clase='CARRO', epoch=1.0)
        _usar_carpeta(carpeta)
        try:
            assert _buscar()['total'] == 1
            _alerta(carpeta, '20260730_110000_000_b.jpg', clase='MOTO',
                    epoch=2.0)
            # Dentro del TTL el indice NO cambia (es la gracia del cache)...
            assert _buscar()['total'] == 1
            # ...y al vencer, si.
            api._CACHE_ALERTAS['marca'] = 0.0
            assert _buscar()['total'] == 2
        finally:
            _soltar_carpeta()


def test_ranking_ordena_y_deja_sin_datos_al_final():
    reales = (api._registro.dispositivos, api._informes, api._hay_heatmap)
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        for ident, entradas in (('cam-a', 5), ('cam-b', 40)):
            (carpeta / f'{ident}.json').write_text(json.dumps({
                'total_entradas': entradas, 'visitantes_unicos': entradas,
                'permanencia_media_s': 12.5}), encoding='utf-8')

        def _dispositivos(site_id=None, client_type=None):
            return [{'device_id': d, 'camera_name': d, 'site_id': 's',
                     'client_type': client_type or 'tienda', 'frames': 10}
                    for d in ('cam-a', 'cam-b', 'cam-sin-datos')]

        api._registro.dispositivos = _dispositivos
        api._informes = lambda ident: (
            [str(carpeta / f'{ident}.json')]
            if (carpeta / f'{ident}.json').is_file() else [])
        api._hay_heatmap = lambda ident: False
        try:
            fuera = api.ranking_pasillos(site_id=None, client_type='tienda')
            assert fuera['total'] == 3
            assert fuera['mas_concurrido']['device_id'] == 'cam-b'
            assert fuera['menos_concurrido']['device_id'] == 'cam-a'
            assert fuera['ranking'][-1]['device_id'] == 'cam-sin-datos'
            assert fuera['ranking'][-1]['entradas'] is None
        finally:
            (api._registro.dispositivos, api._informes,
             api._hay_heatmap) = reales


def test_con_una_sola_camara_no_hay_menos_concurrido():
    reales = (api._registro.dispositivos, api._informes, api._hay_heatmap)
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        (carpeta / 'unica.json').write_text(
            json.dumps({'total_entradas': 7}), encoding='utf-8')
        api._registro.dispositivos = lambda site_id=None, client_type=None: [
            {'device_id': 'unica', 'camera_name': 'unica', 'site_id': 's',
             'client_type': 'tienda', 'frames': 1}]
        api._informes = lambda ident: [str(carpeta / 'unica.json')]
        api._hay_heatmap = lambda ident: False
        try:
            fuera = api.ranking_pasillos(site_id=None, client_type=None)
            assert fuera['mas_concurrido']['device_id'] == 'unica'
            assert fuera['menos_concurrido'] is None
        finally:
            (api._registro.dispositivos, api._informes,
             api._hay_heatmap) = reales
