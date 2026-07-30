"""
Identidad estable de una camara. Primera rebanada del despiece de `render_box`.

El problema (H-11)
------------------
Los cuatro clientes mandaban como `camera_id` su `component_key`, que es un
`uuid.uuid4()` generado al **construir el panel**. Cada arranque de la
aplicacion inventaba camaras nuevas a ojos del servidor, asi que los heatmaps,
el conteo y la demografia se fragmentaban en un UUID por sesion: no habia
historico por zona. El planograma tambien se guardaba por ese id, asi que las
zonas dibujadas se perdian en cada reinicio.

Se corrigio en tienda el 30-jul-2026, con la idea de replicarlo cuando
`render_box` pasara al nucleo. Como el despiece se retraso, los otros tres
clientes se quedaron con el fallo. Esto es esa parte del despiece: la identidad
vive **una vez** y los cuatro la usan.

`component_key` no desaparece: sigue siendo la clave de enrutado del widget (el
servidor no la usa para nada), asi que dos recuadros que muestren la misma
camara comparten `device_id` sin pisarse las respuestas.
"""
from __future__ import annotations

#: Longitud maxima por defecto. El valor acaba siendo un nombre de archivo.
LIMITE = 64


def slug(texto: str, limite: int = LIMITE) -> str:
    """Deja el texto apto para identificador Y para nombre de archivo.

    Importa porque el `device_id` acaba siendo el nombre de los heatmaps
    (`output/heatmap/<device_id>.png`) y de las capturas.

    Se admiten letras, digitos, `_`, `-` y puntos sueltos. Deliberadamente MAS
    restrictivo que el contrato del HITO 3, que tambien permitiria `:`: los dos
    puntos son validos en el envelope pero **ilegales en un nombre de archivo de
    Windows**, y este valor termina siendo uno. Ademas se colapsan los puntos
    seguidos, para que ningun `..` sobreviva y nadie pueda salirse de la
    carpeta.
    """
    limpio = ''.join(
        c if (c.isalnum() or c in '_-.') else '_' for c in (texto or ''))
    while '..' in limpio:
        limpio = limpio.replace('..', '.')
    return limpio.strip('._-')[:limite].strip('._-') or 'sin_nombre'


def device_id(serie_dvr: str = '',
              canal_dvr: str = '',
              titulo_ventana: str = '',
              indice: int = 0) -> str:
    """Identificador ESTABLE de la camara, para el `camera_id` del payload.

    Prioridad, de mas estable a menos:

    1. **Canal DVR** — numero de serie del equipo + canal. Identifica una
       camara fisica y no cambia nunca.
    2. **Ventana capturada** — su titulo. Estable mientras la aplicacion de
       origen se llame igual.
    3. **Posicion del recuadro** — ultimo recurso; estable dentro de una misma
       disposicion de la rejilla.
    """
    if serie_dvr or canal_dvr:
        serie = slug(serie_dvr, 40)
        canal = slug(canal_dvr, 12)
        return f'dvr-{serie}-{canal}' if canal else f'dvr-{serie}'

    titulo = (titulo_ventana or '').strip()
    if titulo:
        return f'win-{slug(titulo, 72)}'

    return f'box-{int(indice) + 1}'


def nombre_visible(nombre_dvr: str = '',
                   titulo_ventana: str = '',
                   indice: int = 0) -> str:
    """Nombre legible de la camara para el dashboard.

    Es el compañero de `device_id`: uno identifica, el otro se enseña. Van
    juntos porque se deducen de las mismas tres fuentes y en el mismo orden.
    """
    if nombre_dvr:
        return nombre_dvr
    titulo = (titulo_ventana or '').strip()
    if titulo:
        return titulo[:48]
    return f'Camara {int(indice) + 1}'
