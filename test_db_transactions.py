import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """Force the app to use a temp sqlite DB for each test."""
    db_path = tmp_path / "test_library.db"
    monkeypatch.setattr(main, "DB_PATH", db_path)
    main.init_db()
    yield


def test_add_book_commits_and_persists():
    client = TestClient(main.app)

    r = client.post("/api/books", json={"title": "Clean Code", "author": "Robert C. Martin"})
    assert r.status_code == 201
    created_id = r.json()["id"]

    r2 = client.get("/api/books")
    assert r2.status_code == 200
    ids = [b["id"] for b in r2.json()]
    assert created_id in ids


def test_toggle_missing_book_still_404():
    client = TestClient(main.app)

    r = client.post("/api/books/999/toggle")
    assert r.status_code == 404
    assert r.json()["detail"] == "Book not found"


def test_delete_missing_book_still_404():
    client = TestClient(main.app)

    r = client.delete("/api/books/999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Book not found"


def test_sqlite_locked_returns_503(monkeypatch, tmp_path):
    """Simulate sqlite3.OperationalError('database is locked') on INSERT."""
    client = TestClient(main.app)

    original_execute = sqlite3.Connection.execute

    def locked_execute(self, *args, **kwargs):
        sql = args[0] if args else ""
        if isinstance(sql, str) and sql.strip().upper().startswith("INSERT"):
            raise sqlite3.OperationalError("database is locked")
        return original_execute(self, *args, **kwargs)

    monkeypatch.setattr(sqlite3.Connection, "execute", locked_execute, raising=True)

    r = client.post("/api/books", json={"title": "X", "author": "Y"})
    assert r.status_code == 503
    assert "locked" in r.json()["detail"].lower()
