import json

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        jwt_secret="test-only-signing-secret-at-least-32-characters",
        auth_limit_per_minute=100,
        search_limit_per_minute=1000,
        catalog_mode="demo",
        fetch_exchange_rates=False,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def sample_catalog():
    return json.loads((BACKEND_DIR / "app" / "data" / "catalog.json").read_text(encoding="utf-8"))


@pytest.fixture
def register():
    def signup(client, email="shopper@example.com"):
        response = client.post(
            "/api/auth/signup", json={"name": "Test Shopper", "email": email, "password": "TestPass123!"}
        )
        assert response.status_code == 201, response.text
        return response.json()["user"]

    return signup
