"""
Pruebas de la sesion de credenciales de Hik-Connect.

Fijan la propiedad que motiva todo el cambio (H-13): **la credencial no
sobrevive al cierre de sesion ni vive en ningun archivo**. Si alguna de estas
falla, se ha reintroducido el problema que publico las claves en GitHub.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elde_core.config import sesion_hik  # noqa: E402


def _limpio():
    sesion_hik.cerrar()


def test_no_hay_sesion_al_arrancar():
    _limpio()
    assert sesion_hik.activa() is False
    assert sesion_hik.credenciales() == (None, None)


def test_iniciar_publica_en_el_entorno():
    _limpio()
    assert sesion_hik.iniciar('MI_KEY', 'MI_SECRET') is True
    assert sesion_hik.activa() is True
    assert sesion_hik.credenciales() == ('MI_KEY', 'MI_SECRET')
    assert os.environ.get('hik_app_key') == 'MI_KEY'
    _limpio()


def test_cerrar_borra_del_entorno():
    """LA propiedad: cerrar sesion no puede dejar rastro en os.environ."""
    _limpio()
    sesion_hik.iniciar('MI_KEY', 'MI_SECRET')
    sesion_hik.cerrar()
    assert sesion_hik.activa() is False
    assert os.environ.get('hik_app_key') is None
    assert os.environ.get('hik_app_secret') is None


def test_cerrar_es_idempotente():
    _limpio()
    sesion_hik.cerrar()
    sesion_hik.cerrar()          # no debe lanzar


def test_no_inicia_con_credenciales_incompletas():
    """Dejar el entorno a medias es peor que no tocarlo."""
    _limpio()
    for clave, secreto in (('', 'S'), ('K', ''), ('  ', 'S'), ('', '')):
        assert sesion_hik.iniciar(clave, secreto) is False
        assert sesion_hik.activa() is False


def test_ningun_env_del_repositorio_lleva_la_credencial():
    """Ningun `.env` de cliente puede volver a declararla.

    Es la regresion concreta que hay que impedir: la fuga de H-13 empezo por
    tener la App Key en un archivo de configuracion."""
    culpables = []
    for cliente in ('tienda_view', 'perimetrales-view',
                    'windows_managers_view', 'Amazonas View'):
        env = RAIZ / cliente / '.env'
        if not env.is_file():
            continue
        for i, linea in enumerate(
                env.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            limpia = linea.strip()
            if limpia.startswith('#'):
                continue
            if limpia.startswith(('hik_app_key', 'hik_app_secret')):
                culpables.append(f'{cliente}/.env:{i}')
    assert not culpables, (
        'la App Key volvio a un archivo de configuracion: '
        + ', '.join(culpables))


def test_get_url_no_tiene_credenciales_escritas():
    """`get_url.py` fue el archivo que las publico. No puede recaer."""
    import re
    culpables = []
    for cliente in ('tienda_view', 'perimetrales-view',
                    'windows_managers_view', 'Amazonas View'):
        p = RAIZ / cliente / 'get_url.py'
        if not p.is_file():
            continue
        texto = p.read_text(encoding='utf-8', errors='replace')
        # Una asignacion con un literal largo alfanumerico = credencial a pelo.
        if re.search(r'(API_KEY|API_SECRET)\s*=\s*["\'][A-Za-z0-9]{16,}["\']',
                     texto):
            culpables.append(str(p.relative_to(RAIZ)))
    assert not culpables, ('credenciales escritas en el codigo: '
                           + ', '.join(culpables))


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
    _limpio()
    print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLOS'}")
    raise SystemExit(1 if fallos else 0)
