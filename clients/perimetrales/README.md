# Perimetrales View — cliente de vigilancia perimetral

Cliente de **vigilancia de perímetro**: captura vídeo de cámaras DVR o de una
ventana, lo envía al servidor de IA y muestra las entradas y salidas al
perímetro con su clase, su identidad entre cámaras y su tiempo de permanencia.

Es el cliente **más avanzado del ecosistema**: buena parte del núcleo
compartido salió de aquí (el stack DVR completo, el descubrimiento en red, el
soporte de EZVIZ y el panel de dispositivos).

---

## Qué hace

| | |
|---|---|
| **Captura** | Canal DVR / Hik-Connect / EZVIZ por RTSP o HLS, o ventana de Windows |
| **Detecta** | Personas y vehículos que entran al perímetro marcado por el ROI |
| **Identifica** | Re-ID global: la misma persona conserva su `global_id` entre cámaras |
| **Panel Vehículos / Personas** | Dos columnas con hora de llegada, de salida y permanencia |
| **WhatsApp** | Reenvío de cada alerta como imagen al grupo |
| **Jarvis** | Reenvío de alertas a la plataforma Jarvis |
| **Panel web** | Galería de personas de interés y tablero de detecciones (:5333) |

## Modos de inferencia

Los elige el selector del pie:

- `VigilanteAmazonas` — detección de 7 clases, Re-ID de personas concretas
  contra una galería, y alertas.
- `Perimetrales`, `PerimetralesMultiCam`, `PerimetralesBoTSORT` — variantes del
  motor perimetral.

---

## Instalación y arranque

```bat
SETUP_CLIENTE.bat                        REM una sola vez
INICIAR_CLIENTE.bat                      REM arranca el servidor si hace falta
venv\Scripts\python.exe src\main.py      REM solo el cliente
```

El núcleo compartido va en modo editable:

```bat
venv\Scripts\python.exe -m pip install -e ..\packages\elde_core
```

---

## Configuración

Todo en el `.env` de esta carpeta. **Sin valores de red en el código**: desde el
HITO 6, si falta lo obligatorio el cliente avisa y no arranca. Antes se caía en
silencio a una IP escrita a mano y el operador no sabía por qué no llegaba nada.

| Parámetro | Obligatorio | Ejemplo | Para qué |
|---|---|---|---|
| `server_ws_url` | **sí** | `ws://192.168.1.50:9000/ws` | Servidor de IA |
| `site_id` | no | `perimetro-principal` | Sitio vigilado; viaja en el contrato |
| `dashboard_port` | no | `5333` | Puerto del panel de VIGILANTE |
| `dashboard_url` | no | `http://otra-maquina:5333` | Fuerza la URL del panel |
| `jarvis_email`, `jarvis_password`, `jarvis_url` | sí | — | Cuenta de Jarvis |

La URL del panel se **deriva** de `server_ws_url`, así que el panel y la
inferencia no pueden acabar apuntando a máquinas distintas.

### Las credenciales de Hik-Connect NO van aquí

Se escriben en el panel de **Dispositivos**, se guardan cifradas y se borran al
cerrar sesión. Tenerlas en un archivo fue lo que las publicó en GitHub
(`docs/refactor/HALLAZGOS.md`, H-13).

---

## Qué envía y qué recibe

Envía un mensaje por frame con los campos del contrato:

```jsonc
{
  "client_type": "perimetrales",       // quién habla
  "site_id": "perimetro-principal",    // desde dónde
  "type_inference": "VigilanteAmazonas",
  "data": { "image": "<JPEG>", "camera_id": "…", "roi_activate": true,
            "enviar_whatsapp": false }
}
```

Declarar `client_type` importa especialmente aquí: el servidor lo **deducía**
del modo de inferencia, y este cliente ofrece cuatro, así que la deducción
fallaba.

Recibe `metadata` con `detections` y `alerts`. Cada alerta lleva
`hora_llegada`, `hora_salida`, `permanencia_s` y `global_id`, que es lo que
pinta el panel lateral.

---

## Estructura

```
src/
├── main.py       punto de entrada
├── config/       configuración validada al arrancar
├── core/         enlaces al núcleo + almacén de capturas + reenvío a Jarvis
├── gui/          interfaz: recuadros, paneles, tema propio
└── workers/      hilos de captura
```

De 58 archivos, **36 son redirecciones** al núcleo `elde_core`; desaparecerán al
terminar la migración. Lo propio que queda es la lógica de vigilancia.

## Solución de problemas

| Síntoma | Causa probable |
|---|---|
| «Falta `server_ws_url` en el .env» | Falta la línea; el ejemplo va en el mensaje |
| Un canal aparece congelado | Enlace de nube caducado: reconecta el equipo en Dispositivos |
| «No se pudo abrir el stream» | El mensaje distingue si es enlace de nube o RTSP local |
| El panel :5333 no abre | El servidor no está arrancado, o `dashboard_port` no es el suyo |
| No llegan alertas a WhatsApp | El interruptor del pie está apagado, o el pipeline no emite alertas |
