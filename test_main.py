import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _set_test_env(tmp_path, monkeypatch):
    # Use isolated sqlite db per test session.
    monkeypatch.setenv("LIBRARY_API_KEY", "test-key")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")

    # Import-time DB_PATH is computed in main.py; override by setting cwd? Not possible.
    # Instead, replace main.DB_PATH after import.
    import main

    main.DB_PATH = tmp_path / "test_library.db"
    # Ensure fresh schema for each test.
    main.init_db()
    yield


def _client():
    import main

    return TestClient(main.app)


def _auth_headers():
    return {"X-API-Key": "test-key"}


def test_books_add_list_delete_and_auth():
    client = _client()

    # Unauthorized write
    r = client.post("/api/books", json={"title": "Dune", "author": "Frank Herbert"})
    assert r.status_code == 401

    # Create book
    r = client.post("/api/books", json={"title": "Dune", "author": "Frank Herbert"}, headers=_auth_headers())
    assert r.status_code == 201
    book_id = r.json()["id"]

    # List books should show derived availability True
    r = client.get("/api/books")
    assert r.status_code == 200
    books = r.json()
    assert any(b["id"] == book_id and b["available"] is True for b in books)

    # Delete
    r = client.delete(f"/api/books/{book_id}", headers=_auth_headers())
    assert r.status_code == 200

    # Delete missing -> 404
    r = client.delete(f"/api/books/{book_id}", headers=_auth_headers())
    assert r.status_code == 404


def test_members_create_list_and_validation():
    client = _client()

    r = client.post("/api/members", json={"name": "Alice"})
    assert r.status_code == 401

    r = client.post("/api/members", json={"name": "Alice", "email": "alice@example.com"}, headers=_auth_headers())
    assert r.status_code == 201
    member_id = r.json()["id"]

    r = client.get("/api/members")
    assert r.status_code == 200
    assert any(m["id"] == member_id for m in r.json())

    # Validation error (missing name)
    r = client.post("/api/members", json={"email": "x@y.com"}, headers=_auth_headers())
    assert r.status_code == 422


def test_loans_checkout_return_conflicts_and_availability():
    client = _client()

    # Setup: create member + book
    r = client.post("/api/members", json={"name": "Bob"}, headers=_auth_headers())
    member_id = r.json()["id"]

    r = client.post("/api/books", json={"title": "1984", "author": "George Orwell"}, headers=_auth_headers())
    book_id = r.json()["id"]

    # Checkout
    r = client.post(
        "/api/loans",
        json={"book_id": book_id, "member_id": member_id, "days": 7},
        headers=_auth_headers(),
    )
    assert r.status_code == 201
    loan_id = r.json()["id"]

    # Availability becomes False
    r = client.get("/api/books")
    assert any(b["id"] == book_id and b["available"] is False for b in r.json())

    # Double checkout conflict
    r = client.post(
        "/api/loans",
        json={"book_id": book_id, "member_id": member_id, "days": 7},
        headers=_auth_headers(),
    )
    assert r.status_code == 409

    # Return
    r = client.post(f"/api/loans/{loan_id}/return", headers=_auth_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Return again -> conflict
    r = client.post(f"/api/loans/{loan_id}/return", headers=_auth_headers())
    assert r.status_code == 409

    # Availability becomes True again
    r = client.get("/api/books")
    assert any(b["id"] == book_id and b["available"] is True for b in r.json())


def test_loan_404_cases():
    client = _client()

    r = client.post("/api/members", json={"name": "Carl"}, headers=_auth_headers())
    member_id = r.json()["id"]

    # book does not exist
    r = client.post(
        "/api/loans",
        json={"book_id": 9999, "member_id": member_id, "days": 7},
        headers=_auth_headers(),
    )
    assert r.status_code == 404

    # return loan missing
    r = client.post("/api/loans/9999/return", headers=_auth_headers())
    assert r.status_code == 404
