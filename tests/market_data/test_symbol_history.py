"""Ticker changes and delistings (ADR 0033)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app
from services.journal.store import JournalStore
from services.market_data.alpaca import AlpacaConfig, AlpacaMarketDataProvider
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.provider import BarsQuery
from services.market_data.symbol_history import (
    SymbolHistory,
    asof_for,
    register,
    resolution_of,
    ticker_for,
)
from services.market_data.toss_account import sync_executions as toss_sync
from tests.market_data.test_kiwoom_account import NOW, Broker, fill, session

HISTORY = {
    "renames": [
        {"old": "FB", "new": "META", "on": "2022-06-09"},
        {"old": "AAA", "new": "BBB", "on": "2024-01-10"},
        {"old": "BBB", "new": "CCC", "on": "2025-01-10"},
        {"old": "DEVS", "new": "DEVSF", "on": "2025-08-27"},
        {"old": "OLDA", "new": "AAPL", "on": "2026-08-01"},
        {"old": "DXF", "new": "DXFFY", "on": "2023-08-01"},
    ],
    "delisted": [
        {
            "symbol": "GONE",
            "name": "Gone Inc",
            "venue": "XNYS",
            "on": "2025-03-01",
            "reason": "worthless_removal",
        }
    ],
}
LISTED = {"META": "XNAS", "CCC": "XNAS", "AAPL": "XNAS"}


class Fetch:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return HISTORY


def history(root: Path, fetch: Fetch | None = None, now: datetime | None = None) -> SymbolHistory:
    moment = now or datetime(2026, 10, 22, tzinfo=timezone.utc)
    return SymbolHistory(root, "alpaca", fetch or Fetch(), now=lambda: moment)


def test_renames_are_followed_to_the_active_ticker(tmp_path: Path) -> None:
    found = history(tmp_path)
    listed = LISTED.get
    assert found.resolve("FB", date(2021, 1, 5), listed).symbol == "META"
    assert found.resolve("FB", date(2021, 1, 5), listed).kind == "renamed"
    chain = found.resolve("AAA", date(2023, 5, 1), listed)
    assert (chain.kind, chain.symbol) == ("renamed", "CCC")
    # After its own rename date the old ticker is no longer that company's.
    assert found.resolve("AAPL", date(2026, 9, 1), listed).kind == "listed"
    assert found.renamed_to("FB") == "META" and found.renamed_to("AAPL") is None


def test_todays_ticker_is_traced_back_to_the_one_traded(tmp_path: Path) -> None:
    found = history(tmp_path)
    # Toss reports today's ticker: DEVSF for a 2025 trade made as DEVS.
    assert found.traded_as("DEVSF", date(2025, 1, 2)) == "DEVS"
    assert found.traded_as("devsf", date(2025, 9, 1)) == "DEVSF"  # after the rename
    assert found.traded_as("CCC", date(2023, 5, 1)) == "AAA"  # chains, newest first
    assert found.traded_as("CCC", date(2024, 6, 1)) == "BBB"
    assert found.traded_as("GONE", date(2024, 6, 3)) == "GONE"


def test_toss_symbols_missing_from_the_directory_follow_the_history(tmp_path: Path) -> None:
    from tests.market_data import test_toss_account as toss

    found = history(tmp_path)
    store = JournalStore(tmp_path)
    broker = toss.Broker()
    broker.closed_pages[1]["orders"][1]["symbol"] = "DEVSF"
    broker.closed_pages[1]["orders"][1]["execution"]["filledAt"] = "2025-01-02T23:30:05+09:00"

    def history_instrument(symbol: str, on: date) -> str | None:
        traded = found.traded_as(symbol, on)
        resolved = found.resolve(traded, on, LISTED.get)
        return f"us:{resolved.symbol.lower()}:{resolved.venue.lower()}:2026"

    register(found)
    try:
        result = toss_sync(
            toss.session(broker),
            store,
            toss.instrument_for,
            lambda: toss.NOW,
            history_instrument=history_instrument,
        )
    finally:
        register(None)
    assert result["skipped"]["unknown_instrument"] == 0 and result["unknown_symbols"] == []
    (devs,) = [row for row in store.broker_executions() if row["symbol"] == "DEVSF"]
    assert devs["instrument_id"] == "us:devs:xnas:2026"
    assert devs["instrument_resolution"] == "delisted"
    # Symbols the directory knows are stored as before, without a resolution.
    assert all(
        "instrument_resolution" not in row
        for row in store.broker_executions()
        if row["symbol"] != "DEVSF"
    )
    assert found.delisted("DEVS").asof == date(2025, 1, 2)


def test_delisted_tickers_are_kept_and_registered(tmp_path: Path) -> None:
    found = history(tmp_path)
    devs = found.resolve("DEVS", date(2025, 1, 2), LISTED.get)
    assert (devs.kind, devs.symbol, devs.venue, devs.venue_verified, devs.moved_to) == (
        "delisted",
        "DEVS",
        "XNAS",
        False,
        "DEVSF",
    )
    gone = found.resolve("GONE", date(2024, 6, 3), LISTED.get)
    assert (gone.venue, gone.venue_verified) == ("XNYS", True)
    found.resolve("DEVS", date(2025, 5, 2), LISTED.get)
    found.resolve("DEVS", date(2024, 5, 2), LISTED.get)  # an older trade changes nothing
    stored = json.loads((tmp_path / "symbols" / "delisted-instruments.json").read_text())
    assert stored["DEVS"]["asof"] == "2025-05-02"
    assert stored["GONE"]["delisted_on"] == "2025-03-01"
    # A fresh process reads the registry back.
    again = history(tmp_path)
    assert again.delisted("devs") is not None
    register(again)
    try:
        assert asof_for("DEVS") == date(2025, 5, 2)
        assert asof_for("META") is None
    finally:
        register(None)


def test_the_history_is_refreshed_daily_and_survives_outages(tmp_path: Path) -> None:
    fetch = Fetch()
    found = history(tmp_path, fetch)
    found.resolve("FB", date(2021, 1, 5), LISTED.get)
    found.resolve("FB", date(2021, 1, 5), LISTED.get)
    assert fetch.calls == 1
    later = history(tmp_path, Fetch(fail=True), datetime(2026, 10, 24, tzinfo=timezone.utc))
    assert later.resolve("FB", date(2021, 1, 5), LISTED.get).symbol == "META"
    with pytest.raises(RuntimeError):
        history(tmp_path / "empty", Fetch(fail=True)).resolve("FB", date(2021, 1, 5), LISTED.get)


def test_bars_of_a_delisted_ticker_are_read_as_that_company(tmp_path: Path) -> None:
    found = history(tmp_path)
    found.resolve("DEVS", date(2025, 1, 2), LISTED.get)
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"bars": {}, "next_page_token": None})

    adapter = AlpacaMarketDataProvider(
        AlpacaConfig(key_id="k", secret_key="s"),
        transport=httpx.MockTransport(answer),
        now=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc),
    )
    register(found)
    try:
        for instrument_id in ("us:devs:xnas:2026", "us:aapl:xnas:2026"):
            adapter.get_daily_bars(
                BarsQuery(
                    instrument_id,
                    "1d",
                    datetime(2025, 1, 2, tzinfo=timezone.utc),
                    datetime(2025, 2, 1, tzinfo=timezone.utc),
                )
            )
    finally:
        register(None)
    assert requests[0].url.params["asof"] == "2025-01-02"
    assert "asof" not in requests[1].url.params


def test_sync_and_search_follow_the_history(tmp_path: Path) -> None:
    broker = Broker()
    broker.daily["20260707"] = [
        fill("000000001", "OLDA", "매수", "5", "10.0000", "22:51:23"),
        fill("000000002", "DEVS", "매수", "3", "2.0000", "23:00:00"),
    ]
    found = history(tmp_path)
    app = create_app(
        FakeMarketDataProvider.from_path(),
        journal_store=JournalStore(tmp_path),
        kiwoom_session=lambda: session(broker),
        symbol_history=found,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as client:
            synced = client.post("/v1/brokers/kiwoom/sync", params={"since": "2026-07-07"})
            assert synced.status_code == 200, synced.text
            assert synced.json()["stored_new"] == 2
            ledger = client.get("/v1/trades/broker").json()
            by_symbol = {e["symbol"]: e for e in ledger["executions"]}
            assert by_symbol["OLDA"]["instrument_id"] == "us:aapl:xnas:2026"
            assert by_symbol["OLDA"]["instrument_resolution"] == "renamed"
            assert by_symbol["DEVS"]["instrument_id"] == "us:devs:xnas:2026"
            assert by_symbol["DEVS"]["instrument_resolution"] == "delisted"
    finally:
        register(None)


def test_a_reused_ticker_keeps_todays_company_apart(tmp_path: Path) -> None:
    found = history(tmp_path)
    listed = {**LISTED, "DXF": "XNAS"}.get  # another company uses DXF today
    old = found.resolve("DXF", date(2023, 4, 5), listed)
    assert (old.kind, old.symbol, old.moved_to) == ("delisted", "DXF.D23", "DXFFY")
    # Later trades of the same company reuse the ID; today's DXF stays a plain listing.
    assert found.resolve("DXF", date(2023, 6, 1), listed).symbol == "DXF.D23"
    assert found.resolve("DXF", date(2024, 1, 2), listed).kind == "listed"
    register(found)
    try:
        assert asof_for("DXF") is None
        assert asof_for("DXF.D23") == date(2023, 6, 1)
        assert ticker_for("DXF.D23") == "DXF" and ticker_for("DXF") == "DXF"
        assert resolution_of("DXF", "us:dxf.d23:xnas:2026") == "delisted"
        assert resolution_of("DXF", "us:dxf:xnas:2026") is None
        adapter = AlpacaMarketDataProvider(AlpacaConfig(key_id="k", secret_key="s"))
        assert adapter.symbol_for("us:dxf.d23:xnas:2026") == "DXF"
        assert adapter._asof("us:dxf:xnas:2026") is None
    finally:
        register(None)


def test_a_ticker_taken_over_after_a_merger_keeps_older_trades_apart(tmp_path: Path) -> None:
    registry = tmp_path / "symbols" / "delisted-instruments.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "STRR.D23": {
                    "venue": "XNAS",
                    "venue_verified": False,
                    "asof": "2023-05-05",
                    "name": None,
                    "moved_to": None,
                    "delisted_on": None,
                    "ticker": "STRR",
                    "reused_on": "2025-09-05",
                }
            }
        )
    )
    found = history(tmp_path)
    listed = {**LISTED, "STRR": "XNAS"}.get
    old = found.resolve("STRR", date(2023, 6, 1), listed)
    assert (old.kind, old.symbol) == ("delisted", "STRR.D23")
    assert found.resolve("STRR", date(2025, 10, 1), listed).kind == "listed"
    stored = json.loads(registry.read_text())["STRR.D23"]
    assert (stored["asof"], stored["reused_on"]) == ("2023-06-01", "2025-09-05")
    register(found)
    try:
        assert (asof_for("STRR.D23"), ticker_for("STRR.D23")) == (date(2023, 6, 1), "STRR")
    finally:
        register(None)


def test_a_verified_venue_in_the_registry_is_kept(tmp_path: Path) -> None:
    found = history(tmp_path)
    found.resolve("DEVS", date(2025, 1, 2), LISTED.get)
    path = tmp_path / "symbols" / "delisted-instruments.json"
    stored = json.loads(path.read_text())
    # The venue was verified afterwards (SIP tape A: NYSE) and written to the registry.
    stored["DEVS"].update(venue="XNYS", venue_verified=True)
    path.write_text(json.dumps(stored))
    again = history(tmp_path)
    later = again.resolve("DEVS", date(2025, 5, 2), LISTED.get)
    assert (later.venue, later.venue_verified) == ("XNYS", True)
    assert json.loads(path.read_text())["DEVS"]["venue"] == "XNYS"
