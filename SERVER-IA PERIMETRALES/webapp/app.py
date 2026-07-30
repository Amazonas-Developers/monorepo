"""
webapp/app.py - Dashboard de rostros reconocidos.

Lee la base de datos de FaceReidentifier (output/person_db/persons.pkl)
y sirve un dashboard web con tarjetas de cada persona unica detectada,
sus demograficos, conteos de visitas y la foto de su rostro.

Arranque:
  python webapp/app.py

Acceso:
  http://localhost:5000  (LAN: http://<ip-del-server>:5000)

La pagina hace polling a /api/persons cada 3 segundos para refrescar
sin recargar todo el HTML. La DB se lee del disco en cada request -
no compite con el proceso de analytics que la escribe (lectura solo).
"""
import os
import sys
import pickle
import datetime
import logging
import numpy as np
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, Response,
)
from fastapi.staticfiles import StaticFiles

# Permite importar desde src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webapp")

# Paths a la DB y a las fotos
DB_PATH = PROJECT_ROOT / 'output' / 'person_db' / 'persons.pkl'
FACES_DIR = PROJECT_ROOT / 'output' / 'person_db' / 'faces'
# Snapshots del mapa de calor (los escribe el proceso de inferencia)
HEATMAP_DIR = PROJECT_ROOT / 'output' / 'heatmap'
# Capturas (foto por persona + por rostro) que escribe la inferencia
CAPTURES_DIR = PROJECT_ROOT / 'output' / 'captures'
WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / 'static'
TEMPLATES_DIR = WEBAPP_DIR / 'templates'

app = FastAPI(title="ELDE Dashboard - Rostros Reconocidos")

# Servir archivos estaticos (CSS/JS)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)),
              name="static")


def _load_db() -> dict:
    """Lee el pickle de la DB. Devuelve dict vacio si no existe."""
    if not DB_PATH.is_file():
        return {}
    try:
        with open(DB_PATH, 'rb') as f:
            payload = pickle.load(f)
        return payload.get('db', {})
    except Exception as e:
        logger.error(f"Error leyendo DB: {e}")
        return {}


def _save_db(db: dict):
    """Guarda el pickle de la DB conservando otros campos del payload.

    Nota: si el proceso de analytics esta corriendo en paralelo, puede
    haber una mini race condition (su throttle de 5s puede sobrescribir
    el cambio si justo guarda despues). En produccion suele bastar con
    reintentar el borrado si no se persistio.
    """
    try:
        payload = {
            'date': datetime.date.today().isoformat(),
            'db': db,
            'version': 1,
        }
        tmp = DB_PATH.with_suffix('.pkl.tmp')
        with open(tmp, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, DB_PATH)
    except Exception as e:
        logger.error(f"Error guardando DB: {e}")
        raise


def _delete_face_image(uid: str):
    """Borra la imagen del rostro de disco. Silencioso si no existe."""
    face_path = FACES_DIR / f"{uid}.jpg"
    if face_path.is_file():
        try:
            face_path.unlink()
        except OSError as e:
            logger.warning(f"No se pudo borrar {face_path}: {e}")


def _format_timestamp(ts: float) -> str:
    if ts <= 0:
        return "-"
    try:
        dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return "-"


def _format_datetime(ts: float) -> str:
    if ts <= 0:
        return "-"
    try:
        dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _serialize_person(uid: str, rec: dict) -> dict:
    """Convierte un record de DB a JSON serializable."""
    face_path = FACES_DIR / f"{uid}.jpg"
    return {
        'uuid': uid,
        'short_uuid': uid[:8],
        'gender': rec.get('gender') or 'Desconocido',
        'age_range': rec.get('age_range') or 'Desconocido',
        'age_value': float(rec.get('age_value', 0.0)),
        'demo_confidence': float(rec.get('demo_confidence', 0.0)),
        'first_seen': float(rec.get('first_seen', 0.0)),
        'last_seen': float(rec.get('last_seen', 0.0)),
        'first_seen_str': _format_datetime(rec.get('first_seen', 0.0)),
        'last_seen_str': _format_datetime(rec.get('last_seen', 0.0)),
        'visit_count': int(rec.get('visit_count', 1)),
        'face_image_available': face_path.is_file(),
        'manual_override': bool(rec.get('manual_override', False)),
    }


# ── HTML page ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = TEMPLATES_DIR / 'index.html'
    if not index_path.is_file():
        return HTMLResponse("<h1>index.html no encontrado</h1>",
                            status_code=500)
    return HTMLResponse(index_path.read_text(encoding='utf-8'))


# ── API: lista de personas ────────────────────────────────────────

@app.get("/api/persons")
async def api_persons(
    sort: str = "last_seen",
    order: str = "desc",
    gender: str = "",
    min_confidence: float = 0.0,
):
    """Devuelve lista de personas filtrada y ordenada.

    Query params:
      - sort: 'last_seen', 'first_seen', 'visit_count', 'age_value'
      - order: 'asc' | 'desc'
      - gender: 'Hombre' | 'Mujer' | 'Desconocido' | '' (todos)
      - min_confidence: float (filtra demograficos con conf >= valor)
    """
    db = _load_db()
    persons = [_serialize_person(uid, rec) for uid, rec in db.items()]

    # Filtros
    if gender:
        persons = [p for p in persons if p['gender'] == gender]
    if min_confidence > 0:
        persons = [p for p in persons
                   if p['demo_confidence'] >= min_confidence]

    # Sort
    if sort in {'last_seen', 'first_seen', 'visit_count', 'age_value',
                'demo_confidence'}:
        reverse = (order != 'asc')
        persons.sort(key=lambda p: p.get(sort, 0), reverse=reverse)

    return JSONResponse(content={
        "count": len(persons),
        "persons": persons,
    })


# ── API: stats globales ────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats():
    """Estadisticas agregadas para el header del dashboard."""
    db = _load_db()
    total = len(db)
    by_gender: dict = {'Hombre': 0, 'Mujer': 0, 'Desconocido': 0}
    by_age: dict = {}
    total_visits = 0
    persons_with_demo = 0
    latest_ts = 0.0

    for rec in db.values():
        g = rec.get('gender') or 'Desconocido'
        by_gender[g] = by_gender.get(g, 0) + 1
        a = rec.get('age_range') or 'Desconocido'
        by_age[a] = by_age.get(a, 0) + 1
        total_visits += int(rec.get('visit_count', 1))
        if g not in ('Desconocido', None):
            persons_with_demo += 1
        ts = float(rec.get('last_seen', 0.0))
        if ts > latest_ts:
            latest_ts = ts

    today = datetime.date.today()
    today_start = datetime.datetime.combine(
        today, datetime.time.min).timestamp()
    persons_today = sum(
        1 for rec in db.values()
        if float(rec.get('first_seen', 0.0)) >= today_start
    )

    return JSONResponse(content={
        "total_unique": total,
        "total_visits": total_visits,
        "persons_today": persons_today,
        "persons_with_demographics": persons_with_demo,
        "by_gender": by_gender,
        "by_age": by_age,
        "latest_activity": _format_datetime(latest_ts),
        "latest_activity_ts": latest_ts,
        "db_last_modified": (
            _format_datetime(DB_PATH.stat().st_mtime)
            if DB_PATH.is_file() else "-"
        ),
    })


# ── API: foto del rostro ──────────────────────────────────────────

@app.get("/api/faces/{uid}.jpg")
async def api_face_image(uid: str):
    """Devuelve la imagen del rostro guardado.

    Si no existe, devuelve un placeholder 1x1 transparente.
    """
    # Sanitizar uid (solo hex y guiones)
    if not all(c in '0123456789abcdef-' for c in uid):
        raise HTTPException(status_code=400, detail="UID invalido")
    face_path = FACES_DIR / f"{uid}.jpg"
    if not face_path.is_file():
        raise HTTPException(status_code=404, detail="Rostro no encontrado")
    return FileResponse(
        str(face_path), media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=300"}
    )


# ── API: buscar posibles duplicados ──────────────────────────────
# IMPORTANTE: este endpoint debe declararse ANTES de
# /api/persons/{uid} porque FastAPI matchea rutas en orden y
# "duplicates" se interpretaria como un uid.

@app.get("/api/persons/duplicates")
async def api_find_duplicates(
    min_similarity: float = 0.30,
    max_pairs: int = 30,
):
    """Encuentra pares con similitud sospechosa para revision manual.

    Util cuando el auto-match (threshold 0.42) no captura un par porque
    su similitud cayo en la zona gris [0.30, 0.42] (poses muy distintas
    de la misma persona).
    """
    db = _load_db()
    if len(db) < 2:
        return {"count": 0, "pairs": [], "min_similarity": min_similarity}

    uids = list(db.keys())
    pairs = []
    for i in range(len(uids)):
        a_uid = uids[i]
        a_embs = _get_embeddings_compat(db[a_uid])
        if not a_embs:
            continue
        for j in range(i + 1, len(uids)):
            b_uid = uids[j]
            b_embs = _get_embeddings_compat(db[b_uid])
            if not b_embs:
                continue
            best_sim = -1.0
            for ea in a_embs:
                for eb in b_embs:
                    s = _cos_sim(ea, eb)
                    if s > best_sim:
                        best_sim = s
            if best_sim >= min_similarity:
                pairs.append({
                    'uid_a': a_uid,
                    'uid_b': b_uid,
                    'similarity': float(best_sim),
                    'short_a': a_uid[:8],
                    'short_b': b_uid[:8],
                    'gender_a': db[a_uid].get('gender') or 'Desconocido',
                    'gender_b': db[b_uid].get('gender') or 'Desconocido',
                    'age_range_a': db[a_uid].get('age_range') or 'Desconocido',
                    'age_range_b': db[b_uid].get('age_range') or 'Desconocido',
                    'visits_a': int(db[a_uid].get('visit_count', 1)),
                    'visits_b': int(db[b_uid].get('visit_count', 1)),
                    'face_a': (FACES_DIR / f"{a_uid}.jpg").is_file(),
                    'face_b': (FACES_DIR / f"{b_uid}.jpg").is_file(),
                })

    pairs.sort(key=lambda p: p['similarity'], reverse=True)
    return {
        "count": len(pairs),
        "pairs": pairs[:max_pairs],
        "min_similarity": min_similarity,
    }


# ── API: auto-combinar pares de duplicados detectados ────────────

@app.post("/api/persons/auto-merge")
async def api_auto_merge(payload: dict = Body(...)):
    """Fusiona automaticamente todos los pares con similitud >= threshold.

    Algoritmo: union-find. Para cada par (a, b) en pairs:
      - Si a y b ya estan en el mismo grupo: skip
      - Si no: unir sus grupos
    Al final, para cada grupo de 2+ personas: hacer merge_persons en el
    primary (mas visitas) absorbiendo el resto.

    Body: {"min_similarity": 0.35}
    """
    min_sim = float(payload.get('min_similarity', 0.35))
    db = _load_db()
    if len(db) < 2:
        return {"status": "ok", "groups_merged": 0,
                "persons_absorbed": 0}

    uids = list(db.keys())
    # Calcular todas las similitudes >= min_sim
    pairs = []
    for i in range(len(uids)):
        a = uids[i]
        a_embs = _get_embeddings_compat(db[a])
        if not a_embs:
            continue
        for j in range(i + 1, len(uids)):
            b = uids[j]
            b_embs = _get_embeddings_compat(db[b])
            if not b_embs:
                continue
            best = -1.0
            for ea in a_embs:
                for eb in b_embs:
                    s = _cos_sim(ea, eb)
                    if s > best:
                        best = s
            if best >= min_sim:
                pairs.append((a, b, best))

    if not pairs:
        return {"status": "ok", "groups_merged": 0,
                "persons_absorbed": 0}

    # Union-Find
    parent = {u: u for u in uids}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b, _ in pairs:
        union(a, b)

    # Agrupar
    groups = {}
    for u in uids:
        r = find(u)
        groups.setdefault(r, []).append(u)

    # Filtrar grupos con 2+ personas
    merge_groups = [g for g in groups.values() if len(g) > 1]

    persons_absorbed = 0
    groups_merged = 0
    for group in merge_groups:
        # Elegir como primary el de MAS VISITAS, desempate por mas embeddings
        group.sort(
            key=lambda u: (
                db[u].get('visit_count', 1),
                len(_get_embeddings_compat(db[u]))
            ),
            reverse=True
        )
        primary = group[0]
        secondary = group[1:]

        # Hacer el merge inline (reuso de lógica)
        p_rec = db[primary]
        p_embeddings = _get_embeddings_compat(p_rec)
        p_visits = int(p_rec.get('visit_count', 1))
        p_first = float(p_rec.get('first_seen', 0.0))
        p_last = float(p_rec.get('last_seen', 0.0))
        p_conf = float(p_rec.get('demo_confidence', 0.0))

        for sec_uid in secondary:
            sec = db[sec_uid]
            p_embeddings.extend(_get_embeddings_compat(sec))
            p_visits += int(sec.get('visit_count', 1))
            sec_first = float(sec.get('first_seen', p_first))
            sec_last = float(sec.get('last_seen', p_last))
            if sec_first < p_first:
                p_first = sec_first
            if sec_last > p_last:
                p_last = sec_last
            sec_conf = float(sec.get('demo_confidence', 0.0))
            if sec_conf > p_conf:
                p_rec['gender'] = sec.get('gender', p_rec.get('gender'))
                p_rec['age_range'] = sec.get(
                    'age_range', p_rec.get('age_range'))
                p_rec['age_value'] = float(sec.get(
                    'age_value', p_rec.get('age_value', 0.0)))
                p_rec['demo_confidence'] = sec_conf
                p_conf = sec_conf
            _delete_face_image(sec_uid)
            del db[sec_uid]
            persons_absorbed += 1

        if len(p_embeddings) > 5:
            p_embeddings = _diversify(p_embeddings, 5)

        p_rec['embeddings'] = p_embeddings
        p_rec.pop('embedding', None)
        p_rec['embedding_count'] = p_rec.get(
            'embedding_count', 1) + len(secondary)
        p_rec['visit_count'] = p_visits
        p_rec['first_seen'] = p_first
        p_rec['last_seen'] = p_last
        groups_merged += 1

    _save_db(db)
    logger.info(
        f"AUTO-MERGE: {groups_merged} grupos, {persons_absorbed} "
        f"personas absorbidas (min_sim={min_sim})"
    )
    return {
        "status": "ok",
        "groups_merged": groups_merged,
        "persons_absorbed": persons_absorbed,
        "min_similarity_used": min_sim,
    }


# ── API: detalle de una persona ────────────────────────────────────

@app.get("/api/persons/{uid}")
async def api_person_detail(uid: str):
    if not all(c in '0123456789abcdef-' for c in uid):
        raise HTTPException(status_code=404, detail="UID invalido")
    db = _load_db()
    if uid not in db:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    return JSONResponse(content=_serialize_person(uid, db[uid]))


# ── API: editar genero/edad de una persona (override manual) ─────

_VALID_GENDERS = {'Hombre', 'Mujer', 'Desconocido'}
_VALID_AGE_RANGES = {'0-12', '13-17', '18-25', '26-35', '36-50',
                     '51-65', '65+', 'Desconocido'}


@app.patch("/api/persons/{uid}")
async def api_patch_person(uid: str, payload: dict = Body(...)):
    """Edita manualmente los demograficos de una persona.

    Body: {"gender": "Mujer", "age_range": "26-35"}
    Ambos campos son opcionales.

    Aplica un flag 'manual_override=True' para que las futuras detecciones
    automaticas NO sobrescriban esta correccion.
    """
    if not all(c in '0123456789abcdef-' for c in uid):
        raise HTTPException(status_code=400, detail="UID invalido")

    new_gender = payload.get('gender')
    new_age = payload.get('age_range')

    if new_gender is not None and new_gender not in _VALID_GENDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Genero invalido. Valores: {_VALID_GENDERS}"
        )
    if new_age is not None and new_age not in _VALID_AGE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Edad invalida. Valores: {_VALID_AGE_RANGES}"
        )

    db = _load_db()
    if uid not in db:
        raise HTTPException(status_code=404, detail="Persona no encontrada")

    rec = db[uid]
    if new_gender is not None:
        rec['gender'] = new_gender
        # Si manual override, fijar confidence muy alta para que no se
        # sobrescriba con muestras automaticas
        rec['demo_confidence'] = 0.99
    if new_age is not None:
        rec['age_range'] = new_age
        # Sincronizar age_value con el centro del rango
        age_centers = {
            '0-12': 6.0, '13-17': 15.0, '18-25': 21.5, '26-35': 30.0,
            '36-50': 43.0, '51-65': 58.0, '65+': 70.0,
            'Desconocido': 0.0,
        }
        rec['age_value'] = age_centers.get(new_age, 0.0)
    rec['manual_override'] = True
    rec['last_modified'] = datetime.datetime.now().timestamp()

    _save_db(db)
    logger.info(
        f"PATCH {uid[:8]}: gender={new_gender} age={new_age} "
        f"(manual_override)"
    )
    return {
        "status": "ok",
        "uid": uid,
        "gender": rec.get('gender'),
        "age_range": rec.get('age_range'),
        "manual_override": True,
    }


# ── API: borrar persona individual ────────────────────────────────

@app.delete("/api/persons/{uid}")
async def api_delete_person(uid: str):
    # Sanitizar uid
    if not all(c in '0123456789abcdef-' for c in uid):
        raise HTTPException(status_code=400, detail="UID invalido")
    db = _load_db()
    if uid not in db:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    del db[uid]
    _save_db(db)
    _delete_face_image(uid)
    logger.info(f"Borrada persona {uid[:8]}")
    return {"status": "ok", "deleted": uid}


# ── API: borrar multiples personas ────────────────────────────────

@app.post("/api/persons/delete-batch")
async def api_delete_batch(uids: list = Body(..., embed=True)):
    """Borra varias personas en una sola operacion.

    Body: {"uids": ["uuid1", "uuid2", ...]}
    """
    if not isinstance(uids, list):
        raise HTTPException(status_code=400,
                            detail="Body debe ser {uids: [...]}")
    # Sanitizar todos los uids
    for uid in uids:
        if not isinstance(uid, str) or not all(
                c in '0123456789abcdef-' for c in uid):
            raise HTTPException(
                status_code=400, detail=f"UID invalido: {uid}"
            )
    db = _load_db()
    deleted = []
    not_found = []
    for uid in uids:
        if uid in db:
            del db[uid]
            _delete_face_image(uid)
            deleted.append(uid)
        else:
            not_found.append(uid)
    if deleted:
        _save_db(db)
    logger.info(
        f"Borrado batch: {len(deleted)} ok, {len(not_found)} no encontrados"
    )
    return {
        "status": "ok",
        "deleted_count": len(deleted),
        "deleted": deleted,
        "not_found": not_found,
    }


# ── API: combinar (merge) varias personas ────────────────────────

def _get_embeddings_compat(rec: dict) -> list:
    """Devuelve lista de embeddings de un record, sea formato nuevo
    (embeddings: list) o legacy (embedding: single)."""
    if 'embeddings' in rec:
        return list(rec['embeddings'])
    if 'embedding' in rec:
        return [rec['embedding']]
    return []


def _cos_sim(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    an = a / max(np.linalg.norm(a), 1e-9)
    bn = b / max(np.linalg.norm(b), 1e-9)
    return float(np.dot(an, bn))


def _diversify(embeddings: list, k: int) -> list:
    """Reduce embeddings a k manteniendo los mas diversos (farthest-first)."""
    if len(embeddings) <= k:
        return embeddings
    selected = [embeddings[0]]
    remaining = list(embeddings[1:])
    while len(selected) < k and remaining:
        best_idx = 0
        best_min_sim = float('inf')
        for i, c in enumerate(remaining):
            max_sim = max(_cos_sim(c, s) for s in selected)
            if max_sim < best_min_sim:
                best_min_sim = max_sim
                best_idx = i
        selected.append(remaining.pop(best_idx))
    return selected


@app.post("/api/persons/merge")
async def api_merge_persons(payload: dict = Body(...)):
    """Combina varias personas en una.

    Body: {"primary": "uuid", "secondary": ["uuid1", "uuid2", ...]}
    """
    primary = payload.get('primary')
    secondary = payload.get('secondary', [])

    # Sanitizar UUIDs
    all_uids = [primary] + list(secondary)
    for uid in all_uids:
        if not isinstance(uid, str) or not all(
                c in '0123456789abcdef-' for c in uid):
            raise HTTPException(
                status_code=400, detail=f"UID invalido: {uid}"
            )

    db = _load_db()
    if primary not in db:
        raise HTTPException(status_code=404,
                            detail=f"Primary {primary} no encontrado")

    p_rec = db[primary]
    p_embeddings = _get_embeddings_compat(p_rec)
    p_visits = int(p_rec.get('visit_count', 1))
    p_first = float(p_rec.get('first_seen', 0.0))
    p_last = float(p_rec.get('last_seen', 0.0))
    p_conf = float(p_rec.get('demo_confidence', 0.0))

    merged_count = 0
    for sec_uid in secondary:
        if sec_uid == primary or sec_uid not in db:
            continue
        sec = db[sec_uid]
        p_embeddings.extend(_get_embeddings_compat(sec))
        p_visits += int(sec.get('visit_count', 1))
        sec_first = float(sec.get('first_seen', p_first))
        sec_last = float(sec.get('last_seen', p_last))
        if sec_first < p_first:
            p_first = sec_first
        if sec_last > p_last:
            p_last = sec_last
        sec_conf = float(sec.get('demo_confidence', 0.0))
        if sec_conf > p_conf:
            p_rec['gender'] = sec.get('gender', p_rec.get('gender'))
            p_rec['age_range'] = sec.get('age_range',
                                         p_rec.get('age_range'))
            p_rec['age_value'] = float(sec.get(
                'age_value', p_rec.get('age_value', 0.0)))
            p_rec['demo_confidence'] = sec_conf
            p_conf = sec_conf
        # Borrar imagen del secundario
        _delete_face_image(sec_uid)
        # Borrar registro del secundario
        del db[sec_uid]
        merged_count += 1

    # Reducir embeddings a max manteniendo diversidad (default 5)
    MAX_EMB = 5
    if len(p_embeddings) > MAX_EMB:
        p_embeddings = _diversify(p_embeddings, MAX_EMB)

    p_rec['embeddings'] = p_embeddings
    p_rec.pop('embedding', None)
    p_rec['embedding_count'] = p_rec.get('embedding_count', 1) + merged_count
    p_rec['visit_count'] = p_visits
    p_rec['first_seen'] = p_first
    p_rec['last_seen'] = p_last

    _save_db(db)
    logger.info(
        f"MERGE: {primary[:8]} absorbio {merged_count} personas. "
        f"visits={p_visits} embeddings={len(p_embeddings)}"
    )
    return {
        "status": "ok",
        "primary_uid": primary,
        "merged_count": merged_count,
        "total_visits": p_visits,
        "total_embeddings": len(p_embeddings),
    }


# ── API: borrar TODAS las personas ────────────────────────────────

@app.delete("/api/persons")
async def api_delete_all():
    """Borra TODAS las personas y sus imagenes. Operacion destructiva."""
    db = _load_db()
    count = len(db)
    if count == 0:
        return {"status": "ok", "deleted_count": 0}
    # Borrar todas las imagenes
    for uid in db.keys():
        _delete_face_image(uid)
    # Vaciar la DB
    _save_db({})
    logger.warning(f"DELETE ALL: {count} personas eliminadas")
    return {"status": "ok", "deleted_count": count}


# ── API: analisis profundo de cara ────────────────────────────────

# Lazy-load del analyzer (carga modelos ONNX/Caffe la primera vez)
_deep_analyzer = None


def _get_deep_analyzer():
    """Carga e inicializa el analyzer en la primera llamada."""
    global _deep_analyzer
    if _deep_analyzer is not None:
        return _deep_analyzer
    try:
        from deep_analyzer import DeepGenderAgeAnalyzer
        import onnxruntime as ort
        import cv2 as _cv2
        models = PROJECT_ROOT / 'models' / 'classifiers'
        # InsightFace (primario)
        insight = None
        ip = models / 'genderage.onnx'
        if ip.is_file():
            insight = ort.InferenceSession(
                str(ip), providers=['CPUExecutionProvider']
            )
        # Caffe (cross-check)
        caffe_g = caffe_a = None
        gp = models / 'gender_deploy.prototxt'
        gm = models / 'gender_net.caffemodel'
        ap = models / 'age_deploy.prototxt'
        am = models / 'age_net.caffemodel'
        if gp.is_file() and gm.is_file():
            caffe_g = _cv2.dnn.readNet(str(gm), str(gp))
        if ap.is_file() and am.is_file():
            caffe_a = _cv2.dnn.readNet(str(am), str(ap))
        _deep_analyzer = DeepGenderAgeAnalyzer(
            insightface_session=insight,
            caffe_gender_net=caffe_g,
            caffe_age_net=caffe_a,
        )
        logger.info(
            f"DeepAnalyzer cargado: insight={insight is not None} "
            f"caffe_gender={caffe_g is not None} "
            f"caffe_age={caffe_a is not None}"
        )
        return _deep_analyzer
    except Exception as e:
        logger.error(f"No se pudo crear DeepAnalyzer: {e}")
        return None


def _apply_deep_result(db: dict, uid: str, result: dict) -> bool:
    """Aplica el resultado del deep analyzer al record. Returns True
    si se actualizo, False si quedo igual."""
    rec = db.get(uid)
    if rec is None:
        return False
    gender = result.get('gender', 'Desconocido')
    age_range = result.get('age_range', 'Desconocido')
    if gender == 'Desconocido':
        return False
    # Solo actualizar si el resultado es MEJOR que el actual:
    # - Si era Desconocido -> siempre actualizar
    # - Si era otro genero -> actualizar solo si conf alta (>0.80)
    old_gender = rec.get('gender') or 'Desconocido'
    new_conf = float(result.get('confidence', 0.0))
    old_conf = float(rec.get('demo_confidence', 0.0))

    should_update = False
    if old_gender in (None, 'Desconocido'):
        should_update = True
    elif old_gender != gender and new_conf > 0.80:
        should_update = True
    elif old_gender == gender and new_conf > old_conf:
        should_update = True

    if not should_update:
        return False

    rec['gender'] = gender
    rec['age_range'] = age_range
    rec['age_value'] = float(result.get('age_value', 0.0))
    rec['demo_confidence'] = new_conf
    rec['deep_analyzed'] = True
    rec['deep_analyzed_at'] = datetime.datetime.now().timestamp()
    # Marcar como manual_override para que el sistema en vivo NO
    # sobrescriba la decision del analisis profundo
    rec['manual_override'] = True
    return True


@app.post("/api/persons/{uid}/deep-analyze")
async def api_deep_analyze_one(uid: str):
    """Analiza profundamente la foto guardada de UNA persona y actualiza
    sus demograficos si el analisis es confiable."""
    if not all(c in '0123456789abcdef-' for c in uid):
        raise HTTPException(status_code=400, detail="UID invalido")

    face_path = FACES_DIR / f"{uid}.jpg"
    if not face_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No hay imagen de rostro guardada para esta persona"
        )

    analyzer = _get_deep_analyzer()
    if analyzer is None or not analyzer.is_available:
        raise HTTPException(
            status_code=500,
            detail="Deep analyzer no disponible (modelos no cargados)"
        )

    import cv2 as _cv2
    face = _cv2.imread(str(face_path))
    if face is None:
        raise HTTPException(status_code=500,
                            detail="No se pudo leer la imagen")

    result = analyzer.analyze(face)

    db = _load_db()
    updated = _apply_deep_result(db, uid, result)
    if updated:
        _save_db(db)

    return {
        "status": "ok",
        "uid": uid,
        "updated": updated,
        "analysis": result,
    }


@app.post("/api/persons/analyze-unknown")
async def api_deep_analyze_unknown():
    """Analiza profundamente TODAS las personas marcadas como
    'Desconocido' o con demo_confidence < 0.50. Si el analisis es
    confiable, actualiza sus demograficos."""
    analyzer = _get_deep_analyzer()
    if analyzer is None or not analyzer.is_available:
        raise HTTPException(
            status_code=500,
            detail="Deep analyzer no disponible"
        )

    import cv2 as _cv2
    db = _load_db()
    candidates = []
    for uid, rec in db.items():
        g = rec.get('gender') or 'Desconocido'
        conf = float(rec.get('demo_confidence', 0.0))
        if g == 'Desconocido' or conf < 0.50:
            candidates.append(uid)

    analyzed = 0
    updated = 0
    failed = 0
    no_image = 0
    results_detail = []

    for uid in candidates:
        face_path = FACES_DIR / f"{uid}.jpg"
        if not face_path.is_file():
            no_image += 1
            continue
        face = _cv2.imread(str(face_path))
        if face is None:
            failed += 1
            continue
        try:
            result = analyzer.analyze(face)
            analyzed += 1
            if _apply_deep_result(db, uid, result):
                updated += 1
                results_detail.append({
                    'uid': uid[:8],
                    'gender': result.get('gender'),
                    'age_range': result.get('age_range'),
                    'confidence': result.get('confidence'),
                })
        except Exception as e:
            failed += 1
            logger.warning(f"Analisis fallo para {uid[:8]}: {e}")

    if updated > 0:
        _save_db(db)

    logger.info(
        f"Deep analyze unknown: {len(candidates)} candidatos, "
        f"{analyzed} analizados, {updated} actualizados"
    )
    return {
        "status": "ok",
        "total_candidates": len(candidates),
        "analyzed": analyzed,
        "updated": updated,
        "failed": failed,
        "no_image": no_image,
        "updated_persons": results_detail,
    }


# ── API: generar reporte PDF ──────────────────────────────────────

@app.get("/api/report/pdf")
async def api_report_pdf():
    """Genera un PDF con resumen de visitas, genero y edad.

    El PDF tiene 3 paginas:
      1. Portada con totales y desglose por genero
      2. Graficos de torta (genero) y barras (edad)
      3. Visitas por hora + tabla top 10 visitantes
    """
    try:
        from report_generator import generate_report
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Generador no disponible: {e}"
        )
    db = _load_db()
    try:
        pdf_bytes = generate_report(db, heatmap_dir=HEATMAP_DIR)
    except Exception as e:
        logger.error(f"Error generando PDF: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error generando PDF: {e}"
        )
    fname = (f"reporte_visitantes_"
             f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "no-cache",
        },
    )


# ── API: mapas de calor ────────────────────────────────────────────

def _safe_heatmap_name(name: str) -> bool:
    """Solo alfanumerico/guion/guion-bajo (mismo criterio que el writer)."""
    return bool(name) and all(c.isalnum() or c in '_-' for c in name)


@app.get("/api/heatmaps")
async def api_heatmaps():
    """Lista los mapas de calor disponibles (uno por camara).

    El proceso de inferencia guarda PNG+JSON en output/heatmap cada
    ~5s. Aqui solo se leen (sin contencion con el writer).
    """
    items = []
    if HEATMAP_DIR.is_dir():
        import json as _json
        for jf in sorted(HEATMAP_DIR.glob('*.json')):
            name = jf.stem
            if not _safe_heatmap_name(name):
                continue
            png = HEATMAP_DIR / f"{name}.png"
            if not png.is_file():
                continue
            try:
                meta = _json.loads(jf.read_text(encoding='utf-8'))
            except Exception:
                meta = {}
            ts = float(meta.get('actualizado', png.stat().st_mtime))
            hist_dir = HEATMAP_DIR / 'history' / name
            hist_count = (len(list(hist_dir.glob('*.json')))
                          if hist_dir.is_dir() else 0)
            items.append({
                'name': name,
                'camera_id': meta.get('camera_id', name),
                'camera_name': meta.get('camera_name')
                               or meta.get('camera_id', name),
                'image_url': f"/api/heatmaps/{name}.png?t={int(ts)}",
                'updated': ts,
                'updated_str': _format_datetime(ts),
                'samples': int(meta.get('muestras', 0)),
                'zones': meta.get('zonas_calientes', []),
                'history_count': hist_count,
            })
    items.sort(key=lambda x: x['updated'], reverse=True)
    return JSONResponse(content={"count": len(items), "heatmaps": items})


def _safe_stamp(stamp: str) -> bool:
    """Marca horaria 'YYYY-MM-DD_HH' (solo digitos, guion y guion-bajo)."""
    return bool(stamp) and all(c.isdigit() or c in '-_' for c in stamp)


@app.get("/api/heatmaps/{name}/history")
async def api_heatmap_history(name: str):
    """Historico POR HORAS de una camara (mas reciente primero).

    Cada item es el mapa de calor de UNA hora cerrada, con sus zonas
    ordenadas de mayor a menor concentracion.
    """
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    hist_dir = HEATMAP_DIR / 'history' / name
    items = []
    if hist_dir.is_dir():
        import json as _json
        for jf in sorted(hist_dir.glob('*.json'), reverse=True):
            stamp = jf.stem
            if not _safe_stamp(stamp):
                continue
            png = hist_dir / f"{stamp}.png"
            if not png.is_file():
                continue
            try:
                meta = _json.loads(jf.read_text(encoding='utf-8'))
            except Exception:
                meta = {}
            # "2026-06-12_14" -> "2026-06-12 14:00-15:00"
            try:
                d, hh = stamp.rsplit('_', 1)
                label = f"{d} {int(hh):02d}:00-{(int(hh) + 1) % 24:02d}:00"
            except Exception:
                label = stamp
            items.append({
                'stamp': stamp,
                'label': label,
                'image_url': f"/api/heatmaps/{name}/history/{stamp}.png",
                'samples': int(meta.get('muestras', 0)),
                'zones': meta.get('zonas_calientes', []),
                'camera_name': meta.get('camera_name', name),
            })
    return JSONResponse(content={"count": len(items), "history": items})


@app.get("/api/heatmaps/{name}/history/{stamp}.png")
async def api_heatmap_history_image(name: str, stamp: str):
    """Sirve el PNG historico de una hora concreta."""
    if not _safe_heatmap_name(name) or not _safe_stamp(stamp):
        raise HTTPException(status_code=400, detail="Parametros invalidos")
    png = HEATMAP_DIR / 'history' / name / f"{stamp}.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="No encontrado")
    return FileResponse(
        str(png), media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/heatmaps/{name}.png")
async def api_heatmap_image(name: str):
    """Sirve el PNG del mapa de calor de una camara."""
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    png = HEATMAP_DIR / f"{name}.png"
    if not png.is_file():
        raise HTTPException(status_code=404, detail="Heatmap no encontrado")
    return FileResponse(
        str(png), media_type="image/png",
        headers={"Cache-Control": "no-cache, max-age=2"},
    )


# ── Consulta historica de concentracion (agregada por rango) ────────

def _stamp_to_epoch(stamp: str):
    """'YYYY-MM-DD_HH' o 'YYYY-MM-DD' -> epoch (segundos) o None."""
    import time as _t
    for fmt in ("%Y-%m-%d_%H", "%Y-%m-%d"):
        try:
            return _t.mktime(_t.strptime(stamp, fmt))
        except Exception:
            pass
    return None


def _iter_hour_npz(name: str):
    """[(stamp, epoch, path)] de las horas con data .npz de una camara."""
    hist_dir = HEATMAP_DIR / 'history' / name
    out = []
    if hist_dir.is_dir():
        for nz in hist_dir.glob('*.npz'):
            st = nz.stem
            if st.endswith('.tmp') or not _safe_stamp(st):
                continue
            ep = _stamp_to_epoch(st)
            if ep is not None:
                out.append((st, ep, nz))
    out.sort(key=lambda x: x[1])
    return out


def _zones_from_grid(grid, k=8, r=3):
    """Top-K celdas mas calientes -> [{x,y,intensidad}] (rel 0..1).
    Replica HeatmapAccumulator._zones_from sin importar el modulo pesado."""
    g = grid.astype('float64').copy()
    h, w = g.shape
    gmax = float(g.max())
    if gmax <= 0:
        return []
    zones = []
    for _ in range(int(k)):
        idx = int(np.argmax(g))
        gy, gx = divmod(idx, w)
        val = float(g[gy, gx])
        if val <= 0:
            break
        zones.append({"x": round((gx + 0.5) / w, 4),
                      "y": round((gy + 0.5) / h, 4),
                      "intensidad": round(val / gmax, 3)})
        y0, y1 = max(0, gy - r), min(h, gy + r + 1)
        x0, x1 = max(0, gx - r), min(w, gx + r + 1)
        g[y0:y1, x0:x1] = 0.0
    return zones


def _render_heat_png(grid, bg=None, alpha=0.5):
    """Renderiza una grilla como overlay JET sobre un fondo (o negro)."""
    import cv2 as _cv2
    if bg is not None and getattr(bg, 'size', 0) > 0:
        canvas = bg.copy()
    else:
        canvas = np.zeros((grid.shape[0] * 12, grid.shape[1] * 12, 3), np.uint8)
    h, w = canvas.shape[:2]
    gmax = float(grid.max())
    if gmax > 0:
        norm = (grid / gmax * 255.0).astype(np.uint8)
        heat = _cv2.resize(norm, (w, h), interpolation=_cv2.INTER_LINEAR)
        heat = _cv2.GaussianBlur(heat, (0, 0), sigmaX=max(3.0, w / 200.0))
        color = _cv2.applyColorMap(heat, _cv2.COLORMAP_JET)
        mask = heat > 12
        blended = _cv2.addWeighted(color, alpha, canvas, 1.0 - alpha, 0)
        canvas[mask] = blended[mask]
    ok, buf = _cv2.imencode('.png', canvas, [_cv2.IMWRITE_PNG_COMPRESSION, 4])
    return buf.tobytes() if ok else b''


def _load_areas(name: str):
    af = HEATMAP_DIR / 'areas' / f"{name}.json"
    if af.is_file():
        import json as _json
        try:
            data = _json.loads(af.read_text(encoding='utf-8'))
            return data.get('areas', []) if isinstance(data, dict) else []
        except Exception:
            return []
    return []


def _aggregate_grid(name: str, ep_from: float, ep_to: float):
    """Suma las grillas .npz en [ep_from, ep_to].
    -> (grid|None, muestras, n_horas, primer_stamp, ultimo_stamp)."""
    grid = None
    muestras = 0
    nh = 0
    first = last = None
    for st, ep, nz in _iter_hour_npz(name):
        if ep < ep_from or ep > ep_to:
            continue
        try:
            with np.load(str(nz)) as d:
                g = d['grid'].astype('float64')
                s = int(d['samples']) if 'samples' in d else 0
        except Exception:
            continue  # npz corrupto -> saltar
        if grid is None:
            grid = g
        elif g.shape == grid.shape:
            grid += g
        else:
            continue
        muestras += s
        nh += 1
        first = first or st
        last = st
    return grid, muestras, nh, first, last


@app.get("/api/heatmaps/{name}/calendar")
async def api_heatmap_calendar(name: str):
    """Dias/horas con data disponible (para el selector de consulta)."""
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    days = {}
    total_samples = 0
    for st, ep, nz in _iter_hour_npz(name):
        d, hh = st.rsplit('_', 1)
        days.setdefault(d, []).append(hh)
    out = [{"day": d, "hours": sorted(hh)} for d, hh in sorted(days.items())]
    return JSONResponse(content={"camera": name, "count": len(out),
                                 "days": out})


@app.get("/api/heatmaps/{name}/areas")
async def api_heatmap_get_areas(name: str):
    """Lee las areas con nombre definidas para una camara."""
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    return JSONResponse(content={"camera": name, "areas": _load_areas(name)})


@app.put("/api/heatmaps/{name}/areas")
async def api_heatmap_put_areas(name: str, payload: dict = Body(...)):
    """Guarda (sobrescribe) las areas con nombre de una camara."""
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    raw = payload.get('areas', []) if isinstance(payload, dict) else []
    areas = []
    for a in raw[:50]:
        try:
            nm = str(a.get('name', '')).strip()[:40]
            x1 = max(0.0, min(1.0, float(a['x1'])))
            y1 = max(0.0, min(1.0, float(a['y1'])))
            x2 = max(0.0, min(1.0, float(a['x2'])))
            y2 = max(0.0, min(1.0, float(a['y2'])))
            if nm and x2 > x1 and y2 > y1:
                areas.append({"name": nm, "x1": round(x1, 4),
                              "y1": round(y1, 4), "x2": round(x2, 4),
                              "y2": round(y2, 4)})
        except Exception:
            continue
    import json as _json
    adir = HEATMAP_DIR / 'areas'
    adir.mkdir(parents=True, exist_ok=True)
    tmp = adir / f"{name}.json.tmp"
    tmp.write_text(_json.dumps({"areas": areas}, ensure_ascii=False),
                   encoding='utf-8')
    os.replace(str(tmp), str(adir / f"{name}.json"))
    return JSONResponse(content={"camera": name, "areas": areas, "saved": True})


@app.get("/api/heatmaps/{name}/aggregate")
async def api_heatmap_aggregate(name: str, request: Request):
    """Concentracion AGREGADA en un rango [from,to] -> zonas + areas + imagen.

    from/to son stamps 'YYYY-MM-DD_HH' (por hora) o 'YYYY-MM-DD' (dia
    completo). Sin parametros -> el dia de hoy.
    """
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    qp = request.query_params
    s_from = (qp.get('from') or '').strip()
    s_to = (qp.get('to') or '').strip()
    ep_from = _stamp_to_epoch(s_from) if s_from else None
    ep_to = _stamp_to_epoch(s_to) if s_to else None
    if ep_from is None or ep_to is None:
        import time as _t
        today = _t.strftime("%Y-%m-%d")
        ep_from = _stamp_to_epoch(today) or 0.0
        ep_to = ep_from + 86399.0
    else:
        # 'to' por dia (sin hora) -> incluir las 24 horas de ese dia.
        if len(s_to) == 10:
            ep_to += 86399.0
    if ep_from > ep_to:
        ep_from, ep_to = ep_to, ep_from
    grid, muestras, nh, first, last = _aggregate_grid(name, ep_from, ep_to)
    if grid is None:
        return JSONResponse(content={
            "camera": name, "samples": 0, "hours": 0, "zones": [],
            "areas": [], "image": None, "empty": True})
    zones = _zones_from_grid(grid, 8)
    total = float(grid.sum()) or 1.0
    h, w = grid.shape
    areas_out = []
    for a in _load_areas(name):
        try:
            x1 = int(float(a['x1']) * w); x2 = max(x1 + 1, int(float(a['x2']) * w))
            y1 = int(float(a['y1']) * h); y2 = max(y1 + 1, int(float(a['y2']) * h))
            s = float(grid[y1:y2, x1:x2].sum())
            areas_out.append({"name": a.get('name', ''),
                              "pct": round(100.0 * s / total, 1),
                              "valor": round(s, 1)})
        except Exception:
            continue
    areas_out.sort(key=lambda z: z['pct'], reverse=True)
    # Imagen agregada sobre el fondo de la camara (si existe), como data URL.
    bg = None
    bgf = HEATMAP_DIR / 'bg' / f"{name}.jpg"
    if bgf.is_file():
        import cv2 as _cv2
        bg = _cv2.imread(str(bgf))
    png = _render_heat_png(grid, bg)
    import base64 as _b64
    data_url = ("data:image/png;base64," + _b64.b64encode(png).decode()
                if png else None)
    return JSONResponse(content={
        "camera": name, "samples": muestras, "hours": nh,
        "from": first, "to": last, "zones": zones, "areas": areas_out,
        "image": data_url, "empty": False})


def _resolve_range(qp):
    """from/to (stamp) -> (ep_from, ep_to). Sin params -> hoy."""
    import time as _t
    s_from = (qp.get('from') or '').strip()
    s_to = (qp.get('to') or '').strip()
    ep_from = _stamp_to_epoch(s_from) if s_from else None
    ep_to = _stamp_to_epoch(s_to) if s_to else None
    if ep_from is None or ep_to is None:
        today = _t.strftime("%Y-%m-%d")
        ep_from = _stamp_to_epoch(today) or 0.0
        ep_to = ep_from + 86399.0
    elif len(s_to) == 10:
        ep_to += 86399.0
    if ep_from > ep_to:
        ep_from, ep_to = ep_to, ep_from
    return ep_from, ep_to


@app.get("/api/heatmaps/aggregate-all")
async def api_heatmap_aggregate_all(request: Request):
    """VISTA GENERAL: concentracion agregada de TODAS las camaras en un rango.
    Devuelve el total + por-camara (imagen agregada + muestras + zona top),
    ordenadas de mayor a menor actividad."""
    ep_from, ep_to = _resolve_range(request.query_params)
    cams = []
    total = 0
    if HEATMAP_DIR.is_dir():
        import json as _json
        import base64 as _b64
        for jf in sorted(HEATMAP_DIR.glob('*.json')):
            name = jf.stem
            if not _safe_heatmap_name(name):
                continue
            grid, muestras, nh, first, last = _aggregate_grid(name, ep_from, ep_to)
            if grid is None:
                continue
            zones = _zones_from_grid(grid, 3)
            bg = None
            bgf = HEATMAP_DIR / 'bg' / f"{name}.jpg"
            if bgf.is_file():
                import cv2 as _cv2
                bg = _cv2.imread(str(bgf))
            png = _render_heat_png(grid, bg)
            try:
                meta = _json.loads(jf.read_text(encoding='utf-8'))
            except Exception:
                meta = {}
            cams.append({
                "name": name,
                "camera_name": meta.get('camera_name', name),
                "samples": muestras, "hours": nh,
                "top_zone": (zones[0] if zones else None),
                "image": ("data:image/png;base64," + _b64.b64encode(png).decode()
                          if png else None),
            })
            total += muestras
    cams.sort(key=lambda c: c['samples'], reverse=True)
    return JSONResponse(content={"total_samples": total, "count": len(cams),
                                 "cameras": cams, "empty": len(cams) == 0})


@app.delete("/api/heatmaps")
async def api_heatmaps_delete_all():
    """Borra TODOS los mapas de calor (live + historico + npz + bg + aggregate).
    Conserva las areas con nombre (son configuracion, no datos)."""
    import shutil
    removed = 0
    if HEATMAP_DIR.is_dir():
        for item in HEATMAP_DIR.iterdir():
            if item.name == 'areas':
                continue  # conservar areas con nombre
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                removed += 1
            except Exception:
                pass
    return JSONResponse(content={"deleted": True, "removed": removed})


@app.delete("/api/heatmaps/{name}")
async def api_heatmap_delete_one(name: str):
    """Borra el mapa de calor de UNA camara (live + historico + npz + bg + agg)."""
    if not _safe_heatmap_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    import shutil
    targets = [
        HEATMAP_DIR / f"{name}.png", HEATMAP_DIR / f"{name}.json",
        HEATMAP_DIR / 'history' / name,
        HEATMAP_DIR / 'bg' / f"{name}.jpg",
        HEATMAP_DIR / 'aggregate' / f"{name}.png",
    ]
    for t in targets:
        try:
            if t.is_dir():
                shutil.rmtree(t, ignore_errors=True)
            elif t.exists():
                t.unlink()
        except Exception:
            pass
    return JSONResponse(content={"deleted": True, "camera": name})


# ── API: capturas (foto por persona + rostro) ─────────────────────

def _safe_capture_name(name: str) -> bool:
    """Solo nombre de archivo .jpg simple (sin traversal)."""
    return (bool(name) and name.endswith('.jpg') and '..' not in name
            and '/' not in name and '\\' not in name
            and all(c.isalnum() or c in '_-.' for c in name))


def _parse_capture_stem(stem: str):
    """'YYYYMMDD_HHMMSS_<cam>_t<id>' -> (timestamp_str, camara, track_id)."""
    ts_str, cam, tid = "", "", ""
    try:
        parts = stem.split('_')
        d, t = parts[0], parts[1]
        ts_str = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
        rest = parts[2:]
        if rest and rest[-1].startswith('t'):
            tid = rest[-1][1:]
            cam = '_'.join(rest[:-1])
        else:
            cam = '_'.join(rest)
    except Exception:
        pass
    return ts_str, cam, tid


@app.get("/api/captures")
async def api_captures(limit: int = 300):
    """Lista las capturas (persona + rostro) mas recientes."""
    persons_dir = CAPTURES_DIR / 'persons'
    faces_dir = CAPTURES_DIR / 'faces'
    items = []
    if persons_dir.is_dir():
        files = sorted(persons_dir.glob('*.jpg'),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        import json as _json
        for f in files[:max(1, min(int(limit), 2000))]:
            stem = f.stem
            ts_str, cam, tid = _parse_capture_stem(stem)
            face_exists = (faces_dir / f"{stem}.jpg").is_file()
            mt = int(f.stat().st_mtime)
            gender, age_range = None, None
            sj = persons_dir / f"{stem}.json"
            if sj.is_file():
                try:
                    sm = _json.loads(sj.read_text(encoding='utf-8'))
                    gender = sm.get('gender')
                    age_range = sm.get('age_range')
                    if not cam:
                        cam = sm.get('camera', '')
                    if not ts_str:
                        ts_str = sm.get('timestamp', '')
                except Exception:
                    pass
            items.append({
                'stem': stem,
                'person_url': f"/api/captures/persons/{f.name}?t={mt}",
                'face_url': (f"/api/captures/faces/{stem}.jpg?t={mt}"
                             if face_exists else None),
                'timestamp': ts_str, 'camera': cam, 'track_id': tid,
                'gender': gender, 'age_range': age_range, 'mtime': mt,
            })
    n_faces = (len(list((CAPTURES_DIR / 'faces').glob('*.jpg')))
               if (CAPTURES_DIR / 'faces').is_dir() else 0)
    return JSONResponse(content={"count": len(items), "faces_total": n_faces,
                                 "captures": items})


@app.get("/api/captures/persons/{name}")
async def api_capture_person(name: str):
    if not _safe_capture_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    p = CAPTURES_DIR / 'persons' / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No encontrado")
    return FileResponse(str(p), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/captures/faces/{name}")
async def api_capture_face(name: str):
    if not _safe_capture_name(name):
        raise HTTPException(status_code=400, detail="Nombre invalido")
    p = CAPTURES_DIR / 'faces' / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No encontrado")
    return FileResponse(str(p), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.delete("/api/captures")
async def api_captures_delete():
    """Borra TODAS las capturas (personas + rostros)."""
    import shutil
    if CAPTURES_DIR.is_dir():
        for sub in ('persons', 'faces'):
            d = CAPTURES_DIR / sub
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
    return JSONResponse(content={"deleted": True})


# ── Health check ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "db_exists": DB_PATH.is_file(),
        "db_path": str(DB_PATH),
    }


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n{'='*60}")
    print(f"  Dashboard ELDE - Rostros Reconocidos")
    print(f"{'='*60}")
    print(f"  Local:    http://localhost:{port}")
    print(f"  LAN:      http://<ip>:{port}")
    print(f"  DB:       {DB_PATH}")
    print(f"  Faces:    {FACES_DIR}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")
