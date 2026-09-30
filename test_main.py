import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Use a temp sqlite DB for each test (module under test uses DB_PATH global)."""
    db_path = tmp_path / "test_library.db"
    monkeypatch.setattr(main, "DB_PATH", db_path)
    # Re-init schema for the new DB
    main.init_db()
    yield


@pytest.fixture
def client(monkeypatch):
    # configure API key for auth-protected endpoints
    monkeypatch.setenv("API_KEY", "test-secret")
    return TestClient(main.app)


def test_list_books_public(client):
    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json() == []


def test_write_requires_api_key(client):
    r = client.post("/api/books", json={"title": "T", "author": "A"})
    assert r.status_code in (401, 500)  # 500 only if server missing API_KEY; fixture sets it
    assert r.status_code == 401


def test_create_book_and_validation_and_strip(client):
    # blank inputs rejected
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "   ", "author": "A"},
    )
    assert r.status_code == 422

    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "  Clean Code  ", "author": "  Robert C. Martin  "},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "Clean Code"
    assert data["author"] == "Robert C. Martin"
    assert data["available"] is True

    # list shows it
    r2 = client.get("/api/books")
    assert r2.status_code == 200
    books = r2.json()
    assert len(books) == 1
    assert books[0]["available"] is True


def test_patch_availability_idempotent(client):
    # create
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "Book", "author": "Auth"},
    )
    book_id = r.json()["id"]

    # set to false twice
    for _ in range(2):
        rp = client.patch(
            f"/api/books/{book_id}",
            headers={"X-API-Key": "test-secret"},
            json={"available": False},
        )
        assert rp.status_code == 200

    rlist = client.get("/api/books")
    assert rlist.json()[0]["available"] is False


def test_toggle_still_works_but_requires_auth(client):
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "Book", "author": "Auth"},
    )
    book_id = r.json()["id"]

    # no auth
    rt = client.post(f"/api/books/{book_id}/toggle")
    assert rt.status_code == 401

    # with auth
    rt = client.post(f"/api/books/{book_id}/toggle", headers={"X-API-Key": "test-secret"})
    assert rt.status_code == 200


def test_delete_requires_auth_and_deletes(client):
    r = client.post(
        "/api/books",
        headers={"X-API-Key": "test-secret"},
        json={"title": "Book", "author": "Auth"},
    )
    book_id = r.json()["id"]

    rd = client.delete(f"/api/books/{book_id}")
    assert rd.status_code == 401

    rd = client.delete(f"/api/books/{book_id}", headers={"X-API-Key": "test-secret"})
    assert rd.status_code == 200

    rlist = client.get("/api/books")
    assert rlist.json() == []
