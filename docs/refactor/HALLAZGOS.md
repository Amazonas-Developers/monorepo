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

## H-11 · El `camera_id` es aleatorio: la analitica por camara no se acumula · `CORREGIDO en tienda` · **critico**

> **Estado (30-jul-2026).** Corregido en `tienda_view`. Los otros **tres
> clientes siguen igual** (`perimetrales-view`, `windows_managers_view`,
> `Amazonas View`): tienen su propia copia del mismo `uuid.uuid4()`. No se
> replica el arreglo cuatro veces a mano a proposito — el HITO 4 extrae
> `render_box` al nucleo compartido y entonces la correccion es una sola.
>
> **La correccion:** `camera_id` pasa a ser un identificador estable
> (`_device_id()`), con esta prioridad:
>
> | Fuente | Ejemplo | Estabilidad |
> |---|---|---|
> | Canal DVR: serie del equipo + canal | `dvr-J12345678-2` | permanente, identifica la camara fisica |
> | Titulo de la ventana capturada | `win-iVMS-4200` | mientras la aplicacion se llame igual |
> | Posicion del recuadro | `box-3` | dentro de la misma disposicion |
>
> `component_key` **se conserva** como clave de enrutado del recuadro (el
> servidor no lo usa para nada), asi que dos paneles que muestren la misma
> camara comparten `device_id` sin pisarse las respuestas.
>
> **Dos regresiones que la correccion habria introducido, detectadas y
> arregladas:** el cliente comparaba el `camera_id` que devuelve el servidor
> contra `component_key` en dos sitios (`render_box.py`). Al dejar de ser el
> mismo valor, esas comparaciones fallaban siempre y **la imagen procesada no
> se habria mostrado nunca**. Ahora se comparan contra `_device_id()`.
>
> **Efecto colateral bueno:** el planograma (zonas de tienda) tambien se
> guardaba por `camera_id`, asi que las zonas definidas se perdian en cada
> reinicio. Con el id estable persisten.
>
> **8 pruebas** en `packages/elde_core/tests/test_device_id.py`, la principal:
> el mismo canal DVR produce el mismo id en dos sesiones distintas.
>
> Lo que **no** recupera: los datos ya acumulados bajo los UUID viejos no se
> pueden reasignar. Se empieza a acumular de cero, como estaba previsto en el
> riesgo 5 del HITO 2.

### Descripcion original

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

## H-14 · El cliente de tienda muestra un modo y envia otro · `CORREGIDO`

**Sintoma.** El selector de inferencia mostraba «Personal de Amazonas», pero el
servidor recibia `type_inference: "VigilanteAmazonas"`. Confirmado con
`GET /health` mientras el cliente («ELDE Tienda 🛒», PID 25980) estaba
conectado y enviando.

**Causa.** Una secuencia de cuatro pasos en `custom_status_bar.py`:

1. Llega `type_inference_default = last_inference`, leido de la configuracion
   **persistida** — podia ser de otro negocio, de una sesion anterior.
2. `findText("VigilanteAmazonas")` da -1 porque este cliente solo ofrece
   `['Seleccione...', 'Personal de Amazonas']`, asi que cae a
   «Personal de Amazonas» (linea 93) y **eso es lo que se muestra**.
3. `setDisabled(True)` (linea 95): el operador **no puede corregirlo** desde la
   interfaz.
4. `currentTextChanged` se conecta en la linea 98, **despues** del
   `setCurrentIndex`, asi que la normalizacion nunca se notifico.

Y `windows_main.py:137-138` arrancaba la conexion con el valor **crudo**, no
con el normalizado.

**Lo irónico:** el comentario de `custom_status_bar.py:79-82` dice que el
selector se limita «para que el operador no elija por error un modo de otro
negocio». Un valor persistido lograba exactamente eso, y sin posibilidad de
deshacerlo.

**Correccion.** Nueva `CustomStatusBar.modo_seleccionado()` devuelve lo que el
selector muestra de verdad, y `windows_main` arranca con eso. Una sola fuente
de verdad: lo que se ve es lo que se envia. Corrige tambien el caso
`last_inference = None`, que antes mostraba «Personal de Amazonas» y **no
conectaba con nada**.

**Impacto en el refactor.** Es la razon por la que la captura de payloads del
HITO 3 solo recogio `VigilanteAmazonas`: el cliente de tienda nunca envio su
propio pipeline.

---

## H-13 · Claves de Hik-Connect en claro y **ya publicadas en GitHub** · `ABIERTO` · **critico / seguridad**

**Que hay.** El App Key y el App Secret de Hik-Connect estan escritos en claro
en el codigo, repetidos en tres archivos (valores enmascarados aqui a
proposito, regla 8):

| Archivo | Donde |
|---|---|
| `get_url.py` lineas 11-12 | en las **4** copias del script (un cliente cada una) |
| `Amazonas View/HIKCONNECT_INTEGRATION.md` | documentacion |
| `hik-connect/api hik.txt` | notas de la API |
| `perimetrales-view/.env` | **no** estaba en riesgo (los `.env` siempre estuvieron ignorados), pero hay que actualizarlo al rotar |

Barrido completo del arbol de trabajo: esos son **todos** los sitios donde
aparecen las claves. No hay mas copias escondidas en respaldos ni en venvs.

```
API_KEY    = "<<6-chars-enmascarados>>…"   (32 caracteres)
API_SECRET = "<<6-chars-enmascarados>>…"   (32 caracteres)
```

**Lo grave: ya estan expuestas.** `get_url.py` esta versionado en el historial
de `perimetrales-view`, `windows_managers_view` y `Amazonas View`, y esos tres
repositorios tienen remoto en GitHub con push hasta el 2-jul-2026. Las claves
llevan meses publicadas.

**Que se ha hecho.** Los tres archivos se excluyeron del monorepo nuevo y se
purgaron de su historial con `filter-branch` (verificado: no aparecen en
ningun commit). Los archivos siguen en disco. **Esto no desexpone nada**: solo
evita repetir la fuga en el repositorio nuevo.

**Que hace falta, y solo puedes hacerlo tu:**

1. **Rotar el App Key y el App Secret** en la consola de Hik-Connect. Es lo
   unico que corta el acceso de verdad: quitar las claves del codigo no las
   borra del historial de GitHub ni de los clones de terceros.
2. Decidir que se hace con los tres repos antiguos (borrarlos, hacerlos
   privados o reescribir su historial).
3. Una vez rotadas, las claves nuevas van a `.env` —nunca al codigo— y la
   documentacion puede volver al repositorio.

**Nota.** El proximo paso natural del refactor (regla 6, sacar todo el hardcode
a configuracion) resuelve la causa; la rotacion resuelve el incidente.

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
