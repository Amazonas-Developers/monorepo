# HITO 8 — Servidor único: validación, registro de dispositivos y API de lectura

> Paso 11 del plan de migración del HITO 2. Generado el 2026-07-30.

---

## 1. Lo que había y lo que falta

El servidor procesaba frames sueltos. No sabía **qué dispositivos existen**, y
la única forma de leer su estado era estar dentro de su proceso. Eso es lo que
mantenía a `dashboards/` vacía.

| | Antes | Ahora |
|---|---|---|
| Saber qué cámaras hay | — | `registro_dispositivos.py` |
| Leer desde fuera | solo `/dashboard/api/`, acoplado | `api_lectura.py` → `/api/v1` |
| Validar lo entrante | `observar`, 0 pipelines vistos | `observar`, **7 de 8** vistos, 0 % de fallos |

## 2. El registro de dispositivos, y por qué no se podía hacer antes

Basta mirar `server/output/` para entenderlo:

```
analytics_log_18dbc565-37d4-4c2e-ad92-48df7b9f41a5.jsonl   <- uuid por sesión
analytics_report_client_2589894109200_win-iVMS-4200.json   <- id estable
```

Los primeros son de antes de H-11: cada arranque de un cliente inventaba una
cámara nueva. **Un registro construido sobre eso habría acumulado un
dispositivo por sesión** — justo la basura que se quería evitar. Con H-11
cerrado en los cuatro clientes, acumular por fin significa algo.

Guarda una fila por `(site_id, device_id)`: quién lo envía, con qué pipelines,
cómo se llama la cámara, cuándo se vio por primera y última vez, y cuántos
frames trajo.

Tres decisiones que merecen explicación:

- **La clave es `(site_id, device_id)`, no `device_id` a secas.** `box-1` es un
  id perfectamente válido, y dos tiendas distintas pueden tener cada una su
  recuadro 1.
- **Se prefiere el `client_type` declarado al deducido.** La deducción por
  pipeline falla justo en los clientes multimodo: `Perimetrales` lanzado desde
  el gestor de ventanas no es el cliente perimetral.
- **Archivo, no base de datos.** El HITO 2 dejó esa decisión para aquí. Son
  decenas de dispositivos, la escritura está amortiguada (30 s) y es atómica
  (temporal + `replace`). Meter un motor de base de datos sería la segunda
  migración grande a la vez. Este módulo es el único sitio que habría que
  cambiar el día que el volumen lo pida.

Y un aviso incorporado: `ids_inestables` cuenta los `device_id` con forma de
`uuid4`. Si deja de ser 0, **H-11 ha vuelto**.

## 3. La API de lectura

`/api/v1`, solo `GET`:

| Endpoint | Para qué |
|---|---|
| `/dispositivos` | qué cámaras conoce, con filtros por sitio y tipo de cliente |
| `/dispositivos/{id}` | una, con si tiene analítica y heatmap |
| `/sitios` | los sitios vistos y cuántos dispositivos tiene cada uno |
| `/analitica/{id}` | el informe más reciente de esa cámara |
| `/heatmaps` | los mapas de calor, cruzados con el registro |
| `/estado` | contrato + registro, sin entrar en el proceso |

Tres reglas deliberadas:

1. **Ni un `POST`.** Lo que modifica algo (vaciar detecciones, disparar
   análisis) se queda en `/dashboard/api/`. Esto no es un traslado: es una capa
   nueva y con menos permisos.
2. **Prefijo con versión.** El día que cambie la forma de una respuesta habrá
   `/api/v2` y los dashboards viejos seguirán vivos. Misma idea que el
   `event_version` del contrato.
3. **Nada de rutas de disco hacia fuera.** Un `device_id` que llega por la URL
   se sanea antes de tocar el sistema de archivos — acaba siendo un nombre de
   archivo, que es justo por lo que `slug()` prohíbe `..` y las barras.

`/heatmaps` marca los **huérfanos**: heatmap sin dispositivo conocido. Casi
todos son de antes de H-11, cuando el nombre del archivo era el uuid de sesión.

## 4. Lo que encontró el modo `observar`

Aquí está el valor real de haber arrancado la validación en modo observación en
vez de cortando.

### Un fallo del contrato que habría tumbado a los cuatro clientes

```
roi_coordinates: un poligono necesita 3 puntos o mas, llegaron 0
```

`get_coordinates()` devuelve `[]` cuando la zona no tiene puntos, y el cliente
lo manda acompañado de su `roi_activate: False`. El validador trataba la lista
vacía como un polígono roto.

**Al pasar a `estricto`, cualquier cliente sin un ROI dibujado habría dejado de
funcionar.** Corregido: vacío significa «no hay zona»; dos puntos sigue
significando «zona rota», y hay una prueba para cada caso.

### Dos fallos del servidor, anotados y no tocados

- **H-19** — `Hummus` no arranca: su modelo configurado (`models/base/1080.pt`)
  no existe en el disco. El propio arranque lo avisa en una línea que se pierde
  entre las demás. Confirma lo que H-16 suponía.
- **H-20** — `PerimetralesBoTSORT` **falla en cada frame**: `BoTSORTWrapper` no
  tiene rama propia en el despacho y cae en la genérica, que lo llama con cinco
  argumentos cuando su firma acepta dos. Este modo no funciona desde que las
  firmas divergieron.

Los dos quedan en `HALLAZGOS.md` sin corregir, según la regla 3.

Detalle que distingue las dos capas: **el contrato valida los mensajes de
BoTSORT sin problema** (aparece en `pipelines_observados` con 0 % de fallos).
Que el contrato acepte un mensaje no dice nada de si el dominio sabe atenderlo.

## 5. Estado de la validación: 7 de 8, y por qué no corto todavía

| | |
|---|---|
| Mensajes válidos | 25 |
| Con problemas | **0 %** |
| Pipelines vistos | `autolavado`, `misters`, `perimetrales`, `perimetrales_botsort`, `perimetrales_multicam`, `personal_amazonas`, `vigilante_amazonas` |
| Falta | `hummus` — **no se puede ejercitar**: su modelo no existe (H-19) |

**Sigue en `observar`, y a propósito.** Dos razones honestas:

1. El tráfico de esta prueba es **sintético**: lo generó un cliente falso, no
   los clientes reales. Comparte la forma del payload —`loop_show_result` es
   ahora código compartido, así que la forma es la misma para los cuatro— pero
   no cubre la ruta del DVR ni los envíos propios de tienda (VLM, eventos).
2. Tu decisión fue «tú los ejercitas y yo corto a estricto». Cortar con tráfico
   que me he inventado yo sería justo lo contrario.

Para cortar hace falta: arrancar los clientes reales unos minutos por modo, y
`hummus` necesita antes que se resuelva H-19.

### Una corrección de mi propio diagnóstico

Los primeros frames de prueba salieron `payload_invalido` y estuve a punto de
apuntarlo como fallo del contrato. **Eran dos errores de mi cliente falso**:
mandaba la imagen en base64 cuando los cuatro clientes reales mandan bytes
crudos, y `roi_coordinates: ''` cuando lo real es una lista. Comprobado en el
código de los cuatro antes de concluir nada. Solo el tercer error —el polígono
vacío— resultó ser real.

## 6. Estado de los criterios de aceptación

- [x] **Registro de dispositivos**, persistente entre arranques. Verificado
      reiniciando el servidor: recupera del disco lo que había.
- [x] **API de lectura** montada y respondiendo, con datos reales del registro.
- [x] **Validación** en el camino real, con 7 de 8 pipelines ejercitados y 0 %
      de fallos.
- [x] **Cero hardcode**: ruta del registro y periodo de volcado desde el
      entorno (`ELDE_REGISTRO_DISPOSITIVOS`, `ELDE_REGISTRO_SEGUNDOS`).
- [x] **Nunca rompe una conexión**: el registro va en el camino de cada frame y
      se traga sus propios errores, igual que la captura y la validación. Hay
      una prueba que le mete basura.
- [x] **Pruebas**: 9 del registro + 2 del contrato. Núcleo: **63**.
- [—] **`estricto`**: no se corta todavía. Sección 5.

## 7. Lo que queda

1. **Cortar a `estricto`** cuando los clientes reales hayan pasado por los 8
   modos. Bloqueado en parte por H-19.
2. **Endurecer los payloads** a `extra='forbid'`. Hoy están en `allow` para no
   romper a un cliente sin actualizar; con los cuatro migrados ya no hace falta,
   pero conviene hacerlo a la vez que el corte a `estricto`, no antes.
3. **HITO 9**: los dashboards sobre esta API. Ya tienen por dónde leer.
