import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    # Ensure API key enabled for write endpoints
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        monkeypatch.setenv("DB_PATH", str(db_path))

        # Import after env is set so module-level config uses it
        import importlib
        import main as main_module

        importlib.reload(main_module)

        yield TestClient(main_module.app)


def _auth_headers():
    return {"X-API-Key": "test-key"}


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_books_crud_and_validation(client):
    # Validation
    r = client.post("/api/books", json={"title": "", "author": "A"}, headers=_auth_headers())
    assert r.status_code == 422

    # Create
    r = client.post("/api/books", json={"title": "T", "author": "A"}, headers=_auth_headers())
    assert r.status_code == 201
    book_id = r.json()["id"]

    # Get by id
    r = client.get(f"/api/books/{book_id}")
    assert r.status_code == 200
    assert r.json()["title"] == "T"

    # Update
    r = client.put(f"/api/books/{book_id}", json={"title": "T2"}, headers=_auth_headers())
    assert r.status_code == 200
    assert r.json()["title"] == "T2"


def test_auth_required_for_write_endpoints(client):
    r = client.post("/api/books", json={"title": "T", "author": "A"})
    assert r.status_code == 401


def test_members_and_loans_workflow(client):
    # Create book
    r = client.post("/api/books", json={"title": "B1", "author": "Au"}, headers=_auth_headers())
    book_id = r.json()["id"]

    # Create member
    r = client.post("/api/members", json={"name": "M1", "email": "m1@example.com"}, headers=_auth_headers())
    assert r.status_code == 201
    member_id = r.json()["id"]

    # Checkout
    r = client.post(
        "/api/loans/checkout",
        json={"book_id": book_id, "member_id": member_id, "due_at": "2099-01-01T00:00:00Z"},
        headers=_auth_headers(),
    )
    assert r.status_code == 201
    loan_id = r.json()["id"]

    # Book should now be unavailable
    r = client.get(f"/api/books/{book_id}")
    assert r.status_code == 200
    assert r.json()["available"] is False

    # Second checkout should conflict
    r = client.post(
        "/api/loans/checkout",
        json={"book_id": book_id, "member_id": member_id},
        headers=_auth_headers(),
    )
    assert r.status_code == 409

    # Return
    r = client.post(f"/api/loans/{loan_id}/return", headers=_auth_headers())
    assert r.status_code == 200

    # Book should now be available
    r = client.get(f"/api/books/{book_id}")
    assert r.status_code == 200
    assert r.json()["available"] is True


def test_overdue_endpoint_empty(client):
    r = client.get("/api/loans/overdue")
    assert r.status_code == 200
    assert r.json() == []
