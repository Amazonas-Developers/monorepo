# HITO 4 — Núcleo compartido

> Dos pasadas: primero lo **identico** entre clientes (paso 7, seccion 1) y
> despues la **reconciliacion** del paquete `dvr/`, que exigia leer diffs
> (paso 8, seccion 9). Lo que queda fuera esta en la seccion 5.
> Generado el 2026-07-30.
>
> **Total extraido: 46 modulos, ~6.680 LOC** que estaban repetidos en 3 o 4
> clientes.

---

## 1. Que se movio

10 modulos, ~970 LOC que estaban repetidos en 3 o 4 clientes:

| Nucleo | Venia de | LOC | Copias eliminadas |
|---|---|---:|---:|
| `capture/window_grab.py` | `core/capture_exaple.py` | 107 | 4 |
| `capture/window_controller.py` | `core/window_controller.py` | 162 | 4 |
| `capture/windows_detector.py` | `core/windows_detector.py` | 57 | 3 |
| `capture/window_monitor.py` | `core/window_global.py` | 130 | 3 |
| `capture/hwnd_state.py` | `core/state_global/hwnd.py` | 13 | 4 |
| `capture/list_windows.py` | `model/windows/list_windows.py` | 17 | 4 |
| `transport/socket_client.py` | `core/network/socket_client.py` | 135 | 4 |
| `transport/jarvis_api.py` | `core/network/jarvis_api.py` | 167 | 3 |
| `config/app_singleton.py` | `core/app_singleton.py` | 66 | 4 |
| `config/settings_model.py` | `model/settings_model.py` | 116 | 3 |

## 2. Como se migro, y por que asi

En cada cliente, la copia del archivo se sustituye por un **alias de modulo**:

```python
import sys as _sys
from elde_core.capture import window_monitor as _modulo
_sys.modules[__name__] = _modulo
```

Con eso, los ~60 archivos de cada cliente siguen haciendo
`from core.window_global import windows_monitor` **sin cambiar una linea**, y
reciben el modulo del nucleo.

**Por que un alias y no reescribir los imports:** reescribirlos serian cientos
de ediciones en cuatro clientes sin tests, todas a la vez. El alias hace la
migracion reversible archivo por archivo y permite verificar cada paso. Los
alias se borran cuando cada cliente se refactorice de verdad (HITOS 5-7).

### 2.1 De que cliente se tomo cada version

De **`tienda_view`**, a proposito: es el unico que lleva los arreglos de este
refactor (H-01 crash al cerrar, H-02 boton del dashboard, H-11 `device_id`
estable). Al mover al nucleo, esas correcciones **se propagan a los otros
clientes**, que las tenian pendientes.

Es un cambio de comportamiento deliberado y beneficioso, pero conviene saberlo:
`perimetrales-view` y `windows_managers_view` ya no cierran con el
`0xc0000409` de H-01.

## 3. Un fallo que salio al migrar

Al importar el nucleo desde los clientes aparecio un `TypeError`:

```
PySide6.QtCore.QThread.wait(QObject)
```

**Lo introduje yo en H-01.** Al cambiar la firma a `stop(self, msec=2000)` no
tuve en cuenta que ese metodo esta conectado a `QObject.destroyed`, senal que
**entrega el objeto destruido como argumento**. Ese QObject aterrizaba en
`msec` y `self.wait(QObject)` lanzaba TypeError al cerrar.

Corregido con `stop(self, *_ignorado, msec: int = 2000)`: absorbe lo que mande
la senal y `msec` pasa a ser de solo palabra clave para que no vuelva a
ocurrir. **Hay una prueba que fija la firma** (`test_nucleo.py`), porque es un
error facil de reintroducir.

## 4. Amazonas View queda fuera de esta pasada

Se migro y se **revirtio**: no tiene venv propio (arranca con el Python global,
ver `INICIAR_AMAZONAS.bat`), y ahi `elde_core` no esta instalado. Con los alias
puestos, el cliente no arrancaba.

Esta restaurado a su estado original y verificado: sus modulos importan. Sigue
con los bugs H-01 y H-11 propios, que recibira al migrarse en el HITO 7, cuando
tenga un entorno donde instalar el nucleo.

**Leccion:** el nucleo solo se puede consumir donde se pueda instalar. Antes de
migrar un cliente hay que comprobarlo, no darlo por hecho.

## 5. Lo que NO se movio, y por que

### 5.1 Los widgets de interfaz (~1.800 LOC)

`device_panel.py` (650 LOC), `interactive_imageLabel.py` (250), `dvr_tree.py`,
`window_bar.py`, `modal_msm.py` y compania. Varios estan en el mismo caso que
`hikconnect`: casi identicos, no identicos. Y `window_bar.py` diverge
precisamente por mi arreglo de H-02.

### 5.2 `render_box.py`

Mezcla chrome comun con logica de dominio (zonas de tienda, VLM, planograma).
El HITO 2 ya decidio que hay que **partirlo**, no moverlo entero. Es trabajo de
los HITOS 5-7.

## 6. Estado de los criterios de aceptacion

- [x] **Cero dependencias del nucleo hacia codigo de cliente.** Verificado con
      AST sobre todos los modulos del nucleo (`test_nucleo.py`): ningun import
      de `core.*`, `gui.*`, `model.*` ni `workers.*`.
- [x] **Tests minimos.** 35 pruebas en total: 13 de contrato, 5 de payloads
      reales, 8 de `device_id`, 9 del nucleo (4 de ellas fijan los arreglos
      de Hik-Connect para que una reconciliacion futura no los pierda).
- [~] **Type hints completos y docstrings en espanol.** Los modulos movidos
      conservan el estilo del original: unos tienen anotaciones y otros no. No
      se reescribieron a proposito — el HITO 4 mueve codigo, y reescribirlo a la
      vez haria imposible distinguir un fallo de migracion de un fallo nuevo.
      Queda como deuda anotada para los HITOS 5-7, donde cada cliente se toca
      de verdad.

## 7. Verificacion

| Comprobacion | Resultado |
|---|---|
| Modulos del nucleo que importan solos | 10/10 + 10/10 del paquete `dvr` |
| Alias que resuelven al nucleo | 10/10 y 9/9 (`dvr`) en los 3 clientes |
| Imports de `main.py` por cliente | tienda 11/11 · perimetrales 13/13 · managers 11/11 |
| `stop_scanner()` y destruccion sin TypeError | los 3 clientes |
| Amazonas View tras revertir | importa correctamente |

## 8. Deuda que sale de este hito

1. **Instalar `elde_core` en Amazonas View** (necesita venv) — HITO 7.
2. **Paso 8 de los widgets** (~1.800 LOC): `device_panel.py`,
   `interactive_imageLabel.py`, `dvr_tree.py`, `window_bar.py`... El de `dvr/`
   ya esta hecho (seccion 9); estos siguen pendientes y exigen la misma
   revision de diffs.
3. **Type hints y docstrings** de los modulos movidos — HITOS 5-7.
4. Los alias son temporales: desaparecen al refactorizar cada cliente.

---

## 9. Reconciliacion del paquete `dvr/` (paso 8)

Es el paso que el HITO 2 marco de **riesgo alto**. No se hizo de forma
mecanica: se leyo el diff de cada archivo y se decidio con evidencia.

### 9.1 Cuatro "divergencias" que no lo eran

`dahua_http.py`, `dahua_sdk.py`, `hikvision_http.py` y `hikvision_sdk.py`
(579 LOC) aparecian con hash distinto en `windows_managers_view`, pero **mismo
numero de lineas**. Tras normalizar fines de linea y espacios finales resultaron
**identicos**: la diferencia era ruido de CRLF. Riesgo real: cero.

Leccion util para el resto del refactor: comparar por hash crudo sobreestima la
divergencia.

### 9.2 Gana `perimetrales-view`, y por que

| Archivo | Divergencia | Decision |
|---|---|---|
| `base.py` | tienda ≡ managers; perimetrales anade `verification_code` (defecto `""`) | perimetrales: aditivo y compatible |
| `context.py` | 3 iguales; perimetrales anade estrategia EZVIZ + `verification_code` | perimetrales: aditivo |
| `hikconnect.py` | tienda ≡ managers; perimetrales, 69 lineas en 7 bloques, con **cambios** | perimetrales: contiene 3 arreglos |
| `discovery.py`, `ezviz.py` | solo en perimetrales | se incorporan (funcionalidad nueva) |

Los tres arreglos de `hikconnect.py`, cada uno documentado con medidas reales en
los propios comentarios del codigo:

1. **`url.endswith(".m3u8")` no acertaba nunca.** La URL trae query
   (`...m3u8?expire=...&id=...`), asi que la verificacion del contenido del
   m3u8 **no se ejecutaba** y se devolvian URLs con `ErrCode`.
2. **El campo `online` de la nube es poco fiable.** Medido contra la cuenta
   real: un DVR reporta `online="0"` en todos sus canales y transmite a
   1280x720, mientras otro reporta `online="1"` y devuelve `ErrCode`. Se dejo
   de filtrar por el y el `status` pasa a reflejar si hay stream de verdad.
3. **Rendimiento.** Pedir main+sub en 20 canales tardaba minutos, porque cada
   peticion prueba 3 protocolos y descarga el m3u8. Ahora el sub reutiliza el
   main.

**Cambio de comportamiento asumido:** `rtsp_sub` ya no es un stream de menor
calidad, es el mismo que `rtsp_main`. Se comprobo que solo se almacena y se
propaga en `device_panel.py`, siempre con `.get("rtsp_sub", "")`, y que
`perimetrales-view` ya corre asi en produccion.

### 9.3 Un bug que salio al reconciliar

La prueba que fija el arreglo del m3u8 fallo: `hikconnect.py` tenia **una
segunda aparicion del mismo fallo** en la linea 592, en otro camino de codigo,
que el arreglo original no habia tocado. Corregida.

Es el argumento a favor de fijar los arreglos con pruebas en vez de confiar en
que la version elegida esta completa.

### 9.4 Que gana cada cliente

`tienda_view` y `windows_managers_view` reciben los 3 arreglos de Hik-Connect,
el soporte de codigo de verificacion para streams cifrados, la estrategia EZVIZ
y el descubrimiento de equipos en red. Antes solo los tenia perimetrales.

---

## 10. Widgets y almacen DVR (segunda pasada del paso 8)

### 10.1 La normalizacion vuelve a pagar

De los 20 archivos de `gui/` comparados entre los 3 clientes migrados:

| | Archivos | LOC |
|---|---:|---:|
| Identicos tras normalizar CRLF | 13 | 984 |
| Divergentes de verdad | 7 | 4.367 |

Se movieron 10 widgets (los 3 restantes eran `__init__.py` vacios) mas
`models/dvr_storage.py`, que si divergia pero de forma **aditiva**
(`verification_code`), coherente con la reconciliacion del paquete `dvr/`: gana
otra vez perimetrales-view. Va a `elde_core/dvr/storage.py` porque es el almacen
cifrado de equipos, no un widget.

Cuatro widgets importaban modulos del cliente; esos imports se reescribieron a
sus equivalentes del nucleo (todos ya migrados) o a imports relativos:
`box_image`, `device_list`, `dvr_tree` y `sidebar_dock`.

`perimetrales-view` no tiene `modal_msm`, `add_device_dialog` ni `device_list`:
resuelve 8/8 de los que si tiene.

### 10.2 El arreglo de H-01 no llegaba a todos los clientes

Al verificar el cierre salio que `perimetrales-view` y `windows_managers_view`
**seguian abortando con 0xc0000409**, pese a tener ya el `stop()` correcto del
nucleo. El motivo: el nucleo puede tener el arreglo y aun asi abortar si nadie
lo invoca, y solo el `main.py` de tienda conectaba `stop_scanner` a
`aboutToQuit`.

Se resolvio **en el nucleo**, registrando `stop_scanner` en `atexit` desde el
propio `Windows_monitor`. `atexit` corre antes de que el interprete desmonte
nada, asi que el hilo se para a tiempo sin depender de que cada `main.py` se
acuerde.

Resultado medido, los 3 clientes:

| Cliente | Antes | Ahora |
|---|---|---|
| tienda_view | limpio | limpio |
| perimetrales-view | **0xC0000409** | **codigo 0** |
| windows_managers_view | **0xC0000409** | **codigo 0** |

Es el mejor argumento a favor del nucleo compartido: un arreglo, tres clientes.

### 10.3 Lo que sigue divergiendo (4.367 LOC)

`render_box.py` (1.914), `device_panel.py` (920), `alerts_sidebar.py` (481),
`custom_status_bar.py` (329), `windows_main.py` (325),
`interactive_imageLabel.py` (272) y `window_bar.py` (126).

Cuatro de ellos divergen **en parte por los arreglos de este refactor** (H-02,
H-11, H-14 y el interruptor de WhatsApp), y `custom_status_bar.py` diverge
legitimamente: cada cliente ofrece modos de inferencia distintos.

`render_box.py` no se movera entero nunca: el HITO 2 decidio **partirlo**.
`device_panel.py`, `alerts_sidebar.py` e `interactive_imageLabel.py` si son
candidatos, y necesitan el mismo trabajo de diff que se hizo con `hikconnect`.

---

## 11. Cierre del paso 8: widgets grandes y componentes nuevos

### 11.1 `device_panel.py` (920 LOC)

Mismo patron que el DVR: **tienda ≡ managers** (0 diferencias) y perimetrales
270 lineas por delante. Gana perimetrales, coherente con la reconciliacion
anterior, porque lo que anade es la modernizacion que ya vive en el nucleo:
descubrimiento en red, EZVIZ como tercera via, regiones de Hik-Connect y el
dialogo del codigo de verificacion. Trae ademas un `closeEvent`, justo la
higiene de H-01 que a los otros les faltaba.

Arrastro dos piezas mas al nucleo: `workers/dvr_connect_worker.py` y
`gui/components/discovery_dialog.py`, este ultimo exclusivo de perimetrales.
Los tres clientes que no lo tenian reciben su alias.

### 11.2 `interactive_imageLabel.py` (272 LOC)

Aditivo: perimetrales anade `hay_punto_en` y `arrastrando_punto`.

**Amazonas View queda fuera a proposito.** Su version es mas antigua, con otra
firma de `__init__` y sin las zonas de pedido/entrega; adoptar el superconjunto
le cambiaria la interfaz que su `render_box` ya invoca. Es el criterio de todo
el paso 8: se reconcilia cuando el superconjunto es compatible, no siempre.

### 11.3 El panel de alertas: primero ampliar, luego migrar

`perimetrales-view` tenia 481 lineas propias porque mostraba **hora de
llegada, hora de salida y permanencia**, que el armazon compartido no cubria.
Migrarlo tal cual habria perdido el dato mas util de la vigilancia.

El orden correcto fue el inverso: **primero** se anadio ese soporte al nucleo
(`_tiempos`, `global_id` en el titular) y **despues** se migro. Resultado:

| Cliente | Antes | Ahora |
|---|---:|---:|
| tienda_view | 449 | 59 |
| perimetrales-view | 481 | 64 |
| windows_managers_view | 394 | 48 |
| **compartido** | — | **333** |

Cada uno conserva sus columnas de dominio, que es lo que NO debe compartirse.

### 11.4 Componentes nuevos del nucleo

Dos que no existian en ningun cliente:

- **`config/sesion_hik.py`** — la App Key deja de vivir en archivos. Se escribe
  en el cliente, se guarda cifrada, se publica en el entorno del proceso al
  conectar y **se borra al cerrar sesion** (H-13).
- **`ui/panel_capturas.py`** — pide las capturas al servidor por HTTP en vez de
  leer una carpeta local. Elimina el hardcode de `CAPTURE_CLIENT_DIR`, que
  apuntaba a Amazonas View y dejaba al resto de clientes sin nada que mostrar,
  y funciona con el servidor en otra maquina.

### 11.5 Balance

**46 modulos, ~6.680 LOC** en el nucleo. **44 pruebas** en verde. Los 4
clientes importan su `main.py` completo y cierran con codigo 0.

---

## 10. `logging/` — el subpaquete que faltaba desde el HITO 2 (30-jul-2026)

El HITO 2 propuso `logging/` y `geometry/` en el nucleo. Ninguno se creo. El
primero ya esta; el segundo sigue abierto porque casi toda esa geometria vive
dentro de `render_box.py`, y encaja partirlo en el mismo trabajo.

### Lo que se encontro al ir a hacerlo

| Proyecto | `basicConfig` | `getLogger` | `print()` |
|---|---:|---:|---:|
| clients/tienda | 0 | **0** | 27 |
| clients/perimetrales | 0 | **0** | 31 |
| clients/managers | 0 | **0** | 23 |
| clients/amazonas | 0 | **0** | 33 |
| server | 1 | 24 | 112 |

Los **cuatro clientes no registraban absolutamente nada**. Cuando uno fallaba no
quedaba rastro; y comparar que hicieron dos clientes ante el mismo evento era
imposible, porque no habia nada que comparar. Eso es exactamente lo que el
HITO 8 necesita.

### Que hace `elde_core.logging`

- **La identidad va en cada linea:** `client_type/site_id`, los mismos del
  contrato del HITO 3. Sin eso, juntar cuatro logs da un amasijo.
- **Archivo rotatorio UTF-8 + consola tolerante.** Los clientes imprimen
  `📄 ✅ 🔥` y eso reventaba el arranque en consolas cp1252; el logger no podia
  repetir el error.
- **Las excepciones no capturadas se registran** antes de que muera el proceso.
  Es la clase de fallo que dejo a tienda cerrandose sin dejar rastro (H-01). El
  hook se **encadena** al anterior: registra, no cambia lo que pasa despues.
- **Idempotente.** Llamarlo dos veces no duplica cada mensaje.

Regla 6: nivel, carpeta, tamaño y numero de copias salen de `ELDE_LOG_NIVEL`,
`ELDE_LOG_DIR`, `ELDE_LOG_MB` y `ELDE_LOG_COPIAS`.

### El servidor, sin tocar sus 24 modulos

Sus modulos hacen `logging.getLogger(__name__)`, que no cuelga de `elde`.
Reescribir 24 archivos seria redisenar, no refactorizar. En su lugar,
`configurar(..., tambien_raiz=True)` engancha **los mismos** manejadores al
logger raiz. Efecto colateral util: deja la raiz con manejadores, asi que el
`logging.basicConfig()` que `app.py` ya hacia queda en nada por si solo, sin
tener que quitarlo.

Verificado con el servidor real: `src.app.app` escribe ahora en
`server/logs/server.log` con `server/sitio-unico` delante.

### Pruebas

7 pruebas en `tests/test_registro.py`, incluida la que fija que
`tambien_raiz` captura un logger que **no** cuelga de `elde` — que es lo unico
que hace util la opcion. Total del nucleo: **52**.
