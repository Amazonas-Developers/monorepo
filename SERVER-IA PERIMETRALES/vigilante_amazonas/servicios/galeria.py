"""
Galería de personas de interés: índice de similitud EN MEMORIA (numpy).

- Se carga desde SQLite (fotos_referencia de personas ACTIVAS).
- `recargar()` es la recarga caliente que dispara el dashboard al subir/borrar
  fotos o activar/desactivar personas — sin reiniciar nada.
- Búsqueda por producto punto (los embeddings están L2-normalizados, así que
  equivale a similitud coseno). Con cientos de fotos numpy sobra; si la
  galería creciera a decenas de miles, el salto natural es faiss.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro
from vigilante_amazonas.web.base_datos import get_bd

logger = configurar_registro(__name__)


@dataclass
class ResultadoBusqueda:
    """Mejor coincidencia de la galería para un embedding consultado."""
    persona_id: int
    persona: str
    nivel: str
    score: float
    foto_id: int
    ruta_foto: str


class _Indice:
    """Matriz de embeddings + metadatos alineados por fila."""

    def __init__(self) -> None:
        self.matriz: np.ndarray = np.zeros((0, 512), dtype=np.float32)
        self.metadatos: list[dict] = []

    def buscar(self, embedding: np.ndarray) -> ResultadoBusqueda | None:
        if self.matriz.shape[0] == 0:
            return None
        scores = self.matriz @ embedding.astype(np.float32)
        idx = int(np.argmax(scores))
        m = self.metadatos[idx]
        return ResultadoBusqueda(
            persona_id=m["persona_id"], persona=m["persona"], nivel=m["nivel"],
            score=float(scores[idx]), foto_id=m["foto_id"], ruta_foto=m["ruta"])


class Galeria:
    """Índices de rostro y vestimenta con recarga caliente."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rostro = _Indice()
        self._vestimenta = _Indice()
        self.version: int = 0
        self.recargar()

    # ------------------------------------------------------------------ carga
    def recargar(self) -> dict[str, int]:
        """Relee la galería desde SQLite (solo personas activas)."""
        bd = get_bd()
        fotos = bd.fotos(solo_personas_activas=True)
        nuevo_rostro, nuevo_vest = _Indice(), _Indice()
        backend_activo = config.VESTIMENTA_BACKEND.lower()
        for f in fotos:
            emb = f.get("embedding")
            if emb is None or not isinstance(emb, np.ndarray) or emb.size == 0:
                continue
            if len(nuevo_rostro.metadatos) + len(nuevo_vest.metadatos) >= config.REID_MAX_GALERIA:
                logger.warning(f"galería truncada a {config.REID_MAX_GALERIA} embeddings")
                break
            meta = {"persona_id": f["persona_id"], "persona": f["persona"],
                    "nivel": f["nivel"], "foto_id": f["id"], "ruta": f["ruta"]}
            if f["tipo"] == "rostro":
                destino = nuevo_rostro
            else:
                # Solo se indexan embeddings del backend de vestimenta ACTIVO
                # (si se conmuta osnet<->clip hay que re-embeber, y el
                # dashboard lo hace al vuelo desde el archivo).
                if f.get("backend") and f["backend"] != backend_activo:
                    continue
                destino = nuevo_vest
            if destino.matriz.shape[0] == 0:
                destino.matriz = emb[None, :].astype(np.float32)
            else:
                destino.matriz = np.vstack([destino.matriz, emb[None, :]])
            destino.metadatos.append(meta)

        with self._lock:
            self._rostro, self._vestimenta = nuevo_rostro, nuevo_vest
            self.version += 1
        resumen = {"rostros": len(nuevo_rostro.metadatos),
                   "vestimentas": len(nuevo_vest.metadatos)}
        logger.info(f"galería recargada: {resumen['rostros']} rostro(s), "
                    f"{resumen['vestimentas']} vestimenta(s) (v{self.version})",
                    extra={"datos": resumen})
        return resumen

    # ---------------------------------------------------------------- búsqueda
    def buscar_rostro(self, embedding: np.ndarray) -> ResultadoBusqueda | None:
        with self._lock:
            return self._rostro.buscar(embedding)

    def buscar_vestimenta(self, embedding: np.ndarray) -> ResultadoBusqueda | None:
        with self._lock:
            return self._vestimenta.buscar(embedding)

    @property
    def tamano(self) -> dict[str, int]:
        with self._lock:
            return {"rostros": len(self._rostro.metadatos),
                    "vestimentas": len(self._vestimenta.metadatos)}
