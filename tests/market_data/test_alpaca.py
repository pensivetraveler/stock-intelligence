"""Alpaca adapter tests against responses shaped like the documented API.

The payloads follow docs.alpaca.markets `GET /v2/stocks/bars`; they are not captured
from a live account. No test reaches the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from packages.contracts.validator import validate_document
from services.collector.backfill import BackfillRequest, run_backfill
from services.market_data.alpaca import AlpacaConfig, AlpacaConfigError, AlpacaMarketDataProvider
from services.market_data.bars_service import MarketBarsService
from services.market_data.provider import (
    BarsQuery,
    InstrumentNotFoundError,
    InvalidBarsQueryError,
    ProviderAuthError,
    ProviderDataError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from services.market_data.selection import ProviderConfigError, provider_from_env
from services.market_data.storage import CachedMarketDataProvider, LocalMarketDataStore

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)  # 11:00 ET
SPY = "us:spy:arcx:2026"
SECRET = "test-secret-not-real"


def config(**changes: Any) -> AlpacaConfig:
    values: dict[str, Any] = {"key_id": "test-key-id", "secret_key": SECRET}
    values.update(changes)
    return AlpacaConfig(**values)


def bar(
    minute: str,
    o: str = "450.10",
    h: str = "450.50",
    low: str = "449.90",
    c: str = "450.25",
    v: int = 1200,
) -> str:
    # Prices are bare JSON numbers, as Alpaca sends them; the adapter must not round them.
    fields = f'"o":{o},"h":{h},"l":{low},"c":{c},"v":{v},"n":12,"vw":450.2'
    return f'{{"t":"2026-10-09T{minute}:00Z",{fields}}}'


def page(*bars: str, token: str | None = None) -> str:
    next_token = "null" if token is None else f'"{token}"'
    return (
        f'{{"bars":{{"SPY":[{",".join(bars)}]}},"next_page_token":{next_token},"currency":"USD"}}'
    )


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def provider(recorder: Recorder, **changes: Any) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        config(**changes), transport=httpx.MockTransport(recorder), now=lambda: NOW
    )


def query(start: str, end: str, **extra: Any) -> BarsQuery:
    day = "2026-10-09T"
    return BarsQuery(
        SPY,
        "1m",
        datetime.fromisoformat(f"{day}{start}:00+00:00"),
        datetime.fromisoformat(f"{day}{end}:00+00:00"),
        **extra,
    )


def ok(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"Content-Type": "application/json"})


def test_bars_map_to_canonical_contract_with_exact_decimals() -> None:
    recorder = Recorder(ok(page(bar("13:30"), bar("13:31", o="450.25", c="450.4"), bar("13:33"))))

    result = provider(recorder).get_bars(query("13:30", "13:35"))

    validate_document(result.to_document())
    first = result.bars[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (
        "450.10",
        "450.50",
        "449.90",
        "450.25",
        "1200",
    )
    assert (first["session"], first["is_final"], first["quality"]) == ("regular", True, "complete")
    assert first["available_at_utc"] == "2026-10-09T13:46:00Z"
    assert first["source_feed_scope"] == "alpaca-sip"
    gaps = {gap["start_utc"][11:16]: gap["reason"] for gap in result.coverage["missing_intervals"]}
    assert gaps == {"13:32": "provider_no_bar", "13:34": "provider_no_bar"}


def test_request_uses_documented_endpoint_headers_and_parameters() -> None:
    recorder = Recorder(ok(page()))

    provider(recorder).get_bars(query("13:30", "13:35"))

    request = recorder.requests[0]
    assert request.url.path == "/v2/stocks/bars"
    assert request.headers["APCA-API-KEY-ID"] == "test-key-id"
    assert request.headers["APCA-API-SECRET-KEY"] == SECRET
    assert dict(request.url.params) == {
        "symbols": "SPY",
        "timeframe": "1Min",
        "start": "2026-10-09T13:30:00Z",
        "end": "2026-10-09T13:35:00Z",
        "limit": "10000",
        "adjustment": "raw",
        "feed": "sip",
        "sort": "asc",
    }


def test_free_plan_sip_never_requests_the_latest_fifteen_minutes() -> None:
    recorder = Recorder(ok(page(bar("14:40"))))

    result = provider(recorder).get_bars(query("14:40", "14:50"))

    assert recorder.requests[0].url.params["end"] == "2026-10-09T14:45:00Z"
    reasons = {
        gap["start_utc"][11:16]: gap["reason"] for gap in result.coverage["missing_intervals"]
    }
    assert reasons["14:44"] == "provider_no_bar"
    assert {reasons[f"14:{m}"] for m in range(45, 50)} == {"outside_entitlement"}


def test_iex_recent_bars_are_provisional_and_recent_gaps_are_not_settled() -> None:
    recorder = Recorder(ok(page(bar("14:55"))))

    result = provider(recorder, feed="iex", publication_delay_minutes=0).get_bars(
        query("14:50", "15:00")
    )

    assert result.bars[0]["is_final"] is False
    assert result.bars[0]["quality"] == "provisional"
    assert {gap["reason"] for gap in result.coverage["missing_intervals"]} == {"not_yet_final"}


def test_pagination_follows_next_page_token() -> None:
    recorder = Recorder(ok(page(bar("13:30"), token="next-1")), ok(page(bar("13:31"))))

    result = provider(recorder).get_bars(query("13:30", "13:32"))

    assert len(result.bars) == 2
    assert recorder.requests[1].url.params["page_token"] == "next-1"


def test_point_in_time_cutoff_hides_bars_not_yet_published() -> None:
    recorder = Recorder(ok(page(bar("13:30"), bar("13:31"))))
    cutoff = datetime(2026, 10, 9, 13, 46, tzinfo=timezone.utc)

    result = provider(recorder).get_bars(query("13:30", "13:32", as_of_utc=cutoff))

    assert [item["interval_start_utc"][11:16] for item in result.bars] == ["13:30"]
    assert result.coverage["missing_intervals"][0]["reason"] == "not_available_as_of_cutoff"


@pytest.mark.parametrize(
    ("response", "error", "fragment"),
    [
        (httpx.Response(401, text='{"message":"unauthorized."}'), ProviderAuthError, "401"),
        (
            httpx.Response(
                403, text='{"message":"subscription does not permit querying recent SIP data"}'
            ),
            ProviderAuthError,
            "recent SIP",
        ),
        (httpx.Response(429, text="{}"), ProviderRateLimitError, "rate limit"),
        (httpx.Response(503, text="down"), ProviderUnavailableError, "503"),
        (httpx.ReadTimeout("slow"), ProviderUnavailableError, "in time"),
        (ok("not json"), ProviderDataError, "not JSON"),
        (ok(page(bar("13:30"), bar("13:30"))), ProviderDataError, "two bars"),
        (ok(page(bar("13:30", h="440.00"))), ProviderDataError, "contract"),
    ],
)
def test_provider_failures_are_typed_and_never_echo_secrets(
    response: httpx.Response | Exception, error: type[Exception], fragment: str
) -> None:
    with pytest.raises(error, match=fragment) as raised:
        provider(Recorder(response)).get_bars(query("13:30", "13:35"))

    assert SECRET not in str(raised.value)


def test_queries_are_validated_before_any_request() -> None:
    recorder = Recorder()
    alpaca = provider(recorder)

    with pytest.raises(InstrumentNotFoundError):
        alpaca.get_bars(BarsQuery("us:zzzz:xnas:2026", "1m", NOW - timedelta(hours=1), NOW))
    with pytest.raises(InvalidBarsQueryError, match="1m"):
        alpaca.get_bars(BarsQuery(SPY, "5m", NOW - timedelta(hours=1), NOW))
    with pytest.raises(InvalidBarsQueryError, match="future"):
        alpaca.get_bars(query("13:30", "13:35", as_of_utc=NOW + timedelta(minutes=1)))
    assert recorder.requests == []


def test_no_trade_minutes_do_not_make_aggregates_partial() -> None:
    recorder = Recorder(ok(page(bar("13:30"), bar("13:31"), bar("13:33"), bar("13:34"))))

    window = query("13:30", "13:40")
    result = MarketBarsService(provider(recorder)).get_bars(
        BarsQuery(SPY, "5m", window.start_utc, window.end_utc)
    )

    [bucket] = result.bars
    assert (bucket["quality"], bucket["is_final"]) == ("complete", True)
    assert bucket["lineage"]["expected_source_bar_count"] == 4
    assert result.coverage["missing_intervals"] == [
        {
            "start_utc": "2026-10-09T13:35:00Z",
            "end_utc": "2026-10-09T13:40:00Z",
            "reason": "provider_no_bar",
        }
    ]


def test_cache_reuses_results_whose_gaps_are_settled_no_trade_minutes(tmp_path: Path) -> None:
    recorder = Recorder(ok(page(bar("13:30"), bar("13:32"))))
    with LocalMarketDataStore(tmp_path) as store:
        cached = CachedMarketDataProvider(provider(recorder), store)
        first = cached.get_bars(query("13:30", "13:33"))
        second = cached.get_bars(query("13:30", "13:33"))

    assert len(recorder.requests) == 1
    assert second.to_document() == first.to_document()


def test_backfill_fails_fast_on_rejected_credentials(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(401, text='{"message":"unauthorized."}'))
    request = BackfillRequest(
        SPY, query("13:30", "13:35").start_utc, query("13:30", "13:35").end_utc, max_attempts=3
    )
    with LocalMarketDataStore(tmp_path) as store:
        with pytest.raises(ProviderAuthError):
            run_backfill(store, provider(recorder), request, sleep=lambda _seconds: None)
        job = store.enqueue_job(
            BarsQuery(SPY, "1m", request.start_utc, request.end_utc), "alpaca-sip"
        )
        state = store.job_state(job)

    assert len(recorder.requests) == 1
    assert (state["status"], state["error_code"]) == ("failed", "provider_auth")


def test_config_comes_from_environment_without_leaking_secrets() -> None:
    env = {
        "APCA_API_KEY_ID": "key",
        "APCA_API_SECRET_KEY": SECRET,
        "SIW_ALPACA_FEED": "IEX",
        "SIW_ALPACA_SYMBOLS": "spy:arcx, brk.b:xnys",
    }

    loaded = AlpacaConfig.from_env(env)

    assert loaded.feed == "iex"
    assert loaded.publication_delay_minutes == 0
    assert loaded.symbols == (("SPY", "ARCX"), ("BRK.B", "XNYS"))
    assert SECRET not in repr(loaded)
    with pytest.raises(AlpacaConfigError, match="APCA_API_KEY_ID and APCA_API_SECRET_KEY"):
        AlpacaConfig.from_env({})
    with pytest.raises(AlpacaConfigError, match="SIW_ALPACA_FEED"):
        AlpacaConfig.from_env({**env, "SIW_ALPACA_FEED": "otc"})
    with pytest.raises(AlpacaConfigError, match="SYMBOL:MIC"):
        AlpacaConfig.from_env({**env, "SIW_ALPACA_SYMBOLS": "SPY:NYSE-ARCA"})


def test_capabilities_and_instruments_follow_the_contract() -> None:
    alpaca = provider(Recorder())

    validate_document(alpaca.capabilities())
    assert alpaca.capabilities()["verification_status"] == "integration_tested"
    assert alpaca.capabilities()["entitlement_status"] == "unknown"
    for item in alpaca.instruments():
        validate_document(item["instrument"])
    assert [item["instrument"]["instrument_id"] for item in alpaca.instruments()] == [
        "us:spy:arcx:2026",
        "us:aapl:xnas:2026",
        "us:msft:xnas:2026",
    ]


def test_provider_selection_defaults_to_synthetic_and_requires_alpaca_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("SIW_MARKET_DATA_PROVIDER", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert provider_from_env().source_id == "fake-market-data"

    monkeypatch.setenv("SIW_MARKET_DATA_PROVIDER", "alpaca")
    with pytest.raises(ProviderConfigError, match="APCA_API_KEY_ID"):
        provider_from_env()

    monkeypatch.setenv("SIW_MARKET_DATA_PROVIDER", "massive")
    with pytest.raises(ProviderConfigError, match="fake, alpaca"):
        provider_from_env()


def make_app(recorder: Recorder) -> Any:
    from services.api.main import create_app

    return create_app(provider(recorder))


@pytest.mark.parametrize(
    ("response", "status", "code"),
    [
        (httpx.Response(401, text="{}"), 502, "provider_auth_failed"),
        (httpx.Response(429, text="{}"), 503, "provider_rate_limited"),
        (httpx.Response(500, text="{}"), 503, "provider_unavailable"),
        (ok(page(bar("13:30", h="1"))), 502, "provider_data_invalid"),
    ],
)
def test_api_maps_provider_failures(response: httpx.Response, status: int, code: str) -> None:
    from fastapi.testclient import TestClient

    with TestClient(make_app(Recorder(response))) as client:
        reply = client.get(
            "/v1/market/bars",
            params={
                "instrument_id": SPY,
                "start_utc": "2026-10-09T13:30:00Z",
                "end_utc": "2026-10-09T13:35:00Z",
            },
        )

    assert reply.status_code == status
    assert reply.json()["error"]["code"] == code
    assert SECRET not in reply.text
