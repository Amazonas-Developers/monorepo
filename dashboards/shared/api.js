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
