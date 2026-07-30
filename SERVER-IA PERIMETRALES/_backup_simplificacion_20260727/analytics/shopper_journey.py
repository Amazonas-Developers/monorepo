"""
analytics/shopper_journey.py - Recorrido de compra y decision por persona.

Toma los eventos crudos (toma / devolucion / deposito en carrito / consulta
de precio / pasillo) y los convierte en la informacion que le sirve a
marketing y a ventas:

  * Por PRODUCTO: cuantas veces lo agarraron, cuantas lo devolvieron al
    estante, cuantos se lo llevaron, y la TASA DE CONVERSION (se lo llevan /
    lo agarran). Un producto que todo el mundo agarra y casi nadie se lleva
    es un problema de precio o de empaque, y es justo lo que un heatmap
    solo nunca te dice.

  * DUELOS entre productos: cuando una misma persona agarra dos productos
    DISTINTOS dentro de una ventana corta y termina llevandose uno y
    devolviendo el otro, se registra "A gano a B". Agregado sobre muchos
    clientes, eso es una matriz de preferencia real entre marcas.

  * Segmentacion demografica de todo lo anterior: quien agarra que, con la
    taxonomia de negocio Nino / Adolescente / Hombre / Mujer / Anciano /
    Anciana, derivada del genero y el rango de edad que ya calcula el
    pipeline biometrico.

  * Consultas de precio: quien se acerca a la maquina consultora, cuanto
    tiempo, y si despues de consultar se llevo el producto o lo devolvio
    (senal directa de sensibilidad al precio).

Estados de un producto agarrado:

    EN_MANO  -> recien tomado del estante, destino sin decidir.
    LLEVADO  -> confirmado en el carrito/cesta, o la persona se fue del
                encuadre sin haberlo devuelto (ver CART_ASSUME_KEPT).
    DEVUELTO -> volvio al estante.
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .config import AnalyticsConfig
from .shelf_interaction import EVENT_PICKUP, EVENT_RETURN, EVENT_TOUCH

logger = logging.getLogger(__name__)

STATE_IN_HAND = "EN_MANO"
STATE_KEPT = "LLEVADO"
STATE_RETURNED = "DEVUELTO"


def segmento_demografico(gender: Optional[str],
                         age_range: Optional[str]) -> str:
    """Etiqueta de negocio a partir de genero + rango de edad.

    Devuelve: Nino, Nina, Adolescente, Hombre, Mujer, Anciano, Anciana o
    Desconocido. Los rangos de edad son los de ``AnalyticsConfig.AGE_RANGES``
    tal y como los emite el clasificador ("0-12", "26-35", "65+"...).
    """
    g = (gender or "").strip().lower()
    a = (age_range or "").strip()
    # El pipeline emite 'Hombre'/'Mujer' en espanol; se aceptan tambien las
    # etiquetas en ingles por si entra de otra fuente.
    es_hombre = g.startswith("hombre") or g in ("male", "man", "m")
    es_mujer = g.startswith("mujer") or g in ("female", "woman", "f")

    if a in ("0-12",):
        return "Nino" if es_hombre else ("Nina" if es_mujer else "Nino")
    if a in ("13-17",):
        return "Adolescente"
    if a in ("65+", "66+"):
        return "Anciano" if es_hombre else ("Anciana" if es_mujer
                                            else "Anciano")
    if es_hombre:
        return "Hombre"
    if es_mujer:
        return "Mujer"
    return "Desconocido"


class _PickedItem:
    """Un producto que una persona agarro del estante."""

    __slots__ = ("producto", "pasillo", "categoria", "sku", "precio",
                 "estado", "t_toma", "t_decision", "duracion_alcance",
                 "consulto_precio")

    def __init__(self, ev: Dict[str, Any]):
        self.producto = ev.get("producto", "?")
        self.pasillo = ev.get("pasillo", "")
        self.categoria = ev.get("categoria", "")
        self.sku = ev.get("sku", "")
        self.precio = float(ev.get("precio") or 0.0)
        self.estado = STATE_IN_HAND
        self.t_toma = float(ev.get("timestamp") or time.time())
        self.t_decision: Optional[float] = None
        self.duracion_alcance = float(ev.get("duracion_s") or 0.0)
        self.consulto_precio = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "producto": self.producto, "pasillo": self.pasillo,
            "categoria": self.categoria, "sku": self.sku,
            "precio": self.precio, "estado": self.estado,
            "t_toma": self.t_toma, "t_decision": self.t_decision,
            "consulto_precio": self.consulto_precio,
        }


class _Journey:
    """Recorrido de compra de una persona (persistent_id)."""

    def __init__(self, pid: str):
        self.pid = pid
        self.inicio = time.time()
        self.ultimo_visto = self.inicio
        self.segmento = "Desconocido"
        self.genero = "Desconocido"
        self.edad = "Desconocido"
        self.items: List[_PickedItem] = []
        self.toques: Counter = Counter()
        self.pasillos: List[str] = []
        self.consultas_precio: int = 0
        self.tiempo_consulta: float = 0.0
        self.cerrado: bool = False

    def en_mano(self) -> List[_PickedItem]:
        return [i for i in self.items if i.estado == STATE_IN_HAND]

    def llevados(self) -> List[_PickedItem]:
        return [i for i in self.items if i.estado == STATE_KEPT]


class ShopperJourney:
    """Motor de decision de compra y catalogacion para marketing."""

    def __init__(self, config: AnalyticsConfig = None):
        cfg = config or AnalyticsConfig
        self._cfg = cfg
        self._cart_window = float(cfg.CART_DEPOSIT_WINDOW_S)
        self._comp_window = float(cfg.COMPARISON_WINDOW_S)
        self._assume_kept = bool(cfg.CART_ASSUME_KEPT)
        self._idle_close_s = float(cfg.JOURNEY_CLOSE_AFTER_S)
        self._journeys: Dict[str, _Journey] = {}
        # Agregados por producto
        self._prod_tomas: Counter = Counter()
        self._prod_devs: Counter = Counter()
        self._prod_toques: Counter = Counter()
        self._prod_llevados: Counter = Counter()
        self._prod_precio: Dict[str, float] = {}
        self._prod_pasillo: Dict[str, str] = {}
        self._prod_categoria: Dict[str, str] = {}
        # producto -> segmento -> conteo
        self._prod_seg_toma: Dict[str, Counter] = defaultdict(Counter)
        self._prod_seg_llevado: Dict[str, Counter] = defaultdict(Counter)
        self._prod_genero: Dict[str, Counter] = defaultdict(Counter)
        self._prod_edad: Dict[str, Counter] = defaultdict(Counter)
        # Duelos: (ganador, perdedor) -> veces
        self._duelos: Counter = Counter()
        self._comparaciones: List[Dict[str, Any]] = []
        # Consultas de precio
        self._price_checks: List[Dict[str, Any]] = []
        self._price_seg: Counter = Counter()

    # ── Entrada de datos ─────────────────────────────────────────────

    def touch_person(self, pid: str, gender: str = None, age_range: str = None,
                     pasillo: str = None) -> _Journey:
        """Crea/actualiza el recorrido de una persona vista este frame."""
        pid = str(pid)
        j = self._journeys.get(pid)
        if j is None:
            j = _Journey(pid)
            self._journeys[pid] = j
        j.ultimo_visto = time.time()
        if gender and gender != "Desconocido":
            j.genero = gender
        if age_range and age_range != "Desconocido":
            j.edad = age_range
        if j.genero != "Desconocido" or j.edad != "Desconocido":
            j.segmento = segmento_demografico(j.genero, j.edad)
        if pasillo and (not j.pasillos or j.pasillos[-1] != pasillo):
            j.pasillos.append(pasillo)
        return j

    def on_shelf_event(self, ev: Dict[str, Any]) -> None:
        """Procesa un evento TOMA / DEVOLUCION / TOQUE."""
        if ev.get("ambiguo"):
            # Dos personas en el mismo anaquel: no se puede atribuir.
            return
        pid = str(ev.get("persistent_id") or "")
        if not pid:
            return
        j = self.touch_person(pid)
        prod = ev.get("producto", "?")
        self._prod_precio.setdefault(prod, float(ev.get("precio") or 0.0))
        self._prod_pasillo.setdefault(prod, ev.get("pasillo", ""))
        self._prod_categoria.setdefault(prod, ev.get("categoria", ""))
        tipo = ev.get("evento")

        if tipo == EVENT_TOUCH:
            j.toques[prod] += 1
            self._prod_toques[prod] += 1
            return

        if tipo == EVENT_PICKUP:
            item = _PickedItem(ev)
            j.items.append(item)
            self._prod_tomas[prod] += 1
            self._prod_seg_toma[prod][j.segmento] += 1
            self._prod_genero[prod][j.genero] += 1
            self._prod_edad[prod][j.edad] += 1
            return

        if tipo == EVENT_RETURN:
            self._prod_devs[prod] += 1
            # Cerrar el item EN_MANO mas reciente de ese producto.
            for item in reversed(j.items):
                if item.producto == prod and item.estado == STATE_IN_HAND:
                    item.estado = STATE_RETURNED
                    item.t_decision = float(ev.get("timestamp") or time.time())
                    self._maybe_resolve_comparison(j, item)
                    return
            # Devolucion sin toma previa registrada (reposicion de personal
            # o toma perdida): se contabiliza pero no altera ningun item.

    def on_cart_deposit(self, ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """La persona metio la mano en su carrito: confirma lo que llevaba.

        Se asigna al producto EN_MANO mas reciente si fue tomado dentro de
        la ventana ``CART_DEPOSIT_WINDOW_S`` (el tiempo que toma pasar el
        producto del estante al carrito).
        """
        pid = str(ev.get("persistent_id") or "")
        j = self._journeys.get(pid)
        if j is None:
            return None
        now = float(ev.get("timestamp") or time.time())
        candidatos = [i for i in j.en_mano()
                      if (now - i.t_toma) <= self._cart_window]
        if not candidatos:
            return None
        item = max(candidatos, key=lambda i: i.t_toma)
        self._confirm_kept(j, item, now, "carrito")
        return {
            "evento": "producto_al_carrito", "persistent_id": pid,
            "producto": item.producto, "segmento": j.segmento,
            "timestamp": now,
        }

    def on_price_check(self, ev: Dict[str, Any]) -> None:
        """Registra una consulta en la maquina de precios."""
        pid = str(ev.get("persistent_id") or "")
        j = self.touch_person(pid)
        j.consultas_precio += 1
        j.tiempo_consulta += float(ev.get("duracion_s") or 0.0)
        # Marcar los productos que lleva en mano: su decision viene DESPUES
        # de ver el precio -> es lo que mide sensibilidad al precio.
        for item in j.en_mano():
            item.consulto_precio = True
        self._price_seg[j.segmento] += 1
        self._price_checks.append({
            "persistent_id": pid, "segmento": j.segmento,
            "genero": j.genero, "edad": j.edad,
            "duracion_s": round(float(ev.get("duracion_s") or 0.0), 1),
            "maquina": ev.get("maquina", ""),
            "productos_en_mano": [i.producto for i in j.en_mano()],
            "timestamp": float(ev.get("timestamp") or time.time()),
        })

    # ── Decision ─────────────────────────────────────────────────────

    def _confirm_kept(self, j: _Journey, item: _PickedItem, ts: float,
                      via: str) -> None:
        if item.estado != STATE_IN_HAND:
            return
        item.estado = STATE_KEPT
        item.t_decision = ts
        self._prod_llevados[item.producto] += 1
        self._prod_seg_llevado[item.producto][j.segmento] += 1
        logger.debug("Producto LLEVADO (%s): %s por %s", via, item.producto,
                     j.pid)
        self._maybe_resolve_comparison(j, item)

    def _maybe_resolve_comparison(self, j: _Journey,
                                  item: _PickedItem) -> None:
        """Si la persona comparo productos distintos, registra el ganador.

        Criterio: hay al menos otro producto DISTINTO tomado dentro de la
        ventana de comparacion, y entre ambos uno acabo LLEVADO y el otro
        DEVUELTO. Ese es el caso "agarro dos, se quedo con uno".
        """
        ventana = self._comp_window
        cercanos = [
            o for o in j.items
            if o is not item and o.producto != item.producto
            and abs(o.t_toma - item.t_toma) <= ventana
            and o.estado in (STATE_KEPT, STATE_RETURNED)
        ]
        for otro in cercanos:
            if item.estado == STATE_KEPT and otro.estado == STATE_RETURNED:
                ganador, perdedor = item, otro
            elif item.estado == STATE_RETURNED and otro.estado == STATE_KEPT:
                ganador, perdedor = otro, item
            else:
                continue
            par = (ganador.producto, perdedor.producto)
            # No registrar dos veces el mismo duelo del mismo cliente.
            if any(c["persistent_id"] == j.pid
                   and c["elegido"] == par[0] and c["descartado"] == par[1]
                   and abs(c["timestamp"] - max(item.t_decision or 0,
                                                otro.t_decision or 0)) < 1.0
                   for c in self._comparaciones[-20:]):
                continue
            self._duelos[par] += 1
            self._comparaciones.append({
                "persistent_id": j.pid,
                "segmento": j.segmento,
                "genero": j.genero,
                "edad": j.edad,
                "elegido": ganador.producto,
                "descartado": perdedor.producto,
                "precio_elegido": ganador.precio,
                "precio_descartado": perdedor.precio,
                "consulto_precio": bool(ganador.consulto_precio
                                        or perdedor.consulto_precio),
                "timestamp": max(item.t_decision or 0.0,
                                 otro.t_decision or 0.0) or time.time(),
            })

    def close_idle(self, active_pids: set) -> List[Dict[str, Any]]:
        """Cierra los recorridos de quienes ya no estan en escena.

        Si ``CART_ASSUME_KEPT``, todo producto que quedo EN_MANO al irse la
        persona se da por LLEVADO: agarro algo, nunca lo devolvio y se fue
        con ello. Es la unica forma de contabilizar ventas cuando no hay
        deteccion de carrito fiable.
        """
        now = time.time()
        cerrados: List[Dict[str, Any]] = []
        for pid, j in self._journeys.items():
            if j.cerrado or str(pid) in active_pids:
                continue
            if (now - j.ultimo_visto) < self._idle_close_s:
                continue
            j.cerrado = True
            if self._assume_kept:
                for item in j.en_mano():
                    self._confirm_kept(j, item, now, "salida_sin_devolver")
            cerrados.append({
                "evento": "fin_recorrido", "persistent_id": pid,
                "segmento": j.segmento,
                "duracion_s": round(now - j.inicio, 1),
                "productos_llevados": [i.producto for i in j.llevados()],
                "pasillos": list(j.pasillos),
                "timestamp": now,
            })
        return cerrados

    # ── Reportes ─────────────────────────────────────────────────────

    def product_report(self) -> List[Dict[str, Any]]:
        """Una fila por producto, ordenada por interes comercial."""
        productos = set(self._prod_tomas) | set(self._prod_toques) \
            | set(self._prod_devs) | set(self._prod_llevados)
        filas = []
        for p in productos:
            tomas = int(self._prod_tomas[p])
            llevados = int(self._prod_llevados[p])
            devs = int(self._prod_devs[p])
            precio = float(self._prod_precio.get(p, 0.0))
            filas.append({
                "producto": p,
                "pasillo": self._prod_pasillo.get(p, ""),
                "categoria": self._prod_categoria.get(p, ""),
                "precio": precio,
                "toques": int(self._prod_toques[p]),
                "tomas": tomas,
                "devoluciones": devs,
                "llevados": llevados,
                "tasa_conversion": (round(llevados / tomas, 3)
                                    if tomas else 0.0),
                "tasa_abandono": (round(devs / tomas, 3) if tomas else 0.0),
                "ingreso_estimado": round(llevados * precio, 2),
                "por_segmento_toma": dict(self._prod_seg_toma[p]),
                "por_segmento_llevado": dict(self._prod_seg_llevado[p]),
                "por_genero": dict(self._prod_genero[p]),
                "por_edad": dict(self._prod_edad[p]),
            })
        filas.sort(key=lambda f: (f["tomas"], f["toques"]), reverse=True)
        return filas

    def duel_report(self) -> List[Dict[str, Any]]:
        """Matriz de preferencia: producto A elegido sobre B, N veces."""
        return [
            {"elegido": a, "descartado": b, "veces": int(n)}
            for (a, b), n in self._duelos.most_common()
        ]

    def price_check_report(self) -> Dict[str, Any]:
        """Uso de la maquina consultora de precios y su efecto en la compra."""
        tras_consulta_llevados = 0
        tras_consulta_devueltos = 0
        for j in self._journeys.values():
            for item in j.items:
                if not item.consulto_precio:
                    continue
                if item.estado == STATE_KEPT:
                    tras_consulta_llevados += 1
                elif item.estado == STATE_RETURNED:
                    tras_consulta_devueltos += 1
        decididos = tras_consulta_llevados + tras_consulta_devueltos
        return {
            "consultas_totales": len(self._price_checks),
            "personas_distintas": len({c["persistent_id"]
                                       for c in self._price_checks}),
            "por_segmento": dict(self._price_seg),
            "tras_consultar_llevados": tras_consulta_llevados,
            "tras_consultar_devueltos": tras_consulta_devueltos,
            "conversion_tras_consulta": (
                round(tras_consulta_llevados / decididos, 3)
                if decididos else 0.0),
            "ultimas": self._price_checks[-10:],
        }

    def segment_report(self) -> Dict[str, Any]:
        """Comportamiento agregado por segmento demografico."""
        seg_personas: Counter = Counter()
        seg_tomas: Counter = Counter()
        seg_llevados: Counter = Counter()
        seg_ingreso: Dict[str, float] = defaultdict(float)
        seg_prod: Dict[str, Counter] = defaultdict(Counter)
        for j in self._journeys.values():
            seg_personas[j.segmento] += 1
            for item in j.items:
                seg_tomas[j.segmento] += 1
                if item.estado == STATE_KEPT:
                    seg_llevados[j.segmento] += 1
                    seg_ingreso[j.segmento] += item.precio
                    seg_prod[j.segmento][item.producto] += 1
        filas = []
        for seg, n in seg_personas.most_common():
            top = seg_prod[seg].most_common(5)
            filas.append({
                "segmento": seg,
                "personas": int(n),
                "productos_tomados": int(seg_tomas[seg]),
                "productos_llevados": int(seg_llevados[seg]),
                "conversion": (round(seg_llevados[seg] / seg_tomas[seg], 3)
                               if seg_tomas[seg] else 0.0),
                "ingreso_estimado": round(seg_ingreso[seg], 2),
                "top_productos": [{"producto": p, "veces": int(c)}
                                  for p, c in top],
            })
        return {"segmentos": filas}

    def get_stats(self) -> Dict[str, Any]:
        """Resumen liviano (va en el metadata de cada frame)."""
        en_mano = sum(len(j.en_mano()) for j in self._journeys.values())
        tomas = int(sum(self._prod_tomas.values()))
        llevados = int(sum(self._prod_llevados.values()))
        top = self._prod_tomas.most_common(1)
        return {
            "recorridos_activos": sum(1 for j in self._journeys.values()
                                      if not j.cerrado),
            "recorridos_totales": len(self._journeys),
            "productos_tomados": tomas,
            "productos_devueltos": int(sum(self._prod_devs.values())),
            "productos_llevados": llevados,
            "productos_en_mano": en_mano,
            "conversion_global": (round(llevados / tomas, 3)
                                  if tomas else 0.0),
            "producto_mas_tomado": top[0][0] if top else None,
            "comparaciones": len(self._comparaciones),
            "consultas_precio": len(self._price_checks),
        }

    def full_report(self) -> Dict[str, Any]:
        """Reporte completo de marketing/ventas."""
        prods = self.product_report()
        ingreso = round(sum(p["ingreso_estimado"] for p in prods), 2)
        return {
            "resumen": self.get_stats(),
            "ingreso_estimado_total": ingreso,
            "productos": prods,
            "duelos": self.duel_report(),
            "comparaciones_recientes": self._comparaciones[-20:],
            "consulta_precios": self.price_check_report(),
            "demografia": self.segment_report(),
        }

    def reset(self) -> None:
        self._journeys.clear()
        self._prod_tomas.clear()
        self._prod_devs.clear()
        self._prod_toques.clear()
        self._prod_llevados.clear()
        self._prod_seg_toma.clear()
        self._prod_seg_llevado.clear()
        self._prod_genero.clear()
        self._prod_edad.clear()
        self._duelos.clear()
        self._comparaciones.clear()
        self._price_checks.clear()
        self._price_seg.clear()
