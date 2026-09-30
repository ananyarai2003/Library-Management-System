import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, constr

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

logger = logging.getLogger("library_api")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

def _cors_allowlist() -> list[str]:
    # Default: localhost-only
    default = "http://localhost,http://localhost:3000,http://127.0.0.1,http://127.0.0.1:3000"
    raw = os.getenv("CORS_ALLOW_ORIGINS", default)
    return [o.strip() for o in raw.split(",") if o.strip()]

API_KEY_ENV = "API_KEY"

app = FastAPI(title="Library Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowlist(),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    # Concurrency and integrity hardening
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=3000;")
    conn.execute("PRAGMA foreign_keys=ON;")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    return conn


def init_db() -> None:
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class BookIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: constr(min_length=1, max_length=200)  # type: ignore[valid-type]
    author: constr(min_length=1, max_length=200)  # type: ignore[valid-type]


class BookOut(BaseModel):
    id: int
    title: str
    author: str
    available: bool


class OkResponse(BaseModel):
    ok: bool = True


class HealthResponse(BaseModel):
    ok: bool
    db: bool


# TODO: members, due dates, search, edit book, auth


def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    expected = os.getenv(API_KEY_ENV)
    if x_api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def _handle_sqlite_error(exc: sqlite3.Error) -> None:
    msg = str(exc).lower()
    if isinstance(exc, sqlite3.OperationalError) and "locked" in msg:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is locked") from exc
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error") from exc


@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    logger.info("request", extra={"method": request.method, "path": request.url.path})
    response = await call_next(request)
    logger.info(
        "response",
        extra={"method": request.method, "path": request.url.path, "status_code": response.status_code},
    )
    return response


@app.get("/api/books", response_model=list[BookOut])
def list_books():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
        return [dict(r) | {"available": bool(r["available"])} for r in rows]
    except sqlite3.Error as exc:
        _handle_sqlite_error(exc)


@app.post("/api/books", status_code=201, response_model=BookOut, dependencies=[Depends(require_api_key)])
def add_book(book: BookIn):
    try:
        with get_db() as conn:
            cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
            conn.commit()
        return {"id": cur.lastrowid, **book.model_dump(), "available": True}
    except sqlite3.Error as exc:
        _handle_sqlite_error(exc)


class BookPatch(BaseModel):
    available: StrictBool = Field(...)


@app.patch("/api/books/{book_id}", response_model=BookOut, dependencies=[Depends(require_api_key)])
def update_availability(book_id: int, patch: BookPatch):
    try:
        with get_db() as conn:
            cur = conn.execute(
                "UPDATE books SET available = ? WHERE id = ?",
                (1 if patch.available else 0, book_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Book not found")
            conn.commit()
            row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        assert row is not None
        return dict(row) | {"available": bool(row["available"])}
    except sqlite3.Error as exc:
        _handle_sqlite_error(exc)


@app.post(
    "/api/books/{book_id}/toggle",
    response_model=OkResponse,
    deprecated=True,
    dependencies=[Depends(require_api_key)],
)
def toggle_availability(book_id: int):
    # Backward-compatible wrapper: toggles current state via PATCH semantics.
    try:
        with get_db() as conn:
            row = conn.execute("SELECT available FROM books WHERE id = ?", (book_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Book not found")
            new_available = 0 if int(row["available"]) == 1 else 1
            conn.execute("UPDATE books SET available = ? WHERE id = ?", (new_available, book_id))
            conn.commit()
        return {"ok": True}
    except sqlite3.Error as exc:
        _handle_sqlite_error(exc)


@app.delete("/api/books/{book_id}", response_model=OkResponse, dependencies=[Depends(require_api_key)])
def delete_book(book_id: int):
    try:
        with get_db() as conn:
            cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Book not found")
            conn.commit()
        return {"ok": True}
    except sqlite3.Error as exc:
        _handle_sqlite_error(exc)


@app.get("/healthz", response_model=HealthResponse)
def healthz():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True, "db": True}
    except sqlite3.Error as exc:
        # Reflect DB issue without leaking details
        logger.exception("healthz_db_error")
        if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is locked") from exc
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error") from exc


# Serve the frontend at / (mounted last so /api routes take priority)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
