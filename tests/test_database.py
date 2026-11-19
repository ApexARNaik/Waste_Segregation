import sqlite3
import uuid
import pytest
from app.database import (
    get_connection,
    init_db,
    get_database_stats,
    get_public_stats,
    create_capture_with_detections,
    approve_capture,
    reject_capture,
    correct_detection_class,
    list_public_captures,
)


@pytest.fixture
def temp_db(tmp_path):
    """Provide an isolated temporary database for testing."""
    db_file = tmp_path / "test_waste_segregation.db"
    init_db(db_file)
    return db_file


def test_database_initialization(temp_db):
    """Verify database schema initializes tables and pragmas."""
    conn = get_connection(temp_db)
    cursor = conn.cursor()

    # Check tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert "captures" in tables
    assert "detections" in tables
    assert "review_events" in tables

    # Check foreign keys enabled
    cursor.execute("PRAGMA foreign_keys;")
    assert cursor.fetchone()[0] == 1

    conn.close()


def test_repeated_initialization_preserves_data(temp_db):
    """Verify calling init_db repeatedly does not delete existing records."""
    conn = get_connection(temp_db)
    capture_id = str(uuid.uuid4())
    with conn:
        conn.execute(
            """
            INSERT INTO captures (id, image_path, captured_at, status)
            VALUES (?, ?, ?, ?);
            """,
            (capture_id, "data/images/raw/test.jpg", "2026-11-19T10:00:00Z", "pending")
        )
    conn.close()

    # Re-run init_db
    init_db(temp_db)

    conn = get_connection(temp_db)
    row = conn.execute("SELECT * FROM captures WHERE id = ?;", (capture_id,)).fetchone()
    assert row is not None
    assert row["id"] == capture_id
    conn.close()


def test_create_capture_record(temp_db):
    """Verify capture records can be inserted with allowed statuses."""
    conn = get_connection(temp_db)
    cap_id = str(uuid.uuid4())

    with conn:
        conn.execute(
            """
            INSERT INTO captures (id, image_path, source, captured_at, status)
            VALUES (?, ?, ?, ?, ?);
            """,
            (cap_id, "data/images/raw/cap1.jpg", "esp32_cam", "2026-11-19T10:00:00Z", "pending")
        )

    row = conn.execute("SELECT * FROM captures WHERE id = ?;", (cap_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["source"] == "esp32_cam"
    conn.close()


def test_multiple_detections_belong_to_capture(temp_db):
    """Verify multiple detections can be linked to a single capture."""
    conn = get_connection(temp_db)
    cap_id = str(uuid.uuid4())

    with conn:
        conn.execute(
            """
            INSERT INTO captures (id, image_path, captured_at, status)
            VALUES (?, ?, ?, ?);
            """,
            (cap_id, "data/images/raw/multi.jpg", "2026-11-19T10:00:00Z", "approved")
        )

        det1_id = str(uuid.uuid4())
        det2_id = str(uuid.uuid4())

        conn.execute(
            """
            INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (det1_id, cap_id, 0, "dry", 0.92, "dry", 10.0, 20.0, 100.0, 150.0)
        )
        conn.execute(
            """
            INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (det2_id, cap_id, 1, "metal", 0.88, "metal", 150.0, 30.0, 80.0, 90.0)
        )

    rows = conn.execute("SELECT * FROM detections WHERE capture_id = ? ORDER BY item_index;", (cap_id,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["ai_class"] == "dry"
    assert rows[1]["ai_class"] == "metal"
    conn.close()


def test_invalid_class_rejected(temp_db):
    """Verify check constraint rejects invalid classes outside ('wet', 'dry', 'metal')."""
    conn = get_connection(temp_db)
    cap_id = str(uuid.uuid4())

    with conn:
        conn.execute(
            """
            INSERT INTO captures (id, image_path, captured_at, status)
            VALUES (?, ?, ?, ?);
            """,
            (cap_id, "data/images/raw/test.jpg", "2026-11-19T10:00:00Z", "pending")
        )

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (str(uuid.uuid4()), cap_id, 0, "plastic", 0.9, "plastic", 0, 0, 10, 10)
            )

    conn.close()


def test_invalid_confidence_rejected(temp_db):
    """Verify check constraint rejects confidence values outside [0.0, 1.0]."""
    conn = get_connection(temp_db)
    cap_id = str(uuid.uuid4())

    with conn:
        conn.execute(
            """
            INSERT INTO captures (id, image_path, captured_at, status)
            VALUES (?, ?, ?, ?);
            """,
            (cap_id, "data/images/raw/test.jpg", "2026-11-19T10:00:00Z", "pending")
        )

    # Test confidence > 1.0
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (str(uuid.uuid4()), cap_id, 0, "dry", 1.25, "dry", 0, 0, 10, 10)
            )

    # Test confidence < 0.0
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (str(uuid.uuid4()), cap_id, 0, "dry", -0.05, "dry", 0, 0, 10, 10)
            )

    conn.close()


def test_foreign_key_enforcement(temp_db):
    """Verify foreign key constraint fails when referencing non-existent capture."""
    conn = get_connection(temp_db)
    non_existent_cap_id = str(uuid.uuid4())

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (str(uuid.uuid4()), non_existent_cap_id, 0, "wet", 0.85, "wet", 0, 0, 10, 10)
            )

    conn.close()


def test_capture_status_constraint(temp_db):
    """Verify captures table rejects invalid status values."""
    conn = get_connection(temp_db)
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'bad.jpg', '2026-11-19T10:00:00Z', 'unknown_status');",
                (str(uuid.uuid4()),)
            )
    conn.close()


def test_review_events_and_class_correction(temp_db):
    """Verify review_events audit trail and preserving original AI class on correction."""
    conn = get_connection(temp_db)
    cap_id = str(uuid.uuid4())
    det_id = str(uuid.uuid4())
    rev_id = str(uuid.uuid4())

    with conn:
        conn.execute(
            "INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'corr.jpg', '2026-11-19T10:00:00Z', 'pending');",
            (cap_id,)
        )
        # Original AI prediction: dry, 0.88
        conn.execute(
            """
            INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height)
            VALUES (?, ?, 0, 'dry', 0.88, 'dry', 10, 10, 50, 50);
            """,
            (det_id, cap_id)
        )

        # Admin corrects classification from 'dry' to 'metal'
        conn.execute(
            """
            INSERT INTO review_events (id, capture_id, detection_id, action, previous_class, new_class, reviewer, note)
            VALUES (?, ?, ?, 'classification_changed', 'dry', 'metal', 'admin_1', 'Visual inspection confirms metal can');
            """,
            (rev_id, cap_id, det_id)
        )
        conn.execute(
            "UPDATE detections SET final_class = 'metal' WHERE id = ?;",
            (det_id,)
        )
        conn.execute(
            "UPDATE captures SET status = 'approved', reviewed_by = 'admin_1', reviewed_at = '2026-11-19T10:05:00Z' WHERE id = ?;",
            (cap_id,)
        )

    # Check that original AI prediction is intact
    det_row = conn.execute("SELECT * FROM detections WHERE id = ?;", (det_id,)).fetchone()
    assert det_row["ai_class"] == "dry"
    assert det_row["ai_confidence"] == 0.88
    assert det_row["final_class"] == "metal"

    # Check review event record
    rev_row = conn.execute("SELECT * FROM review_events WHERE id = ?;", (rev_id,)).fetchone()
    assert rev_row["action"] == "classification_changed"
    assert rev_row["previous_class"] == "dry"
    assert rev_row["new_class"] == "metal"
    assert rev_row["reviewer"] == "admin_1"

    # Check invalid action rejected
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """
                INSERT INTO review_events (id, capture_id, action, reviewer)
                VALUES (?, ?, 'unauthorized_action', 'admin_1');
                """,
                (str(uuid.uuid4()), cap_id)
            )

    conn.close()


def test_database_stats_calculation(temp_db):
    """Verify database stats calculation for captures and detections."""
    conn = get_connection(temp_db)

    # Insert 1 pending, 2 approved, 1 rejected captures
    cap_pending = str(uuid.uuid4())
    cap_app1 = str(uuid.uuid4())
    cap_app2 = str(uuid.uuid4())
    cap_rej = str(uuid.uuid4())

    with conn:
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'p.jpg', '2026-11-19T10:00:00Z', 'pending');", (cap_pending,))
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'a1.jpg', '2026-11-19T10:01:00Z', 'approved');", (cap_app1,))
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'a2.jpg', '2026-11-19T10:02:00Z', 'approved');", (cap_app2,))
        conn.execute("INSERT INTO captures (id, image_path, captured_at, status) VALUES (?, 'r.jpg', '2026-11-19T10:03:00Z', 'rejected');", (cap_rej,))

        # Add detections
        # Approved 1: 1 dry, 1 metal
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'dry', 0.9, 'dry', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_app1))
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 1, 'metal', 0.85, 'metal', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_app1))
        # Approved 2: 1 wet
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'wet', 0.95, 'wet', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_app2))
        # Pending capture: 1 dry
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'dry', 0.8, 'dry', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_pending))
        # Rejected capture: 1 metal
        conn.execute("INSERT INTO detections (id, capture_id, item_index, ai_class, ai_confidence, final_class, bbox_x, bbox_y, bbox_width, bbox_height) VALUES (?, ?, 0, 'metal', 0.7, 'metal', 0, 0, 10, 10);", (str(uuid.uuid4()), cap_rej))

    admin_stats = get_database_stats(conn)
    assert admin_stats["total_captures"] == 4
    assert admin_stats["total_detections"] == 5
    assert admin_stats["pending_captures"] == 1
    assert admin_stats["approved_captures"] == 2
    assert admin_stats["rejected_captures"] == 1

    public_stats = get_public_stats(conn)
    # Strictly approved items only
    assert public_stats["approved_captures"] == 2
    assert public_stats["approved_wet_detections"] == 1
    assert public_stats["approved_dry_detections"] == 1
    assert public_stats["approved_metal_detections"] == 1

    conn.close()


def test_stage2_create_capture_with_pending_detections(temp_db):
    """Verify new captures leave final_class as NULL until admin review."""
    det_list = [
        {"ai_class": "dry", "ai_confidence": 0.89, "bbox_x": 10, "bbox_y": 20, "bbox_width": 50, "bbox_height": 60},
        {"ai_class": "metal", "ai_confidence": 0.94, "bbox_x": 80, "bbox_y": 90, "bbox_width": 40, "bbox_height": 45},
    ]
    cap_id = create_capture_with_detections(
        image_path="data/images/raw/test1.jpg",
        prediction_image_path="data/images/predictions/test1.jpg",
        model_id="waste_models_yolo",
        detections=det_list,
        db_path=temp_db,
    )

    conn = get_connection(temp_db)
    cap_row = conn.execute("SELECT * FROM captures WHERE id = ?;", (cap_id,)).fetchone()
    assert cap_row["status"] == "pending"

    det_rows = conn.execute("SELECT * FROM detections WHERE capture_id = ? ORDER BY item_index;", (cap_id,)).fetchall()
    assert len(det_rows) == 2
    # Verify final_class is NULL initially
    assert det_rows[0]["final_class"] is None
    assert det_rows[1]["final_class"] is None
    conn.close()


def test_stage2_approve_and_reject_workflow(temp_db):
    """Verify approval populates final_class from ai_class and records audit event."""
    det_list = [{"ai_class": "metal", "ai_confidence": 0.91, "bbox_x": 0, "bbox_y": 0, "bbox_width": 10, "bbox_height": 10}]
    cap_id = create_capture_with_detections(
        image_path="test_app.jpg",
        prediction_image_path="test_app_p.jpg",
        model_id="waste_models_yolo",
        detections=det_list,
        db_path=temp_db,
    )

    # Approve
    approved_cap = approve_capture(cap_id, reviewer="lead_admin", note="Verified by admin", db_path=temp_db)
    assert approved_cap["status"] == "approved"
    assert approved_cap["reviewed_by"] == "lead_admin"
    assert approved_cap["detections"][0]["final_class"] == "metal"
    assert len(approved_cap["review_events"]) == 1
    assert approved_cap["review_events"][0]["action"] == "approved"

    # Reject another capture
    cap2_id = create_capture_with_detections("r.jpg", "r_p.jpg", "waste_models_yolo", db_path=temp_db)
    rejected_cap = reject_capture(cap2_id, reviewer="lead_admin", note="Empty chute", db_path=temp_db)
    assert rejected_cap["status"] == "rejected"
    assert len(rejected_cap["review_events"]) == 1
    assert rejected_cap["review_events"][0]["action"] == "rejected"


def test_stage2_list_public_captures_privacy(temp_db):
    """Verify list_public_captures only returns approved captures."""
    # 1 pending, 1 rejected, 1 approved
    cap_p = create_capture_with_detections("p.jpg", "p.jpg", "model", db_path=temp_db)
    cap_r = create_capture_with_detections("r.jpg", "r.jpg", "model", db_path=temp_db)
    reject_capture(cap_r, db_path=temp_db)
    cap_a = create_capture_with_detections("a.jpg", "a.jpg", "model", db_path=temp_db)
    approve_capture(cap_a, db_path=temp_db)

    public_items = list_public_captures(db_path=temp_db)
    assert len(public_items) == 1
    assert public_items[0]["id"] == cap_a
    assert public_items[0]["status"] == "approved"

