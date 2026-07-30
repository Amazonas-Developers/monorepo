"""
analytics - Modulos de analitica retail en tiempo real.

Submodulos:
    demographics       - Clasificacion de genero y rango de edad
    people_counter     - Conteo unico de personas
    attendance_tracker - Deteccion de atencion vendedor-cliente
    seller_efficiency  - Metricas de eficiencia y premio horario
    stock_monitor      - Monitoreo de productos por ROI
    config             - Parametros configurables

Analitica de supermercado (planograma + comportamiento de compra):
    store_layout       - Pasillos, anaqueles y mobiliario por camara
    aisle_traffic      - Afluencia y concentracion por pasillo
    shelf_interaction  - Nivel de anaqueles + toma/devolucion de producto
    cart_tracker       - Carritos/cestas y depositos de producto
    shopper_journey    - Decision de compra, duelos y segmentos demograficos
    retail_analytics   - Orquestador y reporte de marketing
"""

from .config import AnalyticsConfig
from .demographics import DemographicsClassifier
from .people_counter import PeopleCounter
from .attendance_tracker import AttendanceTracker
from .seller_efficiency import SellerEfficiency
from .stock_monitor import StockMonitor
from .store_layout import StoreLayout, Aisle, Shelf, Fixture
from .aisle_traffic import AisleTraffic
from .shelf_interaction import ShelfInteractionDetector, ShelfStockTracker
from .cart_tracker import CartTracker
from .shopper_journey import ShopperJourney, segmento_demografico
from .box_monitor import AisleBoxMonitor
from .restock_detector import RestockDetector
from .staff_gallery import StaffGallery
from .retail_analytics import RetailAnalytics
