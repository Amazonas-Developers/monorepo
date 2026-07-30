"""
resumen_telemetria.py - Vuelca el resumen de la telemetria demografica.

Lee el JSONL que escribe el servidor (un registro por track cerrado) y
presenta la tabla de motivos: cuantos tracks acabaron con demografia y,
sobre todo, POR QUE fallaron los demas.

Uso:
    venv\\Scripts\\python.exe scripts\\resumen_telemetria.py
    venv\\Scripts\\python.exe scripts\\resumen_telemetria.py --archivo otro.jsonl
    venv\\Scripts\\python.exe scripts\\resumen_telemetria.py --desde 2026-07-27

Tambien sirve contra un servidor vivo:
    curl http://localhost:9000/dashboard/api/telemetria
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional

# Permite ejecutar el script desde la raiz del repo sin instalar el paquete.
_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

_RUTA_POR_DEFECTO = os.path.join(_RAIZ, "output", "telemetria_demografica.jsonl")

# Que hacer ante cada motivo. Es la traduccion de "numero" a "accion".
_INTERPRETACION: Dict[str, str] = {
    "clasificado": "OK - rama facial",
    "clasificado_solo_cuerpo": "OK - rama corporal (sin rostro)",
    "heredado_reid": "OK - reusado de una visita anterior",
    "sin_rostro": "No se vio cara: necesita rama corporal (MiVOLO)",
    "rostro_muy_pequeno": "Cara por debajo del minimo util (< 25 px)",
    "calidad_insuficiente": "Cara rechazada por pose/nitidez/contraste",
    "muestras_insuficientes": "Hubo muestras pero no alcanzaron el umbral",
    "track_no_cerrado": "El track murio antes de tener veredicto",
    "excepcion_estimador": "EL ESTIMADOR LANZO: revisar el log",
}


def cargar(ruta: str, desde: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lee el JSONL. Ignora lineas corruptas (el volcado es concurrente)."""
    if not os.path.isfile(ruta):
        print(f"No existe el archivo de telemetria: {ruta}")
        print("Arranca el servidor con TELEMETRIA_DEMO_ENABLED=True y deja")
        print("que procese trafico real antes de pedir el resumen.")
        return []
    registros: List[Dict[str, Any]] = []
    corruptas = 0
    with open(ruta, encoding="utf-8") as fichero:
        for linea in fichero:
            linea = linea.strip()
            if not linea:
                continue
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                corruptas += 1
                continue
            if desde and str(registro.get("ts", "")) < desde:
                continue
            registros.append(registro)
    if corruptas:
        print(f"(se ignoraron {corruptas} lineas ilegibles)")
    return registros


def imprimir_resumen(registros: List[Dict[str, Any]]) -> None:
    """Tabla de motivos, tasa de exito y estadisticas de tamano de rostro."""
    total = len(registros)
    if total == 0:
        print("Sin registros que resumir.")
        return

    motivos = Counter(r.get("motivo", "?") for r in registros)
    exitosos = sum(1 for r in registros if r.get("exitoso"))

    print("=" * 78)
    print(" TELEMETRIA DEL MODULO DEMOGRAFICO")
    print("=" * 78)
    print(f" Tracks cerrados analizados : {total}")
    print(f" Con demografia             : {exitosos} "
          f"({100.0 * exitosos / total:.1f} %)")
    print(f" Sin demografia             : {total - exitosos} "
          f"({100.0 * (total - exitosos) / total:.1f} %)")

    print("\n" + "-" * 78)
    print(f" {'MOTIVO':<26} {'TRACKS':>7} {'%':>7}   INTERPRETACION")
    print("-" * 78)
    for motivo, cuenta in motivos.most_common():
        print(f" {motivo:<26} {cuenta:>7} {100.0 * cuenta / total:>6.1f} %   "
              f"{_INTERPRETACION.get(motivo, '')}")
    print("-" * 78)
    print(f" {'SUMA':<26} {sum(motivos.values()):>7} "
          f"{'100.0':>6} %   (debe coincidir con el total)")

    # Tamano de rostro observado: define si el modo cuerpo es obligatorio.
    anchos = sorted(float(r.get("mejor_ancho_rostro_px", 0) or 0)
                    for r in registros
                    if float(r.get("mejor_ancho_rostro_px", 0) or 0) > 0)
    print(f"\n Tracks en los que se llego a ver un rostro: {len(anchos)} "
          f"({100.0 * len(anchos) / total:.1f} %)")
    if anchos:
        def pct(p: float) -> float:
            return anchos[min(len(anchos) - 1, int(len(anchos) * p))]
        print(f"   ancho del rostro (px reales): min={anchos[0]:.0f}  "
              f"p25={pct(.25):.0f}  mediana={pct(.50):.0f}  "
              f"p75={pct(.75):.0f}  max={anchos[-1]:.0f}")
        for umbral in (25, 40, 60):
            n = sum(1 for a in anchos if a < umbral)
            print(f"   por debajo de {umbral:>3} px: {n:>4} "
                  f"({100.0 * n / len(anchos):.0f} % de los que tienen cara)")

    # Reparto por camara, util con varias camaras a la vez.
    camaras = Counter(str(r.get("camara", "?")) for r in registros)
    if len(camaras) > 1:
        print("\n Por camara:")
        for cam, n in camaras.most_common():
            ok = sum(1 for r in registros
                     if str(r.get("camara")) == cam and r.get("exitoso"))
            print(f"   {cam:<28} {n:>5} tracks, {ok:>4} con demografia "
                  f"({100.0 * ok / max(1, n):.1f} %)")

    excepciones = [r for r in registros
                   if r.get("motivo") == "excepcion_estimador"]
    if excepciones:
        print(f"\n ATENCION: {len(excepciones)} track(s) con excepcion del "
              f"estimador. Revisa el log del servidor (nivel WARNING).")

    print("\n" + "=" * 78)
    print(" LECTURA")
    print("=" * 78)
    dominante = motivos.most_common(1)[0]
    print(f" Causa dominante: '{dominante[0]}' con {dominante[1]} tracks "
          f"({100.0 * dominante[1] / total:.1f} %).")
    print(f" -> {_INTERPRETACION.get(dominante[0], 'sin interpretacion')}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resumen de la telemetria del modulo demografico")
    parser.add_argument("--archivo", default=_RUTA_POR_DEFECTO,
                        help="ruta del JSONL de telemetria")
    parser.add_argument("--desde", default=None,
                        help="solo registros con ts >= este valor "
                             "(ej. 2026-07-27 o 2026-07-27T15:00:00)")
    args = parser.parse_args()

    registros = cargar(args.archivo, args.desde)
    imprimir_resumen(registros)
    return 0 if registros else 1


if __name__ == "__main__":
    sys.exit(main())
