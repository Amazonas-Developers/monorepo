# HITO 6 — Cliente de perimetrales

> Segundo cliente sobre el núcleo y el contrato. Generado el 2026-07-30.

---

## 1. Llegaba mucho más sano que tienda

Es el cliente **modernizado el 28-jul**, y se nota: no tenía código muerto que
poner en cuarentena. De hecho, en los HITOS 4 y 6 ha sido **la fuente** de casi
todo lo que se movió al núcleo.

| | Antes | Después |
|---|---:|---:|
| Archivos `.py` | 56 | 58 |
| LOC | 3.124 | 2.933 |
| Redirecciones al núcleo | 34 | **36** |
| Código propio | 22 archivos | 22 |
| Hardcode de red | 2 sitios | **0** |

Los dos archivos de más son `config/`; los 191 LOC de menos salen de mover dos
workers al núcleo.

De sus **9.926 LOC del HITO 0** quedan 2.933: **menos de un tercio**.

## 2. Dos workers más al núcleo, y perimetrales vuelve a ganar

| Archivo | Versiones | Decisión |
|---|---|---|
| `rtsp_worker.py` | tienda/managers/Amazonas 90 LOC · perimetrales 103 | perimetrales |
| `capture_woker.py` | 154 · **179** · 154 · 110 | perimetrales |

No es preferencia: su versión arregla cosas reales.

**`rtsp_worker`** añade `CAP_PROP_OPEN_TIMEOUT_MSEC` y `READ_TIMEOUT_MSEC`. Sin
ellos, *«una URL muerta cuelga el hilo ~30 s dentro de FFMPEG y el canal parece
congelado sin decir nada»*. Y distingue el motivo del fallo: un enlace de
Hik-Connect llega por HTTP y caduca; un RTSP local falla por IP o credenciales.
El mensaje al operador es distinto en cada caso.

**`capture_woker`** arregla el empaquetado: con PyInstaller en modo ventana
`sys.stdout` es `None`, pero el descriptor 1 heredado del padre sí vale. Cae a
`os.write(1)`. Sin eso, el ejecutable no devolvía frames.

Los otros tres clientes heredan ambos arreglos.

## 3. Configuración: `config/` absorbe `dashboard_url`

`core/dashboard_url.py` ya estaba **bien pensado** —derivaba la URL del panel
desde `server_ws_url` en vez de configurarla aparte—, así que la lógica se
conservó tal cual. Lo que le faltaba era **validar**:

> Antes, si `server_ws_url` no estaba en el `.env`, se caía en silencio a
> `ws://127.0.0.1:9000/ws` y el cliente intentaba conectar a un servidor local
> que quizá no existía. El operador veía un cliente que "no recibe nada".

Ahora es obligatorio y falla con un mensaje que trae el ejemplo. El módulo
antiguo se queda como redirección de 12 líneas para no tocar sus tres llamantes.

Este cliente habla con **dos servidores en la misma máquina** —inferencia en el
9000, panel de VIGILANTE en el 5333— y ambas URLs salen del mismo origen, así
que no pueden divergir.

## 4. El contrato importa más aquí que en tienda

El servidor **deducía** el `client_type` a partir del pipeline. Con tienda
acertaba por casualidad, porque solo tiene uno. Este cliente ofrece **cuatro**
(`VigilanteAmazonas`, `Perimetrales`, `PerimetralesMultiCam`,
`PerimetralesBoTSORT`), así que la deducción era justo lo que fallaba.

Ahora lo declara: `client_type: "perimetrales"`, `site_id: "perimetro-principal"`.

## 5. Estado de los criterios de aceptación

- [x] **Arranca y establece conexión.** Verificado contra el servidor real: el
      socket conecta y llega el `connection_init` con su `id_connection`.
- [x] **LOC y archivos antes/después** — sección 1.
- [x] **Cero hardcode** de IPs, puertos ni rutas en el código.
- [x] **Ningún import roto** — 13/13 módulos de `main.py`.
- [x] **README propio.**
- [x] **Comportamiento funcional equivalente.** Aquí no hay ningún cambio
      visible: a diferencia de tienda, no se retiró ninguna función de la
      interfaz.
- [—] **Cuarentena:** no aplica. No tenía código muerto; ya se había limpiado
      el 28-jul.

### Una nota sobre la verificación

El primer sondeo de conexión daba `is_connected() == False` durante 12 s, pero
el servidor **sí** registraba la conexión (`total_connections` pasaba de 0 a 1).
Al capturar las señales del socket directamente se ve que conecta y recibe
`connection_init` sin problema: el falso negativo estaba en mi sondeo, no en el
cliente. Se deja anotado porque la primera lectura invitaba a concluir lo
contrario.

## 6. Lo que queda para este cliente

1. **Partir `render_box.py`** (1.214 LOC), igual que en tienda.
2. **`tema.py`** (100 LOC) lo comparte con Amazonas View: candidato al núcleo
   cuando se unifique el estilo.
3. `capture_store.py` y `jarvis_alert_forwarder.py` son propios de este
   cliente y se quedan.
