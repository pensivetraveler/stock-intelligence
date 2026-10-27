"""Period review statistics, saved reviews and what-if totals (ADR 0042, step 4a)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from services.review.store import ReviewStore
from tests.agents.test_review_agent import broker
from tests.review.test_report_api import Asked, bars
from tests.review.test_whatif_api import _wait

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SAMSUNG = "kr:005930:xkrx:2026"


def korean(order_id: str, side: str, price: str, when: str) -> dict[str, Any]:
    document = broker(order_id, side, price, when, "kiwoom-a")
    document.update(instrument_id=SAMSUNG, symbol="005930", currency="KRW")
    return document


def period_documents() -> list[dict[str, Any]]:
    return [
        # The previous period (25 Feb to 1 Mar): one win of 200 gross.
        broker("0a", "buy", "100", "2026-02-26T15:00:00Z", "kiwoom-a"),
        broker("0b", "sell", "120", "2026-02-26T16:00:00Z", "kiwoom-a"),
        # Monday 2 March, 10:00 ET: +100 gross.
        broker("1a", "buy", "100", "2026-03-02T15:00:00Z", "kiwoom-a"),
        broker("1b", "sell", "110", "2026-03-02T16:00:00Z", "kiwoom-a"),
        # Tuesday 3 March, 9:40 ET (first 30 minutes): -50 gross.
        broker("2a", "buy", "100", "2026-03-03T14:40:00Z", "kiwoom-a"),
        broker("2b", "sell", "95", "2026-03-03T16:00:00Z", "kiwoom-a"),
        # Wednesday 4 March to Thursday: -100 gross, held a day.
        broker("3a", "buy", "100", "2026-03-04T15:00:00Z", "kiwoom-a"),
        broker("3b", "sell", "90", "2026-03-05T16:00:00Z", "kiwoom-a"),
        # A won trade the same week: kept apart from the dollar figures.
        korean("4a", "buy", "70000", "2026-03-03T01:00:00Z"),
        korean("4b", "sell", "71000", "2026-03-03T02:00:00Z"),
    ]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original = main.MarketBarsService

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    journal = JournalStore(tmp_path / "journal")
    for document in period_documents():
        journal.record_broker_execution(document)
    app = create_app(
        journal_store=journal,
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        review_ask=Asked(),
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        main.MarketBarsService = original  # type: ignore[misc]


def make(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/v1/review/periods", json={"from_date": "2026-03-02", "to_date": "2026-03-06", **body}
    )
    assert response.status_code == 201, response.text
    document: dict[str, Any] = response.json()
    validate_document(document)
    return document


def section(document: dict[str, Any], currency: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in document["sections"] if s["currency"] == currency)
    return found


def test_statistics_per_currency_with_the_previous_period(client: TestClient) -> None:
    document = make(client)
    assert document["previous_from_date"] == "2026-02-25"
    assert document["previous_to_date"] == "2026-03-01"
    usd = section(document, "USD")
    summary = usd["summary"]
    assert summary["trades"] == 3
    assert summary["wins"] == 1 and summary["losses"] == 2
    assert summary["win_rate"] == "0.3333"
    # Each trade paid two 0.50 fees: 99, -51, -101.
    assert summary["net_pnl"] == "-53"
    assert summary["fees"] == "3"
    assert summary["profit_factor"] == "0.6513"  # 99 / 152
    assert summary["expectancy"] == "-17.6667"
    assert summary["max_drawdown"] == "152"  # from +99 down to -53
    assert summary["longest_losing_streak"] == 2
    assert summary["largest_loss_share"] == "0.6645"  # 101 of 152
    assert usd["previous_summary"]["trades"] == 1
    assert usd["previous_summary"]["net_pnl"] == "199"
    assert [p["cumulative"] for p in usd["curve"]] == ["99", "48", "-53"]
    assert [d["date"] for d in usd["daily"]] == ["2026-03-02", "2026-03-03", "2026-03-05"]
    krw = section(document, "KRW")
    assert krw["summary"]["trades"] == 1
    assert krw["summary"]["net_pnl"] == "9999"


def test_groups(client: TestClient) -> None:
    client.put(
        "/v1/review/trades/flat:broker:brk-1a/annotation",
        json={"note": "", "tags": ["돌파"]},
    )
    groups = section(make(client), "USD")["groups"]
    by = {name: {row["key"]: row for row in rows} for name, rows in groups.items()}
    assert by["tag"]["돌파"]["net_pnl"] == "99"
    assert by["tag"]["(none)"]["trades"] == 2
    assert set(by["time_of_day"]) == {"regular", "first30"}
    assert by["time_of_day"]["first30"]["net_pnl"] == "-51"
    assert set(by["weekday"]) == {"mon", "tue", "wed"}
    assert by["holding"]["1-5d"]["trades"] == 1
    assert by["ticker"]["AAPL"]["trades"] == 3
    assert by["size"]["1000-5000"]["trades"] == 3  # each peaked at 10 x 100


def test_filters_by_account_and_strategy(client: TestClient) -> None:
    assert make(client, account_ref="toss-x-1")["sections"] == []
    assert section(make(client, strategy_id="none"), "USD")["summary"]["trades"] == 3
    bad = client.post(
        "/v1/review/periods", json={"from_date": "2026-03-06", "to_date": "2026-03-02"}
    )
    assert bad.json()["error"]["code"] == "invalid_period"


def test_principles_and_what_if_totals_come_from_stored_checks(client: TestClient) -> None:
    client.put(
        "/v1/review/principles",
        json={
            "items": [{"scope": "shared", "kind": "stop_loss_pct", "value": "5", "text": "손절 5%"}]
        },
    )
    first = section(make(client), "USD")
    assert first["principles"]["unchecked"] == 3  # making the review queued them
    assert first["what_if"]["missing"] == 3
    _wait(client)
    usd = section(make(client), "USD")
    principles = usd["principles"]
    assert principles["checked"] == 3 and principles["unchecked"] == 0
    assert principles["items"][0]["text"] == "손절 5%"
    assert principles["broken_trades"] >= 1  # the -10% trade broke the 5% stop
    assert principles["broken_trades"] + principles["kept_trades"] == 3
    what_if = usd["what_if"]
    assert what_if["computed"] == 3 and what_if["missing"] == 0
    assert what_if["hindsight"] is True


def test_reviews_are_saved_and_listed(client: TestClient) -> None:
    made = make(client)
    listed = client.get("/v1/review/periods").json()
    validate_document(listed)
    assert listed["reviews"][0]["review_id"] == made["review_id"]
    assert {t["currency"] for t in listed["reviews"][0]["totals"]} == {"USD", "KRW"}
    again = client.get(f"/v1/review/periods/{made['review_id']}").json()
    assert again == made
    assert client.get("/v1/review/periods/period-nope").status_code == 404
