import sqlite3

from app.db import read_connection, transaction
from app.errors import ConflictError, NotFoundError
from app.schemas import BookIn


def _to_dict(row: sqlite3.Row) -> dict:
    return dict(row) | {"available": row["available_copies"] > 0}


def list_books(q: str | None) -> list[dict]:
    sql, params = "SELECT * FROM books", ()
    if q:
        like = f"%{q}%"
        sql += " WHERE title LIKE ? OR author LIKE ? OR isbn LIKE ?"
        params = (like, like, like)
    with read_connection() as conn:
        return [_to_dict(r) for r in conn.execute(sql + " ORDER BY id DESC", params)]


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
