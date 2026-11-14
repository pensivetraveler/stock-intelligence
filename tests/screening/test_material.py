"""Stage 2 material impact (ADR 0057 §2, U-b) from the news service's two conclusions.

Fixtures only: no network. Conclusions follow news-analysis `docs/service-api.md`
(news_check 1.1/1.2, `material-judgement-2`); articles and numbers are invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.market_data.provider import BarsQuery, BarsResult
from services.screening.market_cap import Shares, market_cap_context, shares_from_stock_info
from services.screening.material import (
    flow_stage2,
    from_conclusions,
    stage2_by_symbol,
    stage2_verdict,
)
from services.screening.store import ScreeningStore
from tests.screening.test_news_service import FakeService, app_for, article, news_check

CUTOFF = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def conclusions(
    material: str, filter_: str | None, rationale: str | None, articles: list[Any] | None = None
) -> dict[str, Any]:
    return {
        "version": "material-judgement-2",
        "label": "INFERENCE",
        "notice": "…",
        "material": material,
        "filter": filter_,
        "rationale": rationale,
        "articles": articles or [],
    }


@pytest.mark.parametrize(
    ("material", "filter_", "rationale", "outcome", "label", "strategy"),
    [
        ("present", "yes", None, "excluded_today", "low_impact", None),
        ("present", "no", "yes", "passed", "rationale", "normal"),
        ("present", "no", "no", "passed", "no_rationale", "conservative"),
        ("present", "no", "unknown", "passed", "unconfirmed", "conservative"),
        ("present", "unknown", "unknown", "passed", "unconfirmed", "conservative"),
        # Filter unknown with a rationale: still not known to be not low-impact.
        ("present", "unknown", "yes", "passed", "unconfirmed", "conservative"),
        ("none", None, None, "passed", "none", "conservative"),
        ("unknown", None, None, "passed", "unconfirmed", "conservative"),
        ("something-new", None, None, "passed", "unconfirmed", "conservative"),
    ],
)
def test_each_combination_of_the_two_conclusions(
    material: str,
    filter_: str | None,
    rationale: str | None,
    outcome: str,
    label: str,
    strategy: str | None,
) -> None:
    verdict = from_conclusions(conclusions(material, filter_, rationale))
    assert (verdict["outcome"], verdict["material"], verdict["strategy"]) == (
        outcome,
        label,
        strategy,
    )
    assert verdict["conclusions"]["filter"] == filter_
    assert flow_stage2(verdict) == ("failed" if outcome == "excluded_today" else "passed")


def stored(status: str, check: dict[str, Any] | None, check_id: int = 7) -> dict[str, Any]:
    result: dict[str, Any] = {"lookback_hours": 72}
    if check is not None:
        result["news_check"] = check
    return {
        "check_id": check_id,
        "symbol": "ABCD",
        "status": status,
        "cutoff_utc": "2026-11-09T15:00:00.000Z",
        "result": result,
    }


def test_conclusions_win_over_the_status() -> None:
    check = news_check("no_material")
    check["conclusions"] = conclusions("present", "no", "yes")
    verdict = stage2_verdict(stored("no_material", check))
    assert verdict["outcome"] == "passed" and verdict["strategy"] == "normal"
    assert verdict["news_check_id"] == 7 and verdict["news_status"] == "no_material"


@pytest.mark.parametrize(
    ("made", "reason"),
    [
        (None, "no_news_check"),
        (stored("pending", None), "news_pending"),  # service down or still running
        (stored("error", None), "news_error"),
        (stored("material", news_check()), "no_conclusions"),  # news_check 1.0
    ],
)
def test_what_cannot_be_judged_passes_unconfirmed(made: dict[str, Any] | None, reason: str) -> None:
    verdict = stage2_verdict(made)
    assert verdict["outcome"] == "passed" and verdict["material"] == "unconfirmed"
    assert verdict["strategy"] == "conservative" and verdict["reason"] == reason


def test_the_basis_of_each_article_is_kept_with_inherited_nir() -> None:
    check = news_check()
    check["articles"].append(
        article("ABCD:art_3", "2026-11-09T13:30:00Z", "ABCD Receives $2M Purchase Order")
    )
    rows = [
        {
            "article": {"source_id": "marketnewsapi-news", "record_id": "ABCD:art_2"},
            "categories": ["offering_announcement"],
            "filter": "yes",
            "filter_basis": "rules",
            "filter_reasons": ["offering_announcement"],
            "rationale": "not_assessed",
            "rationale_basis": None,
            "nir_record_id": None,
            "nir_state": "skipped",
            "nir_reason": "rule_low_impact",
            "nir_inherited_from": None,
        },
        {
            "article": {"source_id": "marketnewsapi-news", "record_id": "ABCD:art_3"},
            "categories": ["binding_contract"],
            "filter": "no",
            "filter_basis": "rules",
            "filter_reasons": [],
            "rationale": "yes",
            "rationale_basis": "nir",
            "nir_record_id": "NIR-ABCD-1",
            "nir_state": "skipped",
            "nir_reason": "same_headline",
            "nir_inherited_from": {
                "source_id": "marketnewsapi-news",
                "record_id": "ABCD:art_1",
                "revision_sha256": "c" * 64,
            },
        },
    ]
    check["conclusions"] = conclusions("present", "no", "yes", rows)
    verdict = stage2_verdict(stored("material", check))
    first, second = verdict["articles"]
    assert first["headline"] == "ABCD Prices Registered Direct Offering"
    assert first["filter_basis"] == "rules" and first["rationale"] == "not_assessed"
    assert second["rationale_basis"] == "nir" and second["nir_record_id"] == "NIR-ABCD-1"
    assert second["nir_inherited_from"] == {
        "source_id": "marketnewsapi-news",
        "record_id": "ABCD:art_1",
    }


def test_a_newer_waiting_check_does_not_undo_a_finished_one(tmp_path: Path) -> None:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: CUTOFF)
    check = news_check()
    check["conclusions"] = conclusions("present", "yes", None)
    base = {
        "kind": "screening_check",
        "check": "news",
        "session_date": "2026-11-09",
        "symbol": "ABCD",
        "cutoff_utc": "2026-11-09T15:00:00.000Z",
        "created_at_utc": "2026-11-09T15:00:00.000Z",
    }
    screening.add_check({**base, "status": "material", "result": {"news_check": check}})
    screening.add_check({**base, "status": "pending", "result": {}})
    verdicts = stage2_by_symbol(screening, CUTOFF.date())
    assert verdicts["ABCD"]["outcome"] == "excluded_today"
    screening.close()


# --- cutoff market cap -------------------------------------------------------------------
def bars_until(last_end: datetime, close: str = "2.50", final: bool = True) -> Any:
    def get_bars(query: BarsQuery) -> BarsResult:
        assert query.as_of_utc == query.end_utc  # read as known at the cutoff
        made = []
        for minutes in (3, 2, 1, 0, -1):  # the last one ends after the cutoff
            end = last_end - timedelta(minutes=minutes)
            made.append(
                {
                    "interval_start_utc": (end - timedelta(minutes=1))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "interval_end_utc": end.isoformat().replace("+00:00", "Z"),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": "9.99" if minutes < 0 else close,
                    "volume": "100",
                    "volume_unit": "shares",
                    "is_final": final,
                    "source_id": "alpaca-sip",
                }
            )
        return BarsResult(tuple(made), {"source_id": "alpaca-sip", "missing_intervals": []})

    return get_bars


def test_market_cap_is_shares_times_the_last_close_before_the_cutoff() -> None:
    shares = Shares(Decimal("19300000"), CUTOFF - timedelta(seconds=1), "toss-us")
    context, note = market_cap_context("us.x", CUTOFF, shares, bars_until(CUTOFF))
    assert note is None and context is not None
    assert context["market_cap_usd"] == 48250000  # 19.3M x $2.50, not the later $9.99 bar
    assert context["market_cap_as_of_utc"] == "2026-11-09T15:00:00.000Z"
    assert context["market_cap_source"].startswith("siw:toss-shares_x_1m-close:toss-us")
    assert set(context) == {"market_cap_usd", "market_cap_as_of_utc", "market_cap_source"}


def test_nothing_later_than_the_cutoff_is_sent() -> None:
    late = Shares(Decimal("1000"), CUTOFF + timedelta(seconds=1), "toss-us")
    assert market_cap_context("us.x", CUTOFF, late, bars_until(CUTOFF))[0] is None
    # The service gets whole seconds: a read 0.4 s into the cutoff second is too late.
    cut = CUTOFF + timedelta(milliseconds=500)
    inside = Shares(Decimal("1000"), CUTOFF + timedelta(milliseconds=400), "toss-us")
    assert market_cap_context("us.x", cut, inside, bars_until(CUTOFF))[0] is None
    no_final = market_cap_context(
        "us.x", CUTOFF, Shares(Decimal("1000"), CUTOFF, "t"), bars_until(CUTOFF, final=False)
    )
    assert no_final[0] is None and "final" in str(no_final[1])
    assert market_cap_context("us.x", CUTOFF, None, bars_until(CUTOFF))[0] is None


def test_toss_stock_info_without_a_count_gives_no_shares() -> None:
    info = {"observed_at_utc": "2026-11-09T14:59:59Z", "source_id": "toss-us"}
    assert shares_from_stock_info({**info, "shares_outstanding": None}) is None
    assert shares_from_stock_info({**info, "shares_outstanding": "0"}) is None
    found = shares_from_stock_info({**info, "shares_outstanding": "19300000"})
    assert found is not None and found.count == Decimal("19300000")


def test_the_news_request_carries_the_context_and_a_refresh_resends_it(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from packages.contracts.validator import validate_document
    from services.api.main import _error_response
    from services.api.screening_news_routes import register_screening_news_routes
    from services.market_data.broker_common import instrument_listing
    from tests.screening.test_news_service import NOW, no_sleep

    fake = FakeService()
    fake.down = True
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)
    app = FastAPI()
    register_screening_news_routes(
        app,
        store=lambda: screening,
        listing_for=lambda s: instrument_listing(s, "XNAS", "test", "test-r1", "test"),
        news_service=fake.service,
        error_response=_error_response,
        clock=lambda: NOW,
        sleep=no_sleep,
        read_shares=lambda _s, _i: Shares(Decimal("19300000"), NOW, "toss-us"),
        get_bars=lambda: bars_until(NOW),
    )
    http = TestClient(app)
    waiting = http.post("/v1/screening/checks/news", json={"symbol": "ABCD"}).json()
    validate_document(waiting)
    assert waiting["status"] == "pending"
    assert waiting["result"]["context_sent"]["market_cap_usd"] == 48250000
    fake.down = False
    http.post(f"/v1/screening/checks/news/{waiting['check_id']}/refresh")
    sent = json.loads(fake.requests[-3].content)  # POST, then the job and result reads
    assert sent["context"] == waiting["result"]["context_sent"]
    assert sent["context"]["market_cap_as_of_utc"] <= sent["cutoff_utc"].replace("Z", ".000Z")

    # A past session's cutoff is its close: Toss read now would be later, so none is sent.
    past = http.post(
        "/v1/screening/checks/news", json={"symbol": "ABCD", "session_date": "2026-11-06"}
    ).json()
    assert "context" not in json.loads(fake.requests[-3].content)
    assert past["result"]["context_sent"] == {} and "past session" in past["result"]["context_note"]
    screening.close()


def test_without_a_share_source_no_context_is_sent(tmp_path: Path) -> None:
    fake = FakeService()
    http, screening = app_for(tmp_path, fake)
    done = http.post("/v1/screening/checks/news", json={"symbol": "ABCD"}).json()
    assert "context" not in json.loads(fake.requests[0].content)
    assert done["result"]["context_note"] == "no share count source is configured"
    screening.close()


# --- stage 3 and the candidates list -----------------------------------------------------
def test_excluded_symbols_are_not_watched_and_passed_ones_carry_the_strategy(
    tmp_path: Path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from packages.contracts.validator import validate_document
    from services.api.main import _error_response
    from services.api.screening_flow_routes import register_screening_flow_routes
    from services.market_data.broker_common import instrument_listing
    from tests.screening.test_flow_watch import _bars, session
    from tests.screening.test_momentum import gappy_record
    from tests.screening.test_screening import WINDOW

    gappy_record(tmp_path)
    now = WINDOW.pre_open_utc + timedelta(minutes=45, seconds=30)
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: now)
    day = WINDOW.session_date.isoformat()
    for symbol, made in (
        ("FLIP", conclusions("present", "yes", None)),
        ("EVT", conclusions("present", "no", "yes")),
    ):
        check = news_check()
        check["conclusions"] = made
        screening.add_check(
            {
                "kind": "screening_check",
                "check": "news",
                "session_date": day,
                "symbol": symbol,
                "status": "material",
                "cutoff_utc": "2026-11-09T09:30:00.000Z",
                "created_at_utc": "2026-11-09T09:30:00.000Z",
                "result": {"news_check": check},
            }
        )
    asked: list[str] = []

    def get_bars(query: BarsQuery) -> BarsResult:
        asked.append(query.instrument_id)
        count = int((query.end_utc - query.start_utc).total_seconds() // 60)
        bars = _bars(session(("mfffff" * 10)[:count]))
        for minute, item in enumerate(bars):
            start = query.start_utc + timedelta(minutes=minute)
            item["interval_start_utc"] = start.isoformat().replace("+00:00", "Z")
        return BarsResult(tuple(bars), {"source_id": "fixture", "missing_intervals": []})

    app = FastAPI()
    register_screening_flow_routes(
        app,
        store=lambda: screening,
        data_dir=lambda: tmp_path,
        listing_for=lambda s: instrument_listing(s, "XNAS", "test", "test-r1", "test"),
        get_bars=lambda: get_bars,
        error_response=_error_response,
        clock=lambda: now,
    )
    with TestClient(app) as client:
        document = client.get("/v1/screening/flow").json()
    validate_document(document)
    rows = {row["symbol"]: row for row in document["candidates"]}
    flip = rows["FLIP"]
    assert flip["status"] == "stage2_excluded" and flip["result"]["stage2"] == "failed"
    assert flip["result"]["new_buys_allowed"] is False
    assert not any("flip" in item.lower() for item in asked)  # no bars read for it
    evt = rows["EVT"]["result"]
    assert evt["stage2"] == "passed" and evt["stage2_detail"]["strategy"] == "normal"
    assert rows["QUIET"]["result"]["stage2_detail"]["reason"] == "no_news_check"
    assert document["versions"]["stage2_rules"] == "adr-0057-stage2-1"

    # The candidates list shows the same verdicts.
    from services.api.screening_routes import register_screening_routes

    listing_app = FastAPI()
    register_screening_routes(
        listing_app,
        store=lambda: screening,
        data_dir=lambda: tmp_path,
        listing_for=lambda s: instrument_listing(s, "XNAS", "test", "test-r1", "test"),
        get_bars=lambda: get_bars,
        error_response=_error_response,
        clock=lambda: now,
    )
    with TestClient(listing_app) as client:
        listed = client.get("/v1/screening/candidates").json()
    validate_document(listed)
    by_symbol = {row["symbol"]: row["stage2"] for row in listed["candidates"]}
    assert by_symbol["FLIP"]["outcome"] == "excluded_today"
    assert by_symbol["EVT"]["material"] == "rationale"
    screening.close()
