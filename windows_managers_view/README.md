window_manager/
├── main.py                     # Punto de entrada y inicialización de la app.
|
├── core/                       # Lógica de negocio (Controladores, lógica de captura).
│   ├── network/
│   │   
|   │_init__.py
│   │   │  
│   │   ├── socket_client.py    # Maneja la conexión continua (WebSockets/Socket.io)
│   │   └── api_client.py
│   │   
│   └── window_controller.py
|
├── native/                     # Funciones C++ y Pybind11 para tareas nativas.
│   ├── ...
│   └── bindings.cpp
|
├── gui/                        # Capa de presentación (Widgets, Ventana principal).
│   ├── main_window.py
│   ├── components/
│   │   ├── ...
│   │   └── window_preview.py   # Contiene la clase interactive_imageLabel
│   └── styles/
│       └── theme.py
|
├── models/                     # Modelos de datos y la CLASE que maneja la persistencia.
│   ├── __init__.py
│   ├── window_data.py
│   └── settings_model.py       # NUEVO: Clase central para cargar/guardar la configuración del usuario.
|
├── config/                     # Configuración estática y por defecto.
│   ├── __init__.py
│   └── default_settings.json   # NUEVO: Valores iniciales de los puntos, tema, etc.
|
└── data_user/                  # Ubicación LÓGICA donde se guardan los archivos de usuario.
    └── user_settings.json      # ARCHIVO REAL: Se guarda en una ruta específica del SO (ej. %APPDATA%).ssss