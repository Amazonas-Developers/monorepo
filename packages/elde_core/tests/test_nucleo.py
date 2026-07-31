"""
Pruebas del nucleo compartido (HITO 4).

Comprueban las dos propiedades que hacen que la extraccion sea correcta:

1. **Los modulos del nucleo se importan solos**, sin arrastrar nada de un
   cliente concreto. Si alguno necesitara `core.x` o `gui.y`, la extraccion
   estaria mal hecha.
2. **Los alias de los clientes resuelven al nucleo.** Un cliente que siga
   cargando su copia antigua no se habria migrado de verdad.

Necesitan una `QApplication` porque varios modulos son widgets o QObject.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

MODULOS = [
    'elde_core.capture.window_grab',
    'elde_core.capture.window_controller',
    'elde_core.capture.windows_detector',
    'elde_core.capture.window_monitor',
    'elde_core.capture.hwnd_state',
    'elde_core.capture.list_windows',
    'elde_core.transport.socket_client',
    'elde_core.transport.jarvis_api',
    'elde_core.config.app_singleton',
    'elde_core.config.settings_model',
]

# alias en el cliente -> modulo del nucleo al que debe resolver
ALIAS = {
    'core.capture_exaple': 'window_grab',
    'core.window_controller': 'window_controller',
    'core.windows_detector': 'windows_detector',
    'core.window_global': 'window_monitor',
    'core.state_global.hwnd': 'hwnd_state',
    'model.windows.list_windows': 'list_windows',
    'core.network.socket_client': 'socket_client',
    'core.network.jarvis_api': 'jarvis_api',
    'core.app_singleton': 'app_singleton',
    'model.settings_model': 'settings_model',
}

CLIENTES_MIGRADOS = ['clients/tienda', 'clients/perimetrales',
                     'clients/managers']


def test_todos_los_modulos_del_nucleo_importan():
    fallos = []
    for m in MODULOS:
        try:
            __import__(m, fromlist=['*'])
        except Exception as exc:
            fallos.append(f'{m}: {type(exc).__name__}: {exc}')
    assert not fallos, 'modulos del nucleo que no importan:\n  ' + \
        '\n  '.join(fallos)


def test_el_nucleo_no_depende_de_ningun_cliente():
    """Criterio de aceptacion del hito: cero dependencias hacia un cliente."""
    import ast
    prohibidos = ('core.', 'gui.', 'model.', 'workers.')
    fallos = []
    base = Path(__file__).resolve().parents[1] / 'elde_core'
    for py in base.rglob('*.py'):
        arbol = ast.parse(py.read_text(encoding='utf-8', errors='replace'))
        for n in ast.walk(arbol):
            mod = None
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mod = n.module
            elif isinstance(n, ast.Import):
                mod = n.names[0].name
            if mod and mod.startswith(prohibidos):
                fallos.append(f'{py.name}:{n.lineno} importa {mod}')
    assert not fallos, 'el nucleo depende de codigo de cliente:\n  ' + \
        '\n  '.join(fallos)


def test_windows_detector_stop_absorbe_el_argumento_de_destroyed():
    """`QObject.destroyed` entrega el objeto destruido como argumento.

    Con la firma `stop(self, msec=2000)` ese QObject aterrizaba en `msec` y
    `wait(QObject)` lanzaba TypeError al cerrar. Se detecto al migrar."""
    from elde_core.capture.windows_detector import WindowScannerThread
    import inspect
    firma = inspect.signature(WindowScannerThread.stop)
    tiene_varargs = any(p.kind == p.VAR_POSITIONAL
                        for p in firma.parameters.values())
    assert tiene_varargs, (
        'stop() debe aceptar *args: se conecta a destroyed, que pasa el '
        f'QObject destruido. Firma actual: {firma}')
    msec = firma.parameters.get('msec')
    assert msec is not None and msec.kind == msec.KEYWORD_ONLY, \
        'msec debe ser de solo palabra clave, para que destroyed no lo pise'


def test_las_carpetas_de_los_clientes_existen():
    """Guardia contra el fallo silencioso.

    Los dos tests de alias saltan el cliente cuyo `src/` no existe, asi que si
    una ruta se queda obsoleta pasan en VACIO en vez de fallar. Al mover los
    clientes a `clients/` el 30-jul-2026 quedaron obsoletas las tres a la vez.
    Este test es el que se entera."""
    faltan = [c for c in CLIENTES_MIGRADOS if not (RAIZ / c / 'src').is_dir()]
    assert not faltan, (
        'CLIENTES_MIGRADOS apunta a carpetas que no existen: '
        + ', '.join(faltan))


def test_los_alias_de_los_clientes_resuelven_al_nucleo():
    fallos = []
    for cliente in CLIENTES_MIGRADOS:
        src = RAIZ / cliente / 'src'
        if not src.is_dir():
            continue
        for alias, esperado in ALIAS.items():
            ruta = src / (alias.replace('.', '/') + '.py')
            if not ruta.is_file():
                continue
            texto = ruta.read_text(encoding='utf-8', errors='replace')
            if 'elde_core' not in texto:
                fallos.append(f'{cliente}/{alias}: sigue con su copia propia')
            elif esperado not in texto:
                fallos.append(f'{cliente}/{alias}: apunta al modulo equivocado')
    assert not fallos, 'alias mal migrados:\n  ' + '\n  '.join(fallos)


def test_el_alias_es_un_redireccion_y_no_una_copia():
    """El alias debe ser corto: si tiene cuerpo, se duplico en vez de mover."""
    demasiado_largo = []
    for cliente in CLIENTES_MIGRADOS:
        src = RAIZ / cliente / 'src'
        for alias in ALIAS:
            ruta = src / (alias.replace('.', '/') + '.py')
            if ruta.is_file():
                lineas = [l for l in ruta.read_text(
                    encoding='utf-8', errors='replace').splitlines()
                    if l.strip()]
                if len(lineas) > 15:
                    demasiado_largo.append(f'{cliente}/{alias}: {len(lineas)} lineas')
    assert not demasiado_largo, 'alias con cuerpo propio:\n  ' + \
        '\n  '.join(demasiado_largo)


def test_el_monitor_se_registra_en_atexit():
    """El arreglo de H-01 tiene que valer para los 4 clientes, no solo tienda.

    El nucleo puede tener el `stop()` correcto y aun asi abortar al cerrar si
    nadie lo llama: solo el `main.py` de tienda conectaba `stop_scanner` a
    `aboutToQuit`, y perimetrales y managers seguian saliendo con 0xc0000409.
    Registrarlo en `atexit` desde el propio nucleo lo resuelve para todos."""
    import inspect
    from elde_core.capture import window_monitor
    src = inspect.getsource(window_monitor)
    assert 'atexit.register' in src, (
        'el monitor debe registrarse en atexit: sin eso, el arreglo de H-01 '
        'solo funciona en los clientes cuyo main.py llama a stop_scanner()')
    assert hasattr(window_monitor.windows_monitor, 'stop_scanner')


def test_stop_scanner_es_idempotente():
    """Se llama desde atexit Y desde el main.py de tienda: no puede molestar
    que se invoque dos veces."""
    from elde_core.capture.window_monitor import windows_monitor
    windows_monitor.stop_scanner()
    windows_monitor.stop_scanner()      # segunda vez: no debe lanzar


def test_el_paquete_dvr_importa_completo():
    modulos = ['base', 'context', 'dahua_http', 'dahua_sdk', 'discovery',
               'ezviz', 'hikconnect', 'hikconnect_channel_encoder',
               'hikvision_http', 'hikvision_sdk']
    fallos = []
    for m in modulos:
        try:
            __import__(f'elde_core.dvr.{m}', fromlist=['*'])
        except Exception as exc:
            fallos.append(f'{m}: {type(exc).__name__}: {exc}')
    assert not fallos, 'modulos dvr que no importan:\n  ' + '\n  '.join(fallos)


def test_no_se_pierden_los_arreglos_de_hikconnect():
    """Fija los 3 arreglos por los que gano la version de perimetrales-view.

    Si una reconciliacion futura toma la version equivocada, estos fallan. Cada
    uno corresponde a un fallo medido contra la cuenta real, documentado en el
    codigo y en 04_NUCLEO_COMPARTIDO.md."""
    import inspect
    from elde_core.dvr import hikconnect
    src = inspect.getsource(hikconnect)

    # 1. La URL del m3u8 trae query, asi que endswith() nunca acertaba y la
    #    verificacion del contenido no se ejecutaba.
    assert '".m3u8" in url' in src, 'se perdio el arreglo de deteccion del m3u8'
    assert 'url.endswith(".m3u8")' not in src, \
        'volvio el endswith(".m3u8"), que falla con la query de la URL'

    # 2. El campo `online` de la nube no es fiable: filtrar por el descartaba
    #    canales que si transmiten.
    assert 'if online == "1":' not in src, \
        'volvio el filtro por online=="1", medido como poco fiable'

    # 3. Marca de equipo bloqueado por la nube (ErrCode en los segmentos).
    assert '_hls_bloqueado' in src, 'se perdio la deteccion de HLS bloqueado'


def test_dvr_admite_codigo_de_verificacion():
    """Necesario para desbloquear streams cifrados. Aditivo: defecto ''."""
    import inspect
    from elde_core.dvr.base import DVRStrategy
    p = inspect.signature(DVRStrategy.__init__).parameters
    assert 'verification_code' in p, 'falta verification_code en DVRStrategy'
    assert p['verification_code'].default == '', \
        'verification_code debe tener defecto vacio para no romper llamadas'


def test_dvr_tiene_la_estrategia_ezviz():
    """Venia solo en perimetrales-view; al reconciliar la reciben los 3."""
    import inspect
    from elde_core.dvr import context
    assert 'EzvizStrategy' in inspect.getsource(context), \
        'se perdio la estrategia EZVIZ al reconciliar'


if __name__ == '__main__':
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'  OK    {nombre}')
            except Exception as exc:
                fallos += 1
                print(f'  FALLA {nombre}: {exc}')
    print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
    raise SystemExit(1 if fallos else 0)


def test_base_http_sin_configuracion_no_inventa_localhost():
    """El fallback silencioso a `http://127.0.0.1:9000` era el antipatron de
    H-02: fallar hacia un servidor que quiza no es el tuyo. Sin URL y sin
    entorno, la respuesta correcta es vacio (error VISIBLE al usarla)."""
    import os
    from elde_core.ui.panel_capturas import base_http_del_websocket
    previo = os.environ.pop('server_ws_url', None)
    try:
        assert base_http_del_websocket('') == ''
        assert '127.0.0.1' not in base_http_del_websocket('')
        # Con socket: deriva del socket.
        assert (base_http_del_websocket('ws://192.0.2.7:9000/ws')
                == 'http://192.0.2.7:9000')
        # Sin socket pero con entorno (que ajustes garantiza): deriva de ahi.
        os.environ['server_ws_url'] = 'ws://192.0.2.9:9000/ws'
        assert base_http_del_websocket('') == 'http://192.0.2.9:9000'
    finally:
        os.environ.pop('server_ws_url', None)
        if previo is not None:
            os.environ['server_ws_url'] = previo


def test_jarvis_api_conserva_el_superconjunto_de_perimetrales():
    """La regresion del 31-jul: el HITO 4 extrajo la version identica en 3
    clientes y ENTERRO la de perimetrales, que habia divergido por razones
    reales. El cliente crasheo al arrancar (`establecimiento`) y habria
    vuelto a fallar en la primera alerta (`enviar_novedad_async`).

    Importar no lo detectaba: hizo falta CONSTRUIR. Esta prueba fija la
    firma y los metodos para que ninguna reconciliacion futura los pierda."""
    import inspect
    from elde_core.transport.jarvis_api import Jarvis_api
    firma = inspect.signature(Jarvis_api.__init__)
    assert 'establecimiento' in firma.parameters, \
        'perimetrales pasa establecimiento= desde su .env'
    assert firma.parameters['establecimiento'].default is None, \
        'debe ser opcional: los otros 3 clientes no lo mandan'
    for metodo in ('enviar_novedad_async', 'subir_imagen_async',
                   'selection_establishment'):
        assert hasattr(Jarvis_api, metodo), \
            f'{metodo} lo usa jarvis_alert_forwarder de perimetrales'


def test_el_worker_de_captura_funciona_COMO_SCRIPT():
    """H-23: `capture_woker.py` no se importa, se EJECUTA en un subproceso.

    Un alias de modulo a secas (`sys.modules[__name__] = _modulo`) hacia que
    el subproceso terminara al instante sin capturar: al correr como script
    `__name__` es `"__main__"`, pero el modulo del nucleo se importa con su
    nombre real y su guarda `if __name__ == "__main__"` nunca corria. Efecto
    visible: el boton Play no hacia nada, sin error ni frames.

    Los alias normales NO necesitan esto; este es el unico que se ejecuta.
    """
    import ast
    for cliente in CLIENTES_MIGRADOS + ['clients/amazonas']:
        ruta = RAIZ / cliente / 'src' / 'workers' / 'capture_woker.py'
        if not ruta.is_file():
            continue
        texto = ruta.read_text(encoding='utf-8', errors='replace')
        assert '__main__' in texto, (
            f'{cliente}: el alias del worker no replica la guarda de script; '
            'el subproceso saldria sin capturar (H-23)')
        assert 'ejecutar_worker' in texto, (
            f'{cliente}: debe llamar a ejecutar_worker() en modo script')
        ast.parse(texto)          # y debe seguir siendo Python valido


def test_el_overlay_de_supervision_esta_disponible():
    """H-24: `sv_overlay` importa `supervision` bajo try/except.

    Si falta, el overlay se apaga EN SILENCIO: no hay error ni traza, pero
    desaparecen las cajas de deteccion, las zonas del ROI y las estelas. Se
    percibe como "el cliente no identifica y no activa el ROI".

    Paso al recrear los venv aislados (H-04): antes `supervision` llegaba del
    user-site global y no estaba declarado en ningun requirements.
    """
    from elde_core.ui import sv_overlay
    assert sv_overlay.sv is not None, (
        'supervision no esta instalado en este entorno: el overlay del modo '
        'directo quedaria apagado sin decir nada (H-24). '
        'pip install supervision==0.28.0')
    assert sv_overlay.SupervisionOverlay is not None
