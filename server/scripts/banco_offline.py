"""
banco_offline.py - Banco de pruebas reproducible del modulo demografico.

Corre el estimador REAL sobre los crops ya guardados en disco, sin servidor
ni camaras. Sirve para medir el techo de lo que se puede sacar de estas
imagenes y para comparar antes/despues de cualquier cambio.

Mide, por cada crop:
  * si se detecta rostro y de que ancho REAL (px del frame, sin upscale);
  * si el rostro pasa los gates de pose y calidad;
  * que diria el modelo SIN gates (prediccion cruda) frente a lo que el
    sistema deja pasar CON gates -> cuantifica cuanta cobertura cuesta la
    politica de precision;
  * si la rama corporal (sin rostro) puede clasificar;
  * el tiempo por crop.

Y genera un contact sheet: mosaico con el veredicto sobreimpreso en cada
crop, para juzgar a ojo si las estimaciones son razonables.

Uso:
    venv\\Scripts\\python.exe scripts\\banco_offline.py
    venv\\Scripts\\python.exe scripts\\banco_offline.py --sin-rostro
    venv\\Scripts\\python.exe scripts\\banco_offline.py --carpeta otra\\ruta

Por defecto usa `output/captures/persons` (crops LIMPIOS del servidor) en
lugar de la carpeta `capture/` del cliente: esta ultima lleva el banner de
genero/edad y el primer plano del rostro ya compuestos encima, que
contaminarian tanto la deteccion como la lectura visual.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from src.analityc.core.analytics.config import AnalyticsConfig  # noqa: E402
from src.analityc.core.analytics.demographics import (  # noqa: E402
    DemographicsClassifier, _AGE_BIN_CENTERS)

# Colores BGR del contact sheet, por estado del crop.
_COLOR_OK = (110, 210, 110)        # verde  - clasificado
_COLOR_CRUDO = (60, 190, 235)      # ambar  - el modelo opina, los gates no dejan
_COLOR_SIN_CARA = (150, 150, 150)  # gris   - no hay rostro
_COLOR_RECHAZO = (90, 90, 235)     # rojo   - rostro rechazado por gates


def cargar_modelos() -> DemographicsClassifier:
    """Crea el clasificador con los modelos que haya en disco.

    Avisa explicitamente de los que faltan: la rama corporal no puede
    funcionar sin `mivolo.onnx` o `par_gender.onnx`.
    """
    from src.analityc.core.person_amazona_inference import (
        _create_yunet, _build_onnx_providers, _INSIGHTFACE_GENDERAGE,
        _MIVOLO_MODEL, _PAR_MODEL)

    yunet = _create_yunet("cuda")
    print(f"  YuNet (deteccion de rostro) : "
          f"{'OK' if yunet is not None else 'NO DISPONIBLE'}")

    insight = None
    if os.path.exists(_INSIGHTFACE_GENDERAGE):
        import onnxruntime as ort
        insight = ort.InferenceSession(_INSIGHTFACE_GENDERAGE,
                                       providers=_build_onnx_providers())
        print("  InsightFace genderage       : OK")
    else:
        print("  InsightFace genderage       : NO DISPONIBLE")

    mivolo = par = None
    import onnxruntime as ort
    if os.path.exists(_MIVOLO_MODEL):
        mivolo = ort.InferenceSession(_MIVOLO_MODEL,
                                      providers=_build_onnx_providers())
        print("  MiVOLO (rama corporal)      : OK")
    else:
        print(f"  MiVOLO (rama corporal)      : NO EXISTE ({_MIVOLO_MODEL})")
    if os.path.exists(_PAR_MODEL):
        par = ort.InferenceSession(_PAR_MODEL,
                                   providers=_build_onnx_providers())
        print("  PAR (rama corporal)         : OK")
    else:
        print(f"  PAR (rama corporal)         : NO EXISTE ({_PAR_MODEL})")

    if mivolo is None and par is None:
        print("\n  AVISO: sin modelo corporal, los crops SIN ROSTRO no")
        print("  tienen ninguna via de clasificacion. Es la causa raiz")
        print("  identificada en la auditoria (Hito 1).")

    return DemographicsClassifier(
        yunet=yunet, insightface_session=insight,
        mivolo_session=mivolo, par_session=par, camera_id="banco_offline")


def analizar_crop(clf: DemographicsClassifier, imagen: np.ndarray,
                  forzar_sin_rostro: bool = False,
                  estimador: Any = None) -> Dict[str, Any]:
    """Analiza un crop y devuelve el detalle de lo que ocurre con el.

    No usa el acumulador temporal (eso es cosa del track): mide lo que se
    puede extraer de UNA imagen, que es el techo por muestra.
    """
    alto, ancho = imagen.shape[:2]
    resultado: Dict[str, Any] = {
        "ancho_crop": ancho, "alto_crop": alto,
        "rostro": False, "ancho_rostro_px": 0.0,
        "pose": None, "pasa_gates": False,
        "genero_crudo": None, "conf_genero_cruda": 0.0, "edad_cruda": None,
        "genero_sistema": None, "edad_sistema": None,
        "estado": "sin_rostro", "motivo": "",
        "solo_cuerpo": False,
    }

    # ── Gate de tamano de persona (el primero del pipeline real) ──
    if (ancho < clf._cfg.DEMO_MIN_PERSON_BBOX_W
            or alto < clf._cfg.DEMO_MIN_PERSON_BBOX_H):
        resultado["estado"] = "rechazado"
        resultado["motivo"] = f"bbox {ancho}x{alto} < minimo"
        return resultado

    cara = None
    if not forzar_sin_rostro:
        cara, pose, ancho_nativo = clf._detect_face_with_pose(imagen)
        resultado["pose"] = (None if pose is None
                             else tuple(round(float(v), 1) for v in pose))
        resultado["ancho_rostro_px"] = round(float(ancho_nativo), 1)
        resultado["rostro"] = cara is not None

    if cara is None:
        # ── Rama corporal: la unica via cuando no hay rostro ──
        resultado["solo_cuerpo"] = True
        # Ruta PRINCIPAL (Hito 4): MiVOLO en modo cuerpo.
        if estimador is not None and estimador.disponible:
            m = estimador.estimar(0, imagen, None)
            if m is not None and m.es_valida():
                resultado["genero_crudo"] = m.genero
                resultado["conf_genero_cruda"] = m.conf_genero
                resultado["edad_cruda"] = m.edad_anios
                resultado["estado"] = "clasificado"
                resultado["genero_sistema"] = m.genero
                resultado["edad_sistema"] = m.rango_edad
                return resultado
            resultado["estado"] = "rechazado"
            resultado["motivo"] = (m.motivo_descarte if m is not None
                                   and m.motivo_descarte
                                   else "MiVOLO sin muestra valida")
            return resultado
        cuerpo = clf._infer_body_only(imagen)
        if cuerpo is None:
            resultado["estado"] = "sin_rostro"
            resultado["motivo"] = ("sin modelo corporal"
                                   if not forzar_sin_rostro
                                   else "sin modelo corporal (--sin-rostro)")
            return resultado
        probs, edad = cuerpo
        idx = int(np.argmax(probs))
        resultado["genero_crudo"] = ("Hombre", "Mujer")[idx]
        resultado["conf_genero_cruda"] = round(float(probs[idx]), 3)
        resultado["edad_cruda"] = (None if edad is None
                                   else round(float(edad), 1))
        if float(probs[idx]) >= clf._cfg.DEMO_BODY_MIN_GENDER_CONF:
            resultado["estado"] = "clasificado"
            resultado["genero_sistema"] = resultado["genero_crudo"]
            resultado["edad_sistema"] = (
                AnalyticsConfig.coarse_age_from_value(edad)
                if edad is not None else "Desconocido")
        else:
            resultado["estado"] = "rechazado"
            resultado["motivo"] = (f"conf corporal "
                                   f"{probs[idx]:.2f} < "
                                   f"{clf._cfg.DEMO_BODY_MIN_GENDER_CONF}")
        return resultado

    # ── Hay rostro: prediccion CRUDA (lo que el modelo opina, sin gates) ──
    try:
        g, a, _c = clf._infer_insightface_tta(cara)
        if g is not None:
            idx = int(np.argmax(g))
            resultado["genero_crudo"] = ("Hombre", "Mujer")[idx]
            resultado["conf_genero_cruda"] = round(float(g[idx]), 3)
        if a is not None:
            resultado["edad_cruda"] = round(
                float(np.sum(np.asarray(a, dtype=float) * _AGE_BIN_CENTERS)), 1)
    except Exception as exc:  # noqa: BLE001
        resultado["motivo"] = f"excepcion: {type(exc).__name__}"

    # ── Gates del sistema sobre ese mismo rostro ──
    motivos: List[str] = []
    if resultado["pose"] is not None:
        yaw, pitch, roll = resultado["pose"]
        if abs(yaw) > clf._cfg.DEMO_POSE_MAX_YAW_DEG:
            motivos.append(f"yaw {yaw:.0f}")
        if abs(pitch) > clf._cfg.DEMO_POSE_MAX_PITCH_DEG:
            motivos.append(f"pitch {pitch:.0f}")
        if abs(roll) > clf._cfg.DEMO_POSE_MAX_ROLL_DEG:
            motivos.append(f"roll {roll:.0f}")
    if not clf._face_passes_quality(cara):
        motivos.append("calidad")

    brackets = clf._cfg.DEMO_ADAPTIVE_BRACKETS
    minimo = min(b[0] for b in brackets) if brackets else 0
    ancho_rostro = resultado["ancho_rostro_px"]
    conf_exigida = None
    if ancho_rostro < minimo:
        motivos.append(f"rostro {ancho_rostro:.0f}px < {minimo}px")
    elif resultado["genero_crudo"] is None:
        # El modelo no llego a emitir prediccion: las variantes de TTA no
        # coincidieron entre si (DEMO_TTA_REQUIRE_AGREEMENT) o el ensemble
        # discrepo. Nombrarlo asi evita el enganoso "conf 0.00 < umbral".
        motivos.append("TTA/ensemble sin acuerdo")
    else:
        for fw_min, conf_min, _margen in brackets:
            if ancho_rostro >= fw_min:
                conf_exigida = conf_min
                break
        if (conf_exigida is not None
                and resultado["conf_genero_cruda"] < conf_exigida):
            motivos.append(f"conf {resultado['conf_genero_cruda']:.3f}"
                           f"<{conf_exigida}")

    if motivos:
        # ── CASCADA: la rama facial estricta rechaza -> MiVOLO ──
        # Antes estos crops se perdian: tenian rostro, no pasaban los gates
        # de "precision maxima" y nadie los recogia. Ahora van a MiVOLO con
        # ENTRADA DUAL (cara + cuerpo), que es su modo mas preciso.
        if estimador is not None and estimador.disponible:
            m = estimador.estimar(0, imagen, cara)
            if m is not None and m.es_valida():
                resultado["genero_crudo"] = m.genero
                resultado["conf_genero_cruda"] = m.conf_genero
                resultado["edad_cruda"] = m.edad_anios
                resultado["estado"] = "clasificado"
                resultado["genero_sistema"] = m.genero
                resultado["edad_sistema"] = m.rango_edad
                resultado["motivo"] = ("facial rechazo ("
                                       + ", ".join(motivos) + ") -> MiVOLO")
                return resultado
        resultado["estado"] = "rechazado"
        resultado["motivo"] = ", ".join(motivos)
    else:
        resultado["pasa_gates"] = True
        resultado["estado"] = "clasificado"
        resultado["genero_sistema"] = resultado["genero_crudo"]
        resultado["edad_sistema"] = (
            AnalyticsConfig.age_range_from_value(int(resultado["edad_cruda"]))
            if resultado["edad_cruda"] is not None else "Desconocido")
    return resultado


def dibujar_ficha(imagen: np.ndarray, r: Dict[str, Any],
                  ancho_celda: int, alto_celda: int) -> np.ndarray:
    """Miniatura del crop con el veredicto sobreimpreso."""
    alto_img = alto_celda - 46          # deja sitio al pie de texto
    escala = min(ancho_celda / imagen.shape[1], alto_img / imagen.shape[0])
    nw = max(1, int(imagen.shape[1] * escala))
    nh = max(1, int(imagen.shape[0] * escala))
    mini = cv2.resize(imagen, (nw, nh), interpolation=cv2.INTER_AREA)

    celda = np.full((alto_celda, ancho_celda, 3), 24, np.uint8)
    x0 = (ancho_celda - nw) // 2
    celda[0:nh, x0:x0 + nw] = mini

    estado = r["estado"]
    if estado == "clasificado":
        color = _COLOR_OK
        linea1 = f"{r['genero_sistema']} {r['edad_sistema'] or ''}".strip()
    elif estado == "sin_rostro":
        color = _COLOR_SIN_CARA
        linea1 = "sin rostro"
    else:
        # Rechazado: si el modelo igualmente opino, se muestra en ambar
        # para ver cuanta cobertura cuesta la politica de gates.
        color = _COLOR_CRUDO if r["genero_crudo"] else _COLOR_RECHAZO
        linea1 = (f"[{r['genero_crudo']} {r['conf_genero_cruda']:.2f}]"
                  if r["genero_crudo"] else "rechazado")

    cv2.rectangle(celda, (0, nh), (ancho_celda, nh + 2), color, -1)
    cv2.putText(celda, linea1[:26], (4, alto_celda - 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    detalle = (f"cara {r['ancho_rostro_px']:.0f}px"
               if r["rostro"] else "cuerpo")
    if r["motivo"]:
        detalle += f" | {r['motivo']}"
    cv2.putText(celda, detalle[:34], (4, alto_celda - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.34, (185, 185, 185), 1, cv2.LINE_AA)
    return celda


def construir_contact_sheet(fichas: List[np.ndarray], columnas: int,
                            ruta: str) -> None:
    """Compone el mosaico y lo guarda."""
    if not fichas:
        print("  Sin fichas que componer.")
        return
    alto_celda, ancho_celda = fichas[0].shape[:2]
    filas = (len(fichas) + columnas - 1) // columnas
    hoja = np.full((filas * alto_celda, columnas * ancho_celda, 3), 14, np.uint8)
    for i, ficha in enumerate(fichas):
        fila, col = divmod(i, columnas)
        hoja[fila * alto_celda:(fila + 1) * alto_celda,
             col * ancho_celda:(col + 1) * ancho_celda] = ficha
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    cv2.imwrite(ruta, hoja, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"  Contact sheet: {ruta}  ({hoja.shape[1]}x{hoja.shape[0]} px)")


def imprimir_informe(resultados: List[Dict[str, Any]],
                     tiempos: List[float]) -> None:
    """Tabla final del banco."""
    total = len(resultados)
    if total == 0:
        print("Sin crops que analizar.")
        return

    con_rostro = [r for r in resultados if r["rostro"]]
    clasificados = [r for r in resultados if r["estado"] == "clasificado"]
    opinables = [r for r in resultados if r["genero_crudo"]]
    anchos = sorted(r["ancho_rostro_px"] for r in con_rostro
                    if r["ancho_rostro_px"] > 0)

    print("\n" + "=" * 78)
    print(" RESULTADOS DEL BANCO OFFLINE")
    print("=" * 78)
    print(f" Crops analizados                : {total}")
    print(f" Con rostro DETECTADO            : {len(con_rostro)} "
          f"({100.0 * len(con_rostro) / total:.1f} %)")
    print(f" Clasificados por el sistema     : {len(clasificados)} "
          f"({100.0 * len(clasificados) / total:.1f} %)")
    print(f" El modelo opina (con o sin gates): {len(opinables)} "
          f"({100.0 * len(opinables) / total:.1f} %)")

    if anchos:
        print(f"\n Ancho del rostro (px reales):")
        print(f"   min={anchos[0]:.0f}  mediana={statistics.median(anchos):.0f}"
              f"  max={anchos[-1]:.0f}")
        for umbral in (25, 40, 60, 100):
            n = sum(1 for a in anchos if a < umbral)
            print(f"   por debajo de {umbral:>3} px : {n:>3} / {len(anchos)} "
                  f"({100.0 * n / len(anchos):.0f} %)")

    # Rostro UTILIZABLE = detectado y ademas por encima del minimo del bracket.
    minimo = min(b[0] for b in AnalyticsConfig.DEMO_ADAPTIVE_BRACKETS)
    utilizables = [a for a in anchos if a >= minimo]
    tasa_util = 100.0 * len(utilizables) / total
    print(f"\n Rostro UTILIZABLE (>= {minimo} px): {len(utilizables)} / {total} "
          f"({tasa_util:.1f} %)")
    if tasa_util < 20.0:
        print("   -> POR DEBAJO DEL 20 %: el modo cuerpo es OBLIGATORIO.")
    else:
        print("   -> Por encima del 20 %: la rama facial aun aporta.")

    motivos = Counter(r["motivo"].split(",")[0].strip()
                      for r in resultados if r["motivo"])
    if motivos:
        print("\n Motivos de descarte (primero de cada crop):")
        for motivo, n in motivos.most_common(12):
            print(f"   {motivo:<44} {n:>3}")

    # Cuanta cobertura cuesta la politica de gates.
    perdidos = [r for r in resultados
                if r["genero_crudo"] and r["estado"] != "clasificado"]
    if perdidos:
        print(f"\n Crops donde el modelo SI opina pero los gates lo "
              f"descartan: {len(perdidos)} "
              f"({100.0 * len(perdidos) / total:.1f} % del total)")
        confs = sorted(r["conf_genero_cruda"] for r in perdidos)
        print(f"   confianza de esas predicciones: min={confs[0]:.2f} "
              f"mediana={statistics.median(confs):.2f} max={confs[-1]:.2f}")

    if tiempos:
        print(f"\n Tiempo por crop: media={statistics.mean(tiempos)*1000:.0f} ms"
              f"  mediana={statistics.median(tiempos)*1000:.0f} ms"
              f"  total={sum(tiempos):.1f} s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Banco offline del modulo demografico")
    parser.add_argument("--carpeta",
                        default=os.path.join(_RAIZ, "output", "captures",
                                             "persons"),
                        help="carpeta con los crops a analizar")
    parser.add_argument("--salida",
                        default=os.path.join(_RAIZ, "output",
                                             "banco_offline_contact_sheet.jpg"),
                        help="ruta del contact sheet")
    parser.add_argument("--informe",
                        default=os.path.join(_RAIZ, "output",
                                             "banco_offline.json"),
                        help="ruta del informe JSON con el detalle por crop")
    parser.add_argument("--limite", type=int, default=0,
                        help="analizar como mucho N crops (0 = todos)")
    parser.add_argument("--columnas", type=int, default=10,
                        help="columnas del contact sheet")
    parser.add_argument("--sin-mivolo", action="store_true",
                        help="no usar MiVOLO: mide la cobertura ANTES del "
                             "Hito 4 (para la comparativa antes/despues)")
    parser.add_argument("--sin-rostro", action="store_true",
                        help="ignora el detector de rostro y fuerza la rama "
                             "corporal (para validar el modo cuerpo)")
    args = parser.parse_args()

    print("=" * 78)
    print(" BANCO OFFLINE - MODULO DEMOGRAFICO")
    print("=" * 78)
    print(f" Carpeta: {args.carpeta}")
    if args.sin_rostro:
        print(" MODO: --sin-rostro (se ignora el detector facial)")

    rutas = sorted(glob.glob(os.path.join(args.carpeta, "*.jpg")))
    if args.limite > 0:
        rutas = rutas[:args.limite]
    if not rutas:
        print(f"\n No se encontraron crops .jpg en {args.carpeta}")
        return 1
    print(f" Crops encontrados: {len(rutas)}")

    print("\n Cargando modelos:")
    clf = cargar_modelos()
    clf._cfg._overrides["DEMO_DEBUG_REJECTIONS"] = False

    # Ruta principal del Hito 4: MiVOLO v2 en modo cuerpo. Con --sin-mivolo
    # se mide la cobertura ANTERIOR, para la comparativa antes/despues.
    estimador = None
    if not args.sin_mivolo:
        from src.analityc.core.analytics.estimador_edad_genero import (
            EstimadorEdadGenero)
        estimador = EstimadorEdadGenero()
        print(f"  MiVOLO v2 (modo cuerpo)     : "
              f"{'OK (' + estimador.dispositivo + ')' if estimador.disponible
                 else 'NO DISPONIBLE'}")

    print(f"\n Analizando {len(rutas)} crops...")
    resultados: List[Dict[str, Any]] = []
    fichas: List[np.ndarray] = []
    tiempos: List[float] = []
    inicio = time.perf_counter()

    for i, ruta in enumerate(rutas):
        imagen = cv2.imread(ruta)
        if imagen is None:
            continue
        t0 = time.perf_counter()
        r = analizar_crop(clf, imagen, forzar_sin_rostro=args.sin_rostro,
                          estimador=estimador)
        tiempos.append(time.perf_counter() - t0)
        r["archivo"] = os.path.basename(ruta)
        resultados.append(r)
        fichas.append(dibujar_ficha(imagen, r, 190, 300))
        if (i + 1) % 25 == 0:
            print(f"   {i + 1}/{len(rutas)}...")

    print(f" Analisis terminado en {time.perf_counter() - inicio:.1f} s")

    imprimir_informe(resultados, tiempos)

    print("\n Generando salidas:")
    construir_contact_sheet(fichas, args.columnas, args.salida)
    try:
        os.makedirs(os.path.dirname(args.informe), exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fichero:
            json.dump(resultados, fichero, ensure_ascii=False, indent=1)
        print(f"  Informe JSON : {args.informe}")
    except Exception as exc:  # noqa: BLE001
        print(f"  No se pudo escribir el informe: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
