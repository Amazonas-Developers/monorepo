# DEMOGRAFIA-AMAZONAS

Detección de personas con **género y rango de edad** sobre cámaras de
seguridad, para el cliente **Amazonas View**.

---

## Resultado: antes y después

El sistema devolvía género en el **1.8 %** de las capturas (2 de 109). Tras
la intervención, el pipeline real clasifica el **74.1 %**.

| Métrica | Antes | Después |
|---|---|---|
| **Capturas con género (pipeline real)** | **1.8 %** (2/109) | **74.1 %** (103/139) |
| **Capturas con género (+ reanálisis)** | — | **87.3 %** (178/204) |
| Cobertura en banco offline | 2.5 % | 91.8 % |
| Personas sin rostro que se clasifican | 0 % | ~90 % |
| Motivo cuando no clasifica | *(silencio)* | motivo explícito |

Ninguna captura se queda ya en «Analizando…». Las 204 acaban en un estado
explícito:

| Estado | Nº | Qué significa |
|---|---|---|
| Género + rango de edad | **178** | Resuelta |
| Persona sin identificar | 22 | El VLM ve a alguien, pero de espaldas o demasiado lejos para decidir |
| Modelos en desacuerdo | 3 | MiVOLO y el VLM discrepan: no se publica nada |
| No es una persona | 1 | El VLM miró la foto y confirmó que no hay nadie |

**El VLM también revisa lo que el filtro geométrico descarta.** Ese filtro
mira proporciones (alto/ancho, tamaño mínimo): es barato y necesario en
vivo, pero se equivoca con gente de perfil, muy recortada o pegada al
borde del encuadre. En el reanálisis, antes de dar una captura por
perdida, se le pide al VLM que **mire** la imagen y diga si hay una
persona y, si la hay, su género y edad. Medido sobre las 37 pendientes:
**rescató 11 personas reales** que la geometría había tirado. Van marcadas
con 🤖 y `origen_demografia: "vlm_rescate"` para que se distingan de las
que respalda MiVOLO.

### Reanálisis de capturas guardadas

Botón "Analizar pendientes" en el dashboard y en la pestaña Capturas del
cliente. Repasa las fotos que quedaron sin género aprovechando que, sobre
una imagen ya guardada, no hay FPS que sostener:

  * **TTA de 4 vistas** (original, espejo y dos reencuadres) que votan.
  * **Entrada dual** de MiVOLO cuando hay rostro guardado.
  * **VLM (Qwen2.5-VL-3B)** para lo que MiVOLO no decide o duda (< 0.70).
    Se enciende y apaga con el botón **VLM** (cliente y dashboard). Viene
    **encendido**; la preferencia se guarda en `output/vlm_reanalisis.txt`
    y sobrevive a los reinicios.

Medido sobre 204 capturas reales: MiVOLO+TTA resolvió 158 a 0.25 s por
foto; el VLM rescató 5 más de los casos difíciles, a 1.8 s por foto y
7.4 GB de VRAM. Si los dos modelos discrepan **no se publica nada**: se
marca `modelos en desacuerdo` y se deja el caso abierto.

### Por qué fallaba

No era un bug de software, eran dos hechos sumados (auditoría completa en
`AUDITORIA.md`):

1. **El 79 % de los crops no tiene rostro detectable.** La cámara ve sobre
   todo espaldas (pasillo, gente alejándose). En el 21 % restante, la
   mediana del rostro es de **29 px**.
2. **La rama corporal estaba activada pero sin modelo.** Ni `mivolo.onnx`
   ni `par_gender.onnx` existían en disco, así que salía en su primer `if`
   y devolvía `False` siempre.

Para 4 de cada 5 personas **no había ninguna vía de clasificación
habilitada**. No fallaba: no había con qué intentarlo.

### Qué se hizo

- **MiVOLO v2 en modo cuerpo** como ruta principal (no como fallback).
- **Cascada**: si el rostro no supera los filtros estrictos, se aprovecha
  igualmente pasándoselo a MiVOLO en entrada dual (rostro + cuerpo).
- **Filtro de recortes**: se descartan franjas de pared y fragmentos que
  el detector deja pasar y que el estimador etiquetaría como personas.
- **Telemetría**: un veredicto por track con motivos excluyentes, para que
  un `null` nunca vuelva a ser mudo.

---

## Analizar un archivo de vídeo

Se puede analizar material grabado, no solo cámaras en vivo. Dos formas,
ambas sobre una celda del cliente:

  * **Arrastrar el archivo** desde el Explorador y soltarlo en la celda.
  * El botón **▶** de la barra de la celda, que abre un diálogo.

Acepta 20 extensiones (`.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.webm`,
`.ts`, `.dav`…); por debajo es FFMPEG, así que en la práctica abre más.

Al soltarlo **la IA se enciende sola** —es lo que se está pidiendo— y el
vídeo avanza *en handshake* con el servidor: entrega un frame y espera la
respuesta antes de leer el siguiente. En vivo perder frames es inevitable;
con un archivo sería tirar material, porque esa persona no vuelve a pasar.
La contrapartida es que el análisis va al ritmo de la inferencia (~5 fps
medidos), no a la velocidad de reproducción. La celda muestra el avance y
el tiempo restante estimado.

Al terminar, el repaso del VLM se lanza **solo** sobre lo que quedó sin
género, y la pestaña Capturas se marca con un punto. No se cambia de
pestaña a la fuerza: puedes estar mirando otras cámaras.

Medido sobre un vídeo de 12 s con 6 personas: 180 frames en 36 s, 7
capturas, 3 resueltas en vivo y las 4 restantes por el VLM → **7/7 con
género y edad**.

---

## Instalación

Requiere el venv del servidor ya existente. **Usa siempre ese Python**: el
global de la máquina no tiene el stack completo.

```
venv\Scripts\python.exe scripts\verificar_entorno.py
```

Debe terminar con código 0. Comprueba driver, CUDA, `sm_120` (RTX 5060 Ti)
y —importante— si conviven `onnxruntime` y `onnxruntime-gpu`, que es un
conflicto silencioso: comparten espacio de nombres y el de CPU puede ganar
sin avisar. Saneamiento:

```
pip uninstall -y onnxruntime
pip install --force-reinstall onnxruntime-gpu
```

### Modelos necesarios

| Archivo | Qué es | Obligatorio |
|---|---|---|
| `models/classifiers/mivolo_v2.safetensors` | MiVOLO v2 (edad+género, cuerpo) | **Sí** |
| `models/classifiers/mivolo_v2.onnx` (+ `.onnx.data`) | El mismo, backend alternativo | No |
| `models/classifiers/genderage.onnx` | InsightFace (rama facial) | Recomendado |
| `models/classifiers/face_detection_yunet_2023mar.onnx` | Detector de rostro | Recomendado |
| `models/classifiers/w600k_r50.onnx` | ArcFace (Re-ID, identidad) | Recomendado |

---

## Cómo correr

```
venv\Scripts\python.exe iniciar_servidor_headless.py 9000
```

O el lanzador completo (servidor + dashboard + cliente):
`Amazonas View\INICIAR_AMAZONAS.bat`

- WebSocket: `ws://localhost:9000/ws`
- Dashboard: `http://localhost:9000/dashboard`

---

## Variables de entorno

| Variable | Efecto |
|---|---|
| `INFERENCE_DEVICE` | `auto` (por defecto), `cpu`, `cuda:0` |
| `ENABLE_TENSORRT` | Antepone TensorRT en ONNX. **No recomendado** (ver límites) |
| `MODELS_DIR` / `OUTPUT_DIR` | Rutas de modelos y salidas |
| `CONFIG_RELOAD` | Relee la configuración en cada consulta |

En el **cliente** (Amazonas View):

| Variable | Efecto |
|---|---|
| `AMAZONAS_SERVER_WS` | Servidor al que se conecta. Admite `host:puerto` o la URL completa. `INICIAR_AMAZONAS.bat` la fija a `ws://127.0.0.1:9000/ws`, que es el servidor que él mismo arranca. Sin ella se usa `ws://72.68.60.171:9000/ws`. |

Los umbrales del pipeline están centralizados en
`src/analityc/core/analytics/config.py`, documentados uno a uno.

---

## Herramientas de diagnóstico

| Script | Para qué |
|---|---|
| `scripts/verificar_entorno.py` | GPU, CUDA, providers, conflicto de onnxruntime |
| `scripts/banco_offline.py` | Corre el estimador sobre crops de disco + contact sheet |
| `scripts/resumen_telemetria.py` | Por qué los tracks no obtuvieron demografía |
| `scripts/benchmark_demografia.py` | FPS, latencia, VRAM, prueba de fuga |
| `scripts/preparar_etiquetado.py` | Genera hojas numeradas para etiquetar a mano |
| `scripts/evaluar_etiquetado.py` | Precisión real contra esas etiquetas |

En vivo: `GET /dashboard/api/telemetria`.

---

## Rendimiento (RTX 5060 Ti, 16 GB)

Frames desde disco, 3 personas por frame, detector YOLO26 TensorRT a 1280.

| Escenario | FPS | Latencia media | p95 |
|---|---|---|---|
| 1 cámara | 9.6 | 104 ms | **796 ms** |
| 4 cámaras (agregado) | 17.9 | 56 ms | 146 ms |

- Arranque: 5.9 s · VRAM tras cargar modelos: 74 MB · pico 149 MB
- MiVOLO: 57 ms con lote 1, **22 ms/persona con lote 8**
- Fuga de memoria: 600 iteraciones → RSS +14 MB **y estabilizado**, VRAM +0 MB

---

## Límites conocidos

Esto es lo que el sistema **no** puede hacer hoy. Se documenta para que
nadie prometa lo que la entrada no soporta.

1. **La precisión real todavía no está medida.** Hay cobertura (74 %), no
   una cifra de acierto validada. Para tenerla hay que etiquetar a mano
   los crops (`preparar_etiquetado.py`) y evaluar
   (`evaluar_etiquetado.py`). Cualquier porcentaje de precisión que se dé
   antes de eso sería inventado.

2. **El dataset de retratos no sirve para validar.** Un conjunto de fotos
   de estudio mide otro problema: allí el 98 % tiene rostro detectable con
   385 px de mediana; aquí es el 20 % con 29 px. Validar con eso daría una
   precisión alta y engañosa.

3. **Picos de latencia (p95 796 ms con 1 cámara).** MiVOLO se invoca una
   persona cada vez (57 ms) en lugar de por lotes (22 ms/persona). Cuando
   varias reclasificaciones coinciden en el mismo frame, se acumulan. La
   mejora clara es agrupar las peticiones en un lote; no se hizo porque
   toca el camino crítico y exige revalidar.

4. **No actives `ENABLE_TENSORRT`.** TensorRT no está instalado como
   librería para onnxruntime; al activarlo, ORT descarta también el
   proveedor CUDA y el modelo cae a **CPU (971 ms/crop, 42× más lento)**.
   Degrada de forma limpia y lo avisa en el log, pero no aporta nada.
   El backend recomendado es **PyTorch FP16** (23 ms/crop), que es el
   predeterminado; ONNX en CUDA va a 40 ms/crop.

5. **La edad es orientativa.** Se reporta como rango, nunca como año
   exacto. Sin rostro, la edad es inherentemente gruesa: en los dos casos
   con referencia conocida el género acertó pero la edad se desvió un
   bucket.

6. **La calidad de entrada ya viene degradada**: substream reescalado en
   el visor, JPEG q75 y captura de pantalla. Ninguna mejora de software
   crea información que no está en el píxel. Para subir el techo hay que
   acercar la cámara o subir la resolución de envío.

7. **El VLM: apagado en vivo, encendido en el reanálisis.** En tiempo
   real (`VLM_VERIFICADOR_ENABLED = False`) el modelo ni se carga. En el
   reanálisis a posteriori sí se usa (`REANALISIS_USAR_VLM = True`),
   porque ahí el coste es asumible. Ocupa 7.4 GB de VRAM mientras dura y
   se carga solo al lanzar un reanálisis.
   **Modelo fijado a Qwen2.5-VL-3B** (`output/vlm_model.txt`).
   El 7B **también está descargado** (16 GB en la caché de HuggingFace),
   pero medido sobre 28 capturas reales sale peor en todo:

   | Modelo | Resuelve | Acuerdo | s/foto | VRAM |
   |---|---|---|---|---|
   | **3B** | **23/28 (82 %)** | 96 % | **1.84** | **7.3 GB** |
   | 7B | 19/28 (68 %) | 95 % | 5.76 | 9.0 GB |

   El 7B se abstiene más ("desconocido") sobre cuerpos de espaldas
   degradados: es un modelo más prudente, pero para nuestro objetivo
   —cobertura— eso significa resolver menos, y encima 3× más lento.
   Con esta calidad de entrada, más parámetros no compensan: el límite
   está en el píxel, no en el modelo.
   Para cambiarlo: `echo 7b > output/vlm_model.txt`
   Comparar de nuevo: `venv\Scripts\python.exe scripts\comparar_vlm.py`

8. **`AgregadorDemografico` no está en el camino crítico.** La
   consolidación por track la hace `_TrackAccumulator`. Se conserva
   documentado; no lo conectes creyendo que ya está activo.

---

## Datos y retención

- **Vaciar detecciones** (botón en el dashboard, en el panel lateral del
  cliente y en la pestaña Capturas) no destruye nada: mueve las capturas,
  la galería del Re-ID y los mapas de calor a
  `output/papelera/<fecha_hora>/`, conservando la estructura. Recuperar es
  copiar esa carpeta de vuelta. La papelera **no se limpia sola**: si
  ocupa demasiado, bórrala a mano.
- `output/captures/` crece sin techo: no hay política de retención
  automática. Conviene definir un borrado periódico.
- La galería de identidades (`output/person_db/persons.pkl`) **nunca se
  borra sola** (`REID_RESET_POLICY = "never"`).
- Los agregados demográficos van desacoplados de la identidad salvo por
  el `person_uuid` que ya usaba el Re-ID.
