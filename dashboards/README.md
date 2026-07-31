# dashboards/

Los dashboards de producto por cliente (HITO 9 + fase 2 del 31-jul-2026).
**Páginas estáticas** que leen exclusivamente de la API de lectura
(`/api/v1`, HITO 8).

| Página | Dominio | Color |
|---|---|---|
| `perimetrales/` | buscador de alertas, galería de fotos, desgloses por detección/evento/cámara, totales, búsqueda VLM | `#e67e22` |
| `tienda/` | pasillos más/menos concurridos, marketing (visitantes, género, edad, franjas), capturas, búsqueda VLM | `#00c8ff` |
| `amazonas/` | totales de personas/mujeres/hombres, desglose género×edad, capturas, búsqueda VLM | `#9b59b6` |
| `managers/` | operación global: salud del servidor, todas las cámaras, últimos eventos | `#2ecc71` |
| `index.html` | portada con el estado del servidor | — |
| `shared/` | `estilo.css` + `api.js` + `vlm.js`, comunes a todos | — |

## Cómo se sirven

El servidor las monta como archivos estáticos en `/dashboards` (puerto 9000).
**No las importa ni comparte estado con ellas**: solo les hace de fichero. Por
eso el día que deban vivir en otro proceso basta con servir esta misma carpeta
desde allí — las páginas no cambian, porque ya hablan con el servidor por HTTP.

La carpeta se puede recolocar con `ELDE_DASHBOARDS_DIR`; si no existe, el
servidor arranca igual y lo avisa en el log.

## Las reglas que cumplen (y hay que conservar)

1. **Los DATOS, solo de `/api/v1`.** Ni una página lee un archivo del servidor
   ni consulta datos por `/dashboard/api/`. Si un dato falta, se añade a la API
   de lectura, no se puentea. La ÚNICA excepción son las ACCIONES del buscador
   VLM (`shared/vlm.js` → `/dashboard/api/vlm-buscador` y `/vlm-busqueda`):
   `/api/v1` es solo lectura por regla, así que encender el buscador y lanzar
   una búsqueda viven donde ya viven las acciones.
2. **Cero hosts y cero puertos escritos.** Las llamadas son rutas relativas; los
   enlaces a paneles en otros puertos (VIGILANTE :5333) se preguntan a
   `/api/v1/paneles` y se montan sobre el hostname actual.
3. **Un fallo de red no rompe el bucle**: se muestra en la cabecera y se
   reintenta en el siguiente refresco.

## Relación con los dashboards anteriores

- **`/dashboard` (9000, analítica de visitantes): vivo.** Tiene el detalle que
  estas páginas no replican (galería, capturas, VLM, vaciado); la de tienda
  enlaza a él.
- **El dashboard propio de tienda (9030): RETIRADO el 31-jul-2026** por
  decisión del cierre del refactor. Lo sustituye `tienda/` de esta carpeta.
  Rollback: el módulo `server/src/app/dashboard_tienda.py` sigue en el árbol;
  revivirlo es volver a llamar a `iniciar_dashboard_tienda()` en
  `iniciar_servidor_headless.py`.
