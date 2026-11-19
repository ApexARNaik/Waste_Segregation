import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union, Dict, Any, List
from app.config import settings

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY,
    image_path TEXT NOT NULL,
    prediction_image_path TEXT,
    source TEXT NOT NULL DEFAULT 'esp32_cam',
    model_id TEXT,
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
    reviewed_at TEXT,
    reviewed_by TEXT,
    admin_note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS detections (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    item_index INTEGER NOT NULL,
    ai_class TEXT NOT NULL CHECK(ai_class IN ('wet', 'dry', 'metal')),
    ai_confidence REAL NOT NULL CHECK(ai_confidence >= 0.0 AND ai_confidence <= 1.0),
    final_class TEXT CHECK(final_class IS NULL OR final_class IN ('wet', 'dry', 'metal')),
    bbox_x REAL NOT NULL,
    bbox_y REAL NOT NULL,
    bbox_width REAL NOT NULL,
    bbox_height REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS review_events (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    detection_id TEXT REFERENCES detections(id) ON DELETE SET NULL,
    action TEXT NOT NULL CHECK(action IN ('approved', 'rejected', 'classification_changed')),
    previous_class TEXT,
    new_class TEXT,
    reviewer TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_captures_status ON captures(status);
CREATE INDEX IF NOT EXISTS idx_captures_captured_at ON captures(captured_at);
CREATE INDEX IF NOT EXISTS idx_detections_capture_id ON detections(capture_id);
CREATE INDEX IF NOT EXISTS idx_review_events_capture_id ON review_events(capture_id);
"""


def get_connection(db_path: Optional[Union[Path, str]] = None) -> sqlite3.Connection:
    """Establish and configure a SQLite connection with foreign keys and WAL mode."""
    target_path = Path(db_path) if db_path else settings.DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Enforce foreign key constraints and WAL journaling mode
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    return conn


def migrate_db(conn: sqlite3.Connection) -> None:
    """Safe versioned migration to ensure final_class in detections is nullable for pending reviews."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(detections);")
    columns = cursor.fetchall()
    if not columns:
        return

    final_class_col = next((col for col in columns if col["name"] == "final_class"), None)
    # If final_class is NOT NULL, migrate it to allow NULL
    if final_class_col and final_class_col["notnull"] == 1:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("""
            CREATE TABLE detections_migration (
                id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                item_index INTEGER NOT NULL,
                ai_class TEXT NOT NULL CHECK(ai_class IN ('wet', 'dry', 'metal')),
                ai_confidence REAL NOT NULL CHECK(ai_confidence >= 0.0 AND ai_confidence <= 1.0),
                final_class TEXT CHECK(final_class IS NULL OR final_class IN ('wet', 'dry', 'metal')),
                bbox_x REAL NOT NULL,
                bbox_y REAL NOT NULL,
                bbox_width REAL NOT NULL,
                bbox_height REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
        """)
        conn.execute("""
            INSERT INTO detections_migration (
                id, capture_id, item_index, ai_class, ai_confidence, final_class,
                bbox_x, bbox_y, bbox_width, bbox_height, created_at
            )
            SELECT id, capture_id, item_index, ai_class, ai_confidence, final_class,
                   bbox_x, bbox_y, bbox_width, bbox_height, created_at
            FROM detections;
        """)
        conn.execute("DROP TABLE detections;")
        conn.execute("ALTER TABLE detections_migration RENAME TO detections;")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_detections_capture_id ON detections(capture_id);")
        conn.execute("PRAGMA foreign_keys = ON;")


def init_db(db_path: Optional[Union[Path, str]] = None) -> None:
    """Initialize database schema safely and apply migrations without losing existing records."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
            migrate_db(conn)
    finally:
        conn.close()


def create_capture_with_detections(
    image_path: str,
    prediction_image_path: Optional[str],
    model_id: Optional[str],
    captured_at: Optional[str] = None,
    source: str = "esp32_cam",
    detections: Optional[List[Dict[str, Any]]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> str:
    """Atomically record a new capture and its detections with pending status."""
    capture_id = str(uuid.uuid4())
    now_iso = captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO captures (id, image_path, prediction_image_path, source, model_id, captured_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending');
                """,
                (capture_id, image_path, prediction_image_path, source, model_id, now_iso),
            )

            if detections:
                for idx, det in enumerate(detections):
                    det_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO detections (
                            id, capture_id, item_index, ai_class, ai_confidence,
                            final_class, bbox_x, bbox_y, bbox_width, bbox_height
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            det_id,
                            capture_id,
                            idx,
                            det["ai_class"],
                            float(det["ai_confidence"]),
                            det.get("final_class", None),  # None until admin review
                            float(det["bbox_x"]),
                            float(det["bbox_y"]),
                            float(det["bbox_width"]),
                            float(det["bbox_height"]),
                        ),
                    )
        return capture_id
    finally:
        conn.close()


def approve_capture(
    capture_id: str,
    reviewer: str = "admin",
    note: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Approve a pending capture in a transaction.
    Sets final_class = ai_class for any detection where final_class was unset.
    Records audit entry in review_events.
    """
    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM captures WHERE id = ?;", (capture_id,))
            cap = cursor.fetchone()
            if not cap:
                raise KeyError(f"Capture {capture_id} not found.")

            # Update final_class for unreviewed detections
            cursor.execute(
                """
                UPDATE detections
                SET final_class = ai_class
                WHERE capture_id = ? AND final_class IS NULL;
                """,
                (capture_id,),
            )

            # Update capture status
            cursor.execute(
                """
                UPDATE captures
                SET status = 'approved', reviewed_at = ?, reviewed_by = ?, admin_note = ?
                WHERE id = ?;
                """,
                (now_iso, reviewer, note, capture_id),
            )

            # Record review event
            event_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO review_events (id, capture_id, action, reviewer, note)
                VALUES (?, ?, 'approved', ?, ?);
                """,
                (event_id, capture_id, reviewer, note),
            )

        return get_capture_by_id(capture_id, db_path=db_path)
    finally:
        conn.close()


def reject_capture(
    capture_id: str,
    reviewer: str = "admin",
    note: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Reject a capture in a transaction and log audit event.
    """
    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM captures WHERE id = ?;", (capture_id,))
            cap = cursor.fetchone()
            if not cap:
                raise KeyError(f"Capture {capture_id} not found.")

            cursor.execute(
                """
                UPDATE captures
                SET status = 'rejected', reviewed_at = ?, reviewed_by = ?, admin_note = ?
                WHERE id = ?;
                """,
                (now_iso, reviewer, note, capture_id),
            )

            event_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO review_events (id, capture_id, action, reviewer, note)
                VALUES (?, ?, 'rejected', ?, ?);
                """,
                (event_id, capture_id, reviewer, note),
            )

        return get_capture_by_id(capture_id, db_path=db_path)
    finally:
        conn.close()


def correct_detection_class(
    detection_id: str,
    new_class: str,
    reviewer: str = "admin",
    note: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """
    Update final_class for a detection and log audit trail in review_events.
    Preserves original ai_class and ai_confidence.
    """
    if new_class not in ("wet", "dry", "metal"):
        raise ValueError(f"Invalid class: {new_class}. Allowed: 'wet', 'dry', 'metal'")

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM detections WHERE id = ?;", (detection_id,))
            det = cursor.fetchone()
            if not det:
                raise KeyError(f"Detection {detection_id} not found.")

            prev_class = det["final_class"] if det["final_class"] is not None else det["ai_class"]
            capture_id = det["capture_id"]

            cursor.execute(
                """
                UPDATE detections
                SET final_class = ?
                WHERE id = ?;
                """,
                (new_class, detection_id),
            )

            event_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO review_events (
                    id, capture_id, detection_id, action, previous_class, new_class, reviewer, note
                )
                VALUES (?, ?, ?, 'classification_changed', ?, ?, ?, ?);
                """,
                (event_id, capture_id, detection_id, prev_class, new_class, reviewer, note),
            )

            cursor.execute("SELECT * FROM detections WHERE id = ?;", (detection_id,))
            updated_det = dict(cursor.fetchone())
            return updated_det
    finally:
        conn.close()


def get_capture_by_id(
    capture_id: str,
    db_path: Optional[Union[Path, str]] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve full capture details with detections and review events."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM captures WHERE id = ?;", (capture_id,))
        cap_row = cursor.fetchone()
        if not cap_row:
            return None

        cap_dict = dict(cap_row)

        cursor.execute("SELECT * FROM detections WHERE capture_id = ? ORDER BY item_index;", (capture_id,))
        cap_dict["detections"] = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM review_events WHERE capture_id = ? ORDER BY created_at DESC;", (capture_id,))
        cap_dict["review_events"] = [dict(r) for r in cursor.fetchall()]

        return cap_dict
    finally:
        conn.close()


def list_captures(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """Query captures with optional status filter and their associated detections."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                """
                SELECT * FROM captures
                WHERE status = ?
                ORDER BY captured_at DESC
                LIMIT ? OFFSET ?;
                """,
                (status, limit, offset),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM captures
                ORDER BY captured_at DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )

        captures = [dict(r) for r in cursor.fetchall()]
        for cap in captures:
            cursor.execute(
                "SELECT * FROM detections WHERE capture_id = ? ORDER BY item_index;",
                (cap["id"],)
            )
            cap["detections"] = [dict(d) for d in cursor.fetchall()]

        return captures
    finally:
        conn.close()


def list_public_captures(
    limit: int = 20,
    offset: int = 0,
    db_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Query strictly approved captures for public viewing.
    Pending and rejected captures are completely omitted.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, captured_at, status, model_id, created_at
            FROM captures
            WHERE status = 'approved'
            ORDER BY captured_at DESC
            LIMIT ? OFFSET ?;
            """,
            (limit, offset),
        )
        approved_caps = [dict(r) for r in cursor.fetchall()]

        for cap in approved_caps:
            cursor.execute(
                """
                SELECT id, item_index, final_class, ai_confidence, bbox_x, bbox_y, bbox_width, bbox_height
                FROM detections
                WHERE capture_id = ?
                ORDER BY item_index;
                """,
                (cap["id"],),
            )
            cap["detections"] = [dict(d) for d in cursor.fetchall()]
            cap["image_url"] = f"/api/public/images/{cap['id']}"

        return approved_caps
    finally:
        conn.close()


def get_database_stats(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[Union[Path, str]] = None
) -> Dict[str, Any]:
    """Calculate internal admin database metrics."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM captures;")
        total_captures = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM detections;")
        total_detections = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM captures WHERE status = 'pending';")
        pending_captures = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM captures WHERE status = 'approved';")
        approved_captures = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM captures WHERE status = 'rejected';")
        rejected_captures = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT final_class, COUNT(*) as cnt
            FROM detections
            WHERE final_class IS NOT NULL
            GROUP BY final_class;
            """
        )
        class_counts = {"wet": 0, "dry": 0, "metal": 0}
        for row in cursor.fetchall():
            cls = row["final_class"]
            if cls in class_counts:
                class_counts[cls] = row["cnt"]

        return {
            "total_captures": total_captures,
            "total_detections": total_detections,
            "pending_captures": pending_captures,
            "approved_captures": approved_captures,
            "rejected_captures": rejected_captures,
            "dry_detections": class_counts["dry"],
            "metal_detections": class_counts["metal"],
            "wet_detections": class_counts["wet"],
        }
    finally:
        if should_close:
            conn.close()


def get_public_stats(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[Union[Path, str]] = None
) -> Dict[str, int]:
    """Calculate public waste segregation statistics (strictly approved items only)."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    try:
        cursor = conn.cursor()

        # Approved captures
        cursor.execute("SELECT COUNT(*) FROM captures WHERE status = 'approved';")
        approved_captures = cursor.fetchone()[0]

        # Approved detections broken down by final_class
        cursor.execute(
            """
            SELECT d.final_class, COUNT(*) as cnt
            FROM detections d
            JOIN captures c ON d.capture_id = c.id
            WHERE c.status = 'approved'
            GROUP BY d.final_class;
            """
        )
        counts = {"wet": 0, "dry": 0, "metal": 0}
        for row in cursor.fetchall():
            cls = row["final_class"]
            if cls in counts:
                counts[cls] = row["cnt"]

        return {
            "approved_captures": approved_captures,
            "approved_wet_detections": counts["wet"],
            "approved_dry_detections": counts["dry"],
            "approved_metal_detections": counts["metal"],
        }
    finally:
        if should_close:
            conn.close()
