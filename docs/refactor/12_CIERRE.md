# HITO 11 — Cierre del refactor del ecosistema ELDE

> Último hito del plan. Generado el 2026-07-31.

---

## 1. El ecosistema, antes y después

| | HITO 0 (29-jul) | Cierre (31-jul) |
|---|---:|---:|
| LOC de los 4 clientes | 37.043 (en 4 copias divergentes) | **12.976** |
| Núcleo compartido | — | **9.171** (con 63 pruebas) |
| Pruebas automatizadas | 0 | **75** (63 núcleo + 12 servidor) |
| Hardcode de red/rutas | IPs, puertos y rutas absolutas por doquier | **0** conocidos |
| Contrato de eventos | ninguno (formato implícito) | Pydantic, validando 8/8 pipelines* |
| Identidad de cámara | `uuid4()` por sesión (H-11) | estable, con registro de dispositivos |
| Estructura | 5 carpetas con nombres dispares | `clients/ · server/ · dashboards/ · packages/ · docs/` |
| Credenciales | en claro en el código y en GitHub | solo en sesión, cifradas en reposo (H-13) |

\* validados en forma con tráfico sintético; el corte a `estricto` espera
tráfico real (sección 4).

La cuenta honesta de LOC: los clientes suman 12.976 porque **ganaron**
funciones durante el refactor (paneles de alertas dedicados, WhatsApp,
capturas, sesión de credenciales), no solo perdieron duplicado. Lo eliminado
de verdad: ~24.000 LOC de copias divergentes, 1.492 de código muerto
(`_legacy/`, HITO 10) y 45 archivos de respaldos manuales (este hito).

## 2. Decisiones de este cierre (panel del 31-jul)

1. **Respaldos manuales borrados** con tag previo (`pre-hito11-backups`):
   `server/_backup_simplificacion_20260727`, los dos `_backup_limpieza_*` de
   amazonas y perimetrales, y el `__pycache__` de la raíz. `clientes_windows/`
   se conserva: es utilidad real de despliegue.
2. **El dashboard propio de tienda (9030) queda RETIRADO.** Lo sustituye
   `/dashboards/tienda/` sobre la API. Rollback en una línea: volver a llamar
   `iniciar_dashboard_tienda()` en `iniciar_servidor_headless.py` (el módulo
   sigue en el árbol). El de visitantes (`/dashboard`, 9000) sigue vivo:
   tiene el detalle que la página de dominio no replica.
3. **H-20 corregido con aprobación** — y al verificarlo salió su gemelo
   **H-21** (`Autolavado`, mismo defecto de despacho), corregido bajo la misma
   aprobación y dicho aquí: ver ambos en `HALLAZGOS.md`. Cero errores de firma
   con tráfico en los dos modos.
4. **Publicado en el monorepo de GitHub** al cerrar (tras revisar que ningún
   commit lleva secretos).

## 3. Los hitos, de una vez

| Hito | Qué fue | Informe |
|---|---|---|
| 0 | Inventario read-only (46.000 LOC, 5 subrepos) | `00_INVENTARIO.md` |
| 1 | Análisis de uso y código muerto | `01_ANALISIS_USO.md` |
| 2 | Arquitectura objetivo y plan de 13 pasos | `02_ARQUITECTURA_OBJETIVO.md` |
| 3 | Contrato de eventos (envelope + payloads + compat) | `03_CONTRATO_EVENTOS.md` |
| 4 | Núcleo compartido `elde_core` | `04_NUCLEO_COMPARTIDO.md` |
| 5-7 | Los 4 clientes sobre el núcleo | `05…07_*.md` |
| — | Reorganización a `clients/ server/ dashboards/` | `08_REORGANIZACION.md` |
| 8 | Registro de dispositivos + API de lectura | `09_SERVIDOR.md` |
| 9 | Tres dashboards de dominio sobre `/api/v1` | `10_DASHBOARDS.md` |
| 10 | Vaciado de `_legacy/` | `11_LEGACY.md` |
| 11 | Este cierre | `12_CIERRE.md` |

Los hallazgos (H-01…H-21) viven en `HALLAZGOS.md`: 17 corregidos, 4 abiertos.

## 4. Lo que queda abierto, ordenado por dueño

### Tuyo (nadie más puede)

| | Qué | Por qué urge |
|---|---|---|
| **H-13** | **Rotar** App Key/Secret de Hik-Connect y la key de Roboflow | llevan meses en el historial público de GitHub; borrarlas no las desexpone |
| — | Poner **privado** el monorepo | está público desde su creación |
| H-12 | Archivar (solo lectura) los 3 repos antiguos | dos clientes distintos publicaban al mismo remoto |
| — | **Ejercitar los 8 modos con clientes reales** unos minutos | es lo único que falta para cortar la validación a `estricto` con datos de verdad |

### Del código (con decisión previa)

| | Qué |
|---|---|
| H-19 | `Hummus` apunta a `models/base/1080.pt`, que no existe. Decisión de producto: conseguir ese modelo o apuntar a otro |
| H-04 | Los venv no aíslan (`--system-site-packages`); recrearlos exige revalidar torch/CUDA |
| H-10 | 50 GB de `modelos NVIDIA/` + 1 GB de `hik-connect/` en la raíz |
| contrato | pasar los payloads a `extra='forbid'` **a la vez** que el corte a `estricto` |
| núcleo | `geometry/` (propuesto en el HITO 2) encaja cuando se siga partiendo `render_box` |

### Deuda de arquitectura anotada

- El despacho de procesadores por `isinstance` con rama genérica es frágil
  (lección de H-20/H-21): un tercer caso pediría interfaz común, no otra rama.
- Retirar las redirecciones (`sys.modules`) de los clientes cuando cada uno
  importe `elde_core` directamente.
- `/dashboard` (visitantes) sigue leyendo por dentro del proceso; si algún día
  molesta, el camino es el que ya recorrió el de tienda: función a función
  hacia `/api/v1`.

## 5. Cómo se opera hoy (referencia rápida)

| Acción | Cómo |
|---|---|
| Arrancar todo (tienda) | `INICIAR_TIENDA.bat` (servidor + dashboard + cliente) |
| Elegir sistema | `SELECTOR.bat` |
| Dashboards | `http://<host>:9000/dashboards/` |
| Analítica de visitantes (detalle) | `http://<host>:9000/dashboard` |
| Panel VIGILANTE | `http://<host>:5333` |
| API de lectura | `http://<host>:9000/api/v1/…` |
| Salud + contrato + registro | `http://<host>:9000/health` |
| Logs | `logs/<client_type>.log` en cada cliente y `server/logs/server.log` |
| Credenciales Hik-Connect | panel de Dispositivos del cliente (sesión; se borran al cerrar) |
| Validación del contrato | `ELDE_VALIDAR_CONTRATO=observar\|estricto\|apagado` |

## 6. Reglas que siguen vigentes después del refactor

1. **Cero valores incrustados** (regla 6): todo nuevo puerto, ruta o umbral
   sale de configuración.
2. **Los bugs se anotan en `HALLAZGOS.md`** y se corrigen con aprobación
   (regla 3). El registro queda vivo.
3. **Lo compartido va al núcleo con pruebas**; los clientes guardan solo su
   dominio.
4. **El contrato es código**: si un payload cambia, cambia primero en
   `elde_core/contracts` y sube `event_version`.
