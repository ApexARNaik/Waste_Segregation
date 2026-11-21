import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

import serial

from app.config import settings

logger = logging.getLogger(__name__)


def parse_telemetry_line(raw_line: str) -> Optional[Dict[str, Any]]:
    """
    Safely parse and validate a raw string as JSON telemetry or ack line.
    Returns None if line is malformed or invalid JSON.
    """
    cleaned = raw_line.strip()
    if not cleaned or not (cleaned.startswith("{") and cleaned.endswith("}")):
        return None
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        pass
    return None


class SensorSerialService:
    """
    Serial manager for ESP32 DevKit V1 (Sensors & Actuators Controller).
    Strictly independent from the ESP32-CAM serial worker.
    Runs on its own COM port (default 115200 baud).
    
    Provides:
      - Continuous non-blocking telemetry ingestion (JSON Lines)
      - Telemetry freshness and staleness tracking
      - Thread-safe command queue for motor control (Servo & Stepper)
      - Single-owner thread for serial port I/O
      - Automatic reconnection on disconnection
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: Optional[int] = None,
        stale_seconds: Optional[float] = None,
    ):
        self.port = port if port is not None else settings.SENSOR_CONTROLLER_PORT
        self.baud = baud if baud is not None else settings.SENSOR_CONTROLLER_BAUD
        self.stale_seconds = (
            stale_seconds
            if stale_seconds is not None
            else settings.SENSOR_TELEMETRY_STALE_SECONDS
        )

        self._lock = threading.Lock()
        self._command_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional[serial.Serial] = None

        self._connected: bool = False
        self._last_telemetry_monotonic: Optional[float] = None
        self._last_telemetry_iso: Optional[str] = None
        self._last_error: Optional[str] = None

        # In-memory latest snapshots
        self._sensors: Dict[str, Any] = {
            "rain_analog": None,
            "rain_digital": None,
            "ir_raw": None,
            "ir_detected": None,
            "inductive_raw": None,
            "metal_detected": None,
        }
        self._actuators: Dict[str, Any] = {
            "servo_angle": None,
            "stepper_angle_estimated": None,
            "stepper_speed_rpm": None,
            "stepper_busy": None,
        }

    @property
    def is_connected(self) -> bool:
        return self._connected

    def is_stale(self) -> bool:
        """Return True if telemetry is missing, offline, or older than stale threshold."""
        if not self._connected or self._last_telemetry_monotonic is None:
            return True
        return (time.monotonic() - self._last_telemetry_monotonic) > self.stale_seconds

    def start(self) -> None:
        """Start the background serial worker thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="SensorSerialWorker",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                f"SensorSerialService worker started for port '{self.port}' at {self.baud} baud."
            )

    def stop(self) -> None:
        """Stop background worker and safely close serial port."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._close_serial()
        with self._lock:
            self._connected = False

    def _close_serial(self) -> None:
        """Close active serial instance if open."""
        with self._lock:
            if self._serial:
                try:
                    if self._serial.is_open:
                        self._serial.close()
                except Exception as err:
                    logger.debug(f"Error closing sensor serial port: {err}")
                finally:
                    self._serial = None

    def _open_serial(self) -> bool:
        """Attempt to open and configure the DevKit serial port."""
        if not self.port:
            self._last_error = "SENSOR_CONTROLLER_PORT is not configured in .env"
            return False

        try:
            self._close_serial()
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=0.2,
                write_timeout=1.0,
            )
            time.sleep(1.0)  # Allow ESP32 boot/reset settling
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Verify connection via PING
            ser.write(b"PING\n")
            ser.flush()

            deadline = time.monotonic() + 2.5
            ping_ok = False
            while time.monotonic() < deadline:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    data = parse_telemetry_line(line)
                    if data and data.get("type") == "ack" and data.get("command") == "PING":
                        ping_ok = True
                        break
                    # Also accept incoming telemetry if controller streams immediately
                    if data and data.get("type") == "telemetry":
                        ping_ok = True
                        self._update_telemetry_snapshot(data)
                        break

            if not ping_ok:
                logger.warning(f"Sensor controller at {self.port} did not respond to PING handshake.")
                ser.close()
                self._last_error = "Handshake failed: No PING ack received from controller."
                return False

            with self._lock:
                self._serial = ser
                self._connected = True
                self._last_error = None
            logger.info(f"Sensor controller connected successfully on {self.port} ({self.baud} baud).")
            return True

        except Exception as exc:
            self._last_error = f"Failed to connect to {self.port}: {str(exc)}"
            logger.debug(self._last_error)
            with self._lock:
                self._connected = False
            return False

    def _update_telemetry_snapshot(self, data: Dict[str, Any]) -> None:
        """Thread-safe update of parsed sensor and actuator telemetry."""
        with self._lock:
            self._last_telemetry_monotonic = time.monotonic()
            self._last_telemetry_iso = datetime.now(timezone.utc).isoformat()

            # Update sensors
            for key in ["rain_analog", "rain_digital", "ir_raw", "ir_detected", "inductive_raw", "metal_detected"]:
                if key in data:
                    self._sensors[key] = data[key]

            # Update actuators
            for key in ["servo_angle", "stepper_angle_estimated", "stepper_speed_rpm", "stepper_busy"]:
                if key in data:
                    self._actuators[key] = data[key]

    def _worker_loop(self) -> None:
        """Main serial loop: streams telemetry and processes queued motor commands."""
        while not self._stop_event.is_set():
            if not self._connected or not self._serial or not self._serial.is_open:
                if not self.port:
                    self._stop_event.wait(2.0)
                    continue

                success = self._open_serial()
                if not success:
                    self._stop_event.wait(2.0)
                    continue

            # Connected: process commands and telemetry
            ser = self._serial
            try:
                # 1. Process queued motor command if present
                if not self._command_queue.empty():
                    try:
                        cmd_item = self._command_queue.get_nowait()
                        self._execute_command_on_serial(ser, cmd_item)
                    except queue.Empty:
                        pass

                # 2. Read incoming serial telemetry lines
                if ser.in_waiting:
                    raw_bytes = ser.readline()
                    line = raw_bytes.decode("utf-8", errors="replace").strip()
                    if line:
                        parsed = parse_telemetry_line(line)
                        if parsed:
                            if parsed.get("type") == "telemetry":
                                self._update_telemetry_snapshot(parsed)
                        else:
                            logger.debug(f"Ignored non-JSON line on sensor serial: {line[:60]}")
                else:
                    time.sleep(0.01)

            except (serial.SerialException, OSError, IOError) as err:
                logger.warning(f"Sensor controller disconnected: {err}")
                with self._lock:
                    self._connected = False
                    self._last_error = f"Disconnected: {str(err)}"
                self._close_serial()
                self._drain_pending_commands_on_error("Sensor controller disconnected.")
                self._stop_event.wait(2.0)

    def _execute_command_on_serial(self, ser: serial.Serial, cmd_item: Dict[str, Any]) -> None:
        """Send command string and wait for matching JSON acknowledgement."""
        cmd_str = cmd_item["cmd"]
        event = cmd_item["event"]
        deadline = time.monotonic() + cmd_item.get("timeout", 3.0)

        try:
            ser.reset_input_buffer()
            ser.write(f"{cmd_str}\n".encode("ascii"))
            ser.flush()

            ack_found = False
            while time.monotonic() < deadline:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                parsed = parse_telemetry_line(line)
                if not parsed:
                    continue

                if parsed.get("type") == "ack":
                    cmd_item["response"] = parsed
                    ack_found = True
                    break
                elif parsed.get("type") == "telemetry":
                    self._update_telemetry_snapshot(parsed)

            if not ack_found:
                cmd_item["error"] = f"Timeout waiting for acknowledgement of command '{cmd_str}'"
        except Exception as err:
            cmd_item["error"] = f"Serial error sending '{cmd_str}': {str(err)}"
        finally:
            event.set()

    def _drain_pending_commands_on_error(self, error_message: str) -> None:
        """Fail all waiting commands when serial is severed."""
        while not self._command_queue.empty():
            try:
                cmd_item = self._command_queue.get_nowait()
                cmd_item["error"] = error_message
                cmd_item["event"].set()
            except queue.Empty:
                break

    def get_latest(self) -> Dict[str, Any]:
        """
        Return the latest sensor readings and actuator statuses.
        Follows the strict response schema. Returns nulls when offline/stale.
        """
        with self._lock:
            connected = self._connected
            stale = self.is_stale()
            last_update = self._last_telemetry_iso

            if not connected or stale:
                # Do not fabricate sensor values when offline or stale
                return {
                    "connected": connected,
                    "last_update": last_update,
                    "stale": stale,
                    "sensors": {
                        "rain_analog": None,
                        "rain_digital": None,
                        "ir_raw": None,
                        "ir_detected": None,
                        "inductive_raw": None,
                        "metal_detected": None,
                    },
                    "actuators": {
                        "servo_angle": self._actuators["servo_angle"] if connected else None,
                        "stepper_angle_estimated": self._actuators["stepper_angle_estimated"] if connected else None,
                        "stepper_speed_rpm": self._actuators["stepper_speed_rpm"] if connected else None,
                        "stepper_busy": self._actuators["stepper_busy"] if connected else None,
                    },
                }

            return {
                "connected": True,
                "last_update": last_update,
                "stale": False,
                "sensors": dict(self._sensors),
                "actuators": dict(self._actuators),
            }

    def get_status(self) -> Dict[str, Any]:
        """Return controller connection status, COM port, baud rate, and health."""
        with self._lock:
            return {
                "connected": self._connected,
                "port": self.port or None,
                "baud": self.baud,
                "last_update": self._last_telemetry_iso,
                "stale": self.is_stale(),
                "last_error": self._last_error,
            }

    def send_command(self, cmd_name: str, value: Any, timeout: float = 3.0) -> Dict[str, Any]:
        """
        Thread-safe synchronous dispatch of an actuator command to the DevKit.
        Blocks until acknowledgement is received or timeout expires.
        """
        with self._lock:
            if not self._connected or not self._serial or not self._serial.is_open:
                raise RuntimeError("ESP32 DevKit sensor controller is offline or disconnected.")

        event = threading.Event()
        cmd_str = f"{cmd_name} {value}".strip() if value is not None else cmd_name.strip()
        cmd_item = {
            "cmd": cmd_str,
            "event": event,
            "response": None,
            "error": None,
            "timeout": timeout,
        }

        self._command_queue.put(cmd_item)

        if not event.wait(timeout=timeout + 0.5):
            raise TimeoutError(f"Command '{cmd_str}' timed out waiting for controller acknowledgement.")

        if cmd_item["error"]:
            raise RuntimeError(cmd_item["error"])

        return cmd_item["response"]


# Global sensor service singleton
sensor_service = SensorSerialService()
