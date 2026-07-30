# Dashboard ELDE — Rostros Reconocidos

Dashboard web en tiempo real para visualizar los rostros únicos detectados
por el sistema de re-identificación facial (`FaceReidentifier`).

## Arranque

```bash
# Desde la raíz del proyecto
python webapp/app.py
```

Por defecto se sirve en `http://localhost:5000`. Para escuchar en otra
interfaz o puerto:

```powershell
$env:HOST = "0.0.0.0"      # acepta conexiones LAN
$env:PORT = "8080"
python webapp/app.py
```

## Características

- **Vista en grid** con tarjetas de cada persona única detectada.
- **Foto del rostro** guardada por el `FaceReidentifier` (160×160 JPEG).
- **Demográficos**: género, rango de edad, % de confianza.
- **Tiempos**: primera visita, última visita (formato relativo "hace
  Xm/h").
- **Conteo de visitas**: badge `×N` cuando una persona ha sido
  re-identificada múltiples veces.
- **Auto-refresh cada 3 segundos** (se puede desactivar con el
  checkbox en la esquina superior derecha).
- **Filtros**: por género, por confianza mínima.
- **Ordenamiento**: por última visita, primera visita, número de
  visitas, confianza, edad.
- **Stats globales**: total únicas, nuevas hoy, total visitas,
  breakdown por género.
- **Modal de detalle** al clickear una tarjeta: muestra UUID completo,
  edad estimada exacta, timestamps absolutos.
- **Animación de "nueva persona"**: cuando aparece una persona nueva,
  su tarjeta brilla en verde por 1.5s.

## API REST

Si quieres consumir los datos desde otro sistema:

| Endpoint | Método | Descripción |
|---|---|---|
| `/api/persons` | GET | Lista filtrada/ordenada de personas |
| `/api/persons/{uuid}` | GET | Detalle de una persona |
| `/api/faces/{uuid}.jpg` | GET | Imagen del rostro (160×160) |
| `/api/stats` | GET | Estadísticas agregadas |
| `/health` | GET | Health check |

### Query params de `/api/persons`

- `sort`: `last_seen` (default) | `first_seen` | `visit_count` |
  `age_value` | `demo_confidence`
- `order`: `desc` (default) | `asc`
- `gender`: `Hombre` | `Mujer` | `Desconocido` | `` (todos)
- `min_confidence`: float 0.0–1.0

Ejemplo:
```
GET /api/persons?gender=Mujer&min_confidence=0.7&sort=visit_count
```

## Arquitectura

```
webapp/
├── app.py              # FastAPI server
├── templates/
│   └── index.html      # HTML del dashboard
└── static/
    ├── style.css       # Tema oscuro
    └── app.js          # Polling + render + filtros
```

- El servidor **lee** `output/person_db/persons.pkl` en cada request
  (no compite con la escritura del proceso de analytics).
- Las imágenes vienen de `output/person_db/faces/<uuid>.jpg`, creadas
  por el `FaceReidentifier` cuando una persona se registra.
- Polling cada 3 segundos al `/api/persons` y `/api/stats` desde el
  frontend.

## Troubleshooting

**Sin datos en el dashboard**:
- Verifica que `output/person_db/persons.pkl` existe (se crea cuando el
  sistema de analytics detecta al menos una cara con buena calidad).
- Comprueba `/health` — debe devolver `db_exists: true`.
- Comprueba que `output/person_db/faces/` tiene imágenes.

**No se ven las fotos**:
- Confirma que ArcFace ONNX está cargado:
  `python scripts/setup_face_embedding.py` (debe imprimir "ya existe").
- El `FaceReidentifier` solo guarda imágenes cuando hay rostro de
  calidad **Y** está habilitada la re-identificación
  (`AnalyticsConfig.REID_ENABLED = True`).

**Reset diario**:
- A las 00:00 la DB se borra automáticamente (`REID_RESET_POLICY="daily"`).
- Si quieres histórico semanal: `REID_RESET_POLICY="weekly"`.
- Para histórico permanente: `REID_RESET_POLICY="never"`.
