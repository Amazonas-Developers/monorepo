"""Cliente de la API de Jarvis365 (login, establecimientos, novedades).

RECONCILIACION del 31-jul-2026 (paso 8 del HITO 2, aplicado tarde): el HITO 4
extrajo al nucleo la version identica en tienda/managers/amazonas, pero la de
perimetrales habia DIVERGIDO por razones reales y el alias la enterro. Se noto
en el primer arranque real tras los venv aislados:

  TypeError: Jarvis_api.__init__() got an unexpected keyword argument
  'establecimiento'

Esta es la version de perimetrales, que es superconjunto estricto (el analisis
de metodos no encontro NADA propio en la otra). Lo que aporta:

- `establecimiento=` en el constructor: nombre preferido (del .env) que se
  auto-selecciona por coincidencia al cargar la lista; si no, el primero.
- `establishments_loaded` (Signal): la UI puebla el selector del pie.
- `enviar_novedad_async` / `subir_imagen_async`: el envio asincrono que usa
  `jarvis_alert_forwarder` — con la version anterior del nucleo habria
  fallado con AttributeError en la PRIMERA alerta.
- El arreglo de la carrera del login: los establecimientos se piden DESPUES
  de que el login deje la cookie de sesion, no en el constructor ("Host
  requires authentication").
- Acceso defensivo (`.get()`) al armar la novedad.

Para tienda/managers/amazonas nada cambia: construyen `Jarvis_api` sin
`establecimiento` (opcional) y no llaman a ningun metodo de envio.
"""
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QSslConfiguration , QNetworkCookieJar, QNetworkReply, QSslSocket, QHttpMultiPart, QHttpPart
from PySide6.QtCore import QObject , QUrl, QByteArray,Signal, QEventLoop
import json
import base64, re



class Jarvis_api(QObject):
    
    
    error_request = Signal(str)
    selected_establishmentSignal = Signal(dict)
    # Se emite cuando la lista de establecimientos termina de cargar (async,
    # tras el login). La UI la usa para poblar el selector del pie.
    establishments_loaded = Signal(list)
    
    
    def __init__(self, emailuser=None, password=None, url_api=None, establecimiento=None):
        super().__init__()
        self.email_jarvis_request = emailuser
        self.password_jarvis_request = password
        self.url_api_base = url_api
        # Nombre del establecimiento preferido (del .env). Si está, se
        # auto-selecciona por coincidencia de nombre al cargar la lista; si no,
        # se toma el primero. La UI del footer puede sobrescribirlo.
        self._establecimiento_preferido = (establecimiento or '').strip()

        self.session_user = None
        self.error_User = None
        self.list_of_establishments = []
        self.selected_establishment = None
        
        self.manager_request = QNetworkAccessManager()
        cookie_jar = QNetworkCookieJar()
        self.manager_request.setCookieJar(cookie_jar)
        
        self.ssl_config = QSslConfiguration.defaultConfiguration()  # OBTIENE LA CONFIGURACIÓN SSL
        self.ssl_config.setPeerVerifyMode(QSslSocket.VerifyNone) # DESACTIVA ERROR DE CERTIFICADOS SSL INVALIDO

        # OJO: la carga de establecimientos (localligth) requiere la cookie de
        # sesión que deja el login. Por eso NO se llama aquí (era una carrera:
        # se ejecutaba antes de que el login async terminara -> "Host requires
        # authentication"). Se encadena en _handler_response_session tras el OK.
        self.create_session()
        
        
    
    def selection_establishment(self, name):
        for iteration in self.list_of_establishments:
            if iteration['name'] == name:
                self.selected_establishment = iteration
                print(f'{name} seleted by client')
                break

    def buscar_establecimiento(self, nombre):
        """El dict del establecimiento por nombre, o None.

        Primero coincidencia exacta, luego parcial (ambas sin distinguir
        mayusculas). Existe para el envio POR CAMARA (1-ago-2026): cada
        recuadro puede apuntar sus alertas a un local distinto y aqui se
        resuelve el nombre guardado contra la lista cargada."""
        buscado = str(nombre or '').strip().lower()
        if not buscado:
            return None
        for est in self.list_of_establishments:
            if (isinstance(est, dict)
                    and str(est.get('name', '')).strip().lower() == buscado):
                return est
        for est in self.list_of_establishments:
            if (isinstance(est, dict)
                    and buscado in str(est.get('name', '')).lower()):
                return est
        return None

    def _resolver_destino(self, establecimiento):
        """dict | nombre | None -> el establecimiento al que enviar.

        Sin `establecimiento` (o si el nombre no aparece en la lista) se cae
        al seleccionado global del pie, que es el comportamiento historico."""
        if isinstance(establecimiento, dict):
            return establecimiento
        if establecimiento:
            encontrado = self.buscar_establecimiento(establecimiento)
            if encontrado is not None:
                return encontrado
            print(f"[jarvis] establecimiento '{establecimiento}' no esta en "
                  "la lista: se usa el seleccionado global")
        return self.selected_establishment
    
    
    
    
    def create_session(self):
        body_dic = { "email": self.email_jarvis_request, "password": self.password_jarvis_request } 
        response = self._request(resource='auth/login', body=body_dic, berb='post')
        response.finished.connect(lambda: self._handler_response_session(response))
        
        
        
    def fetching_establishment(self):
        response = self._request(resource='localligth')
        response.finished.connect(lambda: self._handler_response_establishment(response))
        
        
        
    def send_alert_to_api(self, url_image='https://amazona365.ddns.net/api_jarvis/v1/novelty/img=novelty_1769530415033.jpeg' , title='Alerta de perimetral', message='', params=None, establecimiento=None):
        # ENVÍO DE ALERTAS ACTIVO (perimetrales-view). Envía una "novedad" a
        # Jarvis365 (POST /novelties) de forma ASÍNCRONA (no bloquea la GUI).
        # `establecimiento` (dict o nombre) manda la novedad a ESE local en
        # vez del seleccionado global — es lo que usa el envio POR CAMARA.
        try:
            if self.session_user is None:
                self.error_request.emit('Error de sesión')
                print('[jarvis] no se envía alerta: sesión no autenticada')
                return None
            destino = self._resolver_destino(establecimiento)
            if destino is None:
                self.error_request.emit('Seleciones un establecimiento o lugar')
                print('[jarvis] no se envía alerta: sin establecimiento seleccionado')
                return None

            imageurl = url_image
            nombre = f"{self.session_user.get('name', '')} {self.session_user.get('surName', '')}".strip()
            data = {
                'title': f'{title} ("Modo IA")',
                'userName': nombre or 'Jarvis Visión',
                'userId': self.session_user.get('_id', ''),
                'localName': destino.get('name', ''),
                'localId': destino.get('_id', ''),
                'ruleBonus': {'worth': 0, 'acomulate': 0},
                'alertId': '6977974dfed1f0dcaffceefa',
                'description': 'Alerta generada con IA',
                'imageToShare': imageurl,
                'menu': f"*{destino.get('name', '')}*\n_{title}_\n{message}",
                'imageUrl': [
                    {'url': imageurl, 'caption': 'alert'}
                ]
            }

            response = self._request(resource='novelties', body=data, berb='post')
            if response is not None:
                response.finished.connect(lambda: self.__handler_response_alert(response))
            print(f"[jarvis] novedad enviada -> {title} "
                  f"[local: {destino.get('name', '?')}]")
        except Exception as e:
            print(f'[jarvis] error enviando alerta: {e}')
        
        
        
    def _request(self, resource='', body=None, berb='get'):
        try:
            request = QNetworkRequest(QUrl(f'{self.url_api_base}/{resource}'))
            request.setSslConfiguration(self.ssl_config)
            request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
            
            # Agregar cabeceras personalizadas
            request.setRawHeader(QByteArray(b'Source-Application'), QByteArray(b'Jarvis_Vision'))
            request.setRawHeader(QByteArray(b'Version-App'), QByteArray(b'1.0'))
            
            if berb == 'post':
                body_byte = QByteArray(json.dumps(body).encode("utf-8"))
                return self.manager_request.post(request, body_byte)
            else:
                return self.manager_request.get(request)
                
        except Exception  as e:
            print(e)
            
            
    
    
    def _handler_response_session(self, reply: QNetworkReply):
        if reply.error() == QNetworkReply.NetworkError.NoError: 
            data = reply.readAll().data().decode() 
            try:
                json_data = json.loads(data)
                print('User autenticated')
                self.session_user = json_data
                # Ya hay cookie de sesión: ahora sí pedir los establecimientos.
                self.fetching_establishment()

            except Exception:
                print("Respuesta texto:", data)

        else:
            print("Error en la respuesta:", reply.errorString())
            self.error_request.emit('Error en la autenticación en el servicio de Jarvis365')
            
            
            
            
    def _handler_response_establishment(self, reply: QNetworkReply):
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll().data().decode()
            try:
                json_data = json.loads(data)

                self.list_of_establishments = json_data
                # Auto-selección para que el envío quede operativo sin depender
                # de la UI: si el .env fija un establecimiento preferido se busca
                # por nombre (coincidencia parcial, sin distinguir mayúsculas);
                # si no se encuentra o no hay preferido, se toma el primero.
                # La UI (footer) puede sobrescribirlo con selection_establishment.
                if (self.selected_establishment is None
                        and isinstance(json_data, list) and json_data):
                    elegido = None
                    pref = self._establecimiento_preferido.lower()
                    if pref:
                        for est in json_data:
                            if isinstance(est, dict) and pref in str(est.get('name', '')).lower():
                                elegido = est
                                break
                    if elegido is None:
                        elegido = json_data[0]
                    self.selected_establishment = elegido
                    nombre = elegido.get('name', '?') if isinstance(elegido, dict) else '?'
                    print(f'[jarvis] establecimiento auto-seleccionado: {nombre}'
                          + (f" (preferido: '{self._establecimiento_preferido}')"
                             if self._establecimiento_preferido else ''))
                # Avisar a la UI (selector del pie) que ya hay lista.
                self.establishments_loaded.emit(
                    json_data if isinstance(json_data, list) else [])

            except Exception:
                print("Respuesta texto:", data)
        else:

            print("Error en la respuesta:", reply.errorString())
            self.error_request.emit('Error al obtener la lista de clientes')
            
            
    
    def __handler_response_alert(self, reply: QNetworkReply):
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll().data().decode()
            try:
                json_data = json.loads(data)
       
            except Exception:
                print("Respuesta texto:", data)
        else:
            # Leer cuerpo de la respuesta para obtener detalles del error
            data = reply.readAll().data().decode()
            try:
                json_data = json.loads(data)
                print("Error response JSON:", json_data)
                if isinstance(json_data, dict) and 'response' in json_data:
                    print("Propiedad 'response' del servidor:", json_data['response'])
            except Exception:
                print("Error response text:", data)
            print("Error en la respuesta:", reply.errorString())
            self.error_request.emit('Error al enviar la alerta')



    def send_base64_image(self, base64_image: str, field_name: str = 'img', filename: str = 'image.jpg', timeout: int = 15000):
        """Envía una imagen en base64 al endpoint /multimedia y devuelve la respuesta del servidor.

        Devuelve (success: bool, result: dict|str).
        Atención: este método espera de forma bloqueante hasta que la petición termine o se alcance `timeout`.
        """
        try:
            # Normalizar y decodificar base64 (soporta data URLs)
            b64 = re.sub(r'^data:image/\w+;base64,', '', base64_image)
            raw = base64.b64decode(b64)

            multipart = QHttpMultiPart(QHttpMultiPart.FormDataType)

            part = QHttpPart()
            part.setHeader(QNetworkRequest.ContentDispositionHeader, f'form-data; name="{field_name}"; filename="{filename}"')
            content_type = 'image/jpeg'
            if filename.lower().endswith('.png'):
                content_type = 'image/png'
            part.setHeader(QNetworkRequest.ContentTypeHeader, content_type)
            part.setBody(QByteArray(raw))
            multipart.append(part)

            request = QNetworkRequest(QUrl(f'{self.url_api_base}/multimedia'))
            request.setSslConfiguration(self.ssl_config)
            # agregar cabeceras personalizadas
            request.setRawHeader(QByteArray(b'Source-Application'), QByteArray(b'Jarvis_Vision'))
            request.setRawHeader(QByteArray(b'Version-App'), QByteArray(b'1.0'))

            reply = self.manager_request.post(request, multipart)
            multipart.setParent(reply)

            loop = QEventLoop()
            reply.finished.connect(loop.quit)
            loop.exec()

            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = reply.readAll().data().decode()
                try:
                    return True, json.loads(data)
                except Exception:
                    return True, data
            else:
                data = reply.readAll().data().decode()
                try:
                    json_data = json.loads(data)
                    if isinstance(json_data, dict) and 'response' in json_data:
                        return False, json_data['response']
                    return False, json_data
                except Exception:
                    return False, data or reply.errorString()

        except Exception as e:
            return False, str(e)

    # =====================================================================
    # ENVÍO ASÍNCRONO DE ALERTAS (no bloquea la GUI) — usado por el
    # reenviador de alertas de perimetrales-view (jarvis_alert_forwarder).
    # =====================================================================

    @staticmethod
    def _extraer_url_imagen(respuesta) -> str:
        """Extrae la URL de la imagen subida desde la respuesta de /multimedia.

        El esquema exacto de la respuesta puede variar; se prueban las claves
        más comunes de forma tolerante. Si no se encuentra, devuelve ''.
        """
        if isinstance(respuesta, str):
            return respuesta.strip()
        if not isinstance(respuesta, dict):
            return ''
        # Posibles anidamientos: {data: {...}} / {result: {...}}.
        for anidado in ('data', 'result', 'novelty', 'response'):
            if isinstance(respuesta.get(anidado), dict):
                url = Jarvis_api._extraer_url_imagen(respuesta[anidado])
                if url:
                    return url
        for clave in ('url', 'imageUrl', 'image_url', 'path', 'location',
                      'secure_url', 'src', 'filename', 'file', 'img', 'name'):
            valor = respuesta.get(clave)
            if isinstance(valor, str) and valor:
                return valor
            if isinstance(valor, list) and valor:
                primero = valor[0]
                if isinstance(primero, str):
                    return primero
                if isinstance(primero, dict) and isinstance(primero.get('url'), str):
                    return primero['url']
        return ''

    def subir_imagen_async(self, base64_image: str, callback):
        """Sube una imagen (base64) a /multimedia SIN bloquear y llama a
        `callback(url_str)` cuando termina (url='' si falló)."""
        try:
            b64 = re.sub(r'^data:image/\w+;base64,', '', base64_image or '')
            if not b64:
                callback('')
                return
            raw = base64.b64decode(b64)

            multipart = QHttpMultiPart(QHttpMultiPart.FormDataType)
            part = QHttpPart()
            part.setHeader(QNetworkRequest.ContentDispositionHeader,
                           'form-data; name="img"; filename="alerta.jpg"')
            part.setHeader(QNetworkRequest.ContentTypeHeader, 'image/jpeg')
            part.setBody(QByteArray(raw))
            multipart.append(part)

            request = QNetworkRequest(QUrl(f'{self.url_api_base}/multimedia'))
            request.setSslConfiguration(self.ssl_config)
            request.setRawHeader(QByteArray(b'Source-Application'), QByteArray(b'Jarvis_Vision'))
            request.setRawHeader(QByteArray(b'Version-App'), QByteArray(b'1.0'))

            reply = self.manager_request.post(request, multipart)
            multipart.setParent(reply)

            def _al_terminar():
                url = ''
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    data = reply.readAll().data().decode()
                    try:
                        url = self._extraer_url_imagen(json.loads(data))
                    except Exception:
                        url = data.strip()
                    if not url:
                        print(f'[jarvis] /multimedia respondió sin URL reconocible: {data[:200]}')
                else:
                    print(f'[jarvis] error subiendo imagen: {reply.errorString()}')
                reply.deleteLater()
                callback(url)

            reply.finished.connect(_al_terminar)
        except Exception as e:
            print(f'[jarvis] excepción subiendo imagen: {e}')
            callback('')

    def enviar_novedad_async(self, base64_image: str = '', title: str = 'Alerta de perimetral',
                             message: str = '', establecimiento=None):
        """Flujo completo NO bloqueante: sube la imagen (si hay) y luego envía
        la novedad con esa URL. Si no hay imagen, envía la novedad directa.

        `establecimiento` (nombre o dict) apunta la novedad a ESE local: es lo
        que permite que cada camara del cliente alerte a su establecimiento.
        Se resuelve AQUI (no en el callback) para que un cambio de seleccion
        global mientras sube la imagen no desvie la alerta."""
        if self.session_user is None:
            print('[jarvis] novedad omitida: sin sesión')
            return
        destino = self._resolver_destino(establecimiento)
        if destino is None:
            print('[jarvis] novedad omitida: sin establecimiento')
            return
        if base64_image:
            self.subir_imagen_async(
                base64_image,
                lambda url: self.send_alert_to_api(
                    url_image=url or '', title=title, message=message,
                    establecimiento=destino))
        else:
            self.send_alert_to_api(title=title, message=message,
                                   establecimiento=destino)
            
            
            
            