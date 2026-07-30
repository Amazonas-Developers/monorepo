from roboflow import Roboflow
from ultralytics import YOLO
import os


class Train:
    """
    Clase optimizada para entrenar yolo26m con Roboflow.
    """

    def __init__(
        self,
        api_key: str = None,
        workspace_name_roboflow: str = None,
        project_name_roboflow: str = None,
        version: int = None,
        device_train: str = '0',
        model_path: str = 'models/base/yolo26m.pt'   # 🔥 Ruta del modelo pre-entrenado (default: yolo26m)
    ):
        self.api_key = api_key
        self.workspace_name_roboflow = workspace_name_roboflow
        self.project_name_roboflow = project_name_roboflow
        self.version = version

        self.path_dataset = f'train/datasets/{self.project_name_roboflow}'
        self.path_yamal = f'{self.path_dataset}/data.yaml'
        self.path_result = 'models'

        self.device = device_train
        self.model = YOLO(model_path)        # 🔥 AHORA instancia el modelo con pesos

        self._download_dataset()

    def _download_dataset(self):
        """Descarga el dataset desde Roboflow en formato YOLOv12"""
        if not os.path.exists(self.path_dataset):
            rf = Roboflow(api_key=self.api_key)
            project = rf.workspace(self.workspace_name_roboflow).project(self.project_name_roboflow)
            version = project.version(self.version)
            version.download('yolov12', location=self.path_dataset)
            print(f"✅ Dataset descargado en: {self.path_dataset}")
        else:
            print(f"📁 Dataset ya existe: {self.path_dataset}")

    def run_train(self, **kwargs):
        """Entrena el modelo (yolo26m) pasando TODOS los hiperparametros desde
        el caller (train_hummus.py -> TRAIN_CONFIG).

        La clase fija SOLO la infraestructura (dataset, dispositivo, carpeta de
        salida); cualquier hiperparametro va en **kwargs. Antes la firma era
        fija (epochs/patience/batch/imgsz) y NO aceptaba el TRAIN_CONFIG ->
        TypeError; ademas tenia hardcodeados params de segmentacion
        (overlap_mask/mask_ratio), dropout (clasificacion) y augmentaciones
        agresivas (degrees/shear/flipud/perspective) malas para deteccion de
        personas. Todo eso se elimino: una sola fuente de verdad, el caller.
        """
        self.model.train(
            data=self.path_yamal,
            device=self.device,
            project=self.path_result,
            name=self.project_name_roboflow,
            exist_ok=True,
            **kwargs,
        )
        print(f"✅ Entrenamiento completado. Mejor modelo: "
              f"{self.path_result}/{self.project_name_roboflow}/weights/best.pt")