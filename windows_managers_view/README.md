# Gestor de ventanas — cliente multimodo

Cliente oficial de proposito general: captura cualquier ventana de Windows o un
canal DVR, lo envia al servidor de IA y muestra los resultados.

Su panel derecho tiene **tres columnas** —Personas, Vehiculos y Eventos—
porque, al poder correr cualquier pipeline, dos columnas fijas dejarian fuera la
mitad de lo que puede ver: los pedidos de Hummus o las fases de Autolavado no
son ni personas ni vehiculos.

## Modos de inferencia

Es un cliente **multimodo**: el selector del pie ofrece `Hummus`, `HummusVLM`,
`Autolavado`, `Perimetrales`, `PerimetralesMultiCam` y `Personal de Amazonas`.

Por eso importa que declare su `client_type`: el servidor lo **deducia** del
modo de inferencia, y esa deduccion falla justamente en un cliente que ofrece
varios — pedir `Perimetrales` desde aqui se etiquetaba como si fuera el cliente
perimetral.

## Instalacion y arranque

```bat
INICIAR_CLIENTE.bat
venv\Scripts\python.exe src\main.py
```

El nucleo compartido va en modo editable:

```bat
venv\Scripts\python.exe -m pip install -e ..\packages\elde_core
```

## Configuracion

Todo en el `.env` de esta carpeta. **Sin valores de red en el codigo**: si falta
lo obligatorio, el cliente avisa con un ejemplo y no arranca. Antes se caia en
silencio a una IP escrita a mano, y el operador solo veia que "no llega nada".

| Parametro | Obligatorio | Para que |
|---|---|---|
| `server_ws_url` | **si** | Servidor de IA. De el se deriva la URL del dashboard |
| `site_id` | no | Local o sucursal; viaja en el contrato |
| `jarvis_email`, `jarvis_password`, `jarvis_url` | si | Cuenta de Jarvis |

### Las credenciales de Hik-Connect NO van aqui

Se escriben en el panel de **Dispositivos**, se guardan cifradas y se borran al
cerrar sesion. Tenerlas en un archivo fue lo que las publico en GitHub
(`docs/refactor/HALLAZGOS.md`, H-13).

## Estructura

```
src/
├── main.py       punto de entrada
├── config/       configuracion validada al arrancar
├── core/         enlaces al nucleo compartido
├── gui/          interfaz
└── workers/      hilos de captura
```

La mayoria de archivos son **redirecciones** al nucleo `elde_core` y
desapareceran al terminar la migracion.

## Solucion de problemas

| Sintoma | Causa probable |
|---|---|
| «Falta `server_ws_url` en el .env» | Falta la linea; el ejemplo va en el mensaje |
| Un canal aparece congelado | Enlace de nube caducado: reconecta el equipo en Dispositivos |
| El cliente abre y se cierra | Mirar la consola: `main.py` imprime el traceback |
