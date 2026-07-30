"""
Pruebas del registro unificado (`elde_core.logging`).

Lo que se fija aqui es lo que hace util al modulo: que la identidad del emisor
salga en cada linea (sin eso, juntar los logs de cuatro clientes no sirve de
nada), que llamarlo dos veces no duplique cada mensaje, y que un mensaje con
emoji no tumbe el proceso.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RAIZ / 'packages' / 'elde_core'))

from elde_core.logging import configurar, obtener, registro  # noqa: E402


def _cerrar():
    """Deja el modulo como recien importado (es un singleton a proposito).

    En Windows hay que cerrar el manejador ANTES de borrar la carpeta: si el
    archivo sigue abierto, `TemporaryDirectory` no puede limpiarla y el error
    que sale (`NotADirectoryError`) no se parece en nada a la causa.
    """
    log = logging.getLogger(registro.RAIZ)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    registro._configurado = False


@contextlib.contextmanager
def _registro_en_carpeta_temporal(client_type: str, site_id: str):
    """Configura el registro en una carpeta desechable y lo desmonta al salir."""
    _cerrar()
    with tempfile.TemporaryDirectory() as carpeta:
        os.environ['ELDE_LOG_DIR'] = carpeta
        try:
            configurar(client_type, site_id, capturar_excepciones=False)
            yield Path(carpeta) / f'{client_type}.log'
        finally:
            os.environ.pop('ELDE_LOG_DIR', None)
            _cerrar()


def test_cada_linea_lleva_client_type_y_site_id():
    with _registro_en_carpeta_temporal('tienda', 'tienda-principal') as archivo:
        obtener('prueba').info('hola')
        texto = archivo.read_text(encoding='utf-8')
    assert 'tienda/tienda-principal' in texto, (
        'sin la identidad del emisor no se pueden comparar los logs de los '
        f'cuatro clientes. Escrito: {texto!r}')
    assert 'elde.prueba' in texto and 'hola' in texto


def test_configurar_dos_veces_no_duplica_los_mensajes():
    with _registro_en_carpeta_temporal('managers', 'managers-principal'):
        n = len(logging.getLogger(registro.RAIZ).handlers)
        configurar('managers', 'managers-principal', capturar_excepciones=False)
        m = len(logging.getLogger(registro.RAIZ).handlers)
    assert n == m, (f'la segunda llamada anadio manejadores ({n} -> {m}): '
                    'cada mensaje se escribiria por duplicado')


def test_un_emoji_no_tumba_el_registro():
    """Los clientes imprimen 📄 ✅ 🔥. En consola cp1252 eso reventaba el
    arranque; el logger no puede repetir el mismo error."""
    with _registro_en_carpeta_temporal('amazonas', 'amazonas-principal') as arch:
        obtener('prueba').info('camara lista 📄 ✅ 🔥')
        texto = arch.read_text(encoding='utf-8')
    assert '📄' in texto, 'el archivo debe ser UTF-8 y conservar el emoji'


def test_el_excepthook_se_encadena_y_no_traga_la_excepcion():
    """Registrar el fallo no debe cambiar lo que pasa despues: el hook que
    hubiera antes tiene que seguir ejecutandose."""
    _cerrar()
    llamado = []
    anterior = sys.excepthook
    sys.excepthook = lambda *a: llamado.append(a)
    try:
        registro._instalar_excepthook(logging.getLogger(registro.RAIZ))
        sys.excepthook(ValueError, ValueError('x'), None)
    finally:
        sys.excepthook = anterior
    assert llamado, 'el excepthook anterior debe seguir recibiendo la excepcion'


def test_el_nivel_sale_del_entorno_y_no_del_codigo():
    """Regla 6: nada incrustado."""
    _cerrar()
    os.environ['ELDE_LOG_NIVEL'] = 'DEBUG'
    try:
        assert registro._nivel() == logging.DEBUG
        os.environ['ELDE_LOG_NIVEL'] = 'disparate'
        assert registro._nivel() == logging.INFO, 'un valor invalido cae a INFO'
    finally:
        os.environ.pop('ELDE_LOG_NIVEL', None)


def test_tambien_raiz_captura_los_loggers_del_servidor():
    """El servidor tiene 24 modulos con `getLogger(__name__)`, fuera del
    prefijo `elde`. Sin `tambien_raiz` no escribirian en el archivo comun, que
    es justo lo que el HITO 8 necesita."""
    _cerrar()
    raiz = logging.getLogger()
    previos = list(raiz.handlers)
    nivel_previo = raiz.level
    for h in previos:
        raiz.removeHandler(h)
    try:
        with tempfile.TemporaryDirectory() as carpeta:
            os.environ['ELDE_LOG_DIR'] = carpeta
            try:
                configurar('server', 'sitio-unico',
                           capturar_excepciones=False, tambien_raiz=True)
                # Un logger como los del servidor: NO cuelga de `elde`.
                logging.getLogger('src.app.app').warning('desde el servidor')
                texto = (Path(carpeta) / 'server.log').read_text(
                    encoding='utf-8')
            finally:
                os.environ.pop('ELDE_LOG_DIR', None)
                for h in list(raiz.handlers):
                    raiz.removeHandler(h)
                _cerrar()
    finally:
        for h in previos:
            raiz.addHandler(h)
        raiz.setLevel(nivel_previo)
    assert 'src.app.app' in texto and 'desde el servidor' in texto, (
        f'los loggers del servidor no llegaron al archivo comun: {texto!r}')
    assert 'server/sitio-unico' in texto


def test_la_carpeta_por_defecto_cuelga_de_la_raiz_que_se_le_pase():
    """No debe escribir donde caiga el directorio de trabajo del momento."""
    _cerrar()
    os.environ.pop('ELDE_LOG_DIR', None)
    assert registro._carpeta(Path('X:/proyecto')) == Path('X:/proyecto/logs')
