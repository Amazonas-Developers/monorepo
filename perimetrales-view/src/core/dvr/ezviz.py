"""
src/core/dvr/ezviz.py — EZVIZ Open API (cuentas Hik-Connect de consumo)

POR QUÉ ESTE MÓDULO
-------------------
Hay DOS nubes distintas de Hikvision y cada una tiene su propia API:

  • HikCentral Connect / Hik-Connect for Teams  (hikcentralconnect.com)
    Cuentas de EMPRESA. Credenciales AK/SK del portal de partners.
    -> lo cubre hikconnect.py

  • EZVIZ / Hik-Connect de consumo              (ezvizlife.com)
    Es la cuenta normal de la app móvil Hik-Connect. Su API oficial es la
    "EZVIZ Open API": el usuario registra su PROPIA cuenta en
    https://open.ezvizlife.com y obtiene su AppKey + AppSecret.
    -> lo cubre ESTE módulo

Con esto se puede entrar con CUALQUIER cuenta: cada usuario usa el AppKey/
AppSecret emitido para su propia cuenta.

NOTA sobre el flujo OAuth de iVMS-4200
--------------------------------------
Existe también `openauth.ezvizlife.com/oauth/authorize?...&client_id=...&sign=...`,
que es el login web que usa el cliente de escritorio iVMS-4200. NO se usa aquí:
el `client_id` y la firma `sign` pertenecen a ese programa y reproducirlos exige
extraer su secreto interno (se rompe en cada actualización). La Open API hace lo
mismo de forma estable y soportada.

Detalles del protocolo (distintos a los de hikconnect.py):
  • Los parámetros van FORM-URLENCODED, no JSON.
  • El éxito es code == "200" (string), no errorCode == "0".
"""
from __future__ import annotations

import requests

from .base import ChannelInfo, DeviceInfo, DVRStrategy

# Servidores por región. Una cuenta solo existe en la suya, así que se prueban
# en orden hasta que una devuelva token (igual que hace la app móvil).
REGIONES: list[tuple[str, str]] = [
    ("https://open.ezvizlife.com",       "Global / China"),
    ("https://ieuopen.ezvizlife.com",    "Europa"),
    ("https://iusopen.ezvizlife.com",    "América del Norte"),
    ("https://isgpopen.ezvizlife.com",   "Singapur"),
    ("https://iindiaopen.ezvizlife.com", "India"),
    ("https://irusopen.ezvizlife.com",   "Rusia"),
]

_TOKEN_PATH = "/api/lapp/token/get"
_DEVICES_PATH = "/api/lapp/device/list"
_CAMERAS_PATH = "/api/lapp/camera/list"
_LIVE_PATH = "/api/lapp/live/address/get"

# Mensajes de los códigos de error más habituales de la Open API.
_ERRORES = {
    "10001": "Parámetros incorrectos (revisa AppKey/AppSecret).",
    "10002": "El accessToken caducó o no es válido.",
    "10005": "AppKey bloqueado o deshabilitado.",
    "10017": "AppKey no existe.",
    "10030": "AppKey y AppSecret no coinciden.",
    "20002": "El dispositivo no existe en esta cuenta.",
    "20006": "Fallo de red con el dispositivo.",
    "20007": "El dispositivo está desconectado.",
    "20014": "Número de serie inválido.",
    "20018": "El dispositivo no pertenece a esta cuenta.",
    "60019": "El dispositivo no está compartido con esta cuenta.",
}


def _post(url: str, datos: dict, timeout: int) -> dict:
    """POST form-urlencoded que nunca lanza: devuelve {} si algo falla."""
    try:
        r = requests.post(
            url, data=datos, timeout=timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            return {}
        texto = r.text.strip()
        if not texto or texto.startswith("<"):
            return {}
        return r.json()
    except Exception:
        return {}


class EzvizStrategy(DVRStrategy):
    """Acceso a las cámaras de una cuenta EZVIZ / Hik-Connect de consumo.

    Reutiliza los campos de DVRStrategy:
      username -> AppKey
      password -> AppSecret
      host     -> dominio de región ('' = probar todas)
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token: str = ""
        self._base: str = ""
        self.log: list[str] = []

    @property
    def _app_key(self) -> str:
        return (self.username or "").strip()

    @property
    def _app_secret(self) -> str:
        return (self.password or "").strip()

    # ------------------------------------------------------------- token
    def _obtener_token(self) -> str:
        """Pide el accessToken probando las regiones. Deja self._base fijado."""
        if not self._app_key or not self._app_secret:
            raise PermissionError("Faltan el AppKey y el AppSecret de EZVIZ.")

        bases: list[str] = []
        if self.host:
            h = self.host.strip().rstrip("/")
            bases.append(h if h.startswith("http") else f"https://{h}")
        bases += [b for b, _ in REGIONES if b not in bases]

        fallos: list[str] = []
        for base in bases:
            d = _post(f"{base}{_TOKEN_PATH}",
                      {"appKey": self._app_key, "appSecret": self._app_secret},
                      self.timeout)
            if not d:
                fallos.append(f"{base} → sin respuesta")
                continue
            code = str(d.get("code", ""))
            if code == "200":
                token = (d.get("data") or {}).get("accessToken", "")
                if token:
                    self._base = base
                    self.log.append(f"Token obtenido en {base}")
                    return token
                fallos.append(f"{base} → token vacío")
                continue
            # Credenciales mal formadas: no tiene sentido probar más regiones.
            if code in ("10030", "10017", "10005"):
                raise PermissionError(_ERRORES.get(code, f"[{code}] "
                                                  f"{d.get('msg', '')}"))
            fallos.append(f"{base} → [{code}] {d.get('msg', '')}")

        raise ConnectionError(
            "No se pudo iniciar sesión en EZVIZ:\n"
            + "\n".join(f"  • {f}" for f in fallos)
            + "\n\nRevisa el AppKey/AppSecret en https://open.ezvizlife.com")

    # ------------------------------------------------------- dispositivos
    def _listar(self, ruta: str, clave_extra: dict | None = None) -> list[dict]:
        datos = {"accessToken": self._token, "pageStart": 0, "pageSize": 50}
        datos.update(clave_extra or {})
        d = _post(f"{self._base}{ruta}", datos, self.timeout)
        code = str(d.get("code", ""))
        if code != "200":
            self.log.append(f"{ruta} → [{code}] {d.get('msg', '')}")
            return []
        return d.get("data") or []

    def _url_en_vivo(self, serial: str, canal: int, codigo: str = "") -> str:
        """URL reproducible del canal. Prueba HLS y luego RTMP."""
        for protocolo in (2, 3):        # 2 = HLS, 3 = RTMP
            datos = {
                "accessToken": self._token,
                "deviceSerial": serial,
                "channelNo": canal,
                "protocol": protocolo,
                "quality": 1,           # 1 = alta definición
                "expireTime": 3600,
            }
            if codigo:
                datos["code"] = codigo   # clave de verificación si está cifrado
            d = _post(f"{self._base}{_LIVE_PATH}", datos, self.timeout)
            if str(d.get("code", "")) == "200":
                url = (d.get("data") or {}).get("url", "")
                if url:
                    return url
        return ""

    # --------------------------------------------------------------- API
    def get_device_info(self) -> DeviceInfo:
        self._token = self._obtener_token()

        dispositivos = self._listar(_DEVICES_PATH)
        camaras = self._listar(_CAMERAS_PATH)
        self.log.append(f"{len(dispositivos)} dispositivo(s), "
                        f"{len(camaras)} cámara(s)")

        # Índice serial -> datos del equipo, para nombrar bien cada canal.
        por_serial = {d.get("deviceSerial", ""): d for d in dispositivos}

        canales: list[ChannelInfo] = []
        for cam in camaras:
            serial = cam.get("deviceSerial", "")
            canal_no = cam.get("channelNo", 1)
            equipo = por_serial.get(serial, {})
            nombre = (cam.get("channelName")
                      or equipo.get("deviceName")
                      or f"Cámara {canal_no}")
            url = self._url_en_vivo(
                serial, canal_no,
                codigo=getattr(self, "verification_code", "") or "")
            canales.append(ChannelInfo(
                id=f"{serial}_{canal_no}",
                name=nombre,
                status="active" if equipo.get("status") == 1 else "offline",
                rtsp_main=url,
                rtsp_sub=url,
                extra={
                    "device_serial": serial,
                    "channel_no": canal_no,
                    "resource_id": f"{serial}_{canal_no}",
                    "is_ezviz": True,
                    "stream_encrypt_enable": bool(cam.get("isEncrypt", 0)),
                },
            ))

        principal = dispositivos[0] if dispositivos else {}
        return DeviceInfo(
            brand="EZVIZ",
            model=principal.get("model", ""),
            serial_number=principal.get("deviceSerial", ""),
            firmware_version=principal.get("deviceVersion", ""),
            device_name=principal.get("deviceName", "Cuenta EZVIZ"),
            num_video_channels=len(canales),
            channels=canales,
            extra={
                "access_token": self._token,
                "base_url": self._base,
                "num_devices": len(dispositivos),
                "log": list(self.log),
            },
        )

    def build_rtsp_url(self, channel: int, sub_stream: bool = False) -> str:
        """En la nube no hay RTSP directo: se pide la URL en vivo al vuelo."""
        if not self._token:
            self._token = self._obtener_token()
        dispositivos = self._listar(_DEVICES_PATH)
        if not dispositivos:
            return ""
        return self._url_en_vivo(dispositivos[0].get("deviceSerial", ""), channel)

    # ------------------------------------------------------- utilidades
    @staticmethod
    def refrescar_url(access_token: str, base_url: str, serial: str,
                      canal: int, timeout: int = 10) -> str:
        """Renueva una URL caducada sin rehacer todo el login."""
        d = _post(f"{base_url}{_LIVE_PATH}", {
            "accessToken": access_token, "deviceSerial": serial,
            "channelNo": canal, "protocol": 2, "quality": 1,
            "expireTime": 3600,
        }, timeout)
        if str(d.get("code", "")) == "200":
            return (d.get("data") or {}).get("url", "")
        return ""
