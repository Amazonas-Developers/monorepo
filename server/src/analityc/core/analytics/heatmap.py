"""
analytics/heatmap.py - Mapa de calor de ocupacion/transito de personas.

Acumula la posicion de los PIES de cada persona detectada (centro-x, base
del bbox) en una grilla reducida y produce:

  * Overlay en vivo sobre el frame (colormap JET con alpha) -> lo ve el
    CLIENTE cuando activa "Mapa de calor".
  * Snapshots periodicos a disco (PNG + JSON) -> los lee el DASHBOARD
    (webapp) sin acoplarse al proceso de inferencia.
  * Zonas calientes (top-K celdas) en coordenadas relativas 0..1 -> van
    en el metadata del payload.

Dos acumuladores en paralelo:
  - `reciente`: con decaimiento exponencial (half-life configurable).
    Muestra la actividad de los ultimos minutos.
  - `total`: acumulado de toda la sesion (sin decay).

Diseno: O(1) por persona (estampa gaussiana 5x5 pre-computada sobre la
grilla); el render solo ocurre si el overlay esta activo o toca snapshot.
"""
from __future__ import annotations

import json
import os
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import AnalyticsConfig

logger = logging.getLogger(__name__)


def _safe_camera_name(camera_id: Any) -> str:
    """Sanitiza un camera_id (puede ser UUID) para usarlo como filename."""
    s = str(camera_id)
    return "".join(c for c in s if c.isalnum() or c in "_-")[:64] or "cam"


class HeatmapAccumulator:
    """Acumulador de mapa de calor por camara."""

    def __init__(
        self,
        grid_w: int = None,
        grid_h: int = None,
        decay_half_life_s: float = None,
    ) -> None:
        self.grid_w = int(grid_w or AnalyticsConfig.HEATMAP_GRID_W)
        self.grid_h = int(grid_h or AnalyticsConfig.HEATMAP_GRID_H)
        self.decay_half_life_s = float(
            decay_half_life_s or AnalyticsConfig.HEATMAP_DECAY_HALF_LIFE_S)

        # Grillas float32: reciente (con decay), total (sesion) y horaria
        # (se vacia cada hora al guardar el snapshot historico).
        self._recent = np.zeros((self.grid_h, self.grid_w), np.float32)
        self._total = np.zeros((self.grid_h, self.grid_w), np.float32)
        self._hour = np.zeros((self.grid_h, self.grid_w), np.float32)
        self._hour_samples = 0
        self._hour_stamp = self._current_stamp()
        self._samples = 0
        self._last_decay_ts = time.time()
        self._last_snapshot_ts = 0.0
        self._last_bg_ts = 0.0
        self._last_state_ts = 0.0
        self._started_ts = time.time()

        # Estampa gaussiana (suaviza la huella de cada persona). RADIO y
        # dispersion configurables (Hito 7): kernel de lado 2*r+1.
        self._stamp_r = max(1, int(AnalyticsConfig.HEATMAP_STAMP_RADIUS))
        _ksize = 2 * self._stamp_r + 1
        _sigma = float(AnalyticsConfig.HEATMAP_STAMP_SIGMA)
        k = cv2.getGaussianKernel(_ksize, _sigma)
        self._stamp = (k @ k.T).astype(np.float32)
        self._stamp /= self._stamp.max()

    # ── Acumulacion ───────────────────────────────────────────────────

    def add_person(self, box: Any, frame_w: int, frame_h: int) -> None:
        """Registra una persona. `box` = (x1, y1, x2, y2) en px del frame.

        Se usa la posicion de los PIES (centro-x, y2): para camaras
        frontales/laterales representa el punto del piso que ocupa la
        persona; en cenital coincide con el borde inferior del cuerpo.
        """
        if frame_w <= 0 or frame_h <= 0:
            return
        try:
            x1, y1, x2, y2 = [float(v) for v in box]
        except (TypeError, ValueError):
            return
        cx = (x1 + x2) / 2.0
        fy = min(y2, frame_h - 1)
        gx = int(cx / frame_w * self.grid_w)
        gy = int(fy / frame_h * self.grid_h)
        if not (0 <= gx < self.grid_w and 0 <= gy < self.grid_h):
            return

        self._apply_decay()

        # Estampar gaussiana centrada en (gy, gx), recortada a bordes.
        r = self._stamp_r
        y0, y1_ = max(0, gy - r), min(self.grid_h, gy + r + 1)
        x0, x1_ = max(0, gx - r), min(self.grid_w, gx + r + 1)
        sy0, sx0 = y0 - (gy - r), x0 - (gx - r)
        patch = self._stamp[sy0:sy0 + (y1_ - y0), sx0:sx0 + (x1_ - x0)]
        self._recent[y0:y1_, x0:x1_] += patch
        self._total[y0:y1_, x0:x1_] += patch
        self._hour[y0:y1_, x0:x1_] += patch
        self._samples += 1
        self._hour_samples += 1

    def _apply_decay(self) -> None:
        """Decaimiento exponencial de la grilla `reciente` segun half-life."""
        now = time.time()
        dt = now - self._last_decay_ts
        if dt < 1.0:
            return
        if self.decay_half_life_s > 0:
            factor = 0.5 ** (dt / self.decay_half_life_s)
            self._recent *= factor
        self._last_decay_ts = now

    # ── Render ────────────────────────────────────────────────────────

    def render_overlay(
        self,
        frame: np.ndarray,
        alpha: float = None,
        use_total: bool = False,
    ) -> np.ndarray:
        """Devuelve el frame con el mapa de calor mezclado encima.

        Solo tine las zonas con actividad (>5% del maximo); el resto del
        frame queda intacto. No modifica el frame de entrada.
        """
        if frame is None or frame.size == 0 or self._samples == 0:
            return frame
        grid = self._total if use_total else self._recent
        return self._render_grid(frame, grid, alpha)

    def _render_grid(
        self,
        frame: np.ndarray,
        grid: np.ndarray,
        alpha: float = None,
    ) -> np.ndarray:
        """Mezcla una grilla arbitraria (reciente/total/horaria) sobre el frame."""
        alpha = float(alpha if alpha is not None
                      else AnalyticsConfig.HEATMAP_OVERLAY_ALPHA)
        gmax = float(grid.max())
        if gmax <= 0 or frame is None or frame.size == 0:
            return frame

        h, w = frame.shape[:2]
        norm = (grid / gmax * 255.0).astype(np.uint8)
        heat = cv2.resize(norm, (w, h), interpolation=cv2.INTER_LINEAR)
        heat = cv2.GaussianBlur(heat, (0, 0), sigmaX=max(3.0, w / 200.0))
        color = cv2.applyColorMap(heat, cv2.COLORMAP_JET)

        # Mezclar solo donde hay actividad real
        mask = heat > 12  # ~5% del maximo
        out = frame.copy()
        blended = cv2.addWeighted(color, alpha, frame, 1.0 - alpha, 0)
        out[mask] = blended[mask]
        return out

    def export_image(self, path: str,
                     background: Optional[np.ndarray] = None,
                     use_total: bool = True) -> bool:
        """Exporta el mapa de calor a una imagen ON-DEMAND (Hito 7).

        A diferencia de maybe_save_snapshot (periodico/throttled), esto escribe
        cuando se le pide -> util para el reporte (Hito 8) o un boton de export.

        Args:
            path: ruta destino (.png/.jpg). Escritura atomica.
            background: frame de referencia para superponer el calor; si None,
                se renderiza sobre un lienzo negro proporcional a la grilla.
            use_total: True usa el acumulado TOTAL de sesion; False el reciente.

        Returns:
            True si se guardo correctamente.
        """
        try:
            if background is not None and background.size > 0:
                img = self.render_overlay(background, use_total=use_total)
            else:
                canvas = np.zeros((self.grid_h * 10, self.grid_w * 10, 3),
                                  np.uint8)
                img = self.render_overlay(canvas, use_total=use_total)
            if img is None or img.size == 0:
                return False
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            root, ext = os.path.splitext(path)
            tmp = f"{root}.tmp{ext or '.png'}"
            ok = cv2.imwrite(tmp, img)
            if ok:
                os.replace(tmp, path)
            return bool(ok)
        except Exception as exc:  # noqa: BLE001 - no tumbar el frame loop
            logger.debug("Heatmap export_image fallo: %s", exc)
            return False

    # ── Zonas calientes ───────────────────────────────────────────────

    def top_zones(self, k: int = None) -> List[Dict[str, float]]:
        """Top-K celdas mas calientes del TOTAL en coords relativas 0..1.

        Cada zona: {'x': cx_rel, 'y': cy_rel, 'intensidad': 0..1}.
        Se suprime el vecindario de cada maximo para no devolver K celdas
        contiguas de la misma mancha.
        """
        k = int(k or AnalyticsConfig.HEATMAP_TOP_ZONES)
        if self._samples == 0:
            return []
        return self._zones_from(self._total, k)

    def _zones_from(self, source: np.ndarray, k: int) -> List[Dict[str, float]]:
        """Top-K zonas de una grilla arbitraria (mayor a menor intensidad)."""
        grid = source.copy()
        gmax = float(grid.max())
        if gmax <= 0:
            return []
        zones: List[Dict[str, float]] = []
        r = 3  # radio de supresion (celdas)
        for _ in range(k):
            idx = int(np.argmax(grid))
            gy, gx = divmod(idx, self.grid_w)
            val = float(grid[gy, gx])
            if val <= 0:
                break
            zones.append({
                "x": round((gx + 0.5) / self.grid_w, 4),
                "y": round((gy + 0.5) / self.grid_h, 4),
                "intensidad": round(val / gmax, 3),
            })
            y0, y1_ = max(0, gy - r), min(self.grid_h, gy + r + 1)
            x0, x1_ = max(0, gx - r), min(self.grid_w, gx + r + 1)
            grid[y0:y1_, x0:x1_] = 0.0
        return zones

    def get_stats(self) -> Dict[str, Any]:
        return {
            "muestras": int(self._samples),
            "zonas_calientes": self.top_zones(),
            "desde": self._started_ts,
        }

    # ── Persistencia del acumulado (1-ago-2026) ───────────────────────
    #
    # Antes el TOTAL vivia solo en memoria: al cerrar la camara o reiniciar
    # el servidor, la sesion siguiente empezaba de cero y el siguiente
    # snapshot PISABA el PNG con esa sesion vacia. Guardando las grillas
    # crudas, la camara CONTINUA donde iba — que es lo que significa "el
    # mapa de calor debe permanecer aunque las camaras se cierren".

    @staticmethod
    def _ruta_estado(camera_id: Any, out_dir: str = None) -> str:
        out_dir = out_dir or AnalyticsConfig.HEATMAP_DIR
        return os.path.join(out_dir, "state",
                            f"{_safe_camera_name(camera_id)}.npz")

    def guardar_estado(self, camera_id: Any, out_dir: str = None) -> bool:
        """Grillas crudas (total + hora en curso) a disco. Atomico, nunca
        lanza: un fallo de disco no debe tumbar el frame loop."""
        if self._samples == 0:
            return False
        try:
            ruta = self._ruta_estado(camera_id, out_dir)
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            tmp = ruta + ".tmp.npz"
            np.savez_compressed(
                tmp,
                total=self._total.astype(np.float32),
                hour=self._hour.astype(np.float32),
                samples=np.int64(self._samples),
                hour_samples=np.int64(self._hour_samples),
                hour_stamp=np.bytes_(self._hour_stamp.encode('ascii')),
                grid=np.array([self.grid_w, self.grid_h], np.int64))
            os.replace(tmp, ruta)
            self._last_state_ts = time.time()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Heatmap guardar_estado fallo: %s", exc)
            return False

    def cargar_estado(self, camera_id: Any, out_dir: str = None) -> bool:
        """Restaura el acumulado persistido, si existe y las grillas encajan.

        La hora restaurada conserva su `hour_stamp`: si ya es OTRA hora, el
        proximo snapshot cierra aquella hora interrumpida en el historico
        (via _maybe_roll_hour), que es justo lo que se perdia antes."""
        try:
            ruta = self._ruta_estado(camera_id, out_dir)
            if not os.path.isfile(ruta):
                return False
            with np.load(ruta) as datos:
                gw, gh = (int(v) for v in datos['grid'])
                if (gw, gh) != (self.grid_w, self.grid_h):
                    logger.info(
                        "Heatmap estado de %s descartado: grilla %sx%s != "
                        "%sx%s", camera_id, gw, gh, self.grid_w, self.grid_h)
                    return False
                self._total = datos['total'].astype(np.float32)
                self._hour = datos['hour'].astype(np.float32)
                self._samples = int(datos['samples'])
                self._hour_samples = int(datos['hour_samples'])
                self._hour_stamp = bytes(datos['hour_stamp']).decode('ascii')
            logger.info("Heatmap de %s restaurado: %d muestras acumuladas",
                        camera_id, self._samples)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Heatmap cargar_estado fallo para %s: %s",
                           camera_id, exc)
            return False

    def flush(self, camera_id: Any, background: Optional[np.ndarray] = None,
              out_dir: str = None, camera_name: Optional[str] = None) -> bool:
        """Volcado FORZADO (desconexion del cliente / apagado del servidor):
        PNG + JSON + estado crudo, sin esperar el throttle.

        Sin frame a mano (el cliente ya se fue) usa el ultimo fondo guardado
        en disco, para que el PNG final siga viendose sobre la camara real."""
        if self._samples == 0:
            return False
        out_dir = out_dir or AnalyticsConfig.HEATMAP_DIR
        if background is None or getattr(background, 'size', 0) == 0:
            try:
                bg = cv2.imread(os.path.join(
                    out_dir, "bg", f"{_safe_camera_name(camera_id)}.jpg"))
                background = bg if bg is not None and bg.size else None
            except Exception:  # noqa: BLE001
                background = None
        self._last_snapshot_ts = 0.0          # anula el throttle
        ok = self.maybe_save_snapshot(camera_id, background=background,
                                      out_dir=out_dir, every_s=0.0,
                                      camera_name=camera_name)
        self.guardar_estado(camera_id, out_dir)
        return bool(ok)

    # ── Snapshot a disco (para el dashboard) ──────────────────────────

    @staticmethod
    def _current_stamp() -> str:
        """Marca de hora local 'YYYY-MM-DD_HH' (1 archivo por hora)."""
        return time.strftime("%Y-%m-%d_%H")

    def _maybe_roll_hour(
        self,
        camera_id: Any,
        camera_name: Optional[str],
        background: Optional[np.ndarray],
        out_dir: str,
    ) -> None:
        """Si cambio la hora, guarda el HISTORICO de la hora cerrada.

        Escribe output/heatmap/history/<cam>/<YYYY-MM-DD_HH>.png|.json y
        vacia el buffer horario. Aplica retencion (borra los mas viejos).
        Defensivo: nunca lanza.
        """
        stamp_now = self._current_stamp()
        if stamp_now == self._hour_stamp:
            return
        closed_stamp = self._hour_stamp
        try:
            if (AnalyticsConfig.HEATMAP_HISTORY_ENABLED
                    and self._hour_samples > 0):
                name = _safe_camera_name(camera_id)
                hist_dir = os.path.join(out_dir, "history", name)
                os.makedirs(hist_dir, exist_ok=True)

                if background is not None and background.size > 0:
                    img = self._render_grid(background, self._hour)
                else:
                    canvas = np.zeros(
                        (self.grid_h * 10, self.grid_w * 10, 3), np.uint8)
                    img = self._render_grid(canvas, self._hour)

                png_path = os.path.join(hist_dir, f"{closed_stamp}.png")
                tmp_png = os.path.join(hist_dir, f"{closed_stamp}.tmp.png")
                if cv2.imwrite(tmp_png, img,
                               [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                    os.replace(tmp_png, png_path)

                meta = {
                    "camera_id": str(camera_id),
                    "camera_name": (camera_name or str(camera_id)),
                    "stamp": closed_stamp,
                    "muestras": int(self._hour_samples),
                    "zonas_calientes": self._zones_from(
                        self._hour, AnalyticsConfig.HEATMAP_TOP_ZONES),
                }
                jp = os.path.join(hist_dir, f"{closed_stamp}.json")
                tmp_j = jp + ".tmp"
                with open(tmp_j, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False)
                os.replace(tmp_j, jp)

                # Grilla CRUDA comprimida (.npz, ~5-10 KB): permite re-agregar
                # CUALQUIER rango de tiempo en el dashboard (consulta historica).
                npz_path = os.path.join(hist_dir, f"{closed_stamp}.npz")
                tmp_npz = os.path.join(hist_dir, f"{closed_stamp}.tmp.npz")
                try:
                    np.savez_compressed(
                        tmp_npz, grid=self._hour.astype(np.float32),
                        w=int(self.grid_w), h=int(self.grid_h),
                        samples=int(self._hour_samples))
                    os.replace(tmp_npz, npz_path)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Heatmap npz fallo: %s", exc)

                self._apply_history_retention(hist_dir)
                logger.info(
                    "Heatmap historico guardado: %s/%s (%d muestras)",
                    name, closed_stamp, self._hour_samples)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Heatmap roll horario fallo: %s", exc)
        finally:
            self._hour[:] = 0.0
            self._hour_samples = 0
            self._hour_stamp = stamp_now

    @staticmethod
    def _stamp_epoch(stamp: str):
        """'YYYY-MM-DD_HH' -> epoch (segundos) o None si no parsea."""
        try:
            return time.mktime(time.strptime(stamp, "%Y-%m-%d_%H"))
        except Exception:
            return None

    def _apply_history_retention(self, hist_dir: str) -> None:
        """Retencion del historico: PNG por cantidad (mas pesados), data cruda
        (.npz/.json) por antiguedad en dias. Defensivo: nunca lanza."""
        try:
            files = os.listdir(hist_dir)
        except OSError:
            return
        # 1) PNG por hora: conservar solo los ultimos N.
        try:
            keep = max(1, int(AnalyticsConfig.HEATMAP_HISTORY_KEEP))
            pngs = sorted(f for f in files if f.endswith(".png")
                          and not f.endswith(".tmp.png"))
            for old in (pngs[:-keep] if len(pngs) > keep else []):
                try:
                    os.remove(os.path.join(hist_dir, old))
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass
        # 2) Data cruda (.npz/.json): borrar la mas vieja que N dias.
        try:
            keep_days = max(1, int(AnalyticsConfig.HEATMAP_DATA_KEEP_DAYS))
            cutoff = time.time() - keep_days * 86400.0
            for f in files:
                if not (f.endswith(".npz") or f.endswith(".json")):
                    continue
                if f.endswith(".tmp.npz"):
                    continue
                ts = self._stamp_epoch(f.rsplit(".", 1)[0])
                if ts is not None and ts < cutoff:
                    try:
                        os.remove(os.path.join(hist_dir, f))
                    except OSError:
                        pass
        except Exception:  # noqa: BLE001
            pass

    def maybe_save_snapshot(
        self,
        camera_id: Any,
        background: Optional[np.ndarray] = None,
        out_dir: str = None,
        every_s: float = None,
        camera_name: Optional[str] = None,
    ) -> bool:
        """Guarda PNG (overlay sobre el frame actual) + JSON, con throttle.

        Devuelve True si guardo. Defensivo: nunca lanza (un fallo de disco
        no debe tumbar el frame loop).
        """
        every_s = float(every_s if every_s is not None
                        else AnalyticsConfig.HEATMAP_SNAPSHOT_EVERY_S)
        now = time.time()
        if now - self._last_snapshot_ts < every_s or self._samples == 0:
            return False
        self._last_snapshot_ts = now
        try:
            out_dir = out_dir or AnalyticsConfig.HEATMAP_DIR
            os.makedirs(out_dir, exist_ok=True)
            name = _safe_camera_name(camera_id)

            # Cierre de hora: guardar historico si corresponde
            self._maybe_roll_hour(camera_id, camera_name, background,
                                  out_dir)

            # Fondo de la camara (para renderizar los agregados de consulta
            # sobre la vista real). Throttle propio (~60 s).
            if (background is not None and background.size > 0
                    and now - self._last_bg_ts
                    >= AnalyticsConfig.HEATMAP_BG_EVERY_S):
                self._last_bg_ts = now
                try:
                    bg_dir = os.path.join(out_dir, "bg")
                    os.makedirs(bg_dir, exist_ok=True)
                    bg_tmp = os.path.join(bg_dir, f"{name}.tmp.jpg")
                    bg_path = os.path.join(bg_dir, f"{name}.jpg")
                    if cv2.imwrite(bg_tmp, background,
                                   [cv2.IMWRITE_JPEG_QUALITY, 70]):
                        os.replace(bg_tmp, bg_path)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Heatmap bg fallo: %s", exc)

            if background is not None and background.size > 0:
                img = self.render_overlay(background, use_total=True)
            else:
                # Sin fondo: render del heatmap puro sobre negro
                canvas = np.zeros((self.grid_h * 10, self.grid_w * 10, 3),
                                  np.uint8)
                img = self.render_overlay(canvas, use_total=True)

            png_path = os.path.join(out_dir, f"{name}.png")
            # El tmp DEBE terminar en .png: cv2.imwrite infiere el formato
            # de la extension (con '.tmp' falla silenciosamente).
            tmp_png = os.path.join(out_dir, f"{name}.tmp.png")
            ok = cv2.imwrite(tmp_png, img,
                             [cv2.IMWRITE_PNG_COMPRESSION, 3])
            if ok:
                os.replace(tmp_png, png_path)

            meta = {
                "camera_id": str(camera_id),
                "camera_name": (camera_name or str(camera_id)),
                "actualizado": now,
                "muestras": int(self._samples),
                "zonas_calientes": self.top_zones(),
                "grid": [self.grid_w, self.grid_h],
            }
            json_path = os.path.join(out_dir, f"{name}.json")
            tmp_json = json_path + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            os.replace(tmp_json, json_path)

            # Estado crudo con throttle propio (mas suave que el snapshot):
            # si el proceso muere sin flush, se pierde este intervalo como
            # maximo, no la sesion entera.
            cada = float(os.getenv("ELDE_HEATMAP_ESTADO_CADA_S", "30"))
            if now - self._last_state_ts >= cada:
                self.guardar_estado(camera_id, out_dir)
            return bool(ok)
        except Exception as exc:  # noqa: BLE001 - no tumbar el frame loop
            logger.debug("Heatmap snapshot fallo: %s", exc)
            return False

    def reset(self) -> None:
        self._recent[:] = 0.0
        self._total[:] = 0.0
        self._hour[:] = 0.0
        self._hour_samples = 0
        self._hour_stamp = self._current_stamp()
        self._samples = 0
        self._started_ts = time.time()
