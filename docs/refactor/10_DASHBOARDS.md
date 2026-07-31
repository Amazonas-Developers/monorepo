# HITO 9 — Los tres dashboards sobre la API de lectura

> Paso 12 del plan de migración del HITO 2. Generado el 2026-07-31.

---

## 1. El problema que resuelve

`dashboards/` llevaba desde la reorganización con un README que decía «vacía a
propósito»: los dashboards vivían **dentro** del proceso del servidor, leyendo
sus estructuras en memoria y su disco directamente. Moverlos de carpeta sin
cambiar eso habría sido cosmético.

El HITO 8 construyó la puerta (`/api/v1`). Este hito construye lo que entra
por ella.

## 2. Qué se construyó

**Páginas estáticas.** Ni un framework, ni un proceso nuevo, ni un puerto
nuevo: HTML + JS plano que el servidor sirve como archivos en
`http://<host>:9000/dashboards/`. El servidor **no las importa ni comparte
estado con ellas** — solo les hace de fichero. El día que deban vivir en otro
proceso, se sirve la misma carpeta desde allí y no cambia una línea, porque ya
hablan con el servidor por HTTP como lo haría cualquier cliente externo.

| Página | Dominio |
|---|---|
| `/dashboards/` | portada: estado del servidor (dispositivos, sitios, validación) |
| `/dashboards/tienda/` | visitantes, género, edad, permanencia, cámaras, heatmaps |
| `/dashboards/perimetrales/` | cámaras del perímetro, pipelines del dominio, enlace a VIGILANTE |
| `/dashboards/amazonas/` | cámaras, analítica por dispositivo, galería |
| `shared/` | `estilo.css` + `api.js`, comunes: el color de dominio es una variable CSS |

Cada dashboard hereda el color de su cliente (`#00c8ff`, `#e67e22`,
`#9b59b6`), los mismos del selector.

## 3. Las tres reglas, y cómo se cumplen

1. **Solo `/api/v1`.** Ninguna página lee archivos del servidor ni llama a los
   endpoints internos de `/dashboard/api/`. Lo que faltaba en la API se añadió
   a la API (sección 4), no se puenteó.
2. **Cero hosts y cero puertos escritos** (regla 6, ahora también en el
   navegador). Las llamadas son rutas relativas — las páginas las sirve el
   mismo origen que responde la API. Y los enlaces a paneles en otros puertos
   (tienda :9030, VIGILANTE :5333) se preguntan a `/api/v1/paneles`, que los
   saca de la configuración real del servidor (`PUERTO_TIENDA`,
   `vigilante.config.PUERTO_API`), y se montan sobre el hostname actual. Si un
   panel no existe en una instalación, llega `puerto: null` y el enlace no se
   pinta.
3. **Un fallo de red no rompe el bucle de refresco**: se muestra en la
   cabecera («sin conexión con la API») y se reintenta en el siguiente tick.

## 4. Lo que se añadió a `/api/v1`

- **`/resumen`** — KPIs y distribuciones (visitantes, género, edad,
  permanencia). **Delega en el mismo cálculo** que `/dashboard/api/summary`
  en vez de reimplementarlo: una sola fuente de verdad para los números, dos
  puertas para leerlos. Verificado con los datos reales: 24 visitantes únicos,
  distribución de género/edad de la base de personas.
- **`/paneles`** — dónde viven los paneles auxiliares, para que ninguna página
  lleve un puerto escrito.

## 5. Los dashboards anteriores siguen vivos

Como manda el paso 12 del HITO 2 («los actuales siguen vivos» es el rollback):
`/dashboard` en el 9000 y el de tienda en el 9030 no se tocaron. Los tres
nuevos son la vista por dominio sobre la API y **enlazan** a aquellos para el
detalle (galería, capturas, VLM). Retirarlos, si algún día procede, es una
decisión del HITO 11 con los nuevos ya rodados.

## 6. Verificación

- [x] Las 6 rutas estáticas responden 200 con contenido (portada, 3 paneles,
      CSS, JS).
- [x] Los 7 endpoints que consumen las páginas responden con datos reales
      (`resumen` con los 24 visitantes históricos; `paneles` con 9030 y 5333
      leídos de la configuración).
- [x] El JS de las 5 fuentes pasa `node --check` (sin navegador aquí, la
      maquetación queda verificada a nivel de sintaxis y de contrato de datos:
      cada campo que las páginas leen se contrastó contra la respuesta real
      del endpoint).
- [x] El saneado de `device_id` por URL tiene pruebas (traversal → 404,
      comprobado también por HTTP real).
- [x] 12 pruebas del servidor y 63 del núcleo en verde.
- [x] El servidor arranca igual si `dashboards/` no existe (aviso en el log,
      `ELDE_DASHBOARDS_DIR` para recolocarla).

## 7. Lo que queda

1. Ver los tres dashboards **con tráfico real** de los clientes: hoy el
   registro está limpio a propósito (los dispositivos de mis pruebas
   sintéticas se borraron) y las tablas de cámaras salen vacías hasta que un
   cliente real envíe frames.
2. El corte a `estricto` del contrato sigue pendiente de ese mismo tráfico
   real (y de H-19 para `hummus`).
3. HITO 10: vaciar `_legacy/`. HITO 11: documentación final y decisión sobre
   los dashboards antiguos.
