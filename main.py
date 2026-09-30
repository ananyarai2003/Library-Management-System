import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent / "library.db")))
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(Path(__file__).parent.parent / "frontend")))
API_KEY = os.getenv("API_KEY", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "").strip()

logger = logging.getLogger("library")
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(message)s")

app = FastAPI(title="Library Management System")

if _CORS_ORIGINS_RAW:
    cors_origins = [o.strip() for o in _CORS_ORIGINS_RAW.split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )


def _log(event: str, **fields):
    payload = {"event": event, **{k: v for k, v in fields.items() if v is not None}}
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))


def get_db():
    # FastAPI may serve requests across threads; allow sqlite connection usage safely per request.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Avoid immediate "database is locked" failures under light concurrency.
    conn.execute("PRAGMA busy_timeout = 3000")
    return conn


def init_db():
    try:
        with get_db() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1
                )"""
            )
    except sqlite3.OperationalError as e:
        _log("db_init_failed", error=str(e), db_path=str(DB_PATH))
        raise


init_db()


class BookIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(..., min_length=1, max_length=200)

    @field_validator("title", "author")
    @classmethod
    def _strip_and_validate(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


# TODO: members, due dates, search, edit book


def _get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-Id") or str(uuid.uuid4())


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    status_code: Optional[int] = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        _log(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            latency_ms=latency_ms,
        )


def _require_api_key(request: Request) -> None:
    # If API_KEY env var is unset/empty, treat as misconfiguration for write operations.
    if not API_KEY:
        raise HTTPException(status_code=503, detail="API key auth is not configured")
    provided = request.headers.get("X-API-Key", "")
    if provided != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _map_sqlite_error(e: Exception) -> HTTPException:
    if isinstance(e, sqlite3.IntegrityError):
        return HTTPException(status_code=409, detail="Conflict")
    if isinstance(e, sqlite3.OperationalError):
        return HTTPException(status_code=503, detail="Database unavailable")
    return HTTPException(status_code=500, detail="Internal server error")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True}
    except sqlite3.OperationalError as e:
        _log("db_ready_failed", error=str(e), db_path=str(DB_PATH))
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/api/books")
def list_books(request: Request):
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
        return [dict(r) | {"available": bool(r["available"])} for r in rows]
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        _log("books_list_failed", request_id=_get_request_id(request), error=str(e))
        raise _map_sqlite_error(e)


@app.post("/api/books", status_code=201)
def add_book(book: BookIn, request: Request):
    _require_api_key(request)
    try:
        with get_db() as conn:
            cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
        _log("book_added", request_id=_get_request_id(request), book_id=cur.lastrowid)
        return {"id": cur.lastrowid, **book.model_dump(), "available": True}
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        _log("book_add_failed", request_id=_get_request_id(request), error=str(e))
        raise _map_sqlite_error(e)


@app.post("/api/books/{book_id}/toggle")
def toggle_availability(book_id: int, request: Request):
    _require_api_key(request)
    try:
        with get_db() as conn:
            cur = conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Book not found")
            row = conn.execute("SELECT id, available FROM books WHERE id = ?", (book_id,)).fetchone()
        available = bool(row["available"]) if row else None
        _log("book_toggled", request_id=_get_request_id(request), book_id=book_id, available=available)
        return {"ok": True, "id": book_id, "available": available}
    except HTTPException:
        raise
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        _log("book_toggle_failed", request_id=_get_request_id(request), book_id=book_id, error=str(e))
        raise _map_sqlite_error(e)


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, request: Request):
    _require_api_key(request)
    try:
        with get_db() as conn:
            cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        _log("book_deleted", request_id=_get_request_id(request), book_id=book_id)
        return {"ok": True}
    except HTTPException:
        raise
    except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
        _log("book_delete_failed", request_id=_get_request_id(request), book_id=book_id, error=str(e))
        raise _map_sqlite_error(e)


# Serve the frontend at / (mounted last so /api routes take priority)
# If the directory doesn't exist (e.g., API-only deploy), do not crash startup.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    _log("frontend_dir_missing", directory=str(FRONTEND_DIR))
