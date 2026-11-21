import json
import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.security import create_admin_token
from app.sensors.serial_service import (
    SensorSerialService,
    parse_telemetry_line,
    sensor_service,
)


class MockSerialDevice:
    """Mock serial stream for testing ESP32 sensor controller protocol."""

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

    def readline(self) -> bytes:
        idx = self.incoming.find(b"\n")
        if idx != -1:
            line = self.incoming[: idx + 1]
            self.incoming = self.incoming[idx + 1 :]
            return bytes(line)
        line = bytes(self.incoming)
        self.incoming.clear()
        return line

    def write(self, data: bytes) -> int:
        self.outgoing.extend(data)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


@pytest.fixture
def auth_header():
    token = create_admin_token()
    return {"Authorization": f"Bearer {token}"}


# =====================================================================
# 1. Parsing valid sensor telemetry JSON
# =====================================================================
def test_parse_valid_telemetry_json():
    raw = (
        '{"type":"telemetry","uptime_ms":125000,"rain_analog":2450,"rain_digital":1,'
        '"ir_raw":0,"ir_detected":true,"inductive_raw":1,"metal_detected":false,'
        '"servo_angle":90,"stepper_angle_estimated":0,"stepper_speed_rpm":5,"stepper_busy":false}\n'
    )
    parsed = parse_telemetry_line(raw)
    assert parsed is not None
    assert parsed["type"] == "telemetry"
    assert parsed["rain_analog"] == 2450
    assert parsed["rain_digital"] == 1
    assert parsed["ir_raw"] == 0
    assert parsed["ir_detected"] is True
    assert parsed["inductive_raw"] == 1
    assert parsed["metal_detected"] is False
    assert parsed["servo_angle"] == 90
    assert parsed["stepper_speed_rpm"] == 5
    assert parsed["stepper_busy"] is False


# =====================================================================
# 2. Handling malformed JSON messages
# =====================================================================
def test_handling_malformed_json_messages():
    # Incomplete JSON
    assert parse_telemetry_line('{"type":"telemetry"') is None
    # Plain text / boot debug logs
    assert parse_telemetry_line("Guru Meditation Error: Core 1 panic'ed") is None
    # Empty strings and whitespace
    assert parse_telemetry_line("") is None
    assert parse_telemetry_line("   \n") is None
    # Corrupted / non-dict JSON
    assert parse_telemetry_line("[1, 2, 3]") is None
    assert parse_telemetry_line('{"incomplete": ') is None


# =====================================================================
# 3. Handling missing or null sensor values
# =====================================================================
def test_missing_or_null_sensor_values():
    service = SensorSerialService(port=None, stale_seconds=10.0)
    service._connected = True

    # Telemetry with partial / null values
    partial_data = {
        "type": "telemetry",
        "rain_analog": 1800,
        "rain_digital": None,
        "ir_raw": 1,
        # inductive omitted
    }
    service._update_telemetry_snapshot(partial_data)

    latest = service.get_latest()
    assert latest["connected"] is True
    assert latest["sensors"]["rain_analog"] == 1800
    assert latest["sensors"]["rain_digital"] is None
    assert latest["sensors"]["ir_raw"] == 1
    assert latest["sensors"]["inductive_raw"] is None


# =====================================================================
# 4. Stale telemetry detection
# =====================================================================
def test_stale_telemetry_detection():
    service = SensorSerialService(port=None, stale_seconds=0.1)
    service._connected = True

    # Fresh update
    service._update_telemetry_snapshot({
        "rain_analog": 2000,
        "rain_digital": 1,
        "ir_raw": 1,
        "ir_detected": False,
        "inductive_raw": 1,
        "metal_detected": False,
    })
    assert service.is_stale() is False
    latest = service.get_latest()
    assert latest["stale"] is False
    assert latest["sensors"]["rain_analog"] == 2000

    # Wait until stale threshold passes
    time.sleep(0.15)
    assert service.is_stale() is True

    stale_latest = service.get_latest()
    assert stale_latest["stale"] is True
    # Sensor values must be masked to None when stale to avoid presenting stale readings as current
    assert stale_latest["sensors"]["rain_analog"] is None


# =====================================================================
# 5. Controller disconnection and reconnection
# =====================================================================
def test_controller_disconnection_and_reconnection():
    service = SensorSerialService(port="COM_TEST", baud=115200)

    # Initial state
    assert service.is_connected is False
    status = service.get_status()
    assert status["connected"] is False
    assert status["stale"] is True

    # Simulate connection with MockSerial responding to PING
    mock_ser = MockSerialDevice(b'{"type":"ack","command":"PING","value":null,"success":true,"message":"PONG"}\n')
    with patch("serial.Serial", return_value=mock_ser):
        connected = service._open_serial()
        assert connected is True
        assert service.is_connected is True

    # Simulate disconnection
    service._close_serial()
    with service._lock:
        service._connected = False
    assert service.is_connected is False

    # Simulate reconnection
    mock_ser2 = MockSerialDevice(b'{"type":"ack","command":"PING","value":null,"success":true,"message":"PONG"}\n')
    with patch("serial.Serial", return_value=mock_ser2):
        reconnected = service._open_serial()
        assert reconnected is True
        assert service.is_connected is True

    service.stop()


# =====================================================================
# 6. Serial command queue synchronization
# =====================================================================
def test_serial_command_queue_synchronization():
    service = SensorSerialService(port="COM_TEST")
    service._connected = True

    mock_ser = MockSerialDevice()
    service._serial = mock_ser

    # Simulate worker thread executing command from queue
    def fake_worker_command_execution():
        item = service._command_queue.get(timeout=1.0)
        # Mock immediate response
        item["response"] = {
            "type": "ack",
            "command": "SERVO",
            "value": 90,
            "success": True,
            "message": "Servo set to 90 deg",
        }
        item["event"].set()

    import threading
    t = threading.Thread(target=fake_worker_command_execution)
    t.start()

    res = service.send_command("SERVO", 90, timeout=2.0)
    t.join()

    assert res["success"] is True
    assert res["command"] == "SERVO"
    assert res["value"] == 90


# =====================================================================
# 7. Actuator command validation
# =====================================================================
def test_actuator_command_validation(auth_header):
    client = TestClient(app)

    # Servo: angle must be between 0 and 180
    resp_invalid_servo_high = client.post(
        "/api/admin/actuators/servo",
        headers=auth_header,
        json={"angle": 181},
    )
    assert resp_invalid_servo_high.status_code == 422

    resp_invalid_servo_low = client.post(
        "/api/admin/actuators/servo",
        headers=auth_header,
        json={"angle": -1},
    )
    assert resp_invalid_servo_low.status_code == 422

    # Stepper: angle must be between -360 and 360
    resp_invalid_stepper = client.post(
        "/api/admin/actuators/stepper",
        headers=auth_header,
        json={"angle": 400},
    )
    assert resp_invalid_stepper.status_code == 422

    # Stepper Speed: rpm must be between 1 and 10
    resp_invalid_speed = client.post(
        "/api/admin/actuators/stepper/speed",
        headers=auth_header,
        json={"rpm": 15},
    )
    assert resp_invalid_speed.status_code == 422


# =====================================================================
# 8. API authentication for motor commands
# =====================================================================
def test_api_authentication_for_motor_commands():
    client = TestClient(app)

    # Missing token -> 401
    resp_no_auth = client.post("/api/admin/actuators/servo", json={"angle": 90})
    assert resp_no_auth.status_code == 401

    # Invalid token -> 401
    resp_bad_auth = client.post(
        "/api/admin/actuators/servo",
        headers={"Authorization": "Bearer invalid_garbage_token"},
        json={"angle": 90},
    )
    assert resp_bad_auth.status_code == 401


# =====================================================================
# 9. Motor command acknowledgement handling
# =====================================================================
def test_motor_command_acknowledgement_handling(auth_header):
    client = TestClient(app)

    with patch.object(SensorSerialService, "is_connected", new_callable=PropertyMock, return_value=True):
        # 1. Success ACK
        with patch.object(
            sensor_service,
            "send_command",
            return_value={"type": "ack", "command": "SERVO", "value": 90, "success": True, "message": "OK"},
        ):
            resp = client.post("/api/admin/actuators/servo", headers=auth_header, json={"angle": 90})
            assert resp.status_code == 200
            assert resp.json()["success"] is True

        # 2. Failure ACK from controller
        with patch.object(
            sensor_service,
            "send_command",
            return_value={"type": "ack", "command": "SERVO", "value": 90, "success": False, "message": "Motor stalled"},
        ):
            resp = client.post("/api/admin/actuators/servo", headers=auth_header, json={"angle": 90})
            assert resp.status_code == 400
            assert "Motor stalled" in resp.json()["detail"]

        # 3. Timeout error
        with patch.object(
            sensor_service,
            "send_command",
            side_effect=TimeoutError("Command timed out waiting for controller acknowledgement"),
        ):
            resp = client.post("/api/admin/actuators/servo", headers=auth_header, json={"angle": 90})
            assert resp.status_code == 503
            assert "timed out" in resp.json()["detail"]


# =====================================================================
# 10. Frontend display when controller is disconnected
# =====================================================================
def test_controller_disconnected_api_response(auth_header):
    client = TestClient(app)

    with patch.object(SensorSerialService, "is_connected", new_callable=PropertyMock, return_value=False):
        # Latest endpoint returns connected: false and null sensor readings
        resp_latest = client.get("/api/sensors/latest")
        assert resp_latest.status_code == 200
        data = resp_latest.json()
        assert data["connected"] is False
        assert data["stale"] is True
        assert data["sensors"]["rain_analog"] is None
        assert data["sensors"]["ir_detected"] is None
        assert data["sensors"]["metal_detected"] is None

        # Status endpoint returns controller state
        resp_status = client.get("/api/sensors/status")
        assert resp_status.status_code == 200
        status_data = resp_status.json()
        assert status_data["connected"] is False

        # Motor commands return 503 when controller is offline
        resp_servo = client.post("/api/admin/actuators/servo", headers=auth_header, json={"angle": 90})
        assert resp_servo.status_code == 503
        assert "offline or disconnected" in resp_servo.json()["detail"]
