import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.database import get_connection, init_db


@pytest.fixture
def client_with_isolated_db(tmp_path):
    """Provide a TestClient with an isolated test SQLite database."""
    test_db = tmp_path / "test_api_database.db"
    original_db = settings.DB_PATH
    settings.DB_PATH = test_db
    init_db(test_db)

    with TestClient(app) as test_client:
        yield test_client, test_db

    settings.DB_PATH = original_db


def test_root_endpoint(client_with_isolated_db):
    """GET / returns application details, version, and documentation link."""
    client, _ = client_with_isolated_db
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Smart Waste Segregation API"
    assert data["status"] == "running"
    assert data["version"] == "1.0.0"
    assert data["docs_url"] == "/docs"


def test_health_endpoint(client_with_isolated_db):
    """GET /api/health returns healthy status and connected database."""
    client, _ = client_with_isolated_db
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database_status"] == "connected"


def test_admin_login_success_and_failure(client_with_isolated_db):
    """POST /api/admin/login validates credentials correctly."""
    client, _ = client_with_isolated_db

    # Success with correct password
    resp = client.post("/api/admin/login", json={"password": settings.ADMIN_PASSWORD})
    assert resp.status_code == 200
    login_data = resp.json()
    assert "access_token" in login_data
    assert login_data["token_type"] == "bearer"
    assert login_data["expires_in"] == settings.SESSION_TTL_SECONDS

    # Failure with incorrect password
    resp_fail = client.post("/api/admin/login", json={"password": "IncorrectPassword123"})
    assert resp_fail.status_code == 401
    assert "Invalid admin credentials" in resp_fail.json()["detail"]


def test_admin_login_rate_limiting(client_with_isolated_db):
    """POST /api/admin/login blocks requests after excessive failed attempts."""
    client, _ = client_with_isolated_db
    from app.security import reset_rate_limit

    test_ip = "testclient"
    reset_rate_limit(test_ip)

    # 5 failed attempts (allowed threshold)
    for _ in range(5):
        resp = client.post("/api/admin/login", json={"password": "bad_password"})
        assert resp.status_code == 401

    # 6th attempt should be rate limited (429)
    resp_blocked = client.post("/api/admin/login", json={"password": "bad_password"})
    assert resp_blocked.status_code == 429
    assert "Too many failed login attempts" in resp_blocked.json()["detail"]

    # Clean up rate limit for other tests
    reset_rate_limit(test_ip)


def test_admin_me_requires_authentication(client_with_isolated_db):
    """GET /api/admin/me requires valid bearer token."""
    client, _ = client_with_isolated_db

    # Unauthenticated request
    unauth_resp = client.get("/api/admin/me")
    assert unauth_resp.status_code == 401

    # Request with invalid token
    invalid_resp = client.get(
        "/api/admin/me",
        headers={"Authorization": "Bearer invalid_garbage_token"}
    )
    assert invalid_resp.status_code == 401

    # Authenticated request
    login_resp = client.post("/api/admin/login", json={"password": settings.ADMIN_PASSWORD})
    token = login_resp.json()["access_token"]

    auth_resp = client.get(
        "/api/admin/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert auth_resp.status_code == 200
    user_data = auth_resp.json()
    assert user_data["username"] == "admin"
    assert user_data["role"] == "admin"


def test_admin_database_requires_authentication(client_with_isolated_db):
    """GET /api/admin/database requires authentication and returns internal metrics."""
    client, test_db = client_with_isolated_db

    # Unauthenticated rejected
    unauth_resp = client.get("/api/admin/database")
    assert unauth_resp.status_code == 401

    # Authenticated
    login_resp = client.post("/api/admin/login", json={"password": settings.ADMIN_PASSWORD})
    token = login_resp.json()["access_token"]

    auth_resp = client.get(
        "/api/admin/database",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert auth_resp.status_code == 200
    db_metrics = auth_resp.json()
    assert db_metrics["database_status"] == "connected"
    assert "total_captures" in db_metrics
    assert "total_detections" in db_metrics
    assert "pending_captures" in db_metrics
    assert "approved_captures" in db_metrics
    assert "rejected_captures" in db_metrics


def test_public_statistics_never_exposes_pending_or_rejected(client_with_isolated_db):
    """
    CRITICAL PRIVACY TEST:
    GET /api/public/stats must NEVER expose pending or rejected predictions.
    """
    client, test_db = client_with_isolated_db

    # Populate test database with pending, rejected, and approved data
    conn = get_connection(test_db)
    cap_pending = str(uuid.uuid4())
    cap_rejected = str(uuid.uuid4())
    cap_approved = str(uuid.uuid4())

    with conn:
        # Pending capture with 2 dry items
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'pend.jpg', '2026-11-19T10:00:00Z', 'pending');", (cap_pending,))
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'dry', 0.9, 'dry', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_pending))
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 1, 'dry', 0.95, 'dry', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_pending))

        # Rejected capture with 1 metal item
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'rej.jpg', '2026-11-19T10:01:00Z', 'rejected');", (cap_rejected,))
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'metal', 0.85, 'metal', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_rejected))

        # Approved capture with 1 wet, 1 metal item
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'app.jpg', '2026-11-19T10:02:00Z', 'approved');", (cap_approved,))
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'wet', 0.98, 'wet', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_approved))
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 1, 'metal', 0.91, 'metal', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_approved))

    conn.close()

    # Query public endpoint
    pub_resp = client.get("/api/public/stats")
    assert pub_resp.status_code == 200
    pub_data = pub_resp.json()

    # Only approved items must be reflected
    assert pub_data["approved_captures"] == 1
    assert pub_data["approved_wet_detections"] == 1
    assert pub_data["approved_metal_detections"] == 1
    # 2 dry items were in pending capture, so approved dry detections MUST be 0
    assert pub_data["approved_dry_detections"] == 0

    # Ensure admin endpoint reflects all items
    login_resp = client.post("/api/admin/login", json={"password": settings.ADMIN_PASSWORD})
    token = login_resp.json()["access_token"]
    admin_resp = client.get("/api/admin/database", headers={"Authorization": f"Bearer {token}"})
    admin_data = admin_resp.json()

    assert admin_data["total_captures"] == 3
    assert admin_data["total_detections"] == 5
    assert admin_data["pending_captures"] == 1
    assert admin_data["approved_captures"] == 1
    assert admin_data["rejected_captures"] == 1
