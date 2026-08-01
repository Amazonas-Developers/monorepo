"""
Pruebas del heatmap persistente e historico (1-ago-2026).

Lo pedido: los mapas de calor se generan para TODAS las camaras, PERMANECEN
aunque la camara se cierre o el servidor se reinicie, y conservan un
historico por horas. Aqui se prueba la mecanica sin GPU ni pipelines:
el acumulador con estado, el registro global y la API del historico.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from fastapi import HTTPException  # noqa: E402

from src.analityc.core.analytics.config import AnalyticsConfig  # noqa: E402
from src.analityc.core.analytics.heatmap import HeatmapAccumulator  # noqa: E402
from src.analityc.core.analytics import heatmap_registro as reg  # noqa: E402
from src.app import api_lectura as api  # noqa: E402

_FRAME = np.zeros((360, 640, 3), np.uint8)


def _estampar(acc: HeatmapAccumulator, veces: int = 10) -> None:
    for _ in range(veces):
        acc.add_person((100, 100, 140, 200), 640, 360)


def test_el_acumulado_sobrevive_a_un_reinicio():
    with tempfile.TemporaryDirectory() as tmp:
        acc = HeatmapAccumulator()
        _estampar(acc, 10)
        assert acc.guardar_estado('cam-x', tmp)

        renacido = HeatmapAccumulator()
        assert renacido.cargar_estado('cam-x', tmp)
        assert renacido._samples == 10
        assert np.allclose(renacido._total, acc._total)
        # Y sigue acumulando ENCIMA, no desde cero.
        _estampar(renacido, 1)
        assert renacido._samples == 11


def test_flush_escribe_png_y_estado_sin_frame_a_mano():
    with tempfile.TemporaryDirectory() as tmp:
        acc = HeatmapAccumulator()
        _estampar(acc)
        assert acc.flush('cam-y', out_dir=tmp)
        assert (Path(tmp) / 'cam-y.png').is_file()
        assert (Path(tmp) / 'cam-y.json').is_file()
        assert (Path(tmp) / 'state' / 'cam-y.npz').is_file()
        # Sin muestras no se escribe nada (no ensuciar el disco con vacios).
        assert not HeatmapAccumulator().flush('cam-vacia', out_dir=tmp)
        assert not (Path(tmp) / 'cam-vacia.png').exists()


def test_la_hora_interrumpida_se_cierra_al_volver():
    """Camara cerrada a mitad de hora: al volver, esa hora parcial se guarda
    en el historico en vez de perderse. OJO: el sello debe caer DENTRO de la
    retencion (90 dias para .npz/.json) — con un sello del 2020 la retencion
    los borra nada mas escribirlos, que es lo correcto."""
    hace_1h = time.strftime('%Y-%m-%d_%H', time.localtime(time.time() - 3600))
    with tempfile.TemporaryDirectory() as tmp:
        acc = HeatmapAccumulator()
        _estampar(acc, 5)
        acc._hour_stamp = hace_1h              # hora anterior, quedo a medias
        acc.guardar_estado('cam-z', tmp)

        renacido = HeatmapAccumulator()
        assert renacido.cargar_estado('cam-z', tmp)
        assert renacido._hour_stamp == hace_1h
        renacido.maybe_save_snapshot('cam-z', background=_FRAME,
                                     out_dir=tmp, every_s=0.0)
        hist = Path(tmp) / 'history' / 'cam-z'
        assert (hist / f'{hace_1h}.png').is_file()
        assert (hist / f'{hace_1h}.npz').is_file()
        with open(hist / f'{hace_1h}.json', encoding='utf-8') as f:
            meta = json.load(f)
        assert meta['muestras'] == 5
        # El buffer horario quedo vaciado y en la hora vigente.
        assert renacido._hour_samples == 0
        assert renacido._hour_stamp == time.strftime('%Y-%m-%d_%H')


def test_el_registro_comparte_acumulador_y_no_cuenta_doble():
    original = AnalyticsConfig.HEATMAP_DIR
    with tempfile.TemporaryDirectory() as tmp:
        AnalyticsConfig.HEATMAP_DIR = tmp
        try:
            a1 = reg.obtener('cam-reg-1', 'Pasillo 1')
            assert reg.obtener('cam-reg-1') is a1

            n = reg.acumular_desde_metadata(
                'cam-reg-1', 'Pasillo 1', _FRAME,
                {'detections': [{'box': [10, 10, 50, 90]}],
                 'tracks': [{'bbox': [60, 10, 100, 90]}]})
            assert n == 2 and a1._samples == 2

            # Metadata con clave `heatmap` = el pipeline YA acumulo por
            # dentro (PersonAmazonas): estampar aqui seria contar doble.
            n2 = reg.acumular_desde_metadata(
                'cam-reg-1', None, _FRAME,
                {'heatmap': {'muestras': 9},
                 'detections': [{'box': [10, 10, 50, 90]}]})
            assert n2 == 0 and a1._samples == 2

            # Metadata sin cajas o roto: no pasa nada.
            assert reg.acumular_desde_metadata('cam-reg-1', None, _FRAME, {}) == 0
            assert reg.acumular_desde_metadata('cam-reg-1', None, _FRAME, None) == 0
            assert reg.acumular_desde_metadata('cam-reg-1', None, None, {}) == 0
        finally:
            AnalyticsConfig.HEATMAP_DIR = original


def test_volcar_todos_escribe_cada_camara():
    original = AnalyticsConfig.HEATMAP_DIR
    with tempfile.TemporaryDirectory() as tmp:
        AnalyticsConfig.HEATMAP_DIR = tmp
        try:
            reg.acumular_desde_metadata(
                'cam-volcado', 'Volcada', _FRAME,
                {'detections': [{'box': [10, 10, 50, 90]}]})
            assert reg.volcar_todos() >= 1
            assert (Path(tmp) / 'cam-volcado.png').is_file()
            assert (Path(tmp) / 'state' / 'cam-volcado.npz').is_file()
        finally:
            AnalyticsConfig.HEATMAP_DIR = original


def test_api_historico_lista_y_sirve_saneado():
    salida_real = api._salida
    with tempfile.TemporaryDirectory() as tmp:
        hist = Path(tmp) / 'heatmap' / 'history' / 'dev-1'
        hist.mkdir(parents=True)
        (hist / '2026-08-01_10.png').write_bytes(b'\x89PNGfake')
        (hist / '2026-08-01_10.json').write_text(
            json.dumps({'muestras': 42, 'zonas_calientes': []}),
            encoding='utf-8')
        (hist / '2026-08-01_09.png').write_bytes(b'\x89PNGfake')
        api._salida = lambda: Path(tmp)
        try:
            fuera = api.historico_de_heatmap('dev-1')
            assert fuera['total'] == 2
            assert fuera['horas'][0]['stamp'] == '2026-08-01_10'  # reciente 1o
            assert fuera['horas'][0]['muestras'] == 42
            assert fuera['horas'][1]['muestras'] is None          # sin json

            servida = api.foto_de_historico('dev-1', '2026-08-01_10')
            assert Path(servida.path).resolve().parent == hist.resolve()
            for malo in (('dev-1', '../../secreto'), ('../dev-1', 'x'),
                         ('dev-1', 'no-existe')):
                try:
                    api.foto_de_historico(*malo)
                    assert False, f'{malo} debio dar 404'
                except HTTPException as e:
                    assert e.status_code == 404
            assert api.historico_de_heatmap('sin-historia')['total'] == 0
        finally:
            api._salida = salida_real
