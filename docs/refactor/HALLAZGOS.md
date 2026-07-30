# HALLAZGOS — registro de bugs y riesgos del ecosistema ELDE

Registro vivo exigido por la regla 4 del plan de refactorizacion: los bugs se
anotan aqui y **no se corrigen** salvo aprobacion explicita.

Estados: `ABIERTO` · `CORREGIDO` · `ACLARADO` (resulto no ser un bug).

> **Nota de transparencia.** Los hallazgos H-01 a H-04 se detectaron y
> corrigieron el 29-jul-2026 **antes** de que existiera este plan de
> refactorizacion, durante una peticion previa de soporte. Quedan registrados
> por trazabilidad. De H-05 en adelante no se ha tocado nada.

---

## H-01 · Cliente de tienda aborta al cerrarse · `CORREGIDO`

**Sintoma.** `tienda_view` se cerraba con un crash de Windows en cada salida.
Seis eventos identicos en el visor de sucesos (28 y 29-jul): `python.exe` /
`Qt6Core.dll`, excepcion `0xc0000409`, offset `0x1bfa8`. Sin traceback de
Python, porque es un `qFatal()` de Qt que aborta el proceso.

**Causa.** Un `QThread` destruyendose mientras seguia corriendo. El principal,
`WindowScannerThread` (`tienda_view/src/core/windows_detector.py`), escanea
ventanas cada 300 ms durante toda la vida de la app; su `stop()` hacia
`running = False` + `quit()` pero **sin `wait()`**, y el unico enganche era
`destroyed.connect(...)`, que llega cuando el hilo ya se esta destruyendo.
Ademas el singleton `windows_monitor` se construye al importar
`core.window_global`, **antes** de que exista la `QApplication`, por lo que no
podia engancharse a `aboutToQuit` desde su `__init__`. No habia ni un solo
`closeEvent` en todo el cliente.

**Evidencia.** Reproducido de forma aislada: sin el arreglo el proceso sale con
`0xC0000409`; con el arreglo, con `0`.

**Correccion.** `stop()` espera de verdad (`wait` + `terminate` de reserva);
`Windows_monitor.stop_scanner()` idempotente; `main.py` conecta
`app.aboutToQuit`. En `render_box.py` se anadio `_stop_all_workers()` para el
`RTSPWorker` y los workers de VLM, que se bloquean en `requests` hasta 180 s.

---

## H-02 · El boton Dashboard apuntaba a un puerto muerto · `CORREGIDO`

**Sintoma.** El boton «Dashboard» del cliente de tienda no abria nada.

**Causa.** Abria `http://localhost:5030`, puerto del panel de retail eliminado
en la simplificacion del 27-jul (hoy solo vive en
`_backup_simplificacion_20260727/tienda_dashboard.py`). El dashboard real lo
sirve el propio servidor en `/dashboard` sobre el 9000.

**Correccion.** El boton deriva la URL de `server_ws_url` (el servidor al que
el cliente ya esta conectado) en vez de un puerto fijo, con `DASHBOARD_URL`
como override opcional.

---

## H-03 · Las capturas perdian la camara de origen · `CORREGIDO`

**Sintoma.** Las 204 capturas en disco tienen `camera: "cam"`, un valor
generico. Imposible desglosar demografia por pasillo.

**Causa.** `app.py` solo asignaba `_camera_display_name` si el cliente mandaba
`camera_name`; si llegaba vacio, `person_amazona_inference.py:2498` caia al
literal `"cam"`. Los heatmaps si usaban el `camera_id` real, de ahi la
incoherencia entre ambas fuentes.

**Correccion.** `app.py` usa `camera_name or str(camera_id)`. Solo afecta a
capturas **nuevas**; las 204 antiguas no son recuperables por pasillo.

---

## H-04 · Los venv no aislan · `ABIERTO` (entorno, no codigo)

Los venv se crearon con `--system-site-packages`:
`include-system-site-packages = true` y `ENABLE_USER_SITE = True`. **PySide6
6.10 y shiboken6 no estan en ningun venv**: salen de
`AppData\Roaming\Python\Python312\site-packages`, igual que torchreid en el
servidor.

**Riesgo.** Actualizar PySide6 afecta a los 4 clientes a la vez, y un
`pip install` dentro de un venv puede no ser el paquete que se acaba usando.
Los crashes de Windows reportan `C:\Program Files\Python312\python.exe`, lo que
despista al diagnosticar.

Ademas, `tienda_view\venv\pyvenv.cfg` registra que ese venv se creo para
`windows_managers_view`: es una copia.

**Por que no se corrige.** Recrear los venv sin `--system-site-packages` obliga
a revalidar torch/CUDA del servidor. Decision del usuario.

---

## H-05 · `selector.py` documenta mal a Amazonas View · `ABIERTO`

`selector.py:137-141` afirma que Amazonas View es un «proyecto aparte, backend
propio» y lo marca `needs_server=False`. Es **falso**: su
`INICIAR_AMAZONAS.bat` lanza el mismo `iniciar_servidor_headless.py` que la
tienda. Los cuatro clientes dependen del servidor unico.

**Impacto.** Quien arranque Amazonas desde el selector confiando en el
comentario asumira que no necesita el servidor. Afecta ademas a la premisa de
arquitectura del refactor.

---

## H-06 · `requirements.txt` del servidor: UTF-16 y con dependencias que faltan · `ABIERTO`

**Correccion de una cifra mia.** En el HITO 0 dije que tenia «1796 lineas».
Era **falso**: el archivo esta en **UTF-16LE** (con BOM `ff fe`, tipico de
`pip freeze > requirements.txt` en Windows PowerShell) y `grep`, al leerlo como
UTF-8, devolvia una cuenta absurda. Tiene **92 lineas**.

**Lo que si es cierto, verificado:**

1. **Es un volcado de `pip freeze`**: el entorno entero en orden alfabetico
   (`altgraph`, `annotated-doc`, `anyio`, `asyncio`...), no una lista curada.
   De los 92 paquetes declarados, solo **32** se importan de verdad; **74**
   estan declarados y no se usan.
2. **Faltan dependencias que el servidor necesita para arrancar**:
   `tensorrt`, `supervision`, `timm`, `safetensors`, `python-socketio`,
   `pynvml`, `cpuinfo`. Una instalacion limpia desde este `requirements.txt`
   **no produce un servidor funcional**.
3. **El UTF-16 no rompe a pip** (su `auto_decode` reconoce el BOM: comprobado),
   pero si rompe cualquier otra herramienta que lo lea como UTF-8: `grep`,
   editores, scripts de CI y el propio analizador de este refactor.

**Impacto.** El punto 2 es el grave: no hay forma de reconstruir el entorno del
servidor en otra maquina a partir del repositorio.

---

## H-07 · El punto de entrada del servidor no estaba versionado · `CORREGIDO`

`iniciar_servidor_headless.py` —el arranque real que usa
`INICIAR_TIENDA.bat`— estaba **sin versionar**, junto con el paquete
`vigilante_amazonas` completo. Recuperados en el commit de respaldo `6181b67`.

---

## H-08 · El reinicio a cero del dashboard no pide confirmacion · `ABIERTO`

**Contexto.** Se investigo la «desaparicion» de `output/heatmap/`. Resulto
**no ser un bug**: el endpoint de reinicio de `dashboard.py:345-433` *mueve*
—no borra— capturas, rostros, galeria Re-ID y mapas de calor a
`output/papelera/<fecha_hora>/`. Hubo un reinicio a las 16:19:11 del 29-jul y
una restauracion manual a las 16:30; los 19 heatmaps y las 204 capturas estan
intactos con su fecha original.

**El riesgo real que queda.** Ese endpoint borra de un golpe *toda* la
analitica acumulada —incluida la galeria biometrica— y por lo visto se puede
disparar sin una confirmacion que deje claro el alcance. Que sea reversible
depende de que nadie vacie la papelera.

---

## H-09 · Artefactos de ejecucion sin ignorar · `CORREGIDO`

`perimetrales-view` acumulaba 964 capturas de pantalla (38 MB) y `Amazonas
View` 408 capturas, todo sin versionar y sin regla de ignorado, a un paso de
entrar al historial. Anadidos a `.gitignore` en el commit de respaldo.

---

## H-11 · El `camera_id` es aleatorio: la analitica por camara no se acumula · `ABIERTO` · **critico**

**Causa.** `tienda_view/src/gui/components/render_box/render_box.py:195`:

```python
self.component_key = str(uuid.uuid4())
```

Ese `component_key` es lo que se envia al servidor como `camera_id`
(`render_box.py:663`). Se genera **al construir el panel de video**, asi que
cada arranque de la aplicacion —o cada panel nuevo— inventa una camara
distinta a ojos del servidor.

**Consecuencias.**

1. Los heatmaps, el conteo y la demografia se acumulan bajo un UUID nuevo en
   cada sesion: **no hay historico por camara**, solo fragmentos sueltos.
2. Cualquier nombre que se asigne a un `camera_id` (`config/pasillos.json`)
   queda obsoleto en el siguiente arranque.
3. El ranking de pasillo mas/menos frecuentado **no es comparable** entre
   sesiones.

**Evidencia.** De los 7 `camera_id` con heatmap, las imagenes de fondo se
agrupan por hash en solo **4 escenas**, y al mirarlas son en realidad **2**:

| camera_id | Muestras | Escena real |
|---|---:|---|
| `de60bb79…` | 13360 | Corredor de oficina, «Camera 12». La unica camara real |
| `a45de547…` | 795 | mismo videoclip de prueba |
| `5e1834aa…` | 54 | mismo videoclip de prueba |
| `9e0bf6b0…` | 4 | mismo videoclip (solo cambia el poligono ROI dibujado) |
| `18dbc565…` | 4 | mismo videoclip |
| `a0eae1a7…` | 4 | mismo videoclip |
| `4465df4d…` | 3 | mismo videoclip |

Seis de los siete son la misma grabacion reprocesada en sesiones distintas.
Ademas, la escena real **no es un supermercado**: es un corredor con puertas,
reloj de pared y cartelera informativa.

**Correccion propuesta (requiere aprobacion).** Ya existe un identificador
estable a mano: `_camera_display_name()` (`render_box.py`) resuelve
`alias del canal DVR > titulo de la ventana > "Camara N"`. Basta derivar el
`camera_id` de ahi —o persistir el UUID junto al canal en la configuracion del
cliente— para que la identidad sobreviva a los reinicios. Es un cambio de
comportamiento, asi que no se toca sin tu visto bueno.

**Nota.** Hasta que esto se arregle, cualquier metrica «por pasillo» del
dashboard de tienda describe sesiones, no lugares.

---

## H-12 · Dos carpetas divergentes publican en el mismo repo de GitHub · `ABIERTO`

Topologia de remotos actual:

| Carpeta local | Remoto configurado |
|---|---|
| `perimetrales-view` | `view.official.git` (origin) **+** `Amazonasview.git` |
| `windows_managers_view` | `view.official.git` (origin) |
| `Amazonas View` | `Amazonasview.git` (origin) |
| `SERVER-IA PERIMETRALES` | `SERVER-IA.git` (origin) |

**El problema.** `perimetrales-view` y `windows_managers_view` son dos clientes
distintos, con contenido divergente, y los dos tienen su `origin/main` apuntando
a **`view.official.git`**. Quien haga `git push` segundo sobrevive; el trabajo
del otro queda enterrado en el remoto. Lo mismo, en menor grado, con
`Amazonasview.git`, que es origin de `Amazonas View` y a la vez segundo remoto
de `perimetrales-view`.

**Estado.** Los remotos estan vivos: ultimos push entre el 29-may y el
2-jul-2026, y localmente solo hay 1-3 commits sin publicar (los de respaldo de
este refactor). No hay perdida detectada **todavia**.

**Correccion propuesta.** Es una de las razones de la decision del HITO 2:
absorber los subrepos en el monorepo y publicar en **un unico remoto nuevo**,
dejando los tres actuales como archivo de solo lectura. Requiere que el usuario
cree el repositorio vacio en GitHub.

---

## H-10 · Peso muerto en la raiz del proyecto · `ABIERTO`

- `modelos NVIDIA/`: **50 GB**.
- `hik-connect/`: 3954 archivos de SDK de terceros (~200 MB en `.exe`, `.dll`,
  `.pdb`, PDF), el 97% del arbol de la raiz, sin codigo propio salvo una nota.
- `PerimetralesView_cliente.zip`: 185 MB.

Excluidos del monorepo por `.gitignore`, pero siguen ocupando disco y
enturbiando cualquier busqueda.
