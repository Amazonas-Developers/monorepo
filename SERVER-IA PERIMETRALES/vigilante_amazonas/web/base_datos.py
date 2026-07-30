"""
Base de datos SQLite de VIGILANTE-AMAZONAS (WAL, thread-safe).

Tablas:
  personas_interes  — nombre, descripción, nivel de alerta, activo
  fotos_referencia  — fotos de rostro/vestimenta + su embedding (BLOB)
  alertas           — historial con snapshot y tipo de match
  camaras           — registro informativo de cámaras vistas

Los embeddings se guardan como BLOB float32; la galería en memoria
(servicios/galeria.py) los levanta al arrancar y en cada recarga caliente.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from vigilante_amazonas import config
from vigilante_amazonas.utilidades.registro import configurar_registro

logger = configurar_registro(__name__)

_ESQUEMA: str = """
CREATE TABLE IF NOT EXISTS personas_interes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL UNIQUE,
    descripcion TEXT DEFAULT '',
    nivel       TEXT NOT NULL DEFAULT 'medio',
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fotos_referencia (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id  INTEGER NOT NULL REFERENCES personas_interes(id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL CHECK (tipo IN ('rostro', 'vestimenta')),
    ruta        TEXT NOT NULL,
    embedding   BLOB,
    dim         INTEGER DEFAULT 0,
    backend     TEXT DEFAULT '',
    creado      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alertas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id  INTEGER,
    persona     TEXT NOT NULL,
    nivel       TEXT NOT NULL,
    camara      TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    score       REAL NOT NULL,
    tipo_match  TEXT NOT NULL,
    snapshot    TEXT DEFAULT '',
    track_id    INTEGER DEFAULT -1,
    verificacion TEXT DEFAULT 'no_requerida'
);
CREATE TABLE IF NOT EXISTS camaras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL UNIQUE,
    url         TEXT DEFAULT '',
    ultima_vista TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alertas_ts ON alertas(timestamp);
CREATE INDEX IF NOT EXISTS idx_fotos_persona ON fotos_referencia(persona_id);
"""


def _ahora() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class BaseDatos:
    """Acceso serializado a SQLite (una conexión, un lock por proceso)."""

    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta: Path = ruta or config.RUTA_DB
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.ruta), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
            self._con.executescript(_ESQUEMA)
            self._con.commit()
        logger.info(f"base de datos lista: {self.ruta}")

    # ------------------------------------------------------------ personas
    def crear_persona(self, nombre: str, descripcion: str = "",
                      nivel: str = "medio", activo: bool = True) -> int:
        if nivel not in config.NIVELES_ALERTA:
            raise ValueError(f"nivel inválido: {nivel} (usar {config.NIVELES_ALERTA})")
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO personas_interes (nombre, descripcion, nivel, activo, creado) "
                "VALUES (?, ?, ?, ?, ?)",
                (nombre.strip(), descripcion.strip(), nivel, int(activo), _ahora()))
            self._con.commit()
            return int(cur.lastrowid)

    def actualizar_persona(self, persona_id: int, **campos: Any) -> None:
        permitidos = {"nombre", "descripcion", "nivel", "activo"}
        cambios = {k: v for k, v in campos.items() if k in permitidos}
        if not cambios:
            return
        if "nivel" in cambios and cambios["nivel"] not in config.NIVELES_ALERTA:
            raise ValueError(f"nivel inválido: {cambios['nivel']}")
        sets = ", ".join(f"{k} = ?" for k in cambios)
        with self._lock:
            self._con.execute(f"UPDATE personas_interes SET {sets} WHERE id = ?",
                              (*cambios.values(), persona_id))
            self._con.commit()

    def borrar_persona(self, persona_id: int) -> None:
        with self._lock:
            self._con.execute("DELETE FROM fotos_referencia WHERE persona_id = ?", (persona_id,))
            self._con.execute("DELETE FROM personas_interes WHERE id = ?", (persona_id,))
            self._con.commit()

    def personas(self, solo_activas: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM personas_interes"
        if solo_activas:
            sql += " WHERE activo = 1"
        sql += " ORDER BY nombre"
        with self._lock:
            filas = self._con.execute(sql).fetchall()
        return [dict(f) for f in filas]

    def persona(self, persona_id: int) -> dict[str, Any] | None:
        with self._lock:
            fila = self._con.execute("SELECT * FROM personas_interes WHERE id = ?",
                                     (persona_id,)).fetchone()
        return dict(fila) if fila else None

    # ---------------------------------------------------------------- fotos
    def agregar_foto(self, persona_id: int, tipo: str, ruta: str,
                     embedding: np.ndarray | None, backend: str = "") -> int:
        blob = embedding.astype(np.float32).tobytes() if embedding is not None else None
        dim = int(embedding.shape[-1]) if embedding is not None else 0
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO fotos_referencia (persona_id, tipo, ruta, embedding, dim, backend, creado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (persona_id, tipo, ruta, blob, dim, backend, _ahora()))
            self._con.commit()
            return int(cur.lastrowid)

    def borrar_foto(self, foto_id: int) -> str | None:
        """Borra la fila y devuelve la ruta del archivo (para limpiarlo)."""
        with self._lock:
            fila = self._con.execute("SELECT ruta FROM fotos_referencia WHERE id = ?",
                                     (foto_id,)).fetchone()
            self._con.execute("DELETE FROM fotos_referencia WHERE id = ?", (foto_id,))
            self._con.commit()
        return fila["ruta"] if fila else None

    def fotos(self, persona_id: int | None = None,
              solo_personas_activas: bool = False) -> list[dict[str, Any]]:
        sql = ("SELECT f.*, p.nombre AS persona, p.nivel, p.activo "
               "FROM fotos_referencia f JOIN personas_interes p ON p.id = f.persona_id")
        cond, args = [], []
        if persona_id is not None:
            cond.append("f.persona_id = ?")
            args.append(persona_id)
        if solo_personas_activas:
            cond.append("p.activo = 1")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        with self._lock:
            filas = self._con.execute(sql, args).fetchall()
        salida: list[dict[str, Any]] = []
        for f in filas:
            d = dict(f)
            if d.get("embedding"):
                d["embedding"] = np.frombuffer(d["embedding"], dtype=np.float32)
            salida.append(d)
        return salida

    # --------------------------------------------------------------- alertas
    def registrar_alerta(self, persona_id: int | None, persona: str, nivel: str,
                         camara: str, score: float, tipo_match: str,
                         snapshot: str = "", track_id: int = -1,
                         verificacion: str = "no_requerida") -> int:
        with self._lock:
            cur = self._con.execute(
                "INSERT INTO alertas (persona_id, persona, nivel, camara, timestamp, "
                "score, tipo_match, snapshot, track_id, verificacion) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (persona_id, persona, nivel, camara, _ahora(), score, tipo_match,
                 snapshot, track_id, verificacion))
            self._con.commit()
            return int(cur.lastrowid)

    def alertas(self, camara: str | None = None, persona: str | None = None,
                fecha: str | None = None, limite: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alertas"
        cond, args = [], []
        if camara:
            cond.append("camara = ?")
            args.append(camara)
        if persona:
            cond.append("persona LIKE ?")
            args.append(f"%{persona}%")
        if fecha:
            cond.append("timestamp LIKE ?")
            args.append(f"{fecha}%")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limite))
        with self._lock:
            filas = self._con.execute(sql, args).fetchall()
        return [dict(f) for f in filas]

    # --------------------------------------------------------------- cámaras
    def registrar_camara(self, nombre: str, url: str = "") -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO camaras (nombre, url, ultima_vista) VALUES (?, ?, ?) "
                "ON CONFLICT(nombre) DO UPDATE SET ultima_vista = excluded.ultima_vista",
                (nombre, url, _ahora()))
            self._con.commit()

    def camaras(self) -> list[dict[str, Any]]:
        with self._lock:
            filas = self._con.execute("SELECT * FROM camaras ORDER BY nombre").fetchall()
        return [dict(f) for f in filas]

    def cerrar(self) -> None:
        with self._lock:
            self._con.close()


# Singleton de conveniencia.
_lock_bd = threading.Lock()
_bd: BaseDatos | None = None


def get_bd() -> BaseDatos:
    global _bd
    with _lock_bd:
        if _bd is None:
            _bd = BaseDatos()
        return _bd
