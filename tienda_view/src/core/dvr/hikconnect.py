"""
src/core/dvr/hikconnect.py — Hik-Connect for Teams OpenAPI V2.15.0

Cambios vs versión anterior:
  • _get_live_url() acepta parámetro `code` para streams encriptados
  • Cada canal incluye metadatos: device_serial, resource_id, stream_encrypt_enable
  • Se almacena access_token y area_domain en info.extra para refresh de URLs
  • Nuevo método estático refresh_live_url() para renovar URLs expiradas
  • Nuevo método estático refresh_token() para renovar el token
"""
from __future__ import annotations
import requests
from .base import DVRStrategy, DeviceInfo, ChannelInfo

_REGION_HOSTS = [
    "https://isa.hikcentralconnect.com",
    "https://ius.hikcentralconnect.com",
    "https://ieu.hikcentralconnect.com",
    "https://isgp.hikcentralconnect.com",
]

_TOKEN_PATH   = "/api/hccgw/platform/v1/token/get"
_DEVICES_PATH = "/api/hccgw/resource/v1/devices/get"
_CAMERAS_PATH = "/api/hccgw/resource/v1/areas/cameras/get"
_LIVE_PATH    = "/api/hccgw/video/v1/live/address/get"
_STREAM_TOKEN = "/api/hccgw/platform/v1/streamtoken/get"


def _safe_json(resp) -> dict:
    try:
        t = resp.text.strip()
        if not t or t.startswith("<"):
            return {}
        return resp.json()
    except Exception:
        return {}


class HikConnectStrategy(DVRStrategy):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token: str | None = None
        self._area_domain: str | None = None
        self._debug_log: list[str] = []

    @property
    def _app_key(self): return self.username.strip()
    @property
    def _app_secret(self): return self.password.strip()

    @property
    def _base(self) -> str:
        return (self._area_domain or _REGION_HOSTS[0]).rstrip("/")

    def _h(self, token=False) -> dict:
        h = {"Content-Type": "application/json"}
        if token and self._token:
            h["Token"] = self._token
        return h

    def _log(self, msg): self._debug_log.append(msg)

    # ── Token ─────────────────────────────────────────────────

    def _get_token(self) -> str:
        hosts = list(_REGION_HOSTS)
        user = self.host.strip() if self.host else ""
        if user and "hik-connect.com" not in user and user not in ("open.hikvision.com", ""):
            b = user if user.startswith("http") else f"https://{user}"
            if b not in hosts:
                hosts.insert(0, b)

        errors = []
        for base in hosts:
            try:
                r = requests.post(f"{base}{_TOKEN_PATH}",
                    json={"appKey": self._app_key, "secretKey": self._app_secret},
                    headers=self._h(), timeout=self.timeout)
                if "text/html" in r.headers.get("Content-Type", ""):
                    errors.append(f"{base} → HTML"); continue
                if r.status_code not in (200, 201):
                    errors.append(f"{base} → HTTP {r.status_code}"); continue
                d = _safe_json(r)
                if not d:
                    errors.append(f"{base} → vacío"); continue
                code = str(d.get("errorCode", "-1"))
                if code == "0":
                    inner = d.get("data") or {}
                    tok = inner.get("accessToken", "")
                    if not tok:
                        errors.append(f"{base} → token vacío"); continue
                    area = inner.get("areaDomain", "")
                    self._area_domain = area.rstrip("/") if area else base
                    self._log(f"✅ Token desde: {base}")
                    self._log(f"   areaDomain: {self._area_domain}")
                    return tok
                if code == "LAP300001":
                    errors.append(f"{base} → LAP300001"); continue
                raise PermissionError({
                    "OPEN300001": "AK/SK inválido.",
                    "OPEN300002": "API Secret incorrecto.",
                }.get(code, f"[{code}] {d.get('message', code)}"))
            except PermissionError: raise
            except Exception as e:
                errors.append(f"{base} → {e}"); continue
        raise ConnectionError("Sin token:\n" + "\n".join(f"  • {e}" for e in errors))

    # ── Dispositivos ─────────────────────────────────────────

    def _get_devices(self) -> list[dict]:
        try:
            r = requests.post(f"{self._base}{_DEVICES_PATH}",
                json={"pageIndex": 1, "pageSize": 100},
                headers=self._h(token=True), timeout=self.timeout)
            d = _safe_json(r)
            total = (d.get("data") or {}).get("totalCount", "?")
            devs  = (d.get("data") or {}).get("device", [])
            self._log(f"📦 Devices: HTTP {r.status_code} | code={d.get('errorCode')} "
                      f"| total={total} | devs={len(devs)}")
            return devs
        except Exception as e:
            self._log(f"📦 Devices error: {e}")
            return []

    # ── Cámaras por serial ────────────────────────────────────

    def _get_cameras_by_serial(self, serial: str) -> list[dict]:
        try:
            payload = {
                "pageIndex": 1,
                "pageSize":  200,
                "filter": {"deviceSerialNo": serial},
            }
            r = requests.post(f"{self._base}{_CAMERAS_PATH}",
                json=payload, headers=self._h(token=True), timeout=self.timeout)
            d    = _safe_json(r)
            code = str(d.get("errorCode", "-1"))
            cams = (d.get("data") or {}).get("camera", [])
            total = (d.get("data") or {}).get("totalCount", "?")
            self._log(f"   📷 serial={serial}: HTTP {r.status_code} "
                      f"| code={code} | total={total} | cams={len(cams)}")
            return cams if code == "0" else []
        except Exception as e:
            self._log(f"   📷 serial={serial} error: {e}")
            return []

    def _get_cameras_no_filter(self) -> list[dict]:
        try:
            payload = {"pageIndex": 1, "pageSize": 200, "filter": {}}
            r = requests.post(f"{self._base}{_CAMERAS_PATH}",
                json=payload, headers=self._h(token=True), timeout=self.timeout)
            d    = _safe_json(r)
            code = str(d.get("errorCode", "-1"))
            cams = (d.get("data") or {}).get("camera", [])
            total = (d.get("data") or {}).get("totalCount", "?")
            self._log(f"   📷 sin filtro: HTTP {r.status_code} "
                      f"| code={code} | total={total} | cams={len(cams)}")
            return cams if code == "0" else []
        except Exception as e:
            self._log(f"   📷 sin filtro error: {e}")
            return []

    # ── Live URL ──────────────────────────────────────────────

    @staticmethod
    def _check_hls_segments(url: str, timeout: int = 8) -> bool:
        """
        Descarga el .m3u8 y verifica que los segmentos no sean ErrCode.
        Retorna True si el stream es reproducible, False si está bloqueado.
        """
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code != 200:
                return False
            text = r.text
            # Si los segmentos apuntan a ErrCode → stream bloqueado (EZVIZ MQTT issue)
            if "ErrCode" in text or "errcode" in text.lower():
                return False
            # Debe tener al menos un segmento .ts real
            return ".ts" in text or ".m4s" in text or "http" in text
        except Exception:
            return False

    def _get_live_url(self, resource_id: str, serial: str,
                      quality: int = 1, code: str = "") -> str:
        """
        Obtiene URL de live view.
        Prueba protocolos en orden: HLS(2) → RTSP(1) → RTMP(3).
        Para URLs HLS de EZVIZ/HiLook, verifica el contenido del m3u8
        antes de aceptarla (los segmentos pueden contener ErrCode/9053).

        Returns:
            URL del stream, "ENCRYPTED" si requiere clave, o "" si falla
        """
        for protocol in [2, 1, 3]:  # HLS → RTSP → RTMP
            try:
                body: dict = {
                    "deviceSerial": serial,
                    "resourceId": resource_id,
                    "type": "1",
                    "protocol": protocol,
                    "quality": quality,
                    "expireTime": 3600,
                }
                if code:
                    body["code"] = code
                    body["validateCode"] = code  # EZVIZ usa validateCode en algunos endpoints

                r = requests.post(f"{self._base}{_LIVE_PATH}",
                    json=body,
                    headers=self._h(token=True), timeout=self.timeout)
                d   = _safe_json(r)
                err = str(d.get("errorCode", "-1"))
                if err != "0":
                    msg = d.get("message", err)
                    self._log(f"   ⚠ live URL [{err}] proto={protocol}: {msg}")
                    continue

                url = (d.get("data") or {}).get("url", "")
                if not url:
                    continue

                # ErrCode en la URL misma → encriptación
                if "ErrCode" in url:
                    self._log(f"   🔒 ErrCode en URL (encriptación requerida)")
                    return "ENCRYPTED"

                # ezopen:// no es soportado por FFmpeg/OpenCV → siguiente protocolo
                if url.startswith("ezopen://"):
                    self._log(f"   ⚠ ezopen:// no soportado (proto={protocol}), probando siguiente...")
                    continue

                # Para URLs HLS de EZVIZ → verificar contenido del m3u8
                if "ezvizlife.com" in url and url.endswith(".m3u8"):
                    proto_name = "HLS"
                    self._log(f"   🔍 EZVIZ HLS detectado, verificando segmentos...")
                    if not self._check_hls_segments(url):
                        self._log(f"   ⚠ Segmentos HLS con ErrCode/bloqueados, probando proto={protocol} siguiente...")
                        continue
                    self._log(f"   ✅ {proto_name} EZVIZ verificado OK: {url[:80]}")
                    return url

                proto_name = {1: "RTSP", 2: "HLS", 3: "RTMP"}.get(protocol, str(protocol))
                self._log(f"   ✅ {proto_name} URL obtenida: {url[:80]}")
                return url

            except Exception as e:
                self._log(f"   ⚠ live URL excepción (proto={protocol}): {e}")
        return ""

    # ── get_device_info ───────────────────────────────────────

    def get_device_info(self) -> DeviceInfo:
        self._debug_log = []
        self._token = self._get_token()

        # 1. Obtener dispositivos (con serial de cada uno)
        devices = self._get_devices()

        # 2. Buscar cámaras por serial de cada dispositivo
        cameras: list[dict] = []
        serials = [d.get("serialNo", "") for d in devices if d.get("serialNo")]

        # Mapa serial → encriptación habilitada
        device_encrypt_map: dict[str, bool] = {}
        for dv in devices:
            ser = dv.get("serialNo", "")
            encrypt = str(dv.get("streamEncryptEnable", "0")) == "1"
            device_encrypt_map[ser] = encrypt

        self._log(f"🔎 Buscando cámaras por serial para: {serials}")
        for serial in serials:
            cams = self._get_cameras_by_serial(serial)
            cameras.extend(cams)

        # 3. Si no encontró por serial, intentar sin filtro
        if not cameras:
            self._log("🔎 Intentando sin filtro...")
            cameras = self._get_cameras_no_filter()

        # 4. DeviceInfo
        info = DeviceInfo(brand="Hik-Connect", ip_address=self._base)
        if devices:
            d = devices[0]
            info.model            = d.get("type", "DVR/NVR")
            info.serial_number    = d.get("serialNo", "")
            info.device_name      = d.get("name", "")
            info.firmware_version = d.get("version", "")
            info.extra["total_devices"] = len(devices)
            info.extra["devices"] = [
                f"{dv.get('name','')} ({dv.get('serialNo','')}) "
                f"online={dv.get('onlineStatus','?')}"
                for dv in devices
            ]

        # Guardar datos de sesión para refresh de URLs
        info.extra["debug"] = self._debug_log
        info.extra["access_token"] = self._token
        info.extra["area_domain"] = self._area_domain
        info.extra["app_key"] = self._app_key
        info.extra["app_secret"] = self._app_secret
        info.num_video_channels = len(cameras)

        for i, cam in enumerate(cameras, 1):
            rid       = cam.get("id", "")
            ch_name   = cam.get("name", f"Canal {i}")
            online    = str(cam.get("online", "1"))
            dev_inf   = cam.get("device", {}).get("devInfo", {})
            dev_ser   = dev_inf.get("serialNo", "")
            ch_info   = cam.get("device", {}).get("channelInfo", {})
            ch_no     = str(ch_info.get("id", i))

            # Detectar encriptación
            stream_secret = dev_inf.get("streamSecretKey", "")
            encrypt_flag  = device_encrypt_map.get(dev_ser, False)
            is_encrypted  = encrypt_flag or bool(stream_secret)

            if online == "1":
                rtsp_main = self._get_live_url(rid, dev_ser, quality=1)
                # Detectar si la API retornó ErrCode (encriptación requerida)
                if rtsp_main == "ENCRYPTED":
                    is_encrypted = True
                    rtsp_main = ""
                    rtsp_sub  = ""
                    self._log(f"   🔒 {ch_name}: requiere clave de encriptación")
                else:
                    rtsp_sub = self._get_live_url(rid, dev_ser, quality=2)
                    if rtsp_sub == "ENCRYPTED":
                        rtsp_sub = ""
                    if rtsp_main:
                        self._log(f"   🎬 {ch_name}: {rtsp_main[:90]}...")
                    else:
                        self._log(f"   ❌ {ch_name}: SIN URL")
            else:
                rtsp_main = ""
                rtsp_sub  = ""

            info.channels.append(ChannelInfo(
                id=rid or str(i), name=ch_name,
                status="active" if online == "1" else "inactive",
                rtsp_main=rtsp_main, rtsp_sub=rtsp_sub,
                extra={
                    "device_serial": dev_ser,
                    "resource_id": rid,
                    "channel_no": ch_no,
                    "stream_encrypt_enable": is_encrypted,
                    "is_hikconnect": True,
                },
            ))

        return info

    def build_rtsp_url(self, channel: int, sub_stream: bool = False) -> str:
        return f"[Hik-Connect URL dinámica — canal {channel}]"

    # ── Métodos estáticos para refresh ────────────────────────

    @staticmethod
    def _get_ezviz_stream_token(base: str, access_token: str, timeout: int = 10) -> str:
        """
        Llama a /api/hccgw/platform/v1/streamtoken/get para obtener un token
        de stream EZVIZ válido para usar con la EZVIZ Open Platform API.
        """
        try:
            r = requests.post(
                f"{base}{_STREAM_TOKEN}",
                json={},
                headers={"Content-Type": "application/json", "Token": access_token},
                timeout=timeout,
            )
            d = _safe_json(r)
            err = str(d.get("errorCode", "-1"))
            token = (d.get("data") or {}).get("accessToken", "")
            print(f"[DVR]   → streamtoken err={err} token_len={len(token)}")
            if err == "0" and token:
                return token
        except Exception as e:
            print(f"[DVR]   → streamtoken excepción: {e}")
        return ""

    @staticmethod
    def _ezopen_to_playable(
        ezopen_url: str,
        base: str,
        resource_id: str,
        device_serial: str,
        quality: int,
        timeout: int,
        access_token: str = "",
    ) -> str:
        """
        Convierte ezopen://CODE@open.ezviz.com/SERIAL/CH.hd.live en URL reproducible.

        Estrategia:
          1. Obtener stream token EZVIZ via /api/hccgw/platform/v1/streamtoken/get
          2. Usar ese token + validateCode con EZVIZ lapp/stream/start
          3. Fallback: HLS directo con validateCode en query string
        """
        try:
            # Parsear ezopen: ezopen://CODE@open.ezviz.com/SERIAL/CHANNEL.hd.live
            after_scheme  = ezopen_url.replace("ezopen://", "")
            verify_code   = after_scheme.split("@")[0]
            path_part     = after_scheme.split("@")[1] if "@" in after_scheme else ""
            path_segments = path_part.split("/")
            serial        = path_segments[1] if len(path_segments) > 1 else device_serial
            ch_part       = path_segments[2] if len(path_segments) > 2 else "1.hd.live"
            channel_no    = ch_part.split(".")[0]

            print(f"[DVR]   → ezopen parsed: serial={serial} channel={channel_no} "
                  f"code_len={len(verify_code)}")

            if not verify_code or not channel_no:
                return ""

            # Inferir dominio EZVIZ desde area de HikCentral
            # isa.hikcentralconnect.com → isaopen.ezvizlife.com
            if "hikcentralconnect.com" in base:
                region = base.split("//")[-1].split(".")[0]  # "isa", "ieu", "ius"...
                ezviz_base = f"https://{region}open.ezvizlife.com"
            else:
                ezviz_base = "https://isaopen.ezvizlife.com"

            print(f"[DVR]   → EZVIZ base: {ezviz_base}")

            # 1. Obtener EZVIZ stream token via HikCentral
            ezviz_token = HikConnectStrategy._get_ezviz_stream_token(base, access_token, timeout)

            # 2. Intentar EZVIZ lapp/stream/start con los tokens disponibles
            tokens_to_try = []
            if ezviz_token:
                tokens_to_try.append(("streamtoken", ezviz_token))
            if access_token:
                tokens_to_try.append(("hikconnect", access_token))

            lapp_bases = [ezviz_base, "https://open.ezvizlife.com"]
            for lapp_base in lapp_bases:
                for token_label, token_val in tokens_to_try:
                    for ezviz_proto, pname in [(3, "RTMP"), (2, "RTSP"), (4, "FLV")]:
                        try:
                            params = {
                                "accessToken":  token_val,
                                "deviceSerial": serial,
                                "channelNo":    channel_no,
                                "validateCode": verify_code,
                                "protocol":     str(ezviz_proto),
                                "quality":      str(quality),
                                "expireTime":   "3600",
                            }
                            r = requests.post(
                                f"{lapp_base}/api/lapp/stream/start",
                                data=params,
                                headers={"Content-Type": "application/x-www-form-urlencoded"},
                                timeout=timeout,
                            )
                            d    = _safe_json(r)
                            err  = str(d.get("code", d.get("errorCode", "-1")))
                            data_obj = d.get("data") or {}
                            url  = (data_obj.get("url") or data_obj.get("rtspUrl")
                                    or data_obj.get("rtmpUrl") or "")
                            print(f"[DVR]   → lapp {pname} ({token_label}@{lapp_base.split('//')[-1]}) "
                                  f"err={err} url={url[:80] if url else '(vacía)'} raw={str(d)[:80]}")

                            if err in ("200", "0") and url and "ErrCode" not in url:
                                print(f"[DVR]   → lapp {pname} ✅")
                                return url
                        except Exception as e:
                            print(f"[DVR]   → lapp {pname} excepción: {e}")

            # 3. Fallback: HLS directo con validateCode como query param
            hls_url = (f"{ezviz_base}/v3/openlive/{serial}_{channel_no}_1.m3u8"
                       f"?validateCode={verify_code}")
            print(f"[DVR]   → Probando HLS directo: {hls_url}")
            try:
                resp = requests.get(hls_url, timeout=8)
                text = resp.text if resp.status_code == 200 else ""
                print(f"[DVR]   → HLS directo HTTP={resp.status_code} preview={text[:120]!r}")
                if (resp.status_code == 200 and "ErrCode" not in text
                        and (".ts" in text or ".m4s" in text or "http" in text)):
                    print(f"[DVR]   → HLS directo ✅")
                    return hls_url
            except Exception as e:
                print(f"[DVR]   → HLS directo excepción: {e}")

        except Exception as e:
            print(f"[DVR]   → _ezopen_to_playable excepción: {e}")
        return ""

    @staticmethod
    def refresh_live_url(
        area_domain: str,
        access_token: str,
        resource_id: str,
        device_serial: str,
        quality: int = 1,
        code: str = "",
        timeout: int = 10,
    ) -> str:
        """
        Renueva la URL de live view sin instanciar la estrategia.
        Estrategia:
          1. Prueba HLS/RTMP con code (si fue provisto)
          2. Prueba RTSP (no requiere code) → si devuelve ezopen://, extrae
             el token embebido y lo usa para pedir HLS/RTMP directamente
        """
        base = area_domain.rstrip("/")
        proto_names = {1: "RTSP", 2: "HLS", 3: "RTMP"}

        print(f"[DVR] refresh_live_url: code={'(vacío)' if not code else f'(presente, len={len(code)})'}")

        ezopen_url_found = ""  # guardamos el ezopen para conversión posterior

        for protocol in [2, 1, 3]:
            try:
                body: dict = {
                    "deviceSerial": device_serial,
                    "resourceId": resource_id,
                    "type": "1",
                    "protocol": protocol,
                    "quality": quality,
                    "expireTime": 3600,
                }
                if code:
                    # Probar todos los nombres de campo que usa EZVIZ/HikCentral
                    body["code"]             = code
                    body["validateCode"]     = code
                    body["streamVerifyCode"] = code
                    body["encryptCode"]      = code
                    body["verifyCode"]       = code

                r = requests.post(
                    f"{base}{_LIVE_PATH}",
                    json=body,
                    headers={"Content-Type": "application/json", "Token": access_token},
                    timeout=timeout,
                )
                d = _safe_json(r)
                err_code = str(d.get("errorCode", "-1"))
                url = (d.get("data") or {}).get("url", "")
                pname = proto_names.get(protocol, str(protocol))
                print(f"[DVR] refresh proto={pname} err={err_code} url={url[:100] if url else '(vacía)'}")

                if err_code != "0":
                    print(f"[DVR]   → API error {err_code}: {d.get('message', '')}")
                    continue

                if not url:
                    print(f"[DVR]   → URL vacía")
                    continue

                if "ErrCode" in url:
                    print(f"[DVR]   → ErrCode en URL → clave incorrecta")
                    return ""

                # ezopen://: guardar para conversión, no parar el loop
                if url.startswith("ezopen://"):
                    print(f"[DVR]   → ezopen:// guardado para conversión")
                    if not ezopen_url_found:
                        ezopen_url_found = url
                    continue

                # Para HLS de EZVIZ → verificar contenido del m3u8
                if "ezvizlife.com" in url and url.endswith(".m3u8"):
                    try:
                        resp = requests.get(url, timeout=8)
                        print(f"[DVR]   → m3u8 HTTP {resp.status_code}, preview: {resp.text[:150]!r}")
                        if resp.status_code == 200:
                            text = resp.text
                            if "ErrCode" in text or "errcode" in text.lower():
                                print(f"[DVR]   → ErrCode en segmentos, siguiente protocolo")
                                continue
                            if ".ts" in text or ".m4s" in text or "http" in text:
                                print(f"[DVR]   → m3u8 válido ✅")
                                return url
                        continue
                    except Exception as e:
                        print(f"[DVR]   → Error descargando m3u8: {e}")
                        continue

                print(f"[DVR]   → URL aceptada ✅")
                return url

            except Exception as e:
                print(f"[DVR] refresh proto={protocol} excepción: {e}")
                continue

        # Si obtuvimos un ezopen://, intentar convertirlo con su token embebido
        if ezopen_url_found:
            print(f"[DVR] Intentando convertir ezopen:// con token embebido...")
            result = HikConnectStrategy._ezopen_to_playable(
                ezopen_url_found, base, resource_id, device_serial, quality, timeout,
                access_token=access_token,
            )
            if result:
                return result

        print(f"[DVR] refresh_live_url: todos los protocolos fallaron")
        return ""


    @staticmethod
    def refresh_token(app_key: str, app_secret: str, timeout: int = 10) -> dict:
        """
        Renueva el token. Retorna dict con access_token, area_domain, expire_time.
        """
        for base in _REGION_HOSTS:
            try:
                r = requests.post(
                    f"{base}{_TOKEN_PATH}",
                    json={"appKey": app_key, "secretKey": app_secret},
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
                d = _safe_json(r)
                if str(d.get("errorCode", "-1")) == "0":
                    inner = d.get("data") or {}
                    tok = inner.get("accessToken", "")
                    if tok:
                        area = inner.get("areaDomain", base).rstrip("/")
                        return {
                            "access_token": tok,
                            "area_domain": area,
                            "expire_time": inner.get("expireTime", 0),
                        }
            except Exception:
                continue
        return {}
