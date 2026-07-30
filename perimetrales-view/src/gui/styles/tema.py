"""
tema.py - Paleta y tokens de diseño de perimetrales-view.

Un unico sitio donde vive el color. Antes cada componente llevaba sus
propios hex sueltos (#1e1e1e, #292929, #333333...), asi que cambiar el
aspecto obligaba a tocar diez archivos y el resultado nunca quedaba
homogeneo.

Criterio del tema: oscuro, sobrio y de alto contraste, como una consola
de analisis. El color se reserva para lo que significa algo — el estado
del sistema y el tipo de deteccion —; el resto es escala de grises. Un
panel de vigilancia se mira durante horas: si todo llama la atencion,
nada la llama.

Comparte paleta con Amazonas View a proposito: los dos clientes se usan
en el mismo puesto y saltar entre ellos no deberia sentirse como cambiar
de programa.
"""

from __future__ import annotations

# ── Superficies (de mas profunda a mas elevada) ─────────────────────────
FONDO = "#0b0e14"          # lienzo de la aplicacion
SUPERFICIE = "#131720"     # paneles y barras
ELEVADO = "#1a1f2b"        # tarjetas sobre un panel
ELEVADO_HOVER = "#212736"  # la misma tarjeta bajo el cursor
BORDE = "#232936"          # separadores y contornos
BORDE_SUAVE = "#1b2029"

# ── Texto ───────────────────────────────────────────────────────────────
TEXTO = "#e6e9ef"          # principal
TEXTO_SUAVE = "#9aa4b8"    # secundario
TEXTO_TENUE = "#5e6878"    # metadatos, marcas de tiempo

# ── Acentos ─────────────────────────────────────────────────────────────
ACENTO = "#3d9dff"         # accion principal, foco, seleccion
ACENTO_HOVER = "#5aabff"
ACENTO_FONDO = "rgba(61, 157, 255, 0.12)"   # relleno sutil al pasar por encima

# ── Estado ──────────────────────────────────────────────────────────────
EXITO = "#00d4aa"          # conectado, analisis activo
AVISO = "#ffb340"
ERROR = "#ff5c5c"
INACTIVO = "#5e6878"

# ── Tipo de deteccion (la clase gruesa que manda el servidor) ──────────
PERSONA = "#3d9dff"
VEHICULO = "#00d4aa"
SIN_CLASE = "#5e6878"

# ── Eventos perimetrales ───────────────────────────────────────────────
EVENTO_LLEGADA = "#00d4aa"
EVENTO_SALIDA = "#ffb340"
EVENTO_MERODEO = "#ff5c5c"
EVENTO_ALERTA = "#ff5c5c"

# ── Geometria ───────────────────────────────────────────────────────────
RADIO = "6px"
RADIO_SM = "4px"
RADIO_LG = "10px"

# Tipografia del sistema. Sin fuentes externas: deben verse igual en
# cualquier equipo donde se despliegue.
FUENTE = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif'
FUENTE_MONO = '"Cascadia Mono", "Consolas", monospace'


def color_clase(gruesa: str | None) -> str:
    """Color de la clase gruesa ('persona' | 'vehiculo')."""
    return {"persona": PERSONA,
            "vehiculo": VEHICULO}.get((gruesa or "").strip().lower(),
                                      SIN_CLASE)


def color_evento(evento: str | None) -> str:
    """Color del tipo de evento perimetral."""
    return {"llegada": EVENTO_LLEGADA, "salida": EVENTO_SALIDA,
            "merodeo": EVENTO_MERODEO, "permanencia": ACENTO,
            "alerta": EVENTO_ALERTA}.get((evento or "").strip().lower(),
                                         TEXTO_SUAVE)


def tarjeta(borde_izquierdo: str | None = None) -> str:
    """QSS de una tarjeta, con franja de color opcional a la izquierda."""
    franja = (f"border-left: 3px solid {borde_izquierdo};"
              if borde_izquierdo else f"border: 1px solid {BORDE};")
    return f"""
        background-color: {ELEVADO};
        {franja}
        border-radius: {RADIO};
    """


def boton(acento: bool = False) -> str:
    """QSS de un boton: `acento=True` para la accion principal."""
    if acento:
        return f"""
            QPushButton {{
                background-color: {ACENTO}; color: #06101c;
                border: none; border-radius: {RADIO_SM};
                padding: 7px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {ACENTO_HOVER}; }}
            QPushButton:disabled {{
                background-color: {BORDE}; color: {TEXTO_TENUE};
            }}
        """
    return f"""
        QPushButton {{
            background-color: transparent; color: {TEXTO_SUAVE};
            border: 1px solid {BORDE}; border-radius: {RADIO_SM};
            padding: 6px 14px; font-size: 12px;
        }}
        QPushButton:hover {{
            color: {TEXTO}; border-color: {ACENTO};
            background-color: {ACENTO_FONDO};
        }}
        QPushButton:disabled {{ color: {TEXTO_TENUE};
                                border-color: {BORDE_SUAVE}; }}
    """
