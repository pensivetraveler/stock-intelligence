"""Check B (ADR 0052 §3) against a fake news-analysis service (`news-contract-v1`).

No network: the service is an `httpx.MockTransport`. The articles are invented.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.api.main import _error_response
from services.api.screening_news_routes import register_screening_news_routes
from services.market_data.broker_common import instrument_listing
from services.screening.news_service import (
    NewsService,
    NewsServiceError,
    follow,
    service_url,
    summary,
)
from services.screening.store import ScreeningStore

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)  # 10:00 New York, session open
JOB = "a" * 32
RESULT = "b" * 32


def article(record_id: str, published: str, headline: str) -> dict[str, Any]:
    return {
        "kind": "article",
        "schema_version": "1.0",
        "source_id": "marketnewsapi-news",
        "record_id": record_id,
        "article_id": record_id.split(":")[1],
        "revision_sha256": "c" * 64,
        "ticker": "ABCD",
        "headline": headline,
        "summary": "",
        "generated_summary": None,
        "author": "",
        "publisher": "Example Newswire",
        "article_type": "press_release",
        "symbols": ["ABCD"],
        "url": "https://example.invalid/pr",
        "body": None,
        "body_kept": False,
        "published_at_utc": published,
        "updated_at_utc": None,
        "first_observed_utc": published,
        "revision_observed_utc": published,
        "revisions": 1,
        "observed_by_cutoff": True,
        "revised_after_cutoff": False,
        "parser_version": "marketnewsapi-1",
        "terms": {
            "personal_use_only": True,
            "redistribution": False,
            "delete_on_subscription_end": False,
            "body_display": "press_release_with_attribution",
        },
        "same_headline_as": [],
    }


def classification(
    record_id: str, published: str, categories: list[str], dilution: bool
) -> dict[str, Any]:
    return {
        "kind": "article_classification",
        "schema_version": "1.0",
        "method": "rules",
        "rules_version": "news-rules-2",
        "rules_sha256": "d" * 64,
        "article": {
            "source_id": "marketnewsapi-news",
            "record_id": record_id,
            "revision_sha256": "c" * 64,
            "published_at_utc": published,
        },
        "status": "categorized" if categories else ("out_of_scope" if dilution else "none"),
        "categories": categories,
        "matches": {c: ["purchase order"] for c in categories},
        "suppressed": [],
        "out_of_scope": [
            {
                "topic": "dilution",
                "phrases": ["registered direct offering"],
                "route_to": "stock-dilution-analyzer",
            }
        ]
        if dilution
        else [],
        "evidence_level": "keyword_match",
        "note": "",
    }


def news_check(status: str = "material") -> dict[str, Any]:
    articles = [
        article("ABCD:art_1", "2026-11-09T12:00:00Z", "ABCD Receives $2M Purchase Order"),
        article("ABCD:art_2", "2026-11-09T13:00:00Z", "ABCD Prices Registered Direct Offering"),
    ]
    return {
        "kind": "news_check",
        "schema_version": "1.0",
        "contract": "news-contract-v1",
        "ticker": "ABCD",
        "cutoff_utc": "2026-11-09T15:00:00Z",
        "window_start_utc": "2026-11-06T15:00:00Z",
        "lookback_hours": 72,
        "generated_at_utc": "2026-11-09T15:00:02Z",
        "status": status,
        "reason": None,
        "feeds": [
            {
                "source_id": "marketnewsapi-news",
                "state": "ok",
                "articles": 2,
                "truncated": False,
                "requests": 1,
                "last_download_utc": "2026-11-09T15:00:01Z",
                "notes": [],
            },
            {
                "source_id": "finnhub-news",
                "state": "no_key",
                "articles": 0,
                "truncated": False,
                "requests": 0,
                "last_download_utc": None,
                "notes": [],
            },
        ],
        "articles": articles,
        "classifications": [
            classification("ABCD:art_1", "2026-11-09T12:00:00Z", ["binding_contract"], False),
            classification("ABCD:art_2", "2026-11-09T13:00:00Z", [], True),
        ],
        "categories": {"binding_contract": 1},
        "out_of_scope": {"dilution": {"articles": 1, "route_to": "stock-dilution-analyzer"}},
        "warnings": ["MarketNewsAPI answered with a 15-minute delay (free plan)."],
        "uncertainty": [],
        "versions": {
            "service": "0.1.0",
            "rules_version": "news-rules-2",
            "rules_sha256": "d" * 64,
            "parsers": {"marketnewsapi-news": "marketnewsapi-1"},
        },
    }


class FakeService:
    """The service's three routes; `states` is what successive job reads answer."""

    def __init__(self, states: list[str] | None = None, check: dict[str, Any] | None = None):
        self.states = states or ["done"]
        self.check = check or news_check()
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/news-checks":
            return httpx.Response(202, json={"job_id": JOB})
        if path == f"/api/v1/jobs/{JOB}":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            done = state == "done"
            return httpx.Response(
                200,
                json={
                    "job_id": JOB,
                    "state": state,
                    "result_id": RESULT if done else None,
                    "error": {"code": "internal", "message": "boom"} if state == "failed" else None,
                },
            )
        if path == f"/api/v1/results/{RESULT}":
            return httpx.Response(200, content=json.dumps(self.check).encode())
        return httpx.Response(404)

    def service(self) -> NewsService:
        return NewsService("http://127.0.0.1:8776", httpx.MockTransport(self))


def no_sleep(_seconds: float) -> None:
    return None


# --- client ---------------------------------------------------------------------------


def test_service_url_must_be_this_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert service_url() == "http://127.0.0.1:8776"
    monkeypatch.setenv("SIW_NEWS_URL", "http://news.example.com:8776")
    with pytest.raises(NewsServiceError, match="this computer"):
        service_url()


def test_submit_sends_ticker_cutoff_and_window() -> None:
    fake = FakeService()
    assert fake.service().submit("ABCD", NOW, 72) == JOB
    body = json.loads(fake.requests[0].content)
    assert body == {
        "ticker": "ABCD",
        "cutoff_utc": "2026-11-09T15:00:00Z",
        "lookback_hours": 72,
        "refresh": "auto",
    }


def test_follow_keeps_the_service_document_and_summarizes_it() -> None:
    fake = FakeService(states=["queued", "running", "done"])
    status, result = follow(fake.service(), JOB, 15.0, no_sleep)
    assert status == "material"
    assert result["result_id"] == RESULT
    assert result["news_check"] == news_check()  # unchanged
    assert result["categories"] == {"binding_contract": 1}
    assert result["out_of_scope"]["dilution"]["articles"] == 1
    assert result["latest_categorized"]["record_id"] == "ABCD:art_1"
    assert [f["state"] for f in result["feeds"]] == ["ok", "no_key"]


def test_follow_waits_then_reports_pending() -> None:
    status, result = follow(FakeService(states=["running"]).service(), JOB, 1.0, no_sleep)
    assert status == "pending" and result["job_id"] == JOB


def test_failed_job_is_an_error() -> None:
    status, result = follow(FakeService(states=["failed"]).service(), JOB, 1.0, no_sleep)
    assert status == "error" and "internal" in result["reason"]


def test_other_contract_is_refused() -> None:
    fake = FakeService(check={**news_check(), "contract": "news-contract-v2"})
    with pytest.raises(NewsServiceError, match="news-contract-v1"):
        follow(fake.service(), JOB, 1.0, no_sleep)


def test_summary_without_categories() -> None:
    quiet = summary({**news_check("no_material"), "categories": {}})
    assert quiet["categories"] == {}
    assert quiet["article_count"] == 2


def nir_block(state: str = "done", items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "label": "INFERENCE",
        "notice": "LLM inference, not verified fact.",
        "state": state,
        "reason": None if state == "done" else "no_llm_key",
        "rules_only": not items,
        "model": "openai/gpt-6-luna",
        "prompt_version": "nir-judge-1",
        "items": items or [],
    }


NIR_SUMMARY = {
    "event_type": "Purchase Order",
    "counterparty": "Invented Utility Co",
    "legally_binding": "Yes",
    "purchase_obligation": "Yes",
    "committed_amount": "$2 million",
    "potential_amount": "UNKNOWN",
    "economic_substance": "Moderate",
    "promotion_risk": "Low",
    "evidence_strength": "Company claim",
    "confidence": "Medium",
    "summary_ko": "지어낸 회사가 200만 달러 구매 주문을 받았다고 발표(추론).",
    "verification_failures": 0,
    "rule_conflicts": [],
}


def test_summary_digests_the_llm_reading_as_inference() -> None:
    item = {
        "article": {
            "source_id": "marketnewsapi-news",
            "record_id": "ABCD:art_1",
            "revision_sha256": "c" * 64,
        },
        "state": "created",
        "nir_record_id": "NIR-ABCD-20261109-mna-art_1-001",
        "cost_usd": 0.0004,
        "summary": NIR_SUMMARY,
    }
    skipped = {**item, "state": "skipped", "reason": "same_headline", "nir_record_id": None}
    del skipped["summary"]
    digest = summary({**news_check(), "nir": nir_block("done", [item, skipped])})["nir"]
    assert digest["label"] == "INFERENCE" and digest["rules_only"] is False
    [only] = digest["items"]
    assert only["headline"] == "ABCD Receives $2M Purchase Order"
    assert only["event_type"] == "Purchase Order" and only["committed_amount"] == "$2 million"
    assert only["nir_record_id"].startswith("NIR-ABCD-")
    assert "cost_usd" not in only


def test_summary_without_llm_is_rules_only() -> None:
    old = summary(news_check())["nir"]  # made before the LLM step existed
    assert old["rules_only"] is True and old["state"] is None and old["items"] == []
    stopped = summary({**news_check(), "nir": nir_block("skipped")})["nir"]
    assert stopped["rules_only"] is True and stopped["reason"] == "no_llm_key"


def test_llm_versions_are_recorded(tmp_path: Path) -> None:
    check = news_check()
    check["nir"] = nir_block("not_needed")
    check["versions"] = {
        **check["versions"],
        "nir_prompt_version": "nir-judge-1",
        "nir_model": "openai/gpt-6-luna",
    }
    http, screening = app_for(tmp_path, FakeService(check=check))
    stored = http.post("/v1/screening/checks/news", json={"symbol": "ABCD"}).json()
    validate_document(stored)
    assert stored["status"] == "material"  # the LLM never changes the status
    assert stored["versions"]["nir_prompt_version"] == "nir-judge-1"
    assert stored["result"]["nir"]["state"] == "not_needed"
    screening.close()


# --- routes ---------------------------------------------------------------------------


def app_for(tmp_path: Path, fake: FakeService | None) -> tuple[TestClient, ScreeningStore]:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: NOW)

    def listing_for(symbol: str) -> dict[str, Any] | None:
        if symbol != "ABCD":
            return None
        return instrument_listing(symbol, "XNAS", "test", "test-r1", "test")

    app = FastAPI()
    register_screening_news_routes(
        app,
        store=lambda: screening,
        listing_for=listing_for,
        news_service=None if fake is None else fake.service,
        error_response=_error_response,
        clock=lambda: NOW,
        sleep=no_sleep,
    )
    return TestClient(app), screening


def test_route_stores_the_check(tmp_path: Path) -> None:
    http, screening = app_for(tmp_path, FakeService())
    response = http.post("/v1/screening/checks/news", json={"symbol": "abcd"})
    assert response.status_code == 200, response.text
    stored = response.json()
    validate_document(stored)
    assert stored["check"] == "news" and stored["status"] == "material"
    assert stored["cutoff_utc"].startswith("2026-11-09T15:00:00")
    assert stored["after_the_fact"] is False
    assert stored["versions"]["contract"] == "news-contract-v1"
    assert stored["versions"]["rules_version"] == "news-rules-2"
    assert stored["versions"]["parser.marketnewsapi-news"] == "marketnewsapi-1"
    assert stored["sources"] == {
        "service": "news-analysis",
        "contract": "news-contract-v1",
        "result_id": RESULT,
    }
    assert any("15-minute delay" in w for w in stored["warnings"])
    assert screening.latest(date(2026, 11, 9))["ABCD"]["news"]["status"] == "material"
    screening.close()


def test_past_session_asks_at_its_close(tmp_path: Path) -> None:
    fake = FakeService()
    http, screening = app_for(tmp_path, fake)
    stored = http.post(
        "/v1/screening/checks/news",
        json={"symbol": "ABCD", "session_date": "2026-11-06", "lookback_hours": 168},
    ).json()
    sent = json.loads(fake.requests[0].content)
    assert sent["cutoff_utc"] == "2026-11-07T01:00:00Z"  # 20:00 New York, Friday
    assert sent["lookback_hours"] == 168
    assert stored["after_the_fact"] is True
    screening.close()


def test_service_down_waits_and_refresh_picks_it_up(tmp_path: Path) -> None:
    fake = FakeService()
    fake.down = True
    http, screening = app_for(tmp_path, fake)
    waiting = http.post("/v1/screening/checks/news", json={"symbol": "ABCD"}).json()
    assert waiting["status"] == "pending"
    assert "did not answer" in waiting["result"]["reason"]
    fake.down = False
    refreshed = http.post(f"/v1/screening/checks/news/{waiting['check_id']}/refresh").json()
    assert refreshed["status"] == "material"
    assert refreshed["check_id"] != waiting["check_id"]
    assert refreshed["cutoff_utc"] == waiting["cutoff_utc"]  # the same cutoff, asked again
    screening.close()


def test_refresh_leaves_finished_checks_alone(tmp_path: Path) -> None:
    http, screening = app_for(tmp_path, FakeService())
    done = http.post("/v1/screening/checks/news", json={"symbol": "ABCD"}).json()
    again = http.post(f"/v1/screening/checks/news/{done['check_id']}/refresh").json()
    assert again == done
    screening.close()


def test_unconfigured_service_is_pending(tmp_path: Path) -> None:
    http, screening = app_for(tmp_path, None)
    stored = http.post("/v1/screening/checks/news", json={"symbol": "ABCD"}).json()
    assert stored["status"] == "pending"
    screening.close()


def test_unknown_symbols_and_dates_are_refused(tmp_path: Path) -> None:
    http, screening = app_for(tmp_path, FakeService())
    assert http.post("/v1/screening/checks/news", json={"symbol": "ZZZZ"}).status_code == 422
    weekend = {"symbol": "ABCD", "session_date": "2026-11-08"}
    assert http.post("/v1/screening/checks/news", json=weekend).status_code == 422
    screening.close()
