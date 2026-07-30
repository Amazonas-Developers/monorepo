# Perfiles demográficos por cámara

Cada archivo `<camera_id>.json` de esta carpeta ajusta los umbrales del
pipeline de rostro/género/edad para UNA cámara, sin tocar la config
global (`src/analityc/core/analytics/config.py`) ni afectar al resto.

## Cómo funciona

- Al crearse el clasificador demográfico de una cámara, se busca
  `config/camera_profiles/<camera_id>.json`. Si existe, sus claves
  SOBREESCRIBEN el valor global de `AnalyticsConfig` solo para esa
  cámara. Si no existe, la cámara usa la config global tal cual.
- El `<camera_id>` es el mismo id con el que el cliente envía los frames
  (sanitizado a letras/números/`_`/`-`). Para descubrirlo: los archivos
  `output/debug_cajas/<camera_id>.jpg` o los logs
  `output/analytics_log_<camera_id>.jsonl` usan exactamente ese nombre.
- Las claves del JSON son nombres de atributos de `AnalyticsConfig`
  (p.ej. `"DEMO_MIN_CONTRAST": 12.0`). Las claves que empiezan con `_`
  se ignoran (sirven para comentar). Las desconocidas se avisan en el
  log y se descartan.
- Al arrancar, el log muestra qué perfil se aplicó:
  `Perfil demografico camara <id> aplicado (<archivo>): <claves>`.
- Los cambios requieren reiniciar el servidor (el perfil se lee al crear
  el procesador de la cámara).

## Claves más útiles para calibrar una cámara

| Clave | Global | Qué controla |
|---|---|---|
| `DEMO_POSE_MAX_PITCH_DEG` | 48 | Picado máximo aceptado (cámaras altas → subir) |
| `DEMO_POSE_MAX_YAW_DEG` | 35 | Giro lateral máximo de la cara |
| `DEMO_MIN_CONTRAST` | 18 | Contraste mínimo (escenas lavadas → bajar) |
| `DEMO_MIN_BLUR_VAR` | 25 | Nitidez mínima (Laplaciano) |
| `DEMO_YUNET_MIN_SCORE` | 0.70 | Score mínimo del detector de caras (piso duro: 0.55) |
| `DEMO_YUNET_UPSCALE_TARGET` | 480 | Resolución de entrada a YuNet (caras lejanas → subir) |
| `DEMO_MIN_PERSON_BBOX_W/H` | 60/140 | Tamaño mínimo de persona para intentar clasificar |
| `DEMO_FACE_ASPECT_MIN/MAX` | 0.85/1.45 | Aspecto válido de cara (lentes gran angular → ampliar) |
| `DEMO_MIN_EYE_FACE_RATIO` | 0.22 | Distancia interocular mínima relativa |
| `DEMO_MAX_ASYMMETRY` | 135 | Asimetría de iluminación tolerada |

Regla de la instalación (modo precisión máxima): en los perfiles por
cámara se relajan solo los filtros de CAPTACIÓN (pose/calidad/tamaño).
Los umbrales de PRECISIÓN del commit (`DEMO_MIN_CONFIDENCE`,
`DEMO_MIN_MARGIN`, `DEMO_ADAPTIVE_BRACKETS`, `DEMO_COMMIT_AGREE_RATIO`,
fast-commit, ensemble, TTA) se dejan en la config global: son los que
garantizan que antes se responda "Desconocido" que un género/edad
equivocado.

## Perfiles existentes

- `cam12.json` — Cámara 12: pasillo interior estrecho, cámara en esquina
  alta, gran angular con distorsión de barril, iluminación sobreexpuesta.
