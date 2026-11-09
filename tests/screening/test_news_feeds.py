"""Finnhub and MarketNewsAPI feeds, and the news check over several feeds (ADR 0052 §3).

No network: every request goes to `httpx.MockTransport`. Articles are invented.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from packages.contracts.validator import validate_document
from services.public_sources.alpaca_news import AlpacaNewsCredential
from services.public_sources.cache import utc_text
from services.public_sources.finnhub_news import FINNHUB_NEWS, FinnhubCredential
from services.public_sources.http import RateLimiter
from services.public_sources.marketnews import MARKETNEWS, MarketNewsCredential
from services.public_sources.sources import PublicSources
from services.screening.news import news_check

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
ABCD = "us:abcd:xnas:2026"


def unix(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


FINNHUB = [
    {
        "category": "company",
        "datetime": unix("2026-11-09T12:16:00Z"),
        "headline": "ABCD Announces Pricing of $8 Million Registered Direct Offering",
        "id": 9001,
        "image": "https://example.invalid/t.png",
        "related": "ABCD",
        "source": "Example Wire",
        "summary": "",
        "url": "https://example.invalid/f/9001",
    },
    {
        "category": "company",
        "datetime": unix("2026-11-09T14:20:00Z"),  # after the cutoff
        "headline": "ABCD Shares Halted",
        "id": 9002,
        "image": "",
        "related": "ABCD",
        "source": "Example Wire",
        "summary": "",
        "url": "https://example.invalid/f/9002",
    },
]

MARKETNEWS_PAGE = {
    "data": [
        {
            "id": "art_1",
            "title": "ABCD Announces Pricing of $8 Million Registered Direct Offering",
            "url": "https://example.invalid/pr/1",
            "source": {"name": "Example Newswire", "domain": "example.invalid"},
            "publisher": "Example Newswire",
            "published_at": "2026-11-09T12:15:00Z",
            "category": "stocks",
            "type": "press_release",
            "tickers": ["ABCD"],
            "listings": [{"symbol": "ABCD", "exchange": "NASDAQ"}],
            "snippet": "ABCD entered into a securities purchase agreement with an investor.",
            "summary": "A machine-written summary that is not used for tagging.",
            "body": "Invented press release text for tests.",
            "entities": [],
            "language": "en",
        },
        {
            "id": "art_2",
            "title": "ABCD Mining Drills New Hole",
            "url": "https://example.invalid/pr/2",
            "source": {"name": "Example Newswire", "domain": "example.invalid"},
            "published_at": "2026-11-09T11:00:00Z",
            "category": "stocks",
            "type": "press_release",
            "tickers": ["ABCD"],
            "listings": [{"symbol": "ABCD", "exchange": "TSXV"}],
            "snippet": "",
            "summary": "",
            "body": "Another issuer with the same ticker in Canada.",
        },
        {
            "id": "art_3",
            "title": "Small Caps Move Higher",
            "url": "https://example.invalid/n/3",
            "source": {"name": "Example Daily", "domain": "daily.example.invalid"},
            "published_at": "2026-11-09T10:00:00Z",
            "category": "stocks",
            "type": "news",
            "tickers": ["ABCD", "WXYZ"],
            "listings": [],
            "snippet": "Several names rose before the open.",
            "summary": "Generated.",
            "body": None,
        },
    ],
    "meta": {"count": 3, "next_cursor": None, "plan": "free", "delayed_minutes": 15},
}


class Upstream:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.finnhub_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "finnhub.io":
            if self.finnhub_status != 200:
                return httpx.Response(self.finnhub_status)
            return httpx.Response(200, json=FINNHUB)
        if host == "api.marketnewsapi.com":
            return httpx.Response(200, json=MARKETNEWS_PAGE)
        if host == "data.alpaca.markets":
            return httpx.Response(
                200,
                json={
                    "news": [
                        {
                            "id": 77,
                            "headline": "ABCD announces pricing of $8 million registered "
                            "direct offering",
                            "summary": "",
                            "author": "",
                            "created_at": "2026-11-09T12:17:00Z",
                            "updated_at": "2026-11-09T12:17:00Z",
                            "symbols": ["ABCD"],
                            "source": "benzinga",
                            "url": "https://example.invalid/a/77",
                            "content": "",
                            "images": [],
                        }
                    ],
                    "next_page_token": None,
                },
            )
        return httpx.Response(404)

    def of(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


def sources_for(
    tmp_path: Path,
    upstream: Upstream,
    *,
    alpaca: bool = True,
    finnhub: bool = True,
    marketnews: bool = True,
) -> PublicSources:
    return PublicSources(
        tmp_path / "public_sources.sqlite3",
        lambda: NOW,
        user_agent=lambda: None,
        finra_credential=lambda: None,
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
        alpaca_credential=lambda: AlpacaNewsCredential("k", "s-not-real") if alpaca else None,
        finnhub_credential=lambda: FinnhubCredential("fh-test-not-real") if finnhub else None,
        marketnews_credential=lambda: (
            MarketNewsCredential("mna-test-not-real") if marketnews else None
        ),
        news_limiter=RateLimiter(0),
        finnhub_limiter=RateLimiter(0),
        marketnews_limiter=RateLimiter(0),
    )


def envelope(outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "screening_check",
        "schema_version": "0.1",
        "check_id": 0,
        "check": "news",
        "session_date": "2026-11-09",
        "symbol": "ABCD",
        "instrument_id": ABCD,
        "cutoff_utc": utc_text(CUTOFF),
        "created_at_utc": utc_text(NOW),
        "after_the_fact": False,
        **outcome,
    }


# --- Finnhub ----------------------------------------------------------------------------


def test_finnhub_request_uses_header_key_and_whole_dates(tmp_path: Path) -> None:
    upstream = Upstream()
    sources = sources_for(tmp_path, upstream, alpaca=False, marketnews=False)
    found = sources.finnhub_news().articles(
        "ABCD", datetime(2026, 11, 6, 14, tzinfo=timezone.utc), CUTOFF
    )
    (request,) = upstream.of("finnhub.io")
    assert request.url.path == "/api/v1/company-news"
    assert request.url.params["symbol"] == "ABCD"
    assert request.url.params["from"] == "2026-11-05"  # a day before the window
    assert request.url.params["to"] == "2026-11-10"  # a day after now
    assert request.headers["X-Finnhub-Token"] == "fh-test-not-real"
    assert "token" not in request.url.params
    assert [a.article_id for a in found] == ["9001", "9002"]  # the caller applies the cutoff
    first = found[0]
    assert first.published_at == datetime(2026, 11, 9, 12, 16, tzinfo=timezone.utc)
    assert first.updated_at is None
    assert first.publisher == "Example Wire"
    assert "image" not in first.record.document


# --- MarketNewsAPI ----------------------------------------------------------------------


def test_marketnews_request_and_stored_fields(tmp_path: Path) -> None:
    upstream = Upstream()
    sources = sources_for(tmp_path, upstream, alpaca=False, finnhub=False)
    feed = sources.marketnews()
    found = feed.articles("ABCD", datetime(2026, 11, 6, 14, tzinfo=timezone.utc), CUTOFF)
    (request,) = upstream.of("api.marketnewsapi.com")
    params = request.url.params
    assert request.url.path == "/v1/news"
    assert params["tickers"] == "ABCD"
    assert params["type"] == "news,press_release"
    assert params["include_legal"] == "false"
    assert params["from"] == "2026-11-06T14:00:00Z"
    assert params["limit"] == "100"
    assert request.headers["X-API-Key"] == "mna-test-not-real"
    assert "api_key" not in params
    by_id = {a.article_id: a for a in found}
    assert set(by_id) == {"art_1", "art_3"}  # art_2 lists ABCD only on TSXV
    release = by_id["art_1"]
    assert release.article_type == "press_release" and release.has_body
    assert release.summary.startswith("ABCD entered into")  # the snippet, not the generated one
    assert release.record.document["generated_summary"].startswith("A machine-written")
    assert by_id["art_3"].has_body is False
    assert any("15-minute delay" in note for note in feed.notes)
    assert any("non-US exchanges" in note for note in feed.notes)


def test_a_source_can_be_deleted(tmp_path: Path) -> None:
    sources = sources_for(tmp_path, Upstream(), alpaca=False, marketnews=False)
    sources.finnhub_news().articles("ABCD", datetime(2026, 11, 6, 14, tzinfo=timezone.utc), CUTOFF)
    assert sources.cache.delete_source(FINNHUB_NEWS) == 2
    assert sources.cache.records(FINNHUB_NEWS, "ABCD:") == []
    assert sources.cache.last_fetch(FINNHUB_NEWS, "ABCD:2026-11-06T14:00:00Z") is None


# --- several feeds ----------------------------------------------------------------------


def test_feeds_are_listed_apart_and_matching_headlines_pointed_out(tmp_path: Path) -> None:
    sources = sources_for(tmp_path, Upstream())
    outcome = news_check(sources.news_feeds(), ABCD, CUTOFF)
    validate_document(envelope(outcome))
    result = outcome["result"]
    assert outcome["status"] == "material"
    assert [f["source_id"] for f in result["feeds"]] == ["alpaca-news", MARKETNEWS, FINNHUB_NEWS]
    assert {f["source_id"]: f["articles"] for f in result["feeds"]} == {
        "alpaca-news": 1,
        MARKETNEWS: 2,
        FINNHUB_NEWS: 1,  # 9002 is after the cutoff
    }
    refs = [a["record_ref"] for a in result["articles"]]
    assert refs == [
        "alpaca-news/ABCD:77",
        "finnhub-news/ABCD:9001",
        "marketnewsapi-news/ABCD:art_1",
        "marketnewsapi-news/ABCD:art_3",
    ]  # newest first, never merged
    first = result["articles"][0]
    assert sorted(first["same_headline_as"]) == [
        "finnhub-news/ABCD:9001",
        "marketnewsapi-news/ABCD:art_1",
    ]
    assert result["articles"][3]["same_headline_as"] == []
    assert outcome["versions"]["parser.finnhub-news"] == "finnhub-news-1"
    assert outcome["versions"]["parser.marketnewsapi-news"] == "marketnewsapi-1"
    assert len(outcome["sources"]["records"]) == 4
    raw = str(outcome)
    for secret in ("fh-test-not-real", "mna-test-not-real", "s-not-real"):
        assert secret not in raw


def test_one_failing_feed_does_not_fail_the_check(tmp_path: Path) -> None:
    upstream = Upstream()
    upstream.finnhub_status = 403
    outcome = news_check(sources_for(tmp_path, upstream).news_feeds(), ABCD, CUTOFF)
    validate_document(envelope(outcome))
    assert outcome["status"] == "material"
    finnhub = outcome["result"]["feeds"][2]
    assert finnhub["state"] == "error" and finnhub["reason"] == "source_unavailable"
    assert any("Finnhub could not be read" in w for w in outcome["warnings"])


def test_feeds_without_keys_are_skipped_without_requests(tmp_path: Path) -> None:
    upstream = Upstream()
    sources = sources_for(tmp_path, upstream, alpaca=False, finnhub=False)
    outcome = news_check(sources.news_feeds(), ABCD, CUTOFF)
    states = {f["source_id"]: (f["state"], f["reason"]) for f in outcome["result"]["feeds"]}
    assert states["alpaca-news"] == ("no_key", "alpaca_credential_missing")
    assert states[FINNHUB_NEWS] == ("no_key", "finnhub_credential_missing")
    assert states[MARKETNEWS] == ("ok", None)
    assert {r.url.host for r in upstream.requests} == {"api.marketnewsapi.com"}


def test_no_keys_at_all_is_an_error(tmp_path: Path) -> None:
    upstream = Upstream()
    sources = sources_for(tmp_path, upstream, alpaca=False, finnhub=False, marketnews=False)
    outcome = news_check(sources.news_feeds(), ABCD, CUTOFF)
    validate_document(envelope(outcome))
    assert outcome["status"] == "error"
    assert outcome["result"]["reason"] == "news_credentials_missing"
    assert upstream.requests == []
