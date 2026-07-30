# PLAN — Demografía multi-ángulo (Hito 0: Auditoría y plan)

Proyecto: **SERVER-IA PERIMETRALES** · Núcleo: `PersonAmazonas`
(`src/analityc/core/person_amazona_inference.py`)
Rama de trabajo: `feature/demografia-multiangulo`
Hardware objetivo: Quadro RTX 4000 (8 GB, Turing/FP16) · i9 11ª gen · 50 GB RAM

> Este documento es el entregable del **Hito 0**. No se ha escrito código de
> producción. Cada hito posterior se detiene para validación.

---

## 1. Resumen ejecutivo del estado actual

El sistema YA tiene una **rama facial de precisión máxima** muy elaborada
(`analytics/demographics.py`, 1688 líneas): YuNet + keypoints → pose 3D
(solvePnP) → alineación ArcFace 112×112 → ensemble InsightFace/MiVOLO/Caffe
→ heavy-TTA → voting temporal robusto (trimmed-mean) → commit con histéresis
→ Re-ID ArcFace. La filosofía es **"preferible no clasificar a equivocarse"**.

El trabajo NO es reescribir eso, sino **añadir la rama corporal** (clasificar
sin cara) y la **capa de captura universal**, además de **parametrizar por
ángulo de cámara** y **eliminar rutas hardcodeadas**. La arquitectura de dos
ramas de la sección 5 del encargo encaja casi 1:1 con lo que ya existe; lo que
falta es E3B (cuerpo) y la infraestructura alrededor.

---

## 2. Mapa de módulos (qué hace cada archivo y qué se toca)

### Núcleo
| Archivo | Responsabilidad | Hitos que lo tocan |
|---|---|---|
| `core/person_amazona_inference.py` (2941 L) | Clase `PersonAmazonas`: detección YOLO11m+BoT-SORT, orquestación, multicam (`get_camera_processor`, `_get_cam_state`), `process_frame`, `get_stats`, init de sesiones ONNX (`_init_gender_age_classifier`), integración demografía (`_classify_person`) | **2, 3, 5, 6** |

### Analítica (`core/analytics/`)
| Archivo | Responsabilidad | Hitos |
|---|---|---|
| `demographics.py` | `DemographicsClassifier` + `_TrackAccumulator`. Rama facial completa, voting, commit, TTA, ensemble | **3, 4, 5** |
| `face_reidentifier.py` | `FaceReidentifier`: DB persistente de embeddings ArcFace, identify_or_register, merge, herencia demográfica | **5** |
| `config.py` | `AnalyticsConfig`: ~80 thresholds (DEMO_*, REID_*, MIVOLO_*) | **2, 3, 4, 5** |
| `people_counter.py`, `attendance_tracker.py`, `seller_efficiency.py`, `stock_monitor.py` | Módulos retail. **NO se tocan** (solo se respetan sus llamadas) | — |

### Config / utilidades / capa nueva
| Ruta | Estado | Hitos |
|---|---|---|
| `config/config.py` | `_MODELS_BASE` y `_OUTPUT_BASE` **hardcodeados** a `C:\...`. Helper `model()`/`output()`. Falta perfil por cámara | **2** |
| `core/capture/` | **NO existe** → se crea `CameraSource` | **1** |
| `core/utils/overlay.py`, `utils/logger.py` | Overlays + `AnalyticsLogger`. Se extienden (no rompen) para `age_source`/`gender_confidence` | **5, 6** |
| `scripts/` | Ya hay `setup_face_embedding.py`, `setup_mivolo.py`, `setup_buffalo_l_genderage.py`. Falta `setup_modelos.py` unificado | **4** |
| `tools/` | Falta `benchmark.py` y `eval_demografia.py` | **6** |

### Modelos presentes (`models/classifiers/`)
✅ `face_detection_yunet_2023mar.onnx`, `genderage.onnx`, `w600k_r50.onnx`,
Caffe (gender/age), Res10 SSD. ❌ **Falta `mivolo.onnx`** (clave para E3B).
Modelo de personas: `models/base/yolo11m.pt`.

---

## 3. Hallazgos críticos (leer antes de tocar)

### 3.1 ✅ RESUELTO — onnxruntime ahora usa la GPU
**Causa raíz:** en el Python global convivían `onnxruntime` (1.24.4, CPU) y
`onnxruntime-gpu` (1.23.2). Al compartir el namespace `onnxruntime/`, el build
CPU pisaba al de GPU → `get_device()==CPU`, sin `CUDAExecutionProvider`.
InsightFace/ArcFace/MiVOLO corrían en CPU.

**Solución aplicada (venv aislado, decisión del usuario):**
- Creado `venv/` con `--system-site-packages` (reusa torch/ultralytics/opencv
  globales, sin re-descargar 2.5 GB).
- Instalado `onnxruntime-gpu==1.23.2` limpio dentro del venv (prioridad sobre
  el global roto).
- **Validado end-to-end:** `genderage.onnx` corre sobre `CUDAExecutionProvider`.
  Providers disponibles: TensorRT + CUDA + CPU.

**Cómo se usa:** lanzar la app con `venv\Scripts\python.exe`. Como el código
hace `import torch` antes de crear las sesiones ONNX, las DLLs de CUDA 12/cuDNN 9
(que trae torch) quedan disponibles para ORT **sin cambios de código**. En el
Hito 6 se añadirá `ort.preload_dlls()` explícito como cinturón de seguridad.

> El Python global queda intacto (sigue con el conflicto). La app DEBE lanzarse
> con el venv. La taxonomía de modelos y FPS objetivo ya son alcanzables.

### 3.2 🟠 Filtro de aspecto rompe el cenital
`person_amazona_inference.py:2433` → `if bh < bw * 0.8: continue`. En cenital
una persona se ve más ancha que alta y se descarta. **Hito 2** lo vuelve
configurable por perfil (`frontal|lateral|cenital`).

### 3.3 🟠 Rutas hardcodeadas
`config/config.py:33-34` (`_MODELS_BASE`, `_OUTPUT_BASE`) y
`person_amazona_inference.py:62,65` (`_DEFAULT_PERSON_MODEL`,
`_DEFAULT_PRODUCT_MODEL`) apuntan a `C:\Users\Sistema-1\...`. **Hito 2** las pasa
a rutas relativas a la raíz del proyecto (con override por env, ya soportado).

### 3.4 🟡 Pitch alto en cenital ya contemplado, pero el resto de gates no
`DEMO_POSE_MAX_PITCH_DEG=48` ya da margen para cenital, PERO la rama facial
seguirá fallando de espaldas/perfil extremo **por diseño** (correcto). Ahí entra
E3B. Hoy, sin cara → la persona queda en `'Personas'` para siempre.

### 3.5 🟡 Tracker BoT-SORT compartido entre cámaras
`get_camera_processor` comparte `person_model`, pero `model.track(persist=True)`
mantiene estado de tracker DENTRO del objeto YOLO. Varias cámaras llamando
`track()` sobre el mismo modelo pueden contaminar IDs. Hoy se mitiga con el
offset `+100000`, pero conviene verificarlo en el **Hito 2** con 2+ streams.

---

## 4. Presupuesto de VRAM (análisis de viabilidad en 8 GB)

| Modelo | Precisión | Pesos aprox | Dónde corre |
|---|---|---|---|
| YOLO11m (personas) | FP16 | ~40 MB + activaciones ~0.3–0.6 GB/stream | GPU (torch) |
| genderage.onnx (96²) | FP16 | ~3 MB | GPU (tras fix 3.1) |
| ArcFace w600k_r50 (112²) | FP16 | ~90 MB | GPU |
| MiVOLO (224²) | FP16 | ~100–200 MB | GPU |
| YuNet | — | ~1 MB | CPU (OpenCV) |
| Caffe (fallback) | — | ~45 MB | CPU |

**Conclusión: VRAM NO es el cuello de botella.** Pesos totales < 0.5 GB; aun
con 4 streams compartiendo un solo YOLO (pesos únicos, activaciones
secuenciales) el total estimado es **< 4 GB**, holgado bajo el límite de 6.5 GB.
El verdadero límite es **throughput** (FPS), no memoria — y ahí el bloqueador es
3.1. **El plan es viable en esta máquina** siempre que se resuelva el provider
CUDA de ORT.

---

## 5. Arquitectura de dos ramas → mapeo a código existente

```
Frame → [E1] YOLO11m + BoT-SORT (process_frame, línea ~2402)
          → por track NO convergido (DemographicsClassifier.classify):
            [E2] YuNet en crop de persona (_detect_align_and_pose_yunet)  ✅ EXISTE
              ├─ cara OK → [E3A] rama facial: align+ensemble+TTA          ✅ EXISTE
              └─ sin cara → [E3B] rama corporal MiVOLO body-only          ❌ A CONSTRUIR
          → [E4] fusión + voting ponderado (peso facial 1.0 / corporal 0.5)  🟠 EXTENDER
          → [E5] Re-ID ArcFace + herencia demográfica (FaceReidentifier)  ✅ EXISTE
```

- **E1, E2, E3A, E5 ya existen** y son sólidos. Se respetan.
- **E3B es el corazón del encargo**: clasificar género/edad desde el cuerpo
  completo cuando no hay cara (de espaldas, cenital, perfil extremo).
- **E4** hoy asume todas las muestras faciales (peso por nitidez/pose). Hay que
  introducir el concepto de **muestra corporal** con peso 0.5 y `age_source`.

---

## 6. Plan por hito (con archivos y criterio de aceptación)

### Hito 1 — Captura universal
- **Nuevo:** `core/capture/camera_source.py` (`CameraSource`), `demo.py` (CLI).
- Hilo lector dedicado, `deque(maxlen=1)` (drop policy), reconexión con backoff
  1→30 s + watchdog, `get_stats()` (fps_entrada/procesado, descartados,
  reconexiones). Backends: RTSP (FFMPEG+tcp), USB (DSHOW/MSMF), archivo, MJPEG.
- **Primer sub-paso:** diagnóstico del provider CUDA de ORT (hallazgo 3.1).
- **Aceptación:** `python demo.py --source 0|rtsp://...|video.mp4` corre; al
  cortar la red, reconecta solo.

### Hito 2 — Detección multi-ángulo + des-hardcodeo
- **Tocar:** `config/config.py` (rutas relativas a raíz vía
  `Path(__file__)`, + sección `CAMERA_PROFILES` con `angulo`, filtro de aspecto,
  umbrales, `roi`, `resolucion_inferencia`); `person_amazona_inference.py`
  (línea 2433 → filtro aspecto configurable por perfil; líneas 62/65 → rutas
  relativas).
- **Aceptación:** tracking estable en un clip cenital y uno de espaldas; cero
  rutas `C:\` en `src/`.

### Hito 3 — Rama facial mejorada
- **Tocar:** `analytics/demographics.py` (gates de calidad: ya hay blur/tamaño/
  yaw — endurecer y loguear el rechazo de borrosas para demostrarlo).
- La alineación ArcFace ya existe; aquí se **verifica y se instrumenta**.
- **Aceptación:** ≥95 % género en clips frontales propios; el log demuestra cero
  muestras borrosas aceptadas.

### Hito 4 — Rama corporal (E3B) ⭐
- **Nuevo:** `scripts/setup_modelos.py` (descarga/verifica YuNet, genderage,
  w600k_r50; instrucciones reproducibles para exportar MiVOLO a ONNX).
- **Tocar:** `demographics.py` → método `_infer_body_only(person_crop)` y ruta
  E3B cuando `_detect_face_with_pose` devuelve `None`. Gates: bbox persona
  ≥120 px alto, descarte por blur. `age_source='body'` + taxonomía gruesa
  (Niño/Joven/Adulto/Mayor). Pesos en `AnalyticsConfig`.
- **Decisión documentada:** MiVOLO body-only como primario; alternativa PAR
  (PA-100K, solo género) si MiVOLO no exporta limpio a ONNX.
- **Aceptación:** ≥85 % género en clip 100 % de espaldas.

### Hito 5 — Fusión, voting ponderado y Re-ID
- **Tocar:** `demographics.py` (`_TrackAccumulator.add` con `weight` facial 1.0
  / corporal 0.5 — el peso ya existe, se cablea por origen; commit con
  histéresis ya presente; lock tras commit ya presente); `face_reidentifier.py`
  (herencia ya existe en `get_demographics_for_person` + `_commit_from_reid` —
  se verifica); `person_amazona_inference.py` / overlays (añadir `age_range`,
  `age_source`, `gender_confidence` a metadata SIN romper categorías legacy).
- **Aceptación:** etiquetas sin parpadeo tras lock; conteo único al salir/volver.

### Hito 6 — Optimización y cierre
- **Resolver 3.1** (CUDAExecutionProvider). FP16 en YOLO (`half=True`),
  providers ONNX correctos, flag de config `TensorrtExecutionProvider`
  (default OFF). **Nuevo:** `tools/benchmark.py` (FPS por etapa, p50/p95, VRAM
  vía pynvml) y `tools/eval_demografia.py` (matriz confusión género, accuracy
  edad ±1, desglose facial/corporal).
- **Aceptación:** se cumplen los números de la sección 8 medidos en esta máquina.

---

## 7. Reglas que se respetan (recordatorio)

- API pública intacta: firma de `process_frame(...)`, `get_stats()`, y los
  strings legacy `'Hombres'/'Mujeres'/'Niños'/'Personas'`. Lo nuevo se **añade**.
- No se degradan los módulos retail. Logs/comentarios/docstrings en español,
  PEP8 + type hints. Degradación con warning si falta un ONNX (nunca crash).
- Sin frameworks pesados nuevos (TF/MediaPipe/Paddle): todo modelo externo se
  consume **exportado a ONNX**.
- `cosmetics_enabled=False` permanece. Enfoque exclusivo personas + demografía.

---

## 8. Decisiones tomadas (confirmadas tras Hito 0)

1. **Provider CUDA de ORT (3.1):** ✅ **Diagnosticar/arreglar YA** al inicio del
   Hito 1 antes de avanzar. Es bloqueante para los FPS objetivo.
2. **MiVOLO body-only:** ✅ **Fallback aceptado a PAR (solo género)** si MiVOLO
   no exporta limpio a ONNX; la edad corporal queda en taxonomía gruesa
   heurística. Se documentará la elección en el Hito 4.
3. **Edad sin cara:** será gruesa (Niño/Joven/Adulto/Mayor) con
   `age_source='body'`. Confirmado en el encargo.

---

## 9. Archivos que se tocarán por hito (resumen)

| Hito | Nuevos | Modificados |
|---|---|---|
| 1 | `core/capture/camera_source.py`, `demo.py` | — |
| 2 | — | `config/config.py`, `person_amazona_inference.py` |
| 3 | — | `analytics/demographics.py`, `analytics/config.py` |
| 4 | `scripts/setup_modelos.py` | `analytics/demographics.py`, `analytics/config.py` |
| 5 | — | `demographics.py`, `face_reidentifier.py`, `person_amazona_inference.py`, `utils/overlay.py` |
| 6 | `tools/benchmark.py`, `tools/eval_demografia.py` | init ONNX, `config/config.py`, `README.md` |

---

**FIN DEL HITO 0.** Esperando tu confirmación para crear la rama
`feature/demografia-multiangulo` y comenzar el Hito 1. Antes de avanzar,
necesito tu respuesta a los 3 puntos de la sección 8.
