import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LMS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("LMS_API_KEY", raising=False)
    init_db()
    with TestClient(create_app()) as c:
        yield c
