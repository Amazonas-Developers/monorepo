"""
Pruebas del contrato (HITO 3).

Lo importante de este archivo no es la cobertura, es que los payloads de
prueba son **los que el sistema emite hoy de verdad**, copiados campo por
campo de `render_box.py:645-669` y `socket_client.py:130-136`. Si el contrato
no valida esto, el contrato esta mal, no el sistema.

Son ademas los primeros tests del ecosistema: no habia ninguno en los 5
proyectos.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elde_core.contracts import (ClientType, ConnectionInit, Envelope,
                                 ErrorEvento, EventType, FrameInference,
                                 FrameResult, Heartbeat, Pipeline,
                                 desde_antiguo, es_formato_antiguo,
                                 hacia_antiguo)

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 512      # cabecera JPEG valida


def sobre_antiguo_real() -> dict:
    """El mensaje EXACTO que manda hoy tienda_view.

    Estructura: socket_client.py:130-136. Contenido de `data`:
    render_box.py:645-669."""
    return {
        "event": "inference",
        "id_connection": 1851954004384,
        "type_inference": "Personal de Amazonas",
        "component_key": "de60bb79-236e-4595-8d93-3c5d40bb3e25",
        "data": {
            "image": JPEG,
            "roi_coordinates": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
            "roi_activate": True,
            "order_zone_coordinates": None,
            "order_zone_activate": False,
            "delivery_zone_coordinates": None,
            "delivery_zone_activate": False,
            "enable_vlm": False,
            "camera_id": "de60bb79-236e-4595-8d93-3c5d40bb3e25",
            "camera_angle": "frontal",
            "heatmap_activate": True,
            "camera_name": "Camera 12",
            "track_classes": [0],
            "draw_server": True,
        },
    }


# ── Envelope ─────────────────────────────────────────────────────────────

def test_envelope_minimo_valido():
    env = Envelope(client_type=ClientType.TIENDA, site_id="lacomarca",
                   device_id="camera-12", event_type=EventType.FRAME_INFERENCE)
    assert env.event_version == 1
    assert env.timestamp_utc.tzinfo is not None, "debe llevar zona horaria"


def test_envelope_rechaza_device_id_con_espacios():
    """Los device_id acaban siendo nombres de archivo (heatmaps, capturas)."""
    try:
        Envelope(client_type=ClientType.TIENDA, site_id="s",
                 device_id="Camera 12/../etc",
                 event_type=EventType.FRAME_INFERENCE)
    except Exception as e:
        assert "identificador invalido" in str(e)
    else:
        raise AssertionError("deberia haber rechazado el device_id")


def test_envelope_rechaza_claves_desconocidas():
    """La cabecera es nueva: puede ser estricta desde el principio."""
    try:
        Envelope(client_type=ClientType.TIENDA, site_id="s", device_id="d",
                 event_type=EventType.HEARTBEAT, campo_inventado=1)
    except Exception:
        pass
    else:
        raise AssertionError("deberia haber rechazado la clave extra")


# ── Payload real ─────────────────────────────────────────────────────────

def test_el_payload_real_valida():
    """EL test del hito: lo que el cliente manda hoy pasa el contrato."""
    fi = FrameInference(**sobre_antiguo_real()["data"])
    assert fi.roi_activate is True
    assert fi.heatmap_activate is True
    assert fi.camera_name == "Camera 12"
    assert fi.camera_angle.value == "frontal"


def test_rechaza_imagen_que_no_es_jpeg():
    try:
        FrameInference(image=b'no soy un jpeg', roi_activate=False)
    except Exception as e:
        assert "JPEG" in str(e)
    else:
        raise AssertionError("deberia haber rechazado la imagen")


def test_rechaza_poligono_de_dos_puntos():
    try:
        FrameInference(image=JPEG, roi_coordinates=[[0, 0], [1, 1]])
    except Exception as e:
        assert "3 puntos" in str(e)
    else:
        raise AssertionError("deberia haber rechazado el poligono")


def test_admite_claves_desconocidas_durante_la_migracion():
    """Un cliente sin actualizar no debe romperse al activar la validacion."""
    fi = FrameInference(image=JPEG, clave_que_no_conocemos="algo")
    assert fi.image == JPEG


# ── Compatibilidad ───────────────────────────────────────────────────────

def test_detecta_el_formato_antiguo():
    assert es_formato_antiguo(sobre_antiguo_real()) is True
    assert es_formato_antiguo({"event_type": "frame.inference"}) is False


def test_traduce_el_mensaje_antiguo_completo():
    env, err = desde_antiguo(sobre_antiguo_real(), site_id="lacomarca")
    assert err is None, err
    assert env.client_type == ClientType.TIENDA
    assert env.pipeline == Pipeline.PERSONAL_AMAZONAS
    assert env.device_id == "de60bb79-236e-4595-8d93-3c5d40bb3e25"
    assert env.event_type == EventType.FRAME_INFERENCE
    # y el payload traducido sigue validando
    FrameInference(**env.payload)


def test_traduce_los_ocho_pipelines():
    """Ningun modo de inferencia vivo puede quedarse fuera del contrato."""
    modos = ["Personal de Amazonas", "Perimetrales", "PerimetralesBoTSORT",
             "PerimetralesMultiCam", "VigilanteAmazonas", "Autolavado",
             "Hummus", "Misters"]
    for m in modos:
        msg = sobre_antiguo_real()
        msg["type_inference"] = m
        env, err = desde_antiguo(msg)
        assert err is None, f"{m}: {err}"
        assert env.pipeline is not None


def test_mensaje_intraducible_no_lanza():
    env, err = desde_antiguo({"type_inference": "ModoInventado", "data": {}})
    assert env is None and "desconocido" in err
    env, err = desde_antiguo({"type_inference": "Hummus"})
    assert env is None and "data" in err


def test_la_respuesta_conserva_la_forma_antigua():
    original = sobre_antiguo_real()
    env, _ = desde_antiguo(original)
    env.payload = {"status": "ok", "processing_time": 0.12}
    salida = hacia_antiguo(env, original)
    assert salida["type_inference"] == "Personal de Amazonas"
    assert salida["component_key"] == original["component_key"]
    assert salida["data"]["status"] == "ok"


# ── Respuestas del servidor ──────────────────────────────────────────────

def test_valida_las_respuestas_reales_del_servidor():
    FrameResult(status="ok", processing_time=0.08, detections_in_roi=3,
                tracks=[{"track_id": 100585, "gender": "Hombre",
                         "age_range": "26-35", "confianza": 0.94}])
    ConnectionInit(id_connection=1851954004384, roi=False)
    Heartbeat(status="ping")
    ErrorEvento(status="error", message="Campo 'image' requerido")


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


def test_un_poligono_vacio_es_no_hay_zona_y_no_un_error():
    """El fallo que el modo `observar` encontro en el HITO 8.

    `get_coordinates()` devuelve `[]` cuando la zona no tiene puntos, y el
    cliente lo manda acompañado de su `*_activate: False`. Rechazarlo habria
    tumbado a los cuatro clientes al pasar la validacion a `estricto`: bastaba
    con no tener un ROI dibujado.
    """
    frame = FrameInference(image=b'\xff\xd8algo', roi_activate=False,
                           roi_coordinates=[], order_zone_coordinates=[],
                           delivery_zone_coordinates=[])
    assert frame.roi_coordinates == []


def test_un_poligono_de_dos_puntos_si_es_un_error():
    """Vacio significa 'no hay zona'; dos puntos significa 'zona rota'."""
    try:
        FrameInference(image=b'\xff\xd8algo', roi_coordinates=[[0, 0], [1, 1]])
    except Exception as exc:
        assert '3 puntos' in str(exc)
    else:
        raise AssertionError('deberia rechazar un poligono de 2 puntos')
