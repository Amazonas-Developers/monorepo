"""
Valida el contrato contra payloads REALES capturados del websocket.

Es el criterio de aceptacion del HITO 3: «los esquemas validan los payloads
reales del sistema actual». Un contrato deducido leyendo el codigo emisor no
basta — de hecho, la primera version de este contrato **rechazaba un payload
real y funcional** porque no contemplaba `camera_angle: "auto"`. Lo descubrio
este archivo, no la lectura del codigo.

Las capturas las genera `SERVER-IA PERIMETRALES/src/app/captura_contrato.py`
con `ELDE_CAPTURA_PAYLOADS=1`. Si no hay capturas, las pruebas se saltan en
vez de fallar: no todo el mundo tiene una sesion grabada.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elde_core.contracts import (FrameInference, FrameResult, desde_antiguo,
                                 es_formato_antiguo)

RAIZ = Path(__file__).resolve().parents[3]
CAPTURAS = RAIZ / "SERVER-IA PERIMETRALES" / "output" / "contrato" / "payloads.jsonl"

_JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64


def _rehidratar(v: Any) -> Any:
    """Deshace los marcadores de la captura.

    El capturador sustituye binarios por «binario: N bytes» y trunca listas
    largas con «… y N mas», para no llenar el disco. Aqui se reponen valores
    plausibles para poder validar la ESTRUCTURA."""
    if isinstance(v, str):
        if v.startswith('«binario'):
            return _JPEG
        if v.startswith('«cadena'):
            return 'x'
        return v
    if isinstance(v, dict):
        return {k: _rehidratar(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_rehidratar(x) for x in v
                if not (isinstance(x, str) and x.startswith('«…'))]
    return v


def _capturas() -> List[dict]:
    if not CAPTURAS.is_file():
        return []
    out = []
    for linea in CAPTURAS.read_text(encoding='utf-8').splitlines():
        if linea.strip():
            try:
                out.append(json.loads(linea))
            except Exception:
                pass
    return out


def test_hay_capturas_o_se_salta():
    caps = _capturas()
    if not caps:
        print("    (sin capturas: ejecutar el servidor con "
              "ELDE_CAPTURA_PAYLOADS=1 y un cliente)")
    else:
        print(f"    ({len(caps)} formas capturadas)")


def test_todo_payload_entrante_real_valida():
    """Ningun mensaje que el sistema envia hoy puede ser rechazado."""
    fallos = []
    for r in _capturas():
        if r.get('direccion') != 'entrante':
            continue
        m = _rehidratar(r['muestra'])
        datos = m.get('data') if isinstance(m, dict) else None
        if not isinstance(datos, dict):
            continue
        try:
            FrameInference(**datos)
        except Exception as exc:
            fallos.append(f"{r['tipo_cliente']}/{r['firma']}: {exc}")
    assert not fallos, "payloads reales rechazados:\n  " + "\n  ".join(fallos)


def test_todo_payload_saliente_real_valida():
    fallos = []
    for r in _capturas():
        if r.get('direccion') != 'saliente':
            continue
        m = _rehidratar(r['muestra'])
        datos = m.get('data') if isinstance(m, dict) else None
        if not isinstance(datos, dict):
            continue
        try:
            FrameResult(**datos)
        except Exception as exc:
            fallos.append(f"{r['tipo_cliente']}/{r['firma']}: {exc}")
    assert not fallos, "respuestas reales rechazadas:\n  " + "\n  ".join(fallos)


def test_la_capa_de_compatibilidad_traduce_lo_real():
    fallos = []
    for r in _capturas():
        if r.get('direccion') != 'entrante':
            continue
        m = _rehidratar(r['muestra'])
        if not es_formato_antiguo(m):
            continue
        env, err = desde_antiguo(m)
        if env is None:
            fallos.append(f"{r['tipo_cliente']}: {err}")
    assert not fallos, "mensajes reales sin traducir:\n  " + "\n  ".join(fallos)


def test_ningun_campo_real_queda_sin_modelar():
    """Aviso, no fallo: los payloads son permisivos durante la migracion.

    Sirve para saber que falta por modelar antes de endurecerlos al cerrar
    el HITO 7."""
    for r in _capturas():
        m = _rehidratar(r['muestra'])
        datos = m.get('data') if isinstance(m, dict) else None
        if not isinstance(datos, dict):
            continue
        modelo = FrameInference if r['direccion'] == 'entrante' else FrameResult
        # `camera_id` no se modela en el payload entrante a proposito: asciende
        # al envelope como `device_id`. No es un campo pendiente.
        promovidos = {'camera_id'} if r['direccion'] == 'entrante' else set()
        extra = sorted(set(datos) - set(modelo.model_fields) - promovidos)
        if extra:
            print(f"    aviso [{r['direccion']}/{r['tipo_cliente']}]: "
                  f"sin modelar -> {extra}")


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK    {nombre}")
            except Exception as exc:
                fallos += 1
                print(f"  FALLA {nombre}: {exc}")
    print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
    raise SystemExit(1 if fallos else 0)
