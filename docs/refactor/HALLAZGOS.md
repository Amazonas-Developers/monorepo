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

## H-06 · `requirements.txt` del servidor es un `pip freeze` · `ABIERTO`

1796 lineas, frente a las 104 de cada cliente. Es un volcado del entorno
completo, no una lista curada: no hay forma de saber que necesita realmente el
servidor, ni de reinstalarlo en otra maquina sin arrastrar todo.

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

## H-10 · Peso muerto en la raiz del proyecto · `ABIERTO`

- `modelos NVIDIA/`: **50 GB**.
- `hik-connect/`: 3954 archivos de SDK de terceros (~200 MB en `.exe`, `.dll`,
  `.pdb`, PDF), el 97% del arbol de la raiz, sin codigo propio salvo una nota.
- `PerimetralesView_cliente.zip`: 185 MB.

Excluidos del monorepo por `.gitignore`, pero siguen ocupando disco y
enturbiando cualquier busqueda.
