# Manual de inicio — Sistema ELDE (Servidor IA + Cliente Windows)

Cómo arrancar TODO el sistema de cero. Pensado para Windows + NVIDIA
(probado en Quadro RTX 4000 8 GB + 48 GB RAM).

Hay 3 piezas:
1. **Servidor de inferencia** (`SERVER-IA PERIMETRALES`) — FastAPI WebSocket en
   el puerto **9000**. Hace la detección + demografía en GPU.
2. **Cliente Windows** (`windows_managers_view`) — la app de escritorio que
   envía las cámaras al servidor y muestra el resultado.
3. **Dashboard web** (opcional) — panel de analítica (`webapp/`).

> ⚠️ **LO MÁS IMPORTANTE:** el servidor **DEBE** lanzarse con el **venv** del
> proyecto (`venv\Scripts\python.exe`), NUNCA con el `python` global. El Python
> global tiene un conflicto de `onnxruntime` que hace correr la IA en CPU (lento).
> El venv tiene el `onnxruntime-gpu` correcto → demografía en GPU.

---

## 0. Requisitos (una sola vez)

- Windows 10/11 + GPU NVIDIA con driver actualizado (`nvidia-smi` debe funcionar).
- Python 3.12 instalado.
- Los dos repos clonados en `C:\Users\<usuario>\Desktop\ELDE\`:
  - `SERVER-IA PERIMETRALES\`
  - `windows_managers_view\`

### 0.1 Preparar el venv del SERVIDOR (si no existe)
```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES"
python -m venv venv --system-site-packages
.\venv\Scripts\python.exe -m pip install --ignore-installed onnxruntime-gpu==1.23.2
```

### 0.2 Verificar GPU + modelos del servidor
```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES"
# CUDA activo en ONNX (debe listar CUDAExecutionProvider):
.\venv\Scripts\python.exe -c "import torch, onnxruntime as ort; ort.preload_dlls(); print(ort.get_available_providers())"
# Modelos presentes (esenciales OK; corporales opcionales):
.\venv\Scripts\python.exe scripts\setup_modelos.py
```
Si falta algún modelo **esencial**, el propio script dice cómo obtenerlo.
Los **corporales** (`mivolo.onnx` / `par_gender.onnx`) son opcionales: sin ellos
la demografía funciona solo con cara visible.

### 0.3 Preparar el venv del CLIENTE (si no existe)
```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\windows_managers_view"
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

---

## 1. Arrancar el SERVIDOR de inferencia (puerto 9000)

Elige UNA de las dos formas. **Siempre con el venv.**

### Opción A — con interfaz gráfica (recomendada)
```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES"
.\venv\Scripts\python.exe main.py
```
Se abre una ventana; pulsa el botón **Iniciar servidor**. Quedará escuchando en
`ws://0.0.0.0:9000`.

### Opción B — sin interfaz (headless / producción)
```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES"
.\venv\Scripts\python.exe -m uvicorn src.app.app:app --host 0.0.0.0 --port 9000
```

### Cómo saber que arrancó bien
En el log de arranque deberías ver:
- `Clasificador primario: ... | Detector: YuNet+keypoints+pose | Re-ID biométrico: OK`
- Al cargar los modelos ONNX, que usan **CUDA** (no CPU).
- `Re-identificador COMPARTIDO` / `Sesiones ONNX COMPARTIDAS` cuando entren varias cámaras.

---

## 2. Arrancar el CLIENTE Windows

```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\windows_managers_view"
.\venv\Scripts\activate
python src\main.py
```
(O simplemente doble clic en `init.bat`.)

En la app:
1. En la barra de estado, elige el tipo de inferencia **"Personal de Amazonas"**.
2. Conecta al servidor (URL por defecto `ws://<IP_DEL_SERVIDOR>:9000/ws`).
   - Si el servidor está en la MISMA PC: `ws://127.0.0.1:9000/ws`.
   - Si está en otra PC: usa su IP de red (ej. `ws://192.168.1.50:9000/ws`).
3. Agrega una cámara (RTSP del DVR Hikvision/Dahua, USB, o archivo) en un recuadro.
4. Activa el modo IA. Verás las personas con su género/edad pintadas por el servidor.

> El **ángulo de cámara es automático**: el cliente envía `camera_angle: "auto"`
> y el servidor deduce solo si es frontal/lateral/cenital y ajusta el filtro.
> No hay que configurar nada. (Para forzarlo manualmente, ver sección 5.)

---

## 3. (Opcional) Dashboard web de analítica
```powershell
cd "C:\Users\Sistema-1\Desktop\ELDE\SERVER-IA PERIMETRALES"
.\venv\Scripts\python.exe webapp\app.py
```
(O doble clic en `dashboard.bat`, pero ese usa el python global; mejor el venv.)

---

## 4. Orden de arranque (resumen rápido)
1. **Servidor** primero (sección 1) → esperar a que cargue los modelos.
2. **Cliente** (sección 2) → conectar y agregar cámaras.
3. (Opcional) Dashboard (sección 3).

---

## 5. Configuración del ángulo de cámara (opcional)

Por defecto es **automático**. Si quieres forzarlo:
- **Global** (todas las cámaras): variable de entorno antes de lanzar el servidor:
  ```powershell
  $env:DEFAULT_CAMERA_ANGLE = "cenital"   # o frontal / lateral
  .\venv\Scripts\python.exe main.py
  ```
- **Por cámara desde el cliente**: cambiar `self.camera_angle` de `"auto"` a
  `"cenital"`/`"frontal"`/`"lateral"` en el recuadro correspondiente
  (`render_box.py`). El manual del cliente lo enviará en el payload y gana sobre
  la auto-detección.

Otras variables útiles del servidor:
- `ENABLE_TENSORRT=true` — activa TensorRT en ONNX (default off).
- `CAMERA_ANGLE_BY_ID="1:cenital,2:lateral"` — mapa por camera_id.

---

## 6. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| La IA va lenta / demografía no clasifica | Lanzaste con el `python` global. Usa `venv\Scripts\python.exe`. Verifica con el comando de 0.2 que aparezca `CUDAExecutionProvider`. |
| El cliente no envía frames | No recibió `id_connection`. Revisa que el servidor esté arriba y la URL `ws://IP:9000/ws` sea correcta y alcanzable (firewall/puerto 9000 abierto). |
| `Repository not found` / `403` al hacer push | El `origin` apunta al repo equivocado o sin permisos. Ajustar con `git remote set-url origin <URL correcta>`. |
| No detecta personas vistas desde arriba | El auto-ángulo necesita ~40 personas para decidir; o fuérzalo a `cenital` (sección 5). |
| Falta `mivolo.onnx`/`par_gender.onnx` | Opcionales (rama corporal sin cara). Ver `scripts\setup_modelos.py`. |
| Mucha VRAM con varias cámaras | No debería: las sesiones ONNX se comparten (VRAM plana ~860 MB con 5 cámaras). Verifica con `nvidia-smi`. |

---

## 7. Herramientas de diagnóstico (servidor)
```powershell
# Rendimiento (FPS por etapa, latencia p50/p95, VRAM):
.\venv\Scripts\python.exe tools\benchmark.py --source video.mp4 --frames 200

# Exactitud de demografía (CSV: archivo,genero,rango_edad):
.\venv\Scripts\python.exe tools\eval_demografia.py --csv clips\labels.csv

# Probar una fuente de video aislada (captura universal):
.\venv\Scripts\python.exe demo.py --source 0          # webcam
.\venv\Scripts\python.exe demo.py --source "rtsp://usuario:clave@IP:554/Streaming/Channels/101"
```
