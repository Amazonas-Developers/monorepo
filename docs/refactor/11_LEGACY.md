# HITO 10 — Vaciado de `_legacy/`

> Paso 13 del plan de migración del HITO 2. Generado el 2026-07-31.

---

## 1. Lo que se borró

`_legacy/` completa: **16 archivos, 1.492 LOC** (87 KB), en cuarentena desde
los HITOS 5 y 7.

| Origen | Archivos | Por qué estaban ahí |
|---|---|---|
| `tienda_view` (HITO 5) | 8 + README | retail y planograma retirados el 27-jul (444 + 454 LOC); inalcanzables del HITO 1 (`locking_windows`, `run_controller`, `window_capture`, `print_png`); dos `api_client.py` vacíos |
| `windows_managers_view` (HITO 7) | 6 + README | los mismos inalcanzables del HITO 1, que perimetrales y Amazonas ya habían borrado a mano |

## 2. La reverificación que el README exigía

Los README de la cuarentena decían: «el borrado definitivo es el HITO 10,
**tras reverificar que nadie lo usa**». Hecha hoy, no en los HITOS 5/7:

1. **Cero imports vivos** de los siete nombres de módulo en `clients/`,
   `server/`, `packages/` y `selector.py`.
2. **El servidor no expone `/retail`** ni emite `metadata['retail']`: sus
   menciones de «retail» son docstrings. Es la condición que retiró
   `retail_panel` y `planogram_editor`.
3. Las menciones de «planogram/retail» en el cliente vivo de tienda son
   **comentarios históricos** (explican por qué se retiró el panel de
   reposición), no código.

Nota para no confundirse en el futuro: el «planograma» que sigue vivo —las
zonas por cámara que H-11 hizo persistentes— es la función de zonas de
`render_box`, **no** el `planogram_editor.py` borrado, que guardaba contra el
endpoint `/retail/layout` que ya no existe.

## 3. Cómo se recupera, si hiciera falta

Doble red:

- **Tag `pre-hito10-legacy`** (`5b6aadc`): el árbol completo con la carpeta
  llena. `git checkout pre-hito10-legacy -- _legacy/` la restaura entera.
- Los 16 archivos estaban **versionados**, así que también viven en el
  historial de cualquier commit anterior.

## 4. Verificación tras el borrado

- [x] Los 4 clientes importan su `main.py` completo (12/12, 14/14, 12/12,
      11/11).
- [x] 63 pruebas del núcleo + 12 del servidor en verde.
- [x] El servidor sigue sirviendo (no se reinició: nada suyo estaba en
      cuarentena).

## 5. Fuera del alcance, y a propósito

`_legacy/` era **la** cuarentena formal del refactor y ya está vacía. Quedan en
el árbol otros restos que **no** pasaron por cuarentena y por tanto no se
tocan sin decisión aparte (candidatos para el HITO 11):

| Resto | Qué es |
|---|---|
| `clients/tienda/_backup_simplificacion_20260727/` | respaldo manual de la simplificación de julio |
| `clients/perimetrales/_backup_limpieza_20260728/` | respaldo manual de la modernización |
| `clientes_windows/` | 8 KB en la raíz |
| `__pycache__/` en la raíz | bytecode suelto |
| Los dashboards antiguos (9000/dashboard, 9030) | vivos a propósito: son el rollback del HITO 9 |
