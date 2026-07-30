"""Generador de reporte PDF de visitantes detectados.

Toma la base de datos de FaceReidentifier y produce un PDF con:
  - Header: titulo + fecha de generacion + periodo cubierto
  - Resumen: total de personas, total de visitas, breakdown por genero
  - Grafico de torta: distribucion por genero
  - Grafico de barras: distribucion por rango de edad
  - Grafico de barras: visitas por hora del dia
  - Tabla con top 10 visitantes (mas visitas)

Implementado con matplotlib (sin dependencias adicionales).
"""

import io
import datetime
from collections import Counter
from typing import Dict, List, Any

import matplotlib
matplotlib.use('Agg')  # backend sin GUI
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

# Tema oscuro coherente con el dashboard
PRIMARY = '#4dabf7'
MALE = '#4dabf7'
FEMALE = '#f783ac'
UNKNOWN = '#868e96'
SUCCESS = '#51cf66'
WARNING = '#ffd43b'
DANGER = '#ff6b6b'
TEXT_DARK = '#1e2730'
TEXT_GREY = '#5a6571'


def _format_dt(ts: float) -> str:
    if ts <= 0:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(ts).strftime(
            "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _aggregate(db: Dict[str, Any]) -> Dict[str, Any]:
    """Calcula agregados de la DB para el reporte."""
    total_persons = len(db)
    total_visits = 0
    by_gender = {'Hombre': 0, 'Mujer': 0, 'Desconocido': 0}
    by_age = {}
    visits_by_hour = Counter()
    min_ts = float('inf')
    max_ts = 0.0
    persons_with_demo = 0

    for rec in db.values():
        g = rec.get('gender') or 'Desconocido'
        by_gender[g] = by_gender.get(g, 0) + 1
        a = rec.get('age_range') or 'Desconocido'
        by_age[a] = by_age.get(a, 0) + 1
        visits = int(rec.get('visit_count', 1))
        total_visits += visits
        if g not in ('Desconocido', None):
            persons_with_demo += 1
        first = float(rec.get('first_seen', 0))
        last = float(rec.get('last_seen', 0))
        if first > 0:
            min_ts = min(min_ts, first)
            try:
                hour = datetime.datetime.fromtimestamp(first).hour
                visits_by_hour[hour] += visits
            except Exception:
                pass
        max_ts = max(max_ts, last)

    return {
        'total_persons': total_persons,
        'total_visits': total_visits,
        'persons_with_demographics': persons_with_demo,
        'by_gender': by_gender,
        'by_age': by_age,
        'visits_by_hour': dict(visits_by_hour),
        'period_start': min_ts if min_ts != float('inf') else 0,
        'period_end': max_ts,
    }


def _draw_cover_page(pdf: PdfPages, agg: Dict[str, Any]):
    """Pagina 1: portada con titulo y stats clave."""
    fig = plt.figure(figsize=(8.27, 11.69))  # A4
    fig.patch.set_facecolor('white')

    # Header
    fig.text(0.5, 0.92, "Reporte de Visitantes",
             ha='center', fontsize=24, fontweight='bold',
             color=TEXT_DARK)
    fig.text(0.5, 0.885, "Sistema ELDE - Detección Facial",
             ha='center', fontsize=12, color=TEXT_GREY)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fig.text(0.5, 0.855,
             f"Generado: {now_str}",
             ha='center', fontsize=9, color=TEXT_GREY)

    # Linea divisoria
    line = plt.Line2D([0.1, 0.9], [0.83, 0.83],
                      color='#cccccc', linewidth=0.5)
    fig.add_artist(line)

    # Periodo
    p_start = _format_dt(agg['period_start'])
    p_end = _format_dt(agg['period_end'])
    fig.text(0.5, 0.79,
             f"Periodo: {p_start} → {p_end}",
             ha='center', fontsize=11, color=TEXT_DARK)

    # Bloques de stats grandes (2x2)
    def stat_block(x, y, label, value, color=PRIMARY):
        fig.text(x, y + 0.04, str(value), ha='center',
                 fontsize=40, fontweight='bold', color=color)
        fig.text(x, y, label, ha='center', fontsize=10,
                 color=TEXT_GREY)

    stat_block(0.27, 0.62, "PERSONAS ÚNICAS",
               agg['total_persons'], PRIMARY)
    stat_block(0.73, 0.62, "VISITAS TOTALES",
               agg['total_visits'], SUCCESS)
    stat_block(0.27, 0.45, "CON CLASIFICACIÓN",
               agg['persons_with_demographics'], WARNING)
    avg_visits = (agg['total_visits'] / max(agg['total_persons'], 1)
                  if agg['total_persons'] else 0)
    stat_block(0.73, 0.45, "VISITAS/PERSONA",
               f"{avg_visits:.1f}", '#f783ac')

    # Breakdown por género (mini tabla)
    fig.text(0.5, 0.32, "Desglose por género",
             ha='center', fontsize=13, fontweight='bold',
             color=TEXT_DARK)
    g = agg['by_gender']
    total_g = sum(g.values()) or 1
    fig.text(0.27, 0.27, "♂ Hombres", ha='center',
             fontsize=10, color=TEXT_GREY)
    fig.text(0.27, 0.22, f"{g['Hombre']}",
             ha='center', fontsize=22, fontweight='bold', color=MALE)
    fig.text(0.27, 0.185,
             f"{g['Hombre'] * 100 / total_g:.1f}%",
             ha='center', fontsize=10, color=TEXT_GREY)

    fig.text(0.5, 0.27, "♀ Mujeres", ha='center',
             fontsize=10, color=TEXT_GREY)
    fig.text(0.5, 0.22, f"{g['Mujer']}",
             ha='center', fontsize=22, fontweight='bold', color=FEMALE)
    fig.text(0.5, 0.185,
             f"{g['Mujer'] * 100 / total_g:.1f}%",
             ha='center', fontsize=10, color=TEXT_GREY)

    fig.text(0.73, 0.27, "? Sin clasificar", ha='center',
             fontsize=10, color=TEXT_GREY)
    fig.text(0.73, 0.22, f"{g['Desconocido']}",
             ha='center', fontsize=22, fontweight='bold', color=UNKNOWN)
    fig.text(0.73, 0.185,
             f"{g['Desconocido'] * 100 / total_g:.1f}%",
             ha='center', fontsize=10, color=TEXT_GREY)

    # Footer
    fig.text(0.5, 0.05,
             "ELDE - Sistema de Análisis Perimetral · "
             "Página 1 de 4",
             ha='center', fontsize=8, color=TEXT_GREY)

    pdf.savefig(fig)
    plt.close(fig)


def _draw_charts_page(pdf: PdfPages, agg: Dict[str, Any]):
    """Pagina 2: graficos de torta (genero) y barras (edad)."""
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.patch.set_facecolor('white')
    fig.suptitle("Distribución Demográfica", fontsize=18,
                 fontweight='bold', color=TEXT_DARK, y=0.95)

    # ── Grafico de torta: genero ──
    ax1 = fig.add_subplot(2, 1, 1)
    g = agg['by_gender']
    labels = []
    sizes = []
    colors = []
    for label, color in [('Hombre', MALE), ('Mujer', FEMALE),
                         ('Desconocido', UNKNOWN)]:
        if g.get(label, 0) > 0:
            labels.append(f"{label} ({g[label]})")
            sizes.append(g[label])
            colors.append(color)
    if sizes:
        wedges, texts, autotexts = ax1.pie(
            sizes, labels=labels, colors=colors,
            autopct='%1.1f%%', startangle=90,
            textprops={'fontsize': 10, 'color': TEXT_DARK},
            wedgeprops={'edgecolor': 'white', 'linewidth': 2}
        )
        for at in autotexts:
            at.set_color('white')
            at.set_fontweight('bold')
    else:
        ax1.text(0.5, 0.5, "Sin datos",
                 ha='center', va='center', color=TEXT_GREY,
                 fontsize=14)
        ax1.axis('off')
    ax1.set_title("Por Género", fontsize=13,
                  color=TEXT_DARK, pad=10)

    # ── Grafico de barras: edad ──
    ax2 = fig.add_subplot(2, 1, 2)
    age_order = ['0-12', '13-17', '18-25', '26-35', '36-50',
                 '51-65', '65+', 'Desconocido']
    by_age = agg['by_age']
    age_labels = [a for a in age_order if by_age.get(a, 0) > 0]
    # Agregar rangos no estandar
    for a in by_age:
        if a not in age_order and by_age[a] > 0:
            age_labels.append(a)
    age_counts = [by_age.get(a, 0) for a in age_labels]
    bar_colors = [UNKNOWN if a == 'Desconocido' else PRIMARY
                  for a in age_labels]
    if age_counts:
        bars = ax2.bar(age_labels, age_counts, color=bar_colors,
                       edgecolor='white', linewidth=1)
        for bar in bars:
            h = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width() / 2, h,
                     f'{int(h)}', ha='center', va='bottom',
                     fontsize=11, fontweight='bold', color=TEXT_DARK)
        ax2.set_ylabel("Personas", color=TEXT_DARK)
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        ax2.tick_params(axis='both', colors=TEXT_DARK)
        ax2.set_ylim(0, max(age_counts) * 1.15)
    else:
        ax2.text(0.5, 0.5, "Sin datos",
                 ha='center', va='center', color=TEXT_GREY,
                 fontsize=14)
        ax2.axis('off')
    ax2.set_title("Por Rango de Edad", fontsize=13,
                  color=TEXT_DARK, pad=10)

    fig.text(0.5, 0.03,
             "ELDE - Sistema de Análisis Perimetral · "
             "Página 2 de 4",
             ha='center', fontsize=8, color=TEXT_GREY)
    fig.subplots_adjust(hspace=0.4, top=0.9, bottom=0.08)

    pdf.savefig(fig)
    plt.close(fig)


def _draw_hourly_and_table_page(pdf: PdfPages, db: Dict[str, Any],
                                agg: Dict[str, Any]):
    """Pagina 3: visitas por hora + tabla de top visitantes."""
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.patch.set_facecolor('white')
    fig.suptitle("Patrones Temporales y Top Visitantes",
                 fontsize=18, fontweight='bold', color=TEXT_DARK, y=0.95)

    # ── Visitas por hora del dia ──
    ax1 = fig.add_subplot(2, 1, 1)
    hours = list(range(24))
    counts = [agg['visits_by_hour'].get(h, 0) for h in hours]
    if any(counts):
        bars = ax1.bar(hours, counts, color=PRIMARY,
                       edgecolor='white', linewidth=0.5)
        ax1.set_xlabel("Hora del día", color=TEXT_DARK)
        ax1.set_ylabel("Visitas", color=TEXT_DARK)
        ax1.set_xticks(range(0, 24, 2))
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        ax1.tick_params(axis='both', colors=TEXT_DARK)
        ax1.grid(axis='y', alpha=0.2)
    else:
        ax1.text(0.5, 0.5, "Sin datos temporales",
                 ha='center', va='center', color=TEXT_GREY,
                 fontsize=14)
        ax1.axis('off')
    ax1.set_title("Distribución por hora del día",
                  fontsize=13, color=TEXT_DARK, pad=10)

    # ── Tabla de top visitantes ──
    ax2 = fig.add_subplot(2, 1, 2)
    ax2.axis('off')
    ax2.set_title("Top 10 visitantes más frecuentes",
                  fontsize=13, color=TEXT_DARK,
                  pad=10, loc='left', x=0.05)

    # Ordenar por visit_count desc
    sorted_persons = sorted(
        db.items(),
        key=lambda kv: kv[1].get('visit_count', 1),
        reverse=True
    )[:10]

    if sorted_persons:
        table_data = [['#', 'UUID', 'Género', 'Edad', 'Visitas',
                       'Primera', 'Última']]
        for i, (uid, rec) in enumerate(sorted_persons, 1):
            first = _format_dt(rec.get('first_seen', 0))[5:16]  # MM-DD HH:MM
            last = _format_dt(rec.get('last_seen', 0))[5:16]
            table_data.append([
                str(i),
                uid[:8],
                rec.get('gender', '?') or '?',
                rec.get('age_range', '?') or '?',
                str(rec.get('visit_count', 1)),
                first,
                last,
            ])
        table = ax2.table(
            cellText=table_data[1:],
            colLabels=table_data[0],
            cellLoc='center',
            loc='center',
            colWidths=[0.05, 0.13, 0.13, 0.12, 0.10, 0.18, 0.18],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.0, 1.6)
        # Estilo del header
        for i in range(len(table_data[0])):
            cell = table[0, i]
            cell.set_facecolor(PRIMARY)
            cell.set_text_props(color='white', fontweight='bold')
            cell.set_edgecolor('white')
        # Filas alternadas
        for r in range(1, len(table_data)):
            for c in range(len(table_data[0])):
                cell = table[r, c]
                cell.set_facecolor('#f5f7fa' if r % 2 else 'white')
                cell.set_edgecolor('#e0e0e0')
    else:
        ax2.text(0.5, 0.5, "Sin visitantes registrados",
                 ha='center', va='center', color=TEXT_GREY,
                 fontsize=14)

    fig.text(0.5, 0.03,
             "ELDE - Sistema de Análisis Perimetral · "
             "Página 3 de 4",
             ha='center', fontsize=8, color=TEXT_GREY)
    fig.subplots_adjust(hspace=0.35, top=0.9, bottom=0.08)

    pdf.savefig(fig)
    plt.close(fig)


def _zones_abs(grid, k=6, r=3):
    """Top-K celdas mas calientes (valor absoluto) -> [(x_rel, y_rel, val)]."""
    import numpy as np
    g = grid.copy()
    h, w = g.shape
    out = []
    for _ in range(k):
        idx = int(np.argmax(g))
        gy, gx = divmod(idx, w)
        val = float(g[gy, gx])
        if val <= 0:
            break
        out.append(((gx + 0.5) / w, (gy + 0.5) / h, val))
        y0, y1 = max(0, gy - r), min(h, gy + r + 1)
        x0, x1 = max(0, gx - r), min(w, gx + r + 1)
        g[y0:y1, x0:x1] = 0.0
    return out


def _area_at(areas, x, y):
    """Nombre del area que contiene (x,y) en coords 0..1, o None."""
    for a in areas or []:
        try:
            if a['x1'] <= x <= a['x2'] and a['y1'] <= y <= a['y2']:
                return a['name']
        except Exception:
            continue
    return None


def _heatmap_summary(heatmap_dir):
    """Suma las grillas .npz historicas por camara y devuelve la zona GLOBAL
    mas y menos frecuentada (camara + ubicacion/area + intensidad). None si no
    hay datos de mapa de calor."""
    import os
    import glob
    import json as _json
    import numpy as np
    if not heatmap_dir:
        return None
    hist = os.path.join(str(heatmap_dir), 'history')
    if not os.path.isdir(hist):
        return None
    cams = []
    for cam in sorted(os.listdir(hist)):
        cdir = os.path.join(hist, cam)
        if not os.path.isdir(cdir):
            continue
        grid = None
        for nz in glob.glob(os.path.join(cdir, '*.npz')):
            if nz.endswith('.tmp.npz'):
                continue
            try:
                with np.load(nz) as d:
                    g = d['grid'].astype('float64')
            except Exception:
                continue
            if grid is None:
                grid = g
            elif g.shape == grid.shape:
                grid += g
        if grid is None or float(grid.max()) <= 0:
            continue
        cname = cam
        lj = os.path.join(str(heatmap_dir), f"{cam}.json")
        if os.path.isfile(lj):
            try:
                cname = _json.loads(
                    open(lj, encoding='utf-8').read()).get('camera_name', cam)
            except Exception:
                pass
        areas = []
        af = os.path.join(str(heatmap_dir), 'areas', f"{cam}.json")
        if os.path.isfile(af):
            try:
                areas = _json.loads(
                    open(af, encoding='utf-8').read()).get('areas', [])
            except Exception:
                pass
        cams.append({'name': cam, 'camera_name': cname,
                     'grid': grid, 'areas': areas})
    if not cams:
        return None
    gmax = max(float(c['grid'].max()) for c in cams) or 1.0
    all_zones = []
    for c in cams:
        for (x, y, val) in _zones_abs(c['grid'], k=6):
            all_zones.append({'cam': c['camera_name'], 'x': x, 'y': y,
                              'val': val, 'pct': round(100.0 * val / gmax, 1),
                              'area': _area_at(c['areas'], x, y),
                              'cam_obj': c})
    if not all_zones:
        return None
    return {'cams': cams,
            'most': max(all_zones, key=lambda z: z['val']),
            'least': min(all_zones, key=lambda z: z['val'])}


def _draw_heatmap_page(pdf: PdfPages, summary, page_no: int, total: int):
    """Pagina: zona mas y menos frecuentada del mapa de calor + imagen."""
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.patch.set_facecolor('white')
    fig.suptitle("Zonas de Concentracion - Mapa de Calor",
                 fontsize=18, fontweight='bold', color=TEXT_DARK, y=0.95)

    if not summary:
        ax = fig.add_subplot(1, 1, 1)
        ax.axis('off')
        ax.text(0.5, 0.58, "Sin datos de mapa de calor en el historico.",
                ha='center', va='center', color=TEXT_GREY, fontsize=14)
        ax.text(0.5, 0.50, "Deja correr el sistema con camaras para "
                "acumular data.", ha='center', va='center',
                color=TEXT_GREY, fontsize=11)
    else:
        most = summary['most']
        least = summary['least']

        def _loc(z):
            return (z['area'] if z['area']
                    else f"posicion x {round(z['x']*100)}%, "
                         f"y {round(z['y']*100)}%")

        ax_t = fig.add_axes([0.08, 0.62, 0.84, 0.28])
        ax_t.axis('off')
        ax_t.add_patch(plt.Rectangle((0, 0.54), 1, 0.44, facecolor='#fff0f0',
                       edgecolor=DANGER, lw=2, transform=ax_t.transAxes))
        ax_t.text(0.03, 0.88, "ZONA MAS FRECUENTADA", fontsize=14,
                  fontweight='bold', color=DANGER, transform=ax_t.transAxes)
        ax_t.text(0.03, 0.74, f"Camara:  {most['cam']}", fontsize=12,
                  color=TEXT_DARK, transform=ax_t.transAxes)
        ax_t.text(0.03, 0.62, f"Zona:  {_loc(most)}   (intensidad "
                  f"{most['pct']}%)", fontsize=12, color=TEXT_DARK,
                  transform=ax_t.transAxes)
        ax_t.add_patch(plt.Rectangle((0, 0.04), 1, 0.44, facecolor='#f0f6ff',
                       edgecolor=PRIMARY, lw=2, transform=ax_t.transAxes))
        ax_t.text(0.03, 0.38, "ZONA MENOS FRECUENTADA", fontsize=14,
                  fontweight='bold', color=PRIMARY, transform=ax_t.transAxes)
        ax_t.text(0.03, 0.24, f"Camara:  {least['cam']}", fontsize=12,
                  color=TEXT_DARK, transform=ax_t.transAxes)
        ax_t.text(0.03, 0.12, f"Zona:  {_loc(least)}   (intensidad "
                  f"{least['pct']}%)", fontsize=12, color=TEXT_DARK,
                  transform=ax_t.transAxes)

        ax_h = fig.add_axes([0.1, 0.08, 0.8, 0.46])
        grid = most['cam_obj']['grid']
        h, w = grid.shape
        ax_h.imshow(grid, cmap='jet', aspect='auto', interpolation='bilinear')
        ax_h.scatter([most['x'] * w], [most['y'] * h], s=180,
                     facecolors='none', edgecolors='white', linewidths=2.5)
        ax_h.set_title(f"Mapa de calor - {most['cam']} "
                       f"(circulo = zona mas frecuentada)",
                       fontsize=12, color=TEXT_DARK)
        ax_h.axis('off')

    fig.text(0.5, 0.03, "ELDE - Sistema de Analisis Perimetral - "
             f"Pagina {page_no} de {total}",
             ha='center', fontsize=8, color=TEXT_GREY)
    pdf.savefig(fig)
    plt.close(fig)


def generate_report(db: Dict[str, Any], heatmap_dir=None) -> bytes:
    """Genera el PDF de reporte y lo devuelve como bytes.

    Args:
        db: dict {uuid: persona_record} (formato de FaceReidentifier).
        heatmap_dir: ruta a output/heatmap para la pagina de zonas (opcional).

    Returns:
        Bytes del PDF listo para servir/guardar.
    """
    agg = _aggregate(db)
    hm = _heatmap_summary(heatmap_dir)
    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        _draw_cover_page(pdf, agg)
        _draw_charts_page(pdf, agg)
        _draw_hourly_and_table_page(pdf, db, agg)
        _draw_heatmap_page(pdf, hm, 4, 4)
        # Metadata
        d = pdf.infodict()
        d['Title'] = 'Reporte de Visitantes ELDE'
        d['Author'] = 'Sistema ELDE'
        d['Subject'] = 'Detección facial y demografía'
        d['Keywords'] = 'visitantes, demografía, género, edad'
        d['CreationDate'] = datetime.datetime.now()
    buffer.seek(0)
    return buffer.read()
