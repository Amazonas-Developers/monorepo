# Proyecto: ReID multicámara (Perimetrales)

Resumen rápido
- Código para mantener IDs globales de personas entre múltiples cámaras usando apariencias (OSNet) con fallback HS+ORB.

Requisitos
- Python 3.10+ (se probó con 3.12 en el entorno del repositorio).
- Instalar dependencias:

```bash
python -m pip install -r requirements.txt
```

Notas sobre GPU
- Para usar GPU asegúrate de instalar la versión de `torch` compilada con tu CUDA (por ejemplo `+cu130`).
- Verifica `torch.cuda.is_available()`.

Pesos OSNet
- El código descargará automáticamente los pesos imagenet / Market/Duke cuando sea necesario y los almacenará en `~/.cache/torch/checkpoints/`.
- También puedes colocar manualmente los checkpoints en `models/osnet/`:
  - `models/osnet/osnet_x1_0_market1501.pt`
  - `models/osnet/osnet_x1_0_dukemtmcreid.pt`

Archivos clave
- `src/analityc/core/perimetrales_multicam.py`: Procesador multicámara (ahora soporta extractor OSNet si está disponible).
- `src/analityc/core/botsort_wrapper.py`: Wrapper BoTSORT simplificado que usa OSNet si existe, con fallback HS+ORB.

Prueba rápida
- Ejecuta el script de test sintético (o usa la REPL) para validar que una misma persona en dos posiciones/cámaras conserva `global_id`.

Ejemplo (línea de comandos):

```bash
python -c "from src.analityc.core.perimetrales_multicam import PerimetralesMultiCam; print('OK')"
```

Si quieres que ejecute pruebas reales con tus secuencias de video, indícame las rutas o dame acceso a un par de frames.

---

# Demografía multi-ángulo (PersonAmazonas)

Detección de personas + género + rango de edad desde **cualquier ángulo**
(frontal, lateral, de espaldas, cenital) y **cualquier fuente** (RTSP, USB,
archivo, MJPEG). Núcleo: `src/analityc/core/person_amazona_inference.py`.

## Entorno (IMPORTANTE)
La app debe lanzarse con el **venv del proyecto** para que onnxruntime use la
GPU (el Python global tiene un conflicto `onnxruntime`/`onnxruntime-gpu`):

```powershell
.\venv\Scripts\python.exe <script>.py
```

Si el venv no existe, créalo reusando los paquetes globales pesados:
```powershell
python -m venv venv --system-site-packages
.\venv\Scripts\python.exe -m pip install --ignore-installed onnxruntime-gpu==1.23.2
```

## Modelos
Verifica/obtén los modelos con:
```powershell
.\venv\Scripts\python.exe scripts\setup_modelos.py
```
- **Esenciales** (rama facial + re-id): `face_detection_yunet_2023mar.onnx`,
  `genderage.onnx`, `w600k_r50.onnx`.
- **Opcionales** (rama corporal, sin cara): `mivolo.onnx` (primario) o
  `par_gender.onnx` (fallback solo género). Sin ellos, el sistema clasifica
  solo cuando hay cara.

## Arquitectura (pipeline de 2 ramas)
```
Frame → YOLO11m + BoT-SORT (FP16) → por track no convergido:
  YuNet en el crop → cara OK → RAMA FACIAL (align ArcFace + ensemble + TTA)
                   → sin cara → RAMA CORPORAL E3B (MiVOLO/PAR, age_source='body')
  → voting ponderado (facial 1.0 / corporal 0.5) → commit + lock (+ histéresis)
  → Re-ID ArcFace (conteo único, herencia de demografía)
```

## Demo de captura
```powershell
.\venv\Scripts\python.exe demo.py --source 0
.\venv\Scripts\python.exe demo.py --source "rtsp://user:pass@ip:554/Streaming/Channels/101"
.\venv\Scripts\python.exe demo.py --source video.mp4 --loop --detect
```

## Benchmark y evaluación
```powershell
.\venv\Scripts\python.exe tools\benchmark.py --source video.mp4 --frames 200
.\venv\Scripts\python.exe tools\eval_demografia.py --csv clips\labels.csv
```
`eval_demografia.py` recibe un CSV `archivo,genero,rango_edad` y produce matriz
de confusión de género, accuracy de edad ±1 rango y desglose facial/corporal.

## Mapa de calor
El sistema acumula la posición (pies) de cada persona y genera un mapa de
calor por cámara:
- **Cliente**: menú de clases → ☑ "Mapa de calor" → el servidor pinta el
  overlay (colormap JET) sobre el video en vivo.
- **Dashboard** (`http://localhost:5000`): sección "Mapa de calor por cámara"
  con el acumulado de sesión + zonas calientes (se actualiza cada 5 s; los
  snapshots los escribe el servidor en `output/heatmap/`).
- **Metadata**: cada frame incluye `heatmap.zonas_calientes` (coordenadas
  relativas 0..1 con intensidad).
- **Nombres por cámara**: el cliente envía `camera_name` (canal DVR
  alias+nombre, o título de ventana, o "Camara N") y el dashboard lo muestra
  en vez del UUID.
- **Histórico por horas**: al cerrar cada hora se guarda el heatmap de ESA
  hora en `output/heatmap/history/<cámara>/<YYYY-MM-DD_HH>.png|json`
  (retención: 48 h). En el dashboard, el selector "Periodo" de cada tarjeta
  permite ver "En vivo (sesión)" o cualquier hora pasada con su propio
  ranking de zonas.
Parámetros en `AnalyticsConfig` (`HEATMAP_*`): grilla, half-life del
decaimiento (5 min), alpha del overlay, cadencia de snapshots, retención
del histórico (`HEATMAP_HISTORY_KEEP`).

## Multi-cámara (VRAM)
`get_camera_processor(camera_id)` crea un procesador por cámara con **estado de
tracking aislado**, pero **comparte** los modelos pesados: YOLO, las sesiones
ONNX (genderage, ArcFace, MiVOLO, PAR) y el re-identificador (una sola DB =
conteo único entre cámaras). Resultado: la VRAM es **prácticamente plana** al
añadir cámaras (medido: 1 cámara ≈ 5 cámaras), holgadamente bajo 6.5 GB. Solo
los detectores OpenCV ligeros (YuNet/Caffe) se cargan por cámara, por
thread-safety.

## Configuración por ángulo / flags (sin tocar código)
Variables de entorno:
- `CAMERA_ANGLE_BY_ID="1:cenital,2:lateral"` — perfil por camera_id. En cenital
  se **desactiva el filtro de aspecto** (la persona se ve más ancha que alta).
- `DEFAULT_CAMERA_ANGLE=frontal|lateral|cenital` — ángulo global por defecto.
- `ENABLE_TENSORRT=true` — antepone TensorRT a los providers ONNX (default OFF).

**Auto-detección (por defecto)**: el sistema **deduce el ángulo solo** a partir
de la forma de los bboxes de personas (cenital = personas anchas/cuadradas,
frontal/lateral = personas altas) y ajusta el filtro de aspecto sin
configuración. El cliente envía `"camera_angle": "auto"` (ya integrado en
`render_box.py`); el servidor detecta y reporta el resultado en
`metadata.camera_angle` (`efectivo`, `auto_detectado`, `mediana_hw`...).

**Override manual**: si el cliente envía `"camera_angle": "cenital"`
(o `frontal`/`lateral`) en `data`, ese valor **gana** sobre la auto-detección
para esa cámara. Volver a `"auto"` reactiva la detección automática.

Parámetros de la auto-detección en `config.py`: `AUTO_ANGLE_DEFAULT`,
`AUTO_ANGLE_MIN_SAMPLES` (40), `AUTO_ANGLE_CENITAL_HW_MAX` (1.25).

Umbrales en `src/analityc/core/analytics/config.py` (`AnalyticsConfig`):
- Rama corporal: `DEMO_BODY_*` (peso 0.5, gates de tamaño/blur, conf mínima).
- Lock/histéresis: `DEMO_HYSTERESIS_ENABLED` (OFF por defecto = cero parpadeo).

## Metadata nueva (no rompe la API legacy)
El resultado del clasificador añade: `age_source` ('face'/'body'/'reid'),
`gender_confidence`, `n_face_samples`, `n_body_samples`. Las categorías legacy
(`Hombres`/`Mujeres`/`Niños`/`Personas`) se mantienen. El reporte de calidad
(`get_quality_report()`) demuestra de forma auditable que **no se aceptan
muestras faciales borrosas** (`cero_borrosas_aceptadas`).