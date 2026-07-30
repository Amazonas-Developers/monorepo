"""
analytics/config.py - Parametros configurables para analitica retail.
"""


class AnalyticsConfig:
    """Contenedor de parametros configurables para todos los modulos de analitica."""

    # ── Reanalisis de capturas pendientes ───────────────────────────────
    # El VLM SI se usa en el reanalisis a posteriori (a diferencia del
    # analisis en vivo, donde esta apagado). Aqui el coste es asumible:
    # son fotos ya guardadas, no hay FPS que sostener, y el modelo solo se
    # carga cuando se lanza un reanalisis. Medido con Qwen2.5-VL-3B en la
    # RTX 5060 Ti: ~1.7 s por foto y 7.3 GB de VRAM.
    # Solo se le consulta cuando MiVOLO no decide o duda (< 0.70), asi que
    # la mayoria de las fotos ni lo tocan.
    REANALISIS_USAR_VLM: bool = True

    # ── Verificador VLM condicional (Hito 6) ────────────────────────────
    # Segunda opinion con Qwen2.5-VL para los tracks que cierran dudosos.
    # DESACTIVADO por defecto: cuesta segundos por consulta y varios GB de
    # VRAM. Mientras este en False el modelo NI SE CARGA (el router es
    # perezoso), asi que el pipeline corre exactamente igual que sin el.
    VLM_VERIFICADOR_ENABLED: bool = False
    # Solo se consulta por debajo de esta confianza global del veredicto.
    VLM_VERIFICADOR_UMBRAL_CONF: float = 0.60
    # Cola ACOTADA: si se llena, las peticiones nuevas se descartan y se
    # cuentan. Nunca se acumula trabajo por detras del video.
    VLM_VERIFICADOR_TAM_COLA: int = 8
    # Respuesta corta: solo se pide un JSON de 3 campos.
    VLM_VERIFICADOR_MAX_TOKENS: int = 80

    # ── Agregacion demografica por track (Hito 5) ───────────────────────
    # Una lectura suelta no decide nada: se juntan las de todo el track y
    # se vota. Ver `agregador_demografico.py` para las reglas.
    # Muestras minimas para emitir un veredicto (por debajo -> sin publicar,
    # pero CON motivo explicito, nunca un null mudo).
    DEMO_AGG_MIN_MUESTRAS: int = 3
    # Ventana deslizante: solo cuentan las ultimas N lecturas del track.
    DEMO_AGG_MAX_MUESTRAS: int = 20
    # Confianza minima de una lectura para entrar en la votacion.
    DEMO_AGG_CONF_MIN_MUESTRA: float = 0.55
    # Confianza global minima para PUBLICAR el veredicto. Calibrado sobre
    # los 122 crops reales (6 muestras por track con jitter de encuadre):
    #     umbral 0.50 -> publica 77.0 %  (confianza media 0.647)
    #     umbral 0.40 -> publica 86.1 %  (confianza media 0.628)
    #     umbral 0.30 -> publica 89.3 %  (confianza media 0.622)
    # Se elige 0.50: 77 % ya es un salto enorme frente al 2 % de partida, y
    # los puntos que se ganarian bajando el umbral corresponden justo a
    # votaciones repartidas, que son las de mayor riesgo de error. Subir la
    # cobertura por aqui es facil, pero se paga en aciertos.
    DEMO_AGG_CONF_MIN_VEREDICTO: float = 0.50

    # ── Telemetria del modulo demografico (Hito 2) ──────────────────────
    # Registra un veredicto final por track (motivo excluyente) para poder
    # explicar con numeros por que una captura sale sin genero. Solo
    # observa: no altera el comportamiento del pipeline.
    TELEMETRIA_DEMO_ENABLED: bool = True
    TELEMETRIA_DEMO_JSONL: str = "output/telemetria_demografica.jsonl"

    # ── Rangos de edad (min, max) ──
    AGE_RANGES = [
        (0, 12), (13, 17), (18, 25), (26, 35),
        (36, 50), (51, 65), (65, 100),
    ]

    # ── Mapeo de indices del modelo Caffe a rangos propios ──
    # Indices Caffe: 0=(0-2), 1=(4-6), 2=(8-12), 3=(15-20), 4=(25-32), 5=(38-43), 6=(48-53), 7=(60+)
    CAFFE_AGE_TO_RANGE = {
        0: "0-12",   # (0-2)
        1: "0-12",   # (4-6)
        2: "0-12",   # (8-12)
        3: "13-17",  # (15-20) -> aprox
        4: "26-35",  # (25-32)
        5: "36-50",  # (38-43)
        6: "51-65",  # (48-53)
        7: "65+",    # (60+)
    }

    # ── Parametros de precision MAXIMA del clasificador demografico ──
    # Filosofia: PRECISION SOBRE COBERTURA. Es preferible devolver
    # "Desconocido" a equivocarse en genero/edad. Los thresholds siguientes
    # estan calibrados para ~100% de precision sacrificando recall en
    # personas lejanas/de espaldas/borrosas. Si necesitas mas cobertura
    # (a costa de error), relaja a los valores comentados [PERMISIVO].

    # Voting temporal: acumular N samples de CALIDAD antes de comprometer.
    # Cada sample debe pasar todos los filtros de calidad y pose.
    # 3 es balance entre velocidad (~150ms con cara grande) y robustez.
    # Combinado con fast-commit, una cara clara puede commitar en 1 frame.
    # Subido 3 -> 5: con 3 muestras un par de frames malos fijaba genero
    # erroneo (hombres detectados como mujeres). 5 muestras + agree 0.90
    # exige consistencia real antes del lock.
    DEMO_MIN_SAMPLES: int = 5                  # [ANTES: 3]
    # Confianza minima del top1 tras voting robusto. Es el threshold
    # BASE para caras grandes (cara real >=120px). Para caras mas chicas
    # se baja proporcionalmente (ver DEMO_ADAPTIVE_THRESHOLD_*).
    DEMO_MIN_CONFIDENCE: float = 0.85          # [PERMISIVO: 0.55]
    # Margen minimo top1 - top2 para comprometer genero.
    DEMO_MIN_MARGIN: float = 0.35              # [PERMISIVO: 0.12]

    # ── Threshold adaptativo por tamano de cara ──
    # Una cara de 30px aporta menos informacion que una de 150px. Exigir
    # 0.85 a una cara chica resulta en NUNCA clasificar personas lejanas
    # aunque el modelo prediga correctamente. Bajamos el threshold para
    # caras chicas manteniendo la garantia de precision para caras grandes.
    DEMO_ADAPTIVE_THRESHOLD: bool = True
    # Tamanos de cara (en px del crop alineado tras upscale a YuNet) y
    # confianza minima asociada. El upscale tipico es ~2x para personas
    # lejanas en CCTV, asi que estos valores son ~2x los px reales.
    # Forma: [(face_w_min, min_conf, min_margin)] ordenado de mayor a
    # menor. Se aplica el primer bracket cuyo umbral cara cumpla.
    # Tradeoff entre cobertura y precision teorica esperada:
    #   0.85 -> precision ~100% (cara grande, modelo SEGURO)
    #   0.72 -> precision ~95%  (cara mediana, modelo moderado)
    #   0.60 -> precision ~85%  (cara chica, mejor que Desconocido)
    # ── Fast-commit: clasificacion INMEDIATA en 1 sample ──
    # Si la primera muestra cumple los siguientes criterios, se compromete
    # el resultado en ese mismo frame (no espera mas samples). Para
    # caras nitidas y frontales esto da resultado en ~150ms.
    # ENDURECIDO: el fast-commit de 1 muestra era la principal fuente de
    # generos mal fijados (un solo frame con sombra/angulo fijaba 'Mujer'
    # en hombres y el lock + Re-ID perpetuaban el error). Ahora solo
    # commitea instantaneo con evidencia abrumadora y cara grande.
    DEMO_FAST_COMMIT_ENABLED: bool = True
    DEMO_FAST_COMMIT_MIN_CONFIDENCE: float = 0.97  # [ANTES: 0.92]
    DEMO_FAST_COMMIT_MIN_MARGIN: float = 0.80      # [ANTES: 0.50]
    DEMO_FAST_COMMIT_MIN_FACE_W: float = 80.0      # [ANTES: 50.0]

    DEMO_ADAPTIVE_BRACKETS = [
        # (face_w_min_NATIVA, min_conf, min_margin)
        # face_w_min es el ancho REAL de la cara en el frame (sin upscale)
        # Con alineamiento ArcFace canonico, la confianza del modelo
        # mejora significativamente, asi que estos thresholds son
        # alcanzables sin sacrificar precision.
        # ENDURECIDOS: las caras chicas (18-60px) eran la zona donde mas
        # hombres salian clasificados como mujeres. Se exige mas confianza
        # cuanto menos informacion real hay, y <25px se rechaza directo
        # (mejor 'Desconocido' que un genero erroneo fijado).
        (100, 0.80, 0.35),   # cara grande   (>= 100px nativos)
        ( 60, 0.85, 0.45),   # cara mediana  (60-100px)
        ( 40, 0.90, 0.55),   # cara chica    (40-60px)
        ( 25, 0.94, 0.70),   # cara muy chica (25-40px) -> certeza casi total
        # <25px: rechazado por face-too-small (sin suficiente info real)
    ]
    # Tamano minimo del rostro DETECTADO en pixeles.
    # InsightFace fue entrenado con caras de ~112 px. <40 px = ruido.
    # Bajamos a 40 para CCTV cenital donde caras quedan ~50-90px.
    DEMO_MIN_FACE_SIZE: int = 40               # [PERMISIVO: 14]
    # Varianza minima del Laplaciano (rechaza caras borrosas).
    # 25.0 = exige nitidez razonable, descarta motion blur tipico CCTV.
    DEMO_MIN_BLUR_VAR: float = 25.0            # [PERMISIVO: 3.0]
    # Relacion alto/ancho valida para un rostro frontal.
    DEMO_FACE_ASPECT_MIN: float = 0.85         # [PERMISIVO: 0.45]
    DEMO_FACE_ASPECT_MAX: float = 1.45         # [PERMISIVO: 2.20]
    # Peso de decay por frame viejo (1.0 = sin decay; <1.0 olvida historia)
    DEMO_DECAY: float = 0.985
    # Tras cuantos samples VALIDAS sin converger, marcar como Desconocido.
    # Subimos a 200 porque ahora con el threshold adaptativo las personas
    # lejanas no acumulan samples (la cara no pasa filtros) y solo
    # empezamos a contar cuando se acercan. Damos margen para que el
    # voting converja una vez que entra en rango aceptable.
    DEMO_MAX_SAMPLES_BEFORE_GIVEUP: int = 200  # [PERMISIVO: 300]
    # BACKOFF anti-derroche: si un track NUNCA logra una cara detectable
    # (camara lejana/cenital, sin modelo corporal) tras este numero de
    # INTENTOS sin cara, se rinde (Desconocido) y deja de gastar YuNet/CPU
    # en cada frame. Es lo que permite subir la resolucion (mas precision
    # facial) sin tumbar el FPS: las personas sin rostro dejan de costar.
    # A reclasificacion N=6, 30 intentos ~= 180 frames (~12s) antes de
    # rendirse. Subir si hay personas mucho rato de espaldas que luego
    # muestran cara; bajar para liberar CPU mas rapido.
    DEMO_MAX_NOFACE_BEFORE_GIVEUP: int = 30
    # Confianza minima del face detector DNN (Res10 SSD fallback).
    DEMO_FACE_DETECTOR_CONF: float = 0.70      # [PERMISIVO: 0.28]
    # Resultado PROVISIONAL: DESHABILITADO en modo precision maxima.
    # Solo se muestra etiqueta cuando hay commit final.
    DEMO_PROVISIONAL_MIN_MARGIN: float = 0.30
    DEMO_PROVISIONAL_MIN_CONFIDENCE: float = 0.80
    # Contraste minimo (std del gray) para aceptar el rostro.
    DEMO_MIN_CONTRAST: float = 18.0            # [PERMISIVO: 5.0]
    # Asimetria maxima izquierda/derecha del rostro.
    # En CCTV la iluminacion lateral puede producir asym=80-130 NATURAL.
    # Con alineamiento ArcFace canonico, la cara queda centrada pero
    # las sombras de luz no se eliminan. 135 es permisivo pero el
    # voting+ensemble filtra los falsos positivos.
    DEMO_MAX_ASYMMETRY: float = 135.0          # [PERMISIVO: 50.0]
    # Acuerdo minimo entre samples individuales para commit.
    # Subido 0.85 -> 0.90: el 90% de las muestras deben coincidir en el
    # genero antes del lock (anti hombres-como-mujeres).
    DEMO_COMMIT_AGREE_RATIO: float = 0.90      # [ANTES: 0.85]
    # Acuerdo para resultado provisional (irrelevante si SHOW_PROVISIONAL=False)
    DEMO_PROVISIONAL_AGREE_RATIO: float = 0.85
    # Mostrar resultado provisional. False = solo committed (recomendado).
    DEMO_SHOW_PROVISIONAL: bool = False
    # ── YuNet face detector ──
    # Score minimo de YuNet para aceptar cara.
    # 0.70 = caras razonablemente nitidas. CCTV bueno -> score >= 0.85.
    DEMO_YUNET_MIN_SCORE: float = 0.70         # [PERMISIVO: 0.25]
    # Distancia inter-ocular minima como fraccion del ancho del bbox.
    # 0.22 fuerza cara semi-frontal (no perfil). En CCTV cenital el
    # eye_ratio tipico de una cara valida cae a 0.25-0.40.
    DEMO_MIN_EYE_FACE_RATIO: float = 0.22      # [PERMISIVO: 0.10]
    # Offset horizontal maximo de la nariz vs centro de ojos (fraccion
    # de la distancia inter-ocular). <0.65 = cabeza ligeramente girada
    # pero clasificable. Caras validas en CCTV cenital dan 0.40-0.60.
    DEMO_MAX_NOSE_OFFSET: float = 0.65         # [PERMISIVO: 1.10]
    # Verbose: imprime motivos de rechazo de cara.
    DEMO_DEBUG_REJECTIONS: bool = True
    # Tamano minimo del bbox de la PERSONA. Personas mas lejanas que esto
    # son rechazadas. En CCTV 1080p personas validas miden 250-600px alto.
    # En frames comprimidos 640x400 quedan en 130-280px.
    DEMO_MIN_PERSON_BBOX_W: int = 60           # [PERMISIVO: 30]
    DEMO_MIN_PERSON_BBOX_H: int = 140          # [PERMISIVO: 80]
    # ── ZOOM AL ROSTRO (primer plano de la cara) ───────────────────────
    # Recorte ampliado del rostro que se guarda con cada captura y se
    # compone junto al cuerpo en la foto de la galeria: permite VER en que
    # cara se baso el genero/edad y verificarlo a ojo.
    # Score minimo de YuNet para aceptar el rostro del primer plano. Mas
    # bajo que el de clasificacion: aqui solo se trata de encuadrar una
    # cara para mirarla, no de decidir genero/edad con ella.
    DEMO_FACE_ZOOM_MIN_SCORE: float = 0.50
    # Ancho (px) al que se amplia el rostro guardado en output/captures/faces.
    DEMO_FACE_ZOOM_OUT_W: int = 220
    #
    # NOTA (medido el 27-jul-2026, NO reintentar sin datos nuevos):
    # se probo un "zoom de 2 pasadas" para CLASIFICAR mejor (ampliar la
    # cara y re-detectar keypoints sobre la ampliacion). EMPEORO genero y
    # edad en los 3 tamanos probados (45/60/80 px) y perdia detecciones.
    # Razon: YuNet ya hace letterbox interno a 640x640, o sea que YA ve la
    # cara ampliada; un zoom previo solo suma interpolacion sobre pixeles
    # inventados. Se elimino. Lo que SI midio mejora fue la interpolacion
    # CUBIC en el warp de alineacion (ver _align_face_arcface): error de
    # edad -43% a 45 px, mejor en 3/3 tamanos.

    # Tamano objetivo del lado mas largo del crop al pasar a YuNet.
    # YuNet corre en CPU y es el cuello del FPS: su costo crece ~cuadratico con
    # este valor. 480 casi duplica el FPS vs 800 sin perder deteccion de caras
    # utiles (480px es de sobra para YuNet). Subir = detecta caras mas lejanas
    # pero baja el FPS; bajar a 320 = mas FPS con menos recall de caras lejanas.
    DEMO_YUNET_UPSCALE_TARGET: int = 480       # [PERMISIVO: 640]

    # Tamano minimo de la cara ORIGINAL (sin upscale) en pixeles del
    # frame original. Esto es lo que realmente determina si hay info
    # suficiente para clasificar genero/edad. Una cara upscaleada de
    # 30x30 a 90x90 sigue siendo 30x30 worth of info (interpolada).
    # InsightFace fue entrenado con caras 112x112, asi que <40px reales
    # da clasificaciones poco fiables (sobre todo en genero).
    DEMO_MIN_NATIVE_FACE_W: int = 40
    # Si la cara nativa es menor que esto, el commit se demora y exige
    # MUCHA mas confianza para evitar errores de overconfidence.
    DEMO_SMALL_FACE_CONF_BOOST: float = 0.10

    # ── Pose: solo caras semi-frontales se clasifican ──
    # CCTV cenital (camara montada arriba mirando abajo) produce pitch
    # alto SIEMPRE aunque la persona mire de frente. Pitch tipico medido
    # en pruebas con frames reales: +30 a +40 grados. Ajustamos arriba.
    # Si tu camara es a nivel ojos, baja pitch_max a 25.
    DEMO_POSE_MAX_YAW_DEG: float = 35.0
    DEMO_POSE_MAX_PITCH_DEG: float = 48.0
    DEMO_POSE_MAX_ROLL_DEG: float = 40.0

    # ── Ensemble multi-modelo ──
    # Si hay >=2 clasificadores disponibles, requerir acuerdo en GENERO
    # para comprometer. Si discrepan -> Desconocido. Cero falsos positivos.
    DEMO_REQUIRE_ENSEMBLE_AGREEMENT: bool = True
    # Diferencia maxima permitida entre predicciones de edad de los
    # modelos (en anos). Si superan esto -> Desconocido para edad.
    DEMO_MAX_AGE_DISAGREEMENT_YEARS: float = 12.0

    # ── Face Re-Identification (ArcFace embeddings) ──
    # Cuando esta activo, cuenta personas UNICAS por embedding biometrico
    # en lugar de por track_id. Previene contar dos veces a la misma
    # persona que entra/sale o cuya tracking se rompio.
    REID_ENABLED: bool = True
    # Filename del modelo de embeddings (ArcFace R50 de buffalo_l)
    REID_MODEL_FILENAME: str = "w600k_r50.onnx"
    # Cosine similarity minimo para considerar misma persona.
    # 0.35 con multi-embedding (guardamos varios embeddings por persona)
    # + pose-strict para registro nuevo = balance que captura mismos
    # individuos en distintos angulos sin generar falsos positivos
    # significativos. Bajar a 0.30 si todavia se duplican; subir a
    # 0.45 si se confunden personas distintas.
    REID_SIMILARITY_THRESHOLD: float = 0.35
    # Maximo de embeddings guardados por persona. Almacenar varios
    # capturando distintos angulos/expresiones permite matchear a la
    # misma persona aun cuando cambia de pose dramaticamente.
    REID_MAX_EMBEDDINGS_PER_PERSON: int = 5
    # Yaw maximo PARA REGISTRAR una persona NUEVA. Mas estricto que el
    # yaw general permitido para clasificacion (DEMO_POSE_MAX_YAW_DEG).
    # Las caras de perfil generan embeddings que NO matchean con
    # frontales aunque sean la misma persona, asi que NO debemos crear
    # nuevos registros desde poses laterales. Sin embargo, las caras
    # de perfil pueden seguir intentando MATCHEAR contra personas ya
    # conocidas (por si tuvieran tambien embeddings de perfil).
    REID_MAX_YAW_FOR_REGISTRATION: float = 20.0
    # Pitch maximo para registrar (mas estricto que el de clasificacion)
    REID_MAX_PITCH_FOR_REGISTRATION: float = 35.0
    # Politica de reset: "never", "daily", "weekly", "manual"
    # "never": la DB de personas NUNCA se borra automaticamente. Solo se
    # vacia si el usuario la borra manualmente (dashboard webapp, o
    # eliminando output/person_db/persons.pkl con el servidor apagado).
    REID_RESET_POLICY: str = "never"           # [ANTES: daily]
    # Path persistente de la base de datos
    REID_DB_PATH: str = "output/person_db/persons.pkl"
    # Threshold de confianza demografica minima para almacenar en la DB.
    # Solo guardamos genero/edad si llego con esta confianza.
    # Subido 0.60 -> 0.80: con 0.60, una clasificacion erronea de baja
    # confianza se guardaba en la DB y se HEREDABA en cada visita
    # (perpetuaba hombres mal etiquetados como mujeres). Con 0.80 solo
    # se hereda demografia confiable; lo dudoso se reclasifica.
    REID_MIN_DEMO_CONFIDENCE: float = 0.80

    # ── Re-ID CORPORAL / vestimenta (OSNet) — 2a senal, para espaldas ──
    # Cuando NO se ve la cara, se usa la apariencia del cuerpo para re-enlazar
    # el track a una identidad YA confirmada por rostro. NUNCA crea identidades
    # (no infla el conteo). EFIMERO con TTL (la ropa cambia), NO se persiste.
    BODY_REID_ENABLED: bool = True
    # Ruta del OSNet exportado a ONNX (onnxruntime-GPU).
    BODY_REID_MODEL: str = "models/osnet/osnet_x1_0_market1501.onnx"
    # Coseno minimo para considerar mismo cuerpo. ALTO: el cuerpo es menos
    # discriminativo que la cara (uniformes/ropa parecida dan ~0.8). Calibrar.
    BODY_REID_SIMILARITY_THRESHOLD: float = 0.90
    # Vida de un embedding corporal (seg). Default 4 h (sesion/dia).
    BODY_REID_TTL_S: float = 14400.0
    # Plantillas corporales por persona (multi-vista).
    BODY_REID_MAX_EMB_PER_PERSON: int = 5
    # Throttle: cada cuantos frames por track se calcula el embedding corporal
    # (OSNet ~5-10ms; no en cada frame por cada persona).
    BODY_REID_EVERY_N: int = 12
    # Altura minima (px) del recorte de persona para usarlo (evita cuerpos
    # diminutos/lejanos que dan embeddings basura).
    BODY_REID_MIN_CROP_H: int = 96
    # Frames minimos que un track debe llevar visible antes de enrolar/matchear
    # su cuerpo (evita ruido de tracks efimeros).
    BODY_REID_MIN_SEEN_FRAMES: int = 4

    # ── Suavizado demografico POR PERSONA (persistent_id) — Hito 6 ──
    # Cada lectura demografica comiteada (ya suavizada por track) se acumula
    # como VOTO en el registro de la persona: genero por voto ponderado por
    # confianza, edad como lista de estimaciones -> genero mayoritario +
    # edad mediana. Asi la demografia mostrada por persona es estable y
    # robusta a lo largo de sus visitas (no depende de una sola lectura).
    # Confianza minima de una lectura para contar como voto (filtra ruido).
    DEMO_VOTE_MIN_CONFIDENCE: float = 0.50
    # Tope de estimaciones de edad guardadas por persona (mediana movil).
    DEMO_AGE_MAX_ESTIMATES: int = 50

    # ── MiVOLO (modelo SOTA 2024 opcional) ──
    # Path al modelo MiVOLO ONNX. Si no existe, se ignora silenciosamente.
    # Para activarlo: descargar de https://github.com/WildChlamydia/MiVOLO
    # y convertir a ONNX (model_imdb_cross_person_4.22_99.46.pth.onnx) y
    # colocar en models/classifiers/mivolo.onnx
    MIVOLO_MODEL_FILENAME: str = "mivolo.onnx"
    # Input size esperado por MiVOLO (224x224 con cara+cuerpo concatenados).
    MIVOLO_INPUT_SIZE: int = 224
    # Cuanto pesa MiVOLO vs InsightFace en el promedio (0..1).
    # 0.6 = MiVOLO 60%, InsightFace 40% (MiVOLO es mas preciso).
    MIVOLO_WEIGHT_IN_ENSEMBLE: float = 0.60

    # ── Heavy-TTA ──
    # Numero de variantes TTA por inferencia. Cada variante es ~30ms en
    # CPU. 3 = original + flip + zoom_in -> ~90ms por sample. 6 era
    # demasiado lento para ser inmediato (~180ms por sample).
    DEMO_TTA_VARIANTS: int = 3                 # [ANTES: 6]
    # Si las variantes TTA deben coincidir en argmax(genero) para contar.
    # False = promedia todas, mas permisivo (deja que el voting temporal
    # del accumulator filtre el ruido). True = exige >=80% agreement,
    # mas precision por sample pero rechaza muchas con caras chicas.
    # Activado: si las variantes TTA de una muestra no coinciden >=80% en
    # el genero, la muestra se descarta. Filtra exactamente las caras
    # ambiguas que producian hombres clasificados como mujeres.
    DEMO_TTA_REQUIRE_AGREEMENT: bool = True    # [ANTES: False]

    # ── Mapa de calor de ocupacion/transito ──
    # Acumula la posicion de los pies de cada persona en una grilla y
    # produce overlay en vivo (cliente) + snapshots PNG/JSON (dashboard).
    HEATMAP_ENABLED: bool = True
    HEATMAP_GRID_W: int = 96
    HEATMAP_GRID_H: int = 54
    # Half-life del acumulador "reciente" (s). 300 = la actividad de hace
    # 5 min pesa la mitad. El acumulado total de sesion no decae.
    HEATMAP_DECAY_HALF_LIFE_S: float = 300.0
    # Transparencia del overlay sobre el video (0..1).
    HEATMAP_OVERLAY_ALPHA: float = 0.45
    # RADIO de la huella de cada persona en CELDAS de la grilla (kernel
    # gaussiano de lado 2*r+1). Mas grande = manchas mas amplias/suaves.
    HEATMAP_STAMP_RADIUS: int = 2
    # Dispersion (sigma) del gaussiano de la huella. Mas alto = mas difuso.
    HEATMAP_STAMP_SIGMA: float = 1.2
    # Cada cuantos segundos se guarda el snapshot PNG/JSON para el dashboard.
    HEATMAP_SNAPSHOT_EVERY_S: float = 5.0
    # Carpeta de snapshots (la lee el webapp).
    HEATMAP_DIR: str = "output/heatmap"
    # Cuantas zonas calientes reportar en metadata/dashboard (ranking
    # de mayor a menor concentracion).
    HEATMAP_TOP_ZONES: int = 8
    # Historico por horas: al cerrar cada hora se guarda el heatmap de ESA
    # hora en output/heatmap/history/<camara>/<YYYY-MM-DD_HH>.png|json|npz.
    HEATMAP_HISTORY_ENABLED: bool = True
    # Cuantas IMAGENES PNG por hora conservar (mas pesadas). 168 = 7 dias.
    # Los agregados de consulta se re-renderizan desde los .npz, no dependen
    # de estos PNG.
    HEATMAP_HISTORY_KEEP: int = 168
    # DATA cruda por hora (.npz, ~5-10 KB): grilla completa para poder
    # re-agregar CUALQUIER rango de tiempo en el dashboard. Retencion en dias.
    HEATMAP_DATA_KEEP_DAYS: int = 90
    # Fondo limpio por camara (output/heatmap/bg/<cam>.jpg) para renderizar los
    # agregados de consulta sobre la vista de la camara. Cada cuantos segundos
    # se refresca.
    HEATMAP_BG_EVERY_S: float = 60.0

    # ── Deteccion de personas (YOLO) ──
    # Tamano de inferencia de YOLO. 640 pierde gente LEJANA/pequena (camaras
    # elevadas, plazas grandes). 1280 detecta mucho mejor a distancia a costa
    # de ~2-3x mas computo. Subir a 1536 si las camaras son muy abiertas;
    # bajar a 960/640 si se prioriza FPS y la gente esta cerca.
    PERSON_DETECT_IMGSZ: int = 1280
    # Piso de confianza para personas (gente lejana sale con baja confianza).
    PERSON_DETECT_CONF: float = 0.25
    # Maximo de detecciones por frame. YOLO26 es NMS-free (end-to-end), su
    # cabeza ya esta limitada a 300 boxes -> alto para no recortar gente en
    # escenas llenas. No requiere NMS extra.
    PERSON_DETECT_MAX_DET: int = 300
    # Preferir el engine TensorRT FP16 (`<modelo>_<imgsz>.engine`) si existe.
    # A imgsz=1280 el engine es solo ~14% mas rapido que el .pt (a 640 era
    # 2.7x) y YOLO no es el cuello del pipeline, asi que el .pt es una opcion
    # perfectamente valida: NO esta atado a esta GPU, no requiere rebuild al
    # cambiar imgsz/equipo, y su FP16 puede captar alguna persona-borde que el
    # engine recorta. False = usar siempre el .pt (mas portable/exacto).
    # Por decision del usuario: usar el .engine TensorRT FP16 para personas.
    PERSON_DETECT_PREFER_ENGINE: bool = True

    # ── Tracking de corto plazo: sv.ByteTrack (sustituye al BoT-SORT interno
    # de YOLO). Es la capa 1 (IDs efimeros por movimiento/Kalman); la capa 2
    # (identidad persistente por rostro) la pone FaceReidentifier. ByteTrack es
    # POR-CAMARA (stateful) y se aisla en el estado de cada camara.
    # Umbral de confianza para ACTIVAR un track (det_thresh = +0.1 interno).
    BYTETRACK_TRACK_ACTIVATION_THRESH: float = 0.25
    # Frames que un track sobrevive "perdido" (oclusiones). Mas alto = aguanta
    # mas tiempo sin verse antes de morir (a costo de arrastrar IDs fantasma).
    # 90: a ~10 FPS reales de IA son ~9s de memoria — alguien tapado por otra
    # persona o un estante conserva su ID (el Kalman de ByteTrack sigue
    # prediciendo su posicion). Con 30 (~3s) cada oclusion normal de pasillo
    # rompia el track -> ID nuevo -> riesgo de recontar a la misma persona.
    BYTETRACK_LOST_BUFFER: int = 90
    # Umbral de matching IoU track<->deteccion. Bajar mejora precision pero
    # fragmenta mas; subir une mas pero arriesga cruces de identidad.
    BYTETRACK_MATCH_THRESH: float = 0.8
    # FPS de referencia para escalar el buffer de perdida (max_time_lost =
    # frame_rate/30 * lost_buffer). 30 da ~lost_buffer frames de memoria.
    BYTETRACK_FRAME_RATE: float = 30.0
    # CONFIRMACION de track: frames consecutivos que un objeto debe verse antes
    # de emitir su ID. >1 evita falsos positivos efimeros (el track no cuenta
    # hasta confirmarse). 1 = sin confirmacion. 3 = robusto sin tardar mucho.
    BYTETRACK_MIN_CONSECUTIVE_FRAMES: int = 3

    # ── Zona de conteo (sv.PolygonZone) — Hito 2 ──
    # El "area" es el roi_polygon (mismo poligono configurable que ya existe).
    # ANCLA para decidir si una persona esta DENTRO: "BOTTOM_CENTER" = el pie
    # (estandar de presencia en piso, mas correcto que el centro para "parado
    # dentro"); "CENTER" = centro de la caja (paridad con el is_inside legacy).
    # Debe ser un nombre valido de sv.Position.
    ZONE_TRIGGER_ANCHOR: str = "BOTTOM_CENTER"
    # Estilo de dibujo del poligono de zona (modo clasico / overlay servidor).
    ZONE_DRAW_COLOR_BGR: tuple = (0, 255, 255)   # amarillo (BGR)
    ZONE_DRAW_THICKNESS: int = 3
    ZONE_FILL_OPACITY: float = 0.30
    # Mostrar el numero "personas dentro" dibujado sobre la zona (modo clasico).
    ZONE_SHOW_COUNT: bool = True

    # ── Entradas/salidas por persona (maquina de estados FUERA/DENTRO) — Hito 5 ──
    # Histeresis / anti-rebote: frames CONSECUTIVOS en el nuevo estado para
    # confirmar una transicion. Evita que alguien parado en el BORDE del
    # poligono dispare entradas/salidas fantasma. 1 = sin histeresis. 3-5 es
    # robusto. (A ~10 fps, 3 = ~0.3 s de estabilidad antes de contar.)
    ZONE_HYSTERESIS_FRAMES: int = 3
    # Solo cuentan entrada/salida las personas con identidad CONFIRMADA por
    # rostro (persistent_id). Las provisionales (sin rostro) NO disparan
    # transiciones hasta confirmarse -> conteo de visitantes unicos exacto.
    ZONE_ENTRY_REQUIRES_CONFIRMED: bool = True

    # ── Reporte consolidado de analitica (Hito 8) ──
    # Cada cuantos segundos se recomputa el reporte consolidado (distribucion
    # genero/edad recorre la galeria -> no conviene cada frame) y se persiste
    # a output/analytics_report_<id>.json. El reporte tambien va en metadata.
    REPORT_EVERY_S: float = 10.0

    # ── Captura de fotos por deteccion ──
    # Guarda UNA foto del recorte de cada persona detectada (track nuevo) y, si
    # se detecta su cara, tambien el recorte del rostro. En output/captures/.
    CAPTURE_DETECTIONS: bool = True
    CAPTURE_DIR: str = "output/captures"
    # Tamano minimo (px) del recorte de persona para guardarlo (evita basura).
    CAPTURE_MIN_PERSON_H: int = 24
    # Frames que debe persistir un track antes de capturarlo (evita fotos de
    # fragmentos de track efimeros). Mas alto = menos fotos pero captura mas
    # tarde. La misma persona en varios tracks se agrupa via Re-ID (dedup).
    CAPTURE_MIN_SEEN_FRAMES: int = 5
    # DEDUP ESPACIAL: no volver a capturar si ya se capturo a alguien en ~la
    # misma zona (celda de grilla) en los ultimos N segundos. Atrapa a la misma
    # persona quieta a la que el tracker le cambia el id (churn de BoT-SORT).
    # Funciona aunque no haya rostro. Subir los segundos = menos fotos repetidas.
    CAPTURE_DEDUP_SECONDS: float = 60.0
    # Grilla para el dedup espacial: la imagen se divide en COLSxROWS celdas y se
    # captura a lo sumo una persona por celda por ventana. Mas celdas = mas
    # granular (mas fotos); menos celdas = mas agrupacion (menos fotos).
    CAPTURE_GRID_COLS: int = 12
    CAPTURE_GRID_ROWS: int = 7
    # ── Carpeta de capturas del CLIENTE (Amazonas View) ──
    # Ademas de output/captures (dashboard), cada captura se escribe ANOTADA
    # (banner con genero/edad/camara/fecha sobre la imagen) en esta carpeta,
    # que el cliente muestra como galeria clickeable (misma maquina; el click
    # abre el archivo). La foto se RE-anota cuando la demografia converge y
    # se borra si el Re-ID la marca como duplicado. Vacio = desactivado.
    CAPTURE_CLIENT_DIR: str = r"C:\Users\Sistema-1\Desktop\ELDE\Amazonas View\capture"

    # ── Deteccion CONTINUA de eventos (VLM Qwen2.5-VL) ──
    # YOLO dispara: cuando hay al menos una persona, cada EVENT_CHECK_EVERY_S
    # segundos se consulta al VLM si esta ocurriendo una COMPRA o una ENTREGA DE
    # BANDEJA. Corre en hilo de fondo (no bloquea el tiempo real) y un solo VLM a
    # la vez en todo el servidor. El evento positivo se envia como alerta al
    # cliente. Subir los segundos = menos consultas al VLM (mas ligero).
    EVENT_DETECTION_ENABLED: bool = True
    EVENT_CHECK_EVERY_S: float = 12.0

    # Contexto GLOBAL que se inyecta en TODAS las consultas al VLM (VQA + eventos
    # + deteccion continua). Describe tu negocio/escena para mejorar la precision.
    # Ej: "Local de comida rapida. La caja esta a la derecha del mostrador. Una
    # COMPRA es cuando el cliente entrega dinero/tarjeta al cajero. Las bandejas
    # de comida son de plastico rojo." (Tambien se puede pasar contexto POR
    # consulta desde el cliente/endpoints, que se suma a este.)
    VLM_CONTEXT: str = ""

    # ── Archivo de log general (JSONL) ──
    ANALYTICS_LOG_JSONL: str = "output/analytics_log.jsonl"

    # ── Rama CORPORAL (E3B): clasificacion de genero/edad SIN cara ──
    # Permite clasificar de espaldas / cenital / perfil extremo, donde la
    # rama facial no ve cara. Genero principalmente; la edad sin cara es
    # inherentemente mas gruesa (taxonomia Nino/Joven/Adulto/Mayor).
    DEMO_BODY_BRANCH_ENABLED: bool = True
    # Modelo primario: MiVOLO en modo body-only (cara ausente). ONNX en
    # models/classifiers/mivolo.onnx (el mismo que el ensemble facial).
    # Fallback: modelo PAR (PA-100K/PETA) SOLO genero, en
    # models/classifiers/par_gender.onnx.
    DEMO_BODY_PAR_MODEL_FILENAME: str = "par_gender.onnx"
    DEMO_BODY_INPUT_SIZE: int = 224
    # Gates de calidad del crop corporal.
    DEMO_BODY_MIN_PERSON_H: int = 120     # alto minimo del bbox de persona (px)
    DEMO_BODY_MIN_PERSON_W: int = 45      # ancho minimo (px)
    DEMO_BODY_MIN_BLUR_VAR: float = 12.0  # descarte por blur del crop corporal
    # Confianza minima de genero para aceptar una muestra corporal.
    DEMO_BODY_MIN_GENDER_CONF: float = 0.70
    # Peso de una muestra corporal en el voting (facial=1.0).
    DEMO_BODY_SAMPLE_WEIGHT: float = 0.5
    # Multiplicador base del peso de una muestra FACIAL (se aplica sobre el
    # peso de calidad 0.25..1.0). 1.0 = sin cambio. Hace explicito el ratio
    # facial(1.0)/corporal(0.5) y lo deja configurable.
    DEMO_FACE_SAMPLE_WEIGHT: float = 1.0

    # ── Lock + histeresis (Hito 5) ──
    # Tras el commit el track queda BLOQUEADO: no se reclasifica (ahorra GPU
    # y evita parpadeo de etiquetas). Si HYSTERESIS_ENABLED, se permite
    # CORREGIR una etiqueta fijada SOLO con evidencia facial fuerte y
    # SOSTENIDA: se exigen N muestras faciales consecutivas, de alta
    # confianza, que contradigan el genero fijado (NUNCA una sola muestra).
    # Default OFF: el lock duro garantiza cero reclasificaciones visibles.
    # ACTIVADA: corrige un genero mal fijado con 5 muestras faciales
    # consecutivas de alta confianza (>=0.90) que lo contradigan. Es la
    # red de seguridad para los locks erroneos que ya ocurrieron.
    DEMO_HYSTERESIS_ENABLED: bool = True       # [ANTES: False]
    DEMO_HYSTERESIS_MIN_SAMPLES: int = 5       # faciales consecutivas
    DEMO_HYSTERESIS_MIN_CONFIDENCE: float = 0.90
    # Cadencia de re-muestreo post-commit. A 10 FPS, 30 = re-chequea la
    # cara cada ~3s por track fijado -> correccion de un error en ~15s
    # (5 muestras sostenidas) sin cargar la GPU con muchos tracks.
    DEMO_HYSTERESIS_RESAMPLE_EVERY: int = 30   # [ANTES: 15]

    # Cadencia de RE-CLASIFICACION de un track NO convergido (sin genero/edad
    # firme aun). Antes era 1 = se corria el pipeline facial completo (YuNet +
    # align + ArcFace + ensemble) en CADA frame para CADA persona -> con 6-8
    # personas sin cara visible (camara lejana, sin modelo corporal) el
    # servidor caia a ~2 FPS. Ahora cada track se reintenta cada N frames y,
    # ademas, ESCALONADO por track_id (se reparte la carga entre frames, sin
    # picos). Sube el numero para mas FPS (menos intentos/seg); bajalo para
    # converger mas rapido la demografia. No afecta los umbrales de precision.
    DEMO_RECLASSIFY_EVERY: int = 10

    # Taxonomia GRUESA de edad para resultados solo-corporales (age_source=body)
    # Forma: (min, max, etiqueta). El maximo del ultimo bin cubre 60+.
    DEMO_BODY_AGE_COARSE_BINS = [
        (0, 12, "Nino"),
        (13, 29, "Joven"),
        (30, 59, "Adulto"),
        (60, 200, "Mayor"),
    ]

    @classmethod
    def coarse_age_from_value(cls, age_value: float) -> str:
        """Convierte un valor de edad (anos) a la taxonomia gruesa corporal."""
        try:
            v = int(round(float(age_value)))
        except (TypeError, ValueError):
            return "Desconocido"
        for lo, hi, label in cls.DEMO_BODY_AGE_COARSE_BINS:
            if lo <= v <= hi:
                return label
        return "Desconocido"

    @classmethod
    def age_range_label(cls, age_idx: int) -> str:
        """Convierte indice del modelo Caffe a etiqueta de rango de edad."""
        return cls.CAFFE_AGE_TO_RANGE.get(age_idx, "18-25")

    @classmethod
    def age_range_from_value(cls, age_value: int) -> str:
        """Convierte un valor numerico de edad a etiqueta de rango."""
        for lo, hi in cls.AGE_RANGES:
            if lo <= age_value <= hi:
                if hi == 100:
                    return "65+"
                return f"{lo}-{hi}"
        return "18-25"
