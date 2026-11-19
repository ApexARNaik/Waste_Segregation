import io
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

import serial
from PIL import Image

from app.config import settings
from app.database import create_capture_with_detections
from app.vision.inference import vision_service

logger = logging.getLogger(__name__)


def read_line(ser: serial.Serial, timeout: float = 5.0) -> str:
    """Read a single line from serial with timeout."""
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    while time.monotonic() < deadline:
        if ser.in_waiting:
            char = ser.read(1)
            if char == b"\n":
                return buffer.decode("ascii", errors="replace").strip()
            if char:
                buffer.extend(char)
        else:
            time.sleep(0.01)
    return buffer.decode("ascii", errors="replace").strip()


def read_exact(ser: serial.Serial, length: int, timeout: float = 15.0) -> bytes:
    """Read exact byte count from serial port."""
    deadline = time.monotonic() + timeout
    received = bytearray()
    while len(received) < length:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Image transfer incomplete: {len(received)}/{length} bytes")
        chunk = ser.read(min(4096, length - len(received)))
        if chunk:
            received.extend(chunk)
        else:
            time.sleep(0.005)
    return bytes(received)


class CameraSerialWorker:
    """
    Serial manager for ESP32-CAM on USB Serial (COM9).
    Single worker owns the COM port with thread-safe synchronization.
    Supports manual captures and future hardware IR 'TRIGGER' messages.
    """

    def __init__(self):
        self.port = settings.CAMERA_PORT
        self.baud = settings.CAMERA_BAUD
        self.crop_box = settings.CROP_BOX

        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional[serial.Serial] = None

        self.status = "disconnected"
        self.last_capture_time: Optional[str] = None
        self.last_capture_result: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None

    def start(self) -> None:
        """Start the background serial communication worker thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._worker_loop, name="CameraSerialWorker", daemon=True)
            self._thread.start()
            logger.info(f"CameraSerialWorker started for port {self.port} at {self.baud} baud.")

    def stop(self) -> None:
        """Stop background worker and close serial connection."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._close_serial()
        self.status = "disconnected"
        logger.info("CameraSerialWorker stopped.")

    def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def get_status(self) -> Dict[str, Any]:
        """Return camera worker status for dashboards and health endpoints."""
        return {
            "status": self.status,
            "port": self.port,
            "baud": self.baud,
            "trigger_mode": settings.CAMERA_TRIGGER,
            "ir_pin": settings.IR_SENSOR_PIN,
            "crop_box": settings.CROP_BOX,
            "last_capture_time": self.last_capture_time,
            "last_capture_result": self.last_capture_result,
            "last_error": self.last_error,
        }

    def request_manual_capture(self, wait_timeout: float = 10.0) -> Dict[str, Any]:
        """
        Thread-safe manual capture request.
        Can be dispatched to physical camera or mock if physical camera is offline.
        """
        completion_event = threading.Event()
        result_holder: Dict[str, Any] = {}

        self._queue.put({
            "type": "capture",
            "source": "manual",
            "event": completion_event,
            "result_holder": result_holder,
        })

        # Wait for worker thread to process capture
        finished = completion_event.wait(timeout=wait_timeout)
        if not finished:
            return {
                "status": "error",
                "message": "Manual capture timed out waiting for serial response.",
            }
        return result_holder.get("response", {"status": "error", "message": "No response"})

    def process_image_bytes(
        self,
        raw_jpeg_bytes: bytes,
        source: str = "esp32_cam",
    ) -> Dict[str, Any]:
        """
        Crop raw frame to chute box, run local YOLO inference,
        save images, and record pending entry in database.
        """
        capture_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Open image from bytes
        with Image.open(io.BytesIO(raw_jpeg_bytes)) as img:
            img.load()

            # Chute cropping
            crop_left, crop_top, crop_right, crop_bottom = settings.CROP_BOX
            crop_right = min(crop_right, img.width)
            crop_bottom = min(crop_bottom, img.height)
            crop_left = max(0, crop_left)
            crop_top = max(0, crop_top)

            if crop_left < crop_right and crop_top < crop_bottom:
                cropped = img.crop((crop_left, crop_top, crop_right, crop_bottom))
            else:
                cropped = img

            # Save raw cropped image
            raw_dest = settings.RAW_IMAGES_DIR / f"{capture_id}.jpg"
            settings.RAW_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            cropped.save(raw_dest, format="JPEG", quality=95)

        # Execute local YOLO inference
        inference_result = vision_service.predict(
            image_path=raw_dest,
            capture_id=capture_id,
            confidence_threshold=settings.MODEL_CONFIDENCE,
        )

        detections = inference_result.get("detections", [])
        prediction_image_path = inference_result.get("prediction_image_path")
        model_id = inference_result.get("model_id", "waste_models_yolo")

        # Save record in database with status='pending'
        create_capture_with_detections(
            image_path=str(raw_dest),
            prediction_image_path=str(prediction_image_path) if prediction_image_path else None,
            model_id=model_id,
            captured_at=now_iso,
            source=source,
            detections=detections,
        )

        res_summary = {
            "status": "success",
            "capture_id": capture_id,
            "captured_at": now_iso,
            "detection_count": len(detections),
            "detections": detections,
            "inference_duration_ms": inference_result.get("inference_duration_ms", 0.0),
        }

        self.last_capture_time = now_iso
        self.last_capture_result = res_summary
        return res_summary

    def _worker_loop(self) -> None:
        """Main serial worker polling loop."""
        while not self._stop_event.is_set():
            # If not connected, attempt connection
            if self._serial is None or not self._serial.is_open:
                try:
                    self._serial = serial.Serial(
                        port=self.port,
                        baudrate=self.baud,
                        timeout=0.25,
                        write_timeout=3.0,
                    )
                    time.sleep(1.5)  # ESP32 reset settling time

                    # Ping camera
                    self._serial.reset_input_buffer()
                    self._serial.write(b"PING\n")
                    self._serial.flush()
                    resp = read_line(self._serial, timeout=2.0)
                    if "READY" in resp:
                        self.status = "connected"
                        self.last_error = None
                        logger.info(f"ESP32-CAM connected on {self.port} and ready.")
                    else:
                        self.status = "connected"  # Port open even if READY not received
                except Exception as exc:
                    self.status = "disconnected"
                    self.last_error = str(exc)
                    self._close_serial()

            # If disconnected, check if we need to handle manual requests via fallback/mock
            if self.status == "disconnected":
                try:
                    task = self._queue.get(timeout=1.0)
                    # If hardware disconnected, fallback to sample capture or error response
                    event = task.get("event")
                    result_holder = task.get("result_holder")
                    if task.get("type") == "capture":
                        res = self._fallback_sample_capture(task.get("source", "manual_fallback"))
                        if result_holder is not None:
                            result_holder["response"] = res
                    if event:
                        event.set()
                except queue.Empty:
                    pass
                continue

            # Connected mode: check for queued manual requests or serial incoming messages
            try:
                # 1. Check for manual capture request in queue
                try:
                    task = self._queue.get(timeout=0.1)
                    if task.get("type") == "capture":
                        self.status = "capturing"
                        res = self._execute_serial_capture(task.get("source", "manual"))
                        self.status = "connected"
                        result_holder = task.get("result_holder")
                        if result_holder is not None:
                            result_holder["response"] = res
                        event = task.get("event")
                        if event:
                            event.set()
                except queue.Empty:
                    pass

                # 2. Check if ESP32 sent an incoming message (e.g., 'TRIGGER' from hardware IR sensor)
                if self._serial and self._serial.in_waiting:
                    line = read_line(self._serial, timeout=0.1)
                    if line == "TRIGGER":
                        logger.info("Hardware IR TRIGGER received from ESP32-CAM! Executing capture.")
                        self.status = "capturing"
                        self._execute_serial_capture(source="ir_sensor")
                        self.status = "connected"

            except Exception as exc:
                logger.error(f"Error in serial communication: {exc}")
                self.status = "disconnected"
                self.last_error = str(exc)
                self._close_serial()
                time.sleep(1.0)

    def _execute_serial_capture(self, source: str = "manual") -> Dict[str, Any]:
        """Perform CAPTURE command exchange over serial and process image."""
        if not self._serial or not self._serial.is_open:
            return {"status": "error", "message": "Serial port not open"}

        try:
            self._serial.reset_input_buffer()
            self._serial.write(b"CAPTURE\n")
            self._serial.flush()

            # Read header IMG:length,width,height
            header = ""
            for _ in range(10):
                line = read_line(self._serial, timeout=4.0)
                if line.startswith("IMG:"):
                    header = line
                    break
                elif line.startswith("ERROR:"):
                    raise RuntimeError(f"ESP32-CAM error: {line}")

            if not header.startswith("IMG:"):
                raise ValueError(f"Did not receive valid image header from ESP32: '{header}'")

            parts = header[4:].split(",")
            length, width, height = int(parts[0]), int(parts[1]), int(parts[2])

            payload = read_exact(self._serial, length, timeout=10.0)
            return self.process_image_bytes(payload, source=source)

        except Exception as exc:
            logger.error(f"Serial capture failed: {exc}")
            self.last_error = str(exc)
            return {"status": "error", "message": str(exc)}

    def _fallback_sample_capture(self, source: str = "manual_fallback") -> Dict[str, Any]:
        """
        Graceful fallback when physical hardware is disconnected:
        Reads one of the preserved local sample captures to test end-to-end pipeline.
        """
        sample_candidates = list((settings.BASE_DIR / "captures").glob("*/*.jpg"))
        if not sample_candidates:
            return {
                "status": "error",
                "message": f"Camera on {self.port} is disconnected and no sample captures exist.",
            }

        # Pick a sample capture image
        sample_path = sample_candidates[0]
        try:
            with open(sample_path, "rb") as f:
                payload = f.read()
            return self.process_image_bytes(payload, source=source)
        except Exception as exc:
            return {"status": "error", "message": f"Fallback capture failed: {exc}"}


# Global singleton camera worker instance
camera_worker = CameraSerialWorker()
