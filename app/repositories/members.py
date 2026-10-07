import sqlite3

from app.db import read_connection, transaction
from app.errors import ConflictError
from app.pagination import Page
from app.schemas import MemberIn


def list_members(page: Page) -> list[dict]:
    with read_connection() as conn:
        rows = conn.execute("SELECT * FROM members ORDER BY id LIMIT ? OFFSET ?", (page.limit, page.offset))
        return [dict(r) for r in rows]


def create(member: MemberIn) -> dict:
    try:
        with transaction() as conn:
            cur = conn.execute("INSERT INTO members (name, email) VALUES (?, ?)", (member.name, member.email))
            row = conn.execute("SELECT * FROM members WHERE id = ?", (cur.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise ConflictError("A member with this email already exists")
    return dict(row)
