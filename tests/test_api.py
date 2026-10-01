import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def _test_db(tmp_path, monkeypatch):
    # Ensure each test run uses an isolated DB file
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("LIBRARY_DB_PATH", str(db_path))

    # Set API key to enforce auth in tests
    monkeypatch.setenv("LIBRARY_API_KEY", "test-key")

    # Re-evaluate DB path used by main module
    main.DB_PATH = Path(os.getenv("LIBRARY_DB_PATH"))

    # Init schema
    main.init_db()

    yield


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_write_requires_auth(client):
    r = client.post("/api/books", json={"title": "A", "author": "B"})
    assert r.status_code == 401


def test_add_list_update_toggle_delete_flow(client):
    headers = {"X-API-Key": "test-key"}

    create = client.post("/api/books", json={"title": "  The  Hobbit ", "author": "  Tolkien  "}, headers=headers)
    assert create.status_code == 201
    created = create.json()
    assert created["title"] == "The Hobbit"
    assert created["author"] == "Tolkien"
    assert created["available"] is True

    # list
    lst = client.get("/api/books")
    assert lst.status_code == 200
    assert any(b["id"] == created["id"] for b in lst.json())

    # search
    search = client.get("/api/books", params={"q": "Hobbit"})
    assert search.status_code == 200
    assert len(search.json()) == 1

    # update
    upd = client.put(
        f"/api/books/{created['id']}",
        json={"title": "The Hobbit: Revised", "available": False},
        headers=headers,
    )
    assert upd.status_code == 200
    assert upd.json()["title"] == "The Hobbit: Revised"
    assert upd.json()["available"] is False

    # filter
    only_unavailable = client.get("/api/books", params={"available": "false"})
    assert only_unavailable.status_code == 200
    assert len(only_unavailable.json()) == 1

    # toggle
    t = client.post(f"/api/books/{created['id']}/toggle", headers=headers)
    assert t.status_code == 200

    # delete
    d = client.delete(f"/api/books/{created['id']}", headers=headers)
    assert d.status_code == 200

    # now 404 on delete
    d2 = client.delete(f"/api/books/{created['id']}", headers=headers)
    assert d2.status_code == 404


def test_validation_rejects_empty_title(client):
    headers = {"X-API-Key": "test-key"}
    r = client.post("/api/books", json={"title": "   ", "author": "A"}, headers=headers)
    assert r.status_code == 422
