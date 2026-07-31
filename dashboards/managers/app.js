/* dashboards/managers/app.js — vista de OPERACIÓN global (los cuatro dominios).
 *
 * Lo que un encargado necesita de un vistazo: si el servidor está sano
 * (contrato, registro, WhatsApp), qué cámaras transmiten y cuáles callaron,
 * y los últimos eventos de cada dominio. Los accesos a los demás dashboards
 * van arriba, en la barra de paneles.
 */
'use strict';

const DOMINIOS = {
  tienda: '🛒 Tienda', perimetrales: '🛡️ Perimetrales',
  amazonas: '📹 Amazonas', managers: '🧭 Managers',
};

function pill(clase, texto) { return `<span class="pill ${clase}">${UI.esc(texto)}</span>`; }

function estadoCamara(ultimaVez) {
  const hace = Date.now() - new Date(ultimaVez).getTime();
  if (isNaN(hace)) return pill('', 'sin datos');
  if (hace < 3600e3) return pill('ok', 'activa');
  return pill('aviso', `callada desde ${UI.fecha(ultimaVez)}`);
}

async function pintar() {
  const hoy = new Date();
  const dia = [hoy.getFullYear(), String(hoy.getMonth() + 1).padStart(2, '0'),
    String(hoy.getDate()).padStart(2, '0')].join('-');
  const [{ contrato, registro, whatsapp }, dispositivos, alertasHoy,
         ultimasAlertas, capturas] = await Promise.all([
    API.leer('estado'),
    API.leer('dispositivos'),
    API.leer(`alertas?limite=1&desde=${dia}&hasta=${dia}`),
    API.leer('alertas?limite=6'),
    API.leer('capturas?limite=6'),
  ]);

  const filas = dispositivos.dispositivos;
  const activas = filas.filter(d =>
    Date.now() - new Date(d.ultima_vez).getTime() < 3600e3).length;
  UI.kpis({
    camaras: dispositivos.total,
    activas,
    modo: contrato.modo,
    problemas: `${contrato.mensajes_con_problema_pct ?? '—'} %`,
    pipelines: `${(contrato.pipelines_observados || []).length} / ${contrato.pipelines_totales}`,
    alertasHoy: alertasHoy.total,
  });

  document.getElementById('salud-contrato').innerHTML = `
    <h2>Contrato</h2>
    <p>${contrato.sin_errores ? pill('ok', 'sin errores') : pill('error', 'con errores')}
       modo <b>${UI.esc(contrato.modo)}</b> · ${UI.esc(contrato.mensajes_con_problema_pct ?? '—')} % con problema</p>
    <p class="vacio">pipelines vistos: ${UI.esc((contrato.pipelines_observados || []).join(', ') || 'ninguno')}</p>`;

  document.getElementById('salud-registro').innerHTML = `
    <h2>Registro de dispositivos</h2>
    <p>${registro.ids_inestables ? pill('error', `${registro.ids_inestables} ids inestables`) : pill('ok', 'ids estables')}
       ${UI.esc(registro.dispositivos)} dispositivo(s) en ${UI.esc(registro.sitios)} sitio(s)</p>`;

  const wa = whatsapp || {};
  document.getElementById('salud-whatsapp').innerHTML = `
    <h2>Reenvío a WhatsApp</h2>
    <p>${wa.disponible ? pill('ok', 'disponible') : pill('aviso', 'no disponible')}
       enviados <b>${UI.esc(wa.enviados ?? '—')}</b> ·
       antiflood ${UI.esc(wa.descartados_antiflood ?? '—')} ·
       ${(wa.fallidos ?? 0) > 0 ? pill('error', `${wa.fallidos} fallidos`) : 'fallidos 0'}</p>`;

  UI.tabla('dispositivos', filas, [
    ['client_type', 'dominio', v => UI.esc(DOMINIOS[v] || v || '—')],
    ['device_id', 'dispositivo'],
    ['camera_name', 'cámara'],
    ['site_id', 'sitio'],
    ['pipelines', 'pipelines', v => UI.esc((v || []).join(', ') || '—')],
    ['frames', 'frames'],
    ['ultima_vez', 'estado', v => estadoCamara(v)],
  ]);

  UI.tabla('ultimas-alertas', ultimasAlertas.alertas, [
    ['clase', 'clase'],
    ['evento', 'evento'],
    ['camara', 'cámara'],
    ['timestamp', 'cuándo'],
  ]);
  UI.tabla('ultimas-capturas', capturas.capturas || [], [
    ['gender', 'género', v => UI.esc(v || '—')],
    ['age_range', 'edad', v => UI.esc(v || '—')],
    ['camera', 'cámara'],
    ['timestamp', 'cuándo'],
  ]);
}

async function pintarPaneles() {
  const enlaces = await API.paneles();
  const partes = [
    '<a href="/dashboards/perimetrales/">🛡️ Perimetrales</a>',
    '<a href="/dashboards/tienda/">🛒 Tienda</a>',
    '<a href="/dashboards/amazonas/">📹 Amazonas</a>',
  ];
  if (enlaces.vigilante) partes.push(`<a href="${enlaces.vigilante}" target="_blank" rel="noopener">🚨 Panel de VIGILANTE</a>`);
  if (enlaces.visitantes) partes.push(`<a href="${enlaces.visitantes}" target="_blank" rel="noopener">👥 Analítica de visitantes</a>`);
  document.getElementById('paneles').innerHTML = partes.join('');
}
pintarPaneles().catch(console.error);

refrescar(pintar, 15);
