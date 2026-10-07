import sqlite3

from app.db import read_connection, transaction
from app.errors import ConflictError, NotFoundError
from app.pagination import Page
from app.schemas import BookIn


def _to_dict(row: sqlite3.Row) -> dict:
    return dict(row) | {"available": row["available_copies"] > 0}


def list_books(q: str | None, available: bool | None, page: Page) -> list[dict]:
    clauses, params = [], []
    if q:
        escaped = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")  # match literally, not as wildcards
        like = f"%{escaped}%"
        clauses.append("(title LIKE ? ESCAPE '!' OR author LIKE ? ESCAPE '!' OR isbn LIKE ? ESCAPE '!')")
        params += [like, like, like]
    if available is not None:
        clauses.append("available_copies > 0" if available else "available_copies = 0")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM books{where} ORDER BY id DESC LIMIT ? OFFSET ?"
    with read_connection() as conn:
        return [_to_dict(r) for r in conn.execute(sql, (*params, page.limit, page.offset))]


def create(book: BookIn) -> dict:
    try:
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO books (title, author, isbn, total_copies, available_copies) VALUES (?, ?, ?, ?, ?)",
                (book.title, book.author, book.isbn, book.total_copies, book.total_copies),
            )
            row = conn.execute("SELECT * FROM books WHERE id = ?", (cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise ConflictError("A book with this ISBN already exists")
    return _to_dict(row)


def delete(book_id: int) -> None:
    try:
        with transaction() as conn:
            cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
            if cur.rowcount == 0:
                raise NotFoundError("Book not found")
    except sqlite3.IntegrityError:
        raise ConflictError("Book has loan records and cannot be deleted")


def set_availability(book_id: int, available_copies: int) -> dict:
    with transaction() as conn:
        # Admin override (e.g. lost or damaged copies); bounded by total_copies in one statement.
        cur = conn.execute(
            "UPDATE books SET available_copies = ? WHERE id = ? AND ? <= total_copies",
            (available_copies, book_id, available_copies),
        )
        if cur.rowcount == 0:
            if not conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone():
                raise NotFoundError("Book not found")
            raise ConflictError("available_copies cannot exceed total_copies")
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return _to_dict(row)
