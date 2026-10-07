"""Environment-driven settings, read at call time so tests can override them."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


def db_path() -> Path:
    return Path(os.environ.get("LMS_DB_PATH", BASE_DIR / "library.db"))


def db_timeout_seconds() -> float:
    return float(os.environ.get("LMS_DB_TIMEOUT", "5"))


def api_key() -> str:
    return os.environ.get("LMS_API_KEY", "")


def cors_origins() -> list[str]:
    raw = os.environ.get("LMS_CORS_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def loan_days() -> int:
    return int(os.environ.get("LMS_LOAN_DAYS", "14"))


def log_level() -> str:
    return os.environ.get("LMS_LOG_LEVEL", "INFO").upper()
