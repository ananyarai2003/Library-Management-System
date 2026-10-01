import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DEFAULT_DB_PATH = Path(__file__).parent / "library.db"
DB_PATH = Path(os.getenv("LIBRARY_DB_PATH") or DEFAULT_DB_PATH)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

logger = logging.getLogger("library")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

app = FastAPI(title="Library Management System")


def _parse_allowed_origins(raw: Optional[str]) -> Optional[list[str]]:
    """
    Returns:
      - None => do not install CORS middleware
      - []   => install CORS but allow nothing (not used)
      - list => allowlist
    """
    if raw is None:
        # Default allowlist for local dev
        raw = "http://localhost:3000,http://localhost:5173"
    raw = raw.strip()
    if raw == "":
        return None
    return [o.strip() for o in raw.split(",") if o.strip()]


_allowed_origins = _parse_allowed_origins(os.getenv("ALLOWED_ORIGINS"))
if _allowed_origins is not None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS enabled for origins: %s", _allowed_origins)
else:
    logger.info("CORS disabled (ALLOWED_ORIGINS empty)")


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Reliability pragmas
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")  # milliseconds
    return conn


def get_db():
    # Kept as a small wrapper to preserve call sites
    return _db_connect()


def _with_db_retry(fn, *, retries: int = 5, base_sleep: float = 0.05):
    for attempt in range(retries + 1):
        try:
            with get_db() as conn:
                return fn(conn)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "database is locked" in msg and attempt < retries:
                sleep_for = base_sleep * (2**attempt)
                logger.warning("SQLite locked, retrying in %.2fs (attempt %d/%d)", sleep_for, attempt + 1, retries)
                time.sleep(sleep_for)
                continue
            raise


def init_db():
    def _init(conn: sqlite3.Connection):
        conn.execute(
            """CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                available INTEGER NOT NULL DEFAULT 1
            )"""
        )

    _with_db_retry(_init)


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("DB initialized at %s", DB_PATH)


def _normalize_whitespace(s: str) -> str:
    return " ".join(s.split()).strip()


class BookIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(..., min_length=1, max_length=200)

    @field_validator("title", "author", mode="before")
    @classmethod
    def _normalize(cls, v):
        if v is None:
            return v
        if not isinstance(v, str):
            return v
        return _normalize_whitespace(v)


class BookUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    author: Optional[str] = Field(default=None, min_length=1, max_length=200)
    available: Optional[bool] = None

    @field_validator("title", "author", mode="before")
    @classmethod
    def _normalize(cls, v):
        if v is None:
            return v
        if not isinstance(v, str):
            return v
        return _normalize_whitespace(v)


def _require_api_key(request: Request, x_api_key: Optional[str]):
    configured = os.getenv("LIBRARY_API_KEY")
    if not configured:
        # Dev mode: allow but warn
        logger.warning("LIBRARY_API_KEY not set; write operations are not authenticated (dev mode).")
        return
    if not x_api_key or x_api_key != configured:
        logger.info("Unauthorized write attempt from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Unauthorized")


# TODO: members, due dates, search, auth


@app.get("/api/books")
def list_books(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, min_length=1, max_length=200),
    available: Optional[bool] = Query(None),
):
    q_norm = _normalize_whitespace(q) if isinstance(q, str) else None

    def _run(conn: sqlite3.Connection):
        where = []
        params: list[object] = []

        if q_norm:
            where.append("(title LIKE ? OR author LIKE ?)")
            like = f"%{q_norm}%"
            params.extend([like, like])

        if available is not None:
            where.append("available = ?")
            params.append(1 if available else 0)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM books {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) | {"available": bool(r["available"])} for r in rows]

    return _with_db_retry(_run)


@app.post("/api/books", status_code=201)
def add_book(
    request: Request,
    book: BookIn,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(request, x_api_key)

    def _run(conn: sqlite3.Connection):
        cur = conn.execute(
            "INSERT INTO books (title, author) VALUES (?, ?)",
            (book.title, book.author),
        )
        return {"id": cur.lastrowid, **book.model_dump(), "available": True}

    return _with_db_retry(_run)


@app.post("/api/books/{book_id}/toggle")
def toggle_availability(
    request: Request,
    book_id: int,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(request, x_api_key)

    def _run(conn: sqlite3.Connection):
        cur = conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        return {"ok": True}

    return _with_db_retry(_run)


@app.put("/api/books/{book_id}")
def update_book(
    request: Request,
    book_id: int,
    patch: BookUpdate,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(request, x_api_key)

    def _run(conn: sqlite3.Connection):
        updates = []
        params: list[object] = []

        if patch.title is not None:
            updates.append("title = ?")
            params.append(patch.title)
        if patch.author is not None:
            updates.append("author = ?")
            params.append(patch.author)
        if patch.available is not None:
            updates.append("available = ?")
            params.append(1 if patch.available else 0)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        params.append(book_id)
        sql = f"UPDATE books SET {', '.join(updates)} WHERE id = ?"
        cur = conn.execute(sql, params)
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")

        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return dict(row) | {"available": bool(row["available"])}

    return _with_db_retry(_run)


@app.delete("/api/books/{book_id}")
def delete_book(
    request: Request,
    book_id: int,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    _require_api_key(request, x_api_key)

    def _run(conn: sqlite3.Connection):
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        return {"ok": True}

    return _with_db_retry(_run)


@app.get("/healthz")
def healthz():
    def _run(conn: sqlite3.Connection):
        conn.execute("SELECT 1").fetchone()
        return {"ok": True}

    return _with_db_retry(_run)


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
