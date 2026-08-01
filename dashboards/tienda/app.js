/* dashboards/tienda/app.js — dashboard de producto de tienda.
 *
 * Marketing y consumo sobre /api/v1: visitantes y distribuciones de
 * /api/v1/resumen, ranking de pasillos de /api/v1/ranking-pasillos, galería
 * de /api/v1/capturas y mapas de calor cruzados con las cámaras del dominio.
 *
 * NOTA de producto: el evento «empleado repone anaquel» NO se pinta porque el
 * servidor dejó de emitir la analítica de retail en la simplificación del
 * 27-jul-2026 (ver clients/tienda/.../alerts_sidebar.py). Cuando el servidor
 * vuelva a emitirlo, esta página es donde debe aparecer.
 */
'use strict';

const TOPE_CAPTURAS = 500;          // límite máximo de /api/v1/capturas
let mostrar = 24;

function tarjetaCaptura(c) {
  const quien = [c.gender, c.age_range].filter(v => v && v !== 'Desconocido').join(' · ');
  return `<a class="item" href="${c.url}" target="_blank" rel="noopener">
    <img loading="lazy" src="${c.url_miniatura}" alt="captura de persona">
    <div class="pie">
      <b>${UI.esc(quien || 'sin demografía')}</b>
      ${c.visitas ? `<span class="pill ok">${UI.esc(c.visitas)} visitas</span>` : ''}
      <small>${UI.esc(c.camera || '—')} · ${UI.esc(c.timestamp || '')}</small>
      ${c.revisado_por_vlm ? '<small>revisado por VLM 🤖</small>' : ''}
    </div></a>`;
}

function franjasDeCapturas(capturas) {
  // El stem trae AAAAMMDD_HHMMSS: la hora está en las posiciones 9-10. Es un
  // conteo de CAPTURAS por hora — una aproximación honesta a la afluencia
  // mientras no haya series temporales en la analítica.
  const porHora = {};
  for (const c of capturas) {
    const ts = String(c.timestamp || '');
    if (ts.length < 11) continue;
    const hora = ts.slice(9, 11);
    if (!/^\d\d$/.test(hora)) continue;
    const clave = `${hora}:00`;
    porHora[clave] = (porHora[clave] || 0) + 1;
  }
  return porHora;
}

async function pintar() {
  const [{ resumen }, ranking, capturas, dispositivos, heatmaps] = await Promise.all([
    API.leer('resumen'),
    API.leer('ranking-pasillos?client_type=tienda'),
    API.leer(`capturas?limite=${Math.min(mostrar, TOPE_CAPTURAS)}`),
    API.leer('dispositivos?client_type=tienda'),
    API.leer('heatmaps'),
  ]);

  const nombre = f => f ? (f.camera_name || f.device_id) : null;
  UI.kpis({
    visitantes: resumen.visitantes_unicos,
    entradas: resumen.trafico_total_entradas ?? resumen.total_entradas,
    aforo: resumen.aforo_actual,
    permanencia: resumen.permanencia_media_s != null
      ? `${Number(resumen.permanencia_media_s).toFixed(1)} s` : null,
    masConcurrido: nombre(ranking.mas_concurrido),
    menosConcurrido: nombre(ranking.menos_concurrido),
  });

  UI.barras('genero', resumen.distribucion_genero);
  UI.barras('edad', resumen.distribucion_edad);

  UI.tabla('pasillos', ranking.ranking, [
    ['camera_name', 'pasillo / cámara', (v, f) => UI.esc(v || f.device_id)],
    ['entradas', 'entradas', v => v === null
      ? '<span class="pill aviso">sin medir aún</span>' : UI.esc(v)],
    ['visitantes_unicos', 'visitantes', v => UI.esc(v ?? '—')],
    ['permanencia_media_s', 'permanencia media',
      v => v != null ? `${Number(v).toFixed(1)} s` : '—'],
    ['heatmap', 'mapa de calor', v => v
      ? '<span class="pill ok">sí</span>' : '<span class="pill">no</span>'],
  ]);

  // Franjas: se calculan sobre las capturas MÁS RECIENTES que sirva la API
  // (hasta el tope), no solo las de la galería.
  const muestra = mostrar >= TOPE_CAPTURAS ? capturas
    : await API.leer(`capturas?limite=${TOPE_CAPTURAS}`);
  UI.barras('franjas', franjasDeCapturas(muestra.capturas || []));

  const galeria = document.getElementById('capturas');
  const filas = capturas.capturas || [];
  galeria.innerHTML = filas.length
    ? filas.map(tarjetaCaptura).join('')
    : '<div class="vacio">sin capturas todavía — aparecen cuando la cámara de tienda transmite con demografía</div>';
  const boton = document.getElementById('mas');
  const agotadas = filas.length >= (capturas.total ?? filas.length);
  boton.disabled = agotadas || mostrar >= TOPE_CAPTURAS;
  document.getElementById('mas-nota').textContent = agotadas
    ? '' : `${filas.length} de ${capturas.total}`;

  const propios = new Set(dispositivos.dispositivos.map(d => d.device_id));
  UI.heatmaps('heatmaps',
    heatmaps.heatmaps.filter(h => propios.has(h.device_id)));

  UI.tabla('dispositivos', dispositivos.dispositivos, [
    ['device_id', 'dispositivo'],
    ['camera_name', 'cámara'],
    ['pipelines', 'pipelines', v => UI.esc((v || []).join(', ') || '—')],
    ['frames', 'frames'],
    ['ultima_vez', 'última vez', v => UI.fecha(v)],
  ]);
}

async function pintarPaneles() {
  const enlaces = await API.paneles();
  const partes = [];
  // El dashboard propio de tienda (9030) se retiró el 31-jul-2026: ESTA
  // página es su sustituta, así que no se enlaza a sí misma.
  if (enlaces.visitantes) partes.push(`<a href="${enlaces.visitantes}" target="_blank" rel="noopener">👥 Analítica de visitantes (detalle, capturas, VLM)</a>`);
  document.getElementById('paneles').innerHTML = partes.join('');
}
pintarPaneles().catch(console.error);

document.getElementById('mas').addEventListener('click', () => {
  mostrar = Math.min(mostrar + 24, TOPE_CAPTURAS);
  pintar().catch(console.error);
});

refrescar(pintar, 15);
