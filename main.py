import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# -----------------------------------------------------------------------------
# Configuration (README-like)
#
# API Key auth (write operations only):
#   - Set env var API_KEY to a shared secret value.
#   - Clients must send header: X-API-Key: <API_KEY>
#   - Read endpoints remain public.
#
# CORS:
#   - Set env var ALLOWED_ORIGINS to a comma-separated list of allowed origins.
#     Example: "http://localhost:8000,http://127.0.0.1:8000"
#   - Defaults to local dev origins if not set.
# -----------------------------------------------------------------------------
DEFAULT_ALLOWED_ORIGINS = ("http://localhost:8000", "http://127.0.0.1:8000")


def _get_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    if not raw.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="Library Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """
    Context-managed DB connection with basic hardening.
    - timeout to wait for locks
    - WAL for better concurrency
    - busy_timeout to reduce "database is locked" errors
    """
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        # Apply pragmas for each connection to keep behavior consistent.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    finally:
        conn.close()


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
    title: str = Field(..., max_length=200)
    author: str = Field(..., max_length=200)

    @field_validator("title", "author", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("title", "author")
    @classmethod
    def not_blank(cls, v: str):
        if not isinstance(v, str) or len(v) < 1:
            raise ValueError("must not be blank")
        return v


# TODO: members, due dates, search, edit book


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    expected = os.getenv("API_KEY", "")
    if not expected:
        # Fail closed: if API_KEY is not configured, do not allow writes.
        raise HTTPException(500, "API_KEY is not configured on the server")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "Invalid API key")


def _with_write_retry(fn, *, retries: int = 3, base_sleep_s: float = 0.05):
    """
    Retry small transient lock errors on SQLite writes.
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "database is locked" in msg or "database is busy" in msg:
                if attempt >= retries:
                    raise
                time.sleep(base_sleep_s * (2**attempt))
                continue
            raise


def _set_book_availability(book_id: int, available: bool) -> None:
    def _op():
        with get_db() as conn:
            cur = conn.execute(
                "UPDATE books SET available = ? WHERE id = ?",
                (1 if available else 0, book_id),
            )
        return cur

    cur = _with_write_retry(_op)
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")


@app.get("/api/books")
def list_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    return [dict(r) | {"available": bool(r["available"])} for r in rows]


@app.post("/api/books", status_code=201, dependencies=[Depends(require_api_key)])
def add_book(book: BookIn):
    def _op():
        with get_db() as conn:
            return conn.execute(
                "INSERT INTO books (title, author) VALUES (?, ?)",
                (book.title, book.author),
            )

    cur = _with_write_retry(_op)
    return {"id": cur.lastrowid, **book.model_dump(), "available": True}


class BookAvailabilityPatch(BaseModel):
    available: bool


@app.patch("/api/books/{book_id}", dependencies=[Depends(require_api_key)])
def set_availability(book_id: int, patch: BookAvailabilityPatch):
    _set_book_availability(book_id, patch.available)
    return {"ok": True}


@app.post("/api/books/{book_id}/toggle", dependencies=[Depends(require_api_key)])
def toggle_availability(book_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT available FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Book not found")

    _set_book_availability(book_id, not bool(row["available"]))
    return {"ok": True}


@app.delete("/api/books/{book_id}", dependencies=[Depends(require_api_key)])
def delete_book(book_id: int):
    def _op():
        with get_db() as conn:
            return conn.execute("DELETE FROM books WHERE id = ?", (book_id,))

    cur = _with_write_retry(_op)
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
