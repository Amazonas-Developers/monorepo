# HITO 1 — Analisis de uso real y codigo muerto

> **Solo lectura**: ningun archivo de los proyectos fue modificado. Generado el 2026-07-30. Detalle completo por elemento en `01_CODIGO_MUERTO.csv`.

## 1. Metodo y sus limites

- **Grafo de imports** construido con AST desde el punto de entrada de cada proyecto, resolviendo imports absolutos y relativos. Se recorren tambien los imports dentro de funciones (carga perezosa).
- Un modulo es **alcanzable** si hay un camino de imports desde la entrada. Al importar `a.b.c` se marcan tambien los `__init__` de `a` y `a.b`, porque Python los ejecuta.
- **Referencias** = numero de *otros* modulos del proyecto que citan el nombre. Es un conteo por token: puede sobreestimar con nombres genericos, nunca subestimar. Por eso ningun elemento pasa a `MUERTO` solo por tener pocas referencias: hace falta ademas ser inalcanzable.
- **Todo lo que huela a dinamico va a `DUDOSO`**, jamas a `MUERTO`.

## 2. Alcanzabilidad por proyecto

| Proyecto | Modulos | Alcanzables | No alcanzables | % muerto potencial |
|---|---:|---:|---:|---:|
| `tienda_view` | 61 | 42 | 19 | 31% |
| `perimetrales-view` | 67 | 49 | 18 | 27% |
| `windows_managers_view` | 59 | 40 | 19 | 32% |
| `Amazonas View` | 53 | 40 | 13 | 25% |
| `SERVER-IA PERIMETRALES` | 107 | 63 | 44 | 41% |

## 3. Clasificacion (modulos + simbolos publicos)

| Proyecto | ACTIVO | DUDOSO | MUERTO | Total |
|---|---:|---:|---:|---:|
| `tienda_view` | 80 | 43 | 10 | 133 |
| `perimetrales-view` | 99 | 58 | 1 | 158 |
| `windows_managers_view` | 76 | 43 | 10 | 129 |
| `Amazonas View` | 75 | 43 | 2 | 120 |
| `SERVER-IA PERIMETRALES` | 139 | 229 | 22 | 390 |
| **TOTAL** | **469** | **416** | **45** | **930** |

### 3.1 Modulos clasificados MUERTO

| Proyecto | Ruta | Justificacion |
|---|---|---|
| tienda_view | `src\core\api_client.py` | NO alcanzable y 0 modulos lo importan |
| tienda_view | `src\core\locking_windows.py` | NO alcanzable y 0 modulos lo importan |
| tienda_view | `src\core\network\api_client.py` | NO alcanzable y 0 modulos lo importan |
| tienda_view | `src\core\run_controller.py` | NO alcanzable y 0 modulos lo importan |
| tienda_view | `src\core\window_capture.py` | NO alcanzable y 0 modulos lo importan |
| tienda_view | `src\utils\files\print_png.py` | NO alcanzable y 0 modulos lo importan |
| perimetrales-view | `generar_icono_persona.py` | NO alcanzable y 0 modulos lo importan |
| windows_managers_view | `src\core\api_client.py` | NO alcanzable y 0 modulos lo importan |
| windows_managers_view | `src\core\locking_windows.py` | NO alcanzable y 0 modulos lo importan |
| windows_managers_view | `src\core\network\api_client.py` | NO alcanzable y 0 modulos lo importan |
| windows_managers_view | `src\core\run_controller.py` | NO alcanzable y 0 modulos lo importan |
| windows_managers_view | `src\core\window_capture.py` | NO alcanzable y 0 modulos lo importan |
| windows_managers_view | `src\utils\files\print_png.py` | NO alcanzable y 0 modulos lo importan |
| Amazonas View | `src\gui\components\add_device_dialog.py` | NO alcanzable y 0 modulos lo importan |
| SERVER-IA PERIMETRALES | `src\analityc\core\class_base.py` | NO alcanzable y 0 modulos lo importan |
| SERVER-IA PERIMETRALES | `src\libs\files_save.py` | NO alcanzable y 0 modulos lo importan |

> Solo **16 modulos** son candidatos a cuarentena. El resto de lo inalcanzable quedo en `DUDOSO` (scripts con entrada propia, nombres citados en cadenas, o modulos importados por otros modulos inalcanzables).

### 3.2 Validacion independiente del veredicto MUERTO

Cinco de los modulos marcados `MUERTO` tienen una comprobacion empirica que no
depende del analisis estatico:

| Modulo | tienda_view | windows_managers_view | perimetrales-view | Amazonas View |
|---|---|---|---|---|
| `src/core/api_client.py` | existe | existe | **borrado** | **borrado** |
| `src/core/network/api_client.py` | existe | existe | **borrado** | **borrado** |
| `src/core/locking_windows.py` | existe | existe | **borrado** | **borrado** |
| `src/core/run_controller.py` | existe | existe | **borrado** | **borrado** |
| `src/core/window_capture.py` | existe | existe | **borrado** | **borrado** |

Los cuatro clientes salen del mismo codigo base. En dos de ellos **ya se
eliminaron a mano** esos archivos y siguen arrancando y funcionando; en los
otros dos siguen ahi, y el analisis los senala como inalcanzables sin que se
supiera nada de lo anterior. Las dos vias coinciden, asi que el veredicto se
sostiene sin depender del grafo de imports.

## 4. Contrato cliente ↔ servidor

El servidor expone **62 rutas HTTP** mas el websocket `/ws/{type_inference}`.

| Consumidor | Nº de rutas |
|---|---:|
| Clientes de escritorio | 4 |
| JS del propio dashboard del servidor | 53 |
| **Nadie (huerfanas)** | **5** |

### 4.1 Rutas huerfanas — expuestas y sin ningun consumidor

| Ruta | Verbo | Archivo |
|---|---|---|
| `/` | GET | `src\app\app.py` |
| `/debug/{camara}` | GET | `vigilante_amazonas\web\dashboard.py` |
| `/galeria/{persona_id}/{archivo}` | GET | `vigilante_amazonas\web\dashboard.py` |
| `/img/{archivo}` | GET | `vigilante_amazonas\web\panel.py` |
| `/snapshots/{archivo}` | GET | `vigilante_amazonas\web\dashboard.py` |

### 4.2 Rutas que los clientes llaman y el servidor no expone

Todas resultaron ser **APIs de nubes externas** (Hik-Connect `hccgw` y EZVIZ `lapp`), no endpoints propios. No son enlaces rotos.

| Ruta externa | La usan |
|---|---|
| `/api/hccgw/platform/v1/streamtoken/get` | perimetrales-view, tienda_view, windows_managers_view |
| `/api/hccgw/platform/v1/token/get` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view |
| `/api/hccgw/resource/v1/areas/cameras/get` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view |
| `/api/hccgw/resource/v1/devices/get` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view |
| `/api/hccgw/video/v1/live/address/get` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view |
| `/api/lapp/camera/list` | perimetrales-view |
| `/api/lapp/device/list` | perimetrales-view |
| `/api/lapp/live/address/get` | perimetrales-view |
| `/api/lapp/token/get` | perimetrales-view |

> `/ws` aparecia como rota por analisis textual, pero es un falso positivo: el cliente concatena `{url}/{type_inference}` (`socket_client.py:50`) y encaja con `@app.websocket("/ws/{type_inference}")` (`src/app/app.py:615`).

### 4.3 Claves del mensaje websocket que lee el servidor

El servidor interpreta **32 claves** del JSON entrante. No hay esquema ni validacion: se leen con `data.get(...)` sueltos, que es justo lo que el HITO 3 debe formalizar.

```
  areas, camera_angle, camera_id, camera_name
  compra, confianza, delivery_zone_activate, delivery_zone_coordinates
  descripcion, detections_in_roi, door_direction, door_direction_activate
  door_roi_activate, door_roi_coordinates, draw_server, enable_vlm
  entrega_bandeja, enviar_whatsapp, heatmap_activate, order_zone_activate
  order_zone_coordinates, pay_roi, pay_roi_activate, processing_time
  roi_activate, roi_coordinates, track_classes, valid_tracks_in_roi
  vehicle_events, washing_now, withdraw_roi, withdraw_roi_activate
```

## 5. Dependencias

| Proyecto | Declaradas | Usadas (3os) | Declaradas sin usar | Usadas sin declarar |
|---|---:|---:|---:|---|
| `tienda_view` | 104 | 16 | 91 | models, supervision, win32ui |
| `perimetrales-view` | 105 | 16 | 92 | models, websocket, win32ui |
| `windows_managers_view` | 104 | 16 | 91 | models, supervision, win32ui |
| `Amazonas View` | 104 | 14 | 92 | models, win32ui |
| `SERVER-IA PERIMETRALES` | 93 | 32 | 93 | cpuinfo, deep-analyzer, fastapi, httpx, matplotlib, mivolo |

> El conteo de "declaradas sin usar" es **orientativo**: un paquete puede ser dependencia transitiva legitima. El dato solido es que el `requirements.txt` del servidor tiene 1796 lineas frente a ~40 paquetes realmente importados: es un `pip freeze`, no una lista de dependencias.

## 6. Invocaciones dinamicas (riesgo de falso "muerto")

| Proyecto | Nº | Patrones mas frecuentes |
|---|---:|---|
| `tienda_view` | 48 | hasattr ×16, getattr ×16, exec ×14, setattr ×2 |
| `perimetrales-view` | 57 | getattr ×25, hasattr ×16, exec ×15, setattr ×1 |
| `windows_managers_view` | 39 | hasattr ×15, exec ×13, getattr ×11 |
| `Amazonas View` | 44 | getattr ×20, hasattr ×14, exec ×10 |
| `SERVER-IA PERIMETRALES` | 190 | getattr ×124, hasattr ×53, eval ×9, setattr ×3 |

Ejemplos reales (los mas peligrosos para el analisis estatico):

| Proyecto | Ubicacion | Codigo |
|---|---|---|
| tienda_view | `src\main.py:151` | `return app.exec()` |
| tienda_view | `src\core\app_singleton.py:89` | `def exec(cls):` |
| tienda_view | `src\core\app_singleton.py:92` | `return cls._app.exec()` |
| tienda_view | `src\core\network\jarvis_api.py:208` | `loop.exec()` |
| tienda_view | `src\gui\windows_main.py:295` | `dlg.exec()` |
| tienda_view | `src\gui\components\box_image.py:101` | `drag.exec(Qt.CopyAction)` |
| tienda_view | `src\gui\components\channel_row.py:84` | `drag.exec(Qt.CopyAction | Qt.MoveAction)` |
| tienda_view | `src\gui\components\device_list.py:97` | `if dialog.exec() == dialog.Accepted:` |
| tienda_view | `src\gui\components\modal_msm.py:213` | `details_dialog.exec()` |
| tienda_view | `src\gui\components\planogram_editor.py:414` | `if dlg.exec() != QDialog.Accepted:` |
| tienda_view | `src\gui\components\render_box\render_box.py:579` | `msg.exec()` |
| tienda_view | `src\gui\components\render_box\render_box.py:1604` | `menu.exec(btn_pos)` |
| tienda_view | `src\gui\components\sidebar\dvr_tree.py:85` | `drag.exec(Qt.CopyAction)` |
| tienda_view | `test\test.py:20` | `sys.exit(app.exec())` |
| perimetrales-view | `test_jarvis_conexion.py:71` | `return app.exec()` |
| perimetrales-view | `test_selector_establecimiento.py:61` | `loop.exec()` |
| perimetrales-view | `src\main.py:214` | `return app.exec()` |
| perimetrales-view | `src\core\app_singleton.py:89` | `def exec(cls):` |
| perimetrales-view | `src\core\app_singleton.py:92` | `return cls._app.exec()` |
| perimetrales-view | `src\core\network\jarvis_api.py:250` | `loop.exec()` |
| perimetrales-view | `src\gui\windows_main.py:322` | `dlg.exec()` |
| perimetrales-view | `src\gui\components\box_image.py:101` | `drag.exec(Qt.CopyAction)` |
| perimetrales-view | `src\gui\components\channel_row.py:84` | `drag.exec(Qt.CopyAction | Qt.MoveAction)` |
| perimetrales-view | `src\gui\components\device_panel.py:560` | `if not dlg.exec() or dlg.seleccionado is None:` |
| perimetrales-view | `src\gui\components\discovery_dialog.py:11` | `if dlg.exec() and dlg.seleccionado:` |
| perimetrales-view | `src\gui\components\render_box\render_box.py:533` | `msg.exec()` |
| perimetrales-view | `src\gui\components\render_box\render_box.py:865` | `menu.exec(btn_pos)` |
| perimetrales-view | `src\gui\components\sidebar\dvr_tree.py:85` | `drag.exec(Qt.CopyAction)` |
| perimetrales-view | `test\test.py:20` | `sys.exit(app.exec())` |
| windows_managers_view | `src\main.py:144` | `return app.exec()` |
| windows_managers_view | `src\core\app_singleton.py:89` | `def exec(cls):` |
| windows_managers_view | `src\core\app_singleton.py:92` | `return cls._app.exec()` |
| windows_managers_view | `src\core\network\jarvis_api.py:208` | `loop.exec()` |
| windows_managers_view | `src\gui\windows_main.py:295` | `dlg.exec()` |
| windows_managers_view | `src\gui\components\box_image.py:101` | `drag.exec(Qt.CopyAction)` |
| windows_managers_view | `src\gui\components\channel_row.py:84` | `drag.exec(Qt.CopyAction | Qt.MoveAction)` |
| windows_managers_view | `src\gui\components\device_list.py:97` | `if dialog.exec() == dialog.Accepted:` |
| windows_managers_view | `src\gui\components\modal_msm.py:213` | `details_dialog.exec()` |
| windows_managers_view | `src\gui\components\render_box\render_box.py:521` | `msg.exec()` |
| windows_managers_view | `src\gui\components\render_box\render_box.py:1016` | `menu.exec(btn_pos)` |
| windows_managers_view | `src\gui\components\sidebar\dvr_tree.py:85` | `drag.exec(Qt.CopyAction)` |
| windows_managers_view | `test\test.py:20` | `sys.exit(app.exec())` |
| Amazonas View | `src\main.py:130` | `return app.exec()` |
| Amazonas View | `src\core\app_singleton.py:89` | `def exec(cls):` |
| Amazonas View | `src\core\app_singleton.py:92` | `return cls._app.exec()` |
| Amazonas View | `src\core\network\jarvis_api.py:208` | `loop.exec()` |
| Amazonas View | `src\gui\windows_main.py:330` | `dlg.exec()` |
| Amazonas View | `src\gui\components\box_image.py:101` | `drag.exec(Qt.CopyAction)` |
| Amazonas View | `src\gui\components\channel_row.py:84` | `drag.exec(Qt.CopyAction | Qt.MoveAction)` |
| Amazonas View | `src\gui\components\sidebar\capturas_sidebar.py:440` | `if primera.exec() != QMessageBox.Yes:` |
| Amazonas View | `src\gui\components\sidebar\dvr_tree.py:72` | `drag.exec(Qt.CopyAction)` |
| Amazonas View | `test\test.py:20` | `sys.exit(app.exec())` |
| SERVER-IA PERIMETRALES | `main.py:27` | `app.exec()` |
| SERVER-IA PERIMETRALES | `scripts\convertir_mivolo_onnx.py:82` | `model.eval()` |
| SERVER-IA PERIMETRALES | `scripts\setup_mivolo.py:150` | `model.eval()` |
| SERVER-IA PERIMETRALES | `src\analityc\core\botsort_wrapper.py:82` | `net = net.to(self.reid_device).eval()` |
| SERVER-IA PERIMETRALES | `src\analityc\core\multimodal_router.py:150` | `device_map=dev).eval()` |
| SERVER-IA PERIMETRALES | `src\analityc\core\multimodal_router.py:153` | `mid, dtype=torch.bfloat16).to(dev).eval()` |
| SERVER-IA PERIMETRALES | `src\analityc\core\analytics\estimador_edad_genero.py:293` | `modelo.eval().to(dispositivo)` |
| SERVER-IA PERIMETRALES | `tools\bench_qwen_vlm.py:83` | `model.eval()` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\servicios\clip_compartido.py:44` | `self._modelo = modelo.to(self.dispositivo).eval()` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\servicios\verificador_vlm.py:233` | `local_files_only=True).eval()` |

## 7. Codigo copiado entre clientes (candidato a nucleo compartido)

**34 archivos identicos** byte a byte en 2 o mas clientes. Son el nucleo compartido evidente del HITO 4:

| Archivo | Clientes que lo repiten | Nº |
|---|---|---:|
| `src\core\app_singleton.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `src\core\capture_exaple.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `src\core\network\socket_client.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `src\core\window_controller.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `src\gui\components\SplashScreen.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `src\gui\components\channel_row.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `src\gui\components\custon_btn\btn_footer.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `test\test.py` | Amazonas View, perimetrales-view, tienda_view, windows_managers_view | 4 |
| `get_and_test.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `get_url.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\core\dvr\context.py` | Amazonas View, tienda_view, windows_managers_view | 3 |
| `src\core\dvr\dahua_http.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\core\dvr\dahua_sdk.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\core\dvr\hikvision_http.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\core\dvr\hikvision_sdk.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\core\network\jarvis_api.py` | Amazonas View, tienda_view, windows_managers_view | 3 |
| `src\core\window_global.py` | Amazonas View, perimetrales-view, windows_managers_view | 3 |
| `src\core\windows_detector.py` | Amazonas View, perimetrales-view, windows_managers_view | 3 |
| `src\gui\components\add_device_dialog.py` | Amazonas View, tienda_view, windows_managers_view | 3 |
| `src\gui\components\box_image.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\gui\components\sidebar\sidebar_dock.py` | Amazonas View, perimetrales-view, tienda_view | 3 |
| `src\gui\components\title_bar\window_bar.py` | Amazonas View, perimetrales-view, windows_managers_view | 3 |
| `src\model\settings_model.py` | perimetrales-view, tienda_view, windows_managers_view | 3 |
| `src\core\dvr\hikconnect_channel_encoder.py` | perimetrales-view, tienda_view | 2 |
| `src\core\locking_windows.py` | tienda_view, windows_managers_view | 2 |
| `src\core\window_capture.py` | tienda_view, windows_managers_view | 2 |
| `src\gui\components\device_list.py` | tienda_view, windows_managers_view | 2 |
| `src\gui\components\modal_msm.py` | tienda_view, windows_managers_view | 2 |
| `src\gui\components\render_box\sv_overlay.py` | perimetrales-view, tienda_view | 2 |
| `src\gui\components\sidebar\dvr_tree.py` | perimetrales-view, tienda_view | 2 |

## 8. Configuracion dispersa (hardcode)

| Proyecto | Total | IP | URL | Puerto | Ruta absoluta |
|---|---:|---:|---:|---:|---:|
| `tienda_view` | 28 | 7 | 19 | 2 | 0 |
| `perimetrales-view` | 54 | 14 | 37 | 2 | 1 |
| `windows_managers_view` | 26 | 5 | 19 | 2 | 0 |
| `Amazonas View` | 29 | 7 | 20 | 2 | 0 |
| `SERVER-IA PERIMETRALES` | 81 | 16 | 34 | 2 | 29 |

### 8.1 Los mas criticos: IP y puerto del servidor en el codigo

| Proyecto | Ubicacion | Valor |
|---|---|---|
| tienda_view | `src\core\dvr\context.py:40` | `192.168.1.64` |
| tienda_view | `src\core\dvr\context.py:41` | `192.168.1.64` |
| tienda_view | `src\gui\windows_main.py:167` | `72.68.60.171` |
| tienda_view | `src\gui\components\device_panel.py:166` | `192.168.1.64` |
| tienda_view | `src\gui\components\render_box\render_box.py:870` | `127.0.0.1` |
| tienda_view | `src\gui\components\title_bar\window_bar.py:92` | `72.68.60.171` |
| tienda_view | `src\gui\components\title_bar\window_bar.py:96` | `127.0.0.1` |
| perimetrales-view | `test_alerta_con_nombre.py:46` | `127.0.0.1` |
| perimetrales-view | `src\core\dashboard_url.py:8` | `72.68.60.171` |
| perimetrales-view | `src\core\dashboard_url.py:30` | `127.0.0.1` |
| perimetrales-view | `src\core\dashboard_url.py:32` | `127.0.0.1` |
| perimetrales-view | `src\core\dvr\context.py:41` | `192.168.1.64` |
| perimetrales-view | `src\core\dvr\context.py:42` | `192.168.1.64` |
| perimetrales-view | `src\core\dvr\discovery.py:8` | `239.255.255.250` |
| perimetrales-view | `src\core\dvr\discovery.py:11` | `239.255.255.250` |
| perimetrales-view | `src\core\dvr\discovery.py:35` | `239.255.255.250` |
| perimetrales-view | `src\core\dvr\discovery.py:138` | `8.8.8.8` |
| perimetrales-view | `src\core\dvr\discovery.py:147` | `192.168.1.37` |
| perimetrales-view | `src\core\dvr\discovery.py:197` | `255.255.255.255` |
| perimetrales-view | `src\gui\windows_main.py:172` | `72.68.60.171` |
| perimetrales-view | `src\gui\components\device_panel.py:197` | `192.168.1.64` |
| windows_managers_view | `src\core\dvr\context.py:40` | `192.168.1.64` |
| windows_managers_view | `src\core\dvr\context.py:41` | `192.168.1.64` |
| windows_managers_view | `src\gui\windows_main.py:167` | `72.68.60.171` |
| windows_managers_view | `src\gui\components\device_panel.py:166` | `192.168.1.64` |
| windows_managers_view | `src\gui\components\render_box\render_box.py:726` | `127.0.0.1` |
| Amazonas View | `src\core\dvr\context.py:40` | `192.168.1.64` |
| Amazonas View | `src\core\dvr\context.py:41` | `192.168.1.64` |
| Amazonas View | `src\gui\windows_main.py:169` | `72.68.60.171` |
| Amazonas View | `src\gui\components\captures_panel.py:280` | `127.0.0.1` |
| Amazonas View | `src\gui\components\captures_panel.py:479` | `127.0.0.1` |
| Amazonas View | `src\gui\components\device_panel.py:170` | `192.168.1.64` |
| Amazonas View | `src\gui\components\sidebar\capturas_sidebar.py:473` | `127.0.0.1` |
| SERVER-IA PERIMETRALES | `demo.py:14` | `192.168.1.10` |
| SERVER-IA PERIMETRALES | `iniciar_servidor_headless.py:81` | `0.0.0.0` |
| SERVER-IA PERIMETRALES | `iniciar_servidor_headless.py:83` | `0.0.0.0` |
| SERVER-IA PERIMETRALES | `src\analityc\config\config.py:193` | `0.0.0.0` |
| SERVER-IA PERIMETRALES | `src\analityc\core\car_washed.py:771` | `72.68.60.254` |
| SERVER-IA PERIMETRALES | `src\analityc\core\person_amazona_inference.py:1141` | `72.68.60.254` |
| SERVER-IA PERIMETRALES | `src\app\dashboard_tienda.py:310` | `127.0.0.1` |
| SERVER-IA PERIMETRALES | `src\app\dashboard_tienda.py:333` | `0.0.0.0` |
| SERVER-IA PERIMETRALES | `src\app\server.py:17` | `0.0.0.0` |
| SERVER-IA PERIMETRALES | `tools\send_test_ws.py:4` | `127.0.0.1` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\config.py:220` | `0.0.0.0` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\config.py:236` | `72.68.60.254` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\ejemplo_cliente\consumo_alertas.py:26` | `127.0.0.1` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\ejemplo_cliente\consumo_alertas.py:38` | `127.0.0.1` |
| SERVER-IA PERIMETRALES | `vigilante_amazonas\web\lanzador.py:30` | `127.0.0.1` |
| SERVER-IA PERIMETRALES | `webapp\app.py:1559` | `0.0.0.0` |

## 9. Conclusiones para los hitos siguientes

1. **Codigo muerto real: poco.** Solo 16 modulos son candidatos firmes a cuarentena. El grueso de lo inalcanzable son scripts y herramientas con entrada propia, que **no** deben borrarse.
2. **El problema no es el codigo muerto, es la duplicacion.** 34 archivos identicos repetidos entre clientes.
3. **No existe contrato.** El servidor lee 32 claves sueltas del websocket sin validacion alguna. Es el mayor riesgo del ecosistema y lo que justifica el HITO 3.
4. **La IP del servidor esta escrita en el codigo de los 4 clientes**, no solo en configuracion.
5. **Las rutas huerfanas son pocas (5)**, pero 53 de 62 solo las usa el JS del propio dashboard: al rehacer los dashboards en el HITO 9 hay que decidir cuales sobreviven.
