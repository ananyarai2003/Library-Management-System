"""SQL access for books."""
import sqlite3

from app.errors import ConflictError, NotFoundError

_SELECT = """
SELECT b.id, b.title, b.author, b.isbn, b.total_copies,
       b.total_copies - (SELECT COUNT(*) FROM loans l WHERE l.book_id = b.id AND l.returned_at IS NULL)
           AS available_copies
FROM books b
"""
_ESC = "!"
_SEARCH = " WHERE b.title LIKE ?1 ESCAPE '!' OR b.author LIKE ?1 ESCAPE '!' OR b.isbn LIKE ?1 ESCAPE '!'"


def _to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["available"] = data["available_copies"] > 0
    return data


def _escape_like(term: str) -> str:
    return term.replace(_ESC, _ESC * 2).replace("%", _ESC + "%").replace("_", _ESC + "_")


def list_books(conn, q: str | None, limit: int, offset: int) -> list[dict]:
    sql, params = _SELECT, []
    if q:
        sql += _SEARCH
        params.append(f"%{_escape_like(q)}%")
    sql += f" ORDER BY b.id DESC LIMIT ?{len(params) + 1} OFFSET ?{len(params) + 2}"
    return [_to_dict(r) for r in conn.execute(sql, [*params, limit, offset])]


def get_book(conn, book_id: int) -> dict:
    row = conn.execute(_SELECT + " WHERE b.id = ?", (book_id,)).fetchone()
    if row is None:
        raise NotFoundError("Book")
    return _to_dict(row)


def create_book(conn, data: dict) -> dict:
    try:
        cur = conn.execute(
            "INSERT INTO books (title, author, isbn, total_copies, created_at) "
            "VALUES (:title, :author, :isbn, :total_copies, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            data,
        )
    except sqlite3.IntegrityError as exc:
        raise ConflictError("A book with this ISBN already exists") from exc
    return get_book(conn, cur.lastrowid)


def update_book(conn, book_id: int, changes: dict) -> dict:
    current = get_book(conn, book_id)
    if not changes:
        return current
    copies = changes.get("total_copies")
    on_loan = current["total_copies"] - current["available_copies"]
    if copies is not None and copies < on_loan:
        raise ConflictError(f"total_copies cannot be below the {on_loan} copies currently on loan")
    # Column names come from the BookUpdate schema (extra="forbid"), never from raw client keys.
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    try:
        conn.execute(f"UPDATE books SET {assignments} WHERE id = :id", {**changes, "id": book_id})
    except sqlite3.IntegrityError as exc:
        raise ConflictError("A book with this ISBN already exists") from exc
    return get_book(conn, book_id)


def delete_book(conn, book_id: int) -> None:
    get_book(conn, book_id)
    if conn.execute("SELECT 1 FROM loans WHERE book_id = ? LIMIT 1", (book_id,)).fetchone():
        raise ConflictError("Book has loan history and cannot be deleted")
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
