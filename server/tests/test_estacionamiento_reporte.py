"""
Pruebas de la bitácora, el PDF, el envío y el planificador del
estacionamiento (portado de ARUBA_DEFINITIVO, 4-ago-2026).

Todo lo que no necesita GPU ni red se prueba aquí; el ciclo completo con el
motor real se verifica en vivo por el websocket.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / 'server'))
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from src.analityc.core import estacionamiento_placas as placas  # noqa: E402
from src.analityc.core import estacionamiento_registro as reg  # noqa: E402
from src.analityc.core import estacionamiento_whatsapp as wa  # noqa: E402
from src.analityc.core.estacionamiento_reporte import (  # noqa: E402
    calcular_metricas, construir_pdf, slug)

_CRUDAS = {
    'persona_entrada': 10, 'persona_salida': 8,
    'vehiculo_entrada': 20, 'vehiculo_permanencia': 9,
    'vehiculo_salida': 15, 'vehiculo_pernocta': 4,
    'carro_entrada': 17, 'carro_permanencia': 7, 'carro_salida': 13,
    'carro_pernocta': 3,
    'camion_entrada': 2, 'camion_permanencia': 2, 'camion_salida': 1,
    'camion_pernocta': 1,
    'moto_entrada': 1, 'moto_salida': 1,
}


# ── Bitácora CSV ─────────────────────────────────────────────────────

def _bitacora_en(tmp: str) -> reg.RegistroEstacionamiento:
    os.environ['ELDE_ESTACIONAMIENTO_DIR'] = tmp
    os.environ['ELDE_ESTACIONAMIENTO_BUFFER'] = '1'   # volcado inmediato
    return reg.RegistroEstacionamiento()


def test_la_bitacora_escribe_cabecera_y_traduce_el_evento():
    with tempfile.TemporaryDirectory() as tmp:
        bit = _bitacora_en(tmp)
        try:
            bit.anotar('llegada', 'carro', 'VIG-c1-T7', camara='c1',
                       local='Carpinteria')
            bit.anotar('estacionado', 'camion', 'VIG-c1-T8',
                       tiempo_acumulado_s=612.4, camara='c1')
            bit.anotar('pernocta', 'carro', 'VIG-c1-T7', camara='c1')
            bit.anotar('salida', 'moto', 'VIG-c1-T9', camara='c1')
            bit.volcar()
            with open(reg.ruta_del_dia(), encoding='utf-8') as f:
                filas = list(csv.DictReader(f))
            assert [f['Evento'] for f in filas] == [
                'Entrada', 'Alerta_Periodica', 'Pernocta', 'Salida']
            # Clase propia -> nomenclatura de la bitácora (la de ARUBA).
            assert [f['Clase'] for f in filas] == ['car', 'truck', 'car',
                                                   'motorcycle']
            assert filas[1]['Tiempo_Acumulado'] == '612'
            assert filas[0]['Local'] == 'Carpinteria'
        finally:
            os.environ.pop('ELDE_ESTACIONAMIENTO_DIR', None)
            os.environ.pop('ELDE_ESTACIONAMIENTO_BUFFER', None)


def test_un_evento_desconocido_no_ensucia_la_bitacora():
    with tempfile.TemporaryDirectory() as tmp:
        bit = _bitacora_en(tmp)
        try:
            bit.anotar('merodeo', 'carro', 'T1')      # no es de la bitácora
            bit.anotar('', 'carro', 'T1')
            bit.volcar()
            assert not reg.ruta_del_dia().exists()
            assert bit.anotados == 0
        finally:
            os.environ.pop('ELDE_ESTACIONAMIENTO_DIR', None)
            os.environ.pop('ELDE_ESTACIONAMIENTO_BUFFER', None)


def test_contar_periodo_agrupa_como_lo_espera_el_reporte():
    with tempfile.TemporaryDirectory() as tmp:
        bit = _bitacora_en(tmp)
        try:
            bit.anotar('llegada', 'carro', 'T1', camara='c1')
            bit.anotar('llegada', 'camioneta', 'T2', camara='c1')
            bit.anotar('estacionado', 'carro', 'T1', camara='c1')
            bit.anotar('pernocta', 'carro', 'T1', camara='c1')
            bit.anotar('salida', 'carro', 'T1', camara='c1')
            bit.anotar('llegada', 'persona', 'T3', camara='c1')
            bit.anotar('estacionado', 'persona', 'T3', camara='c1')
            bit.anotar('llegada', 'objeto', 'T4', camara='c1')
            bit.volcar()
            ahora = datetime.now()
            m = reg.contar_periodo(ahora - timedelta(hours=1),
                                   ahora + timedelta(minutes=1))
            assert m['vehiculo_entrada'] == 2        # carro + camioneta
            assert m['carro_entrada'] == 1
            assert m['camion_entrada'] == 1          # camioneta -> truck
            assert m['vehiculo_permanencia'] == 1
            assert m['vehiculo_pernocta'] == 1
            assert m['persona_entrada'] == 1
            # A las personas no se les cuenta permanencia, y 'objeto' no es
            # vehículo: ninguno de los dos infla los totales.
            assert m['persona_salida'] == 0
            assert m['vehiculo_entrada'] == 2
        finally:
            os.environ.pop('ELDE_ESTACIONAMIENTO_DIR', None)
            os.environ.pop('ELDE_ESTACIONAMIENTO_BUFFER', None)


# ── Métricas y PDF ───────────────────────────────────────────────────

def test_metricas_derivadas():
    m = calcular_metricas(_CRUDAS)
    assert m['total'] == 30                      # solo ENTRADAS (10 + 20)
    assert m['neto_v'] == 5                      # 20 - 15
    assert round(m['idx_permanencia'], 1) == 45.0
    assert round(m['idx_pernocta'], 1) == 20.0
    # 'otro' = lo que no cae en carro/camion/autobus/moto, nunca negativo.
    assert m['o_ent'] == 0
    assert calcular_metricas({'vehiculo_entrada': 1, 'carro_entrada': 5})['o_ent'] == 0


def test_el_pdf_sale_en_los_dos_formatos_y_sin_datos():
    completo, met = construir_pdf(_CRUDAS, cliente='Amazonas 365',
                                  sitio='Estacionamiento',
                                  codigo_reporte='EST-PRUEBA',
                                  ocupacion_actual=5,
                                  placas=[('ABC123', '0.9', 'hoy')])
    assert completo[:4] == b'%PDF' and len(completo) > 3000
    assert met['total'] == 30
    simple, _ = construir_pdf(_CRUDAS, formato='simple')
    assert simple[:4] == b'%PDF'
    assert len(simple) < len(completo)           # el simple no lleva gráficas
    vacio, met0 = construir_pdf({})
    assert vacio[:4] == b'%PDF' and met0['total'] == 0


def test_slug_para_el_nombre_del_archivo():
    assert slug('Carpintería Norte') == 'Carpinteria_Norte'
    assert slug('') == 'reporte'


# ── Envío ────────────────────────────────────────────────────────────

def test_guarda_el_pdf_aunque_no_se_envie():
    with tempfile.TemporaryDirectory() as tmp:
        destino = os.path.join(tmp, 'sub', 'reporte.pdf')
        r = wa.enviar_pdf(b'%PDF-falso', 'reporte', 'hola',
                          guardar_en=destino, enviar=False)
        assert r.guardado_en == destino
        assert Path(destino).read_bytes() == b'%PDF-falso'
        assert r.nombre_archivo.endswith('.pdf')     # se completa la extensión
        assert r.enviado is False and r.error is None


def test_un_fallo_de_red_no_lanza_y_deja_el_pdf():
    with tempfile.TemporaryDirectory() as tmp:
        destino = os.path.join(tmp, 'reporte.pdf')
        os.environ['ESTACIONAMIENTO_BOT_URL'] = 'https://127.0.0.1:9'
        try:
            r = wa.enviar_pdf(b'%PDF-falso', 'r.pdf', 'x',
                              guardar_en=destino, enviar=True, timeout=2)
            assert r.enviado is False
            assert r.error and 'red' in r.error
            assert Path(destino).is_file()      # el reporte NO se pierde
        finally:
            os.environ.pop('ESTACIONAMIENTO_BOT_URL', None)


# ── Placas ───────────────────────────────────────────────────────────

def test_filtro_y_normalizacion_de_placa():
    assert placas.LectorDePlacas.parece_placa('ABC123')
    assert placas.LectorDePlacas.parece_placa('AB-123-C')      # 7 útiles
    assert not placas.LectorDePlacas.parece_placa('AB12')      # muy corta
    assert not placas.LectorDePlacas.parece_placa('ABCDEFGHIJ')
    assert placas.LectorDePlacas.normalizar(' ab-123 c ') == 'AB123C'


def test_la_base_de_placas_se_queda_con_la_mejor_lectura():
    with tempfile.TemporaryDirectory() as tmp:
        bd = placas.BaseDePlacas(Path(tmp) / 'placas.db')
        bd.registrar('T1', 'ABC123', 0.71, 'c1')
        bd.registrar('T1', 'ABC128', 0.55, 'c1')      # peor: no pisa
        assert bd.de_track('T1') == ('ABC123', 0.71)
        bd.registrar('T1', 'ABC124', 0.93, 'c1')      # mejor: sí pisa
        assert bd.de_track('T1') == ('ABC124', 0.93)
        assert bd.de_track('no-existe') is None
        filas = bd.del_periodo(datetime.now() - timedelta(hours=1),
                               datetime.now() + timedelta(hours=1))
        assert filas and filas[0][0] == 'ABC124'


def test_sin_activar_no_hay_lector():
    os.environ.pop('ESTACIONAMIENTO_PLACAS', None)
    assert placas.activado() is False
    assert placas.lector() is None
