"""
Prueba de la LÓGICA del reenviador de alertas a Jarvis (sin red).

Valida la AGRUPACIÓN POR GRUPO (grupo de una clase = 1 alerta) usando un
doble de Jarvis_api, sin generar novedades reales.

  C1: 'llegada' de PERSONA (objeto 1) -> 1 alerta.
  C2: GRUPO de 5 personas juntas (misma clase, ventana) -> sigue 1 alerta.
  C3: pasada la ventana, otra persona -> nueva alerta.
  C4: un GRUPO de carros -> 1 alerta (distinta clase de personas).
  C5: 'llegada' de OBJETO -> NO se reenvía.
  C6: 'salida' de un GRUPO -> 1 alerta (se reenvía, agrupada aparte de la llegada).
  C7: 2 personas de interés distintas (Re-ID) -> 2 alertas (no se agrupan).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PySide6.QtCore import QCoreApplication

from core.network.jarvis_alert_forwarder import JarvisAlertForwarder


class _JarvisFake:
    def __init__(self):
        self.session_user = {"name": "X"}
        self.selected_establishment = {"name": "Local"}
        self.llamadas = []

    def enviar_novedad_async(self, base64_image='', title='', message=''):
        self.llamadas.append({"title": title, "img": base64_image})


def _al(evento, clase, gid, cam="patio", img="AAAA"):
    return {"event_type": evento, "class_name": clase, "global_id": gid,
            "camera_name": cam, "description": f"{clase} {evento}",
            "image_base64": img}


def main() -> int:
    _ = QCoreApplication(sys.argv)
    fake = _JarvisFake()
    fwd = JarvisAlertForwarder(fake)

    # C1: primera persona.
    fwd.on_alert(_al("llegada", "PERSONA", "VIG-patio-T1"))
    c1 = len(fake.llamadas) == 1
    print(f"{'OK' if c1 else 'FALLO'} C1: 1a persona -> 1 alerta ({len(fake.llamadas)})")

    # C2: grupo de 5 personas más (distinto objeto, misma clase, misma ventana).
    for i in range(2, 7):
        fwd.on_alert(_al("llegada", "PERSONA", f"VIG-patio-T{i}"))
    c2 = len(fake.llamadas) == 1 and fwd.agrupadas == 5
    print(f"{'OK' if c2 else 'FALLO'} C2: grupo de 5 -> sigue 1 alerta "
          f"(agrupadas={fwd.agrupadas})")

    # C3: pasada la ventana, otra persona -> nueva alerta.
    # (retrocedemos el último envío de la clave del grupo más allá de la ventana)
    for k in list(fwd._ultimo):
        fwd._ultimo[k] -= (fwd.VENTANA_GRUPO_SEG + 1.0)
    fwd.on_alert(_al("llegada", "PERSONA", "VIG-patio-T7"))
    c3 = len(fake.llamadas) == 2
    print(f"{'OK' if c3 else 'FALLO'} C3: tras la ventana -> nueva alerta ({len(fake.llamadas)})")

    # C4: grupo de carros -> 1 alerta (clase distinta).
    for i in range(1, 4):
        fwd.on_alert(_al("llegada", "CARRO", f"VIG-patio-C{i}"))
    c4 = len(fake.llamadas) == 3
    print(f"{'OK' if c4 else 'FALLO'} C4: grupo de carros -> 1 alerta ({len(fake.llamadas)})")

    # C5: objeto (clase excluida).
    fwd.on_alert(_al("llegada", "OBJETO", "VIG-patio-O1"))
    c5 = len(fake.llamadas) == 3
    print(f"{'OK' if c5 else 'FALLO'} C5: OBJETO NO reenviado ({len(fake.llamadas)})")

    # C6: salida de un grupo de personas -> 1 alerta (agrupada, aparte de la llegada).
    for i in range(1, 5):
        fwd.on_alert(_al("salida", "PERSONA", f"VIG-patio-S{i}"))
    c6 = len(fake.llamadas) == 4
    print(f"{'OK' if c6 else 'FALLO'} C6: salida de grupo -> 1 alerta ({len(fake.llamadas)})")

    # C7: 2 personas de interes distintas (Re-ID) -> 2 alertas.
    fwd.on_alert(_al("Alerta", "Juan Perez", "VIG-3"))
    fwd.on_alert(_al("Alerta", "Ana Lopez", "VIG-4"))
    c7 = len(fake.llamadas) == 6
    print(f"{'OK' if c7 else 'FALLO'} C7: 2 personas de interes -> 2 alertas ({len(fake.llamadas)})")

    # C8: evento de PERMANENCIA (5 min en el area) -> se reenvia con su titulo.
    fwd.on_alert(_al("permanencia", "PERSONA", "VIG-patio-T1"))
    c8 = (len(fake.llamadas) == 7
          and "permanece en el área" in fake.llamadas[-1]["title"])
    print(f"{'OK' if c8 else 'FALLO'} C8: permanencia reenviada "
          f"('{fake.llamadas[-1]['title']}')")

    ok = all([c1, c2, c3, c4, c5, c6, c7, c8])
    print("=" * 50)
    print("OK LOGICA DE AGRUPACION SUPERADA" if ok else "FALLO en la logica")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
