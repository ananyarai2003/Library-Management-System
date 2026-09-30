import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("library")

DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent / "library.db")))
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(Path(__file__).parent.parent / "frontend")))

API_KEY = os.getenv("API_KEY")  # If unset, write endpoints allow all (dev-friendly default).
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

SQLITE_TIMEOUT_SECONDS = float(os.getenv("SQLITE_TIMEOUT_SECONDS", "5.0"))

app = FastAPI(title="Library Management System")

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                due_at TEXT,
                returned_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE CASCADE
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loans_book_active ON loans(book_id, returned_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loans_due_active ON loans(due_at, returned_at)")


init_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso8601(dt: str) -> datetime:
    # Accept "Z" suffix by converting to "+00:00"
    if dt.endswith("Z"):
        dt = dt[:-1] + "+00:00"
    return datetime.fromisoformat(dt)


def require_api_key(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None):
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


class BookIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    author: str = Field(..., min_length=1, max_length=200)


class BookUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    author: str | None = Field(None, min_length=1, max_length=200)
    available: bool | None = None


class MemberIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str | None = Field(None, max_length=320)


class MemberUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    email: str | None = Field(None, max_length=320)


class CheckoutIn(BaseModel):
    book_id: int
    member_id: int
    due_at: str | None = None  # ISO8601


def _row_to_book(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "title": r["title"], "author": r["author"], "available": bool(r["available"])}


def _book_has_active_loan(conn: sqlite3.Connection, book_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM loans WHERE book_id = ? AND returned_at IS NULL LIMIT 1",
        (book_id,),
    ).fetchone()
    return row is not None


def _sync_book_availability_from_loans(conn: sqlite3.Connection, book_id: int) -> None:
    has_active = _book_has_active_loan(conn, book_id)
    conn.execute("UPDATE books SET available = ? WHERE id = ?", (0 if has_active else 1, book_id))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    # Return a generic 500 response instead of re-raising, which would bypass this handler.
    raise HTTPException(status_code=500, detail="Internal Server Error")


@app.get("/health")
def health():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True}
    except Exception as exc:
        logger.exception("Health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/books")
def list_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    return [_row_to_book(r) for r in rows]


@app.get("/api/books/{book_id}")
def get_book(book_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Book not found")
    return _row_to_book(row)


@app.post("/api/books", status_code=201, dependencies=[Depends(require_api_key)])
def add_book(book: BookIn):
    with get_db() as conn:
        cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
        book_id = cur.lastrowid
        logger.info("Book created id=%s title=%r", book_id, book.title)
    return {"id": book_id, **book.model_dump(), "available": True}


@app.put("/api/books/{book_id}", dependencies=[Depends(require_api_key)])
def update_book(book_id: int, payload: BookUpdate):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "Book not found")

        if payload.available is not None:
            if _book_has_active_loan(conn, book_id):
                raise HTTPException(409, "Cannot change availability while book has an active loan")
            conn.execute("UPDATE books SET available = ? WHERE id = ?", (1 if payload.available else 0, book_id))

        if payload.title is not None:
            conn.execute("UPDATE books SET title = ? WHERE id = ?", (payload.title, book_id))
        if payload.author is not None:
            conn.execute("UPDATE books SET author = ? WHERE id = ?", (payload.author, book_id))

        _sync_book_availability_from_loans(conn, book_id)
        updated = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()

        logger.info("Book updated id=%s", book_id)
    return _row_to_book(updated)


# Deprecated: prefer PUT /api/books/{id} or loans. Kept for backwards compatibility.
@app.post("/api/books/{book_id}/toggle", dependencies=[Depends(require_api_key)])
def toggle_availability(book_id: int):
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if exists is None:
            raise HTTPException(404, "Book not found")
        if _book_has_active_loan(conn, book_id):
            raise HTTPException(409, "Cannot toggle availability while book has an active loan")
        conn.execute("UPDATE books SET available = 1 - available WHERE id = ?", (book_id,))
        logger.info("Book toggled id=%s", book_id)
    return {"ok": True}


@app.delete("/api/books/{book_id}", dependencies=[Depends(require_api_key)])
def delete_book(book_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        logger.info("Book delete failed id=%s not found", book_id)
        raise HTTPException(404, "Book not found")
    logger.info("Book deleted id=%s", book_id)
    return {"ok": True}


@app.get("/api/members")
def list_members():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM members ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/members", status_code=201, dependencies=[Depends(require_api_key)])
def add_member(member: MemberIn):
    with get_db() as conn:
        cur = conn.execute("INSERT INTO members (name, email) VALUES (?, ?)", (member.name, member.email))
        member_id = cur.lastrowid
        logger.info("Member created id=%s name=%r", member_id, member.name)
    return {"id": member_id, **member.model_dump()}


@app.get("/api/members/{member_id}")
def get_member(member_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Member not found")
    return dict(row)


@app.put("/api/members/{member_id}", dependencies=[Depends(require_api_key)])
def update_member(member_id: int, payload: MemberUpdate):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "Member not found")

        if payload.name is not None:
            conn.execute("UPDATE members SET name = ? WHERE id = ?", (payload.name, member_id))
        if payload.email is not None:
            conn.execute("UPDATE members SET email = ? WHERE id = ?", (payload.email, member_id))

        updated = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        logger.info("Member updated id=%s", member_id)
    return dict(updated)


@app.delete("/api/members/{member_id}", dependencies=[Depends(require_api_key)])
def delete_member(member_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Member not found")
    logger.info("Member deleted id=%s", member_id)
    return {"ok": True}


@app.post("/api/loans/checkout", status_code=201, dependencies=[Depends(require_api_key)])
def checkout(payload: CheckoutIn):
    with get_db() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (payload.book_id,)).fetchone()
        if book is None:
            raise HTTPException(404, "Book not found")
        member = conn.execute("SELECT * FROM members WHERE id = ?", (payload.member_id,)).fetchone()
        if member is None:
            raise HTTPException(404, "Member not found")

        if _book_has_active_loan(conn, payload.book_id):
            raise HTTPException(409, "Book is already checked out")

        due_at = None
        if payload.due_at is not None:
            try:
                due_at = _parse_iso8601(payload.due_at).astimezone(timezone.utc).replace(microsecond=0).isoformat()
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, "Invalid due_at (expected ISO8601)") from exc

        cur = conn.execute(
            "INSERT INTO loans (book_id, member_id, due_at, returned_at, created_at) VALUES (?, ?, ?, NULL, ?)",
            (payload.book_id, payload.member_id, due_at, _now_iso()),
        )
        loan_id = cur.lastrowid
        _sync_book_availability_from_loans(conn, payload.book_id)
        logger.info("Loan checked out id=%s book_id=%s member_id=%s", loan_id, payload.book_id, payload.member_id)
    return {"id": loan_id, "book_id": payload.book_id, "member_id": payload.member_id, "due_at": due_at, "returned_at": None}


@app.post("/api/loans/{loan_id}/return", dependencies=[Depends(require_api_key)])
def return_loan(loan_id: int):
    with get_db() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise HTTPException(404, "Loan not found")
        if loan["returned_at"] is not None:
            raise HTTPException(409, "Loan already returned")

        returned_at = _now_iso()
        conn.execute("UPDATE loans SET returned_at = ? WHERE id = ?", (returned_at, loan_id))
        _sync_book_availability_from_loans(conn, loan["book_id"])
        logger.info("Loan returned id=%s book_id=%s", loan_id, loan["book_id"])
    return {"ok": True}


@app.get("/api/loans/overdue")
def overdue_loans():
    now = _now_iso()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.*, b.title as book_title, b.author as book_author, m.name as member_name, m.email as member_email
            FROM loans l
            JOIN books b ON b.id = l.book_id
            JOIN members m ON m.id = l.member_id
            WHERE l.returned_at IS NULL AND l.due_at IS NOT NULL AND l.due_at < ?
            ORDER BY l.due_at ASC
            """,
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


# Serve the frontend at / (mounted last so /api routes take priority)
if FRONTEND_DIR.exists() and FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    logger.info("Frontend directory missing; skipping mount: %s", FRONTEND_DIR)
