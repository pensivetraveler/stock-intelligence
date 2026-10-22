"""Kiwoom Korean stock trades from kt00015 settlement lines (no network)."""

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
    sync_domestic,
)
from services.market_data.provider import InvalidBarsQueryError

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)


def line(no: str, code: str, side: str, qty: str, price: str, **extra: str) -> dict[str, Any]:
    return {
        "trde_dt": "20230306",
        "cntr_dt": "20230302",
        "trde_no": no,
        "rmrk_nm": "장내" + side,
        "crd_deal_tp_nm": "보통매매",
        "io_tp_nm": side,
        "stk_cd": code,
        "trde_qty_jwa_cnt": qty,
        "trde_unit": price,
        "cmsn": "000000000000100",
        "trde_agri_tax": "000000000000250",
        "crnc_cd": "KRW",
        **extra,
    }


class Broker:
    def __init__(self, stale_token: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.stale_token = stale_token
        self.tokens = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/token":
            self.tokens += 1
            return httpx.Response(200, json={"token": f"t{self.tokens}", "return_code": 0})
        assert request.url.path == "/api/dostk/acnt"
        assert request.headers["api-id"] == "kt00015"
        if self.stale_token and request.headers["authorization"] == "Bearer t1":
            return httpx.Response(
                200, json={"return_code": 3, "return_msg": "[8005:Token이 유효하지 않습니다]"}
            )
        body = json.loads(request.content)
        assert (body["tp"], body["gds_tp"]) == ("3", "1")
        rows = [
            line("000000001", "A005930", "매수", "10", "56,900"),
            line("000000002", "A005930", "매도", "10", "57,100"),
            line("000000003", "J512345", "매수", "1", "100"),  # an ELW
            line("000000004", "A999999", "매수", "1", "100"),  # not in the directory
        ]
        return httpx.Response(200, json={"trst_ovrl_trde_prps_array": rows, "return_code": 0})


def session(broker: Broker) -> KiwoomAccountSession:
    return KiwoomAccountSession(
        KiwoomAccountCredentials("app-key", "secret", "real"),
        transport=httpx.MockTransport(broker),
        min_interval_seconds=0,
    )


def instrument_for(symbol: str, currency: str, on: object = None) -> str | None:
    return "kr:005930:xkrx:2026" if (symbol, currency) == ("005930", "KRW") else None


def test_settlement_lines_are_stored_once_with_fees_and_no_invented_time(
    tmp_path: Path,
) -> None:
    store = JournalStore(tmp_path)
    result = sync_domestic(
        session(Broker()), store, instrument_for, lambda: NOW, date(2023, 3, 1), date(2023, 3, 31)
    )
    validate_document(result)
    assert (result["orders_seen"], result["stored_new"]) == (4, 2)
    assert result["skipped"]["unknown_instrument"] == 2
    assert {item["symbol"] for item in result["unknown_symbols"]} == {"J512345", "A999999"}
    buy, sell = sorted(store.broker_executions(), key=lambda e: e["broker_order_id"])
    assert (buy["side"], buy["quantity"], buy["price"]) == ("buy", "10", "56900")
    assert (buy["commission"], buy["tax"], buy["fees"]) == ("100", "250", "350")
    assert (buy["executed_at_utc"], buy["ordered_at_utc"]) == (None, None)
    assert (buy["trade_date"], buy["settlement_date"]) == ("2023-03-02", "2023-03-06")
    assert (buy["granularity"], buy["time_meaning"]) == ("settlement_line", "not_reported")
    assert buy["currency"] == "KRW" and buy["broker_order_id"] == "kr-20230306-000000001"
    again = sync_domestic(
        session(Broker()), store, instrument_for, lambda: NOW, date(2023, 3, 1), date(2023, 3, 31)
    )
    assert (again["stored_new"], again["already_stored"]) == (0, 2)
    # Without fill times FIFO cannot order them: listed as unordered, not guessed.
    (position,) = account_positions([buy, sell])
    assert len(position["unordered_execution_ids"]) == 2


def test_a_token_replaced_by_another_client_is_reissued_once(tmp_path: Path) -> None:
    broker = Broker(stale_token=True)
    result = sync_domestic(
        session(broker),
        JournalStore(tmp_path),
        instrument_for,
        lambda: NOW,
        date(2023, 3, 1),
        date(2023, 3, 31),
    )
    assert result["stored_new"] == 2 and broker.tokens == 2


def test_one_call_covers_at_most_a_year(tmp_path: Path) -> None:
    with pytest.raises(InvalidBarsQueryError):
        sync_domestic(
            session(Broker()),
            JournalStore(tmp_path),
            instrument_for,
            lambda: NOW,
            date(2023, 1, 1),
            date(2024, 6, 1),
        )
