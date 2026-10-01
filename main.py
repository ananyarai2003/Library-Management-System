import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

API_KEY = os.getenv("API_KEY")

_SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "3000"))
SQLITE_BUSY_TIMEOUT_SEC = max(_SQLITE_BUSY_TIMEOUT_MS, 0) / 1000.0

DEFAULT_CORS_ORIGINS = [
    "http://localhost",
    "http://localhost:3000",
    "http://127.0.0.1",
    "http://127.0.0.1:3000",
]
_raw_cors = (os.getenv("CORS_ORIGINS") or "").strip()
if _raw_cors:
    CORS_ORIGINS = [o.strip() for o in _raw_cors.split(",") if o.strip()]
else:
    CORS_ORIGINS = DEFAULT_CORS_ORIGINS

logger = logging.getLogger("library")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="Library Management System")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    start = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000.0

    response.headers["X-Request-ID"] = request_id
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            }
        )
    )
    return response


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_BUSY_TIMEOUT_SEC, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS};")
    return conn


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
        conn.commit()


init_db()


def _get_request_id(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    return getattr(request.state, "request_id", None)


def execute_db(func, request: Optional[Request] = None):
    """
    Execute DB operation with consistent error handling.
    Maps SQLITE 'database is locked' to 503 to support transient contention.
    """
    try:
        return func()
    except sqlite3.OperationalError as e:
        request_id = _get_request_id(request)
        logger.exception(
            json.dumps(
                {
                    "event": "db_operational_error",
                    "request_id": request_id,
                    "error": str(e),
                }
            )
        )
        if "locked" in str(e).lower():
            raise HTTPException(503, "Database is busy, please retry") from e
        raise HTTPException(500, "Database error") from e


class BookIn(BaseModel):
    title: str
    author: str


class AvailabilityPatch(BaseModel):
    available: bool


def require_api_key(request: Request):
    incoming = request.headers.get("X-API-Key")
    if not API_KEY:
        raise HTTPException(503, "API key auth not configured")
    if not incoming or incoming != API_KEY:
        raise HTTPException(401, "Unauthorized")


# TODO: members, due dates, search, edit book, auth


@app.get("/api/books")
def list_books(request: Request):
    def op():
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
        return [dict(r) | {"available": bool(r["available"])} for r in rows]

    return execute_db(op, request=request)


@app.post("/api/books", status_code=201, dependencies=[Depends(require_api_key)])
def add_book(book: BookIn, request: Request):
    def op():
        with get_db() as conn:
            cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
            conn.commit()
            return {"id": cur.lastrowid, **book.model_dump(), "available": True}

    return execute_db(op, request=request)


@app.patch("/api/books/{book_id}", dependencies=[Depends(require_api_key)])
def patch_availability(book_id: int, patch: AvailabilityPatch, request: Request):
    def op():
        with get_db() as conn:
            cur = conn.execute(
                "UPDATE books SET available = ? WHERE id = ?",
                (1 if patch.available else 0, book_id),
            )
            conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        return {"ok": True}

    return execute_db(op, request=request)


@app.post("/api/books/{book_id}/toggle", deprecated=True, dependencies=[Depends(require_api_key)])
def toggle_availability(book_id: int, request: Request):
    def op():
        with get_db() as conn:
            row = conn.execute("SELECT available FROM books WHERE id = ?", (book_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Book not found")

            new_available = 0 if int(row["available"]) == 1 else 1
            cur = conn.execute(
                "UPDATE books SET available = ? WHERE id = ?",
                (new_available, book_id),
            )
            conn.commit()

        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        return {"ok": True}

    return execute_db(op, request=request)


@app.delete("/api/books/{book_id}", dependencies=[Depends(require_api_key)])
def delete_book(book_id: int, request: Request):
    def op():
        with get_db() as conn:
            cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
            conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
        return {"ok": True}

    return execute_db(op, request=request)


@app.get("/health")
def health(request: Request):
    def op():
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True}

    return execute_db(op, request=request)


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
