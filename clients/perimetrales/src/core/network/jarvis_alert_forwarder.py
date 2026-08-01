"""
Reenviador de alertas hacia la API de Jarvis365.

Recibe las MISMAS alertas que se muestran en el AlertsSidebar (las tarjetas
que emite cada render_box por `alert_received`) y las reenvía a Jarvis como
"novedades" (POST /novelties, con la imagen subida a /multimedia).

POLÍTICA (a petición del operador):
  - Se reenvían las clases  persona, personal_seguridad, moto, carro,
    camioneta, camión  (la clase `objeto` NO). Se reenvían tanto las ENTRADAS
    ('llegada') como las SALIDAS ('salida', que traen la permanencia total).
  - AGRUPACIÓN POR GRUPO: si entra/sale un GRUPO de la misma clase junto
    (varias personas, varios carros…), se envía UNA SOLA alerta, no una por
    objeto. Se logra con una ventana de agrupación por (evento, cámara,
    clase): la primera dispara la novedad y las siguientes del mismo evento y
    clase dentro de VENTANA_GRUPO_SEG se agrupan (no se reenvían). Entrada y
    salida son eventos distintos, así que no se agrupan entre sí.
  - PERSONAS DE INTERÉS (Re-ID) e intrusiones: NO se agrupan por clase — cada
    individuo identificado genera su propia novedad (dedup por objeto).

Se conecta en main.py junto al sidebar:
    reenviador = JarvisAlertForwarder(jarvis_api)
    for box in window_containter.list_box:
        box.alert_received.connect(reenviador.on_alert)
"""

from __future__ import annotations

import time
import unicodedata

from PySide6.QtCore import QObject, Slot


def _normaliza(texto: str) -> str:
    """MAYÚSCULAS sin acentos: 'Camión' -> 'CAMION'."""
    sin_acento = unicodedata.normalize("NFKD", texto or "")
    sin_acento = "".join(c for c in sin_acento if not unicodedata.combining(c))
    return sin_acento.upper().strip()


class JarvisAlertForwarder(QObject):
    """Puente AlertsSidebar -> API de Jarvis con agrupación por grupo/clase."""

    # Prefijos de clase que SÍ se reenvían (las 6 pedidas). 'OBJETO' queda
    # fuera a propósito. Se compara contra el class_name normalizado
    # (mayúsculas sin acentos); "PERSONA SEGURIDAD" cae bajo "PERSONA".
    CLASES_PERMITIDAS: tuple[str, ...] = (
        "PERSONA", "MOTO", "CARRO", "CAMIONETA", "CAMION",
    )
    # Ventana de AGRUPACIÓN por (cámara, clase): dentro de este lapso, un grupo
    # de la misma clase = 1 sola alerta. Súbela para agrupar grupos que entran
    # más espaciados; bájala para alertar con más frecuencia.
    VENTANA_GRUPO_SEG: float = 30.0
    # Eventos que NO se agrupan por clase (cada objeto/persona su alerta).
    # merodeo: cada visitante recurrente merece su propia novedad (su
    # global_id VIG-MER-<cam>-<id> es estable entre visitas).
    EVENTOS_POR_OBJETO: tuple[str, ...] = ("alerta", "intrusion", "intrusión",
                                           "merodeo")
    # Purga de la tabla anti-fuga.
    MAX_CLAVES: int = 2000

    def __init__(self, jarvis_api, parent=None) -> None:
        super().__init__(parent)
        self._jarvis = jarvis_api
        # Interruptor maestro (checkbox "Enviar a Jarvis" del pie). Con False
        # NO se envía nada a la API; el sidebar sigue mostrando las alertas.
        self.activo: bool = True
        # clave -> timestamp del último envío.
        self._ultimo: dict[tuple, float] = {}
        self.enviadas: int = 0
        self.agrupadas: int = 0     # entradas suprimidas por pertenecer a un grupo
        self.sin_local: int = 0     # omitidas: la cámara no tiene local asignado

    @Slot(bool)
    def set_activo(self, activo: bool) -> None:
        """Activa/desactiva el envío a la API (conectado al checkbox del pie)."""
        self.activo = bool(activo)
        print(f"[jarvis-forwarder] envío a la API "
              f"{'ACTIVADO' if self.activo else 'DESACTIVADO'}")

    @Slot(dict)
    def on_alert(self, alerta: dict) -> None:
        """Recibe una tarjeta de alerta y decide si reenviarla a Jarvis."""
        try:
            if self._jarvis is None or not self.activo:
                return
            evento = str(alerta.get("event_type", "")).strip().lower()
            clase_norm = _normaliza(str(alerta.get("class_name") or ""))
            por_objeto = evento in self.EVENTOS_POR_OBJETO

            # Filtro de clases: las 'llegadas' solo de las 6 permitidas; los
            # eventos por objeto (Re-ID/intrusión) pasan aunque el class_name
            # sea un nombre propio.
            if not por_objeto and not clase_norm.startswith(self.CLASES_PERMITIDAS):
                return

            clave = self._clave(alerta, evento, clase_norm, por_objeto)
            ahora = time.time()
            ventana = self.VENTANA_GRUPO_SEG
            if ahora - self._ultimo.get(clave, 0.0) < ventana:
                # Mismo grupo (misma cámara+clase) dentro de la ventana, o el
                # mismo objeto repetido: se agrupa (no se reenvía).
                self.agrupadas += 1
                return
            self._ultimo[clave] = ahora
            self._purgar(ahora)

            # Local de la CAMARA que disparo la alerta (select "Local" del
            # recuadro). SIN local no se envia (1-ago-2026): el selector
            # global del pie se elimino y un fallback escondido mandaria la
            # alerta al establecimiento equivocado.
            establecimiento = str(alerta.get("establecimiento") or "").strip()
            if not establecimiento:
                self.sin_local += 1
                print("[jarvis-forwarder] alerta NO enviada: la cámara "
                      f"'{alerta.get('camera_name') or '?'}' no tiene "
                      "establecimiento asignado (select Local del recuadro)")
                return

            titulo = self._titulo(alerta, evento, alerta.get("class_name") or "")
            mensaje = str(alerta.get("description", "") or "")
            imagen = alerta.get("image_base64") or alerta.get("crop_image") or ""

            self._jarvis.enviar_novedad_async(
                base64_image=imagen, title=titulo, message=mensaje,
                establecimiento=establecimiento)
            self.enviadas += 1
        except Exception as e:
            print(f"[jarvis-forwarder] error reenviando alerta: {e}")

    def _clave(self, alerta: dict, evento: str, clase_norm: str,
               por_objeto: bool) -> tuple:
        """Clave de deduplicación:
        - llegada/salida de área -> ('grp', evento, cámara, clase): agrupa el
          GRUPO. El evento va en la clave para que entrada y salida NO se
          agrupen entre sí (un grupo que sale sí alerta aunque acabe de entrar).
        - Re-ID/intrusión -> ('obj', global_id): una por individuo.
        """
        if por_objeto:
            gid = str(alerta.get("global_id")
                      or f"{alerta.get('camera_name','?')}:{alerta.get('tracker_id','?')}")
            return ("obj", gid)
        cam = str(alerta.get("camera_name") or alerta.get("camera_id") or "?")
        return ("grp", evento, cam, clase_norm)

    def _purgar(self, ahora: float) -> None:
        if len(self._ultimo) <= self.MAX_CLAVES:
            return
        self._ultimo = {k: t for k, t in self._ultimo.items()
                        if ahora - t < self.VENTANA_GRUPO_SEG}

    @staticmethod
    def _titulo(alerta: dict, evento: str, clase: str) -> str:
        """Título legible para la novedad de Jarvis."""
        camara = str(alerta.get("camera_name") or alerta.get("camera_id") or "")
        if evento == "llegada":
            base = f"{clase} detectado en el área"
        elif evento == "permanencia":
            base = f"{clase} permanece en el área"
        elif evento == "salida":
            base = f"{clase} salió del área"
        elif evento in ("intrusion", "intrusión"):
            base = f"Intrusión: {clase}"
        elif evento == "merodeo":
            base = f"⚠ MERODEO: {clase} entrando y saliendo del área"
        elif evento == "alerta":
            base = f"Persona de interés: {clase}"
        else:
            base = f"Alerta perimetral: {clase}"
        return f"{base} — {camara}" if camara else base
