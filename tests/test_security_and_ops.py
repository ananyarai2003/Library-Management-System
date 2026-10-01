import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect, migrate
from app.main import create_app
from tests.conftest import AUTH

BOOK = {"title": "T", "author": "A"}


def test_writes_require_api_key(client):
    assert client.post("/api/books", json=BOOK).status_code == 401
    assert client.post("/api/books", json=BOOK, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/books", json=BOOK, headers=AUTH).status_code == 201


def test_reads_are_public(client):
    assert client.get("/api/books").status_code == 200
    assert client.get("/api/members").status_code == 200
    assert client.get("/api/loans").status_code == 200


def test_writes_disabled_when_key_not_configured(tmp_path):
    app = create_app(Settings(db_path=tmp_path / "x.db", api_key=""))
    with TestClient(app) as c:
        assert c.post("/api/books", json=BOOK, headers=AUTH).status_code == 503


def test_cors_allows_only_configured_origins(client):
    ok = client.get("/healthz", headers={"Origin": "http://localhost:8000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"
    bad = client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_health_endpoints(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_error_envelope_shape(client):
    body = client.get("/api/books/123").json()
    assert body["error"]["code"] == "not_found"


def test_migration_upgrades_legacy_mvp_database(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
        " author TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1)"
    )
    legacy.execute("INSERT INTO books (title, author) VALUES ('Old', 'Author')")
    legacy.commit()
    legacy.close()

    conn = connect(path)
    migrate(conn)
    migrate(conn)  # idempotent
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    row = conn.execute("SELECT title, total_copies FROM books").fetchone()
    assert (row["title"], row["total_copies"]) == ("Old", 1)
    conn.close()
