# dashboards/

Los tres dashboards de dominio del HITO 9. **Páginas estáticas** que leen
exclusivamente de la API de lectura (`/api/v1`, HITO 8).

| Página | Dominio | Color |
|---|---|---|
| `tienda/` | marketing y consumo: visitantes, género, edad, permanencia, heatmaps | `#00c8ff` |
| `perimetrales/` | cámaras del perímetro, salud del contrato, enlace al panel de VIGILANTE | `#e67e22` |
| `amazonas/` | cámaras del dominio, analítica por dispositivo, galería | `#9b59b6` |
| `index.html` | portada con el estado del servidor | — |
| `shared/` | `estilo.css` + `api.js`, comunes a los tres | — |

## Cómo se sirven

El servidor las monta como archivos estáticos en `/dashboards` (puerto 9000).
**No las importa ni comparte estado con ellas**: solo les hace de fichero. Por
eso el día que deban vivir en otro proceso basta con servir esta misma carpeta
desde allí — las páginas no cambian, porque ya hablan con el servidor por HTTP.

La carpeta se puede recolocar con `ELDE_DASHBOARDS_DIR`; si no existe, el
servidor arranca igual y lo avisa en el log.

## Las reglas que cumplen (y hay que conservar)

1. **Solo `/api/v1`.** Ni una página lee un archivo del servidor ni llama a
   `/dashboard/api/` (los endpoints internos del proceso). Si un dato falta,
   se añade a la API de lectura, no se puentea.
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
