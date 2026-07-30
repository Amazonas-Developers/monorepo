# AUDITORÍA DEL MÓDULO DEMOGRÁFICO — DEMOGRAFIA-AMAZONAS

**Fecha:** 27 de julio de 2026
**Alcance:** ruta completa desde el frame recibido por WebSocket hasta el JSON
escrito en `capture/` del cliente.
**Método:** lectura del código + medición sobre los 108 crops ya guardados,
usando el detector que el sistema ya tiene cargado. No se escribió estimador
nuevo (eso corresponde al Hito 4).

---

## Resumen ejecutivo

El 98 % de `null` **no es un fallo de software**: es el resultado esperado de
dos hechos combinados.

1. **El 79 % de los crops no tiene rostro detectable** (85 de 108). Ángulo
   picado, distancia y JPEG q75 sobre captura de pantalla.
2. **La rama corporal existe, está activada… y no tiene ningún modelo que la
   ejecute.** `DEMO_BODY_BRANCH_ENABLED = True`, pero ni `mivolo.onnx` ni
   `par_gender.onnx` están en disco, así que la rama sale por
   `demographics.py:1608-1609` en el primer `if` y devuelve `False` siempre.

Es decir: para 4 de cada 5 personas **no existe ninguna vía de clasificación
habilitada**. No es que falle: es que no hay con qué intentarlo.

Sobre el 21 % restante (con rostro), los umbrales de "precisión máxima"
—pedidos explícitamente en mayo/junio de 2026— hacen el resto: la mediana del
rostro es **29 px**, que cae en el bracket más duro (conf ≥ 0.94, margen
≥ 0.70) o directamente por debajo del corte de 25 px.

**Conclusión: tu decisión de que MiVOLO en modo cuerpo sea la ruta principal
es correcta y está respaldada por los números.**

---

## Mediciones (evidencia dura)

### Estado de los JSON en `capture/` (109 archivos)

| Métrica | Valor |
|---|---|
| Con `gender` poblado | **2 (1.8 %)** |
| Con `gender: null` | **107 (98.2 %)** |
| Con `person_uuid` + `visitas` + `demo_final` | **19** |

El dato más informativo: **19 capturas llegaron a la fase final de identidad
pero solo 2 tienen género**. El Re-ID (ArcFace) exige rostro para confirmar
identidad, así que en esos 17 casos **hubo rostro suficiente para biometría
pero la demografía lo rechazó**. Eso descarta que el problema sea únicamente
"no hay cara" y prueba que los gates también están cortando.

### Crops de persona (108, medidos en el servidor, sin banner)

| Métrica | Valor |
|---|---|
| Ancho mín / mediana / máx | 33 / **162** / 444 px |
| Alto mín / mediana / máx | 162 / **451** / 684 px |
| Relación de aspecto (alto/ancho) | **2.50** — cuerpo completo |
| Rechazados por `DEMO_MIN_PERSON_BBOX_W` (60) | 2 de 108 |
| Rechazados por `DEMO_MIN_PERSON_BBOX_H` (140) | 0 de 108 |

> Nota: tus cifras (240 / 697) son de los archivos del **cliente**, que llevan
> banner y panel compuestos encima. Las de aquí son el recorte puro del
> servidor. Ambas son correctas, miden cosas distintas.

El gate de bbox de persona **no es la causa**: descarta 2 de 108.

### Detección de rostro sobre esos mismos crops

| Métrica | Valor |
|---|---|
| Con rostro detectado | **23 (21.3 %)** |
| **Sin rostro detectable** | **85 (78.7 %)** |
| Ancho nativo del rostro: mín / p25 / mediana / p75 / máx | 16 / 24 / **29** / 63 / 93 px |
| Rostros < 25 px (rechazo directo) | 8 de 23 (35 %) |
| Rostros < 40 px | 15 de 23 (65 %) |
| Rostros < 60 px | 17 de 23 (74 %) |

### Desglose por causa de descarte

| Causa | Casos |
|---|---|
| `sin_rostro_detectable` | **85** |
| `rostro_bajo_25px_rechazado` | 7 |
| `pose_pitch` (picado > 48°) | 3 |
| Pasa gates → bracket ≥ 60 px (conf ≥ 0.85, margen ≥ 0.45) | 6 |
| Pasa gates → bracket ≥ 40 px (conf ≥ 0.90, margen ≥ 0.55) | 2 |
| Pasa gates → bracket ≥ 25 px (conf ≥ 0.94, margen ≥ 0.70) | 5 |

Solo **13 de 108 crops (12 %)** llegan siquiera a poder intentar una
clasificación, y 5 de ellos con el listón en 0.94 de confianza.

---

## Las 6 preguntas

### 1. ¿Dónde vive el estimador y qué modelo usa hoy?

- **Módulo:** `src/analityc/core/analytics/demographics.py`, clase
  `DemographicsClassifier` (línea **643**).
- **Modelo primario real:** InsightFace `genderage.onnx`
  (`models/classifiers/genderage.onnx`, 1.3 MB), invocado en
  `_infer_insightface_tta` (línea **1413**) con heavy-TTA de 3 variantes.
- **Ensemble:** `_infer_ensemble` (línea **1271**). Caffe (`age_net` /
  `gender_net`) está cargado pero **excluido del ensemble** cuando hay un
  modelo moderno (decisión previa documentada: su ~70 % de acierto
  contradecía a InsightFace y descartaba muestras válidas).
- **Rama corporal (E3B):** `_try_body_sample` (**1598**) →
  `_infer_body_only` (**1661**), que elige PAR (**1672**) o MiVOLO
  body-only (**1702**).

**Hallazgo crítico:** `_infer_body_only` requiere `self._par` o
`self._mivolo`. Ninguno existe en disco:

```
NO  models/classifiers/mivolo.onnx
NO  models/classifiers/par_gender.onnx
SI  models/classifiers/genderage.onnx
SI  models/classifiers/w600k_r50.onnx      (Re-ID, no demografía)
```

Hay un `models/classifiers/mivolo_meta.json` huérfano (in_chans: 6,
input_size 224×224) — resto de un intento previo de integración que quedó a
medias. **Nota para el Hito 4:** ese meta declara **224×224**, no los 384×384
que indica tu prompt; hay que confirmar cuál corresponde a `mivolo_v2` antes
de fijar el preprocesado.

### 2. ¿Qué dispara la estimación y qué emite `demo_final`?

- **Disparo por frame:** `classify()` (**760**), llamado desde
  `person_amazona_inference.py:_classify_person`. Se ejecuta cada
  `DEMO_RECLASSIFY_EVERY = 10` frames por track, escalonado por `track_id`.
- **Compromiso del resultado:** `try_commit()` (**526**). Exige, en orden:
  1. `samples >= DEMO_MIN_SAMPLES` (5), salvo fast-commit;
  2. confianza ≥ umbral del bracket adaptativo por tamaño de rostro;
  3. margen top1−top2 ≥ umbral del bracket;
  4. `agree_ratio >= DEMO_COMMIT_AGREE_RATIO` (0.90).
- **`demo_final` NO lo emite el estimador.** Lo escribe
  `person_amazona_inference.py:2789`, dentro de
  `_update_captures_demographics`, justo antes de re-anotar la foto del
  cliente. Marca "esta captura ya pasó por la consolidación", no "la
  clasificación tuvo éxito". Por eso hay 19 con `demo_final` y solo 2 con
  género: **el campo no distingue éxito de fracaso**, que es justo el punto
  que señalas en tu Hito 5.

### 3. Gates antes del estimador (todos los umbrales)

Ruta facial, en `_sample_once` (**1025**) y `_detect_align_and_pose_yunet`:

| Gate | Parámetro | Valor global | cam12 |
|---|---|---|---|
| Bbox de persona | `DEMO_MIN_PERSON_BBOX_W` / `_H` | 60 / 140 px | 45 / 110 |
| Score del detector | `DEMO_YUNET_MIN_SCORE` | 0.70 | 0.65 |
| Tamaño de rostro | `DEMO_MIN_FACE_SIZE` | 40 px | 40 |
| Rostro nativo mínimo | bracket inferior | **25 px (< 25 se rechaza)** | igual |
| Yaw / Pitch / Roll | `DEMO_POSE_MAX_*_DEG` | 35 / 48 / 40° | 35 / **52** / 40 |
| Nitidez | `DEMO_MIN_BLUR_VAR` | 25.0 | 16.0 |
| Contraste | `DEMO_MIN_CONTRAST` | 18.0 | 12.0 |
| Asimetría | `DEMO_MAX_ASYMMETRY` | 135 | 150 |
| Aspecto del rostro | `DEMO_FACE_ASPECT_MIN/MAX` | 0.85 / 1.45 | 0.80 / 1.55 |
| Ratio interocular | `DEMO_MIN_EYE_FACE_RATIO` | 0.22 | 0.20 |
| Offset de nariz | `DEMO_MAX_NOSE_OFFSET` | 0.65 | 0.65 |
| Muestras mínimas | `DEMO_MIN_SAMPLES` | 5 | 5 |
| Acuerdo entre muestras | `DEMO_COMMIT_AGREE_RATIO` | 0.90 | 0.90 |
| Acuerdo del ensemble | `DEMO_REQUIRE_ENSEMBLE_AGREEMENT` | True | True |
| Acuerdo TTA interno | `DEMO_TTA_REQUIRE_AGREEMENT` | True | True |
| Backoff sin rostro | `DEMO_MAX_NOFACE_BEFORE_GIVEUP` | **30 intentos** | 30 |

**Brackets adaptativos** (`DEMO_ADAPTIVE_BRACKETS`), por ancho nativo:

| Rostro | Confianza mínima | Margen mínimo |
|---|---|---|
| ≥ 100 px | 0.80 | 0.35 |
| 60–100 px | 0.85 | 0.45 |
| 40–60 px | 0.90 | 0.55 |
| 25–40 px | **0.94** | **0.70** |
| < 25 px | **rechazo directo** | — |

Ruta corporal, en `_try_body_sample` (**1598**):

| Gate | Valor |
|---|---|
| `DEMO_BODY_MIN_PERSON_H` / `_W` | 120 / 45 px |
| `DEMO_BODY_MIN_BLUR_VAR` | 12.0 |
| `DEMO_BODY_MIN_GENDER_CONF` | 0.70 |
| Peso de la muestra corporal | 0.5 (facial = 1.0) |
| **Modelo disponible** | **NINGUNO → corta en línea 1608** |

Además existe un **backoff** (`_sample_once`, ~**1099-1105**): tras 30
intentos sin rostro el track se marca `give_up = True` y **deja de intentarse
para siempre**. Con crops donde el rostro aparece solo unos frames, esto
congela el track en Desconocido de forma permanente.

### 4. `person_uuid` y `visitas`

- **Generación del uuid:** `face_reidentifier.py:646` —
  `new_uid = str(uuid.uuid4())` al registrar una persona nueva, dentro de
  `identify_or_register` (**532**). Requiere un embedding ArcFace, es decir
  **rostro presente y de pose aceptable** (`REID_MAX_YAW_FOR_REGISTRATION`
  20°, `REID_MAX_PITCH_FOR_REGISTRATION` 35°).
- **`visit_count`:** se inicializa en 1 (**668**) y se incrementa en
  `face_reidentifier.py:602` cuando un embedding nuevo hace match con una
  persona ya conocida.
- El campo `visitas` del JSON se copia desde ahí en
  `person_amazona_inference.py:~2770`.

### 5. Excepciones silenciadas en la ruta demográfica

| Archivo | Línea | Qué silencia | Gravedad |
|---|---|---|---|
| `demographics.py` | **877** | `except Exception: return` en la histéresis tras `_infer_ensemble`. Una excepción del estimador se traga entera. | **Alta** |
| `demographics.py` | **1593** | `except Exception: return None, None, 0.0` en la inferencia MiVOLO. Si el modelo existiera y fallara, sería invisible. | **Alta** |
| `demographics.py` | **1629** | `except Exception: pass` en el gate de blur corporal. Menor: solo omite el gate. | Baja |
| `demographics.py` | **192** | `except cv2.error: return None` en la alineación. | Media |
| `person_amazona_inference.py` | 2508, 2527, 2549, 2558, 2569 | Escritura de crops y sidecars. | Media |
| `person_amazona_inference.py` | **2726, 2738, 2768, 2782, 2791** | Toda `_update_captures_demographics`, incluida la escritura final del JSON. | **Alta** |

Ninguno registra nada en el log. **H4 no puede descartarse por inspección**:
si el estimador estuviera lanzando, no quedaría rastro. El Hito 2 debe
convertir estos bloques en logs antes de tocar lógica.

### 6. Quién escribe el JSON de `capture/` y cuándo

Dos escrituras, en momentos distintos del ciclo de vida del track:

1. **Al capturar** — `_capture_detection` (**2467**), disparado por
   `_capture_established_tracks` (**2415**) cuando el track lleva
   `CAPTURE_MIN_SEEN_FRAMES = 5` frames. Escribe el JSON inicial con
   `gender: null`, `age_range: null` (línea **2507**) y la foto anotada del
   cliente vía `_write_client_capture` (**2671**, `_json.dump` en **2705**).
2. **Al converger la demografía** — `_update_captures_demographics`
   (**2711**), llamada cada frame desde `process_frame`. Solo escribe
   `gender`/`age_range` **si** `g not in (None, 'Desconocido')`. Añade
   `person_uuid`/`visitas` si el Re-ID los conoce, marca `demo_final = True`
   (**2789**) y re-anota la foto.

**El JSON nunca se borra ni se marca como fallido.** Si la demografía no
converge, el archivo se queda con el `null` inicial para siempre y no hay
manera de distinguir "no se pudo clasificar" de "todavía no se intentó" —
exactamente el problema que tu Hito 5 quiere resolver con
`motivo_sin_demografia`.

---

## Diagrama del flujo actual

```
Cliente (Amazonas View)
  captura ventana -> JPEG q75 -> msgpack -> WebSocket binario
        │
        ▼
app.py: /ws/{type_inference}  ──> process_image_sync
        │
        ▼
PersonAmazonas.process_frame(img, roi, activate_roi, camera_id)
        │
        ├─ YOLO26m (imgsz 1280) ─> personas ─> ByteTrack ─> active_tracks
        │
        ├─ _classify_person(track)  [cada 10 frames, escalonado]
        │       │
        │       ▼
        │   DemographicsClassifier.classify()                    :760
        │       │
        │       ├─ ¿committed? ──sí──> devuelve cache (lock)
        │       ├─ ¿Re-ID ya la clasificó? ──sí──> _commit_from_reid  :983
        │       ├─ ¿give_up? ──sí──> Desconocido permanente
        │       │
        │       ▼  _sample_once()                                :1025
        │       │
        │       ├─ gate bbox persona (60x140) ──falla──> descarta   [2/108]
        │       │
        │       ├─ 3 recortes cabeza+torso -> YuNet
        │       │      │
        │       │      ├─ SIN ROSTRO (79 %) ──> _try_body_sample   :1598
        │       │      │        └─ if _par is None and _mivolo is None:
        │       │      │              return False      ← ◆ MUERE AQUÍ ◆  :1608
        │       │      │                 (no hay modelo corporal en disco)
        │       │      │           -> backoff: 30 intentos -> give_up
        │       │      │
        │       │      └─ CON ROSTRO (21 %, mediana 29 px)
        │       │             ├─ gates pose/calidad ──falla──> descarta
        │       │             ├─ alineación ArcFace 112x112
        │       │             ├─ genderage.onnx + TTA(3) + ensemble
        │       │             └─ acumula muestra (peso 1.0)
        │       │
        │       ▼  try_commit()                                   :526
        │          exige 5 muestras + conf/margen del bracket + acuerdo 0.90
        │          bracket 25-40 px -> conf 0.94, margen 0.70   ← casi imposible
        │
        ├─ _capture_established_tracks (>= 5 frames vistos)       :2415
        │       └─ _capture_detection -> persons/*.jpg + JSON con null  :2467
        │            └─ _write_client_capture -> capture/ del cliente   :2671
        │
        └─ _update_captures_demographics  [cada frame]            :2711
                └─ si género != Desconocido -> reescribe JSON + demo_final :2789
                   si no -> el JSON se queda en null PARA SIEMPRE
```

◆ = punto donde muere el 79 % de los casos.

---

## Hipótesis priorizada

| # | Hipótesis | Veredicto | Evidencia |
|---|---|---|---|
| **H2** | El detector no encuentra cara | ✅ **CAUSA PRINCIPAL (79 %)** | 85/108 sin rostro; mediana 29 px en los que sí |
| **H1** | Los gates rechazan casi todo | ✅ **CAUSA SECUNDARIA** | De 23 con rostro, 7 caen por < 25 px y 3 por pitch; de los 13 restantes, 5 con listón 0.94/0.70. 19 con uuid pero solo 2 con género |
| **H3** | Solo corre al cerrar el track | ❌ **DESCARTADA** | `classify()` corre cada 10 frames; `demo_final` se escribe en la consolidación, no condiciona la estimación |
| **H4** | Excepción tragada silenciosamente | ⚠️ **NO DESCARTABLE** | 11 bloques sin log en la ruta (877, 1593, 2726-2791). Improbable como causa mayoritaria, pero invisible por diseño |
| **H5** | Se estima y no se persiste | ❌ **DESCARTADA** | La escritura funciona: cuando hay género, se persiste (los 2 casos, con uuid y visitas correctos) |

**Causa raíz combinada, en una frase:** el 79 % de las personas no muestra
rostro utilizable y **la única vía alternativa —la rama corporal— está
habilitada pero sin modelo**, así que no se intenta nada; y del 21 % que sí
tiene rostro, la mediana de 29 px cae en el tramo donde el modo "precisión
máxima" exige 0.94 de confianza, que casi nunca se alcanza.

---

## Riesgos detectados (fuera del alcance de las 6 preguntas)

1. **Conflicto de `onnxruntime` (Hito 0).** Conviven `onnxruntime-gpu==1.23.2`
   y `onnxruntime==1.24.4` (CPU, **más nueva**). Ahora mismo gana la GPU, pero
   cualquier `pip install -U` puede invertirlo **sin error visible** y tirar
   la inferencia ONNX a CPU. Comando de saneamiento:
   `pip uninstall -y onnxruntime && pip install --force-reinstall onnxruntime-gpu`
2. **`mivolo_meta.json` declara 224×224**, no los 384×384 de tu prompt. A
   confirmar contra el repo oficial antes del Hito 4.
3. **`capture/` crece sin techo** (109 archivos y subiendo; sin política de
   retención). Ya lo anotas en tu sección 6.
4. **El backoff de 30 intentos** marca `give_up` permanente. Con la rama
   corporal activa habrá que revisarlo: hoy condena tracks que podrían
   clasificarse por cuerpo.

---

## Observación sobre una decisión previa tuya

Los umbrales que hoy bloquean el 21 % con rostro **son los que pediste** en
mayo y junio de 2026 ("precisión máxima, preferible *Desconocido* a
equivocarse", y luego el endurecimiento anti "hombres clasificados como
mujeres"). No es un bug heredado: es una política funcionando.

Si el objetivo ahora es **cobertura** (que la mayoría de las capturas tengan
demografía), hay que aceptar explícitamente que bajará la precisión por
muestra. La forma correcta de hacerlo no es relajar los umbrales a ciegas,
sino apoyarse en la agregación por track de tu Hito 5: muchas muestras
mediocres bien votadas superan a una muestra exigente que nunca llega. Lo
menciono ahora porque condiciona cómo calibraremos el Hito 4.
