# HITO 5 — Cliente de tienda

> Primer cliente refactorizado sobre el núcleo y el contrato.
> Generado el 2026-07-30.

---

## 1. Cifras antes y después

| | Antes | Después | |
|---|---:|---:|---|
| Archivos `.py` | 60 | 54 | −6 |
| LOC | 4.349 | 2.913 | **−1.436 (−33%)** |
| `render_box.py` | 1.914 | 1.506 | −408 |
| Hardcode de red | 3 sitios | **0** | |
| Módulos muertos | 8 | 0 | a `_legacy/` |

El cliente ya venía de 9.461 LOC en el HITO 0: entre la extracción al núcleo y
esta limpieza, **queda menos de un tercio del código original**, y lo que queda
es lo específico de tienda.

## 2. Cuarentena: 1.088 LOC retiradas

En `_legacy/tienda_view/`, con su `README.md` explicando el motivo de cada
archivo. **No se borra**: eso es el HITO 10, tras reverificar.

| Archivo | LOC | Evidencia |
|---|---:|---|
| `gui/components/planogram_editor.py` | 454 | Guarda en `/retail/layout`, endpoint que el servidor ya no expone |
| `gui/components/retail_panel.py` | 444 | Se alimenta de `metadata['retail']`, que el servidor dejó de emitir |
| `core/window_capture.py` | 90 | HITO 1: inalcanzable, 0 referencias |
| `core/locking_windows.py` | 62 | HITO 1: inalcanzable, 0 referencias |
| `utils/files/print_png.py` | 25 | HITO 1: inalcanzable, 0 referencias |
| `core/run_controller.py` | 13 | HITO 1: inalcanzable, 0 referencias |
| `core/api_client.py`, `core/network/api_client.py` | 0 | Archivos vacíos |

Los cinco últimos ya habían sido **borrados a mano** en `perimetrales-view` y
`Amazonas View`, que funcionan sin ellos: la evidencia estática y la empírica
coinciden.

## 3. La analítica de retail, retirada de `render_box`

Fueron **15 métodos completos y 408 líneas**: el menú «Tienda», el editor de
planograma, el panel de analítica, el pintado de zonas, la calibración de
anaqueles y los avisos de stock.

**La evidencia:** el servidor no expone `/retail/layout` ni `/retail/calibrate`,
y `metadata['retail']` no aparece en ninguna parte de su código fuera de
`_backup_simplificacion_20260727`. Todo eso llamaba al vacío desde la
simplificación del 27-jul.

### 3.1 Dos intentos, y por qué el primero estaba mal

El primer barrido buscó los métodos **por su nombre** (`retail`, `planogram`,
`stock`…) y dejó fuera `_build_tienda_menu`, `_calibrate_shelves` y
`_on_calibrate_result`, que no llevan esas palabras en el nombre. Resultado: un
`SyntaxError` por una llamada multilínea a medio cortar.

El segundo buscó **por contenido**, y ahí aparecio la distinción que importaba:

- Métodos que son retail **enteros** → se borran completos (15).
- Métodos que solo lo **mencionan** (`setup_ui`, `on_text_message_received`,
  `_on_dvr_frame`) → se editan quirúrgicamente. Borrarlos habría destruido el
  widget.

Ejemplo del segundo caso: `_paint_store_zones(pix)` devolvía el pixmap con las
zonas dibujadas encima; al no haber zonas, la llamada se sustituye por `pix`.

## 4. Configuración: `config/` y cero hardcode

La IP `72.68.60.171` estaba escrita **en dos archivos distintos** como valor por
defecto (`windows_main.py` y `window_bar.py`), con el riesgo real de que la
conexión y el botón del dashboard acabaran apuntando a servidores distintos.

Ahora `src/config/ajustes.py` es la única fuente:

- `server_ws_url` es **obligatorio**. Si falta, el cliente aborta con un mensaje
  que incluye el ejemplo. Antes arrancaba y se quedaba intentando conectar a una
  IP que quizá no era la suya.
- Las URLs de dashboard y capturas se **derivan** de ella, así que no pueden
  divergir.
- `site_id` identifica el local; viaja en el contrato.

## 5. Contrato del HITO 3 aplicado

El cliente pasa a **declarar quién es** en cada mensaje:

```jsonc
{ "client_type": "tienda", "site_id": "tienda-principal", … }
```

Hasta ahora el servidor lo **deducía** del pipeline (`CLIENTE_POR_PIPELINE`),
una aproximación que falla con un cliente multimodo pidiendo `Perimetrales`. La
capa de compatibilidad ahora prefiere lo declarado y solo deduce si no viene.

Es **aditivo**: el mensaje sigue siendo válido para el formato antiguo, los
otros tres clientes no se enteran, y un valor inválido cae a la deducción sin
reventar. Verificado en los tres casos.

## 6. Estado de los criterios de aceptación

- [x] **Arranca y establece conexión.** Verificado contra el servidor real:
      conecta e `id_connection` asignado. (La primera prueba dio negativo con 7 s
      de margen; con 3 s más, conecta.)
- [x] **LOC y archivos antes/después** — sección 1.
- [x] **Cero hardcode** de IPs, puertos y rutas en el código del cliente.
- [x] **Ningún import roto** — los 12 módulos de `main.py` importan.
- [x] **README propio** con parámetros, contrato y resolución de problemas.
- [~] **Comportamiento funcional equivalente.** Lo es salvo en un punto
      deliberado: **desaparece el menú «Tienda»**. No es una regresión — abría
      diálogos que llamaban a endpoints inexistentes—, pero es un cambio
      visible para quien lo usara.

## 7. Lo que queda para este cliente

1. **Partir `render_box.py`** (1.506 LOC): sigue mezclando chrome común con
   lógica de dominio. El HITO 2 decidió partirlo; es lo que queda del cliente.
2. **Retirar los alias** al núcleo cuando ya no hagan falta.
3. **Type hints y docstrings** en el código propio que queda.
