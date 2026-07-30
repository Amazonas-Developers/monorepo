# Cuarentena del cliente de tienda (HITO 5)

Codigo retirado de `tienda_view` con evidencia. **No se borra aqui**:
el borrado definitivo es el HITO 10, tras reverificar que nadie lo usa.
Para recuperar cualquiera, basta copiarlo de vuelta a su ruta original.

- `src/gui/components/retail_panel.py` — 444 LOC. Analitica de retail: el servidor dejo de emitir metadata["retail"] el 27-jul.
- `src/gui/components/planogram_editor.py` — 454 LOC. Editor de planograma: guarda en /retail/layout, endpoint que el servidor ya no expone.
- `src/core/locking_windows.py` — 62 LOC. HITO 1: no alcanzable desde main.py y 0 referencias. Ya borrado a mano en perimetrales y Amazonas.
- `src/core/run_controller.py` — 13 LOC. HITO 1: no alcanzable y 0 referencias. Ya borrado en los otros clientes.
- `src/core/window_capture.py` — 90 LOC. HITO 1: no alcanzable y 0 referencias. Ya borrado en los otros clientes.
- `src/utils/files/print_png.py` — 25 LOC. HITO 1: no alcanzable y 0 referencias.
- `src/core/api_client.py` — 0 LOC. HITO 1: archivo vacio, no alcanzable.
- `src/core/network/api_client.py` — 0 LOC. HITO 1: archivo vacio, no alcanzable.
