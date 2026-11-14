"""Stage 4 runs by themselves (ADR 0057 §4, U-c) and the information the next stage reads.

No network: the dilution analyzer is an `httpx.MockTransport` fake, Alpaca trades come
from the off-exchange test tape, the condition record is written to a temporary folder.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import _error_response
from services.api.screening_off_exchange_routes import OffExchangeChecks
from services.api.screening_routes import DilutionChecks, register_screening_routes
from services.api.screening_stage4 import Stage4Runner
from services.collector.condition_watch import ConditionWatchStore
from services.market_data.broker_common import instrument_listing
from services.market_data.calendar import UsEquityCalendar
from services.market_data.kiwoom_conditions import Condition, ConditionEvent, ConditionTicker
from services.screening.dilution import DilutionService
from services.screening.stage4 import stage4_summary
from services.screening.store import ScreeningStore
from tests.screening.test_dilution import Analyzer
from tests.screening.test_off_exchange import NOW, adapter, friday_and_thursday
from tests.screening.test_off_exchange_api import shared

CALENDAR = UsEquityCalendar("XNAS")
WINDOW = CALENDAR.window_for_date(NOW.date())
assert WINDOW is not None
DAY = WINDOW.session_date.isoformat()
CONDITION = Condition("2", "SIW_당일급등주")
KNOWN = {"PASS", "EXCL", "RATE"}


def record(data_dir: Path) -> None:
    """PASS and EXCL in the list at the start, RATE entering later, ZZZZ unknown to the app."""
    assert WINDOW is not None
    store = ConditionWatchStore.in_data_dir(data_dir)
    first = WINDOW.pre_open_utc + timedelta(minutes=1)
    watch = store.start(
        CONDITION,
        "real",
        first,
        (
            ConditionTicker("PASS", "ND"),
            ConditionTicker("EXCL", "ND"),
            ConditionTicker("ZZZZ", "ND"),
        ),
    )
    store.add(watch, first + timedelta(minutes=5), ConditionEvent("enter", "RATE", "ND", None, {}))
    store.close()


def news(symbol: str, material: str, filter_: str | None, rationale: str | None) -> dict[str, Any]:
    """A stored news check (check B) carrying only the two conclusions stage 2 reads."""
    return {
        "kind": "screening_check",
        "schema_version": "0.1",
        "check": "news",
        "session_date": DAY,
        "symbol": symbol,
        "status": "material",
        "cutoff_utc": "2026-11-06T14:00:00.000Z",
        "created_at_utc": "2026-11-06T14:00:00.000Z",
        "result": {
            "news_check": {
                "articles": [],
                "conclusions": {
                    "version": "material-judgement-2",
                    "material": material,
                    "filter": filter_,
                    "rationale": rationale,
                    "articles": [],
                },
            }
        },
    }


def listing_for(symbol: str) -> dict[str, Any] | None:
    if symbol not in KNOWN:
        return None
    return instrument_listing(symbol, "XNAS", "test", "test-r1", "test")


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        record(tmp_path)
        self.now = NOW
        self.root = tmp_path
        self.screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: self.now)
        self.analyzer = Analyzer(["done"])
        self.trades: Any = adapter(friday_and_thursday())
        self.structure = shared(tmp_path, lambda: self.trades)

        def service() -> DilutionService:
            return DilutionService(
                "http://127.0.0.1:8775", transport=httpx.MockTransport(self.analyzer)
            )

        self.dilution = DilutionChecks(
            clock=lambda: self.now,
            dilution_service=service,
            wait_seconds=0,
            sleep=lambda _seconds: None,
        )
        self.runner = Stage4Runner(
            store=lambda: self.screening,
            data_dir=lambda: tmp_path,
            listing_for=listing_for,
            dilution=self.dilution,
            off_exchange=OffExchangeChecks(
                finra_review=lambda: None, clock=lambda: self.now, sleep=lambda _s: None
            ),
            structure=self.structure,
            trades=lambda: self.trades,
            clock=lambda: self.now,
            condition_name=CONDITION.name,
        )
        app = FastAPI()
        register_screening_routes(
            app,
            store=lambda: self.screening,
            data_dir=lambda: tmp_path,
            listing_for=listing_for,
            get_bars=lambda: lambda query: None,  # type: ignore[arg-type,return-value]
            error_response=_error_response,
            clock=lambda: self.now,
            dilution_service=service,
            dilution_wait_seconds=0,
            sleep=lambda _seconds: None,
        )
        self.app = app

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return self.screening.documents(WINDOW.session_date, kind)  # type: ignore[union-attr]

    def candidates(self, **params: str) -> dict[str, Any]:
        with TestClient(self.app) as client:
            document = client.get(
                "/v1/screening/candidates", params={"session_date": DAY, **params}
            ).json()
        validate_document(document)
        return {item["symbol"]: item for item in document["candidates"]}


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[Setup]:
    made = Setup(tmp_path)
    # EXCL: a low-impact material, excluded for today. RATE: a rally rationale.
    made.screening.add_check(news("EXCL", "present", "yes", None))
    made.screening.add_check(news("RATE", "present", "no", "yes"))
    yield made
    made.screening.close()


def test_passed_and_unconfirmed_candidates_run_and_excluded_ones_do_not(setup: Setup) -> None:
    report = setup.runner.tick()
    assert report is not None
    assert report["excluded"] == ["EXCL"] and report["unknown_symbol"] == ["ZZZZ"]
    # One rules-only analysis at a time: PASS first (earliest seen).
    assert setup.analyzer.posted == [{"ticker": "PASS", "as_of": DAY, "llm": False}]
    assert report["off_exchange_ran"] == ["PASS", "RATE"]
    dilution = setup.kinds("dilution")[0]
    note = dilution["result"]["stage4"]
    assert note["requested_by"] == "stage4_auto" and note["stage2_reason"] == "no_news_check"
    assert note["stage2_outcome"] == "passed"
    off = {d["symbol"]: d for d in setup.kinds("off_exchange")}
    assert off["RATE"]["result"]["stage4"]["stage2_reason"] == "rally_rationale"
    for document in [dilution, *off.values()]:
        validate_document(document)
    # The next tick asks for RATE's analysis; nothing runs twice.
    second = setup.runner.tick()
    assert second is not None and second["dilution_asked"] == "RATE"
    assert second["off_exchange_ran"] == []
    third = setup.runner.tick()
    assert third is not None and third["dilution_asked"] is None
    assert len(setup.analyzer.posted) == 2 and len(setup.kinds("off_exchange")) == 2
    assert not [d for d in setup.kinds("dilution") if d["symbol"] == "EXCL"]


def test_the_candidates_carry_the_stage4_information(setup: Setup) -> None:
    setup.runner.tick()
    setup.runner.tick()
    rows = setup.candidates()
    passed = rows["PASS"]["stage4"]
    assert passed["state"] == "done" and passed["stage2_outcome"] == "passed"
    assert passed["dilution"]["requested_by"] == "stage4_auto"
    assert passed["dilution"]["securities"][0]["price"]["amount"] == "1.25"
    # The fake analyzer answers as of a later date than the session: said, not hidden.
    assert passed["dilution"]["analysis_as_of"] == "2026-11-09"
    assert any("미래 자료" in note for note in passed["notes"])
    assert passed["off_exchange"]["levels"], "volume peaks from the tape"
    assert passed["off_exchange"]["data_until_utc"] <= passed["off_exchange"]["cutoff_utc"]
    assert passed["short_interest"] == {
        "available": False,
        "reason": "no_source",
        "note": passed["short_interest"]["note"],
    }
    excluded = rows["EXCL"]["stage4"]
    assert excluded["state"] == "stage2_excluded"
    assert excluded["dilution"] is None and excluded["off_exchange"] is None


def test_a_result_the_user_already_has_is_reused(setup: Setup) -> None:
    with TestClient(setup.app) as client:
        made = client.post(
            "/v1/screening/checks/dilution", json={"symbol": "PASS", "session_date": DAY}
        ).json()
    assert made["status"] == "ok"
    report = setup.runner.tick()
    assert report is not None and report["dilution_asked"] == "RATE"
    assert [p["ticker"] for p in setup.analyzer.posted] == ["PASS", "RATE"]
    info = setup.candidates()["PASS"]["stage4"]
    assert info["dilution"]["requested_by"] == "user"


def test_an_llm_analysis_is_not_stage4_information(setup: Setup) -> None:
    with TestClient(setup.app) as client:
        client.post(
            "/v1/screening/checks/dilution",
            json={"symbol": "PASS", "session_date": DAY, "llm": True},
        )
    setup.runner.tick()
    # The rules-only analysis is still asked for (the LLM one is for the weekly review).
    assert setup.analyzer.posted[-1] == {"ticker": "PASS", "as_of": DAY, "llm": False}


def test_a_stopped_analyzer_waits_without_piling_up(setup: Setup) -> None:
    setup.analyzer.down = True
    setup.runner.tick()
    setup.runner.tick()
    waiting = setup.kinds("dilution")
    assert [(d["symbol"], d["status"]) for d in waiting] == [("PASS", "pending")]
    assert "did not answer" in waiting[0]["result"]["reason"]
    assert setup.candidates()["PASS"]["stage4"]["state"] == "waiting"
    # Running again, the waiting check is asked for and the next one waits a tick.
    setup.analyzer.down = False
    report = setup.runner.tick()
    assert report is not None and report["dilution_refreshed"] == ["PASS"]
    assert report["dilution_asked"] == "RATE"
    latest = setup.kinds("dilution")
    assert latest[1]["status"] == "ok" and latest[1]["result"]["stage4"]["requested_by"] == (
        "stage4_auto"
    )


def test_a_slow_analysis_holds_back_the_next_one(setup: Setup) -> None:
    setup.analyzer.states = ["queued"]
    setup.runner.tick()
    report = setup.runner.tick()
    assert report is not None and report["dilution_blocked"]
    assert len(setup.analyzer.posted) == 1
    setup.analyzer.states = ["done"]
    report = setup.runner.tick()
    assert report is not None and report["dilution_refreshed"] == ["PASS"]
    assert len(setup.analyzer.posted) == 2


def test_no_alpaca_key_skips_check_d_only(setup: Setup) -> None:
    setup.trades = None
    report = setup.runner.tick()
    assert report is not None and report["off_exchange_skipped"] == "alpaca_unconfigured"
    assert report["dilution_asked"] == "PASS"


def test_nothing_runs_outside_the_session(setup: Setup) -> None:
    assert WINDOW is not None
    setup.now = WINDOW.post_close_utc + timedelta(minutes=1)
    assert setup.runner.tick() is None
    setup.now = WINDOW.pre_open_utc - timedelta(minutes=1)
    assert setup.runner.tick() is None
    assert setup.analyzer.posted == []


def test_an_earlier_view_leaves_out_later_results(setup: Setup) -> None:
    setup.runner.tick()
    earlier = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    info = setup.candidates(as_of=earlier)["PASS"]["stage4"]
    assert info["state"] == "not_started" and info["as_of_utc"] is not None
    assert setup.candidates()["PASS"]["stage4"]["state"] == "done"


def test_the_summary_without_checks_says_not_started() -> None:
    verdict = {"outcome": "passed", "reason": "no_news_check"}
    summary = stage4_summary(verdict, None, None)
    assert summary["state"] == "not_started" and summary["short_interest"]["available"] is False


def test_the_contract_fixture_is_valid() -> None:
    path = Path("tests/fixtures/contracts/valid/screening-candidates-stage4.json")
    validate_document(json.loads(path.read_text()))
