# ELDE — ecosistema de analítica de video

Monorepo de los cuatro clientes de escritorio, el servidor de inferencia y los
dashboards. Refactorizado en jul-2026: la historia completa está en
[docs/refactor/](docs/refactor/) (el índice es `12_CIERRE.md`).

## Estructura

```
clients/
  tienda/        analítica de supermercado (visitantes, género, edad, heatmaps)
  perimetrales/  vigilancia perimetral (vehículos y personas)
  managers/      gestor de ventanas (cliente multimodo)
  amazonas/      Amazonas View (personal y visitantes)
server/          servidor de inferencia (FastAPI + websocket :9000)
dashboards/      páginas de dominio servidas en :9000/dashboards/ sobre /api/v1
packages/
  elde_core/     núcleo compartido: contratos, captura, DVR, UI, logging (63 pruebas)
docs/refactor/   informes de los hitos + HALLAZGOS.md (registro vivo de bugs)
```

Cada cliente es PySide6 con su propio `venv/` y su `.env` (obligatorio:
`server_ws_url`). El núcleo se comparte por imports de `elde_core`; los
clientes declaran su identidad (`client_type`, `site_id`) en el contrato.

## Arrancar

| Qué | Cómo |
|---|---|
| Todo el sistema de tienda | `INICIAR_TIENDA.bat` |
| Selector de sistemas | `SELECTOR.bat` |
| Solo el servidor | `server\venv\Scripts\python.exe server\iniciar_servidor_headless.py 9000` |

Con el servidor arriba: dashboards en `http://localhost:9000/dashboards/`,
analítica de visitantes en `/dashboard`, API de lectura en `/api/v1`, salud en
`/health`, panel de VIGILANTE en `:5333`.

## Reglas de la casa

1. **Cero valores incrustados**: IPs, puertos, rutas y umbrales salen de
   configuración o del entorno.
2. **Los bugs se anotan** en [docs/refactor/HALLAZGOS.md](docs/refactor/HALLAZGOS.md)
   y se corrigen con aprobación.
3. **Lo compartido va a `elde_core` con pruebas**; los clientes guardan solo
   su dominio.
4. **El contrato es código** (`elde_core/contracts`): los payloads se validan
   en el servidor (`ELDE_VALIDAR_CONTRATO=observar|estricto|apagado`).
5. **Credenciales, jamás en archivos**: la sesión de Hik-Connect se introduce
   en el cliente, vive cifrada en reposo y se borra del entorno al cerrar
   sesión.

## Lo que no está en el repositorio

- `modelos NVIDIA/` (50 GB) y los pesos `*.pt` — compartir por red.
- `hik-connect/` — SDK de terceros y notas de API.
- Los `venv/` y los `.env`.
