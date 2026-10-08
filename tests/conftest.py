from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.config import Settings

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"

CREDENCIALES_DEMO = {"email": "ana.rios@example.com", "password": "clave-de-prueba-1"}


@pytest.fixture(scope="session")
def openapi_spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        adapters="fake",
        session_secret="test-secret-de-al-menos-32-bytes!!",
        fake_demo_email=CREDENCIALES_DEMO["email"],
        fake_demo_password=CREDENCIALES_DEMO["password"],
    )


@pytest_asyncio.fixture
async def app(settings):
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
