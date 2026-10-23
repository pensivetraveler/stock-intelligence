"""Kiwoom US fees per settlement line (ust21100), added to positions (no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from packages.contracts.validator import validate_document
from services.journal.ledger import account_positions
from services.journal.store import JournalStore
from services.market_data.kiwoom_account import (
    KiwoomAccountCredentials,
    KiwoomAccountSession,
    sync_fees,
)
from services.market_data.provider import InvalidBarsQueryError

NOW = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)


def line(
    no: str, symbol: str, side: str, qty: str, price: str, fee: str, tax: str
) -> dict[str, Any]:
    return {
        "deal_dt": "20260708",
        "deal_no": no,
        "rmrk_nm": side,
        "stk_cd": symbol,
        "deal_qty": qty,
        "uv_exrt": price,
        "fc_cmsn": fee,
        "fc_deal_tax": tax,
        "crnc_code": "USD",
    }


class Broker:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "return_code": 0})
        assert request.headers["api-id"] == "ust21100"
        body = json.loads(request.content)
        self.bodies.append(body)
        if body["strt_dt"] > "20260708" or body["end_dt"] < "20260708":
            return httpx.Response(
                200, json={"return_code": 20, "return_msg": "[2000](571758:조회내역이 없습니다.)"}
            )
        rows = [
            line("000000006", "AAPL", "매도", "1", "20.62", "0.00", "0.00"),
            line("000000007", "AAPL", "매도", "1", "20.80", "0.10", "0.01"),
            line("000000008", "ZZZZ", "매수", "5", "1.00", "0.05", "0.00"),
        ]
        return httpx.Response(200, json={"result_list": rows, "return_code": 0})


def session(broker: Broker) -> KiwoomAccountSession:
    return KiwoomAccountSession(
        KiwoomAccountCredentials("app-key", "secret", "real"),
        transport=httpx.MockTransport(broker),
        min_interval_seconds=0,
    )


def instrument_for(symbol: str, currency: str, on: object = None) -> str | None:
    return "us:aapl:xnas:2026" if symbol == "AAPL" else None


def test_fee_lines_are_kept_apart_and_added_to_positions(tmp_path: Path) -> None:
    broker = Broker()
    store = JournalStore(tmp_path)
    result = sync_fees(session(broker), store, instrument_for, lambda: NOW, date(2026, 6, 1))
    validate_document(result)
    # Read a month at a time; only the line with a fee is kept.
    assert [b["strt_dt"] for b in broker.bodies] == ["20260601", "20260702"]
    assert (result["stored_new"], result["skipped"]["unknown_instrument"]) == (1, 1)
    (fee,) = store.broker_fee_lines()
    assert (fee["commission"], fee["tax"], fee["settlement_date"]) == ("0.1", "0.01", "2026-07-08")
    again = sync_fees(session(Broker()), store, instrument_for, lambda: NOW, date(2026, 6, 1))
    assert (again["stored_new"], again["already_stored"]) == (0, 1)

    execution = {
        "execution_id": "e1",
        "instrument_id": "us:aapl:xnas:2026",
        "broker_account_ref": fee["broker_account_ref"],
        "side": "buy",
        "quantity": "2",
        "price": "20",
        "fees": None,
        "executed_at_utc": "2026-07-07T14:00:00Z",
    }
    sell = {
        **execution,
        "execution_id": "e2",
        "side": "sell",
        "price": "21",
        "executed_at_utc": "2026-07-07T15:00:00Z",
    }
    (position,) = account_positions([execution, sell], store.broker_fee_lines())
    assert position["fees"] == "0.11"
    assert position["realized_pnl"] == "1.89"  # 2 x (21 - 20) - 0.11
    assert position["settlement_fee_lines"] == 1
    # The API reads only the amounts; positions come out the same.
    assert account_positions([execution, sell], store.broker_fee_amounts()) == [position]
    assert store.broker_fee_amounts("us:other:xnas:2026") == []


def test_fee_sync_range_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(InvalidBarsQueryError):
        sync_fees(
            session(Broker()), JournalStore(tmp_path), instrument_for, lambda: NOW, date(2025, 1, 1)
        )
