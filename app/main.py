from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import settings
from app.database import (
    get_connection,
    get_database_stats,
    get_public_stats,
    init_db,
)
from app.security import (
    check_rate_limit,
    create_admin_token,
    record_failed_attempt,
    require_admin,
    reset_rate_limit,
    verify_admin_password,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown validation."""
    # 1. Validate configuration and fail fast if missing/insecure
    settings.validate()
    # 2. Initialize database schema safely
    init_db()
    yield


app = FastAPI(
    title="Smart Waste Segregation Backend",
    description="Backend API for AI-powered Smart Waste Segregation System (Stage 1 Foundation)",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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
    database_status: str = Field(..., description="Database connection status")


class PublicStatsResponse(BaseModel):
    approved_captures: int = Field(..., description="Total count of admin-approved captures")
    approved_wet_detections: int = Field(..., description="Total count of approved wet waste items")
    approved_dry_detections: int = Field(..., description="Total count of approved dry waste items")
    approved_metal_detections: int = Field(..., description="Total count of approved metal waste items")


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


# =====================================================================
# Public Endpoints
# =====================================================================

@app.get("/", response_model=RootResponse, tags=["Public"])
def read_root() -> RootResponse:
    """Return application name, status, version, and API documentation link."""
    return RootResponse(
        name="Smart Waste Segregation API",
        status="running",
        version="1.0.0",
        docs_url="/docs",
    )


@app.get("/api/health", response_model=HealthResponse, tags=["Public"])
def health_check() -> HealthResponse:
    """Check application health and test live database connectivity."""
    db_status = "connected"
    try:
        conn = get_connection()
        conn.execute("SELECT 1;").fetchone()
        conn.close()
    except Exception:
        db_status = "error"

    app_status = "healthy" if db_status == "connected" else "degraded"
    return HealthResponse(
        status=app_status,
        database_status=db_status,
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


# =====================================================================
# Admin Endpoints
# =====================================================================

@app.post("/api/admin/login", response_model=AdminLoginResponse, tags=["Admin"])
def admin_login(payload: AdminLoginRequest, request: Request) -> AdminLoginResponse:
    """
    Authenticate admin using single admin password.
    Returns signed expiring bearer session token.
    Enforces in-memory rate limiting against brute force attempts.
    """
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
    """Return the authenticated admin identity and privileges."""
    return AdminMeResponse(
        username=current_admin.get("sub", "admin"),
        role=current_admin.get("role", "admin"),
    )


@app.get("/api/admin/database", response_model=AdminDatabaseResponse, tags=["Admin"])
def get_admin_database_stats(current_admin: dict = Depends(require_admin)) -> AdminDatabaseResponse:
    """Return database connection status and full internal metrics."""
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
