import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

# -----------------------
# Configuration (env-driven)
# -----------------------
DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent / "library.db")))
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(Path(__file__).parent.parent / "frontend")))

# Comma-separated list; do not default to "*" to keep CORS restricted by default.
_ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _ALLOWED_ORIGINS_RAW.split(",") if o.strip()]

API_KEY = os.getenv("API_KEY", "")  # if empty -> write endpoints are effectively disabled unless caller sends empty key

SQLITE_TIMEOUT = float(os.getenv("SQLITE_TIMEOUT", "5.0"))
SQLITE_MAX_RETRIES = int(os.getenv("SQLITE_MAX_RETRIES", "5"))
SQLITE_RETRY_BACKOFF_SECONDS = float(os.getenv("SQLITE_RETRY_BACKOFF_SECONDS", "0.2"))

# -----------------------
# Logging (structured key=value)
# -----------------------
logger = logging.getLogger("app")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log_kv(event: str, **fields: Any) -> None:
    # Simple JSON-ish key/value logging (stable keys, easy to grep)
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, separators=(",", ":"), default=str))


app = FastAPI(title="Library Management System")

# Restrict CORS: configured origins; only needed methods/headers.
# Note: allowing OPTIONS for preflight; Content-Type for JSON; X-API-Key for auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def _configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    # Safer SQLite defaults for a small web app
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    # busy_timeout in ms; complements sqlite3.connect(timeout=...)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_TIMEOUT * 1000)}")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT)
    _configure_sqlite_connection(conn)
    return conn


def _is_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in str(exc).lower() or "database is busy" in str(exc).lower()
    )


def execute_write_with_retry(
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    max_retries: int = SQLITE_MAX_RETRIES,
    backoff_seconds: float = SQLITE_RETRY_BACKOFF_SECONDS,
) -> sqlite3.Cursor:
    # Retry on locked/busy DB errors for write operations only.
    for attempt in range(max_retries + 1):
        try:
            with get_db() as conn:
                return conn.execute(sql, params)
        except BaseException as exc:
            if _is_locked_error(exc) and attempt < max_retries:
                sleep_s = backoff_seconds * (2**attempt)
                log_kv("sqlite_retry", attempt=attempt + 1, max_retries=max_retries, sleep_seconds=sleep_s)
                time.sleep(sleep_s)
                continue
            raise


def init_db():
    with get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                available INTEGER NOT NULL DEFAULT 1
            )"""
        )


init_db()


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=200)

    @field_validator("title", "author")
    @classmethod
    def strip_and_validate(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class BookOut(BaseModel):
    id: int
    title: str
    author: str
    available: bool


class OkResponse(BaseModel):
    ok: bool = True


# TODO: members, due dates, search, edit book, auth


def require_api_key(request: Request) -> None:
    expected = API_KEY
    provided = request.headers.get("X-API-Key", "")
    if not expected or provided != expected:
        # Do not leak whether an API key is configured; generic 401
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    start = time.perf_counter()
    try:
        response: Response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_kv(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_host=(request.client.host if request.client else None),
        )
        return response
    except HTTPException as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_kv(
            "http_exception",
            method=request.method,
            path=request.url.path,
            status_code=exc.status_code,
            detail=exc.detail,
            duration_ms=duration_ms,
            client_host=(request.client.host if request.client else None),
        )
        raise
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log_kv(
            "unhandled_exception",
            method=request.method,
            path=request.url.path,
            status_code=500,
            error=str(exc),
            duration_ms=duration_ms,
            client_host=(request.client.host if request.client else None),
        )
        raise


@app.get("/api/books", response_model=list[BookOut])
def list_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    return [
        BookOut(id=r["id"], title=r["title"], author=r["author"], available=bool(r["available"]))
        for r in rows
    ]


@app.post("/api/books", status_code=201, response_model=BookOut, dependencies=[Depends(require_api_key)])
def add_book(book: BookIn):
    cur = execute_write_with_retry("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
    return BookOut(id=cur.lastrowid, title=book.title, author=book.author, available=True)


@app.post(
    "/api/books/{book_id}/toggle",
    response_model=OkResponse,
    dependencies=[Depends(require_api_key)],
)
def toggle_availability(book_id: int):
    cur = execute_write_with_retry("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return OkResponse(ok=True)


@app.delete(
    "/api/books/{book_id}",
    response_model=OkResponse,
    dependencies=[Depends(require_api_key)],
)
def delete_book(book_id: int):
    cur = execute_write_with_retry("DELETE FROM books WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return OkResponse(ok=True)


@app.get("/health", response_model=OkResponse)
def health() -> OkResponse:
    # Basic dependency check: can we connect and run a trivial query?
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        log_kv("health_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Unhealthy")
    return OkResponse(ok=True)


# Serve the frontend at / (mounted last so /api routes take priority)
if FRONTEND_DIR.exists() and FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    log_kv("frontend_missing", frontend_dir=str(FRONTEND_DIR))
