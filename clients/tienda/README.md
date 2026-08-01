# ELDE Tienda — cliente de escritorio

Cliente de **analítica de visitantes para tienda**: captura vídeo (ventana de
Windows o canal DVR), lo envía al servidor de IA y muestra en vivo las personas
detectadas con su género y rango de edad.

Es un cliente de **modo único**: solo pide el pipeline `Personal de Amazonas`.
Para vigilancia perimetral está `perimetrales-view`; para varios modos a la vez,
`windows_managers_view`.

---

## Qué hace

| | |
|---|---|
| **Captura** | Ventana de Windows, o canal de DVR/Hik-Connect por RTSP |
| **Analiza** | Envía frames al servidor; recibe detecciones, género y edad |
| **Panel Visitantes** | Cada persona identificada, en vivo |
| **Panel Capturas** | Histórico de fotos, servido por el servidor vía HTTP |
| **Mapa de calor** | Zonas más transitadas, en el dashboard web |
| **WhatsApp** | Reenvío opcional de cada visitante al grupo |

---

## Instalación y arranque

```bat
SETUP_CLIENTE.bat                        REM una sola vez

..\INICIAR_TIENDA.bat                    REM servidor + dashboard + cliente
venv\Scripts\python.exe src\main.py      REM solo el cliente
```

El núcleo compartido se instala en modo editable, así que los cambios en
`packages/elde_core` se ven sin reinstalar nada:

```bat
venv\Scripts\python.exe -m pip install -e ..\packages\elde_core
```

---

## Configuración

Todo vive en el `.env` de esta carpeta. **No hay valores de red escritos en el
código**: desde el HITO 5, si falta lo obligatorio el cliente avisa y no
arranca, en vez de quedarse intentando conectar a una URL sin sentido.

| Parámetro | Obligatorio | Ejemplo | Para qué |
|---|---|---|---|
| `server_ws_url` | **sí** | `ws://192.168.1.50:9000/ws` | Servidor de IA. De aquí salen también la URL del dashboard y la del panel de capturas: siempre el mismo servidor |
| `site_id` | no | `tienda-principal` | Local o sucursal. Viaja en el contrato y permite comparar entre sucursales |
| `jarvis_email`, `jarvis_password`, `jarvis_url` | sí | — | Cuenta de Jarvis para el selector de establecimiento |
| `name_project` | no | `ELDE Tienda 🛒` | Título de la ventana |
| `DASHBOARD_URL` | no | `http://otro-servidor:9000/dashboards/tienda/` | Fuerza una URL de dashboard concreta. OJO: si queda apuntando a un panel retirado (como el 9030), el botón seguirá abriéndolo — el override gana siempre |

### Las credenciales de Hik-Connect NO van aquí

Se escriben en el panel de **Dispositivos** del propio cliente, se guardan
cifradas en su almacén y solo existen en memoria mientras la sesión está
abierta: al cerrar sesión se borran.

Tenerlas en un archivo de configuración fue precisamente lo que acabó
publicándolas en GitHub (ver `docs/refactor/HALLAZGOS.md`, H-13).

---

## Qué envía al servidor

Un mensaje por frame, con los campos del contrato
(`packages/elde_core/elde_core/contracts/`):

```jsonc
{
  "event": "inference",
  "client_type": "tienda",              // quién habla
  "site_id": "tienda-principal",        // desde dónde
  "type_inference": "Personal de Amazonas",
  "component_key": "<uuid del recuadro>",
  "data": {
    "image": "<JPEG>",
    "camera_id": "dvr-J12345678-2",     // identidad ESTABLE de la cámara
    "camera_name": "Camera 12",
    "roi_activate": true,
    "roi_coordinates": [[0.1, 0.1], [0.9, 0.9]],
    "heatmap_activate": true,
    "enviar_whatsapp": false
  }
}
```

`camera_id` sobrevive a los reinicios: se deriva del canal DVR (número de serie
+ canal), del título de la ventana o, en último recurso, de la posición del
recuadro. Antes era un UUID aleatorio por sesión, y por eso no existía
histórico por cámara (H-11).

## Qué recibe

`metadata` con `detections`, `demographics`, `people_counter`, `heatmap`,
`analytics_report` y **`alerts`**: un evento «Visitante» por persona, emitido en
el instante en que su género y edad convergen. Eso alimenta el panel lateral y
el envío por WhatsApp.

---

## Estructura

```
src/
├── main.py       punto de entrada
├── config/       toda la configuración, validada al arrancar
├── core/         enlaces al núcleo compartido + lógica propia
├── gui/          interfaz: recuadros de vídeo, paneles, barras
├── model/        modelos de datos
└── workers/      hilos de captura
```

La mayoría de archivos de `core/` y `gui/components/` son **redirecciones** al
núcleo `elde_core`: mantienen funcionando los imports antiguos mientras dura la
migración y desaparecerán al terminarla.

## Solución de problemas

| Síntoma | Causa probable |
|---|---|
| «Falta `server_ws_url` en el .env» | Falta la línea; el ejemplo va en el propio mensaje de error |
| No conecta | El servidor no está arrancado, o la IP del `.env` no es la suya |
| El panel Capturas está vacío | El servidor aún no ha guardado capturas, o no es accesible por HTTP |
| El botón Dashboard no abre nada | El servidor no expone el puerto del dashboard |
| El cliente abre y se cierra | Mirar la consola: `main.py` imprime el traceback completo |
