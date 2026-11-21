import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.camera.serial_worker import camera_worker
from app.config import settings
from app.sensors.serial_service import sensor_service
from app.database import (
    approve_capture,
    correct_detection_class,
    get_capture_by_id,
    get_connection,
    get_database_stats,
    get_public_stats,
    init_db,
    list_captures,
    list_public_captures,
    reject_capture,
)
from app.security import (
    check_rate_limit,
    create_admin_token,
    record_failed_attempt,
    require_admin,
    reset_rate_limit,
    verify_admin_password,
)
from app.vision.inference import vision_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown validation."""
    # 1. Validate environment configuration
    settings.validate()
    # 2. Initialize and migrate database schema safely
    init_db()
    # 3. Load local YOLO model once on startup
    vision_service.load()
    # 4. Start camera serial worker background thread
    camera_worker.start()
    # 5. Start sensor controller serial service background thread
    sensor_service.start()

    yield

    # Shutdown
    sensor_service.stop()
    camera_worker.stop()


app = FastAPI(
    title="Smart Waste Segregation System",
    description="Backend API and Local Inference Dashboard for AI Waste Segregation",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Enable CORS for local development and LAN access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# Pydantic Models
# =====================================================================

class RootResponse(BaseModel):
    name: str = Field(..., description="Application name")
    status: str = Field(..., description="Current running status")
    version: str = Field(..., description="API Version")
    docs_url: str = Field(..., description="Path to interactive API documentation")


class HealthResponse(BaseModel):
    status: str = Field(..., description="Application health status")
    api_status: str = Field(default="healthy", description="API status")
    database_status: str = Field(..., description="Database connection status")
    camera_status: str = Field(..., description="Camera worker status")
    model_status: Dict[str, Any] = Field(..., description="YOLO model operational status")


class PublicStatsResponse(BaseModel):
    approved_captures: int = Field(..., description="Total count of admin-approved captures")
    approved_wet_detections: int = Field(..., description="Total count of approved wet waste items")
    approved_dry_detections: int = Field(..., description="Total count of approved dry waste items")
    approved_metal_detections: int = Field(..., description="Total count of approved metal waste items")


class PublicDetection(BaseModel):
    id: str
    item_index: int
    final_class: Optional[str]
    ai_confidence: float
    bbox_x: float
    bbox_y: float
    bbox_width: float
    bbox_height: float


class PublicCaptureItem(BaseModel):
    id: str
    captured_at: str
    status: str
    model_id: Optional[str]
    created_at: str
    image_url: str
    detections: List[PublicDetection]


class AdminLoginRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Admin password")


class AdminLoginResponse(BaseModel):
    access_token: str = Field(..., description="Signed session token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token validity duration in seconds")


class AdminMeResponse(BaseModel):
    username: str = Field(..., description="Admin identifier")
    role: str = Field(..., description="Role of the authenticated entity")


class AdminDatabaseResponse(BaseModel):
    database_status: str = Field(..., description="Database connection status")
    total_captures: int = Field(..., description="Total captures recorded")
    total_detections: int = Field(..., description="Total individual object detections recorded")
    pending_captures: int = Field(..., description="Captures awaiting admin review")
    approved_captures: int = Field(..., description="Captures approved by admin")
    rejected_captures: int = Field(..., description="Captures rejected by admin")


class AdminDashboardResponse(BaseModel):
    total_captures: int
    pending_captures: int
    approved_captures: int
    rejected_captures: int
    dry_detections: int
    metal_detections: int
    wet_detections: int
    camera_status: Dict[str, Any]
    model_status: Dict[str, Any]


class ReviewActionRequest(BaseModel):
    admin_note: Optional[str] = Field(None, description="Optional reviewer notes")


class CorrectionRequest(BaseModel):
    final_class: str = Field(..., description="Corrected waste class ('wet', 'dry', or 'metal')")
    note: Optional[str] = Field(None, description="Audit note for classification correction")


class SensorData(BaseModel):
    rain_analog: Optional[int] = Field(None, description="Raw ADC 12-bit value (0-4095)")
    rain_digital: Optional[int] = Field(None, description="Digital state (0 or 1)")
    ir_raw: Optional[int] = Field(None, description="Digital state (0 or 1)")
    ir_detected: Optional[bool] = Field(None, description="Interpreted obstacle detected state")
    inductive_raw: Optional[int] = Field(None, description="Digital state (0 or 1)")
    metal_detected: Optional[bool] = Field(None, description="Interpreted metal detected state")


class ActuatorData(BaseModel):
    servo_angle: Optional[int] = Field(None, description="Current commanded angle (0-180)")
    stepper_angle_estimated: Optional[int] = Field(None, description="Estimated accumulated rotation in degrees since boot")
    stepper_speed_rpm: Optional[int] = Field(None, description="Configured speed in RPM (1-10)")
    stepper_busy: Optional[bool] = Field(None, description="True if stepper is currently rotating")


class LatestSensorsResponse(BaseModel):
    connected: bool = Field(..., description="ESP32 DevKit connection state")
    last_update: Optional[str] = Field(None, description="ISO-8601 timestamp of last telemetry received")
    stale: bool = Field(..., description="Whether telemetry is stale or missing")
    sensors: SensorData
    actuators: ActuatorData


class SensorStatusResponse(BaseModel):
    connected: bool = Field(..., description="ESP32 DevKit connection status")
    port: Optional[str] = Field(None, description="Configured serial COM port")
    baud: int = Field(..., description="Serial baud rate")
    last_update: Optional[str] = Field(None, description="Timestamp of last valid telemetry received")
    stale: bool = Field(..., description="Stale telemetry flag")
    last_error: Optional[str] = Field(None, description="Last connection or communication error")


class ServoCommandRequest(BaseModel):
    angle: int = Field(..., ge=0, le=180, description="Target angle in degrees (0 to 180)")


class StepperCommandRequest(BaseModel):
    angle: int = Field(..., ge=-360, le=360, description="Rotation angle in degrees (-360 to 360)")


class StepperSpeedRequest(BaseModel):
    rpm: int = Field(..., ge=1, le=10, description="Rotation speed in RPM (1 to 10)")



# =====================================================================
# Public Endpoints
# =====================================================================

@app.get("/api/health", response_model=HealthResponse, tags=["Public"])
def health_check() -> HealthResponse:
    """Check overall application health, database, camera serial worker, and YOLO model."""
    db_status = "connected"
    try:
        conn = get_connection()
        conn.execute("SELECT 1;").fetchone()
        conn.close()
    except Exception:
        db_status = "error"

    cam_status_dict = camera_worker.get_status()
    model_status_dict = vision_service.get_status()

    is_healthy = db_status == "connected" and model_status_dict.get("loaded", False)
    return HealthResponse(
        status="healthy" if is_healthy else "degraded",
        api_status="healthy",
        database_status=db_status,
        camera_status=cam_status_dict.get("status", "unknown"),
        model_status=model_status_dict,
    )


@app.get("/api/public/stats", response_model=PublicStatsResponse, tags=["Public"])
def get_public_statistics() -> PublicStatsResponse:
    """
    Return waste segregation statistics for public viewing.
    STRICT PRIVACY: Exclusively returns counts for admin-approved items.
    Pending and rejected predictions are never exposed.
    """
    stats = get_public_stats()
    return PublicStatsResponse(
        approved_captures=stats["approved_captures"],
        approved_wet_detections=stats["approved_wet_detections"],
        approved_dry_detections=stats["approved_dry_detections"],
        approved_metal_detections=stats["approved_metal_detections"],
    )


@app.get("/api/public/captures", response_model=List[PublicCaptureItem], tags=["Public"])
def get_public_approved_captures(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> List[PublicCaptureItem]:
    """
    Return recent approved captures with prediction images.
    Strictly filters out pending and rejected captures.
    """
    approved_items = list_public_captures(limit=limit, offset=offset)
    return [PublicCaptureItem(**item) for item in approved_items]


@app.get("/api/sensors/latest", response_model=LatestSensorsResponse, tags=["Sensors"])
def get_latest_sensors() -> LatestSensorsResponse:
    """Return the latest real-time sensor readings and actuator statuses."""
    data = sensor_service.get_latest()
    return LatestSensorsResponse(**data)


@app.get("/api/sensors/status", response_model=SensorStatusResponse, tags=["Sensors"])
def get_sensors_status() -> SensorStatusResponse:
    """Return ESP32 DevKit controller connectivity, COM port, baud rate, and error status."""
    status_data = sensor_service.get_status()
    return SensorStatusResponse(**status_data)


@app.get("/api/public/images/{capture_id}", tags=["Public"])
def get_public_capture_image(capture_id: str) -> FileResponse:
    """
    Safely serve annotated prediction image ONLY if capture is approved.
    Pending and rejected images return 404 to ensure complete privacy.
    """
    cap = get_capture_by_id(capture_id)
    if not cap or cap.get("status") != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found or access restricted.",
        )

    pred_path_str = cap.get("prediction_image_path") or cap.get("image_path")
    if not pred_path_str:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file missing.")

    file_path = Path(pred_path_str).resolve()
    # Path traversal protection
    allowed_dirs = [settings.RAW_IMAGES_DIR.resolve(), settings.PREDICTIONS_IMAGES_DIR.resolve()]
    if not any(file_path.is_relative_to(d) for d in allowed_dirs) or not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found on disk.")

    return FileResponse(path=file_path, media_type="image/jpeg")


# =====================================================================
# Admin Endpoints (Require require_admin)
# =====================================================================

@app.post("/api/admin/login", response_model=AdminLoginResponse, tags=["Admin"])
def admin_login(payload: AdminLoginRequest, request: Request) -> AdminLoginResponse:
    """Authenticate admin using password and return signed expiring bearer session token."""
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    if not verify_admin_password(payload.password):
        record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    reset_rate_limit(client_ip)
    token = create_admin_token()

    return AdminLoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.SESSION_TTL_SECONDS,
    )


@app.get("/api/admin/me", response_model=AdminMeResponse, tags=["Admin"])
def get_admin_me(current_admin: dict = Depends(require_admin)) -> AdminMeResponse:
    """Return authenticated admin identity and role."""
    return AdminMeResponse(
        username=current_admin.get("sub", "admin"),
        role=current_admin.get("role", "admin"),
    )


@app.get("/api/admin/database", response_model=AdminDatabaseResponse, tags=["Admin"])
def get_admin_database_stats(current_admin: dict = Depends(require_admin)) -> AdminDatabaseResponse:
    """Return database connection status and full internal metrics (Stage 1 compatibility)."""
    db_status = "connected"
    try:
        stats = get_database_stats()
    except Exception:
        db_status = "error"
        stats = {
            "total_captures": 0,
            "total_detections": 0,
            "pending_captures": 0,
            "approved_captures": 0,
            "rejected_captures": 0,
        }

    return AdminDatabaseResponse(
        database_status=db_status,
        total_captures=stats["total_captures"],
        total_detections=stats["total_detections"],
        pending_captures=stats["pending_captures"],
        approved_captures=stats["approved_captures"],
        rejected_captures=stats["rejected_captures"],
    )


@app.get("/api/admin/dashboard", response_model=AdminDashboardResponse, tags=["Admin"])
def get_admin_dashboard(current_admin: dict = Depends(require_admin)) -> AdminDashboardResponse:
    """Return aggregate system metrics, camera state, and vision model status."""
    stats = get_database_stats()
    cam_status = camera_worker.get_status()
    model_status = vision_service.get_status()

    return AdminDashboardResponse(
        total_captures=stats["total_captures"],
        pending_captures=stats["pending_captures"],
        approved_captures=stats["approved_captures"],
        rejected_captures=stats["rejected_captures"],
        dry_detections=stats["dry_detections"],
        metal_detections=stats["metal_detections"],
        wet_detections=stats["wet_detections"],
        camera_status=cam_status,
        model_status=model_status,
    )


@app.get("/api/admin/captures", tags=["Admin"])
def get_admin_captures_list(
    status: Optional[str] = Query(None, description="Filter by 'pending', 'approved', or 'rejected'"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_admin: dict = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Return captures with full detections and image URLs for admin review."""
    caps = list_captures(status=status, limit=limit, offset=offset)
    for c in caps:
        c["raw_image_url"] = f"/api/admin/images/{c['id']}/raw"
        c["prediction_image_url"] = f"/api/admin/images/{c['id']}/prediction"
    return caps


@app.get("/api/admin/captures/{capture_id}", tags=["Admin"])
def get_admin_capture_detail(
    capture_id: str,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Return complete capture details, detections, and review event audit trail."""
    cap = get_capture_by_id(capture_id)
    if not cap:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture not found.")
    cap["raw_image_url"] = f"/api/admin/images/{cap['id']}/raw"
    cap["prediction_image_url"] = f"/api/admin/images/{cap['id']}/prediction"
    return cap


@app.post("/api/admin/captures/{capture_id}/approve", tags=["Admin"])
def approve_pending_capture(
    capture_id: str,
    payload: Optional[ReviewActionRequest] = None,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Approve a capture. Sets final_class = ai_class for unreviewed detections and audits event."""
    try:
        note = payload.admin_note if payload else None
        reviewer = current_admin.get("sub", "admin")
        updated = approve_capture(capture_id=capture_id, reviewer=reviewer, note=note)
        return updated
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.post("/api/admin/captures/{capture_id}/reject", tags=["Admin"])
def reject_pending_capture(
    capture_id: str,
    payload: Optional[ReviewActionRequest] = None,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Reject a capture and log review event."""
    try:
        note = payload.admin_note if payload else None
        reviewer = current_admin.get("sub", "admin")
        updated = reject_capture(capture_id=capture_id, reviewer=reviewer, note=note)
        return updated
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.patch("/api/admin/detections/{detection_id}", tags=["Admin"])
def update_detection_classification(
    detection_id: str,
    payload: CorrectionRequest,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """
    Admin correction of an individual detection class.
    Updates final_class while preserving original ai_class and ai_confidence.
    """
    try:
        reviewer = current_admin.get("sub", "admin")
        updated_det = correct_detection_class(
            detection_id=detection_id,
            new_class=payload.final_class,
            reviewer=reviewer,
            note=payload.note,
        )
        return updated_det
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.post("/api/admin/camera/capture", tags=["Admin"])
def trigger_manual_camera_capture(
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Queue manual camera capture through the dedicated serial worker."""
    result = camera_worker.request_manual_capture(wait_timeout=12.0)
    if result.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("message", "Camera capture failed"),
        )
    return result


@app.get("/api/admin/camera/status", tags=["Admin"])
def get_admin_camera_status(
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Return camera worker status, baud, port, and last capture metrics."""
    return camera_worker.get_status()


@app.get("/api/admin/images/{capture_id}/{image_type}", tags=["Admin"])
def get_admin_capture_image(
    capture_id: str,
    image_type: str,
    current_admin: dict = Depends(require_admin),
) -> FileResponse:
    """Serve raw or annotated prediction image for admin review with path validation."""
    if image_type not in ("raw", "prediction"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image_type must be 'raw' or 'prediction'")

    cap = get_capture_by_id(capture_id)
    if not cap:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Capture not found.")

    target_path_str = cap.get("prediction_image_path") if image_type == "prediction" else cap.get("image_path")
    if not target_path_str:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file path empty.")

    file_path = Path(target_path_str).resolve()
    allowed_dirs = [settings.RAW_IMAGES_DIR.resolve(), settings.PREDICTIONS_IMAGES_DIR.resolve()]
    if not any(file_path.is_relative_to(d) for d in allowed_dirs) or not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file not found on disk.")

    return FileResponse(path=file_path, media_type="image/jpeg")


# =====================================================================
# Actuator Control Endpoints (Require require_admin)
# =====================================================================

@app.post("/api/admin/actuators/servo", tags=["Actuators"])
def set_servo_angle(
    payload: ServoCommandRequest,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin command to set servo motor position (0-180 degrees)."""
    if not sensor_service.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ESP32 DevKit sensor controller is offline or disconnected.",
        )
    try:
        ack = sensor_service.send_command("SERVO", payload.angle)
        if not ack.get("success", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ack.get("message", "Servo command rejected by controller"),
            )
        return ack
    except (RuntimeError, TimeoutError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        )


@app.post("/api/admin/actuators/stepper", tags=["Actuators"])
def rotate_stepper(
    payload: StepperCommandRequest,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin command to rotate stepper motor by relative degrees (-360 to 360)."""
    if not sensor_service.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ESP32 DevKit sensor controller is offline or disconnected.",
        )
    try:
        ack = sensor_service.send_command("STEPPER", payload.angle)
        if not ack.get("success", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ack.get("message", "Stepper command rejected by controller"),
            )
        return ack
    except (RuntimeError, TimeoutError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        )


@app.post("/api/admin/actuators/stepper/speed", tags=["Actuators"])
def set_stepper_speed(
    payload: StepperSpeedRequest,
    current_admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin command to configure stepper motor speed (1-10 RPM)."""
    if not sensor_service.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ESP32 DevKit sensor controller is offline or disconnected.",
        )
    try:
        ack = sensor_service.send_command("SPEED", payload.rpm)
        if not ack.get("success", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ack.get("message", "Stepper speed command rejected by controller"),
            )
        return ack
    except (RuntimeError, TimeoutError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        )


# =====================================================================
# Frontend SPA Integration
# =====================================================================

if settings.FRONTEND_DIST_DIR.exists():
    # Mount assets folder
    assets_dir = settings.FRONTEND_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", tags=["Frontend"])
    def serve_frontend_spa(full_path: str, request: Request):
        """Serve built React single-page application for browser navigation or API root for JSON requests."""
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("redoc") or full_path.startswith("openapi.json"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

        # Static file check (e.g., favicon, manifest)
        static_candidate = settings.FRONTEND_DIST_DIR / full_path
        if full_path and static_candidate.is_file():
            return FileResponse(static_candidate)

        # If API client requesting root without text/html
        accept = request.headers.get("accept", "")
        if full_path == "" and "text/html" not in accept:
            return RootResponse(
                name="Smart Waste Segregation API",
                status="running",
                version="1.0.0",
                docs_url="/docs",
            )

        # SPA fallback to index.html for browser navigation
        index_file = settings.FRONTEND_DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frontend build missing.")
else:
    @app.get("/", response_model=RootResponse, tags=["Public"])
    def read_root_api() -> RootResponse:
        """API Root endpoint when frontend dist is not built."""
        return RootResponse(
            name="Smart Waste Segregation API",
            status="running",
            version="1.0.0",
            docs_url="/docs",
        )
