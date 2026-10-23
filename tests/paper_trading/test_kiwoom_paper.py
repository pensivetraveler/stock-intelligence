from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from services.paper_trading.brokers.base import OrderNotSupportedError
from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker, KiwoomPaperError
from services.paper_trading.paper_client import NotPaperCredentialError
from tests.paper_trading.fake_kiwoom import FakeKiwoomPaper

INTENT = json.loads(
    Path("tests/fixtures/contracts/valid/paper-order-intent-limit.json").read_text(encoding="utf-8")
)


def _broker(fake: FakeKiwoomPaper) -> KiwoomPaperBroker:
    return KiwoomPaperBroker(
        "app-key", "secret-key", "demo", markets=frozenset({"us"}), transport=fake.transport()
    )


def _bodies(fake: FakeKiwoomPaper, api_id: str) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in fake.requests if r.headers.get("api-id") == api_id]


def test_real_keys_are_refused() -> None:
    with pytest.raises(NotPaperCredentialError):
        KiwoomPaperBroker("app-key", "secret-key", "real")


def test_no_market_is_offered_until_paper_orders_are_verified() -> None:
    broker = KiwoomPaperBroker("app-key", "secret-key", "demo")
    assert broker.capabilities().markets == frozenset()


def test_limit_buy_is_sent_to_the_paper_host() -> None:
    fake = FakeKiwoomPaper()
    result = _broker(fake).submit(INTENT)
    assert result.status == "accepted"
    assert result.broker_order_ref == "000000001"
    assert {request.url.host for request in fake.requests} == {"mockapi.kiwoom.com"}
    assert _bodies(fake, "ust20000") == [
        {"stex_tp": "ND", "stk_cd": "AAPL", "ord_qty": "3", "ord_uv": "200", "trde_tp": "00"}
    ]
    # No account number in any request body.
    assert all("acnt" not in json.dumps(json.loads(r.content or b"{}")) for r in fake.requests)


def test_market_sell_sends_no_price() -> None:
    fake = FakeKiwoomPaper()
    intent = {
        **INTENT,
        "side": "sell",
        "order": {**INTENT["order"], "order_type": "market", "limit_price": None},
    }
    _broker(fake).submit(intent)
    assert _bodies(fake, "ust20001")[0]["ord_uv"] == ""
    assert _bodies(fake, "ust20001")[0]["trde_tp"] == "03"


def test_rejected_order_keeps_the_code_and_message() -> None:
    fake = FakeKiwoomPaper(reject_orders=True)
    result = _broker(fake).submit(INTENT)
    assert (result.status, result.response_code, result.response_message) == (
        "rejected",
        "20",
        "주문 불가",
    )


def test_lost_reply_is_unknown_and_not_retried() -> None:
    fake = FakeKiwoomPaper(drop_next_order=True)
    result = _broker(fake).submit(INTENT)
    assert result.status == "unknown"
    assert len(_bodies(fake, "ust20000")) == 1
    assert len(fake.orders) == 1  # the server did take it


def test_server_error_without_a_body_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") == "au10001":
            return httpx.Response(200, json={"token": "t", "return_code": 0})
        return httpx.Response(502, text="bad gateway")

    broker = KiwoomPaperBroker("k", "s", "demo", transport=httpx.MockTransport(handler))
    assert broker.submit(INTENT).status == "unknown"


def test_replaced_token_is_reissued_once() -> None:
    fake = FakeKiwoomPaper()
    broker = _broker(fake)
    broker.today_orders()
    fake.invalidate_next_token = True
    assert broker.submit(INTENT).status == "accepted"
    assert fake.tokens_issued == 2


def test_unsupported_venue_is_not_sent() -> None:
    fake = FakeKiwoomPaper()
    with pytest.raises(OrderNotSupportedError):
        _broker(fake).submit({**INTENT, "instrument_id": "us:spy:arcx:2026", "symbol": "SPY"})
    assert _bodies(fake, "ust20000") == []


def test_today_orders_read_zero_padded_rows_across_pages() -> None:
    fake = FakeKiwoomPaper(pages=2)
    broker = _broker(fake)
    broker.submit(INTENT)
    broker.submit(INTENT)
    fake.fill("000000001", "199.95")
    orders = broker.today_orders()
    assert [o.broker_order_ref for o in orders] == ["000000001", "000000002"]
    first = orders[0]
    assert (first.side, first.order_quantity, first.filled_quantity) == (
        "buy",
        Decimal(3),
        Decimal(3),
    )
    assert (first.fill_price, first.remaining_quantity, first.filled_at_local) == (
        Decimal("199.95"),
        Decimal(0),
        "23:31:30",
    )
    assert orders[1].fill_price is None


def test_query_not_offered_in_paper_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("api-id") == "au10001":
            return httpx.Response(200, json={"token": "t", "return_code": 0})
        return httpx.Response(
            200,
            json={
                "return_code": 20,
                "return_msg": "[2000](RC9000:모의투자에서는 해당업무가 제공되지 않습니다.)",
            },
        )

    broker = KiwoomPaperBroker("k", "s", "demo", transport=httpx.MockTransport(handler))
    with pytest.raises(KiwoomPaperError, match="not offered in paper"):
        broker.today_orders()


def test_cancel_names_the_original_order() -> None:
    fake = FakeKiwoomPaper()
    broker = _broker(fake)
    broker.submit(INTENT)
    assert broker.cancel("000000001", INTENT["instrument_id"], "AAPL").status == "accepted"
    assert _bodies(fake, "ust20003") == [
        {"orig_ord_no": "000000001", "stex_tp": "ND", "stk_cd": "AAPL"}
    ]


def test_repr_hides_the_keys() -> None:
    assert "app-key" not in repr(_broker(FakeKiwoomPaper()))
