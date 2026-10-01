import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-key"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "test.db", api_key=API_KEY)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def book(client):
    return client.post("/api/books", json={"title": "Dune", "author": "Frank Herbert", "total_copies": 1}, headers=AUTH).json()


@pytest.fixture
def member(client):
    return client.post("/api/members", json={"name": "Ada", "email": "ada@example.com"}, headers=AUTH).json()
