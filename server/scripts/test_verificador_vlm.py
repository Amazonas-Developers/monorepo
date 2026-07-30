"""
test_verificador_vlm.py - Tests del verificador VLM (Hito 6).

Comprueba los criterios de aceptacion SIN cargar el modelo real (que pesa
varios GB): el router se sustituye por un doble que simula latencia. Lo
que se valida es el comportamiento de la cola y el aislamiento del
pipeline, que es donde estan los riesgos.

Uso:
    venv\\Scripts\\python.exe scripts\\test_verificador_vlm.py
"""

from __future__ import annotations

import os
import sys
import time
from typing import List

import numpy as np

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from src.analityc.core.analytics import verificador_vlm as mod  # noqa: E402
from src.analityc.core.analytics.verificador_vlm import (  # noqa: E402
    VerificadorVLM, _parsear)

_fallos: List[str] = []


def comprobar(cond: bool, desc: str) -> None:
    print(f"  [{'OK ' if cond else 'MAL'}] {desc}")
    if not cond:
        _fallos.append(desc)


IMG = np.zeros((300, 120, 3), np.uint8)


# ── 1. Parseo robusto ───────────────────────────────────────────────────
print("\n1) Parseo de respuestas del modelo")
casos = [
    ('{"genero":"hombre","categoria_edad":"adulto","seguridad":"alta"}',
     "Hombre", "adulto"),
    ('Claro:\n```json\n{"genero": "mujer", "categoria_edad": "adolescente",'
     ' "seguridad": "media"}\n```', "Mujer", "adolescente"),
    ('{"genero":"desconocido","categoria_edad":"desconocido",'
     '"seguridad":"baja"}', None, None),
]
for texto, g_esp, e_esp in casos:
    r = _parsear(texto, 1)
    comprobar(r is not None and r.genero == g_esp and r.categoria_edad == e_esp,
              f"parsea {texto[:38]!r}... -> {None if r is None else r.genero}")
for basura in ("", "no puedo ayudarte con eso", "{roto", None):
    comprobar(_parsear(basura, 1) is None,
              f"descarta basura: {str(basura)[:28]!r}")


# ── 2. Desactivado: no encola, no arranca hilo, no carga modelo ─────────
print("\n2) Desactivado -> el pipeline corre igual y sin VRAM")
v = VerificadorVLM(activo=False)
comprobar(not v.encolar(1, IMG), "encolar devuelve False")
comprobar(not v.necesita_verificacion(0.1, False),
          "no pide verificacion ni con confianza 0.1")
comprobar(v._hilo is None, "no arranca hilo de trabajo")
comprobar("multimodal_router" not in sys.modules,
          "no importa el router (el modelo no se carga)")
comprobar(v.estadisticas()["encolados"] == 0, "no registra encolados")


# ── 3. Solo se encolan los dudosos ──────────────────────────────────────
print("\n3) Solo se consulta a los tracks dudosos")
v = VerificadorVLM(activo=True, umbral_confianza=0.60, tam_cola=4)
comprobar(v.necesita_verificacion(0.45, True), "confianza 0.45 -> se verifica")
comprobar(v.necesita_verificacion(0.0, False), "sin veredicto -> se verifica")
comprobar(not v.necesita_verificacion(0.85, True),
          "confianza 0.85 -> NO se gasta el VLM")
v.detener()


# ── 4. Cola llena: descarta, no acumula ni bloquea ──────────────────────
print("\n4) Cola llena -> descarta sin bloquear")


class _RouterLento:
    """Doble del router: simula una consulta cara (0.4 s)."""

    def __init__(self) -> None:
        self.llamadas = 0

    def vqa(self, imagen, pregunta, max_new_tokens=80):
        self.llamadas += 1
        time.sleep(0.4)
        return '{"genero":"hombre","categoria_edad":"adulto",' \
               '"seguridad":"alta"}'


doble = _RouterLento()
mod.get_multimodal_router = lambda *a, **k: doble   # inyeccion del doble
# El modulo lo importa dentro de la funcion, asi que hay que parchear ahi:
import types  # noqa: E402
_orig = mod.VerificadorVLM._procesar


def _procesar_con_doble(self, track_id, imagen):
    respuesta = doble.vqa(imagen, "x", max_new_tokens=10)
    with self._lock:
        self._stats["procesados"] += 1
    r = mod._parsear(respuesta, track_id)
    if r is None:
        with self._lock:
            self._stats["no_parseables"] += 1
        return
    with self._lock:
        self._resultados[track_id] = r
        if r.es_util:
            self._stats["utiles"] += 1


mod.VerificadorVLM._procesar = _procesar_con_doble

v = VerificadorVLM(activo=True, tam_cola=3)
t0 = time.perf_counter()
aceptados = sum(1 for i in range(40) if v.encolar(i, IMG))
tardanza = time.perf_counter() - t0
comprobar(tardanza < 0.30,
          f"encolar 40 peticiones no bloquea ({tardanza*1000:.0f} ms)")
comprobar(aceptados < 40, f"la cola llena descarta ({aceptados} aceptados)")
st = v.estadisticas()
comprobar(st["descartados_cola_llena"] > 0,
          f"cuenta los descartes ({st['descartados_cola_llena']})")
comprobar(st["en_cola"] <= 3, f"la cola no crece ({st['en_cola']} <= 3)")

time.sleep(1.2)                        # deja avanzar al hilo
st = v.estadisticas()
comprobar(st["procesados"] > 0, f"el hilo procesa ({st['procesados']})")
r = v.obtener(0)
comprobar(r is not None and r.genero == "Hombre",
          "el resultado queda disponible por track")
v.detener()


# ── 5. Una respuesta que revienta no mata el hilo ───────────────────────
print("\n5) Un fallo del modelo no tumba el hilo")


def _procesar_explota(self, track_id, imagen):
    raise RuntimeError("el modelo se cayo")


mod.VerificadorVLM._procesar = _procesar_explota
v = VerificadorVLM(activo=True, tam_cola=5)
for i in range(3):
    v.encolar(i, IMG)
time.sleep(0.8)
st = v.estadisticas()
comprobar(st["errores"] >= 1, f"registra los errores ({st['errores']})")
comprobar(v._hilo is not None and v._hilo.is_alive(),
          "el hilo sigue vivo tras las excepciones")
v.detener()
mod.VerificadorVLM._procesar = _orig

print("\n" + "=" * 62)
if _fallos:
    print(f"FALLOS: {len(_fallos)}")
    for f in _fallos:
        print("  -", f)
    sys.exit(1)
print("TODOS LOS TESTS DEL VERIFICADOR VLM PASAN")
sys.exit(0)
