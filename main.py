import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Configuration (env vars)
# - LIBRARY_ADMIN_API_KEY: API key required for mutating endpoints (POST/DELETE/toggle).
#   If not set, mutating requests are denied (fail-closed) with 503 to avoid accidental open deployments.
# - LIBRARY_CORS_ORIGINS: Comma-separated list of allowed CORS origins. If not set, defaults to no CORS origins.
ADMIN_API_KEY = os.getenv("LIBRARY_ADMIN_API_KEY")
CORS_ORIGINS_RAW = os.getenv("LIBRARY_CORS_ORIGINS", "")

def _parse_cors_origins(raw: str) -> List[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]

CORS_ORIGINS = _parse_cors_origins(CORS_ORIGINS_RAW)

app = FastAPI(title="Library Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_admin(api_key: Optional[str] = Depends(api_key_header)) -> None:
    """
    Dependency that enforces admin API key auth for mutating endpoints.

    Uses header: X-API-Key
    Config env var: LIBRARY_ADMIN_API_KEY

    Fail-closed behavior:
    - If LIBRARY_ADMIN_API_KEY is not set: deny with 503.
    - If provided key doesn't match: deny with 401.
    """
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail={"error": {"code": "auth_not_configured", "message": "Service not configured"}})
    if not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail={"error": {"code": "unauthorized", "message": "Unauthorized"}})


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.exception_handler(sqlite3.Error)
def sqlite_error_handler(_request: Request, _exc: sqlite3.Error):
    # Avoid leaking stack traces/details; return a consistent, generic error body.
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "db_error", "message": "Database error"}},
    )


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
    title: str
    author: str


# TODO: members, due dates, search, edit book, auth


@app.get("/api/books")
def list_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    return [dict(r) | {"available": bool(r["available"])} for r in rows]


@app.post("/api/books", status_code=201, dependencies=[Depends(require_admin)])
def add_book(book: BookIn):
    with get_db() as conn:
        cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
    return {"id": cur.lastrowid, **book.model_dump(), "available": True}


@app.post("/api/books/{book_id}/toggle", dependencies=[Depends(require_admin)])
def toggle_availability(book_id: int):
    with get_db() as conn:
        cur = conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.delete("/api/books/{book_id}", dependencies=[Depends(require_admin)])
def delete_book(book_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
