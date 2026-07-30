# Reorganización a `clients/ · server/ · dashboards/`

> La estructura de carpetas que el HITO 2 propuso y el HITO 5 aplazó.
> Ejecutada el 2026-07-30.

---

## 1. Lo que cambió de sitio

| Antes | Ahora | Archivos versionados |
|---|---|---:|
| `tienda_view/` | `clients/tienda/` | 82 |
| `perimetrales-view/` | `clients/perimetrales/` | 113 |
| `windows_managers_view/` | `clients/managers/` | 84 |
| `Amazonas View/` | `clients/amazonas/` | 99 |
| `SERVER-IA PERIMETRALES/` | `server/` | 170 |
| — | `dashboards/` | README |

`packages/`, `docs/`, `hik-connect/`, `modelos NVIDIA/` y `_legacy/` no se
tocaron.

**Cuadre:** 378 archivos versionados antes, 375 en `clients/` ahora. Los 3 que
faltan son los `HIKCONNECT_INTEGRATION.md` que el commit de seguridad debía
haber desversionado y no desversionó — ver H-18. Siguen en disco.

## 2. Por qué `dashboards/` está vacía

Porque llenarla ahora sería mentira. Los dos dashboards (`/dashboard` en el
9000 y el de tienda en el 9030) los sirve **el propio proceso del servidor**.
Moverlos de carpeta sin sacarlos de ese proceso solo añadiría un import cruzado
entre dos carpetas de primer nivel.

Sacarlos de verdad depende de la API de lectura del HITO 8, que todavía no
existe. La carpeta queda creada con un README que explica exactamente eso.

## 3. La parte que no era mover archivos

Mover fue lo rápido. Lo que costó fue lo que apuntaba a las rutas viejas:

| Qué | Cuántos |
|---|---:|
| Rutas absolutas a esta máquina en el servidor | **20** (5 en `app.py`, 15 en `Hummus`/`Misters`) |
| Lanzadores `.bat` | 3 |
| `selector.py` | 5 sitios |
| Pruebas del núcleo | 4 archivos |
| Rutas de `vigilante_amazonas` | 2 |
| Mensajes de error que nombraban la carpeta | 4 |
| Lanzadores `.exe` de los venv | **71** |

Los 20 valores del servidor están ahora anclados a `__file__`, no al directorio
de trabajo: da igual desde dónde se arranque.

### El cálculo de la raíz en dos lanzadores

`INICIAR_AMAZONAS.bat` e `INICIAR_PERIMETRALES.bat` deducían la raíz del
proyecto con `for %%I in ("%~dp0..")`. Al bajar un nivel, eso pasó a resolver a
`clients/`. Ahora es `%~dp0..\..`. Es el tipo de fallo que no da error: el
lanzador habría buscado el servidor en `clients/server` y dicho «falta el venv».

## 4. Los venv se movieron, no se recrearon

Decisión del usuario, y la correcta: el venv del servidor lleva torch, CUDA y
TensorRT, y recrearlo obliga a revalidar la GPU.

Antes de mover se comprobó que los `.exe` no llevaran la ruta dentro. **Esa
comprobación fue incompleta** —solo miró `pip.exe` y `python.exe` de un venv— y
por eso quedaron 71 lanzadores rotos, que se detectaron y arreglaron después.
De ahí salieron dos hallazgos: H-15 (los venv de tienda y perimetrales son
copias del de managers) y la nota técnica sobre el formato del shebang.

## 5. Verificación

No «compila», sino que **funciona**:

- [x] **Los 4 clientes importan el `main.py` completo** desde su ruta nueva:
      11/11, 13/13, 11/11 y 10/10 módulos propios.
- [x] **El servidor arranca desde `server/`** y los tres servicios responden
      200: inferencia (9000), dashboard de tienda (9030), panel de VIGILANTE
      (5333).
- [x] **45 pruebas del núcleo en verde** (44 + el guardia nuevo de H-17). La de
      payloads reales vuelve a encontrar las capturas del servidor, que es lo
      que confirma que la ruta nueva es la buena.
- [x] **`selector.py`**: las 4 entradas resuelven carpeta, `.bat`, venv y
      `main.py`.
- [x] **Los 5 `pip`** declaran su propio `site-packages` — antes dos de ellos
      declaraban el de otro cliente.

## 6. Lo que queda pendiente

1. Una carpeta vacía, `perimetrales-view/`, que no se pudo borrar porque un
   proceso externo la tiene abierta. Está vacía y git no versiona carpetas
   vacías: se puede borrar a mano cuando se cierre ese proceso.
2. Los venv **siguen sin aislar** (H-04). Se reapuntaron, no se recrearon.
3. Los valores por defecto de Hummus y Misters apuntan a tres modelos que no
   existen (H-16). Es previo a esto y no lo rompe nadie porque la configuración
   siempre los sobrescribe.
