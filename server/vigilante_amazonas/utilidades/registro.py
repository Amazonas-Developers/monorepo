"""
Logging estructurado de VIGILANTE-AMAZONAS: JSON por línea con rotación.

Uso:
    from vigilante_amazonas.utilidades.registro import configurar_registro
    logger = configurar_registro(__name__)
    logger.info("cámara conectada", extra={"datos": {"camara": "entrada"}})

Cada línea del archivo logs/vigilante.jsonl es un objeto JSON:
    {"ts": "...", "nivel": "INFO", "modulo": "...", "mensaje": "...", "datos": {...}}
La consola usa un formato humano legible (también en español).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from vigilante_amazonas import config

_RAIZ: str = "vigilante"
_configurado: bool = False


class FormateadorJSON(logging.Formatter):
    """Serializa cada registro como una línea JSON (UTF-8, sin escapes)."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "modulo": record.name,
            "mensaje": record.getMessage(),
        }
        datos: Any = getattr(record, "datos", None)
        if datos is not None:
            doc["datos"] = datos
        if record.exc_info:
            doc["excepcion"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False, default=str)


def _configurar_raiz() -> None:
    """Configura una sola vez el logger raíz del paquete (archivo + consola)."""
    global _configurado
    if _configurado:
        return
    config.crear_directorios()

    raiz = logging.getLogger(_RAIZ)
    raiz.setLevel(getattr(logging, config.LOG_NIVEL.upper(), logging.INFO))
    raiz.propagate = False

    archivo = RotatingFileHandler(
        config.LOG_ARCHIVO,
        maxBytes=config.LOG_ROTACION_MB * 1024 * 1024,
        backupCount=config.LOG_RESPALDOS,
        encoding="utf-8",
    )
    archivo.setFormatter(FormateadorJSON())
    raiz.addHandler(archivo)

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    raiz.addHandler(consola)
    _configurado = True


def configurar_registro(nombre: str) -> logging.Logger:
    """Devuelve un logger hijo de `vigilante` con salida JSON + consola.

    `nombre` suele ser __name__ del módulo llamador; se cuelga bajo la raíz
    del paquete para heredar los handlers sin duplicarlos.
    """
    _configurar_raiz()
    corto: str = nombre.replace("vigilante_amazonas.", "")
    return logging.getLogger(f"{_RAIZ}.{corto}")
