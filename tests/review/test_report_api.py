"""Single-trade report: facts, images cut at their time, Claude call (ADR 0042 step 2a)."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import create_app
from services.journal.store import JournalStore
from services.market_data.instruments import calendar_for
from services.market_data.provider import BarsQuery, BarsResult
from services.review.claude import ReportError, ask_for_report
from services.review.store import ReviewStore
from tests.agents.test_review_agent import broker

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "contracts" / "valid"
TEMPLATE = json.loads((FIXTURES / "price-bar.json").read_text())
AGGREGATED = json.loads((FIXTURES / "aggregated-price-bar.json").read_text())
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
STEP = {"5m": timedelta(minutes=5)}


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bar(query: BarsQuery, start: datetime, end: datetime, i: int) -> dict[str, Any]:
    # SPY rises steadily; anything else follows a gentle wave around 100.
    base = 400 + i * 0.01 if "spy" in query.instrument_id else 100 + 3 * math.sin(i / 7)
    open_, close = base, base + 0.05
    template = TEMPLATE if query.timeframe == "1d" else AGGREGATED
    return {
        **template,
        "instrument_id": query.instrument_id,
        "timeframe": query.timeframe,
        "interval_start_utc": _utc(start),
        "interval_end_utc": _utc(end),
        "open": f"{open_:.4f}",
        "high": f"{close + 0.5:.4f}",
        "low": f"{open_ - 0.5:.4f}",
        "close": f"{close:.4f}",
        "available_at_utc": _utc(end),
        "lineage": {**TEMPLATE["lineage"], "source_timeframe": "1d"}
        if query.timeframe == "1d"
        else AGGREGATED["lineage"],
    }


def bars(query: BarsQuery) -> BarsResult:
    """Full price_bar documents: 5m through the extended day, 1d and 1w per session."""
    calendar = calendar_for(query.instrument_id)
    out: list[dict[str, Any]] = []
    day = query.start_utc.date() - timedelta(days=1)
    windows = []
    while day <= query.end_utc.date():
        window = calendar.window_for_date(day)
        if (
            window
            and window.pre_open_utc >= query.start_utc
            and window.post_close_utc <= query.end_utc
        ):
            windows.append(window)
        day += timedelta(days=1)
    if query.timeframe == "5m":
        moment = query.start_utc
        i = 0
        for window in windows or [calendar.window_for_date(query.start_utc.date())]:
            if window is None:
                continue
            moment = window.pre_open_utc
            while moment < window.post_close_utc:
                if query.start_utc <= moment and moment + STEP["5m"] <= query.end_utc:
                    out.append(_bar(query, moment, moment + STEP["5m"], i))
                moment += STEP["5m"]
                i += 1
    elif query.timeframe == "1d":
        for i, window in enumerate(windows):
            out.append(_bar(query, window.pre_open_utc, window.post_close_utc, i))
    else:  # 1w: one bar per ISO week
        weeks: dict[tuple[int, int], list[Any]] = {}
        for window in windows:
            weeks.setdefault(window.session_date.isocalendar()[:2], []).append(window)
        for i, group in enumerate(weeks.values()):
            out.append(_bar(query, group[0].pre_open_utc, group[-1].post_close_utc, i))
    coverage = {
        "missing_intervals": [],
        "adjustment": "raw",
        "source_id": "synthetic",
        "source_feed_scope": "synthetic",
        "dataset_revision": "synthetic-r1",
        "expected_interval_count": len(out),
    }
    return BarsResult(tuple(out), coverage)


class Asked:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, draft: dict[str, Any], images: list[Any], key: str, language: str, **kw: Any
    ) -> dict[str, Any]:
        self.calls.append({"draft": draft, "images": images, "key": key, "model": kw["model"]})
        return {
            "model": kw["model"],
            "language": language,
            "created_at_utc": "2026-10-26T12:00:00Z",
            "summary": "요약",
            "sections": [
                {
                    "key": k,
                    "evidence": ["gap_pct"],
                    "went_well": ["a"],
                    "to_improve": ["b"],
                    "check_next": [],
                }
                for k in ("entry", "exit", "sizing")
            ],
            "uncertainty": "불확실",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Asked, list[str | None]]]:
    monkeypatch.delenv("SIW_DATA_DIR", raising=False)
    journal = JournalStore(tmp_path / "journal")
    asked = Asked()
    key: list[str | None] = ["test-key-not-real"]
    import services.api.main as main

    original_service, original_key = main.MarketBarsService, main._claude_key

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: BarsQuery) -> BarsResult:
            return bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    main._claude_key = lambda _credentials: key[0]  # type: ignore[assignment]
    app = create_app(
        journal_store=journal,
        review_store=ReviewStore(tmp_path / "review.sqlite3", lambda: NOW),
        review_ask=asked,
        clock=lambda: NOW,
    )
    # A day trade on Tuesday 3 March 2026 (EST): buy 15:00Z and 16:00Z, sell 18:00Z.
    for order_id, side, price, when in [
        ("1", "buy", "100", "2026-03-03T15:00:00Z"),
        ("2", "buy", "101", "2026-03-03T16:00:00Z"),
        ("3", "sell", "103", "2026-03-03T18:00:00Z"),
    ]:
        document = broker(order_id, side, price, when, "kiwoom-a")
        if order_id == "3":
            document["quantity"] = "20"
        journal.record_broker_execution(document)
    try:
        with TestClient(app) as client:
            yield client, asked, key
    finally:
        main.MarketBarsService = original_service  # type: ignore[misc]
        main._claude_key = original_key  # type: ignore[assignment]


TRADE = "flat:broker:brk-1"


def draft(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post(f"/v1/review/trades/{TRADE}/report-drafts", json=body)
    assert response.status_code == 201, response.text
    document: dict[str, Any] = response.json()
    return document


def codes(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {fact["code"]: fact for fact in document["facts"]}


def test_draft_has_entry_context_size_and_cut_images(setup: Any) -> None:
    client, _asked, _key = setup
    document = draft(client)
    found = codes(document)
    for code in (
        "realized_pnl",
        "entry_vs_previous_close_pct",
        "gap_pct",
        "premarket_high",
        "entry_vs_premarket_high_pct",
        "spy_change_to_entry_pct",
        "relative_to_spy_pct",
        "intraday_mfe_pct",
        "captured_of_best_ratio",
        "holding_minutes",
        "scale_in_effect_pct",
        "peak_position_cost",
    ):
        assert code in found, code
    assert found["holding_minutes"]["value"] == "180"
    assert found["peak_position_cost"]["value"] == "2010.00"
    assert found["intraday_mfe_pct"]["hindsight"] is True
    assert "mfe_pct" not in found  # the daily high would count hours outside the trade
    assert "position_pct_of_capital" in document["missing"]
    # Images: 5-minute up to the first buy, up to the last sell, and daily before the day.
    images = {image["kind"]: image for image in document["images"]}
    assert [image["kind"] for image in document["images"]] == ["entry", "exit", "context"]
    for image in document["images"]:
        cutoff = image["cutoff_utc"]
        assert all(bar["interval_end_utc"] <= cutoff for bar in image["bars"])
    assert images["entry"]["cutoff_utc"] == "2026-03-03T15:00:00Z"
    assert images["entry"]["markers"] == []  # the first buy is at the cut, after every bar
    assert images["exit"]["cutoff_utc"] == "2026-03-03T18:00:00Z"
    assert [m["side"] for m in images["exit"]["markers"]] == ["buy", "buy"]
    assert images["context"]["timeframe"] == "1d"
    assert images["context"]["bars"][-1]["interval_end_utc"] < "2026-03-03T09:00:00Z"
    # What Claude is told about the trade carries no account or record ids.
    assert "account_ref" not in document["trade"]
    assert "kiwoom" not in json.dumps(document["facts"])


def test_account_capital_gives_the_position_share(setup: Any) -> None:
    client, _asked, _key = setup
    listed = client.get("/v1/review/account-capital").json()
    assert listed["accounts"] == [{"account_ref": "kiwoom-a", "currency": "USD"}]
    response = client.post(
        "/v1/review/account-capital",
        json={
            "account_ref": "kiwoom-a",
            "currency": "USD",
            "effective_from": "2026-01-01",
            "amount": "20100",
        },
    )
    assert response.status_code == 201
    entry_id = response.json()["entries"][0]["entry_id"]
    found = codes(draft(client))
    assert found["position_pct_of_capital"]["value"] == "10.00"
    # An entry that starts after the trade does not apply.
    client.delete(f"/v1/review/account-capital/{entry_id}")
    client.post(
        "/v1/review/account-capital",
        json={
            "account_ref": "kiwoom-a",
            "currency": "USD",
            "effective_from": "2026-04-01",
            "amount": "1000",
        },
    )
    assert "position_pct_of_capital" in draft(client)["missing"]
    unknown = client.post(
        "/v1/review/account-capital",
        json={
            "account_ref": "kiwoom-a",
            "currency": "KRW",
            "effective_from": "2026-01-01",
            "amount": "1",
        },
    )
    assert unknown.status_code == 404


def test_note_is_sent_as_written_after_the_trade(setup: Any) -> None:
    client, _asked, _key = setup
    client.put(f"/v1/review/trades/{TRADE}/annotation", json={"note": "추격", "tags": ["돌파"]})
    assert draft(client)["note"] == {
        "text": "추격",
        "tags": ["돌파"],
        "written_at_utc": "2026-10-26T12:00:00Z",
    }
    assert draft(client, include_note=False)["note"] is None


def test_report_uses_the_chosen_model_and_keeps_image_hashes(setup: Any) -> None:
    client, asked, _key = setup
    settings = client.put("/v1/review/settings", json={"report_model": "claude-opus-5-5"})
    assert settings.json()["report_model"] == "claude-opus-5-5"
    document = draft(client)
    images = [{"kind": image["kind"], "png_base64": PNG} for image in document["images"]]
    response = client.post(
        f"/v1/review/report-drafts/{document['draft_id']}/report",
        json={"language": "ko", "images": images},
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["model"] == "claude-opus-5-5"
    assert [s["key"] for s in report["sections"]] == ["entry", "exit", "sizing"]
    assert [i["kind"] for i in report["images"]] == ["entry", "exit", "context"]
    assert asked.calls[0]["model"] == "claude-opus-5-5"
    assert [kind for kind, _ in asked.calls[0]["images"]] == ["entry", "exit", "context"]
    listed = client.get(f"/v1/review/trades/{TRADE}/reports").json()
    assert [r["report_id"] for r in listed["reports"]] == [report["report_id"]]


def test_report_refuses_wrong_images_and_a_missing_key(setup: Any) -> None:
    client, asked, key = setup
    document = draft(client)
    url = f"/v1/review/report-drafts/{document['draft_id']}/report"
    wrong = client.post(url, json={"images": [{"kind": "entry", "png_base64": PNG}]})
    assert wrong.json()["error"]["code"] == "invalid_report_images"
    not_png = [
        {"kind": image["kind"], "png_base64": base64.b64encode(b"GIF89a....").decode()}
        for image in document["images"]
    ]
    assert client.post(url, json={"images": not_png}).status_code == 422
    key[0] = None
    images = [{"kind": image["kind"], "png_base64": PNG} for image in document["images"]]
    assert client.post(url, json={"images": images}).json()["error"]["code"] == "agent_key_missing"
    assert asked.calls == []
    assert client.post("/v1/review/report-drafts/draft-nope/report", json={}).status_code == 404


def test_ask_for_report_sends_images_and_structured_output() -> None:
    sent: list[dict[str, Any]] = []
    reply = {
        "summary": "s",
        "sections": [
            {
                "key": "exit",
                "evidence": ["mfe_pct"],
                "went_well": [],
                "to_improve": ["x"],
                "check_next": [],
            }
        ],
        "uncertainty": "u",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5-5",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(reply)}],
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        )

    document = {
        "trade": {"instrument_id": "us:aapl:xnas:2026"},
        "facts": [],
        "missing": [],
        "uncertainty": [],
        "warnings": [],
        "note": None,
        "images": [
            {"kind": "entry", "timeframe": "5m", "title": "t", "cutoff_utc": "2026-03-03T15:00:00Z"}
        ],
    }
    answer = ask_for_report(
        document,
        [("entry", PNG)],
        "k",
        "en",
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
    )
    body = sent[0]["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert sent[0]["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
    kinds = [block["type"] for block in body["messages"][0]["content"]]
    assert kinds == ["text", "image", "text"]
    # Missing sections come back empty, in a fixed order.
    assert [s["key"] for s in answer["sections"]] == ["entry", "exit", "sizing"]
    assert answer["sections"][1]["to_improve"] == ["x"]
    assert answer["usage"] == {"input_tokens": 3, "output_tokens": 4}
    report = {
        "kind": "review_trade_report",
        "schema_version": "0.1",
        "report_id": "report-1",
        "draft_id": "draft-1",
        "trade_id": TRADE,
        **answer,
        "images": [],
    }
    validate_document(report)


def test_ask_for_report_turns_refusals_into_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stop_reason": "refusal", "content": []})

    document = {
        "trade": {},
        "facts": [],
        "missing": [],
        "uncertainty": [],
        "warnings": [],
        "note": None,
        "images": [],
    }
    with pytest.raises(ReportError, match="declined"):
        ask_for_report(document, [], "k", "en", transport=httpx.MockTransport(handler))
    with pytest.raises(ReportError, match="model"):
        ask_for_report(document, [], "k", "en", model="claude-sonnet-4-5")
