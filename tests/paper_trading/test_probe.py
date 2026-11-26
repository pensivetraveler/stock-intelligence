from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from services.paper_trading.brokers.kiwoom_paper import KiwoomPaperBroker
from services.paper_trading.probe import (
    ProbeRefused,
    check_order,
    check_runner_idle,
    parser,
    run,
)
from services.paper_trading.store import PaperOrderStore
from tests.paper_trading.fake_kiwoom import FakeKiwoomPaper

NOW = datetime(2026, 11, 30, 21, 5, tzinfo=timezone.utc)  # 16:05 ET


def _broker(fake: FakeKiwoomPaper) -> KiwoomPaperBroker:
    return KiwoomPaperBroker(
        "app-key", "secret-key", "demo", transport=fake.transport(), sleep=lambda _s: None
    )


def _args(*argv: str) -> argparse.Namespace:
    return parser().parse_args(list(argv))


def _bodies(fake: FakeKiwoomPaper, api_id: str) -> list[dict[str, str]]:
    return [json.loads(r.content) for r in fake.requests if r.headers.get("api-id") == api_id]


def test_after_hours_limit_sell_goes_to_the_paper_host() -> None:
    fake = FakeKiwoomPaper()
    report = run(
        _args(
            "order",
            "--side",
            "sell",
            "--symbol",
            "AMD",
            "--venue",
            "ND",
            "--type",
            "limit",
            "--price",
            "120.5",
            "--yes",
        ),
        _broker(fake),
        NOW,
    )
    assert report["result"]["status"] == "accepted"
    assert report["at_new_york"].startswith("2026-11-30T16:05")
    [body] = _bodies(fake, "ust20001")
    assert body == {
        "stex_tp": "ND",
        "stk_cd": "AMD",
        "ord_qty": "1",
        "ord_uv": "120.5",
        "stop_pric": "",
        "trde_tp": "00",
    }
    assert all(r.url.host == "mockapi.kiwoom.com" for r in fake.requests)


def test_stop_limit_and_stop_carry_the_stop_price() -> None:
    fake = FakeKiwoomPaper()
    broker = _broker(fake)
    run(
        _args(
            "order",
            "--side",
            "buy",
            "--symbol",
            "AMD",
            "--venue",
            "ND",
            "--type",
            "stop_limit",
            "--price",
            "300",
            "--stop",
            "299",
            "--yes",
        ),
        broker,
        NOW,
    )
    run(
        _args(
            "order",
            "--side",
            "buy",
            "--symbol",
            "AMD",
            "--venue",
            "ND",
            "--type",
            "stop",
            "--stop",
            "299",
            "--yes",
        ),
        broker,
        NOW,
    )
    first, second = _bodies(fake, "ust20000")
    assert (first["trde_tp"], first["ord_uv"], first["stop_pric"]) == ("34", "300", "299")
    assert (second["trde_tp"], second["ord_uv"], second["stop_pric"]) == ("35", "", "299")


def test_cancel_maps_the_venue_back() -> None:
    fake = FakeKiwoomPaper()
    broker = _broker(fake)
    placed = run(
        _args(
            "order",
            "--side",
            "buy",
            "--symbol",
            "AMD",
            "--venue",
            "ND",
            "--type",
            "limit",
            "--price",
            "50",
            "--yes",
        ),
        broker,
        NOW,
    )
    ref = placed["result"]["broker_order_ref"]
    report = run(
        _args("cancel", "--ref", ref, "--symbol", "AMD", "--venue", "ND", "--yes"), broker, NOW
    )
    assert report["result"]["status"] == "accepted"
    assert _bodies(fake, "ust20003")[0]["stex_tp"] == "ND"


def test_nothing_is_sent_without_yes() -> None:
    fake = FakeKiwoomPaper()
    with pytest.raises(ProbeRefused):
        run(
            _args(
                "order",
                "--side",
                "buy",
                "--symbol",
                "AMD",
                "--venue",
                "ND",
                "--type",
                "limit",
                "--price",
                "50",
            ),
            _broker(fake),
            NOW,
        )
    assert not _bodies(fake, "ust20000")


@pytest.mark.parametrize(
    ("order_type", "price", "stop"),
    [
        ("limit", None, None),
        ("stop", None, None),
        ("stop", Decimal(10), Decimal(10)),
        ("stop_limit", Decimal(10), None),
        ("limit", Decimal(1000.01), None),
        ("stop", None, Decimal(1001)),
    ],
)
def test_bad_or_large_orders_are_refused(
    order_type: str, price: Decimal | None, stop: Decimal | None
) -> None:
    with pytest.raises(ProbeRefused):
        check_order(order_type, price, stop)


def test_refuses_while_the_runner_is_armed(tmp_path: Path) -> None:
    store = PaperOrderStore(tmp_path)
    check_runner_idle(store)
    store.halt("test", source="test")
    with pytest.raises(ProbeRefused):
        check_runner_idle(store)


def test_real_keys_cannot_be_used() -> None:
    from services.paper_trading.paper_client import NotPaperCredentialError

    with pytest.raises(NotPaperCredentialError):
        KiwoomPaperBroker("app-key", "secret-key", "real")
