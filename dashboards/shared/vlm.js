/* dashboards/shared/vlm.js — widget de búsqueda VLM para los dashboards.
 *
 * Único punto donde las páginas ACCIONAN en vez de leer: el buscador VLM vive
 * en /dashboard/api (la capa /api/v1 es solo lectura por regla). El flujo es
 * asíncrono a propósito — la VLM puede tardar 20-30 s por foto — así que se
 * crea el trabajo y se sondea su estado cada 2 s sin congelar la página.
 */
'use strict';

function montarBuscadorVLM(idContenedor, ambito, ejemplo) {
  const cont = document.getElementById(idContenedor);
  cont.innerHTML = `
    <div class="filtros">
      <button class="boton" data-vlm="toggle">VLM …</button>
      <input type="search" data-vlm="consulta"
             placeholder="${UI.esc(ejemplo || 'p. ej. búscame el carro rojo')}" size="36">
      <button class="boton" data-vlm="buscar">Buscar</button>
      <span class="cuenta" data-vlm="estado"></span>
    </div>
    <div class="galeria" data-vlm="resultados"></div>`;
  const $ = clave => cont.querySelector(`[data-vlm="${clave}"]`);
  let activo = false;
  let sondeo = null;

  async function refrescarEstado() {
    const r = await fetch('/dashboard/api/vlm-buscador', { cache: 'no-store' });
    const d = await r.json();
    activo = !!d.activo;
    // `modelo` llega como objeto ({key, model_id, ...}): se muestra la clave.
    const modelo = d.modelo && (d.modelo.key || d.modelo.model_id || d.modelo);
    $('toggle').textContent = `VLM ${activo ? 'ON' : 'OFF'}${modelo ? ' · ' + modelo : ''}`;
    $('toggle').style.color = activo ? 'var(--ok)' : 'var(--apagado)';
    $('toggle').style.borderColor = activo ? 'var(--ok)' : 'var(--borde)';
    if (!activo && !$('estado').textContent) {
      $('estado').textContent = 'apagado: la GPU queda para la inferencia en vivo';
    }
  }

  $('toggle').addEventListener('click', async () => {
    $('toggle').disabled = true;
    try {
      await fetch(`/dashboard/api/vlm-buscador?activo=${!activo}`, { method: 'POST' });
      $('estado').textContent = '';
      await refrescarEstado();
    } catch (e) {
      $('estado').textContent = e.message;
    } finally {
      $('toggle').disabled = false;
    }
  });

  function pintarResultados(t) {
    $('resultados').innerHTML = (t.resultados || []).map(f => `
      <a class="item" href="${f.url}" target="_blank" rel="noopener">
        <img loading="lazy" src="${f.url}" alt="">
        <div class="pie">
          <b>${UI.esc(f.nota || f.archivo)}</b>
          <small>${UI.esc(f.detalle || '')}</small>
        </div></a>`).join('');
  }

  function seguir(id) {
    clearInterval(sondeo);
    sondeo = setInterval(async () => {
      try {
        const r = await fetch(`/dashboard/api/vlm-busqueda/${id}`, { cache: 'no-store' });
        if (!r.ok) return;
        const t = await r.json();
        const modo = t.modo === 'deteccion'
          ? `detección rápida${t.termino ? ' de «' + t.termino + '»' : ''}`
          : 'pregunta al VLM (lenta)';
        pintarResultados(t);
        if (t.estado === 'terminado') {
          clearInterval(sondeo);
          $('estado').textContent =
            `listo en ${t.duracion_s} s · ${(t.resultados || []).length} coincidencia(s) en ${t.total} fotos`;
        } else if (t.estado === 'error') {
          clearInterval(sondeo);
          $('estado').textContent = `error: ${t.error}`;
        } else {
          $('estado').textContent =
            `${t.hechas}/${t.total} fotos · ${modo} · ${(t.resultados || []).length} coincidencia(s)`;
        }
      } catch (e) { /* red caída: se reintenta en el próximo tick */ }
    }, 2000);
  }

  async function buscar() {
    const consulta = $('consulta').value.trim();
    if (!consulta) return;
    $('buscar').disabled = true;
    $('resultados').innerHTML = '';
    $('estado').textContent = 'enviando…';
    try {
      const r = await fetch(
        `/dashboard/api/vlm-busqueda?consulta=${encodeURIComponent(consulta)}&ambito=${encodeURIComponent(ambito)}`,
        { method: 'POST' });
      const d = await r.json();
      if (!r.ok) {
        $('estado').textContent = d.detail || `error ${r.status}`;
        return;
      }
      seguir(d.id);
    } catch (e) {
      $('estado').textContent = e.message;
    } finally {
      $('buscar').disabled = false;
    }
  }
  $('buscar').addEventListener('click', buscar);
  $('consulta').addEventListener('keydown', e => { if (e.key === 'Enter') buscar(); });

  refrescarEstado().catch(console.error);
}
