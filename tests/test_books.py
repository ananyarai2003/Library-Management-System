from tests.conftest import AUTH


def test_create_and_get_book(client):
    res = client.post("/api/books", json={"title": "  Dune ", "author": "Herbert"}, headers=AUTH)
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "Dune"
    assert body["available"] is True and body["available_copies"] == 1
    assert client.get(f"/api/books/{body['id']}").json() == body


def test_validation_rejects_blank_title(client):
    res = client.post("/api/books", json={"title": "   ", "author": "x"}, headers=AUTH)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


def test_duplicate_isbn_conflicts(client):
    payload = {"title": "A", "author": "B", "isbn": "9780441013593"}
    assert client.post("/api/books", json=payload, headers=AUTH).status_code == 201
    assert client.post("/api/books", json=payload, headers=AUTH).status_code == 409


def test_search_matches_title_author_isbn_and_escapes_wildcards(client):
    client.post("/api/books", json={"title": "Dune", "author": "Herbert"}, headers=AUTH)
    client.post("/api/books", json={"title": "Emma", "author": "Austen", "isbn": "9781234567890"}, headers=AUTH)
    assert [b["title"] for b in client.get("/api/books", params={"q": "aust"}).json()] == ["Emma"]
    assert [b["title"] for b in client.get("/api/books", params={"q": "1234567"}).json()] == ["Emma"]
    assert client.get("/api/books", params={"q": "%"}).json() == []


def test_edit_book_partial_and_null_rejected(client, book):
    res = client.patch(f"/api/books/{book['id']}", json={"author": "F. Herbert"}, headers=AUTH)
    assert res.status_code == 200 and res.json()["author"] == "F. Herbert"
    assert res.json()["title"] == "Dune"
    assert client.patch(f"/api/books/{book['id']}", json={"title": None}, headers=AUTH).status_code == 422
    assert client.patch(f"/api/books/{book['id']}", json={"bogus": 1}, headers=AUTH).status_code == 422


def test_missing_book_is_404(client):
    assert client.get("/api/books/999").status_code == 404
    assert client.patch("/api/books/999", json={"title": "x"}, headers=AUTH).status_code == 404
    assert client.delete("/api/books/999", headers=AUTH).status_code == 404


def test_delete_book(client, book):
    assert client.delete(f"/api/books/{book['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/api/books/{book['id']}").status_code == 404
