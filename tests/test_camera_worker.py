import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import serial
from PIL import Image

from app.camera.serial_worker import CameraSerialWorker, read_exact, read_line
from app.config import settings
from app.database import get_connection, init_db


class MockSerial:
    """Mock serial stream for testing ESP32 protocol."""
    def __init__(self, incoming_data: bytes = b""):
        self.incoming = bytearray(incoming_data)
        self.outgoing = bytearray()
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def read(self, size: int = 1) -> bytes:
        chunk = self.incoming[:size]
        self.incoming = self.incoming[size:]
        return bytes(chunk)

    def write(self, data: bytes) -> int:
        self.outgoing.extend(data)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


def test_serial_read_line():
    """Verify read_line reads newline-delimited ASCII strings."""
    mock_ser = MockSerial(b"READY\nSECOND_LINE\n")
    line1 = read_line(mock_ser, timeout=1.0)
    assert line1 == "READY"
    line2 = read_line(mock_ser, timeout=1.0)
    assert line2 == "SECOND_LINE"


def test_serial_read_exact():
    """Verify read_exact reads exact byte length or times out."""
    payload = b"1234567890ABCDEF"
    mock_ser = MockSerial(payload)
    read_data = read_exact(mock_ser, 10, timeout=1.0)
    assert read_data == b"1234567890"
    assert len(read_data) == 10

    # Short read timeout
    with pytest.raises(TimeoutError):
        read_exact(mock_ser, 50, timeout=0.1)


def test_camera_worker_execute_serial_capture():
    """Verify worker sends CAPTURE, parses IMG header, and processes JPEG."""
    img = Image.new("RGB", (640, 480), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    header = f"IMG:{len(jpeg_bytes)},640,480\n".encode("ascii")
    stream_bytes = header + jpeg_bytes

    worker = CameraSerialWorker()
    mock_ser = MockSerial(stream_bytes)
    worker._serial = mock_ser

    # Mock process_image_bytes to verify call
    worker.process_image_bytes = MagicMock(return_value={"status": "success", "capture_id": "mock_cap"})

    res = worker._execute_serial_capture(source="test_serial")
    assert res["status"] == "success"
    assert res["capture_id"] == "mock_cap"
    assert mock_ser.outgoing == b"CAPTURE\n"
    worker.process_image_bytes.assert_called_once()


def test_no_sample_image_fallback_when_disconnected(tmp_path):
    """
    CRITICAL FIX 2:
    When camera is disconnected, verify no sample images are picked from captures/,
    no database records are created, and an explicit error is returned.
    """
    db_file = tmp_path / "test_no_fallback.db"
    init_db(db_file)
    original_db = settings.DB_PATH
    settings.DB_PATH = db_file

    try:
        worker = CameraSerialWorker()
        worker.status = "disconnected"
        worker._serial = None

        res = worker.request_manual_capture(wait_timeout=1.0)
        assert res["status"] == "error"
        assert "disconnected" in res["message"].lower()

        # Check database: 0 records created
        conn = get_connection(db_file)
        count = conn.execute("SELECT COUNT(*) FROM captures;").fetchone()[0]
        conn.close()
        assert count == 0
    finally:
        settings.DB_PATH = original_db


def test_camera_status_when_ping_does_not_return_ready():
    """
    CRITICAL FIX 4:
    If serial opens but ESP32 does not respond with READY to PING,
    camera must be marked disconnected and invalid serial closed.
    """
    worker = CameraSerialWorker()
    mock_ser = MockSerial(b"GARBAGE_OR_SILENCE\n")

    with patch("serial.Serial", return_value=mock_ser):
        with patch("time.sleep"):
            success = worker._attempt_connection()

    assert success is False
    assert worker.status == "disconnected"
    assert "READY" in worker.last_error
    assert mock_ser.is_open is False


def test_camera_status_when_ping_returns_ready():
    """Verify camera marks status as connected only when PING returns READY."""
    worker = CameraSerialWorker()
    mock_ser = MockSerial(b"READY\n")

    with patch("serial.Serial", return_value=mock_ser):
        with patch("time.sleep"):
            success = worker._attempt_connection()

    assert success is True
    assert worker.status == "connected"
    assert worker.last_error is None


def test_capture_id_consistency_across_database_and_files(tmp_path):
    """
    CRITICAL FIX 1:
    Verify exactly one capture ID is generated and used consistently across
    raw image filename, prediction filename, SQLite record, and result dict.
    """
    db_file = tmp_path / "test_consistent_id.db"
    init_db(db_file)
    original_db = settings.DB_PATH
    settings.DB_PATH = db_file

    try:
        # Create test JPEG
        img = Image.new("RGB", (640, 480), color=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        worker = CameraSerialWorker()
        result = worker.process_image_bytes(jpeg_bytes, source="test_consistent")

        assert result["status"] == "success"
        assigned_id = result["capture_id"]
        assert assigned_id is not None
        assert worker.last_capture_result["capture_id"] == assigned_id

        # Verify raw image file matches assigned_id
        expected_raw_file = settings.RAW_IMAGES_DIR / f"{assigned_id}.jpg"
        assert expected_raw_file.exists()

        # Verify prediction image file matches assigned_id
        expected_pred_file = settings.PREDICTIONS_IMAGES_DIR / f"{assigned_id}.jpg"
        assert expected_pred_file.exists()

        # Verify SQLite record has identical id
        conn = get_connection(db_file)
        row = conn.execute("SELECT * FROM captures WHERE id = ?;", (assigned_id,)).fetchone()
        assert row is not None
        assert row["id"] == assigned_id
        assert Path(row["image_path"]).resolve() == expected_raw_file.resolve()
        assert Path(row["prediction_image_path"]).resolve() == expected_pred_file.resolve()

        # Clean up files created during test
        expected_raw_file.unlink(missing_ok=True)
        expected_pred_file.unlink(missing_ok=True)
        conn.close()
    finally:
        settings.DB_PATH = original_db


def test_inference_failure_handling(tmp_path):
    """
    CRITICAL FIX 3:
    Inference failure must be recorded as an error and must NOT create
    an empty-chute pending capture in SQLite.
    """
    db_file = tmp_path / "test_inference_fail.db"
    init_db(db_file)
    original_db = settings.DB_PATH
    settings.DB_PATH = db_file

    try:
        img = Image.new("RGB", (640, 480), color=(0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        worker = CameraSerialWorker()

        # Mock vision_service.predict to simulate an inference failure
        with patch("app.camera.serial_worker.vision_service.predict") as mock_predict:
            mock_predict.return_value = {
                "status": "error",
                "error": "Simulated GPU OutOfMemory or Model Error",
                "detections": [],
                "inference_duration_ms": 12.5,
                "prediction_image_path": None,
                "model_id": "waste_models_yolo",
            }

            result = worker.process_image_bytes(jpeg_bytes, source="test_fail")

        # Must return error
        assert result["status"] == "error"
        assert "Inference failure" in result["message"]
        assert worker.last_capture_result["status"] == "error"

        # Must NOT create a capture record in SQLite
        conn = get_connection(db_file)
        count = conn.execute("SELECT COUNT(*) FROM captures;").fetchone()[0]
        conn.close()
        assert count == 0

        # Clean up raw image
        raw_file = settings.RAW_IMAGES_DIR / f"{result['capture_id']}.jpg"
        raw_file.unlink(missing_ok=True)
    finally:
        settings.DB_PATH = original_db
