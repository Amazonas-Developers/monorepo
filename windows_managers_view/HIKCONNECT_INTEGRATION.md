# Integración Hik-Connect — Guía de Uso (v2)

## 📋 Resumen

Soporte completo para **Hik-Connect for Teams OpenAPI V2.15.0**:
- Autenticación con App Key + App Secret (AK/SK)
- Carga automática de dispositivos y cámaras
- Detección de encriptación de stream → pide clave antes de renderizar
- Compatibilidad total con canales IP existentes (patrón Strategy)
- Drag & drop desde sidebar y panel de canales
- Cierre de sesión y gestión de cuentas
- Refresh automático de token y URLs

## 🚀 Flujo de Integración

### 1. Agregar Cuenta Hik-Connect

1. Pestaña **Dispositivos** → Tipo: **"Hik-Connect"**
2. Completar App Key y App Secret
3. **"🔌 Probar conexión"** → autentica y carga cámaras
4. **"✅ Guardar dispositivo"** → persiste cifrado

### 2. Ver y Arrastrar Canales

- Click en la cuenta → se muestran canales en "Canales disponibles"
- Los canales aparecen también en el sidebar DVR Tree
- Canales Hik-Connect: 🔐 | Canales IP: 📹
- Arrastrar un canal al render_box para iniciar streaming

### 3. Cámaras con Encriptación

Si un canal tiene `streamEncryptEnable`, al hacer drop:
1. Aparece diálogo pidiendo la **clave de encriptación del dispositivo**
2. Se renueva el token y se obtiene URL con la clave (`code`)
3. Si la clave es correcta, inicia el stream
4. Si no, muestra error sin iniciar

### 4. Cerrar Sesión

1. Seleccionar cuenta → **"🔓 Cerrar sesión"**
2. Elimina cuenta, canales y tokens asociados

## 🔧 Archivos Modificados (v2)

| Archivo | Cambio |
|---------|--------|
| `core/dvr/base.py` | `ChannelInfo.extra: dict` para metadatos persistentes |
| `core/dvr/hikconnect.py` | `code` en `_get_live_url()`, metadatos por canal, `refresh_live_url()`, `refresh_token()` |
| `core/dvr/__init__.py` | Exporta `HikConnectStrategy` |
| `gui/components/sidebar/dvr_tree.py` | Drag payload incluye `is_hikconnect`, `device_serial`, `stream_encrypt_enable` |
| `gui/components/render_box/render_box.py` | Diálogo de clave de encriptación, refresh de URL con clave |

## 🎯 Patrón Strategy

```
DVRContext (Punto de entrada único)
    ├── HikvisionHTTPStrategy   (IP:80)
    ├── HikvisionSDKStrategy    (IP:8000, DLL)
    ├── DahuaHTTPStrategy       (IP:80)
    ├── DahuaSDKStrategy        (IP:37777, DLL)
    └── HikConnectStrategy      (Cloud, AK/SK)

Metadatos por canal Hik-Connect:
    ├── device_serial          → serial del NVR/DVR
    ├── resource_id            → ID del recurso cámara
    ├── stream_encrypt_enable  → True si requiere clave
    ├── is_hikconnect          → True
    └── channel_no             → número de canal
```

## 🔐 Endpoints API Utilizados

| Endpoint | Uso |
|----------|-----|
| `POST /api/hccgw/platform/v1/token/get` | Autenticación AK/SK → token |
| `POST /api/hccgw/resource/v1/devices/get` | Listar dispositivos |
| `POST /api/hccgw/resource/v1/areas/cameras/get` | Listar cámaras por serial |
| `POST /api/hccgw/video/v1/live/address/get` | Obtener URL de stream (con `code` si encriptado) |

## 🚨 Troubleshooting

| Problema | Solución |
|----------|----------|
| "Sin token: LAP300001" | AK/SK no válidos para esa región |
| "OPEN300002" | App Secret incorrecto |
| "No se pudo abrir stream" | Token expirado, clave incorrecta, o cámara offline |
| "Clave de encriptación requerida" | El dispositivo tiene encriptación habilitada |
| Drag no incluye metadatos HC | Actualizar `dvr_tree.py` (ya corregido en v2) |
