# Guía de trabajo en equipo — windows_managers_view

Cliente de escritorio (Windows). Para no pisarnos, cada quien trabaja en SU rama.
**Nunca se hace commit directo a `main`.**

## ¿Quién toca qué?

| Persona | Zona del repo |
|---------|---------------|
| **C – Cliente Windows** | Responsable principal de todo el repo: `src/sdk/` (Dahua/Hikvision), `src/core/` (dvr, network, state), `src/gui/`, `src/workers/` |

> Si más de una persona entra a este repo, repartir por subcarpeta:
> uno en `src/gui/`, otro en `src/core/` + `src/sdk/`.

## Frontera importante a acordar
- **Con el Backend (repo SERVER-IA)**: el contrato de la API (endpoints, formato JSON
  de eventos/alertas). Si se define bien una vez, cliente y servidor avanzan en paralelo
  sin tocarse. Cualquier cambio en ese contrato se avisa antes.

## ANTES de empezar: pedir el SDK
La carpeta `src/sdk/` está fuera de Git (binarios grandes). Pídela a quien la tenga
y colócala en `src/sdk/` antes de correr el proyecto.

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
- No subir `venv/`, `dist/`, `build/`, `__pycache__` ni `src/sdk/` — ya están en `.gitignore`.

## Nombres de rama sugeridos
- `feature/...` para algo nuevo
- `fix/...` para corregir un bug
- `refactor/...` para reorganizar código sin cambiar comportamiento
