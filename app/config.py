import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Root directory of the WasteSegregation project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env if present
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)


class Settings:
    """Application settings and environment validation."""

    def __init__(self, env_path: Optional[Path] = None):
        if env_path and env_path.exists():
            load_dotenv(dotenv_path=env_path, override=True)

        self.BASE_DIR = BASE_DIR
        self.ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
        self.SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()

        raw_db_path = os.getenv("DB_PATH", "data/waste_segregation.db").strip()
        self.DB_PATH = (BASE_DIR / raw_db_path).resolve() if not Path(raw_db_path).is_absolute() else Path(raw_db_path)

        try:
            self.SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "28800"))
        except ValueError:
            self.SESSION_TTL_SECONDS = 28800

        self.CAMERA_PORT = os.getenv("CAMERA_PORT", "COM9").strip()
        try:
            self.CAMERA_BAUD = int(os.getenv("CAMERA_BAUD", "921600"))
        except ValueError:
            self.CAMERA_BAUD = 921600

        self.CAMERA_TRIGGER = os.getenv("CAMERA_TRIGGER", "ir").strip()
        try:
            self.CAPTURE_INTERVAL_SECONDS = int(os.getenv("CAPTURE_INTERVAL_SECONDS", "5"))
        except ValueError:
            self.CAPTURE_INTERVAL_SECONDS = 5

        self.RAW_IMAGES_DIR = self.BASE_DIR / "data" / "images" / "raw"
        self.PREDICTIONS_IMAGES_DIR = self.BASE_DIR / "data" / "images" / "predictions"

    def ensure_directories(self) -> None:
        """Create required directories if they do not exist."""
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.RAW_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        self.PREDICTIONS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        """Validate required configuration and fail fast if missing or insecure."""
        if not self.ADMIN_PASSWORD:
            raise ValueError(
                "CRITICAL CONFIG ERROR: ADMIN_PASSWORD is empty or not set in .env."
            )

        insecure_placeholders = {
            "YOUR_ADMIN_PASSWORD",
            "admin",
            "password",
            "123456",
            "change_this",
            "change_this_to_a_secure_admin_password",
        }
        if self.ADMIN_PASSWORD in insecure_placeholders or len(self.ADMIN_PASSWORD) < 8:
            raise ValueError(
                "CRITICAL CONFIG ERROR: ADMIN_PASSWORD is insecure or using placeholder default. "
                "Must be at least 8 characters and not a trivial default."
            )

        if not self.SESSION_SECRET:
            raise ValueError(
                "CRITICAL CONFIG ERROR: SESSION_SECRET is empty or not set in .env."
            )

        insecure_secrets = {
            "YOUR_GENERATED_SECRET",
            "secret",
            "generate_a_random_secret_e.g_python_secrets_token_hex_32",
        }
        if self.SESSION_SECRET in insecure_secrets or len(self.SESSION_SECRET) < 16:
            raise ValueError(
                "CRITICAL CONFIG ERROR: SESSION_SECRET is too short or using placeholder default. "
                "Must be at least 16 characters."
            )

        self.ensure_directories()


settings = Settings()
