import pytest
from pathlib import Path
from app.config import settings
from app.vision.inference import VisionInferenceService


@pytest.fixture
def vision():
    """Return an instance of VisionInferenceService."""
    service = VisionInferenceService()
    service.load()
    return service


def test_model_loading_and_classes(vision):
    """Verify local YOLO model loads and exposes expected dry and metal classes."""
    assert vision.is_loaded is True
    status = vision.get_status()
    assert status["loaded"] is True
    assert status["task"] == "detect"
    # Current model classes are {0: 'dry', 1: 'metal'}
    classes = status["classes"]
    assert 0 in classes and classes[0] == "dry"
    assert 1 in classes and classes[1] == "metal"


def test_inference_on_sample_metal_image(vision):
    """Verify inference on known metal sample returns detections with bbox and saves prediction."""
    sample_img = settings.BASE_DIR / "captures" / "session_01(metal)" / "image_00002.jpg"
    if not sample_img.exists():
        pytest.skip("Sample metal image not found")

    res = vision.predict(sample_img, capture_id="test_metal_cap")
    assert res["status"] == "success"
    assert len(res["detections"]) >= 1
    det = res["detections"][0]
    assert det["ai_class"] in ("dry", "metal")
    assert 0.0 <= det["ai_confidence"] <= 1.0
    assert det["bbox_width"] > 0
    assert det["bbox_height"] > 0
    assert res["inference_duration_ms"] > 0
    # Prediction image saved
    assert Path(res["prediction_image_path"]).exists()


def test_inference_on_empty_chute_image(vision):
    """Verify empty chute produces exactly zero detections and does not invent fake classes."""
    empty_img = settings.BASE_DIR / "captures" / "session_01(empty)" / "image_00001.jpg"
    if not empty_img.exists():
        pytest.skip("Sample empty image not found")

    res = vision.predict(empty_img, capture_id="test_empty_cap")
    assert res["status"] == "success"
    assert len(res["detections"]) == 0
    # Prediction image still created for inspection
    assert Path(res["prediction_image_path"]).exists()


def test_inference_missing_image_graceful(vision):
    """Verify missing image returns error status without crashing."""
    res = vision.predict("non_existent_file.jpg", capture_id="test_missing_cap")
    assert res["status"] == "error"
    assert "not found" in res["error"].lower()
    assert len(res["detections"]) == 0
