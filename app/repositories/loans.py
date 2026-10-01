"""SQL access for loans (borrow and return)."""
from datetime import datetime, timedelta, timezone

from app.errors import ConflictError, NotFoundError
from app.repositories import books, members

_TS = "%Y-%m-%dT%H:%M:%SZ"
_SELECT = """
SELECT id, book_id, member_id, loaned_at, due_at, returned_at,
       (returned_at IS NULL AND due_at < :now) AS overdue
FROM loans
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(moment: datetime) -> str:
    return moment.strftime(_TS)


def _to_dict(row) -> dict:
    data = dict(row)
    data["overdue"] = bool(data["overdue"])
    return data


def get_loan(conn, loan_id: int) -> dict:
    row = conn.execute(_SELECT + " WHERE id = :id", {"id": loan_id, "now": _fmt(_now())}).fetchone()
    if row is None:
        raise NotFoundError("Loan")
    return _to_dict(row)


def list_loans(conn, member_id, book_id, active, limit: int, offset: int) -> list[dict]:
    clauses, params = [], {"now": _fmt(_now()), "limit": limit, "offset": offset}
    if member_id is not None:
        clauses.append("member_id = :member_id")
        params["member_id"] = member_id
    if book_id is not None:
        clauses.append("book_id = :book_id")
        params["book_id"] = book_id
    if active is not None:
        clauses.append("returned_at IS NULL" if active else "returned_at IS NOT NULL")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"{_SELECT}{where} ORDER BY id DESC LIMIT :limit OFFSET :offset", params)
    return [_to_dict(r) for r in rows]


def borrow(conn, book_id: int, member_id: int, loan_days: int) -> dict:
    conn.execute("BEGIN IMMEDIATE")  # serialise writers so copies cannot be over-lent
    members.get_member(conn, member_id)
    if books.get_book(conn, book_id)["available_copies"] < 1:
        raise ConflictError("No copies of this book are available")
    now = _now()
    cur = conn.execute(
        "INSERT INTO loans (book_id, member_id, loaned_at, due_at) VALUES (?, ?, ?, ?)",
        (book_id, member_id, _fmt(now), _fmt(now + timedelta(days=loan_days))),
    )
    return get_loan(conn, cur.lastrowid)


def return_loan(conn, loan_id: int) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    get_loan(conn, loan_id)
    cur = conn.execute("UPDATE loans SET returned_at = ? WHERE id = ? AND returned_at IS NULL", (_fmt(_now()), loan_id))
    if cur.rowcount == 0:
        raise ConflictError("Loan has already been returned")
    return get_loan(conn, loan_id)
