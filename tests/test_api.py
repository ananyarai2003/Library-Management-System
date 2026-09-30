import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _set_test_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    # Ensure app reload picks up env vars
    yield


def _client():
    # Import inside function so env vars are applied before module import
    import importlib

    import main

    importlib.reload(main)
    return TestClient(main.app)


def _login(client, username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


def test_health_and_ready():
    client = _client()
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_books_pagination_and_search_requires_auth_for_write():
    client = _client()

    # list (no auth)
    r = client.get("/api/books")
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # write requires auth
    r = client.post("/api/books", json={"title": "A", "author": "B"})
    assert r.status_code in (401, 403)

    token = _login(client, "librarian", "librarian")
    r = client.post(
        "/api/books",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Clean Code", "author": "Robert"},
    )
    assert r.status_code == 201

    # search
    r = client.get("/api/books?q=Clean")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # pagination is bounded
    r = client.get("/api/books?limit=1&offset=0")
    assert r.status_code == 200


def test_admin_only_delete():
    client = _client()
    librarian = _login(client, "librarian", "librarian")
    admin = _login(client, "admin", "admin")

    r = client.post(
        "/api/books",
        headers={"Authorization": f"Bearer {librarian}"},
        json={"title": "X", "author": "Y"},
    )
    book_id = r.json()["id"]

    r = client.delete(f"/api/books/{book_id}", headers={"Authorization": f"Bearer {librarian}"})
    assert r.status_code == 403

    r = client.delete(f"/api/books/{book_id}", headers={"Authorization": f"Bearer {admin}"})
    assert r.status_code == 200


def test_members_and_loans_flow_prevents_double_borrow():
    client = _client()
    token = _login(client, "librarian", "librarian")

    # create member
    r = client.post(
        "/api/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Jane Doe", "email": "jane@example.com"},
    )
    assert r.status_code == 201
    member_id = r.json()["id"]

    # create book
    r = client.post(
        "/api/books",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Dune", "author": "Frank Herbert"},
    )
    book_id = r.json()["id"]

    due = date.today() + timedelta(days=14)

    # borrow
    r = client.post(
        "/api/loans/borrow",
        headers={"Authorization": f"Bearer {token}"},
        json={"book_id": book_id, "member_id": member_id, "due_date": due.isoformat()},
    )
    assert r.status_code == 201
    loan_id = r.json()["id"]

    # cannot borrow again
    r = client.post(
        "/api/loans/borrow",
        headers={"Authorization": f"Bearer {token}"},
        json={"book_id": book_id, "member_id": member_id, "due_date": due.isoformat()},
    )
    assert r.status_code == 409

    # return
    r = client.post(f"/api/loans/{loan_id}/return", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # can borrow again after return
    r = client.post(
        "/api/loans/borrow",
        headers={"Authorization": f"Bearer {token}"},
        json={"book_id": book_id, "member_id": member_id, "due_date": due.isoformat()},
    )
    assert r.status_code == 201
