import sqlite3
from datetime import datetime, timedelta, timezone

from app import config
from app.db import read_connection, transaction
from app.errors import ConflictError, NotFoundError
from app.pagination import Page


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dict(row: sqlite3.Row) -> dict:
    overdue = row["returned_at"] is None and datetime.fromisoformat(row["due_at"]) < _now()
    return dict(row) | {"overdue": overdue}


def list_loans(active: bool, member_id: int | None, book_id: int | None, page: Page) -> list[dict]:
    clauses, params = [], []
    if active:
        clauses.append("returned_at IS NULL")
    if member_id is not None:
        clauses.append("member_id = ?")
        params.append(member_id)
    if book_id is not None:
        clauses.append("book_id = ?")
        params.append(book_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM loans{where} ORDER BY id DESC LIMIT ? OFFSET ?"
    with read_connection() as conn:
        return [_to_dict(r) for r in conn.execute(sql, (*params, page.limit, page.offset))]


def borrow(book_id: int, member_id: int) -> dict:
    now = _now()
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone():
            raise NotFoundError("Book not found")
        if not conn.execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone():
            raise NotFoundError("Member not found")
        # Single conditional UPDATE: cannot over-borrow.
        cur = conn.execute(
            "UPDATE books SET available_copies = available_copies - 1 WHERE id = ? AND available_copies > 0",
            (book_id,),
        )
        if cur.rowcount == 0:
            raise ConflictError("No copies available")
        cur = conn.execute(
            "INSERT INTO loans (book_id, member_id, borrowed_at, due_at) VALUES (?, ?, ?, ?)",
            (book_id, member_id, now.isoformat(), (now + timedelta(days=config.loan_days())).isoformat()),
        )
        row = conn.execute("SELECT * FROM loans WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _to_dict(row)


def return_loan(loan_id: int) -> dict:
    with transaction() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            raise NotFoundError("Loan not found")
        cur = conn.execute(
            "UPDATE loans SET returned_at = ? WHERE id = ? AND returned_at IS NULL",
            (_now().isoformat(), loan_id),
        )
        if cur.rowcount == 0:
            raise ConflictError("Loan already returned")
        conn.execute("UPDATE books SET available_copies = available_copies + 1 WHERE id = ?", (loan["book_id"],))
        row = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    return _to_dict(row)
