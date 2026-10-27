"""Claude on a period review: commentary, suggestions, follow-ups (ADR 0042, step 4b)."""

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
from services.review.period_ai import ask_period_commentary
from services.review.store import ReviewStore
from tests.review.test_conversation_api import Scripted, answer, tool_use
from tests.review.test_period_api import period_documents
from tests.review.test_report_api import Asked, bars

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


class Commented:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def __call__(
        self, payload: dict[str, Any], key: str, language: str, **kw: Any
    ) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "model": kw["model"],
            "language": language,
            "created_at_utc": "2026-10-26T12:00:00Z",
            "summary": "손실이 컸습니다.",
            "repeated_mistakes": ["장 초반 추격"],
            "went_well": ["손실 거래 크기가 작음"],
            "focus_next": ["첫 30분 관망"],
            "principle_suggestions": [
                {
                    "action": "add",
                    "item_id": None,
                    "scope": "shared",
                    "kind": "no_entry_first_minutes",
                    "value": "30",
                    "text": "장 시작 30분 매수 금지",
                    "reason": "first30 거래 1건 -51",
                }
            ],
            "uncertainty": "표본이 작음",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Commented, Scripted]]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    import services.api.main as main

    original_service, original_key = main.MarketBarsService, main._claude_key

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    main._claude_key = lambda _credentials: "test-key-not-real"  # type: ignore[assignment]
    journal = JournalStore(tmp_path / "journal")
    for document in period_documents():
        journal.record_broker_execution(document)
    commented = Commented()
    script = Scripted(
        tool_use(
            ("p1", "period_trades", {"currency": "USD", "group": "time_of_day", "key": "first30"})
        ),
        answer("장 초반 거래는 1건이고 -51입니다."),
    )
    app = create_app(
        journal_store=journal,
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        review_ask=Asked(),
        review_period_ask=commented,
        review_converse=script,
        clock=lambda: NOW,
    )
    try:
        with TestClient(app) as client:
            yield client, commented, script
    finally:
        main.MarketBarsService = original_service  # type: ignore[misc]
        main._claude_key = original_key  # type: ignore[assignment]


def period(client: TestClient) -> str:
    client.put(
        "/v1/review/trades/flat:broker:brk-2a/annotation",
        json={"note": "급해서 샀다", "tags": ["추격"]},
    )
    made = client.post(
        "/v1/review/periods", json={"from_date": "2026-03-02", "to_date": "2026-03-06"}
    ).json()
    review_id: str = made["review_id"]
    return review_id


def test_payload_preview_has_no_ids_and_keeps_best_and_worst(setup: Any) -> None:
    client, _commented, _script = setup
    review_id = period(client)
    document = client.get(f"/v1/review/periods/{review_id}/payload").json()
    validate_document(document)
    payload = document["payload"]
    assert payload["period"] == {"from": "2026-03-02", "to": "2026-03-06"}
    assert all("curve" not in s and "daily" not in s for s in payload["statistics"])
    usd = payload["best_and_worst_trades"]["USD"]
    assert [t["net_pnl"] for t in usd["worst"]] == ["-101", "-51", "99"]
    chased = usd["worst"][1]
    assert chased["time_of_day"] == "first30"
    assert chased["note_written_after_the_trade"] == "급해서 샀다"
    assert chased["tags"] == ["추격"]
    text = json.dumps(payload)
    assert "brk-" not in text and "kiwoom" not in text


def test_commentary_is_saved_with_suggestions(setup: Any) -> None:
    client, commented, _script = setup
    review_id = period(client)
    response = client.post(f"/v1/review/periods/{review_id}/commentary", json={"language": "ko"})
    assert response.status_code == 201, response.text
    document = response.json()
    validate_document(document)
    assert document["principle_suggestions"][0]["kind"] == "no_entry_first_minutes"
    assert commented.payloads[0]["period"]["from"] == "2026-03-02"
    listed = client.get(f"/v1/review/periods/{review_id}/commentaries").json()
    validate_document(listed)
    assert [c["summary"] for c in listed["commentaries"]] == ["손실이 컸습니다."]


def test_follow_up_uses_the_period_tool(setup: Any) -> None:
    client, _commented, script = setup
    review_id = period(client)
    client.post(f"/v1/review/periods/{review_id}/commentary", json={})
    response = client.post(
        f"/v1/review/periods/{review_id}/conversation/messages",
        json={"question": "장 초반 거래는?"},
    )
    assert response.status_code == 200, response.text
    document = response.json()
    validate_document(document)
    assert document["status"] == "idle"
    assert [e["type"] for e in document["events"]] == ["question", "request", "answer"]
    first = script.calls[0]
    assert [tool["name"] for tool in first["tools"]] == ["period_trades"]
    opening = json.loads(first["messages"][0]["content"][0]["text"])
    assert opening["your_commentary"]["summary"] == "손실이 컸습니다."
    result = json.loads(script.calls[1]["messages"][2]["content"][0]["content"])
    assert result["matched"] == 1
    assert result["trades"][0]["net_pnl"] == "-51"
    again = client.get(f"/v1/review/periods/{review_id}/conversation").json()
    assert len(again["events"]) == 3


def test_ask_period_commentary_request_and_cleaning() -> None:
    sent: list[httpx.Request] = []
    reply = {
        "summary": "s",
        "repeated_mistakes": ["a"],
        "went_well": [],
        "focus_next": ["b"],
        "principle_suggestions": [
            {
                "action": "change",
                "item_id": "p1",
                "scope": "shared",
                "kind": "stop_loss_pct",
                "value": 3,
                "text": "손절 3%",
                "reason": "손실 평균 -6%",
            },
            {
                "action": "explode",
                "item_id": None,
                "scope": "shared",
                "kind": "text",
                "value": None,
                "text": "x",
                "reason": "y",
            },
        ],
        "uncertainty": "u",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5-5",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(reply)}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )

    answer_ = ask_period_commentary(
        {"period": {}}, "k", "ko", transport=httpx.MockTransport(handler), now=lambda: NOW
    )
    body = json.loads(sent[0].content)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert [s["action"] for s in answer_["principle_suggestions"]] == ["change"]
    assert answer_["principle_suggestions"][0]["value"] == "3"
