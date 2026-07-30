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

### 5.1 El paquete `dvr/` (~682 LOC en 3 clientes)

Es el bloque mas grande y el mas tentador, pero `context.py` importa
`.hikconnect`, y `hikconnect.py` **no es identico** entre clientes: tiene 90-99%
de similitud (551 LOC). Moverlo obligaria a elegir una version sin haber leido
el diff, y esas divergencias suelen ser arreglos aplicados a un solo cliente.

Es el **paso 8** del plan, marcado de riesgo alto desde el HITO 2. Requiere
revisar diffs archivo por archivo, no una migracion mecanica.

### 5.2 Los widgets de interfaz (~1.800 LOC)

`device_panel.py` (650 LOC), `interactive_imageLabel.py` (250), `dvr_tree.py`,
`window_bar.py`, `modal_msm.py` y compania. Varios estan en el mismo caso que
`hikconnect`: casi identicos, no identicos. Y `window_bar.py` diverge
precisamente por mi arreglo de H-02.

### 5.3 `render_box.py`

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
