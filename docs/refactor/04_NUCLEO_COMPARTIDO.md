# HITO 4 — Núcleo compartido

> Primera pasada: se extrae lo **identico** entre clientes (paso 7 del plan).
> Lo *casi* identico (paso 8, riesgo alto) queda pendiente y explicado en la
> seccion 5. Generado el 2026-07-30.

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
- [x] **Tests minimos.** 31 pruebas en total: 13 de contrato, 5 de payloads
      reales, 8 de `device_id`, 5 del nucleo.
- [~] **Type hints completos y docstrings en espanol.** Los modulos movidos
      conservan el estilo del original: unos tienen anotaciones y otros no. No
      se reescribieron a proposito — el HITO 4 mueve codigo, y reescribirlo a la
      vez haria imposible distinguir un fallo de migracion de un fallo nuevo.
      Queda como deuda anotada para los HITOS 5-7, donde cada cliente se toca
      de verdad.

## 7. Verificacion

| Comprobacion | Resultado |
|---|---|
| Modulos del nucleo que importan solos | 10/10 |
| Alias que resuelven al nucleo | 10/10 en los 3 clientes migrados |
| Imports de `main.py` por cliente | tienda 11/11 · perimetrales 13/13 · managers 11/11 |
| `stop_scanner()` y destruccion sin TypeError | los 3 clientes |
| Amazonas View tras revertir | importa correctamente |

## 8. Deuda que sale de este hito

1. **Instalar `elde_core` en Amazonas View** (necesita venv) — HITO 7.
2. **Paso 8: reconciliar los casi-duplicados** (`dvr/`, widgets). Riesgo alto,
   revision de diffs obligatoria.
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
