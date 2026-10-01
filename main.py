import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

logger = logging.getLogger(__name__)

app = FastAPI(title="Library Management System")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    """Apply safe, low-risk SQLite settings without changing external behavior."""
    try:
        # Wait up to 3s for locks to clear before raising "database is locked".
        conn.execute("PRAGMA busy_timeout = 3000")
    except sqlite3.Error:
        # PRAGMA failures shouldn't take down the service; just log and continue.
        logger.exception("Failed to apply SQLite pragmas")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _configure_sqlite_connection(conn)
    return conn


def _sqlite_error_to_http(exc: sqlite3.Error) -> HTTPException:
    # Translate lock contention to a retryable status.
    if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
        return HTTPException(
            status_code=503,
            detail="Database is busy (locked). Please retry in a moment.",
        )
    return HTTPException(status_code=500, detail="Database error")


@contextmanager
def db_read():
    """Read-only DB context. Uses connection context for automatic close."""
    with get_db() as conn:
        yield conn


@contextmanager
def db_write():
    """
    Write DB context with explicit commit/rollback and standardized sqlite error handling.
    Rolls back on sqlite3.Error and raises an HTTPException.
    """
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            logger.exception("Rollback failed after SQLite error")
        logger.exception("SQLite write operation failed")
        raise _sqlite_error_to_http(exc) from exc
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
    title: str
    author: str


# TODO: members, due dates, search, edit book, auth


@app.get("/api/books")
def list_books():
    with db_read() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    return [dict(r) | {"available": bool(r["available"])} for r in rows]


@app.post("/api/books", status_code=201)
def add_book(book: BookIn):
    with db_write() as conn:
        cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
    return {"id": cur.lastrowid, **book.model_dump(), "available": True}


@app.post("/api/books/{book_id}/toggle")
def toggle_availability(book_id: int):
    with db_write() as conn:
        cur = conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int):
    with db_write() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Book not found")
    return {"ok": True}


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
