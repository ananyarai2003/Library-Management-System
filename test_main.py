import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def _set_env(monkeypatch, tmp_path):
    # Ensure API key is configured for tests
    monkeypatch.setenv("API_KEY", "test-secret")

    # Use a temporary sqlite DB for each test
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(main, "DB_PATH", db_path)

    # Recompute API_KEY in module (it is read at import time)
    monkeypatch.setattr(main, "API_KEY", os.getenv("API_KEY"))

    # init schema
    main.init_db()
    yield


@pytest.fixture()
def client():
    return TestClient(main.app)


def auth_headers():
    return {"X-API-Key": "test-secret"}


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_list_books_public(client):
    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json() == []


def test_write_requires_auth(client):
    r = client.post("/api/books", json={"title": "t", "author": "a"})
    assert r.status_code in (401, 503)


def test_create_persists_across_connections(client):
    r = client.post("/api/books", json={"title": "The Alchemist", "author": "Paulo"}, headers=auth_headers())
    assert r.status_code == 201
    book_id = r.json()["id"]

    # New request -> new connection -> should see persisted data
    r2 = client.get("/api/books")
    assert r2.status_code == 200
    ids = [b["id"] for b in r2.json()]
    assert book_id in ids


def test_patch_availability_idempotent(client):
    r = client.post("/api/books", json={"title": "Dune", "author": "Frank"}, headers=auth_headers())
    book_id = r.json()["id"]

    r1 = client.patch(f"/api/books/{book_id}", json={"available": False}, headers=auth_headers())
    assert r1.status_code == 200

    r2 = client.patch(f"/api/books/{book_id}", json={"available": False}, headers=auth_headers())
    assert r2.status_code == 200

    books = client.get("/api/books").json()
    book = next(b for b in books if b["id"] == book_id)
    assert book["available"] is False


def test_toggle_still_works_deprecated(client):
    r = client.post("/api/books", json={"title": "Neuromancer", "author": "Gibson"}, headers=auth_headers())
    book_id = r.json()["id"]

    books = client.get("/api/books").json()
    book = next(b for b in books if b["id"] == book_id)
    start_avail = book["available"]

    r2 = client.post(f"/api/books/{book_id}/toggle", headers=auth_headers())
    assert r2.status_code == 200

    books2 = client.get("/api/books").json()
    book2 = next(b for b in books2 if b["id"] == book_id)
    assert book2["available"] is (not start_avail)


def test_delete_404(client):
    r = client.delete("/api/books/9999", headers=auth_headers())
    assert r.status_code == 404


def test_request_id_header_present(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "X-Request-ID" in r.headers
