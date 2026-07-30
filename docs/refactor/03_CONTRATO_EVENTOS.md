# HITO 3 — Contrato unico cliente ↔ servidor

> Esquemas ejecutables en `packages/elde_core/elde_core/contracts/`.
> Este documento explica el porque; el codigo es la fuente de verdad.
> Generado el 2026-07-30.

---

## 1. El problema que resuelve

Hoy **no hay contrato**. El servidor lee 32 claves sueltas con `data.get(...)`
sin validar nada (`src/app/app.py`). Consecuencias medidas en el HITO 1:

- Un campo mal escrito en el cliente no da error: se lee como `None` y el
  comportamiento cambia en silencio.
- Nadie sabe que campos son obligatorios. La unica forma de averiguarlo es
  leer las 3.400 lineas del pipeline.
- No se puede evolucionar el formato sin arriesgarse a romper los 4 clientes.

## 2. El formato actual, tal cual circula

Reconstruido de `socket_client.py:130-136` y `render_box.py:645-669`:

```jsonc
{
  "event": "inference",              // siempre el mismo valor
  "id_connection": 1851954004384,    // id(websocket) que asigna el servidor
  "type_inference": "Personal de Amazonas",
  "component_key": "de60bb79-…",     // uuid4 por sesion (H-11)
  "data": {
    "image": "<bytes JPEG>",
    "camera_id": "de60bb79-…",       // el mismo uuid4, duplicado
    "camera_name": "Camera 12",
    "camera_angle": "frontal",
    "roi_activate": true,
    "roi_coordinates": [[x,y], …],
    "heatmap_activate": true,
    "track_classes": [0],
    "draw_server": true,
    "enable_vlm": false,
    "order_zone_*", "delivery_zone_*",   // Hummus
    "pay_roi*", "withdraw_roi*",         // Autolavado
    "door_roi*", "door_direction*"       // Perimetrales
  }
}
```

La respuesta reutiliza **el mismo sobre** con `data` sustituido por el
resultado (`app.py:785`).

### Lo que falta en el formato actual

| Falta | Por que importa |
|---|---|
| `site_id` | Sin el no se puede comparar entre sucursales, que es un requisito del HITO 9 |
| `device_id` estable | El actual cambia en cada arranque (H-11): no hay historico por camara |
| Version del evento | No se puede evolucionar el payload sin romper clientes |
| `timestamp` del emisor | La hora la pone el servidor al recibir, no cuando ocurrio |
| Que aplicacion habla | Solo llega el modo de inferencia, no el cliente |

---

## 3. El envelope nuevo

```python
Envelope(
    client_type    = ClientType.TIENDA,        # que aplicacion
    site_id        = "lacomarca",              # que local
    device_id      = "camera-12",              # que camara (ESTABLE)
    event_type     = EventType.FRAME_INFERENCE,
    event_version  = 1,
    timestamp_utc  = datetime(...),            # con zona horaria
    pipeline       = Pipeline.PERSONAL_AMAZONAS,
    payload        = {...},
)
```

### Decisiones y su razon

**`client_type` y `pipeline` son cosas distintas.** Hoy solo viaja
`type_inference`, y se usa a la vez como "quien habla" y "que quiero". Son
independientes: el cliente de perimetrales puede pedir `Perimetrales`,
`PerimetralesBoTSORT` o `VigilanteAmazonas` sin dejar de ser el mismo cliente.
Mezclarlos obligaria a inventar un `client_type` por modo.

**`device_id` se valida contra `^[A-Za-z0-9_.:-]{1,96}$`.** No es cosmetico:
estos identificadores acaban siendo **nombres de archivo** (heatmaps en
`output/heatmap/<device_id>.png`, capturas). Un `device_id` con `/` o `..`
escribe fuera del directorio previsto.

**El envelope es estricto (`extra='forbid'`), los payloads no.** La cabecera
es nueva y no la emite nadie todavia, asi que puede ser estricta desde el
principio. Los payloads admiten claves desconocidas **solo durante la
migracion**: con `extra='forbid'` cualquier cliente sin actualizar dejaria de
funcionar en cuanto el servidor validase — exactamente lo que hay que evitar.
Al terminar el HITO 7 deben pasar a estrictos.

---

## 4. Catalogo de eventos

| Evento | Direccion | Modelo | Validacion destacada |
|---|---|---|---|
| `connection.init` | servidor → cliente | `ConnectionInit` | `id_connection` obligatorio |
| `frame.inference` | cliente → servidor | `FrameInference` | JPEG real, poligonos de ≥3 puntos |
| `frame.result` | servidor → cliente | `FrameResult` | `processing_time` ≥ 0 |
| `heartbeat` | ambos | `Heartbeat` | estricto, solo `status` |
| `error` | servidor → cliente | `ErrorEvento` | `message` obligatorio |

### 4.1 Pipelines admitidos

Los 8 que `_build_processor` acepta hoy. **Ninguno se queda fuera** y no se
anade ninguno que no exista (criterio: nada huerfano entra al contrato).

| `type_inference` actual | `Pipeline` nuevo | `client_type` deducido |
|---|---|---|
| `Personal de Amazonas` | `personal_amazonas` | tienda |
| `Perimetrales` | `perimetrales` | perimetrales |
| `PerimetralesBoTSORT` | `perimetrales_botsort` | perimetrales |
| `PerimetralesMultiCam` | `perimetrales_multicam` | perimetrales |
| `VigilanteAmazonas` | `vigilante_amazonas` | perimetrales |
| `Autolavado` | `autolavado` | amazonas |
| `Hummus` | `hummus` | amazonas |
| `Misters` | `misters` | amazonas |

La columna derecha es **una deduccion temporal**: el formato antiguo no dice
que aplicacion habla. En cuanto un cliente emite envelope nuevo, `client_type`
viene explicito y la deduccion deja de usarse.

### 4.2 Validaciones que atrapan fallos reales

No son adornos; cada una corresponde a un fallo posible hoy:

| Validacion | Que evita |
|---|---|
| `image` empieza por `FF D8` | Un frame vacio o corrupto falla ahora dentro del detector, con un error mucho menos claro |
| Poligono de ≥3 puntos | Un ROI de 2 puntos es un segmento: el filtro de zona da resultados sin sentido |
| `device_id` sin `/` ni espacios | Escritura de archivos fuera del directorio previsto |
| `confianza` en [0, 1] | Detecta un pipeline que devuelva porcentajes en vez de fracciones |
| `timestamp_utc` con zona | Sin zona no se puede agregar por franja horaria |

---

## 5. Capa de compatibilidad

En `contracts/compat.py`. Permite que el servidor acepte **las dos formas a la
vez** y migrar los clientes de uno en uno (HITOS 5-7).

- `es_formato_antiguo(msg)` — lo reconoce por `type_inference` en la raiz sin
  `event_type`.
- `desde_antiguo(msg, site_id)` — traduce a `Envelope`. **No lanza**: devuelve
  `(None, motivo)` y el mensaje se descarta con log, sin tumbar la conexion.
- `hacia_antiguo(env, original)` — reconstruye la respuesta con la forma que
  el cliente viejo espera.
- `sin_migrar()` — cuenta mensajes antiguos por pipeline. **Es el criterio de
  retirada**: cuando lleve dias a cero, no queda ningun cliente antiguo y la
  capa se puede borrar.

`RENOMBRES` esta vacio a proposito: los nombres del payload se conservan tal
cual para que la migracion sea de **una sola variable** (el envelope) y no de
treinta a la vez.

---

## 6. Reconexion, latido y buffer sin red

Lo que hay hoy, medido en el codigo:

| Aspecto | Estado actual |
|---|---|
| Reconexion | `socket_client.py` tiene `reconnect_timer`, con intervalo fijo |
| Latido | El servidor manda `{"status":"ping"}` tras `WEBSOCKET_TIMEOUT` sin trafico |
| Buffer sin red | **No existe**: lo que se genera sin conexion se pierde |

Politica que adopta el contrato (a implementar en el nucleo, HITO 4):

1. **Reconexion con retroceso exponencial** — 1 s, 2 s, 4 s… hasta 30 s, con
   ±20% de aleatoriedad. El intervalo fijo actual hace que, si el servidor
   cae, los 4 clientes reintenten al unisono y lo tumben al volver.
2. **Latido bidireccional cada 15 s.** Hoy solo late el servidor: el cliente
   no detecta una conexion muerta hasta que intenta enviar.
3. **Buffer en disco de eventos no-frame.** Los frames caducan y **no** se
   almacenan (25/s de cientos de KB llenarian el disco). Los eventos de
   analitica —entradas, salidas, alertas— si: cola en disco con limite por
   tamano y reenvio al reconectar. Sin esto, un corte de red equivale a un
   agujero en los informes.

---

## 7. Estado de los criterios de aceptacion

- [x] **Todo evento vivo del HITO 1 aparece mapeado.** Los 8 pipelines y las
      32 claves del payload estan cubiertos.
- [x] **Ningun evento huerfano entra al contrato.** Las 5 rutas HTTP huerfanas
      del HITO 1 quedan fuera a proposito.
- [x] **Los esquemas validan los payloads reales.** Contrastado con una
      captura de una sesion real del 30-jul (pipeline `VigilanteAmazonas`),
      no solo con la reconstruccion del codigo. Ver seccion 7.1.

### 7.1 Lo que enseno la captura real (y el codigo no)

Esta es la razon por la que el criterio exigia payloads reales. La primera
version del contrato, deducida leyendo `render_box.py`, **habria rechazado un
mensaje real y perfectamente funcional**:

| Hallazgo | Deducido del codigo | Realidad capturada |
|---|---|---|
| `camera_angle` | `frontal\|lateral\|cenital`, por defecto `frontal` | tambien **`auto`**, y es el **valor por defecto real** (`person_amazona_inference.py:860`) |
| `header` | no existia | el cliente perimetral manda `{timestamp, size, format}` |
| `enviar_whatsapp` | no modelado | lo manda el cliente perimetral |
| Frame anotado | solo `image` | **`processed_image`** en los pipelines perimetrales |
| Detecciones | solo `tracks` | **`metadata.detections`** en los perimetrales |
| `status` | se asumio `"ok"` | tambien **`"success"`** |

El caso de `camera_angle` es el importante: un enum incompleto no es un
detalle cosmetico, es un **rechazo de trafico legitimo** en cuanto se active
la validacion en el servidor.

Tambien confirma algo para el HITO 8: el mismo dato viaja con **dos nombres
distintos** segun el pipeline (`image`/`processed_image`,
`tracks`/`metadata.detections`). El contrato modela los dos porque los dos
circulan; unificarlos es trabajo del servidor unico, no de este hito.

## 8. Pruebas

**18 pruebas en verde**, ejecutadas desde los venv del servidor, de tienda y
de perimetrales — el mismo contrato tiene que valer en los tres. Son los
**primeros tests del ecosistema**: no habia ninguno en los 5 proyectos.

`tests/test_contratos.py` (13) — el contrato contra el payload reconstruido
del codigo:

```
OK  test_el_payload_real_valida          <- el payload de tienda, campo por campo
OK  test_traduce_los_ocho_pipelines      <- ningun modo vivo se queda fuera
OK  test_rechaza_imagen_que_no_es_jpeg
OK  test_rechaza_poligono_de_dos_puntos
OK  test_envelope_rechaza_device_id_con_espacios
OK  test_la_respuesta_conserva_la_forma_antigua
… y 7 mas
```

`tests/test_payloads_reales.py` (5) — el contrato contra las **capturas de
verdad**. Si no hay capturas, se salta en vez de fallar. Incluye un aviso de
campos vistos pero no modelados, que es la lista de deberes antes de endurecer
los payloads al cerrar el HITO 7.

---

## 9. Lo que falta para cerrar el hito

1. **H-11**: el `device_id` estable. El contrato ya lo exige; el cliente
   todavia manda `uuid4()`. Es el paso 3 del plan de migracion y va en este
   hito.
2. **Conectar la validacion en el servidor**, detras de la capa de
   compatibilidad.

La captura de payloads reales (paso 2) esta **hecha**. Conviene repetirla con
el cliente de **tienda** —la sesion capturada fue de `VigilanteAmazonas`— para
cubrir tambien las claves de ese pipeline antes de endurecer nada.
