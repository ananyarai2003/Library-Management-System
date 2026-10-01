"""SQLite connection handling and versioned schema migrations."""
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request

MIGRATIONS: list[str] = [
    # v1: original MVP schema
    """
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        available INTEGER NOT NULL DEFAULT 1
    );
    """,
    # v2: copies, isbn, members, loans (books.available is legacy and unused)
    """
    ALTER TABLE books ADD COLUMN isbn TEXT;
    ALTER TABLE books ADD COLUMN total_copies INTEGER NOT NULL DEFAULT 1 CHECK (total_copies >= 0);
    ALTER TABLE books ADD COLUMN created_at TEXT;
    UPDATE books SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE created_at IS NULL;
    CREATE UNIQUE INDEX idx_books_isbn ON books(isbn) WHERE isbn IS NOT NULL;
    CREATE TABLE members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL REFERENCES books(id),
        member_id INTEGER NOT NULL REFERENCES members(id),
        loaned_at TEXT NOT NULL,
        due_at TEXT NOT NULL,
        returned_at TEXT
    );
    CREATE INDEX idx_loans_book_open ON loans(book_id, returned_at);
    CREATE INDEX idx_loans_member_open ON loans(member_id, returned_at);
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations, tracked through PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, script in enumerate(MIGRATIONS, start=1):
        if target <= version:
            continue
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, committed on success."""
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
