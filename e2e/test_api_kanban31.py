"""API tests mapped to KANBAN-31 checklist items (see plan IDs in test names)."""
import concurrent.futures as cf
import json
import time
import urllib.error
import urllib.request

import pytest

from conftest import ALLOWED_ORIGIN, KEY, mk_book, mk_member, uid

pytestmark = pytest.mark.api


def err(r):
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"message", "details"}
    return body["error"]


# ---------- Auth ----------
def test_auth_01_reads_public_with_key_configured(anon):
    for p in ("/api/health", "/api/books", "/api/members", "/api/loans"):
        assert anon.get(p).status == 200


@pytest.mark.parametrize("hdr", [None, "", "wrong", "e2e-secre", "e2e-secret-x"])
def test_auth_02_write_rejected_bad_key(pw, keyed_server, hdr):
    headers = {"X-API-Key": hdr} if hdr is not None else {}
    c = pw.request.new_context(base_url=keyed_server.url, extra_http_headers=headers)
    calls = [("post", "/api/books", {"title": "t", "author": "a"}),
             ("post", "/api/members", {"name": "n", "email": "a@b.co"}),
             ("post", "/api/loans", {"book_id": 1, "member_id": 1}),
             ("post", "/api/loans/1/return", None),
             ("patch", "/api/books/1/availability", {"available_copies": 0}),
             ("delete", "/api/books/1", None)]
    for method, path, body in calls:
        fn = getattr(c, method)
        r = fn(path, data=body) if body else fn(path)
        assert r.status == 401, (method, path, hdr, r.status)
        assert "key" in err(r)["message"].lower()
    c.dispose()


def test_auth_03_auth_wins_over_validation_and_404(anon):
    assert anon.post("/api/books", data={}).status == 401
    assert anon.delete("/api/books/999999").status == 401


def test_auth_04_valid_key_full_write_flow(authed):
    b = mk_book(authed, copies=2)
    m = mk_member(authed)
    loan = authed.post("/api/loans", data={"book_id": b["id"], "member_id": m["id"]})
    assert loan.status == 201
    assert authed.post(f"/api/loans/{loan.json()['id']}/return").status == 200
    assert authed.delete(f"/api/books/{b['id']}").status == 409


def test_auth_05_open_when_key_unset(api):
    assert api.post("/api/books", data={"title": "t", "author": "a"}).status == 201


# ---------- CORS ----------
def test_cors_01_allowed_origin_and_preflight(anon):
    r = anon.fetch("/api/books/1/availability", method="OPTIONS", headers={
        "Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "x-api-key,content-type"})
    assert r.status == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    methods = {m.strip() for m in r.headers["access-control-allow-methods"].split(",")}
    assert methods == {"GET", "POST", "PATCH", "DELETE"}


def test_cors_02_disallowed_origin_and_method(anon):
    r = anon.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    r = anon.fetch("/api/books", method="OPTIONS", headers={
        "Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "PUT"})
    assert r.status == 400


def test_cors_03_no_cors_headers_when_unconfigured(api):
    r = api.get("/api/health", headers={"Origin": ALLOWED_ORIGIN})
    assert "access-control-allow-origin" not in r.headers


# ---------- PATCH availability ----------
def test_avail_01_boundaries(api):
    b = mk_book(api, copies=3)
    url = f"/api/books/{b['id']}/availability"
    for n, avail in [(0, False), (3, True), (1, True)]:
        r = api.patch(url, data={"available_copies": n})
        assert r.status == 200
        j = r.json()
        assert j["available_copies"] == n and j["available"] is avail and j["total_copies"] == 3


@pytest.mark.parametrize("body,status", [
    ({"available_copies": 4}, 409), ({"available_copies": -1}, 422), ({}, 422),
    ({"available_copies": "x"}, 422), ({"available_copies": None}, 422),
    ({"available_copies": 1.5}, 422), ({"available_copies": 1001}, 422)])
def test_avail_02_invalid(api, body, status):
    b = mk_book(api, copies=3)
    r = api.patch(f"/api/books/{b['id']}/availability", data=body)
    assert r.status == status
    err(r)
    assert api.get("/api/books", params={"q": b["title"]}).json()[0]["available_copies"] == 3


def test_avail_03_unknown_and_bad_id(api):
    assert api.patch("/api/books/99999999/availability", data={"available_copies": 0}).status == 404
    assert api.patch("/api/books/abc/availability", data={"available_copies": 0}).status == 422


def test_avail_04_affects_borrow(api):
    b, m = mk_book(api, copies=2), mk_member(api)
    api.patch(f"/api/books/{b['id']}/availability", data={"available_copies": 0})
    assert api.post("/api/loans", data={"book_id": b["id"], "member_id": m["id"]}).status == 409
    api.patch(f"/api/books/{b['id']}/availability", data={"available_copies": 1})
    assert api.post("/api/loans", data={"book_id": b["id"], "member_id": m["id"]}).status == 201


def test_avail_05_return_after_override_must_not_500(api):
    """BUG-2: admin sets available=total while a loan is out, then the loan is returned (CHECK constraint)."""
    b, m = mk_book(api, copies=1), mk_member(api)
    loan = api.post("/api/loans", data={"book_id": b["id"], "member_id": m["id"]}).json()
    assert api.patch(f"/api/books/{b['id']}/availability", data={"available_copies": 1}).status == 200
    r = api.post(f"/api/loans/{loan['id']}/return")
    assert r.status < 500, f"return returned {r.status}"


# ---------- Pagination / filters ----------
@pytest.mark.parametrize("path", ["/api/books", "/api/members", "/api/loans"])
@pytest.mark.parametrize("params,status", [
    ({"limit": 0}, 422), ({"limit": 501}, 422), ({"limit": -1}, 422), ({"offset": -1}, 422),
    ({"limit": "abc"}, 422), ({"limit": 1}, 200), ({"limit": 500}, 200), ({"offset": 10**6}, 200)])
def test_page_01_param_validation(api, path, params, status):
    r = api.get(path, params=params)
    assert r.status == status
    if status == 422:
        assert err(r)["details"]


def test_page_02_default_100_and_max_500(fresh_server, pw):
    c = pw.request.new_context(base_url=fresh_server.url)
    for i in range(105):
        assert c.post("/api/books", data={"title": f"B{i}", "author": "a"}).status == 201
    assert len(c.get("/api/books").json()) == 100
    allb = c.get("/api/books", params={"limit": 500}).json()
    assert len(allb) == 105
    assert len(c.get("/api/books", params={"offset": 100}).json()) == 5
    ids = [b["id"] for b in allb]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 105
    c.dispose()


def test_page_03_pages_do_not_overlap(api):
    for _ in range(3):
        mk_book(api)
    a = api.get("/api/books", params={"limit": 2, "offset": 0}).json()
    b = api.get("/api/books", params={"limit": 2, "offset": 2}).json()
    assert not {x["id"] for x in a} & {x["id"] for x in b}


def test_filter_01_books_q_and_available(api):
    tag = uid()
    b1 = mk_book(api, title=f"Zed {tag} one")
    b2 = mk_book(api, title=f"Zed {tag} two", author="Someone")
    api.patch(f"/api/books/{b2['id']}/availability", data={"available_copies": 0})
    assert {b["id"] for b in api.get("/api/books", params={"q": tag}).json()} == {b1["id"], b2["id"]}
    assert [b["id"] for b in api.get("/api/books", params={"q": tag, "available": "true"}).json()] == [b1["id"]]
    assert [b["id"] for b in api.get("/api/books", params={"q": tag, "available": "false"}).json()] == [b2["id"]]
    assert api.get("/api/books", params={"q": "no-such-" + uid()}).json() == []
    assert api.get("/api/books", params={"available": "maybe"}).status == 422


def test_filter_02_q_by_isbn_and_case(api):
    isbn = "978" + str(int(uid(), 16)).zfill(10)[:10]
    b = mk_book(api, isbn=isbn)
    assert api.get("/api/books", params={"q": isbn}).json()[0]["id"] == b["id"]
    assert api.get("/api/books", params={"q": b["title"].upper()}).json()[0]["id"] == b["id"]


def test_filter_03_q_wildcards_should_be_literal(api):
    """BUG-3 (low): percent and underscore act as SQL LIKE wildcards, so q=percent matches every book."""
    mk_book(api, title=f"plain-{uid()}")
    r = api.get("/api/books", params={"q": "%"})
    assert r.status == 200 and r.json() == []


def test_filter_04_loans(api):
    b, m1, m2 = mk_book(api, copies=3), mk_member(api), mk_member(api)
    l1 = api.post("/api/loans", data={"book_id": b["id"], "member_id": m1["id"]}).json()
    l2 = api.post("/api/loans", data={"book_id": b["id"], "member_id": m2["id"]}).json()
    api.post(f"/api/loans/{l1['id']}/return")

    def ids(**p):
        return [x["id"] for x in api.get("/api/loans", params=p).json()]
    assert ids(book_id=b["id"]) == [l2["id"], l1["id"]]
    assert ids(book_id=b["id"], active="true") == [l2["id"]]
    assert ids(member_id=m1["id"], active="true") == []
    assert ids(member_id=m1["id"], book_id=b["id"]) == [l1["id"]]
    assert ids(member_id=m2["id"], book_id=b["id"] + 1000) == []
    assert ids(book_id=b["id"], limit=1, offset=1) == [l1["id"]]
    assert api.get("/api/loans", params={"member_id": "x"}).status == 422
    assert api.get("/api/loans", params={"active": "x"}).status == 422


# ---------- Error shape ----------
def test_err_01_shape_all_statuses(api, anon):
    assert err(api.get("/api/books", params={"limit": 0}))["details"][0].keys() >= {"loc", "msg"}
    assert err(api.post("/api/books", data={"title": " ", "author": "a"}))["details"]
    assert err(api.delete("/api/books/99999999"))["message"]
    assert err(anon.post("/api/books", data={}))["details"] == []
    assert err(api.post("/api/loans", data={"book_id": 0, "member_id": 1}))["details"]


def test_err_02_unknown_api_route_404(api):
    r = api.get("/api/does-not-exist")
    assert r.status == 404
    assert "error" in r.json()


def test_err_03_malformed_json_body(api):
    r = api.post("/api/books", headers={"Content-Type": "application/json"}, data="{not json")
    assert r.status == 422 and "error" in r.json()


# ---------- Domain regression ----------
def test_dom_01_book_validation(api):
    bad = [{"title": "t"}, {"title": "t", "author": "a", "total_copies": 0},
           {"title": "t", "author": "a", "total_copies": 1001},
           {"title": "x" * 201, "author": "a"}, {"title": "t", "author": "a", "isbn": "123"},
           {"title": "t", "author": "a", "isbn": "abcdefghij"}]
    for b in bad:
        assert api.post("/api/books", data=b).status == 422, b
    ok = api.post("/api/books", data={"title": "  trim  ", "author": "a", "isbn": "  "})
    assert ok.status == 201 and ok.json()["title"] == "trim" and ok.json()["isbn"] is None


def test_dom_02_member_email(api):
    u = uid()
    assert api.post("/api/members", data={"name": "n", "email": f"  {u}@EX.com "}).json()["email"] == f"{u}@ex.com"
    assert api.post("/api/members", data={"name": "n", "email": f"{u}@ex.com"}).status == 409
    assert api.post("/api/members", data={"name": "n", "email": "bad"}).status == 422
    assert api.post("/api/members", data={"name": "n" * 101, "email": f"{uid()}@ex.com"}).status == 422


def test_dom_03_loan_lifecycle(api):
    b, m = mk_book(api, copies=1), mk_member(api)
    loan = api.post("/api/loans", data={"book_id": b["id"], "member_id": m["id"]}).json()
    assert loan["returned_at"] is None and loan["overdue"] is False
    assert api.post("/api/loans", data={"book_id": b["id"], "member_id": m["id"]}).status == 409
    assert api.post(f"/api/loans/{loan['id']}/return").status == 200
    assert api.post(f"/api/loans/{loan['id']}/return").status == 409
    assert api.get("/api/books", params={"q": b["title"]}).json()[0]["available_copies"] == 1


def test_dom_04_duplicate_isbn_and_delete(api):
    isbn = "1234567" + str(int(uid(), 16)).zfill(6)[:6]
    b = mk_book(api, isbn=isbn)
    assert api.post("/api/books", data={"title": "t", "author": "a", "isbn": isbn}).status == 409
    assert api.delete(f"/api/books/{b['id']}").status == 204
    assert api.delete(f"/api/books/{b['id']}").status == 404


# ---------- Logging ----------
def test_log_01_request_id_echo_and_generation(api):
    assert api.get("/api/health", headers={"X-Request-ID": "rid-123"}).headers["x-request-id"] == "rid-123"
    a = api.get("/api/health").headers["x-request-id"]
    b = api.get("/api/health").headers["x-request-id"]
    assert a and b and a != b


def test_log_02_request_id_on_error_responses(api, anon):
    assert api.get("/api/books", params={"limit": 0}, headers={"X-Request-ID": "e1"}).headers["x-request-id"] == "e1"
    assert anon.post("/api/books", data={}, headers={"X-Request-ID": "e2"}).headers["x-request-id"] == "e2"


def test_log_03_json_log_line(api, open_server):
    rid = "log-" + uid()
    api.get("/api/health", headers={"X-Request-ID": rid})
    lines = []
    for _ in range(30):
        lines = [x for x in open_server.logs().splitlines() if rid in x]
        if lines:
            break
        time.sleep(0.1)
    assert lines, "no log line for request id"
    rec = json.loads(lines[-1])
    assert rec.keys() >= {"time", "level", "message", "request_id", "method", "path", "status", "duration_ms"}
    assert rec["status"] == 200 and rec["path"] == "/api/health" and rec["method"] == "GET"


def test_log_04_api_key_never_logged(anon, keyed_server):
    anon.post("/api/books", data={"title": "t", "author": "a"}, headers={"X-API-Key": KEY})
    assert KEY not in keyed_server.logs()


# ---------- DB ops ----------
def test_db_01_concurrent_borrow_no_overborrow(fresh_server, pw):
    c0 = pw.request.new_context(base_url=fresh_server.url)
    b = mk_book(c0, copies=1)
    members = [mk_member(c0) for _ in range(10)]
    c0.dispose()

    def borrow(mid):
        req = urllib.request.Request(
            fresh_server.url + "/api/loans", method="POST",
            data=json.dumps({"book_id": b["id"], "member_id": mid}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=15).status
        except urllib.error.HTTPError as e:
            return e.code
    with cf.ThreadPoolExecutor(10) as ex:
        codes = list(ex.map(borrow, [m["id"] for m in members]))
    assert sorted(codes) == [201] + [409] * 9, codes
