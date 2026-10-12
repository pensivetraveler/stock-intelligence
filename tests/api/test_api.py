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


async def test_market_bars_returns_canonical_fixture_and_coverage() -> None:
    params = {
        "instrument_id": "us:aapl:xnas:2026",
        "start_utc": "2026-10-09T13:30:00Z",
        "end_utc": "2026-10-09T13:34:00Z",
        "timeframe": "1m",
    }
    async with client() as test_client:
        first = await test_client.get("/v1/market/bars", params=params)
        second = await test_client.get("/v1/market/bars", params=params)

    assert first.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["bars"]) == 4
    assert first.json()["coverage"]["coverage_status"] == "complete"
    assert first.json()["coverage"]["dataset_revision"] == "fake-aapl-20261009-r1"


async def test_market_bars_rejects_unknown_instrument() -> None:
    async with client() as test_client:
        response = await test_client.get(
            "/v1/market/bars",
            params={
                "instrument_id": "us:missing:xnas:2026",
                "start_utc": "2026-10-09T13:30:00Z",
                "end_utc": "2026-10-09T13:34:00Z",
            },
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "instrument_not_found"


async def test_market_bars_rejects_invalid_timeframe_and_range() -> None:
    base_params = {
        "instrument_id": "us:aapl:xnas:2026",
        "start_utc": "2026-10-09T13:30:00Z",
        "end_utc": "2026-10-09T13:34:00Z",
    }
    async with client() as test_client:
        timeframe_response = await test_client.get(
            "/v1/market/bars", params={**base_params, "timeframe": "5m"}
        )
        range_response = await test_client.get(
            "/v1/market/bars",
            params={**base_params, "end_utc": "2026-10-09T13:30:00Z"},
        )

    assert timeframe_response.status_code == 422
    assert timeframe_response.json()["error"]["code"] == "validation_error"
    assert range_response.status_code == 422
    assert range_response.json()["error"]["code"] == "invalid_market_data_query"


async def test_market_bars_rejects_future_cutoff() -> None:
    async with client() as test_client:
        response = await test_client.get(
            "/v1/market/bars",
            params={
                "instrument_id": "us:aapl:xnas:2026",
                "start_utc": "2026-10-09T13:30:00Z",
                "end_utc": "2026-10-09T13:34:00Z",
                "as_of_utc": "2999-01-01T00:00:00Z",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_market_data_query"
