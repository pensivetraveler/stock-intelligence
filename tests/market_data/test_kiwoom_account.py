"""Kiwoom US order history sync into the broker ledger (no network).

Rows follow the official spec's ust21180 / ust21150 response fields and the shapes seen
live on 2026-10-22 (Seoul clock times, ord_dt = the US trading date).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.ledger import manual_positions
from services.journal.store import JournalStore
from services.market_data.fake import FakeMarketDataProvider
from services.market_data.kiwoom_account import (
    KiwoomAccountCredentials,
    KiwoomAccountSession,
    session_time,
    sync_executions,
)
from services.market_data.provider import InvalidBarsQueryError, ProviderDataError

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


def fill(ord_no: str, symbol: str, side: str, qty: str, price: str, at: str) -> dict[str, str]:
    return {
        "ord_no": ord_no,
        "crnc_code": "USD",
        "stk_cd": symbol,
        "frgn_trde_tp": "지정가",
        "ord_qty": qty.zfill(12),
        "cntr_qty": qty.zfill(12),
        "slby_tp_nm": side,
        "ord_time": at,
        "cntr_uv": price,
        "ord_stat_nm": "체결완료",
        "cntr_time": at,
    }


class Broker:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.daily = {
            "20260707": [
                fill("000049914", "AAPL", "매수", "149", "0.5890", "22:51:23"),
                # After midnight in Seoul, still 7 July in New York.
                fill("000081698", "AAPL", "매도", "100", "0.6000", "06:58:43"),
                # Daytime session (11:00 Seoul = 22:00 New York the day before).
                fill("000090000", "AAPL", "매수", "1", "1.0000", "11:00:00"),
                fill("000090001", "ZZZZ", "매수", "1", "1.0000", "23:00:00"),
                {**fill("000090002", "AAPL", "매수", "1", "1.0", "23:00:00"), "crnc_code": "HKD"},
                fill("000090003", "AAPL", "매수", "0", "0.0000", "23:00:00"),
            ]
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "return_code": 0})
        assert request.url.path == "/api/us/acnt"
        body = json.loads(request.content)
        api_id = request.headers["api-id"]
        assert api_id == "ust21150"
        rows = self.daily.get(body["ord_dt"])
        if not rows:  # what Kiwoom answers for a date without orders
            return httpx.Response(
                200, json={"return_code": 20, "return_msg": "[2000](571758:조회내역이 없습니다.)"}
            )
        if request.headers.get("cont-yn") == "Y":
            return httpx.Response(200, json={"result_list": rows[3:], "return_code": 0})
        return httpx.Response(
            200,
            json={"result_list": rows[:3], "return_code": 0},
            headers={"cont-yn": "Y", "next-key": "k1"},
        )
        raise AssertionError(f"unexpected api-id {api_id}")


def session(broker: Broker) -> KiwoomAccountSession:
    return KiwoomAccountSession(
        KiwoomAccountCredentials("app-key", "secret", "real"),
        transport=httpx.MockTransport(broker),
        min_interval_seconds=0,
    )


def instrument_for(symbol: str, currency: str) -> str | None:
    return "us:aapl:xnas:2026" if symbol == "AAPL" and currency == "USD" else None


def test_session_time_places_seoul_clock_times_on_the_us_order_date() -> None:
    day = date(2026, 7, 7)
    assert session_time(day, time(22, 51, 23)) == datetime(
        2026, 7, 7, 13, 51, 23, tzinfo=timezone.utc
    )
    assert session_time(day, time(6, 58, 43)) == datetime(
        2026, 7, 7, 21, 58, 43, tzinfo=timezone.utc
    )
    assert session_time(day, time(11, 0)) is None  # outside 04:00-20:00 New York
    # Winter (EST): 23:30 Seoul = 09:30 New York.
    assert session_time(date(2026, 1, 6), time(23, 30)) == datetime(
        2026, 1, 6, 14, 30, tzinfo=timezone.utc
    )


def test_sync_stores_each_filled_order_once_without_inventing_fees(tmp_path: Path) -> None:
    broker = Broker()
    store = JournalStore(tmp_path)
    result = sync_executions(session(broker), store, instrument_for, lambda: NOW, date(2026, 7, 1))
    validate_document(result)
    assert result["broker_id"] == "kiwoom"
    assert (result["orders_seen"], result["stored_new"], result["already_stored"]) == (6, 2, 0)
    assert result["skipped"] == {
        "not_filled": 1,
        "unsupported_currency": 1,
        "unknown_instrument": 1,
        "time_unresolved": 1,
    }
    # Every weekday from 1 to 9 July was read; 7 July in two pages.
    daily = [r for r in broker.requests if r.headers.get("api-id") == "ust21150"]
    assert [json.loads(r.content)["ord_dt"][-2:] for r in daily] == [
        "01",
        "02",
        "03",
        "06",
        "07",
        "07",
        "08",
        "09",
    ]

    executions = store.broker_executions()
    assert [e["executed_at_utc"] for e in executions] == [
        "2026-07-07T13:51:23Z",
        "2026-07-07T21:58:43Z",
    ]
    first = executions[0]
    assert (first["side"], first["quantity"], first["price"]) == ("buy", "149", "0.589")
    assert (first["fees"], first["commission"], first["tax"]) == (None, None, None)
    assert first["time_meaning"] == "reported_fill_time"
    assert first["broker_order_id"] == "20260707-000049914"
    assert first["broker_account_ref"].startswith("kiwoom-")
    assert "app-key" not in json.dumps(executions)

    again = sync_executions(session(broker), store, instrument_for, lambda: NOW, date(2026, 7, 1))
    assert (again["stored_new"], again["already_stored"]) == (0, 2)

    (position,) = manual_positions(executions)
    assert position["open_quantity"] == "49"
    assert position["fees_unreported_execution_ids"] == sorted(
        e["execution_id"] for e in executions
    )


def test_only_the_read_only_account_queries_can_be_sent() -> None:
    with pytest.raises(ProviderDataError):
        list(session(Broker()).rows("ust21000", {}))


def test_since_is_bounded(tmp_path: Path) -> None:
    store = JournalStore(tmp_path)
    with pytest.raises(InvalidBarsQueryError):
        sync_executions(session(Broker()), store, instrument_for, lambda: NOW, date(2026, 8, 1))
    with pytest.raises(InvalidBarsQueryError):
        sync_executions(session(Broker()), store, instrument_for, lambda: NOW, date(2020, 1, 1))
    with pytest.raises(InvalidBarsQueryError):  # more than 400 days in one call
        sync_executions(session(Broker()), store, instrument_for, lambda: NOW, date(2025, 1, 1))
    one_day = sync_executions(
        session(Broker()), store, instrument_for, lambda: NOW, date(2026, 7, 7), date(2026, 7, 7)
    )
    assert one_day["stored_new"] == 2


def test_kiwoom_sync_api(tmp_path: Path) -> None:
    broker = Broker()
    kiwoom = session(broker)
    app = create_app(
        FakeMarketDataProvider.from_path(),
        journal_store=JournalStore(tmp_path),
        kiwoom_session=lambda: kiwoom,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        synced = client.post("/v1/brokers/kiwoom/sync", params={"since": "2026-07-01"})
        assert synced.status_code == 200, synced.text
        assert synced.json()["broker_id"] == "kiwoom"
        ledger = client.get("/v1/trades/broker").json()
        validate_document(ledger)
        assert all(item["broker_id"] == "kiwoom" for item in ledger["executions"])
        missing = create_app(
            FakeMarketDataProvider.from_path(),
            journal_store=JournalStore(tmp_path),
            kiwoom_session=lambda: None,
            clock=lambda: NOW,
        )
        with TestClient(missing) as other:
            assert other.post("/v1/brokers/kiwoom/sync").status_code == 404
    # Only the token and the read-only daily query were sent.
    assert {r.headers.get("api-id") for r in broker.requests} == {"au10001", "ust21150"}
