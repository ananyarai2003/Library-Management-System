from app.db import connect
from tests.conftest import AUTH


def borrow(client, book, member):
    return client.post("/api/loans", json={"book_id": book["id"], "member_id": member["id"]}, headers=AUTH)


def test_borrow_and_return_cycle(client, book, member):
    res = borrow(client, book, member)
    assert res.status_code == 201
    loan = res.json()
    assert loan["returned_at"] is None and loan["overdue"] is False
    assert client.get(f"/api/books/{book['id']}").json()["available"] is False

    returned = client.post(f"/api/loans/{loan['id']}/return", headers=AUTH)
    assert returned.status_code == 200 and returned.json()["returned_at"]
    assert client.get(f"/api/books/{book['id']}").json()["available"] is True


def test_cannot_borrow_without_free_copies(client, book, member):
    assert borrow(client, book, member).status_code == 201
    assert borrow(client, book, member).status_code == 409


def test_multiple_copies(client, member):
    book = client.post("/api/books", json={"title": "T", "author": "A", "total_copies": 2}, headers=AUTH).json()
    assert borrow(client, book, member).status_code == 201
    assert client.get(f"/api/books/{book['id']}").json()["available_copies"] == 1


def test_double_return_conflicts(client, book, member):
    loan = borrow(client, book, member).json()
    client.post(f"/api/loans/{loan['id']}/return", headers=AUTH)
    assert client.post(f"/api/loans/{loan['id']}/return", headers=AUTH).status_code == 409


def test_unknown_book_member_or_loan_is_404(client, book, member):
    assert client.post("/api/loans", json={"book_id": 999, "member_id": member["id"]}, headers=AUTH).status_code == 404
    assert client.post("/api/loans", json={"book_id": book["id"], "member_id": 999}, headers=AUTH).status_code == 404
    assert client.post("/api/loans/999/return", headers=AUTH).status_code == 404


def test_list_filters_and_overdue(client, settings, book, member):
    loan = borrow(client, book, member).json()
    assert len(client.get("/api/loans", params={"active": True}).json()) == 1
    assert client.get("/api/loans", params={"member_id": 999}).json() == []

    conn = connect(settings.db_path)
    conn.execute("UPDATE loans SET due_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", loan["id"]))
    conn.commit()
    conn.close()
    assert client.get("/api/loans").json()[0]["overdue"] is True


def test_cannot_delete_book_or_member_with_history(client, book, member):
    borrow(client, book, member)
    assert client.delete(f"/api/books/{book['id']}", headers=AUTH).status_code == 409
    assert client.delete(f"/api/members/{member['id']}", headers=AUTH).status_code == 409
    assert client.patch(f"/api/books/{book['id']}", json={"total_copies": 1}, headers=AUTH).status_code == 200
