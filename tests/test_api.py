BOOK = {"title": "Dune", "author": "Frank Herbert", "isbn": "978-0441013593", "total_copies": 1}
MEMBER = {"name": "Ada", "email": "ada@example.com"}


def make(client):
    book = client.post("/api/books", json=BOOK).json()
    member = client.post("/api/members", json=MEMBER).json()
    return book, member


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_book_crud_and_search(client):
    r = client.post("/api/books", json=BOOK)
    assert r.status_code == 201 and r.json()["available"] is True
    assert len(client.get("/api/books", params={"q": "herb"}).json()) == 1
    assert client.get("/api/books", params={"q": "zzz"}).json() == []
    assert client.delete(f"/api/books/{r.json()['id']}").status_code == 204
    assert client.delete("/api/books/999").status_code == 404


def test_book_validation(client):
    r = client.post("/api/books", json={"title": " ", "author": "x"})
    assert r.status_code == 422 and r.json()["error"]["details"]
    assert client.post("/api/books", json=BOOK | {"isbn": "abcdefghijk"}).status_code == 422
    assert client.post("/api/books", json=BOOK | {"total_copies": 0}).status_code == 422


def test_duplicate_isbn_conflict(client):
    client.post("/api/books", json=BOOK)
    assert client.post("/api/books", json=BOOK).status_code == 409


def test_member_validation_and_duplicate(client):
    assert client.post("/api/members", json={"name": "A", "email": "bad"}).status_code == 422
    assert client.post("/api/members", json=MEMBER).status_code == 201
    assert client.post("/api/members", json=MEMBER).status_code == 409


def test_loan_lifecycle(client):
    book, member = make(client)
    payload = {"book_id": book["id"], "member_id": member["id"]}
    r = client.post("/api/loans", json=payload)
    assert r.status_code == 201 and r.json()["overdue"] is False
    loan_id = r.json()["id"]
    assert client.get("/api/books").json()[0]["available_copies"] == 0
    assert client.post("/api/loans", json=payload).status_code == 409
    assert len(client.get("/api/loans", params={"active": "true"}).json()) == 1
    assert client.delete(f"/api/books/{book['id']}").status_code == 409
    assert client.post(f"/api/loans/{loan_id}/return").status_code == 200
    assert client.post(f"/api/loans/{loan_id}/return").status_code == 409
    assert client.get("/api/books").json()[0]["available_copies"] == 1
    assert client.get("/api/loans", params={"active": "true"}).json() == []


def test_loan_unknown_refs(client):
    assert client.post("/api/loans", json={"book_id": 1, "member_id": 1}).status_code == 404
    assert client.post("/api/loans/42/return").status_code == 404


def test_api_key_required_for_writes(client, monkeypatch):
    monkeypatch.setenv("LMS_API_KEY", "secret")
    assert client.get("/api/books").status_code == 200
    assert client.post("/api/books", json=BOOK).status_code == 401
    assert client.post("/api/books", json=BOOK, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/books", json=BOOK, headers={"X-API-Key": "secret"}).status_code == 201


def test_transaction_rolls_back_on_error(client):
    from app.db import read_connection, transaction

    try:
        with transaction() as conn:
            conn.execute("INSERT INTO members (name, email) VALUES ('x', 'x@x.io')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with read_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0


def test_frontend_served(client):
    assert "Library Management System" in client.get("/").text
