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

CLIENTES_MIGRADOS = ['tienda_view', 'perimetrales-view',
                     'windows_managers_view']


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
