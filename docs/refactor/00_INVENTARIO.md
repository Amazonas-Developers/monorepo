# HITO 0 — Inventario y mapeo del ecosistema ELDE

> Documento **de solo lectura**: ningun archivo de los proyectos fue modificado para producirlo. Generado el 2026-07-29.

## 1. Bloque de configuracion resuelto

| Clave | Valor detectado | Como se determino |
|---|---|---|
| `RUTA_RAIZ` | `C:\Users\Sistema-1\Desktop\ELDE` | directorio de trabajo |
| `ES_MONOREPO` | **false** (era) → **true** (ahora) | la raiz no estaba versionada; se hizo `git init` como linea base |
| `CLIENTE_TIENDA` | `tienda_view` | `.env`: `name_project = ELDE Tienda`; `INICIAR_TIENDA.bat` |
| `CLIENTE_PERIMETRALES` | `perimetrales-view` | `INICIAR_PERIMETRALES.bat`; entrada en `selector.py` |
| `CLIENTE_AMAZONAS` | `Amazonas View` (dentro de ELDE) | `INICIAR_AMAZONAS.bat`; confirmado por el usuario |
| 4.º cliente | `windows_managers_view` | `selector.py:126` lo llama "Cliente oficial" |
| `SERVIDOR` | `SERVER-IA PERIMETRALES` | unico; lo arrancan los .bat de tienda y de Amazonas |
| `DASHBOARDS` | *no existe como carpeta* | hoy los sirve el propio servidor |
| `STACK_CLIENTES` | Python 3.12 + PySide6 6.10 + OpenCV + Ultralytics | `requirements.txt` |
| `STACK_SERVIDOR` | Python + FastAPI + uvicorn + WebSocket | `iniciar_servidor_headless.py`, `src/app/app.py` |
| `TRANSPORTE` | WebSocket (`ws://HOST:9000/ws`) + REST | `windows_main.py:167` |
| `BASE_DE_DATOS` | **ninguna** | persistencia en archivos: `.pkl`, JSON, PNG |

## 2. Metricas globales

Excluye `venv/`, `.git/`, `__pycache__/`, pesos de modelos, videos, imagenes y salidas de ejecucion.

| Proyecto | Archivos | LOC | Tamano | .py | LOC .py |
|---|---:|---:|---:|---:|---:|
| `tienda_view` | 72 | 9,461 | 427 KB | 61 | 8,764 |
| `perimetrales-view` | 96 | 11,252 | 522 KB | 77 | 9,926 |
| `windows_managers_view` | 71 | 7,867 | 353 KB | 59 | 7,185 |
| `Amazonas View` | 80 | 8,463 | 370 KB | 62 | 7,482 |
| `SERVER-IA PERIMETRALES` | 168 | 55,569 | 2608 KB | 132 | 43,826 |
| **TOTAL** | **487** | **92,612** | **4280 KB** | **391** | **77,183** |

Los **4 clientes** suman 37,043 LOC; el servidor por si solo, 55,569 LOC.

## 3. Puntos de entrada reales

| Sistema | Comando real | Arranca el servidor |
|---|---|---|
| Tienda (completo) | `INICIAR_TIENDA.bat` → `venv\Scripts\python.exe iniciar_servidor_headless.py 9000` + `python src\main.py` | si |
| Perimetrales | `perimetrales-view\INICIAR_CLIENTE.bat` → `venv\Scripts\python.exe src\main.py` | no (lo espera arriba) |
| Gestor de ventanas | `windows_managers_view\INICIAR_CLIENTE.bat` → `venv\Scripts\python.exe src\main.py` | no |
| Amazonas View | `Amazonas View\INICIAR_AMAZONAS.bat` → `iniciar_servidor_headless.py` + `src\main.py` | **si** |
| Hub | `SELECTOR.bat` → `pythonw selector.py` (Python GLOBAL, no venv) | segun el sistema |

> **Hallazgo.** `selector.py:137-141` afirma que Amazonas View es un "proyecto aparte, backend propio" y lo marca `needs_server=False`. Es falso: su `.bat` lanza el mismo `iniciar_servidor_headless.py`. Los cuatro clientes dependen del servidor unico.

## 4. Dependencias declaradas

| Proyecto | Archivo | Nº deps |
|---|---|---:|
| tienda_view | `requirements.txt` / `requirements-cliente.txt` | 104 / 14 |
| perimetrales-view | `requirements.txt` / `requirements_cliente.txt` | 104 / 15 |
| windows_managers_view | `requirements.txt` | 104 |
| Amazonas View | `requirements.txt` | 104 |
| SERVER-IA PERIMETRALES | `requirements.txt` (UTF-16) | 92 |

> **Hallazgo.** Los cuatro clientes declaran **exactamente 104** dependencias: es el mismo archivo copiado. El del servidor son 92 paquetes y es un volcado de `pip freeze` (el entorno entero en orden alfabetico), no una lista curada.
>
> **Correccion (HITO 1).** Una version previa de este documento decia "1796 lineas". Era **falso**: el archivo esta codificado en **UTF-16LE** y `grep` lo contaba mal. Tiene 92 lineas. Detalle en `HALLAZGOS.md` H-06.

## 5. Duplicacion entre proyectos

- Grupos de archivos **byte a byte identicos** entre proyectos distintos: **49**
- Pares de `.py` con similitud >= 60%: **208** (identicos: 104, 90-99%: 61, 75-89%: 24, 60-74%: 19)
- Archivos implicados en pares >= 90%: **136**, ~**17,705 LOC** duplicados

### 5.1 Muestra de duplicacion (top 40 pares)

| Archivo | Proyecto A | Proyecto B | Similitud |
|---|---|---|---:|
| `get_and_test.py` | tienda_view | perimetrales-view | **100% identico** |
| `get_and_test.py` | tienda_view | Amazonas View | **100% identico** |
| `get_and_test.py` | perimetrales-view | Amazonas View | **100% identico** |
| `get_url.py` | tienda_view | perimetrales-view | **100% identico** |
| `get_url.py` | tienda_view | Amazonas View | **100% identico** |
| `get_url.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\app_singleton.py` | tienda_view | perimetrales-view | **100% identico** |
| `src\core\app_singleton.py` | tienda_view | windows_managers_view | **100% identico** |
| `src\core\app_singleton.py` | tienda_view | Amazonas View | **100% identico** |
| `src\core\app_singleton.py` | perimetrales-view | windows_managers_view | **100% identico** |
| `src\core\app_singleton.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\app_singleton.py` | windows_managers_view | Amazonas View | **100% identico** |
| `src\core\capture_exaple.py` | tienda_view | perimetrales-view | **100% identico** |
| `src\core\capture_exaple.py` | tienda_view | windows_managers_view | **100% identico** |
| `src\core\capture_exaple.py` | tienda_view | Amazonas View | **100% identico** |
| `src\core\capture_exaple.py` | perimetrales-view | windows_managers_view | **100% identico** |
| `src\core\capture_exaple.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\capture_exaple.py` | windows_managers_view | Amazonas View | **100% identico** |
| `src\core\locking_windows.py` | tienda_view | windows_managers_view | **100% identico** |
| `src\core\windows_detector.py` | perimetrales-view | windows_managers_view | **100% identico** |
| `src\core\windows_detector.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\windows_detector.py` | windows_managers_view | Amazonas View | **100% identico** |
| `src\core\window_capture.py` | tienda_view | windows_managers_view | **100% identico** |
| `src\core\window_controller.py` | tienda_view | perimetrales-view | **100% identico** |
| `src\core\window_controller.py` | tienda_view | windows_managers_view | **100% identico** |
| `src\core\window_controller.py` | tienda_view | Amazonas View | **100% identico** |
| `src\core\window_controller.py` | perimetrales-view | windows_managers_view | **100% identico** |
| `src\core\window_controller.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\window_controller.py` | windows_managers_view | Amazonas View | **100% identico** |
| `src\core\window_global.py` | perimetrales-view | windows_managers_view | **100% identico** |
| `src\core\window_global.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\window_global.py` | windows_managers_view | Amazonas View | **100% identico** |
| `src\core\dvr\context.py` | tienda_view | windows_managers_view | **100% identico** |
| `src\core\dvr\context.py` | tienda_view | Amazonas View | **100% identico** |
| `src\core\dvr\context.py` | windows_managers_view | Amazonas View | **100% identico** |
| `src\core\dvr\dahua_http.py` | tienda_view | perimetrales-view | **100% identico** |
| `src\core\dvr\dahua_http.py` | tienda_view | Amazonas View | **100% identico** |
| `src\core\dvr\dahua_http.py` | perimetrales-view | Amazonas View | **100% identico** |
| `src\core\dvr\dahua_sdk.py` | tienda_view | perimetrales-view | **100% identico** |
| `src\core\dvr\dahua_sdk.py` | tienda_view | Amazonas View | **100% identico** |

### 5.2 Grupos identicos byte a byte

| Archivo | Presente en | LOC |
|---|---|---:|
| `src\gui\windows_main.py` | tienda_view, windows_managers_view | 300 |
| `src\gui\styles\global.qss` | Amazonas View, perimetrales-view | 272 |
| `src\core\dvr\hikvision_sdk.py` | Amazonas View, perimetrales-view, tienda_view | 253 |
| `main.spec` | perimetrales-view, tienda_view, windows_managers_view | 198 |
| `src\gui\components\sidebar\dvr_tree.py` | perimetrales-view, tienda_view | 193 |
| `src\gui\components\modal_msm.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 174 |
| `src\core\dvr\dahua_sdk.py` | Amazonas View, perimetrales-view, tienda_view | 170 |
| `src\core\network\jarvis_api.py` | Amazonas View, tienda_view, windows_managers_view | 167 |
| `src\core\window_controller.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 162 |
| `src\core\network\socket_client.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 135 |
| `src\gui\components\render_box\sv_overlay.py` | perimetrales-view, tienda_view | 134 |
| `src\gui\styles\global.qss` | tienda_view, windows_managers_view | 132 |
| `src\core\window_global.py` | Amazonas View, perimetrales-view, windows_managers_view | 130 |
| `src\core\dvr\hikconnect_channel_encoder.py` | perimetrales-view, tienda_view | 119 |
| `src\model\settings_model.py` | perimetrales-view, tienda_view, windows_managers_view | 116 |
| `src\gui\components\add_device_dialog.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 109 |
| `src\core\capture_exaple.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 107 |
| `requirements.txt` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 104 |
| `src\core\dvr\context.py` | Amazonas View, tienda_view, windows_managers_view | 103 |
| `src\gui\components\device_list.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 98 |
| `src\core\window_capture.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 90 |
| `src\workers\rtsp_worker.py` | tienda_view, windows_managers_view | 90 |
| `src\gui\components\sidebar\sidebar_dock.py` | Amazonas View, perimetrales-view, tienda_view | 89 |
| `src\gui\components\title_bar\window_bar.py` | Amazonas View, perimetrales-view, windows_managers_view | 89 |
| `src\core\dvr\dahua_http.py` | Amazonas View, perimetrales-view, tienda_view | 78 |
| `src\core\dvr\hikvision_http.py` | Amazonas View, perimetrales-view, tienda_view | 78 |
| `src\gui\components\box_image.py` | Amazonas View, perimetrales-view, tienda_view | 69 |
| `HIKCONNECT_INTEGRATION.md` | perimetrales-view, tienda_view | 68 |
| `src\core\app_singleton.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 66 |
| `src\gui\components\channel_row.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 65 |
| `src\core\locking_windows.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 62 |
| `src\workers\dvr_connect_worker.py` | Amazonas View, tienda_view | 57 |
| `src\core\windows_detector.py` | Amazonas View, perimetrales-view, windows_managers_view | 57 |
| `get_and_test.py` | Amazonas View, perimetrales-view, tienda_view | 53 |
| `EQUIPO.md` | perimetrales-view, tienda_view | 38 |

## 6. Inventario por archivo

Una tabla por proyecto. `Git` es la fecha del ultimo commit que toco el archivo (vacio = nunca versionado); `FS` es la fecha del sistema de archivos.

### tienda_view  ·  72 archivos  ·  9,461 LOC

<details><summary>Ver los 72 archivos</summary>

| Ruta | Leng. | LOC | Git | FS | Proposito inferido |
|---|---|---:|---|---|---|
| `src\gui\components\render_box\render_box.py` | Python | 1849 | — | 2026-07-29 | RenderBox original + soporte de drag & drop DVR (RTSP) con detección automática de Hik-Connect e IP. |
| `src\gui\components\device_panel.py` | Python | 650 | — | 2026-04-06 | Panel completo DVR para la pestaña "Dispositivos". |
| `src\core\dvr\hikconnect.py` | Python | 551 | — | 2026-04-07 | src/core/dvr/hikconnect.py — Hik-Connect for Teams OpenAPI V2.15.0 |
| `src\gui\components\planogram_editor.py` | Python | 454 | — | 2026-07-23 | planogram_editor.py - Editor visual del planograma de la tienda. |
| `src\gui\components\sidebar\alerts_sidebar.py` | Python | 449 | — | 2026-07-25 | AlertsSidebar – Panel lateral para mostrar alertas generadas por las detecciones de YOLO. |
| `src\gui\components\retail_panel.py` | Python | 444 | — | 2026-07-24 | retail_panel.py - Panel de analitica de supermercado. |
| `src\gui\windows_main.py` | Python | 300 | — | 2026-07-25 | Ventana principal — integra la pestaña Dispositivos DVR. |
| `src\core\dvr\hikvision_sdk.py` | Python | 253 | — | 2026-03-15 | src/core/dvr/hikvision_sdk.py |
| `src\gui\components\custon_label\interactive_imageLabel.py` | Python | 250 | — | 2026-05-13 | Colores BGR-independientes para Qt (RGB) |
| `main.spec` | PyInstaller | 198 | — | 2026-03-15 | -*- mode: python ; coding: utf-8 -*- |
| `src\gui\components\sidebar\dvr_tree.py` | Python | 193 | — | 2026-04-06 | src/gui/components/sidebar/dvr_tree.py |
| `src\gui\components\modal_msm.py` | Python | 174 | — | 2026-03-15 | QPushButton, QTextEdit, QWidget) |
| `src\core\dvr\dahua_sdk.py` | Python | 170 | — | 2026-03-15 | Estrategia Dahua via NetSDK nativo (ctypes). |
| `src\core\network\jarvis_api.py` | Python | 167 | — | 2026-03-15 | class Jarvis_api(QObject): |
| `src\core\window_controller.py` | Python | 162 | — | 2026-03-15 | - |
| `src\workers\capture_woker.py` | Python | 154 | — | 2026-06-14 | 🔥 SUPRIMIR LOGS NO DESEADOS AL INICIO |
| `src\core\window_global.py` | Python | 146 | — | 2026-07-29 | class Windows_monitor(QObject): |
| `src\core\network\socket_client.py` | Python | 135 | — | 2026-03-19 | class Socket_services(QObject): |
| `src\gui\components\render_box\sv_overlay.py` | Python | 134 | — | 2026-06-20 | Overlay de Supervision (Roboflow) para el MODO DIRECTO del cliente. |
| `src\gui\styles\global.qss` | Qt QSS | 132 | — | 2026-03-15 | QMainWindow{ |
| `src\gui\components\title_bar\window_bar.py` | Python | 126 | — | 2026-07-29 | load_dotenv() |
| `src\core\dvr\hikconnect_channel_encoder.py` | Python | 119 | — | 2026-04-06 | Codificador de canales Hik-Connect para serialización compatible. |
| `src\main.py` | Python | 118 | — | 2026-07-29 | UTF-8 seguro en consola: los print con emoji (📄 ✅ 🔥) NO deben crashear el |
| `src\model\settings_model.py` | Python | 116 | — | 2026-06-13 | class SettingsModel: |
| `src\gui\components\add_device_dialog.py` | Python | 109 | — | 2026-03-15 | class AddDeviceDialog(QDialog): |
| `src\core\capture_exaple.py` | Python | 107 | — | 2026-04-01 | Configurar PrintWindow |
| `requirements.txt` | Texto | 104 | — | 2026-01-25 | altgraph==0.17.4 |
| `src\core\dvr\context.py` | Python | 103 | — | 2026-04-06 | Contexto del Patrón Estrategia. |
| `src\gui\components\custom_status_bar.py` | Python | 103 | — | 2026-07-25 | class CustomStatusBar(QStatusBar): |
| `src\gui\components\device_list.py` | Python | 98 | — | 2026-03-15 | class ConnectionCheckThread(QThread): |
| `src\core\window_capture.py` | Python | 90 | — | 2026-03-19 | class WindowCapture: |
| `src\workers\rtsp_worker.py` | Python | 90 | — | 2026-03-19 | QThread que captura frames de un stream RTSP con OpenCV |
| `src\core\dvr\base.py` | Python | 89 | — | 2026-04-06 | Clases base del patrón estrategia DVR. |
| `src\gui\components\sidebar\sidebar_dock.py` | Python | 89 | — | 2026-03-15 | Sidebar con dos secciones en QToolBox: |
| `src\core\dvr\dahua_http.py` | Python | 78 | — | 2026-03-15 | Estrategia Dahua via CGI / RPC2 (HTTP + Digest Auth). |
| `src\core\dvr\hikvision_http.py` | Python | 78 | — | 2026-03-15 | Estrategia Hikvision via ISAPI (HTTP + Digest Auth + XML). |
| `src\gui\components\box_image.py` | Python | 69 | — | 2026-04-02 | class Box_cap(QWidget): |
| `HIKCONNECT_INTEGRATION.md` | Markdown | 68 | — | 2026-04-06 | Integración Hik-Connect — Guía de Uso (v2) |
| `src\core\windows_detector.py` | Python | 68 | — | 2026-07-29 | Definir la estructura de la función de callback para SetWinEventHook |
| `src\core\app_singleton.py` | Python | 66 | — | 2026-03-15 | load_dotenv() |
| `src\gui\components\channel_row.py` | Python | 65 | — | 2026-04-06 | Fila de canal con soporte drag & drop. |
| `src\core\locking_windows.py` | Python | 62 | — | 2026-03-15 | class WindowLocker: |
| `src\workers\dvr_connect_worker.py` | Python | 57 | — | 2026-03-15 | QThread que ejecuta DVRContext.connect() sin bloquear la UI. |
| `get_and_test.py` | Python | 53 | — | 2026-03-17 | get_and_test.py - Lee credenciales del almacenamiento de la app y prueba el stream. |
| `EQUIPO.md` | Markdown | 38 | — | 2026-05-29 | Guía de trabajo en equipo — windows_managers_view |
| `README.md` | Markdown | 36 | — | 2026-01-12 | window_manager/ |
| `SETUP_CLIENTE.bat` | Batch | 32 | — | 2026-07-25 | ============================================================ |
| `src\gui\components\title_bar\styles.qss` | Qt QSS | 32 | — | 2026-03-15 | /* Barra de título */ |
| `get_url.py` | Python | 30 | — | 2026-03-17 | get_url.py — Obtiene y muestra las URLs completas de Hik-Connect |
| `src\gui\components\SplashScreen.py` | Python | 27 | — | 2026-03-15 | class SplashScreen(QSplashScreen): |
| `src\gui\components\custon_btn\btn_footer.py` | Python | 26 | — | 2026-03-15 | class BtnIco(QPushButton): |
| `src\utils\files\print_png.py` | Python | 25 | — | 2026-03-15 | def buffer_to_png(bmpstr, width=None, height=None, output_path="output.png"): |
| `.gitignore` | - | 24 | 2026-07-29 | 2026-05-29 | ===== Python ===== |
| `requirements-cliente.txt` | Texto | 23 | — | 2026-07-25 | Dependencias REALES del CLIENTE de tienda (app Qt). |
| `test\test.py` | Python | 20 | — | 2026-01-12 | def main(): |
| `src\model\windows\list_windows.py` | Python | 17 | — | 2026-03-15 | load_dotenv() |
| `src\core\run_controller.py` | Python | 13 | — | 2026-03-15 | def check_admin_privileges(callback): |
| `src\core\state_global\hwnd.py` | Python | 13 | — | 2026-03-15 | class HwndState(QObject): |
| `.env` | - | 10 | — | 2026-07-29 | jarvis_email = '«CORREO ENMASCARADO»' |
| `src\core\dvr\__init__.py` | Python | 4 | — | 2026-04-06 | __all__ = ["DVRContext", "DeviceInfo", "ChannelInfo", "DVRStrategy", "HikConnectStrategy"] |
| `src\model\__init__.py` | Python | 1 | — | 2026-03-15 | - |
| `src\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\core\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\core\api_client.py` | Python | 0 | — | 2026-03-15 | - |
| `src\core\network\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\core\network\api_client.py` | Python | 0 | — | 2026-03-15 | - |
| `src\core\state_global\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\gui\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\gui\components\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\gui\components\custon_btn\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\model\windows\__init__.py` | Python | 0 | — | 2026-03-15 | - |
| `src\workers\__init__.py` | Python | 0 | — | 2026-03-15 | - |

</details>

### perimetrales-view  ·  96 archivos  ·  11,252 LOC

<details><summary>Ver los 96 archivos</summary>

| Ruta | Leng. | LOC | Git | FS | Proposito inferido |
|---|---|---:|---|---|---|
| `src\gui\components\render_box\render_box.py` | Python | 1214 | 2026-07-29 | 2026-07-28 | RenderBox original + soporte de drag & drop DVR (RTSP) con detección automática de Hik-Connect e IP. |
| `src\gui\components\device_panel.py` | Python | 920 | 2026-07-29 | 2026-07-28 | Panel completo DVR para la pestaña "Dispositivos". |
| `src\core\dvr\hikconnect.py` | Python | 580 | 2026-07-29 | 2026-07-27 | Cambios vs versión anterior: |
| `src\gui\components\sidebar\alerts_sidebar.py` | Python | 481 | 2026-07-29 | 2026-07-28 | AlertsSidebar – Panel lateral de alertas PERIMETRALES. |
| `src\core\dvr\discovery.py` | Python | 451 | 2026-07-29 | 2026-07-25 | src/core/dvr/discovery.py |
| `src\gui\components\custom_status_bar.py` | Python | 329 | 2026-07-29 | 2026-07-28 | QComboBox, QCheckBox, QPushButton) |
| `src\gui\windows_main.py` | Python | 325 | 2026-07-29 | 2026-07-25 | src/gui/windows_main.py |
| `src\core\network\jarvis_api.py` | Python | 295 | 2026-07-29 | 2026-07-17 | class Jarvis_api(QObject): |
| `src\gui\components\custon_label\interactive_imageLabel.py` | Python | 272 | 2026-07-29 | 2026-07-28 | Colores BGR-independientes para Qt (RGB) |
| `src\gui\styles\global.qss` | Qt QSS | 272 | 2026-07-29 | 2026-07-28 | /* ═══════════════════════════════════════════════════════════════════ |
| `src\core\dvr\hikvision_sdk.py` | Python | 253 | 2026-03-15 | 2026-03-15 | src/core/dvr/hikvision_sdk.py |
| `src\core\dvr\ezviz.py` | Python | 225 | 2026-07-29 | 2026-07-27 | src/core/dvr/ezviz.py — EZVIZ Open API (cuentas Hik-Connect de consumo) |
| `src\gui\components\discovery_dialog.py` | Python | 203 | 2026-07-29 | 2026-07-25 | src/gui/components/discovery_dialog.py |
| `main.spec` | PyInstaller | 198 | 2025-11-21 | 2026-03-15 | -*- mode: python ; coding: utf-8 -*- |
| `src\gui\components\sidebar\dvr_tree.py` | Python | 193 | 2026-07-02 | 2026-04-06 | src/gui/components/sidebar/dvr_tree.py |
| `src\workers\capture_woker.py` | Python | 179 | 2026-07-29 | 2026-07-21 | 🔥 SUPRIMIR LOGS NO DESEADOS AL INICIO |
| `_backup_limpieza_20260728\src\gui\components\modal_msm.py` 🚩 | Python | 174 | 2026-07-29 | 2026-03-15 | QPushButton, QTextEdit, QWidget) |
| `src\main.py` | Python | 173 | 2026-07-29 | 2026-07-25 | UTF-8 seguro en consola: los print con emoji (📄 ✅ 🔥) NO deben crashear el |
| `src\core\dvr\dahua_sdk.py` | Python | 170 | 2026-03-15 | 2026-03-15 | Estrategia Dahua via NetSDK nativo (ctypes). |
| `src\core\window_controller.py` | Python | 162 | 2025-11-21 | 2026-03-15 | - |
| `_build_exe.log` | .log | 147 | — | 2026-07-21 | 300 INFO: PyInstaller: 6.16.0, contrib hooks: 2025.9 |
| `src\core\network\jarvis_alert_forwarder.py` | Python | 136 | 2026-07-29 | 2026-07-20 | Reenviador de alertas hacia la API de Jarvis365. |
| `src\core\network\socket_client.py` | Python | 135 | 2026-03-23 | 2026-03-19 | class Socket_services(QObject): |
| `src\gui\components\render_box\sv_overlay.py` | Python | 134 | 2026-07-02 | 2026-06-20 | Overlay de Supervision (Roboflow) para el MODO DIRECTO del cliente. |
| `src\core\window_global.py` | Python | 130 | 2025-12-22 | 2026-03-15 | class Windows_monitor(QObject): |
| `src\core\dvr\hikconnect_channel_encoder.py` | Python | 119 | 2026-07-02 | 2026-04-06 | Codificador de canales Hik-Connect para serialización compatible. |
| `PerimetralesView.spec` | PyInstaller | 118 | 2026-07-29 | 2026-07-21 | -*- mode: python ; coding: utf-8 -*- |
| `src\model\settings_model.py` | Python | 116 | 2026-06-14 | 2026-06-13 | class SettingsModel: |
| `test_alerta_con_nombre.py` | Python | 115 | 2026-07-29 | 2026-07-18 | Prueba E2E REAL: persona registrada en el dashboard -> alerta CON SU NOMBRE. |
| `_backup_limpieza_20260728\src\gui\components\add_device_dialog.py` 🚩 | Python | 109 | 2026-07-29 | 2026-03-15 | class AddDeviceDialog(QDialog): |
| `src\core\dvr\context.py` | Python | 108 | 2026-07-29 | 2026-07-27 | Contexto del Patrón Estrategia. |
| `src\core\capture_exaple.py` | Python | 107 | 2026-07-02 | 2026-04-01 | Configurar PrintWindow |
| `requirements.txt` | Texto | 104 | 2026-01-25 | 2026-01-25 | altgraph==0.17.4 |
| `src\workers\rtsp_worker.py` | Python | 103 | 2026-07-29 | 2026-07-28 | QThread que captura frames de un stream RTSP con OpenCV |
| `src\gui\styles\tema.py` | Python | 100 | 2026-07-29 | 2026-07-28 | tema.py - Paleta y tokens de diseño de perimetrales-view. |
| `_backup_limpieza_20260728\src\gui\components\device_list.py` 🚩 | Python | 98 | 2026-07-29 | 2026-03-15 | class ConnectionCheckThread(QThread): |
| `src\core\capture_store.py` | Python | 98 | 2026-07-29 | 2026-07-28 | Almacén local de las fotos de las alertas (screenshots). |
| `src\core\dvr\base.py` | Python | 95 | 2026-07-29 | 2026-07-27 | Clases base del patrón estrategia DVR. |
| `_backup_limpieza_20260728\src\core\window_capture.py` 🚩 | Python | 90 | 2026-07-29 | 2026-03-19 | class WindowCapture: |
| `src\gui\components\sidebar\sidebar_dock.py` | Python | 89 | 2026-03-15 | 2026-03-15 | Sidebar con dos secciones en QToolBox: |
| `src\gui\components\title_bar\window_bar.py` | Python | 89 | 2025-12-21 | 2026-03-15 | load_dotenv() |
| `_backup_limpieza_20260728\src\core\network\vigilante_alertas_cliente.py` 🚩 | Python | 86 | 2026-07-29 | 2026-07-15 | Cliente de EJEMPLO para consumir las alertas de VIGILANTE-AMAZONAS desde |
| `test_jarvis_forwarder.py` | Python | 81 | 2026-07-29 | 2026-07-17 | Prueba de la LÓGICA del reenviador de alertas a Jarvis (sin red). |
| `src\core\dvr\dahua_http.py` | Python | 78 | 2026-03-15 | 2026-03-15 | Estrategia Dahua via CGI / RPC2 (HTTP + Digest Auth). |
| `src\core\dvr\hikvision_http.py` | Python | 78 | 2026-03-15 | 2026-03-15 | Estrategia Hikvision via ISAPI (HTTP + Digest Auth + XML). |
| `test_flujo_captura_sidebar.py` | Python | 75 | 2026-07-29 | 2026-07-18 | Prueba del FLUJO completo: alerta del servidor -> foto en capture/ -> tarjeta |
| `INICIAR_PERIMETRALES.bat` | Batch | 71 | 2026-07-29 | 2026-07-28 | ============================================================ |
| `CONSTRUIR_EXE.bat` | Batch | 70 | 2026-07-29 | 2026-07-21 | ============================================================ |
| `src\gui\components\box_image.py` | Python | 69 | 2026-07-02 | 2026-04-02 | class Box_cap(QWidget): |
| `HIKCONNECT_INTEGRATION.md` | Markdown | 68 | 2026-07-02 | 2026-04-06 | Integración Hik-Connect — Guía de Uso (v2) |
| `test_selector_establecimiento.py` | Python | 68 | 2026-07-29 | 2026-07-17 | Prueba del selector de establecimiento del pie (restaurado). |
| `src\core\app_singleton.py` | Python | 66 | 2025-10-19 | 2026-03-15 | load_dotenv() |
| `src\gui\components\channel_row.py` | Python | 65 | 2026-07-02 | 2026-04-06 | Fila de canal con soporte drag & drop. |
| `_backup_limpieza_20260728\src\core\locking_windows.py` 🚩 | Python | 62 | 2026-07-29 | 2026-03-15 | class WindowLocker: |
| `src\workers\dvr_connect_worker.py` | Python | 60 | 2026-07-29 | 2026-07-27 | QThread que ejecuta DVRContext.connect() sin bloquear la UI. |
| `test_toggle_envio_jarvis.py` | Python | 60 | 2026-07-29 | 2026-07-18 | Prueba del interruptor "Enviar a Jarvis" (activar/desactivar la API). |
| `test_boton_dashboard.py` | Python | 59 | 2026-07-29 | 2026-07-18 | Prueba del botón "Personas de interés" del pie + resolución de la URL. |
| `test_jarvis_conexion.py` | Python | 59 | 2026-07-29 | 2026-07-17 | Prueba de conexión a la API de Jarvis365 (sin GUI). |
| `src\core\windows_detector.py` | Python | 57 | 2025-10-23 | 2026-03-15 | Definir la estructura de la función de callback para SetWinEventHook |
| `get_and_test.py` | Python | 53 | 2026-03-23 | 2026-03-17 | get_and_test.py - Lee credenciales del almacenamiento de la app y prueba el stream. |
| `test_capture_store.py` | Python | 46 | 2026-07-29 | 2026-07-18 | Prueba del almacén local de fotos de alertas (capture_store). |
| `INSTRUCCIONES_CLIENTE.txt` | Texto | 44 | 2026-07-29 | 2026-07-21 | ================================================================ |
| `EQUIPO.md` | Markdown | 38 | 2026-07-02 | 2026-05-29 | Guía de trabajo en equipo — windows_managers_view |
| `README.md` | Markdown | 36 | 2025-12-26 | 2026-01-12 | window_manager/ |
| `requirements_cliente.txt` | Texto | 34 | 2026-07-29 | 2026-07-21 | ============================================================================= |
| `src\gui\components\title_bar\styles.qss` | Qt QSS | 32 | 2025-12-14 | 2026-03-15 | /* Barra de título */ |
| `generar_icono_persona.py` | Python | 31 | 2026-07-29 | 2026-07-18 | Genera el icono de persona del botón "Galería de personas" (resource/person.png). |
| `get_url.py` | Python | 30 | 2026-03-23 | 2026-03-17 | get_url.py — Obtiene y muestra las URLs completas de Hik-Connect |
| `.gitignore` | - | 27 | 2026-07-29 | 2026-07-29 | ===== Python ===== |
| `SETUP_CLIENTE.bat` | Batch | 27 | 2026-06-10 | 2026-06-10 | ============================================================ |
| `src\core\dashboard_url.py` | Python | 27 | 2026-07-29 | 2026-07-28 | Resuelve la URL del panel de VIGILANTE-AMAZONAS (:5333). |
| `src\gui\components\SplashScreen.py` | Python | 27 | 2025-11-21 | 2026-03-15 | class SplashScreen(QSplashScreen): |
| `src\gui\components\custon_btn\btn_footer.py` | Python | 26 | 2025-12-15 | 2026-03-15 | class BtnIco(QPushButton): |
| `_backup_limpieza_20260728\src\utils\files\print_png.py` 🚩 | Python | 25 | 2026-07-29 | 2026-03-15 | def buffer_to_png(bmpstr, width=None, height=None, output_path="output.png"): |
| `test\test.py` | Python | 20 | 2025-11-21 | 2026-01-12 | def main(): |
| `INICIAR_CLIENTE.bat` | Batch | 19 | 2026-06-10 | 2026-06-10 | ============================================================ |
| `.env` | - | 17 | — | 2026-07-25 | jarvis_email = '«CORREO ENMASCARADO»' |
| `src\model\windows\list_windows.py` | Python | 17 | 2025-10-26 | 2026-03-15 | load_dotenv() |
| `_backup_limpieza_20260728\src\core\run_controller.py` 🚩 | Python | 13 | 2026-07-29 | 2026-03-15 | def check_admin_privileges(callback): |
| `src\core\state_global\hwnd.py` | Python | 13 | 2025-10-26 | 2026-03-15 | class HwndState(QObject): |
| `src\core\dvr\__init__.py` | Python | 4 | 2026-07-02 | 2026-04-06 | __all__ = ["DVRContext", "DeviceInfo", "ChannelInfo", "DVRStrategy", "HikConnectStrategy"] |
| `init.bat` | Batch | 3 | 2025-10-15 | 2026-01-12 | python src/main.py |
| `activate.bat` | Batch | 1 | 2025-10-15 | 2026-01-12 | .\venv\Scripts\activate |
| `src\model\__init__.py` | Python | 1 | 2025-12-05 | 2026-03-15 | - |
| `_backup_limpieza_20260728\src\core\api_client.py` 🚩 | Python | 0 | 2026-07-29 | 2026-03-15 | - |
| `_backup_limpieza_20260728\src\core\network\api_client.py` 🚩 | Python | 0 | 2026-07-29 | 2026-03-15 | - |
| `src\__init__.py` | Python | 0 | 2025-11-21 | 2026-03-15 | - |
| `src\core\__init__.py` | Python | 0 | 2025-10-15 | 2026-03-15 | - |
| `src\core\network\__init__.py` | Python | 0 | 2025-12-26 | 2026-03-15 | - |
| `src\core\state_global\__init__.py` | Python | 0 | 2025-10-25 | 2026-03-15 | - |
| `src\gui\__init__.py` | Python | 0 | 2025-10-15 | 2026-03-15 | - |
| `src\gui\components\__init__.py` | Python | 0 | 2025-10-15 | 2026-03-15 | - |
| `src\gui\components\custon_btn\__init__.py` | Python | 0 | 2025-12-14 | 2026-03-15 | - |
| `src\gui\styles\__init__.py` | Python | 0 | 2026-07-29 | 2026-07-28 | - |
| `src\model\windows\__init__.py` | Python | 0 | 2025-10-26 | 2026-03-15 | - |
| `src\workers\__init__.py` | Python | 0 | 2025-10-28 | 2026-03-15 | - |

</details>

### windows_managers_view  ·  71 archivos  ·  7,867 LOC

<details><summary>Ver los 71 archivos</summary>

| Ruta | Leng. | LOC | Git | FS | Proposito inferido |
|---|---|---:|---|---|---|
| `src\gui\components\render_box\render_box.py` | Python | 1302 | 2026-07-02 | 2026-07-25 | RenderBox original + soporte de drag & drop DVR (RTSP) con detección automática de Hik-Connect e IP. |
| `src\gui\components\device_panel.py` | Python | 650 | 2026-07-02 | 2026-07-25 | Panel completo DVR para la pestaña "Dispositivos". |
| `src\core\dvr\hikconnect.py` | Python | 551 | 2026-07-02 | 2026-07-25 | src/core/dvr/hikconnect.py — Hik-Connect for Teams OpenAPI V2.15.0 |
| `src\gui\components\sidebar\alerts_sidebar.py` | Python | 394 | 2026-07-02 | 2026-07-25 | AlertsSidebar – Panel lateral para mostrar alertas generadas por las detecciones de YOLO. |
| `src\gui\windows_main.py` | Python | 300 | 2026-07-29 | 2026-07-25 | Ventana principal — integra la pestaña Dispositivos DVR. |
| `src\core\dvr\hikvision_sdk.py` | Python | 253 | 2026-03-15 | 2026-07-25 | src/core/dvr/hikvision_sdk.py |
| `src\gui\components\custon_label\interactive_imageLabel.py` | Python | 250 | 2026-07-02 | 2026-07-25 | Colores BGR-independientes para Qt (RGB) |
| `main.spec` | PyInstaller | 198 | 2025-11-21 | 2026-07-25 | -*- mode: python ; coding: utf-8 -*- |
| `src\gui\components\sidebar\dvr_tree.py` | Python | 193 | 2026-07-02 | 2026-07-25 | src/gui/components/sidebar/dvr_tree.py |
| `src\gui\components\modal_msm.py` | Python | 174 | 2025-10-15 | 2026-07-25 | QPushButton, QTextEdit, QWidget) |
| `src\core\dvr\dahua_sdk.py` | Python | 170 | 2026-03-15 | 2026-07-25 | Estrategia Dahua via NetSDK nativo (ctypes). |
| `src\core\network\jarvis_api.py` | Python | 167 | 2026-03-15 | 2026-07-25 | class Jarvis_api(QObject): |
| `src\core\window_controller.py` | Python | 162 | 2025-11-21 | 2026-07-25 | - |
| `src\workers\capture_woker.py` | Python | 154 | 2026-06-14 | 2026-07-25 | 🔥 SUPRIMIR LOGS NO DESEADOS AL INICIO |
| `src\core\network\socket_client.py` | Python | 135 | 2026-03-23 | 2026-07-25 | class Socket_services(QObject): |
| `src\gui\components\render_box\sv_overlay.py` | Python | 134 | 2026-07-02 | 2026-07-25 | Overlay de Supervision (Roboflow) para el MODO DIRECTO del cliente. |
| `src\gui\styles\global.qss` | Qt QSS | 132 | 2025-12-21 | 2026-07-25 | QMainWindow{ |
| `src\core\window_global.py` | Python | 130 | 2025-12-22 | 2026-07-25 | class Windows_monitor(QObject): |
| `src\core\dvr\hikconnect_channel_encoder.py` | Python | 119 | 2026-07-02 | 2026-07-25 | Codificador de canales Hik-Connect para serialización compatible. |
| `src\model\settings_model.py` | Python | 116 | 2026-06-14 | 2026-07-25 | class SettingsModel: |
| `src\main.py` | Python | 112 | 2026-07-02 | 2026-07-25 | UTF-8 seguro en consola: los print con emoji (📄 ✅ 🔥) NO deben crashear el |
| `src\gui\components\add_device_dialog.py` | Python | 109 | 2026-01-22 | 2026-07-25 | class AddDeviceDialog(QDialog): |
| `src\core\capture_exaple.py` | Python | 107 | 2026-07-02 | 2026-07-25 | Configurar PrintWindow |
| `requirements.txt` | Texto | 104 | 2026-01-25 | 2026-07-25 | altgraph==0.17.4 |
| `src\core\dvr\context.py` | Python | 103 | 2026-07-02 | 2026-07-25 | Contexto del Patrón Estrategia. |
| `src\gui\components\device_list.py` | Python | 98 | 2026-01-27 | 2026-07-25 | class ConnectionCheckThread(QThread): |
| `src\gui\components\custom_status_bar.py` | Python | 94 | 2026-07-02 | 2026-07-25 | class CustomStatusBar(QStatusBar): |
| `src\core\window_capture.py` | Python | 90 | 2026-03-23 | 2026-07-25 | class WindowCapture: |
| `src\workers\rtsp_worker.py` | Python | 90 | 2026-03-15 | 2026-07-25 | QThread que captura frames de un stream RTSP con OpenCV |
| `src\core\dvr\base.py` | Python | 89 | 2026-07-02 | 2026-07-25 | Clases base del patrón estrategia DVR. |
| `src\gui\components\sidebar\sidebar_dock.py` | Python | 89 | 2026-03-15 | 2026-07-25 | Sidebar con dos secciones en QToolBox: |
| `src\gui\components\title_bar\window_bar.py` | Python | 89 | 2025-12-21 | 2026-07-25 | load_dotenv() |
| `src\core\dvr\dahua_http.py` | Python | 78 | 2026-03-15 | 2026-07-25 | Estrategia Dahua via CGI / RPC2 (HTTP + Digest Auth). |
| `src\core\dvr\hikvision_http.py` | Python | 78 | 2026-03-15 | 2026-07-25 | Estrategia Hikvision via ISAPI (HTTP + Digest Auth + XML). |
| `src\gui\components\box_image.py` | Python | 69 | 2026-07-02 | 2026-07-25 | class Box_cap(QWidget): |
| `HIKCONNECT_INTEGRATION.md` | Markdown | 68 | 2026-07-02 | 2026-07-25 | Integración Hik-Connect — Guía de Uso (v2) |
| `src\core\app_singleton.py` | Python | 66 | 2025-10-19 | 2026-07-25 | load_dotenv() |
| `src\gui\components\channel_row.py` | Python | 65 | 2026-07-02 | 2026-07-25 | Fila de canal con soporte drag & drop. |
| `src\core\locking_windows.py` | Python | 62 | 2025-10-15 | 2026-07-25 | class WindowLocker: |
| `src\core\windows_detector.py` | Python | 57 | 2025-10-23 | 2026-07-25 | Definir la estructura de la función de callback para SetWinEventHook |
| `src\workers\dvr_connect_worker.py` | Python | 57 | 2026-03-15 | 2026-07-25 | QThread que ejecuta DVRContext.connect() sin bloquear la UI. |
| `get_and_test.py` | Python | 53 | 2026-03-23 | 2026-07-25 | get_and_test.py - Lee credenciales del almacenamiento de la app y prueba el stream. |
| `EQUIPO.md` | Markdown | 38 | 2026-07-02 | 2026-07-25 | Guía de trabajo en equipo — windows_managers_view |
| `README.md` | Markdown | 36 | 2025-12-26 | 2026-07-25 | window_manager/ |
| `src\gui\components\title_bar\styles.qss` | Qt QSS | 32 | 2025-12-14 | 2026-07-25 | /* Barra de título */ |
| `get_url.py` | Python | 30 | 2026-03-23 | 2026-07-25 | get_url.py — Obtiene y muestra las URLs completas de Hik-Connect |
| `SETUP_CLIENTE.bat` | Batch | 27 | 2026-06-10 | 2026-07-25 | ============================================================ |
| `src\gui\components\SplashScreen.py` | Python | 27 | 2025-11-21 | 2026-07-25 | class SplashScreen(QSplashScreen): |
| `src\gui\components\custon_btn\btn_footer.py` | Python | 26 | 2025-12-15 | 2026-07-25 | class BtnIco(QPushButton): |
| `src\utils\files\print_png.py` | Python | 25 | 2025-10-15 | 2026-07-25 | def buffer_to_png(bmpstr, width=None, height=None, output_path="output.png"): |
| `.gitignore` | - | 24 | 2026-06-10 | 2026-07-25 | ===== Python ===== |
| `test\test.py` | Python | 20 | 2025-11-21 | 2026-07-25 | def main(): |
| `INICIAR_CLIENTE.bat` | Batch | 19 | 2026-06-10 | 2026-07-25 | ============================================================ |
| `src\model\windows\list_windows.py` | Python | 17 | 2025-10-26 | 2026-07-25 | load_dotenv() |
| `src\core\run_controller.py` | Python | 13 | 2025-10-15 | 2026-07-25 | def check_admin_privileges(callback): |
| `src\core\state_global\hwnd.py` | Python | 13 | 2025-10-26 | 2026-07-25 | class HwndState(QObject): |
| `src\core\dvr\__init__.py` | Python | 4 | 2026-07-02 | 2026-07-25 | __all__ = ["DVRContext", "DeviceInfo", "ChannelInfo", "DVRStrategy", "HikConnectStrategy"] |
| `init.bat` | Batch | 3 | 2025-10-15 | 2026-07-25 | python src/main.py |
| `activate.bat` | Batch | 1 | 2025-10-15 | 2026-07-25 | .\venv\Scripts\activate |
| `src\model\__init__.py` | Python | 1 | 2025-12-05 | 2026-07-25 | - |
| `src\__init__.py` | Python | 0 | 2025-11-21 | 2026-07-25 | - |
| `src\core\__init__.py` | Python | 0 | 2025-10-15 | 2026-07-25 | - |
| `src\core\api_client.py` | Python | 0 | 2025-12-12 | 2026-07-25 | - |
| `src\core\network\__init__.py` | Python | 0 | 2025-12-26 | 2026-07-25 | - |
| `src\core\network\api_client.py` | Python | 0 | 2025-12-26 | 2026-07-25 | - |
| `src\core\state_global\__init__.py` | Python | 0 | 2025-10-25 | 2026-07-25 | - |
| `src\gui\__init__.py` | Python | 0 | 2025-10-15 | 2026-07-25 | - |
| `src\gui\components\__init__.py` | Python | 0 | 2025-10-15 | 2026-07-25 | - |
| `src\gui\components\custon_btn\__init__.py` | Python | 0 | 2025-12-14 | 2026-07-25 | - |
| `src\model\windows\__init__.py` | Python | 0 | 2025-10-26 | 2026-07-25 | - |
| `src\workers\__init__.py` | Python | 0 | 2025-10-28 | 2026-07-25 | - |

</details>

### Amazonas View  ·  80 archivos  ·  8,463 LOC

<details><summary>Ver los 80 archivos</summary>

| Ruta | Leng. | LOC | Git | FS | Proposito inferido |
|---|---|---:|---|---|---|
| `src\gui\components\render_box\render_box.py` | Python | 908 | 2026-05-29 | 2026-07-28 | RenderBox original + soporte de drag & drop DVR (RTSP) con detección automática de Hik-Connect e IP. |
| `src\gui\components\device_panel.py` | Python | 682 | 2026-05-29 | 2026-07-27 | src/gui/components/device_panel.py |
| `src\gui\components\captures_panel.py` | Python | 517 | — | 2026-07-28 | CapturesPanel (Amazonas View) — Pestaña "Capturas". |
| `src\gui\components\sidebar\capturas_sidebar.py` | Python | 424 | — | 2026-07-28 | capturas_sidebar.py - Panel lateral con las capturas en vivo. |
| `_backup_limpieza_20260727\alerts_sidebar.py` 🚩 | Python | 377 | — | 2026-07-27 | AlertsSidebar (Amazonas View) – Panel lateral con una sola columna |
| `src\gui\windows_main.py` | Python | 326 | 2026-05-29 | 2026-07-28 | Ventana principal — integra la pestaña Dispositivos DVR. |
| `src\gui\styles\global.qss` | Qt QSS | 272 | 2026-05-29 | 2026-07-28 | /* ═══════════════════════════════════════════════════════════════════ |
| `src\core\dvr\hikvision_sdk.py` | Python | 253 | 2026-05-29 | 2026-05-29 | src/core/dvr/hikvision_sdk.py |
| `src\core\dvr\hikconnect.py` | Python | 218 | 2026-05-29 | 2026-05-29 | src/core/dvr/hikconnect.py — Hik-Connect for Teams OpenAPI V2.14.0 |
| `main.spec` | PyInstaller | 199 | 2026-05-29 | 2026-05-29 | -*- mode: python ; coding: utf-8 -*- |
| `src\gui\components\custon_label\interactive_imageLabel.py` | Python | 187 | 2026-05-29 | 2026-07-28 | Interactive_imageLabel - Visor de video con el ROI del AREA de conteo. |
| `src\gui\components\sidebar\dvr_tree.py` | Python | 181 | 2026-05-29 | 2026-05-29 | Árbol sidebar con DVRs y canales arrastrables. |
| `_backup_limpieza_20260727\modal_msm.py` 🚩 | Python | 174 | — | 2026-07-27 | QPushButton, QTextEdit, QWidget) |
| `src\core\dvr\dahua_sdk.py` | Python | 170 | 2026-05-29 | 2026-05-29 | Estrategia Dahua via NetSDK nativo (ctypes). |
| `src\core\network\jarvis_api.py` | Python | 167 | 2026-05-29 | 2026-05-29 | class Jarvis_api(QObject): |
| `src\workers\video_worker.py` | Python | 166 | — | 2026-07-28 | QThread que recorre un ARCHIVO de video y entrega sus frames a la celda. |
| `src\core\window_controller.py` | Python | 162 | 2026-05-29 | 2026-05-29 | - |
| `src\core\network\socket_client.py` | Python | 135 | 2026-05-29 | 2026-05-29 | class Socket_services(QObject): |
| `src\core\window_global.py` | Python | 130 | 2026-05-29 | 2026-05-29 | class Windows_monitor(QObject): |
| `HIKCONNECT_INTEGRATION.md` | Markdown | 113 | 2026-05-29 | 2026-05-29 | Integración Hik-Connect - Guía de Uso |
| `src\core\dvr\hikconnect_channel_encoder.py` | Python | 113 | 2026-05-29 | 2026-05-29 | Codificador de canales Hik-Connect para serialización compatible. |
| `src\workers\capture_woker.py` | Python | 110 | 2026-05-29 | 2026-05-29 | 🔥 SUPRIMIR LOGS NO DESEADOS AL INICIO |
| `src\gui\components\add_device_dialog.py` | Python | 109 | 2026-05-29 | 2026-05-29 | class AddDeviceDialog(QDialog): |
| `src\core\capture_exaple.py` | Python | 107 | 2026-05-29 | 2026-05-29 | Configurar PrintWindow |
| `requirements.txt` | Texto | 104 | 2026-05-29 | 2026-05-29 | altgraph==0.17.4 |
| `src\model\settings_model.py` | Python | 104 | 2026-05-29 | 2026-07-27 | class SettingsModel: |
| `src\core\dvr\context.py` | Python | 103 | 2026-05-29 | 2026-05-29 | Contexto del Patrón Estrategia. |
| `src\main.py` | Python | 103 | 2026-05-29 | 2026-07-28 | MODELS AND DATA |
| `_backup_limpieza_20260727\device_list.py` 🚩 | Python | 98 | — | 2026-07-27 | class ConnectionCheckThread(QThread): |
| `INICIAR_AMAZONAS.bat` | Batch | 94 | — | 2026-07-28 | ============================================================ |
| `src\gui\components\custom_status_bar.py` | Python | 94 | 2026-05-29 | 2026-05-29 | class CustomStatusBar(QStatusBar): |
| `_backup_limpieza_20260727\window_capture.py` 🚩 | Python | 90 | — | 2026-07-27 | class WindowCapture: |
| `src\workers\rtsp_worker.py` | Python | 90 | 2026-05-29 | 2026-07-28 | QThread que captura frames de un stream RTSP con OpenCV |
| `src\gui\components\sidebar\sidebar_dock.py` | Python | 89 | 2026-05-29 | 2026-05-29 | Sidebar con dos secciones en QToolBox: |
| `src\gui\components\title_bar\window_bar.py` | Python | 89 | 2026-05-29 | 2026-05-29 | load_dotenv() |
| `src\core\dvr\base.py` | Python | 85 | 2026-05-29 | 2026-05-29 | Clases base del patrón estrategia DVR. |
| `src\gui\styles\tema.py` | Python | 85 | — | 2026-07-27 | tema.py - Paleta y tokens de diseño de Amazonas View. |
| `README.md` | Markdown | 80 | 2026-05-29 | 2026-05-29 | window_manager/ |
| `src\core\dvr\dahua_http.py` | Python | 78 | 2026-05-29 | 2026-05-29 | Estrategia Dahua via CGI / RPC2 (HTTP + Digest Auth). |
| `src\core\dvr\hikvision_http.py` | Python | 78 | 2026-05-29 | 2026-05-29 | Estrategia Hikvision via ISAPI (HTTP + Digest Auth + XML). |
| `_backup_limpieza_20260727\hikvision_manager.py` 🚩 | Python | 76 | — | 2026-07-27 | def open_device(name, ip, http_port, rtsp_port, user, password): |
| `src\gui\components\box_image.py` | Python | 69 | 2026-05-29 | 2026-05-29 | class Box_cap(QWidget): |
| `src\core\app_singleton.py` | Python | 66 | 2026-05-29 | 2026-05-29 | load_dotenv() |
| `src\gui\components\channel_row.py` | Python | 65 | 2026-05-29 | 2026-05-29 | Fila de canal con soporte drag & drop. |
| `_backup_limpieza_20260727\locking_windows.py` 🚩 | Python | 62 | — | 2026-07-27 | class WindowLocker: |
| `src\core\windows_detector.py` | Python | 57 | 2026-05-29 | 2026-05-29 | Definir la estructura de la función de callback para SetWinEventHook |
| `src\workers\dvr_connect_worker.py` | Python | 57 | 2026-05-29 | 2026-05-29 | QThread que ejecuta DVRContext.connect() sin bloquear la UI. |
| `get_and_test.py` | Python | 53 | 2026-05-29 | 2026-05-29 | get_and_test.py - Lee credenciales del almacenamiento de la app y prueba el stream. |
| `.gitignore` | - | 45 | 2026-05-29 | 2026-05-29 | ────────────────────────────────────────────── |
| `src\gui\components\title_bar\styles.qss` | Qt QSS | 32 | 2026-05-29 | 2026-05-29 | /* Barra de título */ |
| `get_url.py` | Python | 30 | 2026-05-29 | 2026-05-29 | get_url.py — Obtiene y muestra las URLs completas de Hik-Connect |
| `src\gui\components\SplashScreen.py` | Python | 27 | 2026-05-29 | 2026-05-29 | class SplashScreen(QSplashScreen): |
| `src\gui\components\custon_btn\btn_footer.py` | Python | 26 | 2026-05-29 | 2026-05-29 | class BtnIco(QPushButton): |
| `_backup_limpieza_20260727\print_png.py` 🚩 | Python | 25 | — | 2026-07-27 | def buffer_to_png(bmpstr, width=None, height=None, output_path="output.png"): |
| `test\test.py` | Python | 20 | 2026-05-29 | 2026-05-29 | def main(): |
| `src\model\windows\list_windows.py` | Python | 17 | 2026-05-29 | 2026-05-29 | load_dotenv() |
| `_backup_limpieza_20260727\run_controller.py` 🚩 | Python | 13 | — | 2026-07-27 | def check_admin_privileges(callback): |
| `src\core\state_global\hwnd.py` | Python | 13 | 2026-05-29 | 2026-05-29 | class HwndState(QObject): |
| `init.bat` | Batch | 11 | 2026-05-29 | 2026-05-29 | net session >nul 2>&1 |
| `src\native\CMakeLists.txt` | Texto | 10 | 2026-05-29 | 2026-05-29 | cmake_minimum_required(VERSION 3.14) |
| `src\native\capture.cpp` | .cpp | 10 | 2026-05-29 | 2026-05-29 | include <pybind11/pybind11.h> |
| `_amazonas_log.txt` | Texto | 7 | — | 2026-07-28 | ===== INICIAR AMAZONAS 28/07/2026 17:13:14.41 ===== |
| `src\core\dvr\__init__.py` | Python | 3 | 2026-05-29 | 2026-05-29 | __all__ = ["DVRContext", "DeviceInfo", "ChannelInfo", "DVRStrategy"] |
| `src\native\.bat` | - | 3 | 2026-05-29 | 2026-05-29 | cd build |
| `activate.bat` | Batch | 1 | 2026-05-29 | 2026-05-29 | .\venv\Scripts\activate |
| `src\model\__init__.py` | Python | 1 | 2026-05-29 | 2026-05-29 | - |
| `_backup_limpieza_20260727\api_client.py` 🚩 | Python | 0 | — | 2026-07-27 | - |
| `src\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\core\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\core\network\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\core\state_global\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\gui\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\gui\components\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\gui\components\custon_btn\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\model\windows\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\native\bindings.cpp` | .cpp | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\native\input_controller.cpp` | .cpp | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\native\window_capture.cpp` | .cpp | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\native\window_manager.cpp` | .cpp | 0 | 2026-05-29 | 2026-05-29 | - |
| `src\workers\__init__.py` | Python | 0 | 2026-05-29 | 2026-05-29 | - |

</details>

### SERVER-IA PERIMETRALES  ·  168 archivos  ·  55,569 LOC

<details><summary>Ver los 168 archivos</summary>

| Ruta | Leng. | LOC | Git | FS | Proposito inferido |
|---|---|---:|---|---|---|
| `_backup_simplificacion_20260727\person_amazona_inference.py` 🚩 | Python | 3273 | 2026-07-29 | 2026-07-27 | - |
| `src\analityc\core\person_amazona_inference.py` | Python | 3134 | 2026-07-29 | 2026-07-27 | - |
| `src\analityc\core\analytics\demographics.py` | Python | 2207 | 2026-07-29 | 2026-07-27 | analytics/demographics.py - Clasificacion de genero y edad con PRECISION |
| `src\analityc\core\Perimetrales.pyc` | .pyc | 2170 | — | 2026-07-29 | � |
| `src\analityc\core\Perimetrales.pyc.bak_wa` | .bak_wa | 2170 | 2026-07-29 | 2026-06-13 | � |
| `_backup_simplificacion_20260727\analytics\demographics.py` 🚩 | Python | 1983 | 2026-07-29 | 2026-07-27 | analytics/demographics.py - Clasificacion de genero y edad con PRECISION |
| `src\analityc\core\Hummus.py` | Python | 1939 | 2026-07-29 | 2026-06-23 | - |
| `webapp\app.py` | Python | 1368 | 2026-07-29 | 2026-06-19 | webapp/app.py - Dashboard de rostros reconocidos. |
| `src\analityc\core\Misters.py` | Python | 1333 | 2026-03-23 | 2026-03-03 | - |
| `src\analityc\core\hummus_vlm.pyc` | .pyc | 1332 | — | 2026-06-19 | � |
| `_backup_simplificacion_20260727\app.py` 🚩 | Python | 994 | 2026-07-29 | 2026-07-27 | - |
| `src\analityc\core\car_washed.py` | Python | 983 | 2026-07-29 | 2026-07-25 | - |
| `_backup_simplificacion_20260727\analytics\face_reidentifier.py` 🚩 | Python | 962 | 2026-07-29 | 2026-07-27 | analytics/face_reidentifier.py - Face re-identification para evitar |
| `src\analityc\core\analytics\face_reidentifier.py` | Python | 962 | 2026-07-29 | 2026-06-26 | analytics/face_reidentifier.py - Face re-identification para evitar |
| `src\analityc\core\perimetrales_multicam.pyc` | .pyc | 945 | — | 2026-06-13 | � |
| `_backup_simplificacion_20260727\analytics\retail_analytics.py` 🚩 | Python | 890 | 2026-07-29 | 2026-07-27 | analytics/retail_analytics.py - Orquestador de la analitica de supermercado. |
| `src\app\app.py` | Python | 852 | 2026-07-29 | 2026-07-29 | - |
| `webapp\static\style.css` | CSS | 848 | 2026-06-10 | 2026-05-20 | /* ELDE Dashboard - Tema oscuro moderno */ |
| `src\app\dashboard.py` | Python | 806 | 2026-07-29 | 2026-07-29 | src/app/dashboard.py — Dashboard web de Analitica de Visitantes. |
| `_backup_simplificacion_20260727\render_box.py` 🚩 | Python | 791 | 2026-07-29 | 2026-07-27 | RenderBox original + soporte de drag & drop DVR (RTSP) con detección automática de Hik-Connect e IP. |
| `webapp\static\app.js` | JavaScript | 781 | 2026-06-10 | 2026-05-27 | // ELDE Dashboard - lógica del frontend |
| `_backup_simplificacion_20260727\analytics\config.py` 🚩 | Python | 780 | 2026-07-29 | 2026-07-27 | analytics/config.py - Parametros configurables para analitica retail. |
| `webapp\templates\index.html` | HTML | 663 | 2026-07-29 | 2026-06-19 | <!DOCTYPE html> |
| `src\analityc\core\vlm_verifier.pyc` | .pyc | 658 | — | 2026-06-13 | � |
| `src\app\dashboard_tienda.py` | Python | 603 | 2026-07-29 | 2026-07-29 | src/app/dashboard_tienda.py — Dashboard de TIENDA (marketing y consumo). |
| `src\analityc\core\analytics\config.py` | Python | 585 | 2026-07-29 | 2026-07-28 | analytics/config.py - Parametros configurables para analitica retail. |
| `_backup_simplificacion_20260727\analytics\shelf_interaction.py` 🚩 | Python | 575 | 2026-07-29 | 2026-07-27 | analytics/shelf_interaction.py - Interaccion persona <-> anaquel. |
| `src\analityc\core\analytics\analizador_pendientes.py` | Python | 508 | 2026-07-29 | 2026-07-28 | analizador_pendientes.py - Analisis a posteriori de las capturas. |
| `webapp\report_generator.py` | Python | 485 | 2026-07-29 | 2026-06-18 | Toma la base de datos de FaceReidentifier y produce un PDF con: |
| `src\analityc\core\base_perimeter.py` | Python | 460 | 2026-03-23 | 2026-02-28 | base_perimeter.py - v2 |
| `_backup_simplificacion_20260727\analytics\shopper_journey.py` 🚩 | Python | 454 | 2026-07-29 | 2026-07-27 | analytics/shopper_journey.py - Recorrido de compra y decision por persona. |
| `_backup_simplificacion_20260727\tienda_dashboard.py` 🚩 | Python | 438 | 2026-07-29 | 2026-07-27 | webapp/tienda_dashboard.py - Dashboard INDEPENDIENTE de TIENDA (puerto 5030). |
| `scripts\banco_offline.py` | Python | 428 | 2026-07-29 | 2026-07-27 | banco_offline.py - Banco de pruebas reproducible del modulo demografico. |
| `_backup_simplificacion_20260727\analytics\box_monitor.py` 🚩 | Python | 427 | 2026-07-29 | 2026-07-27 | analytics/box_monitor.py - Cajas de mercancia en el piso del pasillo. |
| `_backup_simplificacion_20260727\analytics\store_layout.py` 🚩 | Python | 421 | 2026-07-29 | 2026-07-27 | analytics/store_layout.py - Planograma de la tienda por camara. |
| `_backup_simplificacion_20260727\analytics\heatmap.py` 🚩 | Python | 414 | 2026-07-29 | 2026-07-27 | analytics/heatmap.py - Mapa de calor de ocupacion/transito de personas. |
| `src\analityc\core\analytics\heatmap.py` | Python | 414 | 2026-07-29 | 2026-06-25 | analytics/heatmap.py - Mapa de calor de ocupacion/transito de personas. |
| `src\analityc\core\analytics\estimador_edad_genero.py` | Python | 381 | 2026-07-29 | 2026-07-27 | estimador_edad_genero.py - Estimador de edad y genero con MiVOLO v2. |
| `vigilante_amazonas\config.py` | Python | 381 | 2026-07-29 | 2026-07-28 | Configuración centralizada de VIGILANTE-AMAZONAS. |
| `_backup_simplificacion_20260727\interactive_imageLabel.py` 🚩 | Python | 372 | 2026-07-29 | 2026-07-27 | class Interactive_imageLabel(QLabel): |
| `src\analityc\core\multimodal_router.py` | Python | 367 | 2026-07-29 | 2026-06-23 | nruta una consulta (imagen + texto) al motor adecuado, bajo el esquema: |
| `src\analityc\core\analytics\mivolo_vendor\mivolo_model.py` | Python | 343 | 2026-07-29 | 2026-07-27 | Code adapted from timm https://github.com/huggingface/pytorch-image-models |
| `vigilante_amazonas\adaptador_websocket.py` | Python | 328 | 2026-07-29 | 2026-07-28 | Adaptador WEBSOCKET de VIGILANTE-AMAZONAS para el servidor ELDE. |
| `scripts\test_demographics_diagnostico.py` | Python | 322 | 2026-07-29 | 2026-07-27 | Script de diagnostico end-to-end del pipeline de genero/edad. |
| `vigilante_amazonas\servicios\rastreador_area.py` | Python | 294 | 2026-07-29 | 2026-07-20 | astreador de PERMANENCIA en el área (contador de tiempo + alertas de |
| `vigilante_amazonas\web\panel.py` | Python | 290 | 2026-07-29 | 2026-07-28 | Panel de VIGILANTE-AMAZONAS (:5333) — detecciones, totales y control del VLM. |
| `AUDITORIA.md` | Markdown | 288 | 2026-07-29 | 2026-07-27 | AUDITORÍA DEL MÓDULO DEMOGRÁFICO — DEMOGRAFIA-AMAZONAS |
| `src\analityc\core\botsort_wrapper.py` | Python | 288 | 2026-01-23 | 2026-01-23 | try: |
| `vigilante_amazonas\servicios\verificador_vlm.py` | Python | 286 | 2026-07-29 | 2026-07-28 | Verificador VLM (Hito 6): Qwen2.5-VL en DEVICE_VLM, SIEMPRE no bloqueante. |
| `_backup_simplificacion_20260727\analytics\aisle_traffic.py` 🚩 | Python | 275 | 2026-07-29 | 2026-07-27 | analytics/aisle_traffic.py - Afluencia y concentracion por pasillo. |
| `_backup_simplificacion_20260727\definir_planograma.py` 🚩 | Python | 271 | 2026-07-29 | 2026-07-27 | tools/definir_planograma.py - Dibuja el planograma de una camara con el raton. |
| `_backup_simplificacion_20260727\analytics\restock_detector.py` 🚩 | Python | 269 | 2026-07-29 | 2026-07-27 | analytics/restock_detector.py - Reposicion de mercancia por empleados. |
| `src\analityc\config\config.py` | Python | 252 | 2026-07-29 | 2026-06-20 | config.py - v2 |
| `src\analityc\core\analytics\agregador_demografico.py` | Python | 251 | 2026-07-29 | 2026-07-27 | agregador_demografico.py - Consolidacion por track (Hito 5). |
| `scripts\verificar_entorno.py` | Python | 249 | 2026-07-29 | 2026-07-27 | verificar_entorno.py - Verificacion del entorno de GPU para DEMOGRAFIA-AMAZONAS. |
| `vigilante_amazonas\web\dashboard.py` | Python | 244 | 2026-07-29 | 2026-07-18 | Dashboard web de VIGILANTE-AMAZONAS (FastAPI, UI 100% en español). |
| `src\analityc\core\analytics\telemetria_demografica.py` | Python | 238 | 2026-07-29 | 2026-07-27 | telemetria_demografica.py - Instrumentacion del modulo demografico (Hito 2). |
| `vigilante_amazonas\verificar_entorno.py` | Python | 232 | 2026-07-29 | 2026-07-17 | Verificación del entorno de VIGILANTE-AMAZONAS. |
| `src\analityc\core\analytics\verificador_vlm.py` | Python | 231 | 2026-07-29 | 2026-07-27 | verificador_vlm.py - Segunda opinion opcional con Qwen2.5-VL (Hito 6). |
| `_backup_simplificacion_20260727\analytics\cart_tracker.py` 🚩 | Python | 230 | 2026-07-29 | 2026-07-27 | analytics/cart_tracker.py - Carritos y cestas de compra. |
| `webapp\deep_analyzer.py` | Python | 221 | 2026-06-10 | 2026-05-27 | Analisis profundo post-hoc de genero/edad sobre una foto guardada. |
| `README_DEMOGRAFIA.md` | Markdown | 220 | 2026-07-29 | 2026-07-28 | DEMOGRAFIA-AMAZONAS |
| `vigilante_amazonas\servicios\motor_reid.py` | Python | 218 | 2026-07-29 | 2026-07-17 | otor de Re-Identificación (Hito 4): decide si una persona detectada es una |
| `vigilante_amazonas\web\base_datos.py` | Python | 217 | 2026-07-29 | 2026-07-17 | Base de datos SQLite de VIGILANTE-AMAZONAS (WAL, thread-safe). |
| `vigilante_amazonas\web\estaticos\app.js` | JavaScript | 211 | 2026-07-29 | 2026-07-18 | /* VIGILANTE-AMAZONAS — lógica del panel (vanilla JS, en español) */ |
| `scripts\benchmark_demografia.py` | Python | 208 | 2026-07-29 | 2026-07-27 | benchmark_demografia.py - Medicion end-to-end del pipeline (Hito 8). |
| `scripts\setup_mivolo.py` | Python | 204 | 2026-06-10 | 2026-05-20 | Descarga y convierte el modelo MiVOLO 2024 a ONNX. |
| `vigilante_amazonas\deteccion\clasificador_vehiculos.py` | Python | 201 | 2026-07-29 | 2026-07-18 | Clasificador FINO de vehículos: carro vs camioneta (pickup/SUV/van) vs camión. |
| `_backup_simplificacion_20260727\analytics\stock_monitor.py` 🚩 | Python | 200 | 2026-07-29 | 2026-07-27 | analytics/stock_monitor.py - Monitoreo de productos en estantes por ROI. |
| `PLAN.md` | Markdown | 195 | 2026-06-10 | 2026-06-09 | PLAN — Demografía multi-ángulo (Hito 0: Auditoría y plan) |
| `_backup_simplificacion_20260727\analytics\staff_gallery.py` 🚩 | Python | 195 | 2026-07-29 | 2026-07-27 | analytics/staff_gallery.py - Personal registrado por FOTO. |
| `demo.py` | Python | 195 | 2026-06-10 | 2026-06-09 | demo.py - Demo CLI de la capa de captura universal (Hito 1). |
| `vigilante_amazonas\captura\fuente_rtsp.py` | Python | 190 | 2026-07-29 | 2026-07-17 | Captura RTSP multihilo con cola descartable por cámara. |
| `requirements.txt` | Texto | 185 | 2026-03-23 | 2026-02-20 | ��a l t g r a p h = = 0 . 1 7 . 4  |
| `_backup_simplificacion_20260727\analytics\seller_efficiency.py` 🚩 | Python | 182 | 2026-07-29 | 2026-07-27 | analytics/seller_efficiency.py - Metricas de eficiencia de vendedores y premio horario. |
| `src\analityc\core\puente_vigilante.py` | Python | 182 | 2026-07-29 | 2026-07-16 | Puente VIGILANTE-AMAZONAS para el modo "Perimetrales" (BasePerimeter). |
| `scripts\evaluar_etiquetado.py` | Python | 177 | 2026-07-29 | 2026-07-27 | evaluar_etiquetado.py - Mide la precision REAL contra etiquetas manuales. |
| `scripts\comparar_vlm.py` | Python | 170 | 2026-07-29 | 2026-07-28 | comparar_vlm.py - Compara los modelos VLM disponibles (3B vs 7B). |
| `tools\eval_demografia.py` | Python | 170 | 2026-06-10 | 2026-06-09 | tools/eval_demografia.py - Evaluacion de exactitud de la demografia. |
| `scripts\setup_modelos.py` | Python | 160 | 2026-06-10 | 2026-06-09 | scripts/setup_modelos.py - Verificacion y guia reproducible de modelos. |
| `vigilante_amazonas\servicios\detector_merodeo.py` | Python | 160 | 2026-07-29 | 2026-07-20 | Detector de MERODEO: el mismo individuo/vehículo que entra y sale del área |
| `tools\benchmark.py` | Python | 158 | 2026-06-10 | 2026-06-09 | tools/benchmark.py - Benchmark de rendimiento del pipeline de demografia. |
| `_backup_simplificacion_20260727\analytics\body_reidentifier.py` 🚩 | Python | 155 | 2026-07-29 | 2026-07-27 | analytics/body_reidentifier.py - Re-ID corporal/de apariencia (OSNet). |
| `src\analityc\core\analytics\body_reidentifier.py` | Python | 155 | 2026-07-29 | 2026-06-26 | analytics/body_reidentifier.py - Re-ID corporal/de apariencia (OSNet). |
| `scripts\convertir_mivolo_onnx.py` | Python | 148 | 2026-07-29 | 2026-07-09 | Convierte el checkpoint MiVOLO ya descargado a ONNX, compatible con timm 1.0.x. |
| `src\analityc\core\class_base.py` | Python | 148 | 2026-01-29 | 2025-12-30 | - |
| `tools\bench_qwen_vlm.py` | Python | 148 | 2026-06-10 | 2026-05-13 | Benchmark Qwen2.5-VL-7B-AWQ en la GPU local. |
| `scripts\test_reidentification.py` | Python | 146 | 2026-06-10 | 2026-05-20 | Test del FaceReidentifier: |
| `vigilante_amazonas\servicios\emisor_alertas.py` | Python | 145 | 2026-07-29 | 2026-07-17 | Emisor de alertas (Hito 7): Socket.IO + persistencia SQLite + snapshots. |
| `scripts\setup_buffalo_l_genderage.py` | Python | 144 | 2026-06-10 | 2026-05-20 | Descarga el modelo genderage.onnx del pack buffalo_l de InsightFace. |
| `scripts\test_agregador.py` | Python | 143 | 2026-07-29 | 2026-07-27 | test_agregador.py - Tests del agregador demografico (Hito 5). |
| `vigilante_amazonas\servicios\emisor_whatsapp.py` | Python | 141 | 2026-07-29 | 2026-07-28 | Emisor de alertas por WhatsApp (bot 'ava') para VIGILANTE-AMAZONAS. |
| `scripts\resumen_telemetria.py` | Python | 137 | 2026-07-29 | 2026-07-27 | resumen_telemetria.py - Vuelca el resumen de la telemetria demografica. |
| `vigilante_amazonas\README.md` | Markdown | 137 | 2026-07-29 | 2026-07-17 | 🛡️ VIGILANTE-AMAZONAS |
| `_backup_simplificacion_20260727\analytics\attendance_tracker.py` 🚩 | Python | 136 | 2026-07-29 | 2026-07-27 | analytics/attendance_tracker.py - Deteccion de atencion vendedor-cliente. |
| `scripts\test_verificador_vlm.py` | Python | 133 | 2026-07-29 | 2026-07-27 | test_verificador_vlm.py - Tests del verificador VLM (Hito 6). |
| `vigilante_amazonas\deteccion\detector.py` | Python | 133 | 2026-07-29 | 2026-07-17 | Detector multiclase YOLO26m con inferencia POR LOTES centralizada. |
| `vigilante_amazonas\deteccion\motor.py` | Python | 133 | 2026-07-29 | 2026-07-18 | Motor central de VIGILANTE-AMAZONAS: captura -> lote YOLO -> ByteTrack -> |
| `MANUAL_INICIO.md` | Markdown | 130 | 2026-06-10 | 2026-06-10 | Manual de inicio — Sistema ELDE (Servidor IA + Cliente Windows) |
| `README.md` | Markdown | 129 | 2026-06-12 | 2026-06-12 | Proyecto: ReID multicámara (Perimetrales) |
| `vigilante_amazonas\servicios\clasificador_seguridad.py` | Python | 129 | 2026-07-29 | 2026-07-17 | Clasificador de PERSONAL DE SEGURIDAD (Hito 3). |
| `tools\multicam_grid_test.py` | Python | 122 | 2026-01-29 | 2026-01-28 | PROJECT_ROOT = os.getcwd() |
| `src\analityc\core\utils\logger.py` | Python | 118 | 2026-07-29 | 2026-07-27 | utils/logger.py - Logging unificado a archivo JSONL para analitica retail. |
| `scripts\preparar_etiquetado.py` | Python | 115 | 2026-07-29 | 2026-07-27 | preparar_etiquetado.py - Prepara el set de validacion con datos REALES. |
| `scripts\test_delete_sync.py` | Python | 109 | 2026-06-10 | 2026-05-20 | Test de sincronizacion: simula el race condition entre webapp y |
| `vigilante_amazonas\servicios\embebedor_rostro.py` | Python | 106 | 2026-07-29 | 2026-07-17 | Embeddings FACIALES: YuNet (detección + 5 landmarks) -> alineación ArcFace -> |
| `tools\multicam_test.py` | Python | 105 | 2026-01-29 | 2026-01-28 | PROJECT_ROOT = os.getcwd() |
| `vigilante_amazonas\servicios\galeria.py` | Python | 99 | 2026-07-29 | 2026-07-17 | Galería de personas de interés: índice de similitud EN MEMORIA (numpy). |
| `vigilante_amazonas\main.py` | Python | 95 | 2026-07-29 | 2026-07-18 | Orquestador de VIGILANTE-AMAZONAS (Hito 8). |
| `src\analityc\core\analytics\mivolo_vendor\cross_bottleneck_attn.py` | Python | 92 | 2026-07-29 | 2026-07-27 | Code based on timm https://github.com/huggingface/pytorch-image-models |
| `scripts\setup_face_embedding.py` | Python | 91 | 2026-06-10 | 2026-05-20 | Descarga el modelo ArcFace w600k_r50.onnx para face re-identification. |
| `src\analityc\core\utils\overlay.py` | Python | 88 | 2026-07-29 | 2026-07-27 | utils/overlay.py - Funciones de dibujo en frame para analitica retail. |
| `webapp\README.md` | Markdown | 85 | 2026-06-10 | 2026-05-20 | Dashboard ELDE — Rostros Reconocidos |
| `_backup_simplificacion_20260727\store_layout\ejemplo.json` 🚩 | JSON | 83 | 2026-07-29 | 2026-07-27 | { |
| `vigilante_amazonas\deteccion\rastreador.py` | Python | 83 | 2026-07-29 | 2026-07-20 | Tracking ByteTrack por cámara con IDs estables (vía supervision). |
| `vigilante_amazonas\web\estaticos\index.html` | HTML | 78 | 2026-07-29 | 2026-07-17 | <!DOCTYPE html> |
| `vigilante_amazonas\web\estaticos\estilos.css` | CSS | 74 | 2026-07-29 | 2026-07-17 | /* VIGILANTE-AMAZONAS — estilos del panel (oscuro, sin frameworks) */ |
| `vigilante_amazonas\servicios\embebedor_vestimenta.py` | Python | 73 | 2026-07-29 | 2026-07-17 | Embeddings de VESTIMENTA (cuerpo completo) con dos backends conmutables por |
| `vigilante_amazonas\servicios\analizador_asincrono.py` | Python | 70 | 2026-07-29 | 2026-07-17 | Analizador ASÍNCRONO: desacopla el bucle de detección del análisis pesado |
| `iniciar_servidor_headless.py` | Python | 69 | 2026-07-29 | 2026-07-29 | Arranca el servidor de inferencia (websocket :9000) SIN interfaz gráfica. |
| `vigilante_amazonas\servicios\nucleo_servicios.py` | Python | 68 | 2026-07-29 | 2026-07-17 | Núcleo COMPARTIDO de servicios (singleton por proceso). |
| `vigilante_amazonas\servicios\clip_compartido.py` | Python | 67 | 2026-07-29 | 2026-07-17 | CLIP compartido (laion/CLIP-ViT-B-32) para: |
| `vigilante_amazonas\utilidades\registro.py` | Python | 67 | 2026-07-29 | 2026-07-17 | Logging estructurado de VIGILANTE-AMAZONAS: JSON por línea con rotación. |
| `vigilante_amazonas\deteccion\mapeo_clases.py` | Python | 65 | 2026-07-29 | 2026-07-17 | Mapeo de clases COCO -> clases propias de VIGILANTE-AMAZONAS. |
| `_backup_simplificacion_20260727\analytics\people_counter.py` 🚩 | Python | 62 | 2026-07-29 | 2026-07-27 | analytics/people_counter.py - Conteo unico de personas. |
| `src\analityc\core\analytics\people_counter.py` | Python | 62 | 2026-06-10 | 2026-04-21 | analytics/people_counter.py - Conteo unico de personas. |
| `src\analityc\core\hardware_available.py` | Python | 62 | 2026-01-29 | 2026-01-26 | class Device_hardware: |
| `src\gui\window_main.py` | Python | 59 | 2026-01-29 | 2026-01-28 | def _get_device_default() -> str: |
| `vigilante_amazonas\ejemplo_cliente\consumo_alertas.py` | Python | 53 | 2026-07-29 | 2026-07-17 | EJEMPLO de consumo de alertas de VIGILANTE-AMAZONAS para perimetrales-view |
| `vigilante_amazonas\web\lanzador.py` | Python | 53 | 2026-07-29 | 2026-07-28 | Arranque compartido del panel de VIGILANTE (:5333). |
| `config\camera_profiles\README.md` | Markdown | 44 | 2026-07-29 | 2026-07-27 | Perfiles demográficos por cámara |
| `src\app\server.py` | Python | 44 | 2025-11-13 | 2025-11-15 | class Server_services: |
| `.gitignore` | - | 42 | 2026-07-29 | 2026-07-29 | ===== Python ===== |
| `vigilante_amazonas\requirements.txt` | Texto | 42 | 2026-07-29 | 2026-07-17 | ============================================================================= |
| `SETUP_SERVIDOR.bat` | Batch | 37 | 2026-06-10 | 2026-07-25 | ============================================================ |
| `EQUIPO.md` | Markdown | 35 | 2026-06-10 | 2026-05-29 | Guía de trabajo en equipo — SERVER-IA |
| `src\analityc\config\vlm_prompts.json` | JSON | 35 | 2026-06-10 | 2026-06-02 | { |
| `_backup_simplificacion_20260727\analytics\__init__.py` 🚩 | Python | 32 | 2026-07-29 | 2026-07-27 | analytics - Modulos de analitica retail en tiempo real. |
| `vigilante_amazonas\ecosystem.config.js` | JavaScript | 30 | 2026-07-29 | 2026-07-17 | // PM2 en Windows — VIGILANTE-AMAZONAS |
| `tools\send_test_ws.py` | Python | 28 | 2026-01-29 | 2026-01-24 | WS_URL = 'ws://127.0.0.1:9000/ws/PerimetralesMultiCam' |
| `_servidor_err.txt` | Texto | 27 | — | 2026-07-29 | C:\Users\Sistema-1\AppData\Roaming\Python\Python312\site-packages\torchreid\reid\metrics\rank.py:11: UserWarni |
| `config\camera_profiles\cam12.json` | JSON | 24 | 2026-07-29 | 2026-07-27 | { |
| `src\libs\files_save.py` | Python | 22 | 2025-12-28 | 2025-12-07 | project_root = Path(__file__).resolve().parents[2]  # sube hasta la raíz del proyecto |
| `main.py` | Python | 21 | 2026-01-29 | 2026-01-16 | SERVER |
| `src\gui\q_application.py` | Python | 20 | 2025-11-13 | 2025-11-15 | class AppSingletonGui(QObject): |
| `_servidor_log.txt` | Texto | 17 | — | 2026-07-29 | 17:20:05 \| INFO     \| vigilante.web.lanzador \| panel de VIGILANTE en http://localhost:5333 |
| `src\analityc\core\analytics\mivolo_vendor\__init__.py` | Python | 17 | 2026-07-29 | 2026-07-27 | mivolo_vendor - Copia local del codigo de arquitectura de MiVOLO. |
| `src\analityc\core\analytics\__init__.py` | Python | 16 | 2026-07-29 | 2026-07-27 | analytics - Deteccion de personas con genero y rango de edad. |
| `vigilante_amazonas\__init__.py` | Python | 15 | 2026-07-29 | 2026-07-17 | VIGILANTE-AMAZONAS — Sistema de videovigilancia inteligente perimetral. |
| `trackers\botsort_reid.yaml` | YAML | 13 | 2026-03-23 | 2026-02-02 | BoT-SORT con ReID para mayor estabilidad de IDs |
| `config\pasillos.json` | JSON | 9 | 2026-07-29 | 2026-07-29 | { |
| `vigilante_amazonas\servicios\__init__.py` | Python | 9 | 2026-07-29 | 2026-07-17 | Servicios de VIGILANTE-AMAZONAS: clasificador de seguridad, Re-ID, VLM y |
| `.env` | - | 4 | — | 2025-12-28 | api_keyroboflow=«API KEY ENMASCARADA» |
| `vigilante_amazonas\captura\__init__.py` | Python | 1 | 2026-07-29 | 2026-07-17 | Captura de video multihilo (RTSP o archivo) con colas descartables. |
| `vigilante_amazonas\db\vigilante.db-shm` | .db-shm | 1 | 2026-07-29 | 2026-07-29 | �-                                    85��	�-                                    85��	        ������ |
| `vigilante_amazonas\deteccion\__init__.py` | Python | 1 | 2026-07-29 | 2026-07-17 | Detección multiclase (YOLO26 TensorRT/PT) + tracking (ByteTrack). |
| `vigilante_amazonas\utilidades\__init__.py` | Python | 1 | 2026-07-29 | 2026-07-17 | Utilidades transversales de VIGILANTE-AMAZONAS (logging, helpers). |
| `vigilante_amazonas\vlm_activo.txt` | Texto | 1 | 2026-07-29 | 2026-07-29 | 0 |
| `vigilante_amazonas\web\__init__.py` | Python | 1 | 2026-07-29 | 2026-07-17 | Dashboard web (FastAPI) y base de datos SQLite de VIGILANTE-AMAZONAS. |
| `src\__init__.py` | Python | 0 | 2025-11-13 | 2025-11-15 | - |
| `src\analityc\__init__.py` | Python | 0 | 2025-11-29 | 2025-11-21 | - |
| `src\analityc\config\__init__.py` | Python | 0 | 2025-11-29 | 2025-11-21 | - |
| `src\analityc\core\__init__.py` | Python | 0 | 2025-11-29 | 2025-11-22 | - |
| `src\analityc\core\utils\__init__.py` | Python | 0 | 2026-06-10 | 2026-04-21 | - |
| `src\app\__init__.py` | Python | 0 | 2025-11-13 | 2025-11-15 | - |
| `src\app\core\__init__.py` | Python | 0 | 2025-11-13 | 2025-11-15 | - |
| `src\gui\__init__.py` | Python | 0 | 2025-11-13 | 2025-11-15 | - |
| `vigilante_amazonas\db\vigilante.db-wal` | .db-wal | 0 | 2026-07-29 | 2026-07-28 | - |

</details>

🚩 = dentro de una carpeta `_backup_*` (respaldo de limpiezas previas).

## 7. Hallazgos del HITO 0

1. **La duplicacion es cuadruple, no triple.** Los 4 clientes salen del mismo cliente base: comparten hasta el `README.md`. ~17.700 LOC repetidos, casi la mitad del codigo cliente.
2. **`selector.py` documenta mal a Amazonas View** (ver seccion 3).
3. **El `requirements.txt` del servidor es un `pip freeze`** (92 paquetes) en UTF-16, y le faltan dependencias criticas. Ver `HALLAZGOS.md` H-06.
4. **`iniciar_servidor_headless.py`, el punto de entrada real del servidor, no estaba versionado** hasta el commit de respaldo de hoy.
5. **`hik-connect/` son 3954 archivos de SDK de terceros** (~200 MB de .exe/.dll/.pdb): el 97% del arbol de la raiz, sin codigo propio salvo una nota.
6. **`modelos NVIDIA/` ocupa 50 GB** en la raiz del proyecto.
7. **No hay base de datos.** Toda la persistencia es en archivos sueltos (`.pkl`, JSON, PNG), lo que condiciona cualquier API de historico para los dashboards del HITO 9.
8. **Carpetas `_backup_*` conviviendo con el codigo vivo** en el servidor y en perimetrales-view: restos de limpiezas anteriores.
