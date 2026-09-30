import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "library.db"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

DEFAULT_ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]


def _allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS")
    if not raw:
        return DEFAULT_ALLOWED_ORIGINS
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or DEFAULT_ALLOWED_ORIGINS


def _api_key() -> str:
    # Default to dev-key to keep local/dev and tests simple.
    return os.getenv("LIBRARY_API_KEY") or "dev-key"


def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    if x_api_key != _api_key():
        raise HTTPException(401, "Missing or invalid API key")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


app = FastAPI(title="Library Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
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
                checkout_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                returned_at TEXT,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(member_id) REFERENCES members(id) ON DELETE CASCADE
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loans_book_active ON loans(book_id) WHERE returned_at IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loans_member_active ON loans(member_id) WHERE returned_at IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loans_due_at ON loans(due_at)")


class BookIn(BaseModel):
    title: str
    author: str


class MemberIn(BaseModel):
    name: str
    email: Optional[str] = None


class LoanIn(BaseModel):
    book_id: int
    member_id: int
    # Default loan duration in days (simple policy for now)
    days: int = 14


# TODO: search, edit book


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/books")
def list_books():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                b.*,
                CASE WHEN EXISTS (
                    SELECT 1 FROM loans l
                    WHERE l.book_id = b.id AND l.returned_at IS NULL
                ) THEN 0 ELSE 1 END AS derived_available
            FROM books b
            ORDER BY b.id DESC
            """
        ).fetchall()
    return [dict(r) | {"available": bool(r["derived_available"])} for r in rows]


@app.post("/api/books", status_code=201)
def add_book(book: BookIn, _auth: None = None):
    require_api_key()  # explicit to keep minimal signature changes in routing
    with get_db() as conn:
        cur = conn.execute("INSERT INTO books (title, author) VALUES (?, ?)", (book.title, book.author))
    return {"id": cur.lastrowid, **book.model_dump(), "available": True}


@app.post("/api/books/{book_id}/toggle")
def toggle_availability(book_id: int):
    # Deprecated: availability is derived from active loans.
    require_api_key()

    with get_db() as conn:
        book = conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            raise HTTPException(404, "Book not found")

        active = conn.execute(
            "SELECT 1 FROM loans WHERE book_id = ? AND returned_at IS NULL LIMIT 1",
            (book_id,),
        ).fetchone()
        if active:
            raise HTTPException(409, "Cannot toggle availability while book has an active loan")

    raise HTTPException(410, "Endpoint deprecated: availability is derived from loans")


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int):
    require_api_key()
    with get_db() as conn:
        cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Book not found")
    return {"ok": True}


@app.get("/api/members")
def list_members():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM members ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/members", status_code=201)
def add_member(member: MemberIn):
    require_api_key()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO members (name, email) VALUES (?, ?)",
            (member.name, member.email),
        )
    return {"id": cur.lastrowid, **member.model_dump()}


@app.get("/api/loans")
def list_loans():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                l.*,
                b.title AS book_title,
                b.author AS book_author,
                m.name AS member_name,
                m.email AS member_email
            FROM loans l
            JOIN books b ON b.id = l.book_id
            JOIN members m ON m.id = l.member_id
            ORDER BY l.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/loans", status_code=201)
def checkout_loan(payload: LoanIn):
    require_api_key()
    if payload.days <= 0 or payload.days > 365:
        raise HTTPException(400, "days must be between 1 and 365")

    checkout_at = utcnow()
    due_at = checkout_at + timedelta(days=payload.days)

    with get_db() as conn:
        book = conn.execute("SELECT id FROM books WHERE id = ?", (payload.book_id,)).fetchone()
        if not book:
            raise HTTPException(404, "Book not found")

        member = conn.execute("SELECT id FROM members WHERE id = ?", (payload.member_id,)).fetchone()
        if not member:
            raise HTTPException(404, "Member not found")

        active = conn.execute(
            "SELECT id FROM loans WHERE book_id = ? AND returned_at IS NULL LIMIT 1",
            (payload.book_id,),
        ).fetchone()
        if active:
            raise HTTPException(409, "Book already checked out")

        cur = conn.execute(
            "INSERT INTO loans (book_id, member_id, checkout_at, due_at) VALUES (?, ?, ?, ?)",
            (
                payload.book_id,
                payload.member_id,
                checkout_at.isoformat(),
                due_at.isoformat(),
            ),
        )
    return {
        "id": cur.lastrowid,
        "book_id": payload.book_id,
        "member_id": payload.member_id,
        "checkout_at": checkout_at.isoformat(),
        "due_at": due_at.isoformat(),
        "returned_at": None,
    }


@app.post("/api/loans/{loan_id}/return")
def return_loan(loan_id: int):
    require_api_key()
    returned_at = utcnow().isoformat()

    with get_db() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            raise HTTPException(404, "Loan not found")
        if loan["returned_at"] is not None:
            raise HTTPException(409, "Loan already returned")

        conn.execute("UPDATE loans SET returned_at = ? WHERE id = ?", (returned_at, loan_id))

    return {"ok": True, "returned_at": returned_at}


# Serve the frontend at / (mounted last so /api routes take priority)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
