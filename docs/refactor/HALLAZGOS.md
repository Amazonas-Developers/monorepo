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

## H-04 · Los venv no aislan · `CORREGIDO`

> **CORREGIDO el 31-jul-2026 por orden del usuario.** Los 5 venv se recrearon
> SIN `--system-site-packages`: PySide6, cv2 y torch viven ahora dentro de
> cada venv. Clientes desde su `requirements.txt` (sin torch: ninguno lo
> importa, se colaba del user-site) + `elde_core` editable; servidor desde el
> freeze del entorno que funcionaba, con torch 2.8.0+cu128 del indice de
> PyTorch y **CUDA revalidado en la RTX 5060 Ti con una operacion real**.
> Amazonas ya estaba aislado y no se toco. Los venv viejos quedan como
> `venv_sistemasite_old` en cada carpeta hasta rodar los nuevos; luego se
> borran. Verificado: baterias de imports 12/14/12/11, servidor arrancando
> con sus 3 puertos, 7/7 pipelines en estricto y 12+65 pruebas.
>
> Bonus del freeze: la linea `opencv-contrib-python-rolling @ file:///...`
> del requirements del servidor era una linea MUERTA (H-06): el cv2 real
> siempre fue `opencv-contrib-python==4.12.0.88` de PyPI.

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

## H-05 · `selector.py` documenta mal a Amazonas View · `CORREGIDO`

> **Corregido el 30-jul-2026.** La entrada de Amazonas View pasa a
> `needs_server=True` y usa su `INICIAR_AMAZONAS.bat`. Antes estaba marcada
> `needs_server=False` por un comentario que afirmaba que era un «proyecto
> aparte con backend propio»: quien lo arrancara desde el selector se quedaba
> sin servidor.

`selector.py:137-141` afirma que Amazonas View es un «proyecto aparte, backend
propio» y lo marca `needs_server=False`. Es **falso**: su
`INICIAR_AMAZONAS.bat` lanza el mismo `iniciar_servidor_headless.py` que la
tienda. Los cuatro clientes dependen del servidor unico.

**Impacto.** Quien arranque Amazonas desde el selector confiando en el
comentario asumira que no necesita el servidor. Afecta ademas a la premisa de
arquitectura del refactor.

---

## H-06 · `requirements.txt` del servidor: UTF-16 y con dependencias que faltan · `CORREGIDO`

> **Corregido el 30-jul-2026.** Reescrito en UTF-8 sin BOM (92 → 98 paquetes) y
> anadidas las 6 dependencias que faltaban, con el nombre de **distribucion**
> correcto, que no coincide con el del import: `import tensorrt` lo aporta
> `tensorrt_cu12`, `import pynvml` lo aporta `nvidia-ml-py` y `import cpuinfo`
> lo aporta `py-cpuinfo`. Verificado que pip parsea los 98 requisitos.
>
> **Queda un aviso anotado en el propio archivo:** la linea de
> `opencv-contrib-python-rolling` apunta a un `.whl` en `Downloads/`, asi que
> en otra maquina la instalacion falla ahi. No se toca porque esa build
> concreta es la que funciona en este equipo.

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
`vigilante_amazonas` completo. Recuperados en el commit de respaldo `6181b67` — hash del historial pre-absorcion, que hoy vive en los bundles de `ELDE_backup_git/`; en el monorepo el archivo entra con la absorcion (`7f2d492`).

---

## H-08 · El reinicio a cero del dashboard no dice CUANTO se va a borrar · `CORREGIDO`

> **Correccion de mi propio hallazgo.** El titulo original decia «no pide
> confirmacion», y era **falso**: el endpoint exige `confirmar=true` y la
> interfaz ya mostraba un dialogo. Lo que faltaba de verdad era el **alcance**:
> el dialogo enumeraba categorias («todas las capturas») sin decir cuantas.
>
> **Corregido el 30-jul-2026** con un endpoint de previsualizacion,
> `GET /dashboard/api/vaciar-detecciones/previo`, que cuenta sin tocar nada. El
> dialogo ahora dice cifras reales — medido contra el servidor en vivo:
> «954 archivos: 408 capturas del servidor, 408 del cliente, 33 rostros,
> 29 identidades del Re-ID, 75 de mapas de calor» — y avisa de que el conteo de
> visitantes vuelve a cero. Si no hay nada que vaciar, ni siquiera pregunta.

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

## H-11 · El `camera_id` es aleatorio: la analitica por camara no se acumula · `CORREGIDO` · **critico**

> **Estado (30-jul-2026, cerrado).** Corregido en los **cuatro** clientes.
>
> Se corrigio primero solo en tienda, aplazando los otros tres hasta que
> `render_box` pasara al nucleo, para no replicar el arreglo cuatro veces. Ese
> traslado se retraso y el aplazamiento se quedo sin motivo, asi que se hizo lo
> que estaba previsto: **la identidad se extrajo al nucleo**
> (`elde_core/ui/identidad_camara.py`) como funciones puras, y los cuatro
> `render_box` delegan en ellas. Es la primera rebanada del despiece de
> `render_box`, y la que llevaba el fallo dentro.
>
> Antes de recablear nada se comprobo la equivalencia contra la version de
> tienda —la que ya tenia 8 pruebas encima— en **680 combinaciones de entrada:
> cero diferencias**. El despiece mueve comportamiento, no lo cambia.
>
> Dos guardias nuevos en `test_device_id.py`: uno falla si algun
> `render_box` vuelve a mandar `component_key` como `camera_id`; el otro, si
> algun cliente deja de delegar en el nucleo y se guarda una copia propia. Es
> como empezo este hallazgo.
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

## H-13 · Claves de Hik-Connect en claro y **ya publicadas en GitHub** · `CERRADO`

> **CERRADO el 31-jul-2026: el usuario ROTO las claves** (App Key/Secret de
> Hik-Connect y la key de Roboflow). Las cadenas expuestas en los historiales
> antiguos quedaron muertas. Con eso, la documentacion que se excluia del
> repositorio vuelve a el (enmascarada), como prometia el `.gitignore`. Lo
> unico del capitulo que sigue vivo es BORRAR los 3 repos antiguos de GitHub
> (decidido: eliminar, no archivar), que requiere la cuenta del usuario.

> **Mitigado el 30-jul-2026: la credencial ya no vive en ningun archivo.**
>
> | Antes | Ahora |
> |---|---|
> | Escrita en los 4 `get_url.py` | Se escribe en el panel de Dispositivos del cliente |
> | En el `.env` de perimetrales | Guardada **cifrada** en el almacen del cliente |
> | Leida del `.env` por `_prefill_hik_env` | Publicada en `os.environ` del **proceso** al conectar |
> | Sin forma de retirarla | **Borrada del entorno al cerrar sesion** |
>
> Vive en el entorno del proceso en marcha, no en disco: al cerrar el cliente
> desaparece con el, asi que no hay nada que se pueda commitear por descuido.
> Modulo nuevo: `elde_core/config/sesion_hik.py`.
>
> **7 pruebas** fijan la propiedad para que no se reintroduzca: cerrar sesion
> no deja rastro en `os.environ`, ningun `.env` puede volver a declararla y
> ningun `get_url.py` puede llevarla escrita.
>
> ### Lo que SIGUE pendiente y solo puedes hacer tu
>
> **Rotar el App Key y el Secret** en el portal de Hik-Connect para empresas.
> Las claves viejas llevan meses en el historial de tres repos de GitHub y
> sacarlas del codigo **no las desexpone**: hay que invalidarlas en el
> proveedor. Lo mismo con la key de Roboflow.
>
> Son credenciales del gateway empresarial —se envian como `appKey`/`secretKey`
> a `isa.hik-connect.com/api/hccgw/platform/v1/token/get`—, **no** de EZVIZ,
> que usa las suyas y no esta comprometida.
>
> **Alcance de la mitigacion:** protege de la fuga por repositorio, que es la
> que ocurrio. NO protege de alguien con acceso a la maquina: el almacen se
> descifra con una clave derivada del propio hardware.

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

## H-12 · Dos carpetas divergentes publican en el mismo repo de GitHub · `CORREGIDO en local`

> **Resuelto en esta maquina el 30-jul-2026**, como efecto de la absorcion del
> HITO 2: ninguno de los 5 proyectos conserva ya un `.git` propio, asi que
> desde aqui es imposible publicar en `view.official.git` ni en
> `Amazonasview.git`. El unico remoto vivo es el monorepo. Los historiales
> antiguos siguen intactos en `ELDE_backup_git/` (4 bundles verificados y los
> `.git` originales).
>
> **Lo que queda y solo puedes hacer tu:** archivar o poner en privado los tres
> repositorios antiguos en GitHub. Otras copias del proyecto en otras maquinas
> si podrian seguir publicando en ellos y pisandose entre si.

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

## H-10 · Peso muerto en la raiz del proyecto · `CERRADO en lo posible`

> **31-jul-2026:** el usuario movio fuera del proyecto la carpeta
> `hik-connect/` completa (SDK + notas) y casi todos los modelos: de 50 GB
> quedan **118 MB** en `modelos NVIDIA/` ("movi los que pude"). Los zips se
> borraron en el HITO 11. Lo que queda es de uso corriente.

- `modelos NVIDIA/`: **50 GB**.
- `hik-connect/`: 3954 archivos de SDK de terceros (~200 MB en `.exe`, `.dll`,
  `.pdb`, PDF), el 97% del arbol de la raiz, sin codigo propio salvo una nota.
- `PerimetralesView_cliente.zip`: 185 MB.

Excluidos del monorepo por `.gitignore`, pero siguen ocupando disco y
enturbiando cualquier busqueda.

---

## H-15 · Los venv de tienda y perimetrales son COPIAS del de managers · `CORREGIDO`

Al mover las carpetas salio a la luz: el `pip.exe` de `tienda_view/venv` y el de
`perimetrales-view/venv` llevaban incrustada esta ruta:

```
C:\Users\Sistema-1\Desktop\ELDE\windows_managers_view\venv\Scripts\python.exe
```

Es decir: **no se crearon con `python -m venv`, se copiaron del de managers.**

**Por que importa.** Un `pip install X` desde el venv de tienda no instalaba en
el venv de tienda: ejecutaba el Python de *managers* y lo instalaba **alli**.
Nadie lo notaba porque los tres venv heredan ademas del user-site global
(H-04), asi que el paquete acababa visible desde los tres de todos modos.

**Como se corrigio.** Los 71 lanzadores `.exe` de los cinco venv se
reapuntaron cada uno a **su propio** `python.exe`. Comprobado: los cinco `pip`
declaran ahora su `site-packages` correcto.

**Nota tecnica.** Un console script de Windows es el launcher de distlib con un
shebang `#!"<ruta>"` y un zip pegado detras. Se verifico empiricamente —copiando
un `pip.exe`, alargando la ruta y ejecutandolo— que cambiar la longitud del
shebang **no** rompe nada: `zipimport` localiza el archivo desde el final por su
registro EOCD. Tambien se descubrio que distlib entrecomilla la ruta **solo si
tiene espacios**, asi que hay que contemplar las dos formas.

**Lo que NO arregla.** Siguen sin aislar (H-04). Estos venv se reapuntaron, no
se recrearon.

---

## H-16 · 15 rutas absolutas a esta maquina en `Hummus.py` y `Misters.py` · `CORREGIDO`

Ademas de las 5 de `app.py` que ya estaban contadas, estos dos procesadores
tenian 15 valores por defecto mas con la ruta completa a este equipo. Y con un
detalle: estaban escritos como `r"C:\Users\Sistema-1\..."` —cadena **cruda**
con las barras **dobladas**—, o sea que la ruta real llevaba separadores
repetidos. Colaba unicamente porque Windows los colapsa.

Los 20 valores estan ahora anclados a `__file__`, no al directorio de trabajo.

**Defecto previo que quedo a la vista.** Tres de esos modelos —`1080.pt`,
`yolo12m.pt`, `yolo11l.pt`— **no existen** en `models/base/`, ni antes ni
ahora: la ruta vieja apuntaba al mismo sitio fisico. Los valores por defecto de
Hummus y Misters llevan tiempo sin resolver; funcionan solo porque la
configuracion los sobrescribe siempre. `yolo26m.pt`, el de `app.py`, si existe.

---

## H-17 · Las pruebas de alias del nucleo pasaban EN VACIO · `CORREGIDO`

`test_los_alias_de_los_clientes_resuelven_al_nucleo` y
`test_el_alias_es_un_redireccion_y_no_una_copia` recorren `CLIENTES_MIGRADOS` y
se saltan el cliente cuyo `src/` no existe. Al mover los clientes a `clients/`
las tres rutas quedaron obsoletas **a la vez**: las dos pruebas seguian en
verde sin comprobar absolutamente nada.

Anadido `test_las_carpetas_de_los_clientes_existen`, que falla en cuanto una
ruta de la lista deja de existir. Es el guardia que faltaba.

---

## H-18 · El commit de seguridad dejo 3 archivos aun versionados · `CORREGIDO`

El commit `2494827` ("saca del repositorio los archivos con claves en claro")
anadio `HIKCONNECT_INTEGRATION.md` al `.gitignore`, pero ignorar **no**
desversiona: tres de las cuatro copias seguian en el indice. Se detecto al
mover las carpetas, porque en la ruta nueva el `.gitignore` si aplicaba y git
las dejaba fuera.

Las cuatro estan ahora fuera del indice y **siguen en disco**. Revisadas antes:
no contienen valores de clave, solo nombres de dispositivo. Vuelven al
repositorio cuando las claves esten rotadas (H-13).

---

## H-19 · El pipeline `Hummus` no puede arrancar: su modelo no existe · `ABIERTO`

Salio al ejercitar los 8 pipelines en el HITO 8. Al conectar un cliente en modo
`Hummus`, el servidor corta la conexion:

```
Error critico: No se encontro el modelo principal.
Rutas probadas: server\models\base\1080.pt
Fallback ausente: server\models\base\1080.pt
```

La configuracion declara `Hummus -> models/base/1080.pt` y **ese archivo no
esta en el disco**. Ni ahora ni antes de mover el servidor: la ruta absoluta
vieja apuntaba al mismo sitio fisico (ver H-16).

Lo delata el propio arranque, en una linea que se pierde entre el resto:

```
Modelo no encontrado en disco: ...\models\base\1080.pt
1 modelo(s) no encontrado(s) en disco. El servidor iniciara pero fallara al
intentar usarlos.
```

**`Misters` si funciona** aunque su valor por defecto tambien apunte a
`1080.pt`: no esta en `model_paths`, asi que cae en el `model_path` general
(`yolo26m.pt`, que si existe). Es la confirmacion de lo que H-16 suponia — los
valores por defecto de Hummus/Misters llevan tiempo sin resolver y solo
funcionan cuando la configuracion los tapa.

**Decision del usuario (31-jul-2026):** el modelo esta AUN SIN ENTRENAR, y
cuando exista **se llamara exactamente `1080.pt`**. Por tanto la configuracion
actual es correcta y NO se toca: el dia que el archivo aparezca en
`models/base/`, el modo Hummus arranca solo. Estado: esperando el modelo.

---

## H-20 · `PerimetralesBoTSORT` falla en CADA frame · `CORREGIDO` · **critico**

> **Corregido el 31-jul-2026 con aprobacion explicita** (panel del HITO 11).
> `BoTSORTWrapper` tiene ya su rama propia en el despacho, llamado con la
> firma que acepta `(frame, camera_id)`; sus pistas se dibujan como las de
> MultiCam, tolerando `conf=None` (que la etiqueta de MultiCam habria roto
> con su `:.2f`). Verificado con trafico sintetico: 0 errores de firma.


```
Error en worker: BoTSORTWrapper.process_frame() takes 3 positional arguments
but 8 were given
```

`BoTSORTWrapper` se construye en `app.py:267`, pero **no tiene rama propia** en
el despacho de frames: no hay ningun `isinstance(processor, BoTSORTWrapper)`.
Cae en la rama generica, que llama

```python
processor.process_frame(img, roi, roi_activate, camera_id,
                        heatmap_activate=heatmap_activate)
```

mientras su firma es

```python
def process_frame(self, frame: np.ndarray, camera_id: int)
```

Cinco argumentos contra dos. **No es un caso raro: es todos los frames.** Este
modo no ha funcionado nunca desde que la firma diverge.

El contrato **si** valida sus mensajes (aparece en `pipelines_observados` con
0% de problemas): el fallo esta despues, en el procesamiento. Sirve para
distinguir las dos capas — que el contrato acepte un mensaje no dice nada de si
el dominio sabe atenderlo.

**Por que no lo arreglo aqui:** la regla 3 del refactor manda anotar los fallos
y corregirlos solo con aprobacion. Ademas hay que decidir que se le pasa: o se
le da una rama propia con `(img, camera_id)`, o se amplia su firma para que
acepte lo mismo que los demas. Lo segundo es lo coherente con el resto, pero
cambia el comportamiento de un procesador que no puedo probar sin camaras
reales.


---

## H-21 · `Autolavado` fallaba en CADA frame, igual que H-20 · `CORREGIDO`

Salio al VERIFICAR el arreglo de H-20: en el mismo log estaba su gemelo.

```
Error en worker: VehicleProcessor.process_frame() takes from 2 to 4
positional arguments but 8 were given
```

Mismo defecto exacto: `VehicleProcessor` (modo `Autolavado`) tampoco tenia
rama propia en el despacho y caia en la generica. Su firma es
`(image, roi=None, send_to_server=True)` y recibia 8 argumentos.

**Por que se corrigio sin panel nuevo:** la aprobacion del HITO 11 fue para
exactamente esta clase de fallo en exactamente esta funcion; se extendio al
gemelo y quedo dicho en el informe. Rama propia que le pasa
`(img, roi si roi_activate)` y devuelve su `(frame, metadata)` tal cual.
Verificado junto a H-20: 0 errores de firma con trafico en ambos modos.

**La leccion que dejan H-20 y H-21 juntos:** el despacho por `isinstance`
con una rama generica que asume una firma es fragil — cada procesador nuevo
que no la comparta falla en silencio hacia el log. Si aparece un tercer
caso, la correccion de fondo es una interfaz comun de procesador, no otra
rama.


---

## H-22 · La reconciliacion del HITO 4 enterro el `Jarvis_api` de perimetrales · `CORREGIDO`

El primer arranque REAL tras los venv aislados lo destapo:

```
TypeError: Jarvis_api.__init__() got an unexpected keyword argument
'establecimiento'
```

**Causa.** El HITO 4 extrajo al nucleo la version identica en
tienda/managers/amazonas. La de perimetrales habia DIVERGIDO por razones
reales —`establecimiento=` preferido del `.env`, la señal
`establishments_loaded`, el envio asincrono (`enviar_novedad_async` /
`subir_imagen_async`) y el arreglo de la carrera del login— y su alias la
enterro bajo la version comun. Es exactamente el riesgo que el paso 8 del
HITO 2 advertia ("los archivos que divergieron lo hicieron por algo"), y esta
vez se materializo.

Habia una SEGUNDA rotura latente peor: `jarvis_alert_forwarder` llama a
`enviar_novedad_async`, que la version del nucleo no tenia — habria fallado
con AttributeError en la PRIMERA alerta real.

**Correccion.** El nucleo adopta la version de perimetrales, que el analisis
de metodos confirmo como superconjunto ESTRICTO (la comun no tenia nada
propio). Para los otros 3 clientes nada cambia: `establecimiento` es opcional
y ninguno llama a los metodos de envio (verificado: un solo llamante en todo
el ecosistema).

**Verificado construyendo**, no importando: `Jarvis_api` se instancio con los
kwargs exactos de cada `main.py` en los 4 venv. Y la leccion queda en la
prueba nueva de `test_nucleo.py`: **importar no detecta una firma perdida;
hay que construir**. Es la tercera vez que "compila/importa" no basto
(plantillas de `ajustes.py`, y ahora esto).


---

## H-23 · El boton Play no capturaba: el alias del worker se EJECUTA, no se importa · `CORREGIDO` · **critico**

**Sintoma.** Se elige una ventana, se pulsa Play y **no pasa nada**: ni error,
ni frames, ni traza. En el servidor solo aparece "Cliente conectado" y su
desconexion — el cliente conecta bien, pero no envia un solo frame.

**Causa.** `render_box.init_loop` no importa el worker: lo lanza como script
en un subproceso.

```python
QProcess.start(sys.executable, ["src/workers/capture_woker.py", str(hwnd)])
```

El HITO 4 convirtio ese archivo en un alias de modulo como todos los demas:

```python
_sys.modules[__name__] = _modulo
```

Al ejecutarse como script, `__name__` vale `"__main__"`, pero el modulo del
nucleo se importa con su nombre real (`elde_core.capture.capture_worker`), asi
que **su guarda `if __name__ == "__main__"` nunca corria**. El subproceso
arrancaba, reasignaba `sys.modules` y terminaba con exit 0 sin capturar nada.

Comprobado midiendo: el alias salia al instante; el modulo del nucleo lanzado
directamente se quedaba capturando.

**Afectaba a los CUATRO clientes**, y no lo detecto nada: los imports pasaban
(el alias es Python valido), las pruebas pasaban y el cliente arrancaba. Solo
se ve usandolo.

**Correccion.** El alias replica la guarda de script y llama a
`ejecutar_worker()`, que el nucleo ya exponia; en modo import sigue siendo un
alias transparente. Verificado en los 4 venv: el subproceso ahora se queda
vivo capturando. Prueba nueva en `test_nucleo.py`.

**La leccion, que es la misma de H-22 subida de nivel:** un alias de modulo
sirve para lo que se IMPORTA. Si un archivo tambien se ejecuta —worker,
script, punto de entrada— el alias hay que escribirlo de otra forma. Al
migrar conviene preguntarse *como se usa este archivo*, no solo *quien lo
importa*.


---

## H-24 · Sin `supervision`, el overlay se apaga EN SILENCIO · `CORREGIDO`

**Sintoma.** "El cliente no esta activando el ROI y tampoco esta
identificando". Ni error, ni traza: el video se ve, los frames llegan al
servidor (1.763 validos, 0 rechazos) y el procesador corre sin fallos — pero
no aparecen las cajas de deteccion ni las zonas del ROI.

**Causa.** `elde_core.ui.sv_overlay` importa `supervision` bajo
`try/except`, y `render_box` hace lo mismo con el propio overlay:

```python
try:
    import supervision as sv
except Exception:
    sv = None
```

En **modo directo** (`_direct_mode = True` en perimetrales) el servidor NO
dibuja (`draw_server=False`): manda las detecciones y **dibuja el cliente**,
con Supervision. Sin la libreria, ese camino se desactiva sin decir nada.

Lo introdujo la correccion de H-04: al recrear los venv aislados,
`supervision` dejo de llegar del user-site global y **no estaba declarado en
ningun requirements** — el freeze del que se instalaron no lo incluia.

**Correccion.** Instalado `supervision==0.28.0` (la misma version que el
servidor) en los 4 venv y **declarado en los 4 `requirements.txt`** con el
motivo escrito al lado. Prueba nueva que falla si el overlay vuelve a quedar
apagado.

**Barrido de la clase entera.** Tras corregirlo se buscaron TODAS las
dependencias bajo `try/except import` del nucleo y los 4 clientes: son **6**
(`PySide6`, `cv2`, `numpy`, `psutil`, `requests`, `supervision`). Comprobadas
una a una en los 5 entornos: **ninguna mas apagada**. Y la comprobacion quedo
como prueba permanente, asi que una dependencia opcional nueva se vigila sola.

**La leccion.** Un `try/except ImportError` que desactiva una funcion es cómodo
para arrancar, pero convierte una dependencia ausente en un fallo mudo. Los
tres hallazgos del rodaje real (H-22, H-23, H-24) comparten raiz: **el codigo
seguia importando bien; lo que fallaba era el USO**. Ninguna prueba de imports
los habria visto.


---

## H-25 · El reenvio a WhatsApp era una caja negra · `CORREGIDO` (observabilidad)

**Sintoma reportado.** "El envio a WhatsApp no esta funcionando".

**Lo que el diagnostico SI pudo confirmar** (todo verificado, no supuesto):

| Eslabon | Estado |
|---|---|
| El cliente manda `enviar_whatsapp` | **SI** — capturado de una sesion real: `True` |
| El interruptor esta guardado activo | **SI** — `whatsapp_envio_activo: True` |
| Se generan alertas del tipo correcto | **SI** — 90 de `llegada` solo hoy |
| Las claves de la tarjeta coinciden | **SI** — `event_type`, `clase_gruesa`, `image_base64` |
| Los filtros dejan pasar una alerta REAL | **SI** — reproducido con una tarjeta de hoy, interceptando el envio |
| El bot recibio algun intento | **NO** — cero lineas en el log |

**El problema de fondo.** Con todos los eslabones correctos sobre el papel y
cero intentos reales, el diagnostico se quedo sin siguiente paso: el reenvio
**no dejaba rastro de nada**. Ni de que el interruptor llegara, ni de cuantos
mensajes salieron, ni de si el bot rechazo alguno. Tres causas muy distintas
—interruptor apagado, sin alertas, bot caido— eran indistinguibles desde
fuera.

**Correccion.** El camino se hace observable:

1. El servidor **registra cada CAMBIO del interruptor** por cliente (una linea,
   no por frame): `WhatsApp: interruptor ACTIVADO para client=... `.
2. `/api/v1/estado` publica los contadores del emisor: `enviados`,
   `descartados_antiflood`, `fallidos`.

Con eso, la proxima vez la respuesta se lee de un vistazo: si el interruptor no
aparece en el log, no esta llegando; si `enviados` sube y no ves el mensaje, el
problema esta en el bot; si `fallidos` sube, el bot responde mal; si todo esta
a cero con alertas, el flag no llego al procesador.

**Nota.** No se envio ningun mensaje de prueba a WhatsApp a proposito: seria
una accion hacia fuera, a un grupo real, y esa decision es del usuario.


---

## H-26 · Uvicorn mataba conexiones VIVAS cada 40-100 s (keepalive 1011) · `CORREGIDO`

**Sintoma.** "El servidor de vez en cuando se desconecta y despues vuelve a
conectar": el websocket moria cada 43-100 s y el cliente reconectaba a los
5 s (su `reconnect_timer`). En el log del servidor solo se veia la secuela
("Error enviando respuesta" vacio + "Error deserializando... char 0").

**Diagnostico en tres capas, cada una con su evidencia:**

1. El transporte del cliente no registraba el MOTIVO del cierre (el
   `closeCode` solo salia por print, y `errorOccurred` ni estaba conectada).
   Se hizo observable primero.
2. Reproduccion sintetica (cliente Qt real, frame real de 152 KB a 12 fps):

   ```
   [80.5s] ERROR: 'Unable to write'
   [81.9s] DESCONECTADO tras 51.4s | closeCode=1011
           reason='keepalive ping timeout' | enviados=427 recibidos=3
   ```

3. **Causa raiz:** uvicorn manda un PING de protocolo cada 20 s y cierra con
   1011 si el PONG no vuelve en 20 s. El pong se retrasa por dos vias reales:
   **contrapresion** (el cliente escribe mas rapido de lo que el servidor
   drena — 427 enviados vs 3 respuestas — y el pong queda en cola tras los
   frames) y **congelones de la GUI del cliente** (AppHangB1 de python.exe a
   las 15:05:58 en el Visor de eventos: el hilo de interfaz dejo de responder).

**Correccion.** `ws_ping_interval=20` / `ws_ping_timeout=60` en uvicorn
(`ELDE_WS_PING_INTERVALO_SEG` / `ELDE_WS_PING_TIMEOUT_SEG`): tolera baches de
hasta un minuto y sigue detectando peers muertos.

**Lo que NO cura:** los congelones de la GUI (capa 3b) siguen ahi — ahora
cada cierre queda explicado en `clients/<x>/logs/<x>.log` con su closeCode,
asi que si reaparece 1011 con el timeout de 60 s, el problema es un cuelgue
de mas de un minuto del cliente y se ataca alli.

**Hallazgo operativo de la misma sesion:** el servidor llevaba el dia
corriendo como proceso HIJO de la sesion de asistencia; cada reinicio del
anfitrion lo mataba ("se apaga"). Relanzado desligado y regla anotada: el
servidor se arranca con INICIAR_*.bat o el SELECTOR.

## H-27 · `extract_terms` del router multimodal no entiende «búscame» ni traduce · `ABIERTO (esquivado)`

**Síntoma.** El extractor de términos de
`server/src/analityc/core/multimodal_router.py` busca la palabra clave por
substring SIN normalizar acentos: «búscame el carro rojo» no matchea `busca`
(la ú lo impide) y caería a VQA; y si matchea («buscame»), el término
resultante viaja EN ESPAÑOL a YOLO-World, cuyo encoder CLIP entiende mucho
mejor inglés («carro rojo» rinde peor que «red car»).

**Impacto real.** Ninguno hoy: la búsqueda de los dashboards (FASE 6,
`src/app/busqueda_vlm.py`) no usa ese extractor — normaliza sin acentos,
detecta la intención y traduce el vocabulario del dominio (carro→car,
rojo→red, colores delante). Verificado en vivo: «búscame el carro rojo» →
término `red car`; control positivo «busca los carros» → 2/12 coincidencias,
exactamente las dos fotos CARRO.

**Si se corrige el router,** conviene mover allí el extractor de
`busqueda_vlm.py` (una sola fuente de verdad) y que `route()` lo use; los
usuarios actuales de `route()` saldrían ganando gratis.

## H-28 · El puerto 9000 lo puede ocupar OTRO proyecto de la maquina · `ABIERTO (operativo)`

**Sintoma.** El servidor de ELDE no arranca: `[Errno 10048] error while
attempting to bind on address ('0.0.0.0', 9000)`, y `/api/v1/estado`
responde **404** en vez de rechazar la conexion — o sea, "hay servidor,
pero no es el nuestro".

**Causa.** El proyecto `Desktop\amazonas\Amazonas-IA` levanta SU propio
servidor en el mismo puerto 9000 (`Amazonas-IA\venv\...\python main.py`,
que a su vez lanza un hijo con el Python global). Mientras corre, ELDE no
puede escuchar y sus clientes ven "servidor caido".

**Como distinguirlo de un servidor de ELDE sano** (comprobado el
4-ago-2026): el dueno del puerto responde 404 en `/api/v1/estado` y su
linea de comandos NO es `iniciar_servidor_headless.py`:

    Get-NetTCPConnection -LocalPort 9000 -State Listen
    Get-CimInstance Win32_Process -Filter "ProcessId = <pid>"

**Que NO se hizo:** matar ese proceso — es de otro proyecto del operador y
la decision es suya. `INICIAR_SERVIDOR.bat` ya avisa si el 9000 esta
ocupado (58d3122c), pero no puede saber DE QUIEN es.

**Opciones cuando estorbe:** (a) cerrar Amazonas-IA, (b) mover uno de los
dos de puerto — ELDE admite `iniciar_servidor_headless.py <puerto>` y los
clientes lo toman de `server_ws_url`.

**Agravante medido el mismo dia:** la maquina tiene 15,7 GB y llego a 0,5 GB
libres (3 ventanas de VS Code ~3,3 GB + Brave 1,2 GB + los dos proyectos).
Con esa presion el servidor muere al cargar modelos, sin traza ni OOM en el
log — solo deja de escribir. Si "se cayo solo", mirar la RAM antes que el
codigo.
