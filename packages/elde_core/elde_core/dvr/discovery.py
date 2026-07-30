"""
src/core/dvr/discovery.py
Descubrimiento de dispositivos (DVR / NVR / cámaras IP) en la red local.

Tres sondas complementarias, ejecutadas en paralelo y fusionadas por IP:

  1. SADP      — protocolo propietario de Hikvision (UDP multicast
                 239.255.255.250:37020). Es el que usa la herramienta SADP
                 oficial: devuelve modelo, serie, MAC, firmware y puertos
                 SIN credenciales. Es la vía más rica para Hikvision.
  2. WS-Discovery (ONVIF) — UDP multicast 239.255.255.250:3702. Estándar de
                 la industria: encuentra cámaras/NVR de CUALQUIER marca que
                 hable ONVIF (Dahua, Hikvision, genéricas...).
  3. Barrido TCP — conecta a los puertos típicos (80/8000/554/37777...) de
                 cada IP del rango. Cubre equipos con multicast bloqueado
                 (VLAN/switch) y confirma qué servicios están abiertos.

Sin dependencias externas: solo sockets de la stdlib. Nada bloquea la GUI si
se llama desde un hilo (ver DiscoveryWorker en workers/).
"""
from __future__ import annotations

import re
import socket
import struct
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# Multicast estándar SSDP/WS-Discovery (Hikvision reutiliza el grupo con su
# propio puerto 37020).
_MCAST_GRP = "239.255.255.250"
_SADP_PORT = 37020
_WSD_PORT = 3702

# Puertos que delatan un equipo de videovigilancia.
PUERTOS_SONDA: dict[int, str] = {
    80:    "HTTP",
    8000:  "Hikvision SDK",
    554:   "RTSP",
    37777: "Dahua SDK",
    8080:  "HTTP alt",
    443:   "HTTPS",
}

# Prefijos MAC (OUI) conocidos -> marca. Solo para etiquetar, no es
# exhaustivo: si no coincide se deja la marca que reporte el protocolo.
_OUI_MARCAS: dict[str, str] = {
    "44:19:b6": "Hikvision", "c0:56:e3": "Hikvision", "bc:ad:28": "Hikvision",
    "4c:bd:8f": "Hikvision", "58:03:fb": "Hikvision", "a4:14:37": "Hikvision",
    "e0:ca:3c": "Hikvision", "18:68:cb": "Hikvision", "c4:2f:90": "Hikvision",
    "3c:ef:8c": "Dahua",     "90:02:a9": "Dahua",     "e0:50:8b": "Dahua",
    "bc:32:5f": "Dahua",     "14:a7:8b": "Dahua",
}


@dataclass
class DispositivoRed:
    """Un equipo encontrado en la red. Los campos vacíos son 'no reportado'."""
    ip: str
    marca: str = ""
    modelo: str = ""
    nombre: str = ""
    mac: str = ""
    serie: str = ""
    firmware: str = ""
    puerto_http: int = 0
    puerto_sdk: int = 0
    puertos_abiertos: list[int] = field(default_factory=list)
    origen: set[str] = field(default_factory=set)   # {"SADP", "ONVIF", "TCP"}

    @property
    def descripcion(self) -> str:
        partes = [p for p in (self.marca, self.modelo) if p]
        return " ".join(partes) or "Dispositivo desconocido"

    @property
    def marca_ui(self) -> str:
        """Marca tal como la espera el ComboBox del formulario DVR."""
        m = (self.marca or "").lower()
        if "hikvision" in m or "hiwatch" in m or "hilook" in m:
            return "Hikvision HTTP"
        if "dahua" in m or "amcrest" in m or "lorex" in m:
            return "Dahua HTTP"
        # Sin marca clara: deducir por los puertos abiertos.
        if 37777 in self.puertos_abiertos:
            return "Dahua HTTP"
        if 8000 in self.puertos_abiertos:
            return "Hikvision HTTP"
        return "Hikvision HTTP"

    @property
    def es_equipo_video(self) -> bool:
        """¿Es (casi seguro) un DVR/NVR/cámara y no un PC o router?

        Lo es si respondió a SADP/ONVIF (protocolos de videovigilancia) o si
        expone un puerto inequívoco: RTSP (554) o un SDK de fabricante. Un
        equipo con solo 80/443/8080 puede ser cualquier cosa, así que no se
        da por bueno.
        """
        if self.origen & {"SADP", "ONVIF"}:
            return True
        return bool({554, 8000, 37777} & set(self.puertos_abiertos))

    @property
    def puerto_ui(self) -> int:
        if self.puerto_http:
            return self.puerto_http
        for p in (80, 8080, 443):
            if p in self.puertos_abiertos:
                return p
        return 80


# ─────────────────────────────────────────────────────────────────────
# Utilidades de red
# ─────────────────────────────────────────────────────────────────────

def ips_locales() -> list[str]:
    """IPv4 de las interfaces activas (sin loopback). Se usan para enviar el
    multicast por CADA interfaz: con varias NIC (Wi-Fi + Ethernet) el sistema
    elige una sola y el descubrimiento fallaría en la otra red."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    if not ips:
        # Truco del socket UDP: no envía nada, solo resuelve la ruta de salida.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return sorted(ips)


def rango_de_ip(ip: str) -> str:
    """'192.168.1.37' -> '192.168.1' (asume /24, el caso normal en LAN)."""
    partes = ip.split(".")
    return ".".join(partes[:3]) if len(partes) == 4 else ""


def _marca_por_mac(mac: str) -> str:
    if not mac:
        return ""
    clave = mac.lower().replace("-", ":")[:8]
    return _OUI_MARCAS.get(clave, "")


# ─────────────────────────────────────────────────────────────────────
# 1) SADP — Hikvision
# ─────────────────────────────────────────────────────────────────────

_SADP_INQUIRY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<Probe><Uuid>{uuid}</Uuid><Types>inquiry</Types></Probe>"
)


def _texto(nodo, *nombres: str) -> str:
    """Primer hijo que exista con alguno de esos nombres (case-insensitive)."""
    for hijo in nodo.iter():
        etiqueta = hijo.tag.split("}")[-1].lower()
        for n in nombres:
            if etiqueta == n.lower() and hijo.text:
                return hijo.text.strip()
    return ""


def sondear_sadp(timeout: float = 4.0) -> list[DispositivoRed]:
    """Descubre equipos Hikvision por SADP. Devuelve [] si nada responde."""
    encontrados: dict[str, DispositivoRed] = {}
    lock = threading.Lock()

    def _en_interfaz(ip_local: str) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((ip_local, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(ip_local))
            sock.settimeout(0.5)
            mensaje = _SADP_INQUIRY.format(uuid=str(uuid.uuid4()).upper()).encode()
            for destino in ((_MCAST_GRP, _SADP_PORT),
                            ("255.255.255.255", _SADP_PORT)):
                try:
                    sock.sendto(mensaje, destino)
                except Exception:
                    pass
            fin = time.time() + timeout
            while time.time() < fin:
                try:
                    datos, origen = sock.recvfrom(8192)
                except socket.timeout:
                    continue
                except Exception:
                    break
                d = _parsear_sadp(datos, origen[0])
                if d:
                    with lock:
                        if d.ip not in encontrados:
                            encontrados[d.ip] = d
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    hilos = [threading.Thread(target=_en_interfaz, args=(ip,), daemon=True)
             for ip in ips_locales()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout + 2)
    return list(encontrados.values())


def _parsear_sadp(datos: bytes, ip_origen: str) -> DispositivoRed | None:
    try:
        texto = datos.decode("utf-8", errors="ignore")
        if "<" not in texto:
            return None
        raiz = ET.fromstring(texto[texto.index("<"):])
    except Exception:
        return None
    ip = _texto(raiz, "IPv4Address", "IPv4address") or ip_origen
    if not ip:
        return None
    mac = _texto(raiz, "MAC", "MACAddress")
    marca = _marca_por_mac(mac) or "Hikvision"
    def _int(v: str) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    serie = _texto(raiz, "DeviceSN", "SerialNumber")
    modelo = _texto(raiz, "DeviceType", "DeviceDescription")
    # SADP suele devolver DeviceType como código numérico (44497). El modelo
    # legible va al principio del nº de serie: "DVR-216Q-F1162018..." o
    # "DS-2CV1023G2-LIDWF20...". Se extrae el prefijo hasta el bloque de dígitos.
    if modelo.isdigit() and serie:
        m = re.match(r"^([A-Za-z]{2,}[\w\-/]*?)(?=\d{6,})", serie)
        if m:
            modelo = m.group(1).rstrip("-")
    return DispositivoRed(
        ip=ip,
        marca=marca,
        modelo=modelo,
        nombre=_texto(raiz, "DeviceName", "DeviceDescription"),
        mac=mac,
        serie=serie,
        firmware=_texto(raiz, "SoftwareVersion", "FirmwareVersion"),
        puerto_http=_int(_texto(raiz, "HttpPort")) or 80,
        puerto_sdk=_int(_texto(raiz, "CommandPort", "Port")) or 8000,
        origen={"SADP"},
    )


# ─────────────────────────────────────────────────────────────────────
# 2) WS-Discovery (ONVIF) — cualquier marca
# ─────────────────────────────────────────────────────────────────────

_WSD_PROBE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
    'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
    'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<e:Header>"
    "<w:MessageID>uuid:{uuid}</w:MessageID>"
    "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
    "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
    "</e:Header>"
    "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
    "</e:Envelope>"
)


def sondear_onvif(timeout: float = 4.0) -> list[DispositivoRed]:
    """Descubre cámaras/NVR ONVIF de cualquier marca."""
    encontrados: dict[str, DispositivoRed] = {}
    lock = threading.Lock()

    def _en_interfaz(ip_local: str) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((ip_local, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(ip_local))
            sock.settimeout(0.5)
            mensaje = _WSD_PROBE.format(uuid=str(uuid.uuid4())).encode()
            try:
                sock.sendto(mensaje, (_MCAST_GRP, _WSD_PORT))
            except Exception:
                pass
            fin = time.time() + timeout
            while time.time() < fin:
                try:
                    datos, origen = sock.recvfrom(16384)
                except socket.timeout:
                    continue
                except Exception:
                    break
                d = _parsear_onvif(datos, origen[0])
                if d:
                    with lock:
                        if d.ip not in encontrados:
                            encontrados[d.ip] = d
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    hilos = [threading.Thread(target=_en_interfaz, args=(ip,), daemon=True)
             for ip in ips_locales()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout + 2)
    return list(encontrados.values())


def _parsear_onvif(datos: bytes, ip_origen: str) -> DispositivoRed | None:
    try:
        texto = datos.decode("utf-8", errors="ignore")
    except Exception:
        return None
    if "ProbeMatch" not in texto and "XAddrs" not in texto:
        return None
    # La IP real va en XAddrs (http://192.168.1.64/onvif/device_service).
    ip = ip_origen
    m = re.search(r"https?://([\d.]+)[:/]", texto)
    if m:
        ip = m.group(1)
    # Los Scopes traen marca/modelo/nombre en texto plano.
    marca = modelo = nombre = ""
    for campo, patron in (("marca", r"onvif://www\.onvif\.org/manufacturer/([^\s<]+)"),
                          ("modelo", r"onvif://www\.onvif\.org/hardware/([^\s<]+)"),
                          ("nombre", r"onvif://www\.onvif\.org/name/([^\s<]+)")):
        mm = re.search(patron, texto)
        if mm:
            valor = mm.group(1).replace("%20", " ").strip()
            if campo == "marca":
                marca = valor
            elif campo == "modelo":
                modelo = valor
            else:
                nombre = valor
    return DispositivoRed(ip=ip, marca=marca, modelo=modelo, nombre=nombre,
                          origen={"ONVIF"})


# ─────────────────────────────────────────────────────────────────────
# 3) Barrido TCP
# ─────────────────────────────────────────────────────────────────────

def _puerto_abierto(ip: str, puerto: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, puerto)) == 0
    except Exception:
        return False


def barrer_rango(rango: str, puertos: list[int] | None = None,
                 timeout: float = 0.35, hilos: int = 128,
                 progreso=None, cancelado=None) -> list[DispositivoRed]:
    """Escanea 'x.y.z.1-254' probando los puertos típicos de videovigilancia.

    `rango`: los tres primeros octetos ('192.168.1').
    `progreso(hechos, total)`: callback opcional para la barra de la UI.
    `cancelado()`: si devuelve True, aborta cuanto antes.
    """
    puertos = puertos or list(PUERTOS_SONDA.keys())
    if not rango:
        return []
    objetivos = [f"{rango}.{i}" for i in range(1, 255)]
    total = len(objetivos)
    hechos = 0
    resultados: list[DispositivoRed] = []
    lock = threading.Lock()

    def _revisar(ip: str) -> None:
        nonlocal hechos
        if cancelado and cancelado():
            return
        abiertos = [p for p in puertos if _puerto_abierto(ip, p, timeout)]
        with lock:
            hechos += 1
            if progreso:
                try:
                    progreso(hechos, total)
                except Exception:
                    pass
            # Un equipo de vídeo casi siempre expone RTSP o un puerto SDK; si
            # solo tiene 80/443 podría ser un PC o router -> se reporta igual
            # pero sin marca, y el usuario decide.
            if abiertos:
                resultados.append(DispositivoRed(
                    ip=ip,
                    puerto_http=(80 if 80 in abiertos else
                                 (8080 if 8080 in abiertos else 0)),
                    puerto_sdk=(8000 if 8000 in abiertos else
                                (37777 if 37777 in abiertos else 0)),
                    puertos_abiertos=abiertos,
                    origen={"TCP"},
                ))

    with ThreadPoolExecutor(max_workers=hilos) as pool:
        list(pool.map(_revisar, objetivos))
    return resultados


# ─────────────────────────────────────────────────────────────────────
# Orquestador
# ─────────────────────────────────────────────────────────────────────

def _fusionar(destino: dict[str, DispositivoRed], nuevos: list[DispositivoRed]) -> None:
    """Une por IP: los datos ricos (SADP/ONVIF) ganan sobre los del barrido."""
    for d in nuevos:
        actual = destino.get(d.ip)
        if actual is None:
            destino[d.ip] = d
            continue
        actual.origen |= d.origen
        for campo in ("marca", "modelo", "nombre", "mac", "serie", "firmware"):
            if not getattr(actual, campo) and getattr(d, campo):
                setattr(actual, campo, getattr(d, campo))
        if not actual.puerto_http and d.puerto_http:
            actual.puerto_http = d.puerto_http
        if not actual.puerto_sdk and d.puerto_sdk:
            actual.puerto_sdk = d.puerto_sdk
        actual.puertos_abiertos = sorted(
            set(actual.puertos_abiertos) | set(d.puertos_abiertos))


def descubrir(incluir_barrido: bool = True, rango: str = "",
              timeout_multicast: float = 4.0,
              progreso=None, cancelado=None) -> list[DispositivoRed]:
    """Descubrimiento completo: SADP + ONVIF (+ barrido TCP opcional).

    Devuelve la lista fusionada y ordenada por IP. Nunca lanza: una sonda que
    falle (firewall, permisos) simplemente no aporta resultados.
    """
    hallazgos: dict[str, DispositivoRed] = {}

    def _aviso(texto: str, hechos: int = 0, total: int = 0) -> None:
        if progreso:
            try:
                progreso(texto, hechos, total)
            except Exception:
                pass

    _aviso("Buscando equipos Hikvision (SADP)…")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_sadp = pool.submit(sondear_sadp, timeout_multicast)
        f_onvif = pool.submit(sondear_onvif, timeout_multicast)
        try:
            _fusionar(hallazgos, f_sadp.result())
        except Exception:
            pass
        _aviso("Buscando cámaras ONVIF…")
        try:
            _fusionar(hallazgos, f_onvif.result())
        except Exception:
            pass

    if incluir_barrido and not (cancelado and cancelado()):
        rangos = [rango] if rango else sorted(
            {r for r in (rango_de_ip(ip) for ip in ips_locales()) if r})
        for r in rangos:
            _aviso(f"Escaneando la red {r}.0/24…")
            try:
                _fusionar(hallazgos, barrer_rango(
                    r,
                    progreso=(lambda h, t, _r=r: _aviso(
                        f"Escaneando {_r}.0/24…", h, t)),
                    cancelado=cancelado))
            except Exception:
                pass

    def _clave(d: DispositivoRed) -> tuple:
        try:
            return tuple(int(p) for p in d.ip.split("."))
        except Exception:
            return (0, 0, 0, 0)

    return sorted(hallazgos.values(), key=_clave)
