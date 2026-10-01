import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Configuration (via environment variables):
# - API_KEY: required for write endpoints (POST/DELETE). Sent by client as header "X-API-Key".
# - ALLOWED_ORIGINS: comma-separated list of allowed CORS origins (e.g. "http://localhost:5173,https://example.com").
API_KEY_ENV_VAR = "API_KEY"
ALLOWED_ORIGINS_ENV_VAR = "ALLOWED_ORIGINS"
API_KEY_HEADER_NAME = "X-API-Key"

def _parse_allowed_origins(value: str | None) -> list[str]:
    if not value:
        return []
    return [o.strip() for o in value.split(",") if o.strip()]

ALLOWED_ORIGINS = _parse_allowed_origins(os.getenv(ALLOWED_ORIGINS_ENV_VAR))

app = FastAPI(title="Library Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", API_KEY_HEADER_NAME],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


init_db()


def require_api_key(x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER_NAME)) -> None:
    expected = os.getenv(API_KEY_ENV_VAR)
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


class BookIn(BaseModel):
    title: str
    author: str


# TODO: members, due dates, search, edit book, auth


@app.get("/api/books")
def list_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    return [dict(r) | {"available": bool(r["available"])} for r in rows]


@app.post("/api/books", status_code=201)
def add_book(book: BookIn, _: None = require_api_key):
    with get_db() as conn:
        cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
    return {"id": cur.lastrowid, **book.model_dump(), "available": True}


@app.post("/api/books/{book_id}/toggle")
def toggle_availability(book_id: int, _: None = require_api_key):
    with get_db() as conn:
        cur = conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, _: None = require_api_key):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
