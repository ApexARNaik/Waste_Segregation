import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from PIL import Image
from ultralytics import YOLO

from app.config import settings

logger = logging.getLogger(__name__)


class VisionInferenceService:
    """Local Ultralytics YOLO inference service."""

    def __init__(self, model_path: Optional[Union[Path, str]] = None):
        self.model_path = Path(model_path) if model_path else settings.MODEL_PATH
        self.model: Optional[YOLO] = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None
        self._class_names: Dict[int, str] = {}
        self._task: str = "detect"
        self._device: str = "cpu"

    def load(self) -> bool:
        """Load the YOLO model weights into memory once."""
        with self._lock:
            if self.model is not None:
                return True

            try:
                if not self.model_path.exists():
                    self._load_error = f"Model file not found at {self.model_path}"
                    logger.error(self._load_error)
                    return False

                logger.info(f"Loading local YOLO model from {self.model_path}...")
                self.model = YOLO(str(self.model_path))
                self._class_names = self.model.names if hasattr(self.model, "names") else {}
                self._task = getattr(self.model, "task", "detect")
                self._load_error = None
                logger.info(f"YOLO model loaded successfully. Classes: {self._class_names}")
                return True
            except Exception as exc:
                self._load_error = f"Failed to initialize YOLO model: {exc}"
                logger.exception(self._load_error)
                self.model = None
                return False

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def get_status(self) -> Dict[str, Any]:
        """Return operational health of the vision inference engine."""
        return {
            "loaded": self.is_loaded,
            "model_path": str(self.model_path),
            "classes": self._class_names,
            "task": self._task,
            "confidence_threshold": settings.MODEL_CONFIDENCE,
            "error": self._load_error,
        }

    def predict(
        self,
        image_path: Union[Path, str],
        capture_id: str,
        confidence_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Execute local vision inference on a cropped raw image.
        Saves the annotated prediction image to data/images/predictions/{capture_id}.jpg.
        """
        conf = confidence_threshold if confidence_threshold is not None else settings.MODEL_CONFIDENCE
        target_image = Path(image_path)

        if not target_image.exists():
            return {
                "status": "error",
                "error": f"Image file not found: {target_image}",
                "detections": [],
                "inference_duration_ms": 0.0,
                "prediction_image_path": None,
                "model_id": "waste_models_yolo",
            }

        if not self.is_loaded:
            loaded = self.load()
            if not loaded or self.model is None:
                return {
                    "status": "error",
                    "error": f"Model unavailable: {self._load_error}",
                    "detections": [],
                    "inference_duration_ms": 0.0,
                    "prediction_image_path": None,
                    "model_id": "waste_models_yolo",
                }

        start_time = time.perf_counter()
        detections: List[Dict[str, Any]] = []

        try:
            # Thread-safe prediction
            with self._lock:
                results = self.model.predict(
                    source=str(target_image),
                    conf=conf,
                    verbose=False,
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            if not results or len(results) == 0:
                pred_path = self._save_empty_annotation(target_image, capture_id)
                return {
                    "status": "success",
                    "detections": [],
                    "inference_duration_ms": round(elapsed_ms, 2),
                    "prediction_image_path": str(pred_path),
                    "model_id": "waste_models_yolo",
                }

            result = results[0]
            boxes = result.boxes

            # Parse bounding boxes
            if boxes is not None and len(boxes) > 0:
                for idx, box in enumerate(boxes):
                    cls_id = int(box.cls[0].item())
                    score = float(box.conf[0].item())
                    cls_name = self._class_names.get(cls_id, "unknown").lower()

                    # Enforce strict model mapping: only 'dry' and 'metal'
                    if cls_name not in ("dry", "metal"):
                        continue

                    # xywh (center x, center y, width, height) or xyxy
                    xyxy = box.xyxy[0].tolist()
                    x1, y1, x2, y2 = xyxy
                    bbox_x = round(x1, 2)
                    bbox_y = round(y1, 2)
                    bbox_width = round(x2 - x1, 2)
                    bbox_height = round(y2 - y1, 2)

                    detections.append({
                        "item_index": idx,
                        "ai_class": cls_name,
                        "ai_confidence": round(min(max(score, 0.0), 1.0), 4),
                        "bbox_x": bbox_x,
                        "bbox_y": bbox_y,
                        "bbox_width": bbox_width,
                        "bbox_height": bbox_height,
                    })

            # Save annotated prediction image
            pred_filename = f"{capture_id}.jpg"
            pred_dest = settings.PREDICTIONS_IMAGES_DIR / pred_filename
            settings.PREDICTIONS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

            # Render plot using PIL
            plotted_bgr = result.plot()
            if plotted_bgr is not None:
                plotted_rgb = plotted_bgr[..., ::-1]
                Image.fromarray(plotted_rgb).save(pred_dest, format="JPEG", quality=95)
            else:
                self._save_empty_annotation(target_image, capture_id)

            return {
                "status": "success",
                "detections": detections,
                "inference_duration_ms": round(elapsed_ms, 2),
                "prediction_image_path": str(pred_dest),
                "model_id": "waste_models_yolo",
            }

        except Exception as exc:
            logger.exception(f"Inference error on {target_image}: {exc}")
            # Fallback to copy image to predictions
            pred_path = self._save_empty_annotation(target_image, capture_id)
            return {
                "status": "error",
                "error": str(exc),
                "detections": [],
                "inference_duration_ms": round((time.perf_counter() - start_time) * 1000.0, 2),
                "prediction_image_path": str(pred_path),
                "model_id": "waste_models_yolo",
            }

    def _save_empty_annotation(self, source_path: Path, capture_id: str) -> Path:
        """Copy or save original image as prediction image when no detections exist."""
        dest = settings.PREDICTIONS_IMAGES_DIR / f"{capture_id}.jpg"
        settings.PREDICTIONS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(source_path) as img:
                img.save(dest, format="JPEG", quality=95)
        except Exception:
            pass
        return dest


# Global singleton inference instance
vision_service = VisionInferenceService()
