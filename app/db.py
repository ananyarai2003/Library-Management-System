"""SQLite access: explicit transactions (commit on success, rollback on error)."""
import sqlite3
from contextlib import contextmanager

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    author TEXT NOT NULL CHECK (length(trim(author)) > 0),
    isbn TEXT UNIQUE,
    total_copies INTEGER NOT NULL DEFAULT 1 CHECK (total_copies >= 1),
    available_copies INTEGER NOT NULL DEFAULT 1,
    CHECK (available_copies >= 0 AND available_copies <= total_copies)
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    email TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    borrowed_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    returned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_loans_active ON loans(returned_at);
"""


def _connect() -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves.
    conn = sqlite3.connect(config.db_path(), isolation_level=None, timeout=config.db_timeout_seconds())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def read_connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _upgrade_legacy_books(conn: sqlite3.Connection) -> None:
    """Migrate the original books(id, title, author, available) table in place."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    if not cols or "total_copies" in cols:
        return
    conn.execute("ALTER TABLE books RENAME TO books_legacy")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO books (id, title, author, total_copies, available_copies) "
        "SELECT id, title, author, 1, available FROM books_legacy"
    )
    conn.execute("DROP TABLE books_legacy")


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # persistent; readers do not block the writer
        conn.execute("PRAGMA synchronous = NORMAL")
        _upgrade_legacy_books(conn)
        conn.executescript(SCHEMA)
    finally:
        conn.close()
