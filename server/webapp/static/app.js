// ELDE Dashboard - lógica del frontend
'use strict';

const REFRESH_INTERVAL_MS = 3000;
const DUPLICATE_CHECK_INTERVAL_MS = 15000;  // chequear cada 15s
const DUPLICATE_CHECK_THRESHOLD = 0.35;
const state = {
    autoRefresh: true,
    knownUuids: new Set(),
    selectionMode: false,
    selectedUuids: new Set(),
    bannerDismissed: false,
    filters: {
        gender: '',
        sort: 'last_seen',
        order: 'desc',
        min_confidence: 0.0,
    },
};

// ── DOM refs ────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const grid = $('personsGrid');
const statusDot = $('statusDot');
const statusText = $('statusText');
const lastUpdate = $('lastUpdate');
const dbPathEl = $('dbPath');
const dbModifiedEl = $('dbModified');
const resultCountEl = $('resultCount');

// ── Helpers ─────────────────────────────────────────────────
function setStatus(state, msg) {
    statusDot.className = 'status-indicator ' + state;
    statusText.textContent = msg;
}

function formatRelative(ts) {
    if (!ts) return '-';
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return `hace ${Math.floor(diff)}s`;
    if (diff < 3600) return `hace ${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `hace ${Math.floor(diff / 3600)}h`;
    return `hace ${Math.floor(diff / 86400)}d`;
}

function genderClass(gender) {
    if (gender === 'Hombre') return 'hombre';
    if (gender === 'Mujer') return 'mujer';
    return 'desconocido';
}

// ── Render ──────────────────────────────────────────────────
function renderPersons(persons) {
    resultCountEl.textContent = persons.length;

    if (persons.length === 0) {
        grid.innerHTML = `
            <div class="empty">
                <div class="empty-icon">👻</div>
                <div>Aún no se han reconocido rostros</div>
                <div style="font-size:12px; margin-top:8px; color:var(--text-muted)">
                    El dashboard se actualiza automáticamente cuando aparezcan personas
                </div>
            </div>`;
        return;
    }

    const html = persons.map(p => {
        const gClass = genderClass(p.gender);
        const conf = (p.demo_confidence * 100).toFixed(0);
        const isNew = !state.knownUuids.has(p.uuid);
        if (isNew) state.knownUuids.add(p.uuid);
        const newClass = isNew ? ' new' : '';

        const imgSrc = p.face_image_available
            ? `/api/faces/${p.uuid}.jpg`
            : '';
        const imgHtml = imgSrc
            ? `<img src="${imgSrc}" alt="Rostro" loading="lazy"
                   onerror="this.parentNode.innerHTML='<span class=&quot;no-image&quot;>👤</span>'">`
            : `<span class="no-image">👤</span>`;

        const ageDisplay = p.age_range !== 'Desconocido'
            ? p.age_range
            : '—';

        const isSelected = state.selectedUuids.has(p.uuid);
        const selectedClass = isSelected ? ' selected' : '';

        return `
        <div class="person-card${newClass}${selectedClass}" data-uuid="${p.uuid}">
            <div class="person-face">
                ${imgHtml}
                <button class="select-check" data-action="toggle-select"
                        title="Seleccionar">✓</button>
                <span class="gender-badge ${gClass}">${p.gender}</span>
                ${p.visit_count > 1
                    ? `<span class="visit-badge">×${p.visit_count}</span>`
                    : ''}
                <button class="delete-btn" data-action="delete-one"
                        title="Borrar esta persona">×</button>
            </div>
            <div class="person-info">
                <div class="person-id">${p.short_uuid}</div>
                <div class="person-demo">
                    <span class="person-age">${ageDisplay}</span>
                    <span class="person-conf">${conf}%</span>
                </div>
                <div class="person-times">
                    <span><span class="label">Vista:</span> ${formatRelative(p.last_seen)}</span>
                    <span><span class="label">Primera:</span> ${p.first_seen_str.split(' ')[1] || '-'}</span>
                </div>
            </div>
        </div>`;
    }).join('');

    grid.innerHTML = html;
    updateSelectionUI();
}

// ── Selection mode & delete actions ─────────────────────────
function setSelectionMode(enabled) {
    state.selectionMode = enabled;
    document.body.classList.toggle('selection-mode', enabled);
    $('selectionBar').classList.toggle('active', enabled);
    $('btnSelectionMode').classList.toggle('active', enabled);
    if (!enabled) {
        state.selectedUuids.clear();
        document.querySelectorAll('.person-card.selected')
            .forEach(c => c.classList.remove('selected'));
    }
    updateSelectionUI();
}

function updateSelectionUI() {
    const n = state.selectedUuids.size;
    $('selectedCount').textContent = n;
    $('btnDeleteSelected').disabled = (n === 0);
    // Merge requiere al menos 2 personas seleccionadas
    $('btnMergeSelected').disabled = (n < 2);
}

function toggleCardSelection(uuid) {
    if (state.selectedUuids.has(uuid)) {
        state.selectedUuids.delete(uuid);
    } else {
        state.selectedUuids.add(uuid);
    }
    const card = document.querySelector(`.person-card[data-uuid="${uuid}"]`);
    if (card) card.classList.toggle('selected');
    updateSelectionUI();
}

function selectAll() {
    document.querySelectorAll('.person-card').forEach(card => {
        const uuid = card.dataset.uuid;
        if (uuid) {
            state.selectedUuids.add(uuid);
            card.classList.add('selected');
        }
    });
    updateSelectionUI();
}

function selectNone() {
    state.selectedUuids.clear();
    document.querySelectorAll('.person-card.selected')
        .forEach(c => c.classList.remove('selected'));
    updateSelectionUI();
}

// ── Confirmation modal ──────────────────────────────────────
function showConfirm(title, message, onConfirm) {
    $('confirmTitle').textContent = title;
    $('confirmMessage').innerHTML = message;
    const modal = $('confirmModal');
    modal.classList.add('active');
    const okBtn = $('confirmOk');
    const cancelBtn = $('confirmCancel');
    const cleanup = () => {
        modal.classList.remove('active');
        okBtn.replaceWith(okBtn.cloneNode(true));
        cancelBtn.replaceWith(cancelBtn.cloneNode(true));
    };
    okBtn.onclick = () => { cleanup(); onConfirm(); };
    cancelBtn.onclick = cleanup;
}

// ── Toast notifications ─────────────────────────────────────
function toast(msg, type = 'info', duration = 3000) {
    const el = $('toast');
    el.textContent = msg;
    el.className = `toast ${type} show`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
        el.classList.remove('show');
    }, duration);
}

// ── Delete operations ───────────────────────────────────────
async function deletePerson(uuid) {
    try {
        const res = await fetch(`/api/persons/${uuid}`,
                                { method: 'DELETE' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        state.knownUuids.delete(uuid);
        state.selectedUuids.delete(uuid);
        toast(`Persona ${uuid.slice(0, 8)} borrada`, 'success');
        refresh();
    } catch (e) {
        toast(`Error borrando: ${e.message}`, 'error', 5000);
    }
}

async function deleteSelected() {
    if (state.selectedUuids.size === 0) return;
    const uids = Array.from(state.selectedUuids);
    try {
        const res = await fetch('/api/persons/delete-batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ uids }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        toast(`${data.deleted_count} persona(s) borradas`, 'success');
        state.selectedUuids.clear();
        uids.forEach(u => state.knownUuids.delete(u));
        setSelectionMode(false);
        refresh();
    } catch (e) {
        toast(`Error: ${e.message}`, 'error', 5000);
    }
}

async function mergeSelected() {
    if (state.selectedUuids.size < 2) return;
    const uids = Array.from(state.selectedUuids);
    // Por simplicidad: el primero seleccionado es el primary y se
    // conservan sus demograficos, pero el endpoint elige los del
    // que tenga mayor confianza demo.
    const primary = uids[0];
    const secondary = uids.slice(1);
    try {
        const res = await fetch('/api/persons/merge', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ primary, secondary }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        toast(
            `Combinadas ${data.merged_count + 1} personas en una ` +
            `(${data.total_visits} visitas, ${data.total_embeddings} embeddings)`,
            'success', 4000
        );
        state.selectedUuids.clear();
        secondary.forEach(u => state.knownUuids.delete(u));
        setSelectionMode(false);
        refresh();
    } catch (e) {
        toast(`Error combinando: ${e.message}`, 'error', 5000);
    }
}

// ── Find duplicates ─────────────────────────────────────────
async function openDuplicates() {
    $('dupModal').classList.add('active');
    await scanDuplicates();
}

function closeDuplicates() {
    $('dupModal').classList.remove('active');
}

async function scanDuplicates() {
    const minSim = parseFloat($('dupMinSim').value);
    $('dupMinSimVal').textContent = minSim.toFixed(2);
    $('dupList').innerHTML = '<div class="loading">Escaneando...</div>';
    try {
        const res = await fetch(
            `/api/persons/duplicates?min_similarity=${minSim}&max_pairs=50`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderDuplicates(data.pairs, data.count);
    } catch (e) {
        $('dupList').innerHTML =
            `<div class="dup-empty">Error: ${e.message}</div>`;
    }
}

function renderDuplicates(pairs, count) {
    $('dupCount').textContent = `${count} par(es) encontrados`;
    if (pairs.length === 0) {
        $('dupList').innerHTML = `
            <div class="dup-empty">
                <div style="font-size:48px; margin-bottom:12px;">✨</div>
                <div>Sin duplicados detectados con este umbral.</div>
                <div style="font-size:11px; margin-top:8px;">
                    Prueba bajar la similitud mínima para ver más candidatos.
                </div>
            </div>`;
        return;
    }
    const html = pairs.map(p => {
        const faceA = p.face_a
            ? `<img src="/api/faces/${p.uid_a}.jpg" alt="">`
            : `<span class="no-image">👤</span>`;
        const faceB = p.face_b
            ? `<img src="/api/faces/${p.uid_b}.jpg" alt="">`
            : `<span class="no-image">👤</span>`;
        return `
        <div class="dup-pair" data-uid-a="${p.uid_a}" data-uid-b="${p.uid_b}">
            <div class="dup-pair-face">
                ${faceA}
                <div class="dup-pair-info">
                    <div class="id">${p.short_a}</div>
                    <div class="demo">${p.gender_a} · ${p.age_range_a}</div>
                    <div class="visits">${p.visits_a} visita(s)</div>
                </div>
            </div>
            <div class="dup-pair-sim">
                <span>${(p.similarity * 100).toFixed(0)}%</span>
                <small>similitud</small>
            </div>
            <div class="dup-pair-face">
                ${faceB}
                <div class="dup-pair-info">
                    <div class="id">${p.short_b}</div>
                    <div class="demo">${p.gender_b} · ${p.age_range_b}</div>
                    <div class="visits">${p.visits_b} visita(s)</div>
                </div>
            </div>
            <div class="dup-pair-actions">
                <button class="btn btn-accent btn-small" data-action="merge-pair"
                        title="Combinar como la misma persona">
                    <span>🔗</span> Combinar
                </button>
                <button class="btn btn-secondary btn-small" data-action="ignore-pair"
                        title="No son la misma persona">
                    Ignorar
                </button>
            </div>
        </div>`;
    }).join('');
    $('dupList').innerHTML = html;
}

async function autoMergeAll() {
    const minSim = parseFloat($('dupMinSim').value);
    showConfirm(
        '¿Combinar todos los duplicados automáticamente?',
        `Se fusionarán todos los pares con similitud ≥ <strong>${(minSim * 100).toFixed(0)}%</strong>. ` +
        `Esta acción es <strong>irreversible</strong>. Si dos personas distintas ` +
        `tienen similitud alta por coincidencia, se combinarán erróneamente.`,
        async () => {
            try {
                const res = await fetch('/api/persons/auto-merge', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ min_similarity: minSim }),
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                toast(
                    `Auto-merge: ${data.groups_merged} grupos, ` +
                    `${data.persons_absorbed} personas absorbidas`,
                    'success', 5000
                );
                await scanDuplicates();
                refresh();
            } catch (e) {
                toast(`Error: ${e.message}`, 'error', 5000);
            }
        }
    );
}

async function mergePair(uidA, uidB) {
    try {
        const res = await fetch('/api/persons/merge', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ primary: uidA, secondary: [uidB] }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        toast(
            `Combinadas en una persona (${data.total_visits} visitas, ${data.total_embeddings} embeddings)`,
            'success', 4000
        );
        state.knownUuids.delete(uidB);
        // Quitar la card del modal
        document.querySelector(
            `.dup-pair[data-uid-a="${uidA}"][data-uid-b="${uidB}"]`
        )?.remove();
        refresh();
    } catch (e) {
        toast(`Error: ${e.message}`, 'error', 5000);
    }
}

async function deleteAll() {
    try {
        const res = await fetch('/api/persons', { method: 'DELETE' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        toast(`${data.deleted_count} persona(s) eliminadas`, 'success');
        state.knownUuids.clear();
        state.selectedUuids.clear();
        setSelectionMode(false);
        refresh();
    } catch (e) {
        toast(`Error: ${e.message}`, 'error', 5000);
    }
}

function renderStats(stats) {
    $('statTotal').textContent = stats.total_unique;
    $('statToday').textContent = stats.persons_today;
    $('statVisits').textContent = stats.total_visits;
    $('statMen').textContent = stats.by_gender['Hombre'] || 0;
    $('statWomen').textContent = stats.by_gender['Mujer'] || 0;
    $('statUnknown').textContent = stats.by_gender['Desconocido'] || 0;
    if (stats.db_last_modified) {
        dbModifiedEl.textContent = stats.db_last_modified;
    }
}

// ── Fetch ───────────────────────────────────────────────────
async function fetchPersons() {
    const params = new URLSearchParams({
        sort: state.filters.sort,
        order: state.filters.order,
        gender: state.filters.gender,
        min_confidence: state.filters.min_confidence,
    });
    const res = await fetch(`/api/persons?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function fetchStats() {
    const res = await fetch('/api/stats');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function refresh() {
    try {
        const [personsData, statsData] = await Promise.all([
            fetchPersons(),
            fetchStats(),
        ]);
        renderPersons(personsData.persons);
        renderStats(statsData);
        const now = new Date();
        lastUpdate.textContent = now.toLocaleTimeString();
        setStatus('connected', 'Conectado');
    } catch (e) {
        console.error(e);
        setStatus('error', 'Error: ' + e.message);
    }
}

// ── Modal ───────────────────────────────────────────────────
function openModal(uuid) {
    fetch(`/api/persons/${uuid}`)
        .then(r => r.json())
        .then(p => {
            $('modalDelete').dataset.uuid = p.uuid;
            $('modalEditBtn').dataset.uuid = p.uuid;
            $('modalImage').src = p.face_image_available
                ? `/api/faces/${p.uuid}.jpg`
                : '/static/no-face.png';
            // Preseleccionar valores actuales en el editor
            $('editGender').value = p.gender || 'Desconocido';
            $('editAgeRange').value = p.age_range || 'Desconocido';
            // Ocultar editor (se muestra al click en Editar)
            $('modalEdit').style.display = 'none';
            const manualBadge = p.manual_override
                ? '<span class="manual-badge">Manual</span>'
                : '';
            $('modalInfo').innerHTML = `
                <dl>
                    <dt>UUID completo</dt><dd>${p.uuid}</dd>
                    <dt>Género</dt><dd>${p.gender}${manualBadge}</dd>
                    <dt>Rango edad</dt><dd>${p.age_range}${manualBadge}</dd>
                    <dt>Edad estimada</dt><dd>${p.age_value.toFixed(1)} años</dd>
                    <dt>Confianza</dt><dd>${(p.demo_confidence * 100).toFixed(1)}%</dd>
                    <dt>Visitas</dt><dd>${p.visit_count}</dd>
                    <dt>Primera visita</dt><dd>${p.first_seen_str}</dd>
                    <dt>Última visita</dt><dd>${p.last_seen_str}</dd>
                </dl>`;
            $('modal').classList.add('active');
        })
        .catch(e => console.error(e));
}

async function deepAnalyzeOne(uuid) {
    const btn = $('modalDeepAnalyze');
    const origText = btn.querySelector('span:last-child').textContent;
    btn.disabled = true;
    btn.querySelector('span:last-child').textContent = 'Analizando...';
    try {
        const res = await fetch(`/api/persons/${uuid}/deep-analyze`,
                                { method: 'POST' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({detail: 'error'}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        const a = data.analysis;
        let msg = `${a.gender} ${a.age_range} (conf ${(a.confidence * 100).toFixed(0)}%)`;
        if (a.gender === 'Desconocido') {
            msg += ` — ${a.reason}`;
            toast(msg, 'info', 5000);
        } else if (data.updated) {
            toast(`Actualizado: ${msg}`, 'success', 4000);
        } else {
            toast(`Análisis: ${msg} (no se actualizó, ya estaba mejor)`,
                  'info', 4000);
        }
        closeModal();
        refresh();
    } catch (e) {
        toast(`Error: ${e.message}`, 'error', 5000);
    } finally {
        btn.disabled = false;
        btn.querySelector('span:last-child').textContent = origText;
    }
}

async function deepAnalyzeUnknown() {
    showConfirm(
        '¿Re-analizar todas las personas Desconocido?',
        'El análisis profundo usa <strong>heavy TTA</strong> (12 variantes ' +
        'por foto) + cross-check con Caffe. Tarda <strong>1-3 segundos por ' +
        'persona</strong>. Solo se actualizan las que el análisis pueda ' +
        'determinar con confianza ≥65%.',
        async () => {
            const btn = $('btnAnalyzeUnknown');
            const origLabel = btn.querySelector('.btn-label').textContent;
            btn.disabled = true;
            btn.querySelector('.btn-label').textContent = 'Analizando...';
            try {
                const res = await fetch('/api/persons/analyze-unknown',
                                        { method: 'POST' });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const d = await res.json();
                toast(
                    `${d.analyzed} analizadas · ${d.updated} actualizadas · ` +
                    `${d.no_image} sin foto · ${d.failed} fallidas`,
                    d.updated > 0 ? 'success' : 'info',
                    6000
                );
                refresh();
            } catch (e) {
                toast(`Error: ${e.message}`, 'error', 5000);
            } finally {
                btn.disabled = false;
                btn.querySelector('.btn-label').textContent = origLabel;
            }
        }
    );
}

async function savePersonEdit(uuid) {
    const gender = $('editGender').value;
    const ageRange = $('editAgeRange').value;
    try {
        const res = await fetch(`/api/persons/${uuid}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ gender, age_range: ageRange }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({detail: 'error'}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        toast(`Corregido: ${gender}, ${ageRange}`, 'success');
        closeModal();
        refresh();
    } catch (e) {
        toast(`Error: ${e.message}`, 'error', 5000);
    }
}

function closeModal() {
    $('modal').classList.remove('active');
}

// ── Event listeners ─────────────────────────────────────────
grid.addEventListener('click', (e) => {
    const card = e.target.closest('.person-card');
    if (!card || !card.dataset.uuid) return;
    const uuid = card.dataset.uuid;
    const action = e.target.dataset.action;

    // Boton × individual
    if (action === 'delete-one') {
        e.stopPropagation();
        showConfirm(
            '¿Borrar esta persona?',
            `Se eliminará el registro y la imagen del rostro de
             <code>${uuid.slice(0, 8)}</code>. Esta acción no se puede deshacer.`,
            () => deletePerson(uuid)
        );
        return;
    }

    // Click en el checkbox de seleccion
    if (action === 'toggle-select') {
        e.stopPropagation();
        toggleCardSelection(uuid);
        return;
    }

    // En modo seleccion, todo el card toggle-ea
    if (state.selectionMode) {
        toggleCardSelection(uuid);
        return;
    }

    // Click normal -> abre modal
    openModal(uuid);
});

$('modalClose').addEventListener('click', closeModal);
$('modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') closeModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
        $('confirmModal').classList.remove('active');
    }
});

// Botones de la topbar
$('btnDownloadPdf').addEventListener('click', async () => {
    const btn = $('btnDownloadPdf');
    const origText = btn.querySelector('.btn-label').textContent;
    btn.disabled = true;
    btn.querySelector('.btn-label').textContent = 'Generando...';
    try {
        const res = await fetch('/api/report/pdf');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        // Extraer filename del header si está
        const cd = res.headers.get('Content-Disposition') || '';
        const match = cd.match(/filename="([^"]+)"/);
        a.download = match ? match[1] : 'reporte.pdf';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        toast('PDF descargado', 'success');
    } catch (e) {
        toast(`Error: ${e.message}`, 'error', 5000);
    } finally {
        btn.disabled = false;
        btn.querySelector('.btn-label').textContent = origText;
    }
});

$('btnFindDuplicates').addEventListener('click', openDuplicates);

$('dupModalClose').addEventListener('click', closeDuplicates);
$('dupModal').addEventListener('click', (e) => {
    if (e.target.id === 'dupModal') closeDuplicates();
});

$('btnRescan').addEventListener('click', scanDuplicates);
$('dupMinSim').addEventListener('input', (e) => {
    $('dupMinSimVal').textContent = parseFloat(e.target.value).toFixed(2);
});
$('dupMinSim').addEventListener('change', scanDuplicates);

// Acciones dentro del modal de duplicados
$('dupList').addEventListener('click', (e) => {
    const pair = e.target.closest('.dup-pair');
    if (!pair) return;
    const action = e.target.dataset.action ||
                   e.target.closest('[data-action]')?.dataset.action;
    if (action === 'merge-pair') {
        mergePair(pair.dataset.uidA, pair.dataset.uidB);
    } else if (action === 'ignore-pair') {
        pair.remove();
    }
});

$('btnSelectionMode').addEventListener('click', () => {
    setSelectionMode(!state.selectionMode);
});

$('btnDeleteAll').addEventListener('click', () => {
    showConfirm(
        '¿Borrar TODAS las personas?',
        'Se eliminarán <strong>todas</strong> las personas registradas y ' +
        'sus imágenes. Los contadores de visitas se reiniciarán. ' +
        'Esta acción no se puede deshacer.',
        deleteAll
    );
});

// Botones de la selection bar
$('btnSelectAll').addEventListener('click', selectAll);
$('btnSelectNone').addEventListener('click', selectNone);
$('btnMergeSelected').addEventListener('click', () => {
    const n = state.selectedUuids.size;
    if (n < 2) return;
    showConfirm(
        `¿Combinar ${n} fotos como la misma persona?`,
        `Los registros se fusionarán en uno solo. Las visitas se sumarán y ` +
        `los embeddings se preservarán para que <strong>esta persona ya no se ` +
        `duplique</strong> en futuras detecciones. Esta acción no se puede ` +
        `deshacer.`,
        mergeSelected
    );
});

$('btnDeleteSelected').addEventListener('click', () => {
    const n = state.selectedUuids.size;
    if (n === 0) return;
    showConfirm(
        `¿Borrar ${n} persona(s)?`,
        `Se eliminarán <strong>${n}</strong> registros y sus imágenes. ` +
        'Esta acción no se puede deshacer.',
        deleteSelected
    );
});

// Modal de confirmacion: click fuera para cerrar
$('confirmModal').addEventListener('click', (e) => {
    if (e.target.id === 'confirmModal') {
        $('confirmModal').classList.remove('active');
    }
});

// Modal de detalle: boton de borrar
$('modalDelete').addEventListener('click', () => {
    const uuid = $('modalDelete').dataset.uuid;
    if (!uuid) return;
    closeModal();
    showConfirm(
        '¿Borrar esta persona?',
        `Se eliminará el registro y la imagen del rostro de
         <code>${uuid.slice(0, 8)}</code>. Esta acción no se puede deshacer.`,
        () => deletePerson(uuid)
    );
});

// Modal de detalle: analisis profundo
$('modalDeepAnalyze').addEventListener('click', () => {
    const uuid = $('modalEditBtn').dataset.uuid;
    if (!uuid) return;
    deepAnalyzeOne(uuid);
});

// Topbar: re-analizar bulk de Desconocidos
$('btnAnalyzeUnknown').addEventListener('click', deepAnalyzeUnknown);

// Modal de detalle: boton Editar
$('modalEditBtn').addEventListener('click', () => {
    $('modalEdit').style.display = 'block';
});

$('editCancel').addEventListener('click', () => {
    $('modalEdit').style.display = 'none';
});

$('editSave').addEventListener('click', () => {
    const uuid = $('modalEditBtn').dataset.uuid;
    if (!uuid) return;
    savePersonEdit(uuid);
});

$('autoRefresh').addEventListener('change', (e) => {
    state.autoRefresh = e.target.checked;
});

$('filterGender').addEventListener('change', (e) => {
    state.filters.gender = e.target.value;
    refresh();
});
$('filterSort').addEventListener('change', (e) => {
    state.filters.sort = e.target.value;
    refresh();
});
$('filterOrder').addEventListener('change', (e) => {
    state.filters.order = e.target.value;
    refresh();
});
$('filterMinConf').addEventListener('input', (e) => {
    state.filters.min_confidence = parseFloat(e.target.value);
    $('filterMinConfValue').textContent = state.filters.min_confidence.toFixed(2);
});
$('filterMinConf').addEventListener('change', () => refresh());

// ── Duplicates notification banner ──────────────────────────
async function checkForDuplicates() {
    if (state.bannerDismissed) return;
    try {
        const res = await fetch(
            `/api/persons/duplicates?min_similarity=${DUPLICATE_CHECK_THRESHOLD}&max_pairs=1`
        );
        if (!res.ok) return;
        const data = await res.json();
        const banner = $('duplicatesBanner');
        if (data.count > 0) {
            $('bannerCount').textContent = data.count;
            banner.style.display = 'flex';
        } else {
            banner.style.display = 'none';
        }
    } catch (e) {
        // Silencioso si falla
    }
}

$('bannerOpenBtn').addEventListener('click', () => {
    // Abrir modal de duplicados con threshold del banner
    $('dupMinSim').value = DUPLICATE_CHECK_THRESHOLD;
    $('dupMinSimVal').textContent = DUPLICATE_CHECK_THRESHOLD.toFixed(2);
    openDuplicates();
});
$('bannerCloseBtn').addEventListener('click', () => {
    $('duplicatesBanner').style.display = 'none';
    state.bannerDismissed = true;
    // Reset dismissed status tras 5 minutos
    setTimeout(() => { state.bannerDismissed = false; }, 5 * 60 * 1000);
});

$('btnAutoMerge').addEventListener('click', autoMergeAll);

// ── Bootstrap ───────────────────────────────────────────────
dbPathEl.textContent = 'output/person_db/persons.pkl';
refresh();
checkForDuplicates();
setInterval(() => {
    if (state.autoRefresh) refresh();
}, REFRESH_INTERVAL_MS);
setInterval(checkForDuplicates, DUPLICATE_CHECK_INTERVAL_MS);
