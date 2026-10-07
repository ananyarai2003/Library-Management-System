"""Fixtures: spawn real uvicorn servers (own temp SQLite DB each) and Playwright contexts.

Servers:
  open_server   - no LMS_API_KEY (writes open)
  keyed_server  - LMS_API_KEY=KEY, LMS_CORS_ORIGINS=ALLOWED_ORIGIN
  fresh_server  - function-scoped, empty DB
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

BACKEND = Path(__file__).resolve().parent.parent
KEY = "e2e-secret"
ALLOWED_ORIGIN = "https://lib.example"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, env_extra: dict):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmp.name) / "server.log"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LMS_")}
        env |= {"LMS_DB_PATH": str(Path(self._tmp.name) / "e2e.db")} | env_extra
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=BACKEND, env=env, stdout=self._log, stderr=subprocess.STDOUT,
        )
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + "/api/health", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("server did not start")

    def logs(self) -> str:
        self._log.flush()
        return self.log_path.read_text(errors="ignore")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()
        try:
            self._tmp.cleanup()
        except OSError:
            pass


@pytest.fixture(scope="session")
def open_server():
    s = Server({})
    yield s
    s.stop()


@pytest.fixture(scope="session")
def keyed_server():
    s = Server({"LMS_API_KEY": KEY, "LMS_CORS_ORIGINS": ALLOWED_ORIGIN, "LMS_LOAN_DAYS": "7"})
    yield s
    s.stop()


@pytest.fixture()
def fresh_server():
    s = Server({})
    yield s
    s.stop()


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


def _ctx(pw, server, headers=None):
    return pw.request.new_context(base_url=server.url, extra_http_headers=headers or {})


@pytest.fixture()
def api(pw, open_server):
    c = _ctx(pw, open_server)
    yield c
    c.dispose()


@pytest.fixture()
def anon(pw, keyed_server):
    c = _ctx(pw, keyed_server)
    yield c
    c.dispose()


@pytest.fixture()
def authed(pw, keyed_server):
    c = _ctx(pw, keyed_server, {"X-API-Key": KEY})
    yield c
    c.dispose()


@pytest.fixture(scope="session")
def browser(pw):
    b = pw.chromium.launch(headless=not os.environ.get("E2E_HEADED"), slow_mo=int(os.environ.get("E2E_SLOWMO", "0")))
    yield b
    b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context()
    p = ctx.new_page()
    yield p
    ctx.close()


def uid() -> str:
    return uuid.uuid4().hex[:8]


def mk_book(api, copies=1, **kw):
    body = {"title": f"Book-{uid()}", "author": "Auth", "total_copies": copies} | kw
    r = api.post("/api/books", data=body)
    assert r.status == 201, r.text()
    return r.json()


def mk_member(api, **kw):
    u = uid()
    body = {"name": f"Mem-{u}", "email": f"{u}@example.com"} | kw
    r = api.post("/api/members", data=body)
    assert r.status == 201, r.text()
    return r.json()
