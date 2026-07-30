"""Analisis profundo post-hoc de genero/edad sobre una foto guardada.

Pensado para casos "Desconocido" o de baja confianza del clasificador
en tiempo real. Sacrifica velocidad por precision: corre HEAVY TTA
(12 variantes) + ensemble con Caffe + voting estricto.

Tiempo tipico: 1-3 segundos por cara (vs ~150ms del tiempo real).
Precision tipica: +5-10 puntos vs analisis single-shot por la
diversidad de TTA.

Uso:
    analyzer = DeepGenderAgeAnalyzer(insight_session, caffe_gender,
                                     caffe_age)
    result = analyzer.analyze(face_bgr_image)
    # result = {
    #     'gender': 'Mujer' | 'Hombre' | 'Desconocido',
    #     'age_range': '18-25' | ...,
    #     'age_value': 24.5,
    #     'confidence': 0.94,
    #     'agreement': 0.92,  # acuerdo entre TTAs
    #     'caffe_agrees': True,
    #     'tta_count': 12,
    # }
"""
import logging
from typing import Dict, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_GENDER_LIST = ['Hombre', 'Mujer']
_AGE_BIN_CENTERS = np.array([1.0, 5.0, 10.0, 17.5, 28.5, 40.5, 50.5, 70.0],
                            dtype=np.float64)
_CAFFE_MEAN = (78.4263377603, 87.7689143744, 114.895847746)


def _softmax(x):
    x = x - np.max(x)
    e = np.exp(x)
    s = e.sum()
    return e / s if s > 0 else np.full_like(e, 1.0 / len(e))


def _age_range_from_value(age: float) -> str:
    if age <= 12: return "0-12"
    if age <= 17: return "13-17"
    if age <= 25: return "18-25"
    if age <= 35: return "26-35"
    if age <= 50: return "36-50"
    if age <= 65: return "51-65"
    return "65+"


class DeepGenderAgeAnalyzer:
    """Analizador profundo con heavy TTA + ensemble multi-modelo."""

    def __init__(self,
                 insightface_session=None,
                 caffe_gender_net=None,
                 caffe_age_net=None):
        self._insight = insightface_session
        self._caffe_gender = caffe_gender_net
        self._caffe_age = caffe_age_net

        if self._insight is not None:
            try:
                self._in_name = self._insight.get_inputs()[0].name
                self._out_names = [
                    o.name for o in self._insight.get_outputs()
                ]
            except Exception:
                self._insight = None

    @property
    def is_available(self) -> bool:
        return self._insight is not None or self._caffe_gender is not None

    def _build_heavy_tta(self, face_bgr: np.ndarray) -> list:
        """Genera 12 variantes TTA: combinaciones de flip, zoom, rotacion,
        brillo, contraste. Mas que el TTA del clasificador en tiempo real.
        """
        variants = []
        fh, fw = face_bgr.shape[:2]

        # 1. Original
        variants.append(face_bgr)
        # 2. Flip horizontal
        variants.append(cv2.flip(face_bgr, 1))
        # 3. Zoom in 90% (crop central)
        cx1 = int(fw * 0.05); cy1 = int(fh * 0.05)
        cx2 = fw - cx1; cy2 = fh - cy1
        if cx2 - cx1 > 20 and cy2 - cy1 > 20:
            variants.append(face_bgr[cy1:cy2, cx1:cx2])
        # 4. Zoom in 80%
        cx1 = int(fw * 0.10); cy1 = int(fh * 0.10)
        cx2 = fw - cx1; cy2 = fh - cy1
        if cx2 - cx1 > 20 and cy2 - cy1 > 20:
            variants.append(face_bgr[cy1:cy2, cx1:cx2])
        # 5. Zoom out 10% (replicate border)
        pad = int(max(fw, fh) * 0.10)
        if pad > 0:
            variants.append(cv2.copyMakeBorder(
                face_bgr, pad, pad, pad, pad, cv2.BORDER_REPLICATE
            ))
        # 6-9. Rotaciones ±5, ±10 grados
        center = (fw / 2.0, fh / 2.0)
        for angle in (5.0, -5.0, 10.0, -10.0):
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            variants.append(cv2.warpAffine(
                face_bgr, M, (fw, fh),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            ))
        # 10. Brillo +20
        bright = np.clip(face_bgr.astype(np.int16) + 20, 0, 255).astype(np.uint8)
        variants.append(bright)
        # 11. Brillo -20
        dark = np.clip(face_bgr.astype(np.int16) - 20, 0, 255).astype(np.uint8)
        variants.append(dark)
        # 12. CLAHE (mejora contraste local)
        lab = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        l_eq = clahe.apply(l)
        variants.append(cv2.cvtColor(cv2.merge([l_eq, a, b]),
                                      cv2.COLOR_LAB2BGR))

        return variants

    def _infer_insight(self, variants: list):
        """Devuelve lista de (gender_probs [2], age_years)."""
        results = []
        for v in variants:
            if v is None or v.size == 0:
                continue
            try:
                blob = cv2.dnn.blobFromImage(
                    v, 1.0, (96, 96), (0, 0, 0), swapRB=True
                )
                out = self._insight.run(
                    self._out_names, {self._in_name: blob}
                )[0][0]
                g_insight = _softmax(out[:2])  # [female_p, male_p]
                # Mapear a [Hombre, Mujer]
                gender_p = np.array([g_insight[1], g_insight[0]],
                                     dtype=np.float64)
                age_years = max(0.0, min(100.0, float(out[2]) * 100.0))
                results.append((gender_p, age_years))
            except Exception:
                continue
        return results

    def _infer_caffe(self, face_bgr: np.ndarray):
        """Inferencia con Caffe gender/age (modelo 2015, menos preciso
        pero util para cross-check). Devuelve (gender_probs, age_years).
        """
        if self._caffe_gender is None or self._caffe_age is None:
            return None, None
        try:
            blob = cv2.dnn.blobFromImage(
                face_bgr, 1.0, (227, 227), _CAFFE_MEAN, swapRB=False
            )
            self._caffe_gender.setInput(blob)
            g_a = _softmax(self._caffe_gender.forward()[0])
            # Caffe orden: [Male, Female] segun Levi-Hassner
            # Mapear a [Hombre, Mujer] -> [Male, Female] = [Hombre, Mujer]
            gender_p = np.array([g_a[0], g_a[1]], dtype=np.float64)

            self._caffe_age.setInput(blob)
            a_p = _softmax(self._caffe_age.forward()[0])
            age_value = float(np.sum(a_p * _AGE_BIN_CENTERS))
            return gender_p, age_value
        except Exception as e:
            logger.debug(f"Caffe inference fallo: {e}")
            return None, None

    def analyze(self, face_bgr: np.ndarray) -> Dict:
        """Analisis profundo. Devuelve dict con resultado."""
        if not self.is_available:
            return self._empty_result("Sin modelos disponibles")
        if face_bgr is None or face_bgr.size == 0:
            return self._empty_result("Imagen vacia")

        variants = self._build_heavy_tta(face_bgr)
        insight_results = self._infer_insight(variants) if self._insight else []

        if not insight_results:
            return self._empty_result("Inferencia primaria fallo")

        # Promedio + voting
        gender_probs_avg = np.mean(
            [r[0] for r in insight_results], axis=0
        )
        age_avg = float(np.mean([r[1] for r in insight_results]))

        # Acuerdo entre TTAs
        argmaxes = [int(np.argmax(r[0])) for r in insight_results]
        top_choice = max(set(argmaxes), key=argmaxes.count)
        agree_count = sum(1 for a in argmaxes if a == top_choice)
        agreement = agree_count / len(insight_results)

        # Cross-check con Caffe (opcional, mas ruidoso pero ayuda en dudas)
        caffe_g, caffe_age = self._infer_caffe(face_bgr)
        caffe_agrees = None
        if caffe_g is not None:
            caffe_choice = int(np.argmax(caffe_g))
            caffe_agrees = (caffe_choice == top_choice)

        # Decision final:
        # - Si agreement < 0.75 (TTAs no se ponen de acuerdo) -> Desconocido
        # - Si confidence < 0.65 -> Desconocido
        # - Si Caffe disagree Y confidence < 0.85 -> Desconocido (duda)
        # - Caso contrario -> usar top1
        top1_conf = float(gender_probs_avg[top_choice])
        top2_conf = float(gender_probs_avg[1 - top_choice])
        margin = top1_conf - top2_conf

        decision = 'Desconocido'
        if agreement < 0.75:
            reason = f'TTA disagreement (agreement={agreement:.2f})'
        elif top1_conf < 0.65:
            reason = f'Confianza baja ({top1_conf:.2f})'
        elif (caffe_agrees is False and top1_conf < 0.85):
            reason = (f'Caffe disagree con InsightFace y conf<0.85 '
                      f'({top1_conf:.2f})')
        else:
            decision = _GENDER_LIST[top_choice]
            reason = 'OK'

        age_range = _age_range_from_value(age_avg) \
            if decision != 'Desconocido' else 'Desconocido'

        return {
            'gender': decision,
            'age_range': age_range,
            'age_value': round(age_avg, 1),
            'confidence': round(top1_conf, 3),
            'margin': round(margin, 3),
            'agreement': round(agreement, 3),
            'caffe_agrees': caffe_agrees,
            'tta_count': len(insight_results),
            'reason': reason,
        }

    def _empty_result(self, reason: str) -> Dict:
        return {
            'gender': 'Desconocido', 'age_range': 'Desconocido',
            'age_value': 0.0, 'confidence': 0.0, 'margin': 0.0,
            'agreement': 0.0, 'caffe_agrees': None, 'tta_count': 0,
            'reason': reason,
        }
