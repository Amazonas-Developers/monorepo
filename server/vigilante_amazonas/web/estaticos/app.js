/* VIGILANTE-AMAZONAS — lógica del panel (vanilla JS, en español) */
"use strict";

const $ = (sel) => document.querySelector(sel);
let personaActual = null;   // persona abierta en el diálogo de fotos

/* ------------------------------ pestañas ------------------------------ */
document.querySelectorAll(".pestana").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".pestana").forEach((b) => b.classList.remove("activa"));
    btn.classList.add("activa");
    document.querySelectorAll(".vista").forEach((v) => v.classList.add("oculta"));
    $(`#vista-${btn.dataset.vista}`).classList.remove("oculta");
    if (btn.dataset.vista === "alertas") cargarAlertas();
    if (btn.dataset.vista === "estado") cargarEstado();
  });
});

/* ------------------------------- helpers ------------------------------ */
async function api(ruta, opciones = {}) {
  const resp = await fetch(ruta, opciones);
  const datos = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(datos.detail || `Error ${resp.status}`);
  return datos;
}
function avisar(sel, texto, esError = false) {
  const el = $(sel);
  el.textContent = texto;
  el.classList.toggle("error", esError);
  if (texto) setTimeout(() => { el.textContent = ""; }, 6000);
}

/* ------------------------------ personas ------------------------------ */
async function cargarPersonas() {
  const personas = await api("/api/personas");
  const cont = $("#lista-personas");
  cont.innerHTML = personas.length ? "" :
    "<p class='mensaje'>Aún no hay personas registradas.</p>";
  for (const p of personas) {
    const div = document.createElement("div");
    div.className = "tarjeta";
    div.innerHTML = `
      <div class="fila">
        <h3>${p.nombre}</h3>
        <span class="insignia ${p.activo ? p.nivel : "inactivo"}">
          ${p.activo ? p.nivel.toUpperCase() : "INACTIVA"}</span>
      </div>
      <small>${p.descripcion || "Sin descripción"}</small>
      <small>📷 ${p.fotos_rostro} foto(s) de rostro · 👕 ${p.fotos_vestimenta} de vestimenta
        · registrada ${p.creado}</small>
      <div class="acciones">
        <button class="primario" data-accion="fotos">Fotos</button>
        <button data-accion="toggle">${p.activo ? "Desactivar" : "Activar"}</button>
        <button class="peligro" data-accion="borrar">Eliminar</button>
      </div>`;
    div.querySelector("[data-accion=fotos]").onclick = () => abrirFotos(p);
    div.querySelector("[data-accion=toggle]").onclick = async () => {
      await api(`/api/personas/${p.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activo: !p.activo }),
      });
      cargarPersonas();
    };
    div.querySelector("[data-accion=borrar]").onclick = async () => {
      if (!confirm(`¿Eliminar a "${p.nombre}" y todas sus fotos?`)) return;
      await api(`/api/personas/${p.id}`, { method: "DELETE" });
      cargarPersonas();
    };
    cont.appendChild(div);
  }
}

$("#form-persona").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await api("/api/personas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nombre: $("#nombre").value.trim(),
        descripcion: $("#descripcion").value.trim(),
        nivel: $("#nivel").value,
      }),
    });
    avisar("#msg-persona", r.mensaje);
    ev.target.reset();
    cargarPersonas();
  } catch (e) { avisar("#msg-persona", e.message, true); }
});

/* -------------------------------- fotos ------------------------------- */
function abrirFotos(persona) {
  personaActual = persona;
  $("#titulo-fotos").textContent = `Fotos de ${persona.nombre}`;
  $("#msg-fotos").textContent = "";
  cargarMiniaturas();
  $("#dialogo-fotos").showModal();
}
$("#cerrar-fotos").onclick = () => { $("#dialogo-fotos").close(); cargarPersonas(); };

async function cargarMiniaturas() {
  const fotos = await api(`/api/personas/${personaActual.id}/fotos`);
  const cont = $("#miniaturas");
  cont.innerHTML = "";
  for (const f of fotos) {
    const div = document.createElement("div");
    div.className = "miniatura";
    div.innerHTML = `<img src="${f.url}" alt="${f.tipo}">
      <span class="tipo">${f.tipo}</span>
      <button class="quitar" title="Eliminar foto">✕</button>`;
    div.querySelector(".quitar").onclick = async () => {
      await api(`/api/fotos/${f.id}`, { method: "DELETE" });
      cargarMiniaturas();
    };
    cont.appendChild(div);
  }
}

function prepararZona(idZona, tipo) {
  const zona = $(idZona);
  const input = zona.querySelector("input");
  zona.onclick = () => input.click();
  input.onchange = () => subirArchivos(input.files, tipo);
  zona.ondragover = (e) => { e.preventDefault(); zona.classList.add("arrastrando"); };
  zona.ondragleave = () => zona.classList.remove("arrastrando");
  zona.ondrop = (e) => {
    e.preventDefault();
    zona.classList.remove("arrastrando");
    subirArchivos(e.dataTransfer.files, tipo);
  };
}
prepararZona("#zona-rostro", "rostro");
prepararZona("#zona-vestimenta", "vestimenta");

async function subirArchivos(archivos, tipo) {
  for (const archivo of archivos) {
    const fd = new FormData();
    fd.append("archivo", archivo);
    try {
      const r = await api(`/api/personas/${personaActual.id}/fotos/${tipo}`,
                          { method: "POST", body: fd });
      avisar("#msg-fotos", r.mensaje);
    } catch (e) {
      avisar("#msg-fotos", `${archivo.name}: ${e.message}`, true);
    }
  }
  cargarMiniaturas();
}

/* ------------------------------- alertas ------------------------------ */
async function cargarAlertas() {
  const parametros = new URLSearchParams();
  if ($("#f-camara").value) parametros.set("camara", $("#f-camara").value);
  if ($("#f-persona").value) parametros.set("persona", $("#f-persona").value);
  if ($("#f-fecha").value) parametros.set("fecha", $("#f-fecha").value);
  const alertas = await api(`/api/alertas?${parametros}`);
  const cont = $("#lista-alertas");
  cont.innerHTML = alertas.length ? "" :
    "<p class='mensaje'>Sin alertas para los filtros elegidos.</p>";
  for (const a of alertas) {
    const div = document.createElement("div");
    div.className = "tarjeta tarjeta-alerta";
    div.innerHTML = `
      ${a.snapshot_url ? `<img src="${a.snapshot_url}" alt="snapshot">` : ""}
      <div class="fila">
        <h3>${a.persona}</h3>
        <span class="insignia ${a.nivel}">${a.nivel.toUpperCase()}</span>
      </div>
      <div class="meta">📹 ${a.camara} · 🕒 ${a.timestamp}<br>
        score ${Number(a.score).toFixed(2)} · match: ${a.tipo_match}
        · verificación: ${a.verificacion}</div>`;
    cont.appendChild(div);
  }
}
$("#btn-filtrar").onclick = cargarAlertas;
$("#btn-limpiar").onclick = () => {
  $("#f-camara").value = ""; $("#f-persona").value = ""; $("#f-fecha").value = "";
  cargarAlertas();
};

/* -------------------------------- estado ------------------------------ */
function _svc(valor, textoOn, textoOff) {
  if (valor === null || valor === undefined) return "⏳ sin cargar";
  return valor ? textoOn : textoOff;
}

async function cargarEstado(cargarModelos = false) {
  const e = await api(`/api/estado${cargarModelos ? "?cargar_modelos=1" : ""}`);
  const fps = Object.entries(e.fps || {})
    .map(([c, v]) => `${c}: ${v.toFixed(1)}`).join("<br>") || "sin motor propio activo";
  const camaras = (e.camaras || [])
    .map((c) => `${c.nombre} (última vez: ${c.ultima_vista})`).join("<br>") || "ninguna registrada";
  const aviso = e.servicios_cargados ? "" : `
    <div class="estado-item" style="grid-column:1/-1">
      <b>Modelos de IA</b> ⏳ aún no cargados (se cargan solos al subir la
      primera foto o al conectar una cámara).
      <button id="btn-cargar-modelos" style="margin-top:8px">Cargar ahora</button>
    </div>`;
  $("#estado-contenido").innerHTML = `
    ${aviso}
    <div class="estado-item"><b>Galería${e.servicios_cargados ? " en memoria" : " (en base de datos)"}</b>
      ${e.galeria.rostros ?? 0} rostro(s) · ${e.galeria.vestimentas ?? 0} vestimenta(s)</div>
    <div class="estado-item"><b>Clasificador de seguridad</b>
      ${_svc(e.seguridad_disponible, "✅ activo", "❌ no disponible")}</div>
    <div class="estado-item"><b>VLM verificador</b>
      ${_svc(e.vlm_disponible, "✅ activo", "⚠️ no disponible (zona gris degradada)")}</div>
    <div class="estado-item"><b>Emisor de alertas</b>
      ${_svc(e.alertas_disponible, "✅ activo", "❌ no disponible")}</div>
    <div class="estado-item"><b>Backend de vestimenta</b> ${e.vestimenta_backend}</div>
    <div class="estado-item"><b>FPS por cámara</b> ${fps}</div>
    <div class="estado-item"><b>Cámaras vistas</b> ${camaras}</div>`;
  const btn = $("#btn-cargar-modelos");
  if (btn) {
    btn.onclick = () => {
      btn.disabled = true;
      btn.textContent = "Cargando modelos… (puede tardar ~1 min)";
      cargarEstado(true);
    };
  }
}

/* -------------------------------- inicio ------------------------------ */
cargarPersonas();
