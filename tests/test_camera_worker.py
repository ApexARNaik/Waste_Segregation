import io
import pytest
from unittest.mock import MagicMock
from PIL import Image

from app.camera.serial_worker import read_line, read_exact, CameraSerialWorker


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


def test_camera_worker_execute_serial_capture(tmp_path):
    """Verify worker sends CAPTURE, parses IMG header, and processes JPEG."""
    # Generate a small 640x480 test JPEG in memory
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
