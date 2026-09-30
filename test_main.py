import os

from fastapi.testclient import TestClient

import main


def _client_with_env(monkeypatch, api_key: str | None):
    if api_key is None:
        monkeypatch.delenv("LIBRARY_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LIBRARY_API_KEY", api_key)

    # Keep CORS deterministic for import-time configuration
    monkeypatch.setenv("LIBRARY_ALLOWED_ORIGINS", "http://localhost:8000")

    return TestClient(main.app)


def test_write_endpoints_require_api_key_when_configured(monkeypatch):
    client = _client_with_env(monkeypatch, api_key="secret")

    r = client.post("/api/books", json={"title": "T", "author": "A"})
    assert r.status_code == 401

    r = client.post("/api/books", headers={"X-API-Key": "wrong"}, json={"title": "T", "author": "A"})
    assert r.status_code == 401

    r = client.post("/api/books", headers={"X-API-Key": "secret"}, json={"title": "T", "author": "A"})
    assert r.status_code == 201
    book_id = r.json()["id"]

    r = client.post(f"/api/books/{book_id}/toggle")
    assert r.status_code == 401

    r = client.post(f"/api/books/{book_id}/toggle", headers={"X-API-Key": "secret"})
    assert r.status_code == 200

    r = client.delete(f"/api/books/{book_id}")
    assert r.status_code == 401

    r = client.delete(f"/api/books/{book_id}", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


def test_read_endpoint_does_not_require_api_key(monkeypatch):
    client = _client_with_env(monkeypatch, api_key="secret")
    r = client.get("/api/books")
    assert r.status_code == 200


def test_cors_allows_configured_origin(monkeypatch):
    # Note: CORSMiddleware reads env at import-time; ensure env is set before request.
    client = _client_with_env(monkeypatch, api_key=None)

    r = client.options(
        "/api/books",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"
