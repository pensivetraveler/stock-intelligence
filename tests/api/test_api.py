import pytest
from httpx import ASGITransport, AsyncClient

from services.api.main import APP_NAME, APP_VERSION, create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


async def test_health_is_liveness_only() -> None:
    async with client() as test_client:
        response = await test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


async def test_version_contract() -> None:
    async with client() as test_client:
        response = await test_client.get("/version")

    assert response.status_code == 200
    assert response.json() == {
        "name": APP_NAME,
        "api_version": "v1",
        "app_version": APP_VERSION,
    }


async def test_not_found_uses_error_envelope() -> None:
    async with client() as test_client:
        response = await test_client.get("/missing", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Resource not found",
            "request_id": "test-request",
        }
    }
    assert response.headers["X-Request-ID"] == "test-request"


async def test_cors_rejects_unknown_origin() -> None:
    async with client() as test_client:
        response = await test_client.get("/health", headers={"Origin": "https://example.com"})

    assert "access-control-allow-origin" not in response.headers


async def test_cors_allows_local_vite_origin() -> None:
    async with client() as test_client:
        response = await test_client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})

    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
