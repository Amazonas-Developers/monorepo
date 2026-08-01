/* dashboards/shared/api.js — el UNICO camino de datos de los tres dashboards.
 *
 * Regla del HITO 9: estas paginas leen exclusivamente de /api/v1 con rutas
 * RELATIVAS. Ni un host, ni un puerto escrito: las sirve el mismo servidor que
 * responde la API, asi que el origen del navegador ya es el correcto. Los
 * enlaces a paneles en otros puertos (VIGILANTE :5333) tampoco se escriben:
 * se preguntan a /api/v1/paneles y se montan sobre el hostname actual.
 */
'use strict';

const API = {
  /** GET a /api/v1/<ruta>. Lanza con un mensaje legible si algo falla. */
  async leer(ruta) {
    const r = await fetch(`/api/v1/${ruta}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`/api/v1/${ruta} respondio ${r.status}`);
    return r.json();
  },

  /** Enlaces a los paneles auxiliares, montados sobre el host actual. */
  async paneles() {
    const { paneles } = await API.leer('paneles');
    const enlaces = {};
    for (const [nombre, p] of Object.entries(paneles)) {
      if (p.puerto) {
        enlaces[nombre] = `${location.protocol}//${location.hostname}:${p.puerto}${p.ruta}`;
      } else if (p.ruta && p.ruta !== '/') {
        enlaces[nombre] = p.ruta;              // mismo proceso, ruta relativa
      }
    }
    return enlaces;
  },
};

const UI = {
  /** Rellena los .kpi[data-kpi] de la pagina con un objeto {clave: valor}. */
  kpis(valores) {
    for (const el of document.querySelectorAll('[data-kpi]')) {
      const v = valores[el.dataset.kpi];
      el.textContent = (v === undefined || v === null) ? '—' : v;
    }
  },

  /** Barras horizontales para una distribucion {etiqueta: cuenta}. */
  barras(contenedor, distribucion) {
    const el = typeof contenedor === 'string'
      ? document.getElementById(contenedor) : contenedor;
    const entradas = Object.entries(distribucion || {})
      .sort((a, b) => b[1] - a[1]);
    if (!entradas.length) {
      el.innerHTML = '<div class="vacio">sin datos todavía</div>';
      return;
    }
    const max = Math.max(...entradas.map(([, n]) => n));
    el.innerHTML = entradas.map(([nombre, n]) => `
      <div class="barra">
        <span>${UI.esc(nombre)}</span>
        <div class="pista"><div class="relleno" style="width:${(100 * n / max).toFixed(1)}%"></div></div>
        <span class="cifra">${n}</span>
      </div>`).join('');
  },

  /** Tabla a partir de filas [{...}] y columnas [[clave, titulo, formato?]]. */
  tabla(contenedor, filas, columnas) {
    const el = typeof contenedor === 'string'
      ? document.getElementById(contenedor) : contenedor;
    if (!filas.length) {
      el.innerHTML = '<div class="vacio">nada que mostrar todavía</div>';
      return;
    }
    const cab = columnas.map(([, t]) => `<th>${UI.esc(t)}</th>`).join('');
    const cuerpo = filas.map(f => '<tr>' + columnas.map(([c, , fmt]) => {
      const v = f[c];
      return `<td>${fmt ? fmt(v, f) : UI.esc(v ?? '—')}</td>`;
    }).join('') + '</tr>').join('');
    el.innerHTML = `<table><thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody></table>`;
  },

  /** Grilla de mapas de calor con HISTÓRICO por horas desplegable.
   *  `mapas` = filas de /api/v1/heatmaps (ya filtradas al dominio). El botón
   *  «histórico» de cada cámara abre su galería de horas debajo; el estado
   *  abierto sobrevive al refresco periódico de la página. */
  heatmaps(contenedor, mapas) {
    const el = typeof contenedor === 'string'
      ? document.getElementById(contenedor) : contenedor;
    if (!mapas.length) {
      el.innerHTML = '<div class="vacio">sin mapas de calor todavía — se generan solos mientras las cámaras transmiten</div>';
      return;
    }
    el.innerHTML = mapas.map(h => `
      <figure>
        <img src="${h.url}?t=${h.modificado}" alt="mapa de calor ${UI.esc(h.device_id)}" loading="lazy">
        <figcaption>${UI.esc(h.camera_name || h.device_id)}
          ${h.horas_historico ? `<button class="boton btn-historial" data-id="${UI.esc(h.device_id)}">🕐 histórico (${h.horas_historico} h)</button>` : '<small style="color:var(--apagado)"> · histórico: aún sin horas cerradas</small>'}
        </figcaption>
      </figure>`).join('');

    let panel = el.nextElementSibling;
    if (!panel || !panel.classList.contains('historial')) {
      panel = document.createElement('div');
      panel.className = 'historial';
      el.after(panel);
    }
    const abierto = el.dataset.historialAbierto || '';

    async function pintarHistorial(id) {
      const { horas } = await API.leer(`heatmaps/${encodeURIComponent(id)}/historico`);
      panel.innerHTML = `
        <h3>🕐 Histórico de ${UI.esc(id)} <button class="boton btn-cerrar-hist">cerrar</button></h3>
        <div class="heatmaps">${horas.map(x => `
          <figure>
            <img src="${x.url}" alt="${UI.esc(x.stamp)}" loading="lazy">
            <figcaption>${UI.esc(x.stamp.replace('_', ' · '))} h${x.muestras ? ` · ${UI.esc(x.muestras)} muestras` : ''}</figcaption>
          </figure>`).join('') || '<div class="vacio">sin horas guardadas todavía</div>'}
        </div>`;
      panel.querySelector('.btn-cerrar-hist').addEventListener('click', () => {
        el.dataset.historialAbierto = '';
        panel.innerHTML = '';
      });
    }

    for (const btn of el.querySelectorAll('.btn-historial')) {
      btn.addEventListener('click', () => {
        const id = btn.dataset.id;
        if (el.dataset.historialAbierto === id) {
          el.dataset.historialAbierto = '';
          panel.innerHTML = '';
        } else {
          el.dataset.historialAbierto = id;
          pintarHistorial(id).catch(console.error);
        }
      });
    }
    if (abierto && mapas.some(h => h.device_id === abierto)) {
      pintarHistorial(abierto).catch(console.error);
    } else if (abierto) {
      el.dataset.historialAbierto = '';
      panel.innerHTML = '';
    }
  },

  /** Marca de "actualizado hace X s" en el header. */
  refrescado() {
    const el = document.querySelector('.refresco');
    if (el) el.textContent = `actualizado ${new Date().toLocaleTimeString()}`;
  },

  fecha(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? UI.esc(iso) : d.toLocaleString();
  },

  esc(v) {
    return String(v).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  },

  /** Segundos -> "1m51s" (las permanencias de las alertas). */
  duracion(s) {
    if (s === null || s === undefined || isNaN(s)) return '';
    const n = Math.round(Number(s));
    return n < 60 ? `${n}s` : `${Math.floor(n / 60)}m${String(n % 60).padStart(2, '0')}s`;
  },

  /** Espera `ms` sin nuevas llamadas antes de ejecutar (buscadores). */
  debounce(fn, ms = 350) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  },
};

/** Bucle de refresco: pinta ya, y despues cada `segundos`. Un fallo de red no
 *  rompe el bucle: se muestra y se reintenta en el siguiente tick. */
function refrescar(fn, segundos = 10) {
  const tick = async () => {
    try {
      await fn();
      UI.refrescado();
      document.body.classList.remove('sin-conexion');
    } catch (e) {
      console.error(e);
      const el = document.querySelector('.refresco');
      if (el) el.textContent = `sin conexión con la API (${e.message})`;
    }
  };
  tick();
  setInterval(tick, segundos * 1000);
}
