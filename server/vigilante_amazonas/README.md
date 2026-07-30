# 🛡️ VIGILANTE-AMAZONAS

Sistema de videovigilancia inteligente perimetral: **detección multiclase en
tiempo real** (persona, personal_seguridad, moto, carro, camioneta, camion,
objeto), **Re-Identificación de personas de interés** (rostro ArcFace +
vestimenta CLIP/OSNet), **verificación por VLM** (Qwen2.5-VL) y **alertas en
tiempo real** (Socket.IO + integración con el cliente perimetrales-view).

---

## Arquitectura

```
Cámaras RTSP / cliente perimetrales-view (websocket :9000 del servidor ELDE)
        │
        ▼
┌──────────────────────── cuda:0 (RTX 5060 Ti) ────────────────────────┐
│ YOLO26m TensorRT FP16 (lote batch 4, imgsz 1280) + ByteTrack/cámara  │
└──────────────────────────────────────────────────────────────────────┘
        │  (hilo analizador asíncrono: la detección NUNCA espera)
        ▼
┌─────────────────── cuda:1 (3090 Ti #1, hoy → cuda:0) ────────────────┐
│ ClasificadorSeguridad (CLIP zero-shot) → reetiqueta personal_seguridad│
│ MotorReID: YuNet+ArcFace (rostro 512d) + CLIP/OSNet (vestimenta)     │
│           galería en memoria (recarga en caliente desde el dashboard)│
└──────────────────────────────────────────────────────────────────────┘
        │ score en zona gris (0.45–0.65)          │ match confirmado
        ▼                                          ▼
┌── cuda:2 (3090 Ti #2, hoy → cuda:0) ──┐   ┌── EmisorAlertas ─────────┐
│ VerificadorVLM Qwen2.5-VL (cola       │   │ SQLite + snapshot        │
│ no bloqueante, JSON estructurado)     │──▶│ Socket.IO :8091          │
└───────────────────────────────────────┘   │ metadata.alerts (cliente)│
                                            └──────────────────────────┘
Dashboard FastAPI :8090 (galería, historial, estado, visor MJPEG /debug/<cam>)
```

## Instalación

1. **Python 3.11+** con venv (se reutiliza `SERVER-IA PERIMETRALES\venv`).
2. **PyTorch cu128** (OBLIGATORIO para Blackwell sm_120 — ver problemas abajo):
   ```
   pip install torch==2.8.0+cu128 torchvision==0.23.0+cu128 --index-url https://download.pytorch.org/whl/cu128
   ```
3. **TensorRT variante CUDA-12 explícita** (el metapaquete `tensorrt` a secas
   jala dependencias CUDA-13 que fallan en Windows):
   ```
   pip install tensorrt-cu12==10.13.3.9
   ```
4. El resto: `pip install -r vigilante_amazonas\requirements.txt`
5. Verificar el entorno (criterio del Hito 1):
   ```
   venv\Scripts\python.exe vigilante_amazonas\verificar_entorno.py
   ```

## Operación

| Acción | Comando |
|---|---|
| Arrancar todo (autónomo) | `venv\Scripts\python.exe vigilante_amazonas\main.py` |
| Bajo PM2 | `pm2 start vigilante_amazonas\ecosystem.config.js` |
| Dashboard | http://127.0.0.1:8090 |
| Alertas Socket.IO | puerto 8091, evento `alerta_persona` |
| Visor de depuración | http://127.0.0.1:8090/debug/`<camara>` |
| Prueba de carga 30 min | `venv\Scripts\python.exe vigilante_amazonas\pruebas\prueba_carga.py` |

- Las cámaras RTSP se declaran en `config.py` → `CAMARAS_RTSP` (también
  acepta rutas de archivos de video para pruebas).
- **Modo websocket** (el usado en producción con perimetrales-view): el
  cliente muestra las cámaras y envía frames al servidor ELDE
  (`src/app/app.py` :9000); en el combo del pie del cliente se elige el modo
  **"VigilanteAmazonas"**. No requiere `CAMARAS_RTSP`; basta `main.py` en
  modo solo-servicios o el propio servidor ELDE.
- Personas de interés: se registran en el dashboard (fotos de rostro y/o
  vestimenta); el embedding se calcula al subir y la galería se recarga EN
  CALIENTE, sin reiniciar.

## Configuración (config.py)

Todo parámetro vive en `vigilante_amazonas\config.py`: umbrales
(`UMBRAL_ROSTRO` 0.55, `UMBRAL_VESTIMENTA` 0.70, `ZONA_GRIS_VLM` 0.45–0.65),
cooldown anti-spam (60 s por persona/cámara), throttles, prompts del
clasificador de seguridad, dispositivos y puertos.

- `VESTIMENTA_BACKEND`: `"clip"` (activo; ganó la comparativa del Hito 4 con
  margen intra/inter +0.63 vs +0.29 de OSNet) o `"osnet"` (conmutable;
  re-medir con material real multi-cámara).
- `VLM_HABILITADO`: `"auto"` carga Qwen2.5-VL-3B solo si hay GPU dedicada o
  VRAM libre suficiente; sin VLM, la zona gris se rige por
  `ZONA_GRIS_SIN_VLM_EMITIR`.

## Estado del hardware (jul-2026)

⚠️ **Solo está instalada la RTX 5060 Ti (16 GB, Blackwell sm_120).** Las dos
RTX 3090 Ti del diseño NO están visibles (ni en `nvidia-smi` ni en torch).
`config.resolver_dispositivo()` degrada `cuda:1`/`cuda:2` → `cuda:0`
automáticamente y usará las 3090 en cuanto aparezcan, sin cambiar código.

Consecuencias del modo degradado 1-GPU (medidas):
- VRAM total usada con TODO cargado (YOLO+CLIP+ArcFace+VLM 3B): ~9.6 GB.
- 4 cámaras simuladas + análisis completo: **~5.7 FPS/cámara sostenidos**
  (la contención GIL/GPU entre detector y analizador limita el ciclo; la
  detección sola rinde 15.8 ciclos/s). Con 2 cámaras: ~9 FPS.
  Con las 3090 Ti, Re-ID y VLM salen de cuda:0 y el objetivo vuelve a 10.

## Solución de problemas

**Blackwell sm_120 / "no kernel image is available"**
: El torch instalado no trae sm_120. Solución: build cu128
  (`torch==2.8.0+cu128`). `cuda.is_available()` puede dar True y aun así
  fallar TODO kernel — por eso `verificar_entorno.py` ejecuta un matmul real
  en cada GPU.

**TensorRT falla al instalar (nvidia-cuda-runtime-cu13)**
: Instalaste el metapaquete `tensorrt`. Usar la variante explícita
  `tensorrt-cu12==10.13.3.9` (sm_120 necesita TRT ≥ 10.8).

**El engine no carga / "input size not equal to max model size"**
: Los `.engine` son ESPECÍFICOS de GPU, imgsz y batch. `yolo26m_1280_b4.engine`
  es batch estático 4 (el detector rellena los lotes automáticamente). Si
  cambia la GPU: reconstruir con
  `YOLO("yolo26m.pt").export(format="engine", half=True, imgsz=1280, batch=4)`.
  Mientras tanto el detector cae solo al siguiente candidato (`.pt` FP16).

**Error 1455 de Windows al cargar el VLM ("archivo de paginación demasiado pequeño")**
: RAM insuficiente al materializar los shards. Ya mitigado: la carga usa
  `low_cpu_mem_usage + device_map` (los pesos van directo a la GPU). Si
  reaparece: ampliar el archivo de paginación o `VLM_HABILITADO="no"`.

**El VLM confirma/niega todo**
: El prompt importa. El validado (Hito 6) pide describir por pasos ambas
  imágenes y ser escéptico (rostro no visible ⇒ false). Está en
  `verificador_vlm.py::_PROMPT_TAREA`; el 3B es un verificador AUXILIAR — el
  umbral duro de ArcFace sigue mandando. Con la 3090 Ti dedicada se puede
  subir a Qwen2.5-VL-7B-AWQ (`VLM_MODELO_ID`).

**Cámara RTSP caída**
: La fuente reintenta sola con backoff exponencial 1→60 s sin tumbar nada;
  ver `logs/vigilante.jsonl` (evento "sin conexión; reintento en Ns").

**Puertos ocupados (8090/8091)**
: Otro proceso del sistema quedó vivo. `netstat -ano | findstr :8090` y
  `taskkill /F /PID <pid>`. Tras matar un proceso con CUDA, el puerto tarda
  unos segundos en liberarse.

## Estructura

```
vigilante_amazonas/
├── config.py                  # TODA la configuración
├── main.py                    # orquestador (arranque/apagado limpio)
├── verificar_entorno.py       # checks de GPU/torch/TensorRT/modelos
├── adaptador_websocket.py     # modo "VigilanteAmazonas" del servidor ELDE
├── ecosystem.config.js        # PM2 (Windows)
├── captura/fuente_rtsp.py     # hilos RTSP + colas descartables + backoff
├── deteccion/                 # detector (TRT b4), mapeo_clases, ByteTrack, motor
├── servicios/                 # seguridad (CLIP), Re-ID, galería, VLM, alertas,
│                              #   analizador asíncrono, núcleo compartido
├── web/                       # SQLite + dashboard FastAPI + UI en español
├── ejemplo_cliente/           # consumidor Socket.IO de ejemplo
├── pruebas/                   # tests por hito + prueba_carga.py
├── galeria/  snapshots/  db/  logs/
```
