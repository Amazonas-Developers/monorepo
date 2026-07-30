# HITO 7 — Clientes Amazonas View y Gestor de ventanas

> Los dos clientes **multimodo**. Con esto, los cuatro están sobre el núcleo y
> el contrato. Generado el 2026-07-30.

---

## 1. Cifras

| Cliente | LOC antes | LOC después | Redirecciones |
|---|---:|---:|---:|
| Amazonas View | 4.655 | **3.713** | 20 → 29 |
| Gestor de ventanas | 2.522 | **2.376** | 37 |

### El ecosistema completo

| Cliente | HITO 0 | Hoy | |
|---|---:|---:|---|
| tienda_view | 9.461 | 2.683 | −72% |
| perimetrales-view | 11.252 | 2.933 | −74% |
| windows_managers_view | 7.867 | 2.376 | −70% |
| Amazonas View | 8.463 | 3.713 | −56% |
| **Total clientes** | **37.043** | **11.705** | **−68%** |
| Núcleo compartido | — | 7.340 | 50 módulos |

De 37.043 LOC repetidas en cuatro copias a 11.705 propias más 7.340
compartidas. Y lo compartido tiene pruebas.

## 2. Amazonas arrastraba dos stacks DVR a la vez

El hallazgo del hito. Este cliente conservaba **su propia copia del paquete
`core/dvr/`**, mucho más antigua que la del núcleo — `hikconnect.py` con 218
líneas frente a 580.

Lo que lo hacía incoherente: desde el HITO 4 su panel de Dispositivos viene del
núcleo, y **ese** usa el DVR bueno. Así que en el mismo proceso convivían dos
stacks distintos, y la copia local solo se usaba para **una** cosa:
`ChannelTypeDetector`, importado en `render_box.py:33`.

Comprobado que la versión del núcleo es superconjunto compatible —mismas clases
y métodos, más parámetros opcionales con valor por defecto y autodetección de
cifrado—, se alió el paquete entero: **1.101 LOC menos**, y el cliente pasa a
usar un solo stack, el que ya usaba su propio panel.

## 3. La resolución de servidor de Amazonas era mejor que la mía

Al sacar el hardcode me encontré con que su `_url_servidor()` hace más que el
`config/ajustes.py` que escribí para los otros:

1. Variable de entorno `AMAZONAS_SERVER_WS` (la usa su lanzador).
2. Lo que el usuario haya guardado en la configuración de la aplicación.
3. Y acepta `host:puerto` o solo `host`, completando esquema y ruta — que es
   como la gente lo teclea.

**No la sustituí.** Solo se le quitó el literal con la IP: antes, si no había
nada configurado, caía en silencio al servidor de otra instalación. Ahora, si
no hay nada, lo dice con el ejemplo delante.

Es el mismo criterio que con `dashboard_url.py` en perimetrales: cuando el
cliente ya lo hace mejor, se conserva y se le añade lo que falta.

## 4. El contrato importa especialmente en estos dos

Ambos son **multimodo**: su selector ofrece `Hummus`, `HummusVLM`,
`Autolavado`, `Perimetrales`, `PerimetralesMultiCam` y `Personal de Amazonas`.

El servidor **deducía** el `client_type` a partir del pipeline. Con tienda
acertaba por casualidad (solo tiene uno); aquí fallaba de forma sistemática:
`Perimetrales` en el gestor de ventanas se etiquetaba como si fuera el cliente
perimetral. Ahora los cuatro declaran quiénes son:

| Cliente | `client_type` | `site_id` |
|---|---|---|
| tienda_view | `tienda` | `tienda-principal` |
| perimetrales-view | `perimetrales` | `perimetro-principal` |
| windows_managers_view | `managers` | `managers-principal` |
| Amazonas View | `amazonas` | `amazonas-principal` |

## 5. Cuarentena

**Gestor de ventanas:** 190 LOC (`window_capture`, `locking_windows`,
`run_controller`, `print_png` y dos archivos vacíos) a
`_legacy/windows_managers_view/`. Son exactamente los mismos que ya se
retiraron de tienda, y los mismos que perimetrales y Amazonas habían borrado a
mano.

**Amazonas View:** no aplica. Su código muerto era el stack DVR duplicado, que
no se pone en cuarentena sino que se sustituye por la redirección al núcleo.

## 6. Estado de los criterios de aceptación

- [x] **Arrancan y cargan su configuración** — verificado en ambos: imports
      completos (11/11 y 10/10) y contrato resuelto.
- [x] **LOC y archivos antes/después** — sección 1.
- [x] **Cero hardcode** de IPs, puertos ni rutas.
- [x] **Ningún import roto.**
- [x] **README propio** en ambos.
- [x] **Comportamiento equivalente.** Ningún cambio visible en la interfaz de
      ninguno de los dos.

### Un fallo mío al generar la configuración

Los `config/ajustes.py` de estos dos se generaron con una plantilla, y el
escapado convirtió un `\n` en un salto de línea real dentro de una cadena:
`SyntaxError` al importar. Los imports daban 11/11 y 10/10 —el error estaba
solo en el módulo nuevo—, así que la primera lectura parecía correcta. Corregido
y verificado importándolo de verdad, no solo compilando.

Es la segunda vez en este refactor que el escapado dentro de una plantilla
genera código roto. La lección: **generar código con plantillas exige importar
el resultado**, no basta con que compile.

## 7. Lo que queda

Común a los cuatro clientes:

1. **Partir `render_box.py`** — 1.506, 1.214, 1.308 y 915 LOC. Es lo único
   grande que queda y el HITO 2 ya decidió partirlo, no moverlo.
2. **Retirar las redirecciones** cuando cada cliente use el núcleo
   directamente.
3. `tema.py` lo comparten perimetrales y Amazonas: candidato al núcleo si se
   unifica el estilo.
