"""Application settings, read from environment variables."""
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")


def _origins_from_env() -> tuple[str, ...]:
    raw = os.getenv("LMS_CORS_ORIGINS", "")
    parsed = tuple(o.strip() for o in raw.split(",") if o.strip())
    return parsed or DEFAULT_ORIGINS


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("LMS_DB_PATH", BASE_DIR / "library.db")))
    frontend_dir: Path = BASE_DIR.parent / "frontend"
    api_key: str = field(default_factory=lambda: os.getenv("LMS_API_KEY", ""))
    cors_origins: tuple[str, ...] = field(default_factory=_origins_from_env)
    loan_days: int = 14
