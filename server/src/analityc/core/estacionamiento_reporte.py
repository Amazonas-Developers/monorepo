"""
src/analityc/core/estacionamiento_reporte.py — el PDF del estacionamiento.

Portado de ARUBA_DEFINITIVO (`amazonas365_reporte.py` + `reporte_simple.py`):
el Reporte Analítico IA de Amazonas 365, 100 % ReportLab (sin navegador). Dos
formatos, como allá:

  * `completo`  — portada con logo, KPIs, gráfica de pastel (personas vs
    vehículos), barras por tipo de vehículo, tablas, foto de la cámara y
    observaciones/conclusiones. En ARUBA salía los lunes.
  * `simple`    — dos tablas (resumen global y desglose por tipo). El resto
    de los días.

Las métricas derivadas (índice de permanencia, correspondencia entrada/salida,
neto, participación…) son las mismas de `calcular_metricas` de ARUBA.

El PDF se devuelve en bytes; quién lo manda es `estacionamiento_whatsapp.py`.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, Image,
                                KeepTogether, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

logger = logging.getLogger(__name__)

# ── Identidad visual (la de Amazonas 365, igual que ARUBA) ───────────────
VERDE_MARCA = colors.HexColor('#327b32')
LIMA_MARCA = colors.HexColor('#b8ce30')
SERIE_1 = colors.HexColor('#2a78d6')      # azul   — personas / entradas
SERIE_2 = colors.HexColor('#eb6834')      # naranja— vehículos / permanencia
SERIE_3 = colors.HexColor('#1baf7a')      # aqua   — salidas
SERIE_4 = colors.HexColor('#8e5bd0')      # morado — pernocta
TINTA = colors.HexColor('#0b0b0b')
TINTA_2 = colors.HexColor('#52514e')
TINTA_MUTE = colors.HexColor('#6f6e69')
LINEA = colors.HexColor('#d9d8d1')
PLANO = colors.HexColor('#f4f5f1')
BORDE = colors.HexColor('#c9c8c1')

FUENTE = 'Helvetica'
FUENTE_BOLD = 'Helvetica-Bold'

_DATA_URI = re.compile(r'^data:(?P<mime>[\w.+/-]+);base64,', re.IGNORECASE)


# ── Utilidades de formato ────────────────────────────────────────────────

def b64_a_bytes(valor: str) -> bytes:
    """Acepta base64 puro o data-URI. Tolera el relleno faltante."""
    if not valor:
        raise ValueError('la imagen en base64 está vacía')
    limpio = re.sub(r'\s+', '', _DATA_URI.sub('', valor.strip()))
    limpio += '=' * ((-len(limpio)) % 4)
    try:
        return base64.b64decode(limpio, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f'imagen base64 inválida: {exc}') from exc


def _num(valor: Union[int, float]) -> str:
    return f'{int(round(valor)):,}'.replace(',', '.')


def _pct(valor: float, decimales: int = 1) -> str:
    if valor != valor or valor in (float('inf'), float('-inf')):
        valor = 0.0
    return f'{valor:.{decimales}f}'.replace('.', ',') + ' %'


def _dec(valor: float, decimales: int = 2) -> str:
    if valor != valor or valor in (float('inf'), float('-inf')):
        valor = 0.0
    return f'{valor:.{decimales}f}'.replace('.', ',')


def _fecha(valor: Optional[Union[str, date, datetime]]) -> str:
    if not valor:
        return '—'
    if isinstance(valor, datetime):
        valor = valor.date()
    if isinstance(valor, date):
        return valor.strftime('%d/%m/%Y')
    texto = str(valor).strip()
    for patron in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(texto, patron).strftime('%d/%m/%Y')
        except ValueError:
            continue
    return texto


def _xml(texto: Any) -> str:
    return (str(texto or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


def slug(texto: str, largo: int = 40) -> str:
    base = unicodedata.normalize('NFKD', texto or '').encode('ascii',
                                                             'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_')[:largo] or 'reporte'


# ── Métricas derivadas (calcular_metricas de ARUBA) ──────────────────────

def calcular_metricas(m: Dict[str, int]) -> Dict[str, Union[int, float]]:
    """De los conteos crudos de la bitácora a los indicadores del reporte."""
    g = lambda k: int(m.get(k, 0) or 0)          # noqa: E731
    d: Dict[str, Union[int, float]] = {
        'p_ent': g('persona_entrada'), 'p_sal': g('persona_salida'),
        'v_ent': g('vehiculo_entrada'), 'v_per': g('vehiculo_permanencia'),
        'v_sal': g('vehiculo_salida'), 'v_pernocta': g('vehiculo_pernocta'),
        'c_ent': g('carro_entrada'), 'c_per': g('carro_permanencia'),
        'c_sal': g('carro_salida'), 'c_pernocta': g('carro_pernocta'),
        'k_ent': g('camion_entrada'), 'k_per': g('camion_permanencia'),
        'k_sal': g('camion_salida'), 'k_pernocta': g('camion_pernocta'),
        'a_ent': g('autobus_entrada'), 'a_per': g('autobus_permanencia'),
        'a_sal': g('autobus_salida'), 'a_pernocta': g('autobus_pernocta'),
        'mo_ent': g('moto_entrada'), 'mo_per': g('moto_permanencia'),
        'mo_sal': g('moto_salida'), 'mo_pernocta': g('moto_pernocta'),
    }
    # Vehículos que no entran en el desglose por tipo.
    for suf in ('ent', 'per', 'sal', 'pernocta'):
        d[f'o_{suf}'] = max(0, d[f'v_{suf}'] - d[f'c_{suf}'] - d[f'k_{suf}']
                            - d[f'a_{suf}'] - d[f'mo_{suf}'])
    # Solo las ENTRADAS cuentan como detecciones (no inflar los números).
    d['p_tot'], d['v_tot'] = d['p_ent'], d['v_ent']
    d['total'] = d['p_tot'] + d['v_tot']
    d['neto_p'] = d['p_ent'] - d['p_sal']
    d['neto_v'] = d['v_ent'] - d['v_sal']
    d['idx_permanencia'] = (d['v_per'] / d['v_ent'] * 100) if d['v_ent'] else 0.0
    d['idx_pernocta'] = (d['v_pernocta'] / d['v_ent'] * 100) if d['v_ent'] else 0.0
    d['corr_p'] = (d['p_sal'] / d['p_ent'] * 100) if d['p_ent'] else 0.0
    d['corr_v'] = (d['v_sal'] / d['v_ent'] * 100) if d['v_ent'] else 0.0
    d['ratio_vp'] = (d['v_ent'] / d['p_ent']) if d['p_ent'] else 0.0
    d['share_carga'] = (d['k_ent'] / d['v_ent'] * 100) if d['v_ent'] else 0.0
    d['part_personas'] = (d['p_tot'] / d['total'] * 100) if d['total'] else 0.0
    d['part_vehiculos'] = (d['v_tot'] / d['total'] * 100) if d['total'] else 0.0
    return d


# ── Estilos ──────────────────────────────────────────────────────────────

def _estilos() -> Dict[str, ParagraphStyle]:
    base = ParagraphStyle('base', fontName=FUENTE, fontSize=9.5, leading=13,
                          textColor=TINTA)
    def hijo(nombre: str, **kw: Any) -> ParagraphStyle:
        return ParagraphStyle(nombre, parent=base, **kw)
    return {
        'titulo': hijo('titulo', fontName=FUENTE_BOLD, fontSize=20, leading=23),
        'subtitulo': hijo('subtitulo', fontSize=10, textColor=TINTA_2),
        'codigo': hijo('codigo', fontName=FUENTE_BOLD, fontSize=9.5,
                       alignment=TA_RIGHT),
        'emitido': hijo('emitido', fontSize=8.5, textColor=TINTA_2,
                        alignment=TA_RIGHT),
        'seccion': hijo('seccion', fontName=FUENTE_BOLD, fontSize=8.5,
                        textColor=VERDE_MARCA, spaceAfter=4),
        'meta_dt': hijo('meta_dt', fontSize=6.6, textColor=TINTA_MUTE, leading=9),
        'meta_dd': hijo('meta_dd', fontName=FUENTE_BOLD, fontSize=9, leading=11.5),
        'hero_num': hijo('hero_num', fontName=FUENTE_BOLD, fontSize=34, leading=36),
        'hero_txt': hijo('hero_txt', fontSize=9.5, textColor=TINTA_2),
        'kpi_lbl': hijo('kpi_lbl', fontSize=7.4, textColor=TINTA_MUTE, leading=9.5),
        'kpi_val': hijo('kpi_val', fontName=FUENTE_BOLD, fontSize=15, leading=18),
        'kpi_sub': hijo('kpi_sub', fontSize=7.2, textColor=TINTA_2, leading=9),
        'chart_t': hijo('chart_t', fontName=FUENTE_BOLD, fontSize=9.5, leading=12),
        'th': hijo('th', fontName=FUENTE_BOLD, fontSize=7.6,
                   textColor=colors.white, leading=10),
        'th_r': hijo('th_r', fontName=FUENTE_BOLD, fontSize=7.6,
                     textColor=colors.white, leading=10, alignment=TA_RIGHT),
        'td': hijo('td', fontSize=8.4, leading=11),
        'td_r': hijo('td_r', fontSize=8.4, leading=11, alignment=TA_RIGHT),
        'td_b': hijo('td_b', fontName=FUENTE_BOLD, fontSize=8.4, leading=11),
        'td_br': hijo('td_br', fontName=FUENTE_BOLD, fontSize=8.4, leading=11,
                      alignment=TA_RIGHT),
        'caption': hijo('caption', fontSize=8, textColor=TINTA_MUTE, leading=10),
        'obs': hijo('obs', fontSize=8.6, leading=12),
        'obs_vacia': hijo('obs_vacia', fontSize=8.6, leading=12,
                          textColor=TINTA_MUTE),
    }


# ── Piezas gráficas ──────────────────────────────────────────────────────

def _grafica_pastel(datos: Sequence[Tuple[str, int, colors.Color]],
                    ancho: float, alto: float = 118) -> Drawing:
    visibles = [(e, v, c) for e, v, c in datos if v > 0]
    total = sum(v for _, v, _ in visibles)
    d = Drawing(ancho, alto)
    if not visibles or total <= 0:
        d.add(String(ancho / 2, alto / 2, 'Sin datos en el período',
                     fontName=FUENTE, fontSize=8, fillColor=TINTA_MUTE,
                     textAnchor='middle'))
        return d
    pastel = Pie()
    pastel.x, pastel.y = 6, 8
    pastel.width = pastel.height = min(alto - 16, 100)
    pastel.data = [v for _, v, _ in visibles]
    pastel.labels = [f'{v / total * 100:.0f}%' if v / total >= 0.05 else ''
                     for _, v, _ in visibles]
    pastel.slices.strokeColor = colors.white
    pastel.slices.strokeWidth = 1.2
    pastel.slices.fontName = FUENTE_BOLD
    pastel.slices.fontSize = 7.5
    pastel.slices.labelRadius = 0.62
    pastel.slices.fontColor = colors.white
    for i, (_, _, color) in enumerate(visibles):
        pastel.slices[i].fillColor = color
    d.add(pastel)
    leyenda = Legend()
    leyenda.x = pastel.width + 18
    leyenda.y = alto - 22
    leyenda.alignment = 'right'
    leyenda.fontName = FUENTE
    leyenda.fontSize = 7.4
    leyenda.dxTextSpace = 5
    leyenda.deltay = 11
    leyenda.columnMaximum = 6
    leyenda.colorNamePairs = [(c, f'{e}  {_num(v)}') for e, v, c in visibles]
    d.add(leyenda)
    return d


def _escala_limpia(maximo: float, pasos: int = 4) -> float:
    if maximo <= 0:
        return 1.0
    crudo = maximo / pasos
    magnitud = 10 ** int(f'{crudo:e}'.split('e')[1])
    for mult in (1, 2, 2.5, 5, 10):
        if magnitud * mult >= crudo:
            return magnitud * mult * pasos
    return magnitud * 10 * pasos


def _grafica_barras(series: Sequence[Tuple[str, colors.Color]],
                    categorias: Sequence[str], datos: Sequence[Sequence[int]],
                    ancho: float, alto: float = 132) -> Drawing:
    d = Drawing(ancho, alto)
    if not any(any(fila) for fila in datos):
        d.add(String(ancho / 2, alto / 2, 'Sin datos en el período',
                     fontName=FUENTE, fontSize=8, fillColor=TINTA_MUTE,
                     textAnchor='middle'))
        return d
    grafica = VerticalBarChart()
    grafica.x, grafica.y = 28, 26
    grafica.width = ancho - 44
    grafica.height = alto - 52
    grafica.data = [list(fila) for fila in datos]
    grafica.categoryAxis.categoryNames = list(categorias)
    grafica.categoryAxis.labels.fontName = FUENTE
    grafica.categoryAxis.labels.fontSize = 7
    grafica.categoryAxis.labels.dy = -3
    grafica.valueAxis.valueMin = 0
    tope = _escala_limpia(max(max(fila) for fila in datos))
    grafica.valueAxis.valueMax = tope
    grafica.valueAxis.valueStep = tope / 4
    grafica.valueAxis.labels.fontName = FUENTE
    grafica.valueAxis.labels.fontSize = 7
    grafica.valueAxis.gridStrokeColor = LINEA
    grafica.valueAxis.gridStrokeWidth = 0.4
    grafica.valueAxis.visibleGrid = 1
    grafica.barSpacing = 1
    grafica.groupSpacing = 12
    for i, (_, color) in enumerate(series):
        grafica.bars[i].fillColor = color
        grafica.bars[i].strokeColor = None
    d.add(grafica)
    leyenda = Legend()
    leyenda.x, leyenda.y = 26, alto - 8
    leyenda.alignment = 'right'
    leyenda.fontName = FUENTE
    leyenda.fontSize = 6.6
    leyenda.dx = leyenda.dy = 5
    leyenda.dxTextSpace = 3
    # El paso se REPARTE el ancho disponible: con 78 fijos la 4a serie
    # ("Salida") se salía del borde del dibujo.
    leyenda.deltax = max(46.0, (ancho - 34) / max(1, len(series)))
    leyenda.columnMaximum = 1
    leyenda.colorNamePairs = [(c, e) for e, c in series]
    d.add(leyenda)
    return d


def _tarjeta(contenido: List[Any], ancho: float,
             relleno: colors.Color = colors.white) -> Table:
    t = Table([[contenido]], colWidths=[ancho])
    t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, BORDE),
        ('ROUNDEDCORNERS', [5, 5, 5, 5]),
        ('BACKGROUND', (0, 0), (-1, -1), relleno),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return t


def _seccion(titulo: str, numero: int, st: Dict[str, ParagraphStyle],
             ancho: float) -> List[Any]:
    return [
        Paragraph(f'{numero:02d} · {_xml(titulo).upper()}', st['seccion']),
        HRFlowable(width=ancho, thickness=1.1, color=LIMA_MARCA,
                   spaceBefore=0, spaceAfter=7),
    ]


def _tabla_datos(encabezados: Sequence[str], filas: Sequence[Sequence[str]],
                 anchos: Sequence[float], st: Dict[str, ParagraphStyle],
                 alinear_derecha_desde: int = 1) -> Table:
    cab = [Paragraph(_xml(h), st['th_r'] if i >= alinear_derecha_desde
                     else st['th']) for i, h in enumerate(encabezados)]
    cuerpo = [[Paragraph(_xml(c), st['td_r'] if i >= alinear_derecha_desde
                         else st['td']) for i, c in enumerate(fila)]
              for fila in filas]
    t = Table([cab] + cuerpo, colWidths=list(anchos), repeatRows=1)
    estilo = [
        ('BACKGROUND', (0, 0), (-1, 0), VERDE_MARCA),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, VERDE_MARCA),
        ('GRID', (0, 0), (-1, -1), 0.35, LINEA),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(cuerpo) + 1):
        if i % 2 == 0:
            estilo.append(('BACKGROUND', (0, i), (-1, i), PLANO))
    t.setStyle(TableStyle(estilo))
    return t


def _pie_de_pagina(canv, doc, emitido: datetime) -> None:
    canv.saveState()
    y = 10 * mm
    canv.setStrokeColor(LINEA)
    canv.setLineWidth(0.5)
    canv.line(doc.leftMargin, y + 9, doc.leftMargin + doc.width, y + 9)
    canv.setFont(FUENTE, 6.8)
    canv.setFillColor(TINTA_MUTE)
    canv.drawString(doc.leftMargin, y,
                    'Amazonas 365 · Reporte del estacionamiento generado '
                    'automáticamente por analítica de video con IA.')
    canv.drawRightString(doc.leftMargin + doc.width, y,
                         f'Página {doc.page} · '
                         f'{emitido.strftime("%d/%m/%Y %H:%M")}')
    canv.restoreState()


# ── Construcción del PDF ─────────────────────────────────────────────────

def construir_pdf(metricas_crudas: Dict[str, int],
                  cliente: str = '', sitio: str = '',
                  periodo_desde: Any = None, periodo_hasta: Any = None,
                  franja_horaria: str = '', camaras_monitoreadas: str = '',
                  codigo_reporte: str = '',
                  imagen_camara_base64: Optional[str] = None,
                  imagen_camara_etiqueta: str = '',
                  observaciones: str = '', conclusiones: str = '',
                  ocupacion_actual: Optional[int] = None,
                  placas: Optional[Sequence[Tuple[str, str, str]]] = None,
                  formato: str = 'completo',
                  emitido_en: Optional[datetime] = None,
                  ) -> Tuple[bytes, Dict[str, Union[int, float]]]:
    """Devuelve (pdf_bytes, metricas_derivadas).

    `formato='simple'` produce el reporte de dos tablas (el de diario en
    ARUBA); `'completo'`, el analítico con gráficas y foto.
    """
    m = calcular_metricas(metricas_crudas)
    emitido = emitido_en or datetime.now()
    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4,
                          leftMargin=16 * mm, rightMargin=16 * mm,
                          topMargin=14 * mm, bottomMargin=18 * mm,
                          title='Reporte de Estacionamiento — Amazonas 365',
                          author='Amazonas 365')
    marco = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id='cuerpo', showBoundary=0)
    doc.addPageTemplates([PageTemplate(
        id='base', frames=[marco],
        onPage=lambda c, d: _pie_de_pagina(c, d, emitido))])

    st = _estilos()
    ancho = doc.width
    elementos: List[Any] = []

    # ── Cabecera ─────────────────────────────────────────────────
    titulo = [
        Paragraph('Reporte de Estacionamiento', st['titulo']),
        Paragraph('Analítica de video con IA · Amazonas 365', st['subtitulo']),
    ]
    derecha = [
        Paragraph(_xml(codigo_reporte or '—'), st['codigo']),
        Paragraph(f'Emitido {emitido.strftime("%d/%m/%Y %H:%M")}',
                  st['emitido']),
    ]
    cab = Table([[titulo, derecha]], colWidths=[ancho * 0.62, ancho * 0.38])
    cab.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elementos += [cab, Spacer(1, 6),
                  HRFlowable(width=ancho, thickness=2, color=VERDE_MARCA,
                             spaceAfter=10)]

    # ── Ficha del período ────────────────────────────────────────
    def meta(rotulo: str, valor: str) -> List[Any]:
        return [Paragraph(_xml(rotulo).upper(), st['meta_dt']),
                Paragraph(_xml(valor or '—'), st['meta_dd'])]

    periodo = f'{_fecha(periodo_desde)} — {_fecha(periodo_hasta)}'
    ficha = Table([[meta('Cliente', cliente), meta('Sitio', sitio),
                    meta('Período', periodo),
                    meta('Franja', franja_horaria),
                    meta('Cámaras', camaras_monitoreadas)]],
                  colWidths=[ancho / 5] * 5)
    ficha.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, -1), PLANO),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDE),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, BORDE),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elementos += [ficha, Spacer(1, 12)]

    if formato != 'simple':
        elementos += _bloque_resumen(m, st, ancho, ocupacion_actual)

    # ── Tabla 1: resumen global ──────────────────────────────────
    elementos += _seccion('Resumen global por categoría', 1, st, ancho)
    filas_global = [
        ['Persona', 'Entrada', _num(m['p_ent'])],
        ['Persona', 'Salida', _num(m['p_sal'])],
        ['Vehículo', 'Entrada', _num(m['v_ent'])],
        ['Vehículo', 'Permanencia en el área', _num(m['v_per'])],
        ['Vehículo', 'Pernocta', _num(m['v_pernocta'])],
        ['Vehículo', 'Salida', _num(m['v_sal'])],
    ]
    elementos += [_tabla_datos(['Categoría', 'Evento', 'Cantidad'],
                               filas_global,
                               [ancho * 0.42, ancho * 0.4, ancho * 0.18], st,
                               alinear_derecha_desde=2),
                  Spacer(1, 12)]

    # ── Tabla 2: desglose por tipo ───────────────────────────────
    elementos += _seccion('Desglose de vehículos por tipo', 2, st, ancho)
    filas_tipo: List[List[str]] = []
    for etiqueta, pref in (('Carro', 'c'), ('Camión/camioneta', 'k'),
                           ('Autobús', 'a'), ('Moto', 'mo'), ('Otro', 'o')):
        if not any(m[f'{pref}_{s}'] for s in ('ent', 'per', 'sal', 'pernocta')):
            continue
        filas_tipo += [
            [etiqueta, 'Entrada', _num(m[f'{pref}_ent'])],
            [etiqueta, 'Permanencia en el área', _num(m[f'{pref}_per'])],
            [etiqueta, 'Pernocta', _num(m[f'{pref}_pernocta'])],
            [etiqueta, 'Salida', _num(m[f'{pref}_sal'])],
        ]
    if not filas_tipo:
        filas_tipo = [['—', 'Sin vehículos en el período', '0']]
    elementos += [_tabla_datos(['Categoría', 'Evento', 'Cantidad'], filas_tipo,
                               [ancho * 0.42, ancho * 0.4, ancho * 0.18], st,
                               alinear_derecha_desde=2)]

    if formato != 'simple':
        elementos += _bloque_graficas(m, st, ancho)
        if placas:
            elementos += _bloque_placas(placas, st, ancho)
        if imagen_camara_base64:
            elementos += _bloque_camara(imagen_camara_base64,
                                        imagen_camara_etiqueta, st, ancho)
        elementos += _bloque_notas(observaciones, conclusiones, st, ancho)

    doc.build(elementos)
    return buffer.getvalue(), m


def _bloque_resumen(m: Dict[str, Union[int, float]],
                    st: Dict[str, ParagraphStyle], ancho: float,
                    ocupacion: Optional[int]) -> List[Any]:
    """Cifra grande + tarjetas KPI."""
    hero = _tarjeta([
        Paragraph(_num(m['total']), st['hero_num']),
        Paragraph('detecciones en el período<br/>'
                  f'{_num(m["p_tot"])} personas · {_num(m["v_tot"])} vehículos',
                  st['hero_txt']),
    ], ancho * 0.32, PLANO)

    def kpi(rotulo: str, valor: str, sub: str) -> List[Any]:
        return [Paragraph(_xml(rotulo).upper(), st['kpi_lbl']),
                Paragraph(_xml(valor), st['kpi_val']),
                Paragraph(_xml(sub), st['kpi_sub'])]

    tarjetas = [
        kpi('Vehículos que entraron', _num(m['v_ent']),
            f'salieron {_num(m["v_sal"])} · neto {m["neto_v"]:+d}'),
        kpi('Estacionados (permanencia)', _num(m['v_per']),
            f'índice {_pct(m["idx_permanencia"])}'),
        kpi('Pernoctaron', _num(m['v_pernocta']),
            f'{_pct(m["idx_pernocta"])} de los que entraron'),
    ]
    if ocupacion is not None:
        tarjetas.append(kpi('Ocupación al cierre', _num(ocupacion),
                            'vehículos en el área'))
    fila_kpi = Table([tarjetas],
                     colWidths=[(ancho * 0.66) / len(tarjetas)] * len(tarjetas))
    fila_kpi.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.6, BORDE),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, BORDE),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    marco = Table([[hero, fila_kpi]], colWidths=[ancho * 0.33, ancho * 0.67])
    marco.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 6),
    ]))
    return [marco, Spacer(1, 12)]


def _bloque_graficas(m: Dict[str, Union[int, float]],
                     st: Dict[str, ParagraphStyle], ancho: float) -> List[Any]:
    mitad = ancho / 2 - 5
    pastel = _grafica_pastel([('Personas', int(m['p_tot']), SERIE_1),
                              ('Vehículos', int(m['v_tot']), SERIE_2)], mitad)
    categorias, datos = [], [[], [], [], []]
    for etiqueta, pref in (('Carro', 'c'), ('Camión', 'k'), ('Autobús', 'a'),
                           ('Moto', 'mo'), ('Otro', 'o')):
        if not any(m[f'{pref}_{s}'] for s in ('ent', 'per', 'sal', 'pernocta')):
            continue
        categorias.append(etiqueta)
        for i, suf in enumerate(('ent', 'per', 'pernocta', 'sal')):
            datos[i].append(int(m[f'{pref}_{suf}']))
    if not categorias:
        categorias, datos = ['—'], [[0], [0], [0], [0]]
    barras = _grafica_barras(
        [('Entrada', SERIE_1), ('Permanencia', SERIE_2),
         ('Pernocta', SERIE_4), ('Salida', SERIE_3)],
        categorias, datos, mitad)

    izq = [Paragraph('Composición de las detecciones', st['chart_t']),
           Spacer(1, 4), pastel]
    der = [Paragraph('Movimiento por tipo de vehículo', st['chart_t']),
           Spacer(1, 4), barras]
    fila = Table([[izq, der]], colWidths=[mitad + 5, mitad + 5])
    fila.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return [Spacer(1, 12)] + _seccion('Análisis gráfico', 3, st, ancho) + [fila]


def _bloque_placas(placas: Sequence[Tuple[str, str, str]],
                   st: Dict[str, ParagraphStyle], ancho: float) -> List[Any]:
    filas = [[p or '—', c or '—', v or '—'] for p, c, v in placas[:15]]
    return ([Spacer(1, 12)] + _seccion('Placas reconocidas', 4, st, ancho)
            + [_tabla_datos(['Placa', 'Confianza', 'Visto'], filas,
                            [ancho * 0.34, ancho * 0.22, ancho * 0.44], st,
                            alinear_derecha_desde=1)])


def _bloque_camara(imagen_b64: str, etiqueta: str,
                   st: Dict[str, ParagraphStyle], ancho: float) -> List[Any]:
    try:
        crudo = b64_a_bytes(imagen_b64)
        # ImageReader solo para MEDIR: `Image` (platypus) quiere una ruta o un
        # archivo, no un ImageReader — pasarle el lector falla con
        # "expected str, bytes or os.PathLike".
        w, h = ImageReader(io.BytesIO(crudo)).getSize()
        ancho_img = min(ancho, 150 * mm)
        alto_img = ancho_img * (h / w) if w else 80 * mm
        tope = 92 * mm
        if alto_img > tope:
            alto_img, ancho_img = tope, tope * (w / h if h else 1)
        pieza: List[Any] = [Image(io.BytesIO(crudo), width=ancho_img,
                                  height=alto_img)]
        if etiqueta:
            pieza += [Spacer(1, 3), Paragraph(_xml(etiqueta), st['caption'])]
        return ([Spacer(1, 12)] + _seccion('Vista de la cámara', 5, st, ancho)
                + [KeepTogether(pieza)])
    except Exception as exc:                          # noqa: BLE001
        logger.warning('no se pudo incrustar la foto de la cámara: %s', exc)
        return []


def _bloque_notas(observaciones: str, conclusiones: str,
                  st: Dict[str, ParagraphStyle], ancho: float) -> List[Any]:
    def caja(texto: str, vacio: str) -> Table:
        contenido = (texto or '').strip()
        estilo = st['obs'] if contenido else st['obs_vacia']
        parrafos = [Paragraph(_xml(l) or '&nbsp;', estilo)
                    for l in (contenido or vacio).split('\n')]
        t = Table([[parrafos]], colWidths=[ancho])
        t.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.6, BORDE),
            ('ROUNDEDCORNERS', [5, 5, 5, 5]),
            ('LINEBEFORE', (0, 0), (0, 0), 3, LIMA_MARCA),
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ]))
        return t

    return ([Spacer(1, 12)] + _seccion('Observaciones y conclusiones', 6, st,
                                       ancho)
            + [caja(observaciones, 'Sin observaciones registradas.'),
               Spacer(1, 6),
               caja(conclusiones, 'Sin conclusiones registradas.')])
