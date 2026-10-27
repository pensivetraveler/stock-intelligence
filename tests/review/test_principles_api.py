"""Trading principles: versions, Claude conversion, per-trade checks (ADR 0042, step 3a)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from services.review.claude import convert_principles, payload_for
from services.review.store import ReviewStore
from tests.agents.test_review_agent import broker
from tests.review.test_conversation_api import trade_documents
from tests.review.test_report_api import Asked, bars

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
TRADE = "flat:broker:brk-1"


class Converted:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, text: str, strategies: list[Any], key: str, language: str, **kw: Any
    ) -> dict[str, Any]:
        self.calls.append({"text": text, "strategies": strategies, "model": kw["model"]})
        return {
            "items": [
                {"scope": "shared", "kind": "stop_loss_pct", "value": "7", "text": "손절 -7%"},
                {"scope": "shared", "kind": "text", "value": None, "text": "급등주 추격 금지"},
            ],
            "notes": "",
            "model": kw["model"],
        }


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, JournalStore, Converted, Asked, list[str | None]]]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original_service, original_key = main.MarketBarsService, main._claude_key
    key: list[str | None] = ["test-key-not-real"]

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    main._claude_key = lambda _credentials: key[0]  # type: ignore[assignment]
    journal = JournalStore(tmp_path / "journal")
    for document in trade_documents():
        journal.record_broker_execution(document)
    converted, asked = Converted(), Asked()
    app = create_app(
        journal_store=journal,
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        review_ask=asked,
        review_convert=converted,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as client:
            yield client, journal, converted, asked, key
    finally:
        main.MarketBarsService = original_service  # type: ignore[misc]
        main._claude_key = original_key  # type: ignore[assignment]


def save(client: TestClient, *items: tuple[str, str | None, str], scope: str = "shared") -> Any:
    response = client.put(
        "/v1/review/principles",
        json={
            "items": [
                {"scope": scope, "kind": kind, "value": value, "text": text}
                for kind, value, text in items
            ],
            "source_text": "원칙",
        },
    )
    return response


def checked(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get(f"/v1/review/trades/{TRADE}/principle-check")
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    return {item["text"]: item for item in document["results"]}


def test_versions_are_kept_and_checked_after_the_fact(setup: Any) -> None:
    client, _journal, _converted, _asked, _key = setup
    assert client.get("/v1/review/principles").json()["current"] is None
    assert client.get(f"/v1/review/trades/{TRADE}/principle-check").json()["results"] == []
    assert save(client, ("stop_loss_pct", "7", "손절 -7%")).status_code == 201
    second = save(client, ("stop_loss_pct", "5", "손절 -5%"), ("text", None, "메모 남기기"))
    body = second.json()
    validate_document(body)
    assert [v["version"] for v in body["versions"]] == [1, 2]
    assert body["current"]["items"][0] == {
        "item_id": "p1",
        "scope": "shared",
        "kind": "stop_loss_pct",
        "value": "5",
        "text": "손절 -5%",
    }
    document = client.get(f"/v1/review/trades/{TRADE}/principle-check").json()
    # The trade (March) is older than every version: the current one, after the fact.
    assert document["version"] == 2
    assert document["after_the_fact"] is True
    assert {r["text"]: r["status"] for r in document["results"]}["메모 남기기"] == "judgment"


def test_numeric_items_are_checked_from_fills_and_bars(setup: Any) -> None:
    client, journal, _converted, _asked, _key = setup
    client.post(
        "/v1/review/account-capital",
        json={
            "account_ref": "kiwoom-a",
            "currency": "USD",
            "effective_from": "2026-01-01",
            "amount": "20100",
        },
    )
    save(
        client,
        ("stop_loss_pct", "50", "wide stop"),
        ("stop_loss_pct", "0.01", "tight stop"),
        ("take_profit_pct", "50", "far target"),
        ("take_profit_pct", "0.01", "near target"),
        ("max_position_pct", "5", "size 5%"),
        ("max_position_pct", "20", "size 20%"),
        ("no_entry_first_minutes", "60", "first hour"),
        ("no_entry_first_minutes", "15", "first 15 minutes"),
        ("max_gap_chase_pct", "1000", "gap"),
        ("max_trades_per_day", "1", "one a day"),
    )
    results = checked(client)
    status = {text: item["status"] for text, item in results.items()}
    assert status["wide stop"] == "kept"
    assert status["tight stop"] == "broken"  # touched while held, sold later
    assert "held on" in results["tight stop"]["detail"]
    assert status["far target"] == "not_applicable"
    assert status["near target"] == "kept"  # reached early, but still sold above it
    assert status["size 5%"] == "broken"  # 2010 of 20100 is 10%
    assert results["size 5%"]["detail"].startswith("the position peaked at 10.00%")
    assert status["size 20%"] == "kept"
    assert status["first hour"] == "broken"  # 10:00 ET is 30 minutes after the open
    assert status["first 15 minutes"] == "kept"
    assert status["gap"] == "kept"
    assert status["one a day"] == "kept"
    # A second trade opened the same day breaks "one a day".
    journal.record_broker_execution(broker("9", "buy", "100", "2026-03-03T19:00:00Z", "toss-x-1"))
    assert checked(client)["one a day"]["status"] == "broken"


def test_strategy_items_apply_only_to_that_strategy(setup: Any) -> None:
    client, _journal, _converted, _asked, _key = setup
    response = save(client, ("stop_loss_pct", "7", "s"), scope="strategy-nope")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_principles"
    assert save(client, ("stop_loss_pct", "-1", "negative")).status_code == 422
    assert save(client, ("max_trades_per_day", "1.5", "half")).status_code == 422
    assert save(client, ("text", None, "")).status_code == 422


def test_claude_converts_text_into_proposed_items(setup: Any) -> None:
    client, _journal, converted, _asked, key = setup
    response = client.post(
        "/v1/review/principles/draft", json={"text": "손절은 7%, 급등주 추격 금지"}
    )
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    assert [item["kind"] for item in document["items"]] == ["stop_loss_pct", "text"]
    assert converted.calls[0]["model"] == "claude-sonnet-5-5"
    # Nothing is stored until the investor saves.
    assert client.get("/v1/review/principles").json()["versions"] == []
    key[0] = None
    missing = client.post("/v1/review/principles/draft", json={"text": "x"})
    assert missing.json()["error"]["code"] == "agent_key_missing"


def test_the_report_gets_the_check_and_is_told_to_judge_text_items(setup: Any) -> None:
    client, _journal, _converted, _asked, _key = setup
    save(client, ("stop_loss_pct", "50", "wide stop"), ("text", None, "급등주 추격 금지"))
    draft = client.post(f"/v1/review/trades/{TRADE}/report-drafts", json={}).json()
    principles = draft["principles"]
    assert principles["version"] == 1
    assert principles["after_the_fact"] is True
    assert [r["status"] for r in principles["results"]] == ["kept", "judgment"]
    sent = payload_for(draft)
    assert sent["trading_principles"] == principles


def test_convert_principles_asks_for_structured_items() -> None:
    sent: list[httpx.Request] = []
    reply = {
        "items": [{"scope": "shared", "kind": "stop_loss_pct", "value": 7, "text": "손절 7%"}],
        "notes": "",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5-5",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(reply)}],
            },
        )

    answer = convert_principles(
        "손절 7%",
        [{"strategy_id": "strategy-1", "name": "Breakout"}],
        "k",
        "ko",
        transport=httpx.MockTransport(handler),
    )
    body = json.loads(sent[0].content)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "strategy-1 (Breakout)" in body["system"]
    assert answer["items"] == [
        {"scope": "shared", "kind": "stop_loss_pct", "value": "7", "text": "손절 7%"}
    ]
