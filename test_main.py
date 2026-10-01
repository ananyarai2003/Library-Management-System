import os

from fastapi.testclient import TestClient

import main


def _client_with_env(monkeypatch):
    # Ensure env is set for this test run.
    monkeypatch.setenv("API_KEY", "test-key")
    # CORS is configured at import time; we don't test middleware behavior here.
    return TestClient(main.app)


def test_get_books_is_public(monkeypatch):
    client = _client_with_env(monkeypatch)
    resp = client.get("/api/books")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_post_books_requires_auth(monkeypatch):
    client = _client_with_env(monkeypatch)
    resp = client.post("/api/books", json={"title": "T", "author": "A"})
    assert resp.status_code == 401


def test_post_books_with_auth_succeeds(monkeypatch):
    client = _client_with_env(monkeypatch)
    resp = client.post(
        "/api/books",
        json={"title": "Auth Book", "author": "Author"},
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Auth Book"
    assert body["author"] == "Author"
    assert body["available"] is True
    assert isinstance(body["id"], int)


def test_toggle_requires_auth(monkeypatch):
    client = _client_with_env(monkeypatch)

    created = client.post(
        "/api/books",
        json={"title": "Toggle Book", "author": "Author"},
        headers={"X-API-Key": "test-key"},
    ).json()

    resp = client.post(f"/api/books/{created['id']}/toggle")
    assert resp.status_code == 401


def test_delete_requires_auth(monkeypatch):
    client = _client_with_env(monkeypatch)

    created = client.post(
        "/api/books",
        json={"title": "Delete Book", "author": "Author"},
        headers={"X-API-Key": "test-key"},
    ).json()

    resp = client.delete(f"/api/books/{created['id']}")
    assert resp.status_code == 401


def test_delete_with_auth_succeeds(monkeypatch):
    client = _client_with_env(monkeypatch)

    created = client.post(
        "/api/books",
        json={"title": "Delete With Auth", "author": "Author"},
        headers={"X-API-Key": "test-key"},
    ).json()

    resp = client.delete(
        f"/api/books/{created['id']}", headers={"X-API-Key": "test-key"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
