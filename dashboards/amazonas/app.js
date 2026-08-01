/* dashboards/amazonas/app.js — dashboard de producto de Amazonas.
 *
 * Personas, género y edad sobre /api/v1. Los totales de mujeres/hombres salen
 * de la distribución de PERSONAS ÚNICAS (la base biométrica del servidor via
 * /api/v1/resumen), no de contar capturas: la misma persona fotografiada diez
 * veces cuenta una. El cruce género×edad sí es por capturas (cada captura
 * trae ambos) y se etiqueta como tal.
 */
'use strict';

const TOPE_CAPTURAS = 500;
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

function cruceGeneroEdad(capturas) {
  // Tabla edades (filas) × géneros (columnas), contando capturas.
  const generos = ['Mujer', 'Hombre', 'Desconocido'];
  const porEdad = {};
  for (const c of capturas) {
    const edad = c.age_range || 'Desconocido';
    const genero = generos.includes(c.gender) ? c.gender : 'Desconocido';
    porEdad[edad] = porEdad[edad] || { Mujer: 0, Hombre: 0, Desconocido: 0 };
    porEdad[edad][genero] += 1;
  }
  const filas = Object.entries(porEdad)
    .sort((a, b) => a[0].localeCompare(b[0], 'es', { numeric: true }))
    .map(([edad, n]) => ({ edad, ...n, total: n.Mujer + n.Hombre + n.Desconocido }));
  return filas;
}

async function pintar() {
  const [{ resumen }, capturas, dispositivos, heatmaps] = await Promise.all([
    API.leer('resumen'),
    API.leer(`capturas?limite=${TOPE_CAPTURAS}`),
    API.leer('dispositivos?client_type=amazonas'),
    API.leer('heatmaps'),
  ]);

  const genero = resumen.distribucion_genero || {};
  UI.kpis({
    personas: resumen.galeria_total,
    mujeres: genero.Mujer ?? 0,
    hombres: genero.Hombre ?? 0,
    sinIdentificar: genero.Desconocido ?? 0,
    capturas: resumen.total_capturas,
  });
  UI.barras('genero', genero);
  UI.barras('edad', resumen.distribucion_edad);

  const todas = capturas.capturas || [];
  UI.tabla('cruce', cruceGeneroEdad(todas), [
    ['edad', 'edad'],
    ['Mujer', 'mujeres'],
    ['Hombre', 'hombres'],
    ['Desconocido', 'sin género'],
    ['total', 'total'],
  ]);

  const visibles = todas.slice(0, mostrar);
  document.getElementById('capturas').innerHTML = visibles.length
    ? visibles.map(tarjetaCaptura).join('')
    : '<div class="vacio">sin capturas todavía — aparecen cuando la cámara transmite con demografía</div>';
  const boton = document.getElementById('mas');
  boton.disabled = visibles.length >= todas.length;
  document.getElementById('mas-nota').textContent =
    visibles.length >= todas.length ? '' : `${visibles.length} de ${capturas.total}`;

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

  // Analítica por cámara: solo las del dominio; sin informe no es un error,
  // es "todavía no".
  const partes = [];
  for (const d of dispositivos.dispositivos.slice(0, 8)) {
    try {
      const a = await API.leer(`analitica/${encodeURIComponent(d.device_id)}`);
      const x = a.datos || {};
      partes.push({
        device_id: d.device_id,
        visitantes: x.visitantes_unicos ?? '—',
        entradas: x.total_entradas ?? '—',
        permanencia: x.permanencia_media_s != null
          ? `${Number(x.permanencia_media_s).toFixed(1)} s` : '—',
        sesiones: a.sesiones_disponibles,
      });
    } catch (e) {
      partes.push({ device_id: d.device_id, visitantes: '—', entradas: '—',
                    permanencia: 'sin informe todavía', sesiones: 0 });
    }
  }
  UI.tabla('analitica', partes, [
    ['device_id', 'dispositivo'],
    ['visitantes', 'visitantes'],
    ['entradas', 'entradas'],
    ['permanencia', 'permanencia media'],
    ['sesiones', 'sesiones'],
  ]);
}

async function pintarPaneles() {
  const enlaces = await API.paneles();
  const partes = [];
  if (enlaces.vigilante) partes.push(`<a href="${enlaces.vigilante}" target="_blank" rel="noopener">🖼️ Galería de personas de interés</a>`);
  if (enlaces.visitantes) partes.push(`<a href="${enlaces.visitantes}" target="_blank" rel="noopener">👥 Analítica de visitantes</a>`);
  document.getElementById('paneles').innerHTML = partes.join('');
}
pintarPaneles().catch(console.error);

document.getElementById('mas').addEventListener('click', () => {
  mostrar += 24;
  pintar().catch(console.error);
});

refrescar(pintar, 20);
