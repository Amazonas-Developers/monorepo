/* dashboards/perimetrales/app.js — dashboard de producto del perímetro.
 *
 * Todo sale de /api/v1 (regla del HITO 9): el buscador y la galería de
 * /api/v1/alertas, los mapas de calor de /api/v1/heatmaps cruzados con los
 * dispositivos del dominio, y los enlaces de /api/v1/paneles.
 */
'use strict';

// ── estado del buscador ──────────────────────────────────────────────────
// `camara` es el SEGMENTO activo (pestañas "Cámara / cliente"): el operador
// usa un mismo cliente para camaras de establecimientos distintos, y el
// segmento gobierna KPIs, galería, desgloses, mapas de calor y tabla.
const filtros = { q: '', evento: '', clase: '', camara: '', desde: '', hasta: '' };
const TOPE_API = 200;               // limite máximo de /api/v1/alertas
let mostrar = 24;                   // cuántas fotos pide la galería

function consultaAlertas(limite) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filtros)) if (v) p.set(k, v);
  p.set('limite', String(Math.min(limite, TOPE_API)));
  return API.leer(`alertas?${p.toString()}`);
}

// ── piezas de la página ──────────────────────────────────────────────────
function opciones(select, nombres, prefijo) {
  // Rellena un <select> conservando la selección actual. Si nada cambió no se
  // toca el DOM: el refresco de 15 s no debe cerrarle el desplegable al que
  // está eligiendo.
  const actual = select.value;
  const base = select.querySelector('option').outerHTML;
  const nuevo = base + nombres.map(n =>
    `<option value="${UI.esc(n)}">${UI.esc(prefijo ? `${prefijo}: ${n}` : n)}</option>`).join('');
  if (select.innerHTML === nuevo) return;
  select.innerHTML = nuevo;
  select.value = actual;
}

function tarjetaAlerta(a) {
  const foto = `/api/v1/alertas/foto/${encodeURIComponent(a.archivo)}`;
  const clasePill = a.clase_gruesa === 'persona' ? 'ok' : '';
  return `<a class="item" href="${foto}" target="_blank" rel="noopener">
    <img loading="lazy" src="${foto}" alt="${UI.esc(a.clase || 'alerta')}">
    <div class="pie">
      <b>${UI.esc(a.clase || 'sin clase')}</b>
      <span class="pill ${clasePill}">${UI.esc(a.evento || 'sin evento')}</span>
      <small>${UI.esc(a.camara || '—')} · ${UI.esc(a.timestamp || 'sin fecha')}</small>
      ${a.establecimiento ? `<small>🏢 ${UI.esc(a.establecimiento)}</small>` : ''}
      ${a.permanencia_s != null ? `<small>permaneció ${UI.duracion(a.permanencia_s)}</small>` : ''}
      ${a.sin_metadatos ? '<small class="error-carga">foto sin metadatos</small>' : ''}
    </div></a>`;
}

function pintarSegmentos(porCamara, totalGlobal) {
  // Pestañas de camara: se reconstruyen en cada refresco (los handlers van
  // por delegación, así que reconstruir no pierde nada).
  const boton = (valor, texto) =>
    `<button class="boton${filtros.camara === valor ? ' activo' : ''}"
             data-cam="${UI.esc(valor)}">${UI.esc(texto)}</button>`;
  const partes = [boton('', `Todas (${totalGlobal})`)];
  for (const [cam, n] of Object.entries(porCamara || {})) {
    partes.push(boton(cam, `📹 ${cam} (${n})`));
  }
  document.getElementById('segmentos').innerHTML = partes.join('');
}

async function pintar() {
  const [catalogo, global, filtrado, dispositivos, heatmaps] = await Promise.all([
    API.leer('alertas?limite=1'),          // catálogo de cámaras, SIN filtros
    consultaAlertasGlobal(),
    consultaAlertas(mostrar),
    API.leer('dispositivos?client_type=perimetrales'),
    API.leer('heatmaps'),
  ]);

  // Pestañas de segmentación con el catálogo completo (una pestaña por
  // cámara aunque esté filtrado a otra).
  pintarSegmentos(catalogo.facetas.por_camara, catalogo.total);

  // KPIs del SEGMENTO activo (o globales en "Todas"); la galería y los
  // desgloses siguen además al buscador.
  const clases = global.facetas.por_clase || {};
  UI.kpis({
    personas: clases.PERSONA ?? 0,
    carros: clases.CARRO ?? 0,
    motos: clases.MOTO ?? 0,
    total: global.total,
    hoy: global.hoy,
  });

  opciones(document.getElementById('f-evento'), Object.keys(global.facetas.por_evento || {}), 'evento');
  opciones(document.getElementById('f-clase'), Object.keys(clases), 'clase');

  UI.barras('d-clase', filtrado.facetas.por_clase);
  UI.barras('d-evento', filtrado.facetas.por_evento);
  UI.barras('d-camara', filtrado.facetas.por_camara);

  const hayFiltros = Object.values(filtros).some(Boolean);
  document.getElementById('f-cuenta').textContent = hayFiltros
    ? `${filtrado.total} de ${filtrado.total_capturas} alertas`
    : `${filtrado.total} alertas`;

  const galeria = document.getElementById('galeria');
  galeria.innerHTML = filtrado.alertas.length
    ? filtrado.alertas.map(tarjetaAlerta).join('')
    : '<div class="vacio">ninguna alerta coincide con la búsqueda</div>';

  const boton = document.getElementById('mas');
  const nota = document.getElementById('mas-nota');
  const agotadas = filtrado.alertas.length >= filtrado.total;
  boton.disabled = agotadas || mostrar >= TOPE_API;
  nota.textContent = agotadas ? ''
    : (mostrar >= TOPE_API
      ? `la API sirve ${TOPE_API} como máximo: afina la búsqueda para ver el resto`
      : `${filtrado.alertas.length} de ${filtrado.total}`);

  // Mapas de calor y tabla SOLO del dominio y del SEGMENTO activo.
  const visibles = dispositivos.dispositivos.filter(d =>
    !filtros.camara || (d.camera_name || d.device_id) === filtros.camara);
  const propios = new Set(visibles.map(d => d.device_id));
  UI.heatmaps('heatmaps',
    heatmaps.heatmaps.filter(h => propios.has(h.device_id)));

  UI.tabla('dispositivos', visibles, [
    ['device_id', 'dispositivo'],
    ['camera_name', 'cámara'],
    ['pipelines', 'pipelines', v => UI.esc((v || []).join(', ') || '—')],
    ['frames', 'frames'],
    ['primera_vez', 'primera vez', v => UI.fecha(v)],
    ['ultima_vez', 'última vez', v => UI.fecha(v)],
  ]);
}

async function consultaAlertasGlobal() {
  // Totales del SEGMENTO activo + cuántas son de hoy. La fecha local del
  // navegador basta: la API entiende AAAA-MM-DD.
  const hoy = new Date();
  const dia = [hoy.getFullYear(),
    String(hoy.getMonth() + 1).padStart(2, '0'),
    String(hoy.getDate()).padStart(2, '0')].join('-');
  const cam = filtros.camara
    ? `&camara=${encodeURIComponent(filtros.camara)}` : '';
  const [todas, deHoy] = await Promise.all([
    API.leer(`alertas?limite=1${cam}`),
    API.leer(`alertas?limite=1&desde=${dia}&hasta=${dia}${cam}`),
  ]);
  return { ...todas, hoy: deHoy.total };
}

// ── paneles auxiliares ───────────────────────────────────────────────────
async function pintarPaneles() {
  const enlaces = await API.paneles();
  const partes = [];
  if (enlaces.vigilante) partes.push(`<a href="${enlaces.vigilante}" target="_blank" rel="noopener">🚨 Panel de VIGILANTE (gestión y galería)</a>`);
  document.getElementById('paneles').innerHTML = partes.join('');
}
pintarPaneles().catch(console.error);

// ── cableado del buscador ────────────────────────────────────────────────
function alCambiar() {
  mostrar = 24;
  pintar().catch(console.error);
}

document.getElementById('f-q').addEventListener('input', UI.debounce(e => {
  filtros.q = e.target.value.trim();
  alCambiar();
}));
for (const [id, clave] of [['f-evento', 'evento'], ['f-clase', 'clase'],
                           ['f-desde', 'desde'], ['f-hasta', 'hasta']]) {
  document.getElementById(id).addEventListener('change', e => {
    filtros[clave] = e.target.value;
    alCambiar();
  });
}
document.getElementById('f-limpiar').addEventListener('click', () => {
  // Limpia el BUSCADOR; el segmento de cámara es navegación y se conserva.
  for (const clave of ['q', 'evento', 'clase', 'desde', 'hasta']) filtros[clave] = '';
  for (const id of ['f-q', 'f-evento', 'f-clase', 'f-desde', 'f-hasta']) {
    document.getElementById(id).value = '';
  }
  alCambiar();
});
// Pestañas de cámara: delegación (los botones se reconstruyen en cada tick).
document.getElementById('segmentos').addEventListener('click', e => {
  const btn = e.target.closest('button[data-cam]');
  if (!btn) return;
  filtros.camara = btn.dataset.cam;
  alCambiar();
});
document.getElementById('mas').addEventListener('click', () => {
  mostrar = Math.min(mostrar + 24, TOPE_API);
  pintar().catch(console.error);
});

refrescar(pintar, 15);
