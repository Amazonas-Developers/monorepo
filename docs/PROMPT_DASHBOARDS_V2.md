# PROMPT — DASHBOARDS DE PRODUCTO POR CLIENTE (ELDE, fase 2 de dashboards)

Eres el ingeniero del ecosistema ELDE. Vas a convertir los dashboards de
dominio (hoy son vistas finas del HITO 9) en **dashboards de producto
completos**, uno por cliente: Perimetrales, Tienda, Amazonas y Managers.

Trabaja en `C:\Users\Sistema-1\Desktop\ELDE` (monorepo git, publicado).

## 0. Contexto que NO debes redescubrir (léelo, no lo deduzcas)

- **Lee primero**: `README.md`, `docs/refactor/12_CIERRE.md`,
  `docs/refactor/10_DASHBOARDS.md`, `docs/refactor/HALLAZGOS.md` (registro
  vivo de bugs, H-01…H-26), `dashboards/README.md`.
- **Arquitectura vigente**: los dashboards son **páginas estáticas** en
  `dashboards/` (HTML+JS plano, sin framework), servidas por el servidor en
  `http://localhost:9000/dashboards/`. Leen **exclusivamente por HTTP** de la
  API de lectura `/api/v1` (`server/src/app/api_lectura.py`). El servidor solo
  les hace de fichero. `shared/estilo.css` + `shared/api.js` son comunes.
- **Colores por dominio** (no los cambies): tienda `#00c8ff`, perimetrales
  `#e67e22`, amazonas `#9b59b6`, managers `#2ecc71`.
- **API existente**: `/api/v1/dispositivos`, `/sitios`, `/analitica/{id}`,
  `/heatmaps`, `/resumen`, `/paneles`, `/estado`. Todo GET.
- **El contrato websocket está en `estricto`** — no te afecta si respetas la
  regla: los dashboards NO tocan el websocket.
- El dashboard antiguo de visitantes (`/dashboard`, puerto 9000) sigue vivo y
  tiene galería/capturas/VLM: puedes REUSAR sus funciones internas desde
  endpoints nuevos, pero no lo rompas.

## 1. Dónde están los datos de verdad (verificado)

| Dato | Fuente |
|---|---|
| Alertas de perimetrales (fotos + metadatos) | `clients/perimetrales/screenshots/*.jpg` + sidecar `.json` con claves: `camara, clase, clase_gruesa, descripcion, epoch, evento (llegada/salida/permanencia/merodeo), global_id, permanencia_s, timestamp` |
| Capturas de personas (tienda/amazonas) | `server/output/captures/persons/*.jpg` + sidecar (género, edad, cámara, person_uuid…) — ya las sirve `/dashboard/api/captures` |
| Base de personas (visitantes únicos, género, edad) | `output/person_db/persons.pkl` — ya la sirve `/dashboard/api/summary` y `/api/v1/resumen` |
| Mapas de calor | `server/output/heatmap/<device_id>.png` — ya en `/api/v1/heatmaps` |
| Registro de cámaras | `/api/v1/dispositivos` (ids estables tipo `win-iVMS-4200`, `dvr-…`) |
| VLM del servidor | `server/src/analityc/core/multimodal_router.py` (VQA Qwen2.5-VL + grounding YOLO-World). El dashboard viejo lo usa vía `/dashboard/api/vlm` y `analizar-pendientes` |
| Panel VIGILANTE (galería, gestión) | `http://localhost:5333` — enlazar, no duplicar |

**FASE 0 obligatoria (solo lectura)**: inventaría estas fuentes con datos
reales, confirma formas y campos, y presenta un plan corto. PAUSA.

## 2. Los cuatro dashboards (especificación del usuario)

### 2.1 Perimetrales (`dashboards/perimetrales/`)
- **Buscador de alertas**: por texto (clase, cámara, global_id), por tipo de
  evento (llegada/salida/permanencia/merodeo) y por rango de fechas.
- **Galería de fotos** de las alertas (las de `screenshots/`), con su tarjeta
  (evento, clase, cámara, hora, permanencia).
- **Desglose de detecciones**: carros, motos, personas (y camioneta/camión si
  los datos lo traen — los sidecars distinguen `clase`).
- **Totales**: cantidad total de personas, motos y autos detectados.
- **Filtrado por detecciones**: ver solo alertas de una clase.
- **Mapa de calor** de sus cámaras.
- **Botón para encender/apagar la VLM** + **barra de búsqueda VLM** en
  lenguaje natural (ej.: «búscame el carro rojo») que busca sobre las fotos
  de alertas usando el router multimodal del servidor.

### 2.2 Tienda (`dashboards/tienda/`)
Evoluciona la página existente (no partas de cero):
- Mapa de calor; **pasillo más concurrido y menos concurrido** (ranking por
  cámara — los heatmaps y la analítica por device_id dan la materia prima).
- **Imágenes de detecciones** (capturas de personas con género/edad).
- **Evento «empleado repone anaquel»** y todo lo referente a marketing y
  consumo: visitantes únicos, entradas, permanencia media, distribución de
  género/edad, franjas horarias si los datos lo permiten. OJO: verifica ANTES
  qué eventos emite realmente el pipeline de tienda; si «repone anaquel» no
  existe aún como evento, dilo en el plan y proponlo como pendiente del
  servidor, no lo inventes en la UI.
- **Botón VLM + barra de búsqueda VLM** sobre sus capturas.

### 2.3 Amazonas (`dashboards/amazonas/`)
- Mapa de calor; detección de personas.
- **Género y edad**: desglose por género y por rango de edad.
- **Totales**: personas detectadas, total de mujeres, total de hombres.
- Enlaces a la galería de VIGILANTE (5333) como hasta ahora.

### 2.4 Managers (`dashboards/managers/`)
El usuario NO especificó este. Propuesta a validar: vista de OPERACIÓN
global — todas las cámaras de todos los dominios, salud del servidor
(contrato, registro, WhatsApp), últimos eventos de cada pipeline, accesos a
los otros tres dashboards. **Antes de construirlo, pregunta con panel de
opciones** qué quiere exactamente.

## 3. Trabajo de API que esto implica

- Lo que falte se añade a **`/api/v1` (solo GET)**: p. ej.
  `/api/v1/alertas?evento=&clase=&desde=&hasta=&q=` (lee los sidecars de
  perimetrales), `/api/v1/capturas`, `/api/v1/ranking-pasillos`. Sanea
  cualquier id/archivo que venga por URL (mira `_saneado`; los device_id
  acaban siendo nombres de archivo).
- **Las ACCIONES (VLM on/off, consulta VLM) NO van en `/api/v1`** — esa capa
  es solo lectura por regla. Ponlas bajo `/dashboard/api/vlm*` (donde ya
  viven las acciones) y documenta el porqué.
- La consulta VLM («búscame el carro rojo») debe: recibir el texto, correr el
  router multimodal sobre las N fotos más recientes (limita N y tiempo;
  la VLM tarda ~20-30 s por imagen), y devolver las coincidencias con su
  miniatura. Hazla asíncrona con sondeo de estado (el patrón de
  `analizar-pendientes` ya existe). Nada de bloquear el servidor.

## 4. Reglas de la casa (no negociables)

1. **Cero valores incrustados** (regla 6): puertos/rutas/umbrales salen de
   configuración o del entorno. Las páginas usan rutas RELATIVAS; los enlaces
   a otros puertos se piden a `/api/v1/paneles`.
2. **Bugs a `docs/refactor/HALLAZGOS.md`** (H-27 en adelante); no se corrigen
   sin aprobación, salvo los que tú introduzcas en esta misma obra.
3. **Verifica ejecutando, no compilando**: cada endpoint con `curl` contra el
   servidor real y datos reales; cada página servida con 200; el JS con
   `node --check`; los campos que la página lee, contrastados con la
   respuesta real del endpoint. La lección repetida del proyecto: lo que solo
   se importa/compila revienta al usarse (H-22, H-23, H-24).
4. **No rompas lo existente**: portada `/dashboards/`, páginas actuales,
   `/dashboard` viejo, panel 5333. Pruebas del núcleo (69) y del servidor
   (12) deben seguir en verde: córrelas.
5. **El servidor se arranca DESLIGADO** (los `INICIAR_*.bat` o
   `Start-Process`), jamás como hijo de tu sesión (lección H-26 operativa).
   Si generas datos sintéticos para probar, LÍMPIALOS al terminar (registro
   de dispositivos incluido).
6. Un commit por fase, mensaje en español explicando el porqué; push a
   `origin main` tras cada fase verificada.
7. UI en español; tema oscuro consistente con `shared/estilo.css`; el color
   de dominio se define con `--acento`.

## 5. Cómo trabajar con el usuario

- Cuando algo te bloquee o haya decisión de producto, **panel de preguntas**
  (3-4 opciones, la recomendada primero) — no suposiciones ni preguntas
  sueltas.
- Reporta con evidencia (cifras, salidas de curl), no con «debería
  funcionar». Si algo queda a medias, dilo explícitamente.
- Orden sugerido: FASE 0 inventario → FASE 1 API de lectura nueva →
  FASE 2 Perimetrales → FASE 3 Tienda → FASE 4 Amazonas → FASE 5 Managers
  (tras su panel) → FASE 6 VLM (botón + búsqueda). **PAUSA al final de cada
  fase** y espera aprobación explícita antes de la siguiente.
