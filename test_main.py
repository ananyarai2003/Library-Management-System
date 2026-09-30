import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _set_test_env(tmp_path, monkeypatch):
    # Ensure env vars are set before importing main
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("FRONTEND_DIR", str(tmp_path / "no-frontend"))
    yield


def _import_app():
    # Import after env set; reload defensively
    import importlib
    import main

    importlib.reload(main)
    return main.app


def _client():
    return TestClient(_import_app())


def test_health_ok():
    client = _client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_list_books_empty():
    client = _client()
    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json() == []


def test_write_endpoints_require_api_key():
    client = _client()

    r = client.post("/api/books", json={"title": "T", "author": "A"})
    assert r.status_code == 401

    r = client.post("/api/books/1/toggle")
    assert r.status_code == 401

    r = client.delete("/api/books/1")
    assert r.status_code == 401


def test_create_validation_rejects_whitespace():
    client = _client()
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-key"},
        json={"title": "   ", "author": "  "},
    )
    assert r.status_code == 422


def test_create_toggle_delete_happy_path():
    client = _client()

    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-key"},
        json={"title": "  The Hobbit ", "author": " Tolkien  "},
    )
    assert r.status_code == 201
    created = r.json()
    assert created["title"] == "The Hobbit"
    assert created["author"] == "Tolkien"
    assert created["available"] is True

    book_id = created["id"]

    r = client.get("/api/books")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.post(f"/api/books/{book_id}/toggle", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # verify availability flipped
    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json()[0]["available"] is False

    r = client.delete(f"/api/books/{book_id}", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json() == []


def test_toggle_nonexistent_404_with_auth():
    client = _client()
    r = client.post("/api/books/999/toggle", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404


def test_delete_nonexistent_404_with_auth():
    client = _client()
    r = client.delete("/api/books/999", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404
