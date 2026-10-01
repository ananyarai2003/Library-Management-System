"""SQL access for members."""
import sqlite3

from app.errors import ConflictError, NotFoundError

_COLUMNS = "id, name, email"


def list_members(conn, limit: int, offset: int) -> list[dict]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM members ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    return [dict(r) for r in rows]


def get_member(conn, member_id: int) -> dict:
    row = conn.execute(f"SELECT {_COLUMNS} FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        raise NotFoundError("Member")
    return dict(row)


def create_member(conn, data: dict) -> dict:
    try:
        cur = conn.execute("INSERT INTO members (name, email) VALUES (:name, :email)", data)
    except sqlite3.IntegrityError as exc:
        raise ConflictError("A member with this email already exists") from exc
    return get_member(conn, cur.lastrowid)


def update_member(conn, member_id: int, changes: dict) -> dict:
    current = get_member(conn, member_id)
    if not changes:
        return current
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    try:
        conn.execute(f"UPDATE members SET {assignments} WHERE id = :id", {**changes, "id": member_id})
    except sqlite3.IntegrityError as exc:
        raise ConflictError("A member with this email already exists") from exc
    return get_member(conn, member_id)


def delete_member(conn, member_id: int) -> None:
    get_member(conn, member_id)
    if conn.execute("SELECT 1 FROM loans WHERE member_id = ? LIMIT 1", (member_id,)).fetchone():
        raise ConflictError("Member has loan history and cannot be deleted")
    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
