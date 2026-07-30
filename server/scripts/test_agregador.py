"""
test_agregador.py - Tests del agregador demografico (Hito 5).

Cubre los tres criterios de aceptacion pedidos:
  1. Muestras sinteticas ruidosas -> bucket y genero correctos.
  2. Track con muestras insuficientes -> `motivo_sin_demografia`, no un
     `null` mudo.
  3. Una persona que cruza la escena produce EXACTAMENTE un registro.

Uso:
    venv\\Scripts\\python.exe scripts\\test_agregador.py
"""

from __future__ import annotations

import os
import random
import sys
from typing import List

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from src.analityc.core.analytics.agregador_demografico import (  # noqa: E402
    AgregadorDemografico, CONFIANZA_INSUFICIENTE, MUESTRAS_INSUFICIENTES,
    SIN_MUESTRAS)
from src.analityc.core.analytics.estimador_edad_genero import (  # noqa: E402
    MuestraDemografica)

_fallos: List[str] = []


def comprobar(condicion: bool, descripcion: str) -> None:
    print(f"  [{'OK ' if condicion else 'MAL'}] {descripcion}")
    if not condicion:
        _fallos.append(descripcion)


def muestra(track_id: int, genero: str, rango: str, conf: float,
            solo_cuerpo: bool = True) -> MuestraDemografica:
    return MuestraDemografica(
        track_id=track_id, rango_edad=rango, genero=genero,
        conf_genero=conf, conf_edad=0.6, solo_cuerpo=solo_cuerpo)


# ── 1. Votacion con ruido ───────────────────────────────────────────────
print("\n1) Muestras ruidosas -> genero y bucket correctos")
random.seed(7)
ag = AgregadorDemografico(min_muestras=3, max_muestras=20)
# 12 lecturas buenas de "Mujer 26-35" y 4 de ruido con baja confianza.
for _ in range(12):
    ag.agregar(muestra(1, "Mujer", "26-35", random.uniform(0.85, 0.99)))
for _ in range(4):
    ag.agregar(muestra(1, "Hombre", "18-25", random.uniform(0.56, 0.65)))
v = ag.cerrar_track(1)
comprobar(v.genero == "Mujer", f"genero mayoritario correcto (dio {v.genero})")
comprobar(v.rango_edad == "26-35", f"bucket modal correcto (dio {v.rango_edad})")
comprobar(v.confianza_global > 0.5,
          f"confianza razonable ({v.confianza_global})")
comprobar(v.tiene_demografia, "el veredicto se publica")
print(f"      -> {v.genero} {v.rango_edad} conf={v.confianza_global} "
      f"n={v.n_muestras}")

# La confianza ALTA de pocas muestras no debe ganar a la mayoria clara
print("\n1b) Una lectura muy segura no tumba a una mayoria consistente")
ag = AgregadorDemografico(min_muestras=3, max_muestras=20)
for _ in range(8):
    ag.agregar(muestra(2, "Hombre", "36-50", 0.80))
ag.agregar(muestra(2, "Mujer", "18-25", 0.99))
v = ag.cerrar_track(2)
comprobar(v.genero == "Hombre", f"gana la mayoria ponderada (dio {v.genero})")

# ── 2. Muestras insuficientes -> motivo explicito ───────────────────────
print("\n2) Muestras insuficientes -> motivo_sin_demografia")
ag = AgregadorDemografico(min_muestras=5, max_muestras=20)
for _ in range(2):
    ag.agregar(muestra(3, "Hombre", "26-35", 0.9))
v = ag.cerrar_track(3)
comprobar(v.genero is None, "no inventa genero")
comprobar(v.motivo_sin_demografia == MUESTRAS_INSUFICIENTES,
          f"motivo correcto ({v.motivo_sin_demografia})")
comprobar("motivo_sin_demografia" in v.a_dict(),
          "el motivo viaja en el JSON")
comprobar(v.a_dict()["gender"] is None,
          "gender sigue siendo null (contrato del cliente)")

print("\n2b) Ninguna muestra -> sin_muestras")
ag = AgregadorDemografico(min_muestras=3)
v = ag.cerrar_track(99)
comprobar(v.motivo_sin_demografia == SIN_MUESTRAS,
          f"motivo correcto ({v.motivo_sin_demografia})")

print("\n2c) Votacion muy repartida -> confianza_insuficiente")
ag = AgregadorDemografico(min_muestras=3, max_muestras=20)
for _ in range(6):
    ag.agregar(muestra(4, "Hombre", "26-35", 0.60))
for _ in range(6):
    ag.agregar(muestra(4, "Mujer", "26-35", 0.60))
v = ag.cerrar_track(4)
comprobar(not v.tiene_demografia, "no publica un empate")
comprobar(v.motivo_sin_demografia == CONFIANZA_INSUFICIENTE,
          f"motivo correcto ({v.motivo_sin_demografia})")
print(f"      -> confianza={v.confianza_global} motivo={v.motivo_sin_demografia}")

# ── 3. Una persona = un registro ────────────────────────────────────────
print("\n3) Una persona que cruza produce EXACTAMENTE un registro")
ag = AgregadorDemografico(min_muestras=3, max_muestras=10)
for _ in range(25):                       # cruza despacio: 25 frames
    ag.agregar(muestra(5, "Hombre", "26-35", 0.9))
comprobar(ag.n_muestras(5) == 10, "la ventana deslizante acota a 10 muestras")
v1 = ag.cerrar_track(5)
v2 = ag.cerrar_track(5)                   # el pipeline cierra dos veces
v3 = ag.cerrar_track(5)
comprobar(v1 is v2 is v3, "cerrar dos veces devuelve el MISMO veredicto")
comprobar(v1.genero == "Hombre", "el veredicto es correcto")
comprobar(ag.tracks_activos() == 0, "el buffer se libero")

# ── 4. Las lecturas con rostro pesan mas ────────────────────────────────
print("\n4) Las lecturas con rostro pesan mas que las de solo cuerpo")
# El umbral de publicacion se relaja AQUI a proposito: lo que se prueba es
# el MECANISMO de ponderacion, no la politica de publicacion (que tiene su
# propio test en 2c y 4b).
ag = AgregadorDemografico(min_muestras=3, max_muestras=20,
                          conf_minima_veredicto=0.0)
for _ in range(4):
    ag.agregar(muestra(6, "Hombre", "26-35", 0.70, solo_cuerpo=True))
for _ in range(3):
    ag.agregar(muestra(6, "Mujer", "26-35", 0.75, solo_cuerpo=False))
v = ag.cerrar_track(6)
comprobar(v.genero == "Mujer",
          f"3 lecturas con rostro superan a 4 sin rostro (dio {v.genero})")
comprobar(v.n_con_rostro == 3, f"cuenta las de rostro ({v.n_con_rostro})")
comprobar(not v.solo_cuerpo, "marca que hubo rostro")

print("\n4b) Ese mismo caso, con la politica normal, NO se publica")
# 4 vs 3 deja un margen de votacion del 9 %: demasiado repartido para
# publicarlo. Que el mecanismo elija Mujer no significa que haya que
# creerselo.
ag = AgregadorDemografico(min_muestras=3, max_muestras=20)
for _ in range(4):
    ag.agregar(muestra(8, "Hombre", "26-35", 0.70, solo_cuerpo=True))
for _ in range(3):
    ag.agregar(muestra(8, "Mujer", "26-35", 0.75, solo_cuerpo=False))
v = ag.cerrar_track(8)
comprobar(not v.tiene_demografia,
          "una votacion repartida no se publica aunque haya ganador")
print(f"      -> confianza={v.confianza_global} motivo={v.motivo_sin_demografia}")

# ── 5. Muestras de baja confianza se filtran ────────────────────────────
print("\n5) Las lecturas de confianza muy baja no entran en la votacion")
ag = AgregadorDemografico(min_muestras=3, conf_minima_muestra=0.55)
comprobar(not ag.agregar(muestra(7, "Hombre", "26-35", 0.40)),
          "rechaza una lectura de 0.40")
comprobar(ag.agregar(muestra(7, "Hombre", "26-35", 0.80)),
          "acepta una lectura de 0.80")

print("\n" + "=" * 62)
if _fallos:
    print(f"FALLOS: {len(_fallos)}")
    for f in _fallos:
        print("  -", f)
    sys.exit(1)
print("TODOS LOS TESTS DEL AGREGADOR PASAN")
sys.exit(0)
