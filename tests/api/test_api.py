import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from services.api.main import APP_NAME, APP_VERSION, create_app
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import BarsQuery, BarsResult
from services.market_data.storage import AmbiguousRevisionError

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


async def test_configured_data_directory_persists_api_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIW_DATA_DIR", str(tmp_path))
    cached_app = create_app()
    params = {
        "instrument_id": "us:aapl:xnas:2026",
        "start_utc": "2026-10-09T13:30:00Z",
        "end_utc": "2026-10-09T13:34:00Z",
    }

    async with AsyncClient(
        transport=ASGITransport(app=cached_app), base_url="http://test"
    ) as test_client:
        first = await test_client.get("/v1/market/bars", params=params)
        second = await test_client.get("/v1/market/bars", params=params)

    assert first.status_code == 200
    assert second.json() == first.json()
    assert len(list(tmp_path.rglob("*.parquet"))) == 1
    assert (tmp_path / "metadata.sqlite3").is_file()


class _RaisingProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error

    @property
    def source_id(self) -> str:
        return "raising"

    def capabilities(self) -> dict[str, Any]:
        return {}

    def get_bars(self, query: BarsQuery) -> BarsResult:
        raise self.error


def _raising_client(error: Exception) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(
            app=create_app(_RaisingProvider(error)), raise_app_exceptions=False
        ),
        base_url="http://test",
    )


BARS_PARAMS = {
    "instrument_id": "us:aapl:xnas:2026",
    "start_utc": "2026-10-09T13:30:00Z",
    "end_utc": "2026-10-09T13:34:00Z",
}


@pytest.mark.parametrize("year", [1990, 2099])
async def test_market_bars_outside_calendar_range_is_query_error(year: int) -> None:
    async with client() as test_client:
        response = await test_client.get(
            "/v1/market/bars",
            params={
                **BARS_PARAMS,
                "start_utc": f"{year}-01-03T15:00:00Z",
                "end_utc": f"{year}-01-03T15:05:00Z",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_market_data_query"


async def test_market_bars_storage_failure_uses_error_envelope() -> None:
    async with _raising_client(AmbiguousRevisionError("internal detail")) as test_client:
        response = await test_client.get(
            "/v1/market/bars", params=BARS_PARAMS, headers={"X-Request-ID": "storage"}
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "market_data_storage_error",
            "message": "Local market data cache could not serve the request",
            "request_id": "storage",
        }
    }


async def test_unexpected_failure_uses_error_envelope_without_details() -> None:
    async with _raising_client(RuntimeError("secret internal detail")) as test_client:
        response = await test_client.get(
            "/v1/market/bars", params=BARS_PARAMS, headers={"X-Request-ID": "boom"}
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": "boom",
        }
    }
    assert response.headers["X-Request-ID"] == "boom"


async def test_blocking_market_data_call_does_not_stall_other_requests() -> None:
    started = threading.Event()
    release = threading.Event()
    released_before_timeout: list[bool] = []
    upstream = FakeMarketDataProvider.from_path()

    class BlockingProvider:
        source_id = upstream.source_id

        def capabilities(self) -> dict[str, Any]:
            return upstream.capabilities()

        def get_bars(self, query: BarsQuery) -> BarsResult:
            started.set()
            released_before_timeout.append(release.wait(timeout=5))
            return upstream.get_bars(query)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(BlockingProvider())), base_url="http://test"
    ) as test_client:
        bars_request = asyncio.create_task(test_client.get("/v1/market/bars", params=BARS_PARAMS))
        assert await asyncio.to_thread(started.wait, 5)
        health = await test_client.get("/health")
        release.set()
        bars = await bars_request

    assert health.status_code == 200
    assert bars.status_code == 200
    assert released_before_timeout == [True]
