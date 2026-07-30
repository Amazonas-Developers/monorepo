"""
Panel lateral del cliente de TIENDA.

El armazon (tarjeta, columna, contador, enrutado, scroll) vive en
`elde_core.ui.panel_alertas`. Aqui solo esta lo propio de este dominio: **que
columnas tiene el panel de una tienda y como se clasifica cada alerta**.

## Por que estas columnas y no «Vehiculos / Personas»

Este cliente corre un unico modo, `Personal de Amazonas`, cuyo pipeline no
detecta vehiculos ni produce intrusiones: produce **visitantes con genero y
edad**, conteo de aforo y **capturas** de cada persona. El panel refleja eso.

## Por que se reemplazo el panel anterior

Las dos columnas antiguas —«Clientes y Productos» y «Operacion y
Reposicion»— se alimentaban EXCLUSIVAMENTE de eventos de analitica de retail
(«Anaquel vacio», «Caja obstruyendo», «Persona evaluando producto»). El
servidor **dejo de producirlos** en la simplificacion del 27-jul-2026: hoy no
emite `metadata['retail']` en ninguna parte del codigo. Es decir, el panel
llevaba semanas sin poder mostrar nada.

Esas categorias se siguen reconociendo por si la analitica de tienda vuelve,
pero caen en Visitantes en lugar de tener columna propia.
"""

from PySide6.QtCore import Signal

from elde_core.ui.panel_alertas import Columna, PanelAlertas

# Columnas del panel, de izquierda a derecha.
COLUMNAS = [
    Columna('visitantes', 'Visitantes', '👥', '#00a8e8'),
    Columna('capturas',   'Capturas',   '📸', '#2ecc71'),
]

# Eventos de la analitica de retail: ya no llegan (ver cabecera). Si vuelven,
# se muestran en Visitantes en vez de perderse en silencio.
_EVENTOS_RETAIL = ('anaquel', 'caja', 'obstru', 'reposicion', 'reposición',
                   'evaluando', 'stock', 'mercancia', 'mercancía', 'empleado')


def clasificar(alerta: dict) -> str:
    """Devuelve la clave de la columna donde va la alerta.

    Cadena vacia = se descarta. Aqui no se descarta nada: en una tienda toda
    deteccion es informacion de visitantes."""
    evento = str(alerta.get('event_type') or '').strip().lower()
    clase = str(alerta.get('class_name') or '').strip().lower()
    texto = f'{evento} {clase}'

    if 'captura' in texto:
        return 'capturas'
    if any(p in texto for p in _EVENTOS_RETAIL):
        return 'visitantes'
    return 'visitantes'


class AlertsSidebar(PanelAlertas):
    """Panel de alertas de tienda.

    Conserva el nombre `AlertsSidebar`, el slot `add_alert` y la senal
    `new_alert` porque `main.py` ya los usa
    (`box.alert_received.connect(alerts_sidebar.add_alert)`); cambiarlos
    obligaria a tocar los cuatro clientes sin ganar nada."""

    new_alert = Signal(dict)

    def __init__(self, parent=None, title='Visitantes', max_alerts=200,
                 auto_clear_after=None):
        super().__init__(COLUMNAS, clasificar, titulo=title,
                         max_alertas=max_alerts, parent=parent)

    def add_alert(self, alerta: dict) -> None:
        super().add_alert(alerta)
        try:
            self.new_alert.emit(alerta)
        except Exception:
            pass
