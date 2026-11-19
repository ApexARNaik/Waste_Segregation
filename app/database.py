import sqlite3
from pathlib import Path
from typing import Optional, Union, Dict, Any
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
    final_class TEXT NOT NULL CHECK(final_class IN ('wet', 'dry', 'metal')),
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


def init_db(db_path: Optional[Union[Path, str]] = None) -> None:
    """Initialize database schema safely without modifying or deleting existing records."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
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

        return {
            "total_captures": total_captures,
            "total_detections": total_detections,
            "pending_captures": pending_captures,
            "approved_captures": approved_captures,
            "rejected_captures": rejected_captures,
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
