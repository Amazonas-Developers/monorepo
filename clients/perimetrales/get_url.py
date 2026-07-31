"""
get_url.py — Obtiene y muestra las URLs completas de Hik-Connect
Ejecutar desde la carpeta del proyecto: python get_url.py
"""
import sys
sys.path.insert(0, 'src')

from core.dvr.hikconnect import HikConnectStrategy

# ── Credenciales ──────────────────────────────────────────────
# NUNCA escribir aqui la App Key ni el Secret: este archivo se comparte y
# quedo publicado en GitHub con las claves en claro (ver HALLAZGOS.md H-13).
# Se leen del .env, igual que hace el panel de dispositivos con su boton
# "Usar credenciales del .env".
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY    = (os.getenv("hik_app_key") or "").strip()
API_SECRET = (os.getenv("hik_app_secret") or "").strip()

if not API_KEY or not API_SECRET:
    raise SystemExit(
        "Faltan las credenciales. Anade al .env de este cliente:"
        + chr(10) + "    hik_app_key = 'TU_APP_KEY'"
        + chr(10) + "    hik_app_secret = 'TU_APP_SECRET'"
        + chr(10) + "Se obtienen en el portal de Hik-Connect para empresas."
    )
# ─────────────────────────────────────────────────────────────
print("Conectando a Hik-Connect...")
s = HikConnectStrategy(
    host     = "isa.hik-connect.com",
    port     = 443,
    username = API_KEY,
    password = API_SECRET,
    timeout  = 30,
)

try:
    info = s.get_device_info()
    print(f"✅ {info.device_name} — {info.num_video_channels} canales\n")

    online = [ch for ch in info.channels if ch.status == "active" and ch.rtsp_main]
    print(f"Canales online con URL: {len(online)}\n")

    for ch in online[:5]:
        print(f"Canal: {ch.name}")
        print(f"  URL: {ch.rtsp_main}")
        print()

except Exception as e:
    print(f"Error: {e}")
