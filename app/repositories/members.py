import sqlite3

from app.db import read_connection, transaction
from app.errors import ConflictError
from app.schemas import MemberIn


def list_members() -> list[dict]:
    with read_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM members ORDER BY id")]


def create(member: MemberIn) -> dict:
    try:
        with transaction() as conn:
            cur = conn.execute("INSERT INTO members (name, email) VALUES (?, ?)", (member.name, member.email))
            row = conn.execute("SELECT * FROM members WHERE id = ?", (cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise ConflictError("A member with this email already exists")
    return dict(row)
