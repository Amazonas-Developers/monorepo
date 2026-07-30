import sys, os, json, base64, time
import time
from datetime import datetime
import win32gui
import win32ui
import win32con
from PIL import Image
import ctypes
from ctypes import wintypes
import io

# 🔥 SUPRIMIR LOGS NO DESEADOS AL INICIO
import warnings
warnings.filterwarnings('ignore')

# Configurar variables de entorno para Qt ANTES de cualquier import
os.environ['QT_LOGGING_RULES'] = '*.debug=false;qt.*=false'
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = ''

# Configurar PrintWindow
PrintWindow = ctypes.windll.user32.PrintWindow
PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
PrintWindow.restype = wintypes.BOOL

# Cap de ANCHO del frame que se envia al servidor. El process_frame del
# servidor escala ~cuadraticamente con los pixeles (YOLO + crops de
# demografia + draw): a 1920px ~210ms/frame (5 fps), a 1280px ~130ms,
# a 960px ~87ms (11 fps). Reducir el ancho dispara el FPS. En camaras
# lejanas (caras pequenas que ya no se detectan) NO se pierde demografia.
# Subir para mas nitidez/precision facial; bajar para mas FPS.
# Override por variable de entorno: CAPTURE_MAX_WIDTH.
try:
    MAX_SEND_WIDTH = int(os.environ.get("CAPTURE_MAX_WIDTH", "1600"))
except ValueError:
    MAX_SEND_WIDTH = 1600




def capture_window_by_hwnd(hwnd, pw_flag=2):
    try:
        # Obtener dimensiones de la ventana
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            return None

        # Obtener el contexto del dispositivo de la ventana
        hwndDC = win32gui.GetWindowDC(hwnd)
        if not hwndDC:
            return None

        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        # Crear bitmap compatible
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(saveBitMap)

        # Intentar capturar con PrintWindow (flag elegido por el caller)
        result = PrintWindow(hwnd, saveDC.GetSafeHdc(), pw_flag)

        if not result:
            # Fallback a BitBlt
            result = saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)

        if result:
            # Convertir a formato PIL Image
            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)
            im = Image.frombuffer(
                'RGB',
                (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                bmpstr, 'raw', 'BGRX', 0, 1
            )



            # Limpiar recursos
            win32gui.DeleteObject(saveBitMap.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwndDC)

            return im
        else:
            # Limpiar recursos en caso de error
            win32gui.DeleteObject(saveBitMap.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwndDC)
            return None

    except Exception:
        return None


def pil_image_to_png_bytes(imagen_pil, format="PNG", quality=None):
    try:
        buffer = io.BytesIO()
        is_quality = quality if quality else None
        imagen_pil.save(buffer, format=format, quality=is_quality)
        png_bytes = buffer.getvalue()
        buffer.close()
        return png_bytes
    except Exception:
        return None


def _looks_blank(im):
    """True si la imagen es casi uniforme (negra/vacia): senal de que
    PrintWindow con flag 0 no capturo el contenido de una app GPU."""
    try:
        lo, hi = im.convert('L').resize((32, 32)).getextrema()
        return (hi - lo) < 8
    except Exception:
        return False


import msgpack  # Agrega esta línea para serialización binaria


def _escribir_salida(data: bytes) -> None:
    """Escribe los bytes msgpack al pipe del proceso padre.

    OJO empaquetado: con PyInstaller en modo ventana (console=False)
    `sys.stdout` puede ser None, pero el descriptor 1 heredado del padre
    (QProcess crea el pipe) SÍ es válido. Por eso se cae a os.write(1).
    """
    try:
        salida = getattr(sys.stdout, "buffer", None)
        if salida is not None:
            salida.write(data)
            sys.stdout.flush()
            return
    except Exception:
        pass
    os.write(1, data)


def ejecutar_worker(hwnd: int) -> None:
    """Bucle de captura de la ventana `hwnd`: emite frames JPEG por stdout
    (msgpack) hasta que el proceso padre lo cierre.

    Se usa de DOS formas:
      - en desarrollo:  python src/workers/capture_woker.py <hwnd>
      - empaquetado:    PerimetralesView.exe --capture-worker <hwnd>
        (el exe se re-invoca a sí mismo; ver main.py. Sin esto, lanzar
         sys.executable abriría otra copia de la aplicación completa.)
    """
    try:
        # Flag de PrintWindow auto-seleccionado por ventana:
        #   0 = GDI estandar (~3x mas rapido: ~11ms vs ~33ms) para apps normales.
        #   2 = PW_RENDERFULLCONTENT, necesario para apps con GPU (Chrome/Brave/
        #       DirectX) que con flag 0 saldrian en negro.
        # Probamos flag 0 primero; si sale "en blanco" caemos a flag 2 y
        # cacheamos la decision (no se recalcula en cada frame).
        pw_flag = None

        while True:
            if pw_flag is None:
                buffer = capture_window_by_hwnd(hwnd, 0)
                if buffer is None or _looks_blank(buffer):
                    alt = capture_window_by_hwnd(hwnd, 2)
                    if alt is not None and not _looks_blank(alt):
                        pw_flag, buffer = 2, alt      # app GPU: fijar flag 2
                    elif buffer is None:
                        buffer = alt
                    # si ambos salen en blanco la ventana aun no renderiza:
                    # no fijamos pw_flag y reintentamos el proximo frame.
                else:
                    pw_flag = 0                       # app normal: flag 0 rapido
            else:
                buffer = capture_window_by_hwnd(hwnd, pw_flag)

            # Cap de resolucion: reduce el ancho antes de codificar/enviar.
            # Acelera encode + red + process_frame del servidor.
            if buffer is not None and MAX_SEND_WIDTH > 0 and buffer.width > MAX_SEND_WIDTH:
                _nh = max(1, int(buffer.height * MAX_SEND_WIDTH / buffer.width))
                buffer = buffer.resize((MAX_SEND_WIDTH, _nh))

            if buffer:
                image_bytes = pil_image_to_png_bytes(buffer, 'JPEG', 70)
                if image_bytes:
                    header = {
                        "timestamp": datetime.now().isoformat(),
                        "size": len(image_bytes),
                        "format": "JPEG"
                    }

                    # Serializar mensaje binario con MessagePack
                    message = {
                        'header': header,
                        'image_bytes': image_bytes
                    }
                    binary_message = msgpack.packb(message)

                    # Enviar bytes directamente al pipe del padre
                    _escribir_salida(binary_message)

            time.sleep(1 / 60)

    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        # 🔥 SILENCIAR CUALQUIER EXCEPCIÓN EN PRODUCCIÓN
        pass


# 🔥 MANEJO SEGURO DE ARGUMENTOS (modo script, en desarrollo)
if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    try:
        ejecutar_worker(int(sys.argv[1]))
    except (ValueError, TypeError):
        sys.exit(1)
