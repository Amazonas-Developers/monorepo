# Guía de trabajo en equipo — SERVER-IA

Este repo lo trabajan varias personas a la vez. Para no pisarnos, cada quien
trabaja en SU zona y en SU rama. **Nunca se hace commit directo a `main`.**

## ¿Quién toca qué? (evita conflictos)

| Persona | Zona del repo |
|---------|---------------|
| **A – IA / Analítica** | `src/analityc/` (core, cascades, config), `train/`, `pipeline_hummus_vlm.py` |
| **B – Backend / Servidor** | `src/app/`, `src/gui/`, `webapp/`, `scripts/` |

Antes de tocar un archivo que NO es de tu zona, avisa al responsable.

## Fronteras a acordar entre personas
- **A ↔ B**: la interfaz de inferencia (qué función llama el backend al motor de IA y qué devuelve).
- Si cambias esa interfaz, avisa antes.

## Flujo diario (cópialo y repítelo cada día)

```bash
# 1. Cada mañana: traer lo último de main
git checkout main
git pull

# 2. Crear tu rama para lo que vas a hacer hoy
git checkout -b feature/lo-que-voy-a-hacer

# 3. Trabaja SOLO en tu zona. Commits pequeños y frecuentes:
git add .
git commit -m "feat: describe el cambio"

# 4. Subir tu rama
git push -u origin feature/lo-que-voy-a-hacer

# 5. En GitHub: abrir Pull Request -> que otro lo revise -> merge a main
```

Después del merge, borra tu rama y vuelve al paso 1 para la siguiente tarea.

## Reglas
- `main` siempre debe quedar funcionando.
- No subir modelos (`.pt`, `models/`), `venv/`, `__pycache__` ni `output/` — ya están en `.gitignore`.
- Los **modelos** se comparten por carpeta de red / Drive, NO por Git.

## Nombres de rama sugeridos
- `feature/...` para algo nuevo
- `fix/...` para corregir un bug
- `refactor/...` para reorganizar código sin cambiar comportamiento
