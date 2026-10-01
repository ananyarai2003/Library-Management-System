from tests.conftest import AUTH


def test_create_member_normalises_email(client):
    res = client.post("/api/members", json={"name": "Ada", "email": " ADA@Example.com "}, headers=AUTH)
    assert res.status_code == 201 and res.json()["email"] == "ada@example.com"


def test_invalid_email_rejected(client):
    assert client.post("/api/members", json={"name": "Ada", "email": "nope"}, headers=AUTH).status_code == 422


def test_duplicate_email_conflicts(client, member):
    res = client.post("/api/members", json={"name": "Other", "email": member["email"]}, headers=AUTH)
    assert res.status_code == 409


def test_edit_and_delete_member(client, member):
    res = client.patch(f"/api/members/{member['id']}", json={"name": "Ada L."}, headers=AUTH)
    assert res.json()["name"] == "Ada L."
    assert client.delete(f"/api/members/{member['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/api/members/{member['id']}").status_code == 404
