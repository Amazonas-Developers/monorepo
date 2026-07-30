# dashboards/

Carpeta reservada para el **HITO 9**. Hoy está vacía a propósito.

## Por qué está vacía

Los dashboards existen y funcionan, pero **los sirve el propio servidor**, no
un proceso aparte:

| Dashboard | Módulo | Puerto | Cómo arranca |
|---|---|---:|---|
| Visitantes / general | `server/src/app/dashboard.py` | 9000 (`/dashboard`) | Rutas montadas en la app principal de FastAPI |
| Tienda (marketing y consumo) | `server/src/app/dashboard_tienda.py` | 9030 | App FastAPI propia, en un hilo del **mismo** proceso, desde `iniciar_servidor_headless.py` |

Mover esos dos archivos aquí sin cambiar nada más sería **cosmético y peor**:
el servidor seguiría importándolos, así que solo añadiría un import cruzado
entre dos carpetas de primer nivel.

## Qué tiene que pasar antes

Sacarlos del proceso del servidor es una decisión de arquitectura, no un
movimiento de archivos. Depende de algo que todavía no existe:

1. **La API de lectura del HITO 8.** Hoy los dashboards leen el estado del
   servidor por dentro (mismo proceso, mismas estructuras en memoria). Para
   vivir aparte necesitan leerlo por HTTP.
2. **Decidir si son uno o tres.** El HITO 9 pide tres dashboards; hoy hay dos,
   y el de visitantes mezcla dominios que el contrato del HITO 3 ya separa
   (`client_type`).

Hasta entonces, los dashboards se quedan donde están y funcionando.

## Regla al llenar esta carpeta

Lo mismo que rige en el resto del refactor: cero valores incrustados. Los
puertos 9000 y 9030 de la tabla de arriba son los que hay **hoy**; cuando el
código se mueva aquí, tienen que salir de configuración, no del código.
