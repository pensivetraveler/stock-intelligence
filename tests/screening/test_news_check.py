"""Screening check B — news catalysts (ADR 0052 §3) against a mocked Alpaca News API.

No network: every request goes to `httpx.MockTransport`. Articles are invented.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document, validation_errors
from services.api.main import _error_response
from services.api.screening_news_routes import register_screening_news_routes
from services.market_data.broker_common import instrument_listing
from services.public_sources.alpaca_news import ALPACA_NEWS, AlpacaNewsCredential
from services.public_sources.cache import utc_text
from services.public_sources.http import RateLimiter
from services.public_sources.sources import PublicSources
from services.screening.news import news_check
from services.screening.news_rules import NONE, classify, rules_checksum
from services.screening.store import ScreeningStore

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)  # 09:00 ET
ABCD = "us:abcd:xnas:2026"
KEY = AlpacaNewsCredential("test-key-id", "test-secret-not-real")


def article(
    article_id: int,
    created: str,
    headline: str,
    summary: str = "",
    updated: str | None = None,
    symbols: tuple[str, ...] = ("ABCD",),
) -> dict[str, Any]:
    return {
        "id": article_id,
        "headline": headline,
        "author": "Test Desk",
        "created_at": created,
        "updated_at": updated or created,
        "summary": summary,
        "content": "<p>Body text that must never be stored.</p>",
        "images": [{"size": "thumb", "url": "https://example.invalid/i.png"}],
        "symbols": list(symbols),
        "source": "benzinga",
        "url": f"https://example.invalid/news/{article_id}",
    }


ARTICLES = [
    # After the cutoff: never in the result.
    article(105, "2026-11-09T14:30:00Z", "ABCD Announces FDA Clearance for Its Lead Device"),
    article(
        104,
        "2026-11-09T12:15:00Z",
        "ABCD Announces Pricing of $8 Million Registered Direct Offering",
        "The company agreed to sell shares and warrants to an institutional investor.",
    ),
    article(103, "2026-11-08T21:05:00Z", "ABCD Shares Trade Higher After Hours"),
    article(
        102,
        "2026-11-07T13:00:00Z",
        "ABCD Receives Nasdaq Notice Regarding Minimum Bid Price Requirement",
    ),
    # Older than the 72-hour window.
    article(101, "2026-11-01T13:00:00Z", "ABCD Reports Third Quarter Financial Results"),
]


class Upstream:
    def __init__(self, pages: list[list[dict[str, Any]]] | None = None) -> None:
        self.pages = pages or [ARTICLES]
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.offline = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "forbidden"})
        assert request.url.host == "data.alpaca.markets"
        assert request.url.path == "/v1beta1/news"
        token = request.url.params.get("page_token")
        index = int(token) if token else 0
        start = request.url.params["start"]
        page = [a for a in self.pages[index] if a["created_at"] >= start]
        following = str(index + 1) if index + 1 < len(self.pages) else None
        return httpx.Response(200, json={"news": page, "next_page_token": following})


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def sources_for(
    tmp_path: Path, upstream: Upstream, clock: Clock, key: AlpacaNewsCredential | None = KEY
) -> PublicSources:
    return PublicSources(
        tmp_path / "public_sources.sqlite3",
        clock,
        user_agent=lambda: None,
        finra_credential=lambda: None,
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
        alpaca_credential=lambda: key,
        news_limiter=RateLimiter(0),
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


def envelope(
    outcome: dict[str, Any], cutoff: datetime, instrument_id: str = ABCD
) -> dict[str, Any]:
    """The route's `screening_check` around the outcome, as stored (check_id 0)."""
    return {
        "kind": "screening_check",
        "schema_version": "0.1",
        "check_id": 0,
        "check": "news",
        "session_date": "2026-11-09",
        "symbol": "ABCD",
        "instrument_id": instrument_id,
        "cutoff_utc": utc_text(cutoff),
        "created_at_utc": "2026-11-09T15:00:00Z",
        "after_the_fact": False,
        **outcome,
    }


def run(sources: PublicSources, cutoff: datetime = CUTOFF, **kwargs: Any) -> dict[str, Any]:
    document = envelope(news_check([sources.alpaca_news()], ABCD, cutoff, **kwargs), cutoff)
    validate_document(document)
    return document


# --- fetching and storing -------------------------------------------------------------


def test_request_asks_for_no_bodies_and_sends_the_key_in_headers(
    tmp_path: Path, clock: Clock
) -> None:
    upstream = Upstream()
    run(sources_for(tmp_path, upstream, clock))
    (request,) = upstream.requests
    params = request.url.params
    assert params["symbols"] == "ABCD"
    assert params["include_content"] == "false"
    assert params["limit"] == "50"
    assert params["start"] == "2026-11-06T14:00:00Z"  # cutoff - 72 hours
    assert params["end"] == "2026-11-09T15:00:00Z"  # now: later revisions are kept too
    assert request.headers["APCA-API-KEY-ID"] == "test-key-id"
    assert request.headers["APCA-API-SECRET-KEY"] == "test-secret-not-real"
    assert "test-secret" not in str(request.url)


def test_bodies_and_images_are_never_stored(tmp_path: Path, clock: Clock) -> None:
    sources = sources_for(tmp_path, Upstream(), clock)
    run(sources)
    records = sources.cache.records(ALPACA_NEWS, "ABCD:")
    assert records
    for record in records:
        assert "content" not in record.document
        assert "images" not in record.document
    raw = (tmp_path / "public_sources.sqlite3").read_bytes()
    assert b"must never be stored" not in raw
    assert b"test-secret" not in raw


def test_secret_is_not_in_the_credential_repr() -> None:
    assert "test-secret" not in repr(KEY)


def test_pages_are_followed(tmp_path: Path, clock: Clock) -> None:
    upstream = Upstream(pages=[ARTICLES[:2], ARTICLES[2:]])
    document = run(sources_for(tmp_path, upstream, clock))
    assert len(upstream.requests) == 2
    assert upstream.requests[1].url.params["page_token"] == "1"
    assert [a["article_id"] for a in document["result"]["articles"]] == ["104", "103", "102"]


def test_recent_download_is_reused(tmp_path: Path, clock: Clock) -> None:
    upstream = Upstream()
    sources = sources_for(tmp_path, upstream, clock)
    run(sources)
    clock.now = NOW + timedelta(minutes=2)
    run(sources)
    assert len(upstream.requests) == 1


# --- point in time --------------------------------------------------------------------


def test_only_articles_created_by_the_cutoff_count(tmp_path: Path, clock: Clock) -> None:
    document = run(sources_for(tmp_path, Upstream(), clock))
    ids = [a["article_id"] for a in document["result"]["articles"]]
    assert ids == ["104", "103", "102"]  # newest first; 105 is after, 101 before the window
    assert document["cutoff_utc"] == "2026-11-09T14:00:00Z"
    assert document["result"]["window_start_utc"] == "2026-11-06T14:00:00Z"
    assert all(
        a["published_at_utc"] <= document["cutoff_utc"] for a in document["result"]["articles"]
    )
    assert all(f["hindsight"] is False for f in document["result"]["facts"])
    assert [s["record_ref"] for s in document["sources"]["records"]] == [
        "ABCD:104",
        "ABCD:103",
        "ABCD:102",
    ]
    assert all("available_at_utc" not in s for s in document["sources"]["records"])


def test_backfilled_articles_say_availability_is_reconstructed(
    tmp_path: Path, clock: Clock
) -> None:
    # Downloaded at 15:00 for a 14:00 cutoff: nothing was observed by the cutoff.
    document = run(sources_for(tmp_path, Upstream(), clock))
    assert not any(a["observed_by_cutoff"] for a in document["result"]["articles"])
    assert any("reconstructed" in line for line in document["result"]["uncertainty"])


def test_revisions_are_kept_and_chosen_by_what_was_seen_at_the_cutoff(
    tmp_path: Path, clock: Clock
) -> None:
    first = [article(201, "2026-11-09T12:00:00Z", "ABCD Shares Move Higher")]
    upstream = Upstream(pages=[first])
    sources = sources_for(tmp_path, upstream, clock)
    clock.now = datetime(2026, 11, 9, 12, 30, tzinfo=timezone.utc)
    early = datetime(2026, 11, 9, 12, 20, tzinfo=timezone.utc)
    assert run(sources, cutoff=early)["status"] == "no_material"

    upstream.pages = [
        [
            article(
                201,
                "2026-11-09T12:00:00Z",
                "ABCD Shares Move Higher on Merger Agreement",
                updated="2026-11-09T13:10:00Z",
            )
        ]
    ]
    clock.now = NOW
    records = sources.cache.records(ALPACA_NEWS, "ABCD:")
    assert len(records) == 1 and records[0].revisions == 1

    # At 14:00 SIW had seen only the first revision (12:30); the edit arrived at 15:00.
    at_two = run(sources)
    (kept,) = at_two["result"]["articles"]
    assert kept["revisions"] == 2
    assert kept["headline"] == "ABCD Shares Move Higher"
    assert kept["observed_by_cutoff"] is True
    assert at_two["status"] == "no_material"

    # At 15:00 the edited revision is known.
    late = run(sources, cutoff=NOW)
    (latest,) = late["result"]["articles"]
    assert "merger_acquisition" in latest["categories"]
    assert latest["revised_after_cutoff"] is False  # edited at 13:10
    assert latest["first_observed_utc"] == "2026-11-09T12:30:00Z"  # the first revision's

    # Before any revision was seen (12:20), the latest stored one is shown (ADR 0024) and
    # flagged: its text may include the later edit.
    again = run(sources, cutoff=early)
    (shown,) = again["result"]["articles"]
    assert shown["observed_by_cutoff"] is False
    assert shown["revised_after_cutoff"] is True
    assert any("edited after the cutoff" in line for line in again["result"]["uncertainty"])


def test_text_edited_after_the_cutoff_is_flagged(tmp_path: Path, clock: Clock) -> None:
    edited = [
        article(
            301,
            "2026-11-09T12:00:00Z",
            "ABCD Announces Reverse Stock Split",
            updated="2026-11-09T14:45:00Z",
        )
    ]
    document = run(sources_for(tmp_path, Upstream(pages=[edited]), clock))
    assert document["result"]["articles"][0]["revised_after_cutoff"] is True
    assert any("edited after the cutoff" in line for line in document["result"]["uncertainty"])


def test_future_cutoff_is_refused(tmp_path: Path, clock: Clock) -> None:
    with pytest.raises(ValueError, match="future"):
        news_check(
            [sources_for(tmp_path, Upstream(), clock).alpaca_news()], ABCD, NOW + timedelta(hours=1)
        )


def test_naive_cutoff_is_refused(tmp_path: Path, clock: Clock) -> None:
    with pytest.raises(ValueError, match="time zone"):
        news_check(
            [sources_for(tmp_path, Upstream(), clock).alpaca_news()], ABCD, datetime(2026, 1, 1)
        )


# --- result ---------------------------------------------------------------------------


def test_material_result_lists_categories_facts_and_versions(tmp_path: Path, clock: Clock) -> None:
    document = run(sources_for(tmp_path, Upstream(), clock))
    assert document["status"] == "material"
    assert document["result"]["reason"] is None
    categories = {c["category"]: c for c in document["result"]["categories"]}
    assert set(categories) == {"offering", "listing_notice", NONE}
    assert categories["offering"]["latest_published_at_utc"] == "2026-11-09T12:15:00Z"
    by_id = {a["article_id"]: a for a in document["result"]["articles"]}
    assert by_id["104"]["categories"] == ["offering"]
    assert "registered direct offering" in by_id["104"]["matched_terms"]["offering"]
    assert by_id["103"]["categories"] == [NONE]
    facts = {(f["code"], f["subject"]): f for f in document["result"]["facts"]}
    assert facts[("news_count", None)]["value"] == "3"
    assert facts[("material_news_count", None)]["value"] == "2"
    assert facts[("catalyst_count", "offering")]["value"] == "1"
    assert facts[("latest_catalyst_age_hours", "alpaca-news/ABCD:104")]["value"] == "1.8"
    assert document["versions"] == {
        "news_rules": "news-rules-1",
        "news_rules_sha256": rules_checksum(),
        "parser.alpaca-news": "alpaca-news-1",
    }
    assert facts[("feed_article_count", "alpaca-news")]["value"] == "3"
    assert document["result"]["feeds"][0]["state"] == "ok"


def test_no_news_and_no_material(tmp_path: Path, clock: Clock) -> None:
    empty = run(sources_for(tmp_path / "a", Upstream(pages=[[]]), clock))
    assert empty["status"] == "no_data"
    assert empty["result"]["articles"] == [] and empty["result"]["categories"] == []
    plain = [article(401, "2026-11-09T11:00:00Z", "ABCD Is Among Today's Top Movers")]
    quiet = run(sources_for(tmp_path / "b", Upstream(pages=[plain]), clock))
    assert quiet["status"] == "no_material"
    assert quiet["result"]["categories"][0]["category"] == NONE


def test_missing_key_sends_nothing(tmp_path: Path, clock: Clock) -> None:
    upstream = Upstream()
    document = run(sources_for(tmp_path, upstream, clock, key=None))
    assert document["status"] == "error"
    assert document["result"]["reason"] == "news_credentials_missing"
    assert document["result"]["feeds"][0]["reason"] == "alpaca_credential_missing"
    assert upstream.requests == []


def test_refused_key_is_unavailable_and_says_why(tmp_path: Path, clock: Clock) -> None:
    upstream = Upstream()
    upstream.status = 403
    document = run(sources_for(tmp_path, upstream, clock))
    assert document["status"] == "error"
    assert document["result"]["reason"] == "source_unavailable"
    assert "HTTP 403" in document["result"]["missing"][0]
    assert "test-secret" not in str(document)


def test_failed_refresh_uses_the_cache_and_says_from_when(tmp_path: Path, clock: Clock) -> None:
    upstream = Upstream()
    sources = sources_for(tmp_path, upstream, clock)
    run(sources)
    upstream.offline = True
    clock.now = NOW + timedelta(hours=7)
    document = run(sources)
    assert document["status"] == "material"
    assert any("2026-11-09T15:00:00Z" in note for note in document["warnings"])


def test_korean_listing_is_unsupported(tmp_path: Path, clock: Clock) -> None:
    upstream = Upstream()
    outcome = news_check(
        [sources_for(tmp_path, upstream, clock).alpaca_news()], "kr:005930:xkrx:2026", CUTOFF
    )
    validate_document(envelope(outcome, CUTOFF, "kr:005930:xkrx:2026"))
    assert outcome["status"] == "unsupported"
    assert outcome["result"]["reason"] == "unsupported_instrument"
    assert upstream.requests == []


def test_envelope_rejects_an_unknown_status_or_check(tmp_path: Path, clock: Clock) -> None:
    document = run(sources_for(tmp_path, Upstream(), clock))
    assert validation_errors({**document, "status": "buy"})
    assert validation_errors({**document, "check": "headlines"})


# --- keyword rules --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headline", "summary", "expected"),
    [
        ("XYZ Prices $5M Underwritten Public Offering", "", ("offering",)),
        ("XYZ Files Shelf Registration", "", ("offering",)),
        ("XYZ Reports Q3 Results", "EPS beat estimates.", ("earnings",)),
        ("XYZ Announces Positive Topline Phase 2 Data", "", ("clinical_fda",)),
        ("XYZ Receives FDA Fast Track Designation", "", ("clinical_fda",)),
        ("XYZ Awarded $12M Contract by Utility", "", ("contract_order",)),
        ("XYZ Enters Definitive Agreement to Be Acquired", "", ("merger_acquisition",)),
        ("XYZ Announces 1-for-20 Reverse Stock Split", "", ("reverse_split",)),
        ("XYZ Receives Nasdaq Deficiency Notice", "", ("listing_notice",)),
        (
            "XYZ Regains Compliance",
            "regained compliance with the bid-price rule",
            ("listing_notice",),
        ),
        ("XYZ Shares Rise", "", (NONE,)),
        # Whole words only: "contractor" is not "contract", "Fdaxx" is not "FDA".
        ("Contractor Stocks Rally", "Fdaxx index", (NONE,)),
    ],
)
def test_rules(headline: str, summary: str, expected: tuple[str, ...]) -> None:
    assert classify(headline, summary).categories == expected


def test_an_article_can_carry_several_categories() -> None:
    tagged = classify(
        "XYZ to Acquire Rival; Announces Concurrent Public Offering",
        "Deal financed by the offering.",
    )
    assert tagged.categories == ("offering", "merger_acquisition")
    assert tagged.material


# --- route ----------------------------------------------------------------------------


def news_app(tmp_path: Path, clock: Clock, upstream: Upstream) -> tuple[TestClient, ScreeningStore]:
    screening = ScreeningStore(tmp_path / "screening.sqlite3", clock)
    sources = sources_for(tmp_path, upstream, clock)

    def listing_for(symbol: str) -> dict[str, Any] | None:
        return (
            instrument_listing(symbol, "XNAS", "test", "test-r1", "test")
            if symbol == "ABCD"
            else None
        )

    app = FastAPI()
    register_screening_news_routes(
        app,
        store=lambda: screening,
        listing_for=listing_for,
        news=lambda: sources,
        error_response=_error_response,
        clock=clock,
    )
    return TestClient(app), screening


def test_route_stores_a_screening_check(tmp_path: Path, clock: Clock) -> None:
    http, screening = news_app(tmp_path, clock, Upstream())
    response = http.post("/v1/screening/checks/news", json={"symbol": "abcd"})
    assert response.status_code == 200, response.text
    stored = response.json()
    validate_document(stored)
    assert stored["check"] == "news" and stored["status"] == "material"
    assert stored["instrument_id"] == ABCD
    assert stored["session_date"] == "2026-11-09"
    assert stored["cutoff_utc"] == "2026-11-09T15:00:00Z"  # now: the session is still open
    assert stored["after_the_fact"] is False
    assert screening.check(stored["check_id"]) == stored
    latest = screening.latest(date(2026, 11, 9))
    assert latest["ABCD"]["news"]["status"] == "material"
    screening.close()


def test_route_for_a_past_session_cuts_at_its_close(tmp_path: Path, clock: Clock) -> None:
    http, screening = news_app(tmp_path, clock, Upstream())
    response = http.post(
        "/v1/screening/checks/news",
        json={"symbol": "ABCD", "session_date": "2026-11-06", "lookback_hours": 168},
    )
    stored = response.json()
    assert stored["cutoff_utc"] == "2026-11-07T01:00:00Z"  # 20:00 New York, Friday
    assert stored["after_the_fact"] is True
    # 102 (Saturday 13:00 UTC) is after that cutoff; 101 is inside the week before it.
    assert [a["article_id"] for a in stored["result"]["articles"]] == ["101"]
    assert stored["status"] == "material"  # earnings
    screening.close()


def test_route_refuses_unknown_symbols_and_dates(tmp_path: Path, clock: Clock) -> None:
    http, screening = news_app(tmp_path, clock, Upstream())
    assert http.post("/v1/screening/checks/news", json={"symbol": "ZZZZ"}).status_code == 422
    weekend = {"symbol": "ABCD", "session_date": "2026-11-08"}
    assert http.post("/v1/screening/checks/news", json=weekend).status_code == 422
    assert screening.latest(date(2026, 11, 9)) == {}
    screening.close()
