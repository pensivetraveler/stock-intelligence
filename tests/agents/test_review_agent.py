"""Chart and trade review Agent: local evidence, stored analyses, optional commentary."""

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
from services.agents.commentary import CommentaryError, ask_claude, payload_for
from services.agents.evidence import chart_point
from services.agents.store import AgentStore
from services.api.main import create_app
from services.backtest.jobs import BacktestRunner
from services.backtest.store import BacktestStore
from services.journal.store import JournalStore
from tests.backtest.test_backtest_api import CONFIG, RULES, synthetic_bars

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
AAPL = "us:aapl:xnas:2026"
KEY_IN_TEST = "test-anthropic-key-not-real"


def manual(execution_id: str, side: str, price: str, when: str) -> dict[str, Any]:
    return {
        "kind": "manual_execution",
        "schema_version": "0.1",
        "execution_id": execution_id,
        "instrument_id": AAPL,
        "side": side,
        "quantity": "10",
        "price": price,
        "currency": "USD",
        "asserted_at_utc": "2026-10-21T00:00:00Z",
        "time_precision": "exact",
        "selected_interval_start_utc": when,
        "selected_interval_end_utc": when.replace(":00Z", ":59Z"),
        "evidence_status": "unverified",
        "executed_at_utc": when,
        "fees": "1.00",
        "memo": "",
    }


class Asked:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str, str]] = []

    def __call__(self, analysis: dict[str, Any], key: str, language: str, **_: Any) -> dict:
        self.calls.append((analysis, key, language))
        return {
            "model": "claude-sonnet-5-5",
            "language": language,
            "created_at_utc": "2026-10-21T12:00:00Z",
            "summary": "요약",
            "inferences": ["추론"],
            "hypotheses": ["가설"],
            "questions": ["질문"],
            "uncertainty": "불확실성",
        }


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[TestClient, JournalStore, BacktestRunner, Asked]]:
    journal = JournalStore(tmp_path / "journal")
    runner = BacktestRunner(
        BacktestStore(tmp_path / "backtests.sqlite3", lambda: NOW), lambda: synthetic_bars
    )
    asked = Asked()
    app = create_app(
        journal_store=journal,
        backtest_runner=runner,
        agent_store=AgentStore(tmp_path / "agents.sqlite3", lambda: NOW),
        agent_ask=asked,
        clock=lambda: NOW,
    )
    # Bars for the Agent come from the synthetic source, like the backtests'.
    import services.api.main as main

    original = main.MarketBarsService

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return synthetic_bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    try:
        with TestClient(app) as client:
            yield client, journal, runner, asked
    finally:
        main.MarketBarsService = original  # type: ignore[misc]
        runner.stop()


def codes(analysis: dict[str, Any]) -> set[str]:
    return {fact["code"] for fact in analysis["facts"]}


def test_chart_point_reads_nothing_after_the_cutoff() -> None:
    # 2026-06-15 16:00 ET: that day's bar ends at 16:00 ET, after a 14:00 ET cutoff.
    cutoff = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
    evidence = chart_point(synthetic_bars, AAPL, "1d", cutoff)
    assert evidence.cutoff_utc == "2026-06-15T18:00:00Z"
    # Same session after its 20:00Z close: exactly one more bar, that day's.
    later = chart_point(
        synthetic_bars, AAPL, "1d", datetime(2026, 6, 15, 20, 30, tzinfo=timezone.utc)
    )
    used = int(next(f.value for f in evidence.facts if f.code == "bars_used"))
    assert int(next(f.value for f in later.facts if f.code == "bars_used")) == used + 1
    assert {"rsi14", "close_vs_sma20_pct", "close_vs_sma50_pct", "atr14_pct"} <= {
        f.code for f in evidence.facts
    }
    again = chart_point(synthetic_bars, AAPL, "1d", cutoff)
    assert [f.document() for f in again.facts] == [f.document() for f in evidence.facts]


def test_chart_review_is_stored_and_validated(setup: Any) -> None:
    client, journal, _runner, _asked = setup
    response = client.post(
        "/v1/agents/review/chart",
        json={"instrument_id": AAPL, "timeframe": "1d", "cutoff_utc": "2026-06-15T18:00:00Z"},
    )
    assert response.status_code == 201, response.text
    analysis = response.json()
    validate_document(analysis)
    assert analysis["target"] == {"type": "chart", "instrument_id": AAPL, "timeframe": "1d"}
    assert analysis["evidence"]["observations"]
    assert analysis["commentaries"] == []
    listed = client.get("/v1/agents/analyses").json()
    validate_document(listed)
    assert [a["analysis_id"] for a in listed["analyses"]] == [analysis["analysis_id"]]


def test_trade_review_matches_fifo_and_flags_hindsight(setup: Any) -> None:
    client, journal, _runner, _asked = setup
    for execution in (
        manual("buy-1", "buy", "100", "2026-03-02T15:00:00Z"),
        manual("buy-2", "buy", "110", "2026-03-04T15:00:00Z"),
        manual("sell-1", "sell", "120", "2026-03-10T15:00:00Z"),
    ):
        journal.record_manual(execution, execution["execution_id"])
    response = client.post(
        "/v1/agents/review/trades",
        json={
            "instrument_id": AAPL,
            "executions": [
                {"ledger": "manual", "execution_id": "sell-1"},
                {"ledger": "manual", "execution_id": "buy-1"},
                {"ledger": "manual", "execution_id": "buy-2"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    analysis = response.json()
    validate_document(analysis)
    facts = {f["code"]: f for f in analysis["facts"]}
    # FIFO: the sell of 10 closes the first buy at 100: +200, less 3.00 fees.
    assert facts["realized_pnl"]["value"] == "197.00"
    assert facts["open_quantity"]["value"] == "10"
    assert facts["average_buy"]["value"] == "105.0000"
    assert facts["mfe_pct"]["hindsight"] and facts["mae_pct"]["hindsight"]
    assert not facts["entry_rsi14"]["hindsight"]
    assert len([f for f in analysis["facts"] if f["code"] == "fill_range_position"]) == 3
    assert "still open" in " ".join(analysis["uncertainty"])
    assert sorted(analysis["source_refs"]) == ["manual:buy-1", "manual:buy-2", "manual:sell-1"]
    # The Agent writes nothing to the journal.
    assert len(journal.manual_executions()) == 3
    assert journal.scenarios() == [] and journal.broker_executions() == []
    missing = client.post(
        "/v1/agents/review/trades",
        json={"instrument_id": AAPL, "executions": [{"ledger": "broker", "execution_id": "x"}]},
    )
    assert missing.status_code == 404


def test_backtest_review_uses_the_pinned_results(setup: Any) -> None:
    client, _journal, runner, _asked = setup
    strategy = runner.store.save_strategy("s", RULES)
    run = runner.submit(strategy["strategy_id"], None, CONFIG)
    assert runner.wait_idle()
    response = client.post("/v1/agents/review/backtest", json={"run_id": run["run_id"]})
    assert response.status_code == 201, response.text
    analysis = response.json()
    validate_document(analysis)
    assert {"net_vs_benchmark_pct", "max_drawdown_pct", "closed_trades"} <= codes(analysis)
    assert any("too few" in note for note in analysis["uncertainty"])
    assert analysis["source_refs"] == [f"backtest:{run['run_id']}"]


def test_commentary_needs_a_key_and_sends_only_the_evidence(setup: Any, monkeypatch: Any) -> None:
    client, journal, _runner, asked = setup
    analysis = client.post(
        "/v1/agents/review/chart",
        json={"instrument_id": AAPL, "timeframe": "1d", "cutoff_utc": "2026-06-15T18:00:00Z"},
    ).json()
    path = f"/v1/agents/analyses/{analysis['analysis_id']}"
    refused = client.post(f"{path}/commentary", json={"language": "ko"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "agent_key_missing"

    import services.api.main as main

    monkeypatch.setattr(main, "_claude_key", lambda _credentials: KEY_IN_TEST)
    payload = client.get(f"{path}/payload").json()
    assert set(payload) == {
        "target",
        "cutoff_utc",
        "facts",
        "missing_facts",
        "known_uncertainty",
        "data_warnings",
    }
    answered = client.post(f"{path}/commentary", json={"language": "ko"})
    assert answered.status_code == 200, answered.text
    body = answered.json()
    validate_document(body)
    assert body["commentaries"][0]["summary"] == "요약"
    assert body["evidence"]["inferences"] == ["추론"]
    assert body["facts"] == analysis["facts"]  # evidence never changes
    sent, key, language = asked.calls[0]
    assert (key, language) == (KEY_IN_TEST, "ko")
    assert payload_for(sent) == payload


def test_claude_client_parses_json_and_refuses_anything_else() -> None:
    analysis = {
        "target": {"type": "chart"},
        "cutoff_utc": "2026-06-15T18:00:00Z",
        "facts": [],
        "missing": [],
        "uncertainty": [],
        "warnings": [],
    }
    seen: list[httpx.Request] = []

    def reply(text: str) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": text}]},
            )

        return httpx.MockTransport(handler)

    good = json.dumps(
        {
            "summary": "ok",
            "inferences": ["a"],
            "hypotheses": [],
            "questions": ["q"],
            "uncertainty": "u",
        }
    )
    result = ask_claude(analysis, KEY_IN_TEST, "en", transport=reply(good), now=lambda: NOW)
    assert result["summary"] == "ok" and result["questions"] == ["q"]
    request = seen[0]
    assert request.headers["x-api-key"] == KEY_IN_TEST
    body = json.loads(request.content)
    assert "No buy, sell or hold recommendations" in body["system"]
    assert json.loads(body["messages"][0]["content"]) == payload_for(analysis)
    with pytest.raises(CommentaryError, match="required format"):
        ask_claude(analysis, KEY_IN_TEST, "en", transport=reply("Buy now!"), now=lambda: NOW)
    refused = httpx.MockTransport(lambda _r: httpx.Response(401, json={}))
    with pytest.raises(CommentaryError, match="refused the API key"):
        ask_claude(analysis, KEY_IN_TEST, "en", transport=refused, now=lambda: NOW)
