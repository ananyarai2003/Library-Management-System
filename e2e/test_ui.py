"""UI tests against the frontend served by the backend at /. Selectors are the element ids in index.html."""
import pytest
from playwright.sync_api import expect

from conftest import KEY, uid

pytestmark = pytest.mark.ui


def row(page, text):
    return page.locator("#book-list tr", has_text=text)


def add_book(page, title, author="Au", isbn="", copies=None):
    page.fill("#title", title)
    page.fill("#author", author)
    page.fill("#isbn", isbn)
    if copies is not None:
        page.fill("#copies", str(copies))
    page.click("#book-form button[type=submit]")


def add_member(page, name, email):
    page.fill("#member-name", name)
    page.fill("#member-email", email)
    page.click("#member-form button[type=submit]")
    expect(page.locator("#member-list tr", has_text=email)).to_be_visible()


def test_ui_01_empty_states(page, fresh_server):
    page.goto(fresh_server.url)
    expect(page).to_have_title("Library Management System")
    expect(page.locator("#books-empty")).to_be_visible()
    expect(page.locator("#members-empty")).to_be_visible()
    expect(page.locator("#loans-empty")).to_be_visible()
    expect(page.locator("#error")).to_be_hidden()


def test_ui_02_add_borrow_return(page, open_server):
    t, u = f"UI-{uid()}", uid()
    page.goto(open_server.url)
    add_book(page, t, copies=1)
    expect(row(page, t)).to_contain_text("1/1 available")
    add_member(page, f"Name {u}", f"{u}@example.com")
    page.select_option("#loan-book", label=t)
    page.select_option("#loan-member", label=f"Name {u} ({u}@example.com)")
    page.click("#loan-form button[type=submit]")
    expect(row(page, t)).to_contain_text("0/1 available")
    loan = page.locator("#loan-list tr", has_text=t)
    expect(loan).to_contain_text("On loan")
    expect(page.locator("#loan-book option", has_text=t)).to_have_count(0)
    loan.get_by_role("button", name="Return").click()
    expect(page.locator("#loan-list tr", has_text=t)).to_have_count(0)
    expect(row(page, t)).to_contain_text("1/1 available")


def test_ui_03_search(page, fresh_server):
    page.goto(fresh_server.url)
    add_book(page, "Alpha")
    expect(row(page, "Alpha")).to_be_visible()
    add_book(page, "Beta")
    expect(row(page, "Beta")).to_be_visible()
    page.fill("#search", "Alpha")
    page.press("#search", "Enter")
    expect(page.locator("#book-list tr")).to_have_count(1)
    page.fill("#search", "nothing-" + uid())
    page.press("#search", "Enter")
    expect(page.locator("#books-empty")).to_be_visible()


def test_ui_04_server_validation_errors_shown(page, open_server):
    page.goto(open_server.url)
    t = f"Dup-{uid()}"
    isbn = "9" + str(int(uid(), 16)).zfill(9)[:9]
    add_book(page, t, isbn=isbn)
    expect(row(page, t)).to_be_visible()
    add_book(page, t + "x", isbn=isbn)
    expect(page.locator("#error")).to_contain_text("ISBN already exists")
    page.fill("#title", "   ")
    page.fill("#author", "a")
    page.click("#book-form button[type=submit]")
    expect(page.locator("#error")).to_contain_text("blank")


def test_ui_05_delete_confirm_and_cancel(page, open_server):
    t = f"Del-{uid()}"
    page.goto(open_server.url)
    add_book(page, t)
    expect(row(page, t)).to_be_visible()
    page.once("dialog", lambda d: d.dismiss())
    row(page, t).get_by_role("button", name="Delete").click()
    expect(row(page, t)).to_be_visible()
    page.once("dialog", lambda d: d.accept())
    row(page, t).get_by_role("button", name="Delete").click()
    expect(row(page, t)).to_have_count(0)


def test_ui_06_delete_book_with_history_shows_409(page, open_server):
    t, u = f"Hist-{uid()}", uid()
    page.goto(open_server.url)
    add_book(page, t)
    add_member(page, "H " + u, f"{u}@example.com")
    page.select_option("#loan-book", label=t)
    page.select_option("#loan-member", label=f"H {u} ({u}@example.com)")
    page.click("#loan-form button[type=submit]")
    page.locator("#loan-list tr", has_text=t).get_by_role("button", name="Return").click()
    expect(page.locator("#loan-list tr", has_text=t)).to_have_count(0)
    page.once("dialog", lambda d: d.accept())
    row(page, t).get_by_role("button", name="Delete").click()
    expect(page.locator("#error")).to_contain_text("loan records")


def test_ui_07_api_key_flow(page, keyed_server):
    t = f"Key-{uid()}"
    page.goto(keyed_server.url)
    expect(page.locator("#api-key")).to_have_attribute("type", "password")
    add_book(page, t)
    expect(page.locator("#error")).to_contain_text("Invalid or missing API key")
    expect(row(page, t)).to_have_count(0)
    page.fill("#api-key", "wrong")
    page.click("#book-form button[type=submit]")
    expect(page.locator("#error")).to_contain_text("Invalid or missing API key")
    page.fill("#api-key", KEY)
    page.click("#book-form button[type=submit]")
    expect(row(page, t)).to_be_visible()
    expect(page.locator("#error")).to_be_hidden()
    page.reload()
    expect(page.locator("#api-key")).to_have_value(KEY)
    expect(row(page, t)).to_be_visible()


def test_ui_08_xss_rendered_as_text(page, open_server):
    t = f"<img src=x onerror=window.__x=1> {uid()}"
    page.goto(open_server.url)
    add_book(page, t)
    expect(row(page, "<img src=x")).to_be_visible()
    assert page.evaluate("window.__x") is None


def test_ui_09_no_console_errors(page, open_server):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(open_server.url)
    expect(page.locator("#book-form")).to_be_visible()
    assert errors == []


def test_ui_10_member_name_maxlength_matches_api(page, open_server):
    """BUG-4: HTML maxlength=200 but API rejects names over 100 chars."""
    page.goto(open_server.url)
    expect(page.locator("#member-name")).to_have_attribute("maxlength", "100")


def test_ui_11_more_than_100_books_visible(page, fresh_server, pw):
    """BUG-1: UI never sends limit/offset, so with >100 books the list and borrow dropdown truncate silently."""
    c = pw.request.new_context(base_url=fresh_server.url)
    for i in range(101):
        c.post("/api/books", data={"title": f"Bulk {i:03d}", "author": "a"})
    c.dispose()
    page.goto(fresh_server.url)
    expect(page.locator("#book-list tr")).to_have_count(101)
