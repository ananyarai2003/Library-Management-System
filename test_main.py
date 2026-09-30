import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Ensure module reads env vars at import time.
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEY", "test-secret")
    # avoid mounting frontend (directory won't exist)
    monkeypatch.setenv("FRONTEND_DIR", str(tmp_path / "frontend"))

    import importlib
    import main as main_module

    importlib.reload(main_module)

    return TestClient(main_module.app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_readyz(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_write_endpoints_require_api_key(client):
    r = client.post("/api/books", json={"title": "T", "author": "A"})
    assert r.status_code == 401

    # Create with auth
    r = client.post("/api/books", headers={"X-API-Key": "test-secret"}, json={"title": "T", "author": "A"})
    assert r.status_code == 201
    book_id = r.json()["id"]

    r = client.post(f"/api/books/{book_id}/toggle")
    assert r.status_code == 401

    r = client.delete(f"/api/books/{book_id}")
    assert r.status_code == 401


def test_add_list_toggle_delete_flow(client):
    # add
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "  Clean Code ", "author": " Robert C. Martin "},
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["title"] == "Clean Code"
    assert payload["author"] == "Robert C. Martin"
    assert payload["available"] is True

    book_id = payload["id"]

    # list
    r = client.get("/api/books")
    assert r.status_code == 200
    books = r.json()
    assert any(b["id"] == book_id for b in books)

    # toggle
    r = client.post(f"/api/books/{book_id}/toggle", headers={"X-API-Key": "test-secret"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["available"] is False

    # delete
    r = client.delete(f"/api/books/{book_id}", headers={"X-API-Key": "test-secret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_toggle_nonexistent_returns_404(client):
    r = client.post("/api/books/999999/toggle", headers={"X-API-Key": "test-secret"})
    assert r.status_code == 404


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/api/books/999999", headers={"X-API-Key": "test-secret"})
    assert r.status_code == 404


def test_validation_rejects_blank_strings(client):
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "   ", "author": "A"},
    )
    assert r.status_code == 422
