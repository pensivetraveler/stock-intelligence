"""Filings and dark pool Agents (ADR 0024) against mocked SEC and FINRA HTTP: no network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.contracts.validator import validate_document
from services.agents.dark_pool import dark_pool_review
from services.agents.filings import UnsupportedTargetError, filings_review
from services.agents.store import AgentStore
from services.api.main import create_app
from services.public_sources.cache import PublicSourceCache
from services.public_sources.finra import ATS, NON_ATS, FinraCredential
from services.public_sources.http import RateLimiter, SourceConfigError, SourceError
from services.public_sources.sec import accepted_utc
from services.public_sources.sources import PublicSources
from tests.backtest.test_backtest_api import synthetic_bars

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)  # Monday 14:00 ET
AAPL = "us:aapl:xnas:2026"
CIK = 320193
USER_AGENT = "SIW test agent test@example.invalid"
CREDENTIAL = FinraCredential("test-client-id", "test-client-secret-not-real")


def columns(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    names = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form")
    out: dict[str, list[str]] = {name: [row.get(name, "") for row in rows] for name in names}
    out["items"] = [row.get("items", "") for row in rows]
    out["primaryDocument"] = ["doc.htm" for _ in rows]
    return out


# Acceptance times as EDGAR's JSON writes them: the true UTC time plus the Eastern offset
# (4 hours, as every date here is in daylight saving time). See `accepted_utc`.
FILINGS = [
    # After the cutoff: hindsight only.
    {
        "accessionNumber": "0000320193-26-000090",
        "filingDate": "2026-06-18",
        "acceptanceDateTime": "2026-06-19T00:05:00.000Z",
        "form": "8-K",
        "items": "5.02",
    },
    # The cutoff day, but accepted 30 minutes after it: not known.
    {
        "accessionNumber": "0000320193-26-000080",
        "filingDate": "2026-06-15",
        "acceptanceDateTime": "2026-06-15T22:30:00.000Z",
        "form": "4",
    },
    {
        "accessionNumber": "0000320193-26-000070",
        "filingDate": "2026-06-10",
        "acceptanceDateTime": "2026-06-11T00:31:00.000Z",
        "form": "8-K",
        "items": "2.02,9.01",
    },
    {
        "accessionNumber": "0000320193-26-000060",
        "filingDate": "2026-05-01",
        "reportDate": "2026-03-28",
        "acceptanceDateTime": "2026-05-02T00:01:00.000Z",
        "form": "10-Q",
    },
    {
        "accessionNumber": "0000320193-25-000050",
        "filingDate": "2025-10-31",
        "reportDate": "2025-09-27",
        "acceptanceDateTime": "2025-11-01T00:01:00.000Z",
        "form": "10-K",
    },
    {
        "accessionNumber": "0000320193-25-000040",
        "filingDate": "2025-05-02",
        "reportDate": "2025-03-29",
        "acceptanceDateTime": "2025-05-03T00:01:00.000Z",
        "form": "10-Q",
    },
]


def revenue(start: str, end: str, value: int, accn: str, filed: str) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": accn,
        "fp": "Q2",
        "form": "10-Q",
        "filed": filed,
    }


COMPANY_FACTS = {
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        revenue(
                            "2024-12-29", "2025-03-29", 95_000, "0000320193-25-000040", "2025-05-02"
                        ),
                        revenue(
                            "2025-12-28",
                            "2026-03-28",
                            104_500,
                            "0000320193-26-000060",
                            "2026-05-01",
                        ),
                        # Filed after the cutoff (a later quarter): must not be used.
                        revenue(
                            "2026-03-29",
                            "2026-06-27",
                            999_999,
                            "0000320193-26-000099",
                            "2026-07-31",
                        ),
                        # A six-month total: not a quarter.
                        revenue(
                            "2025-09-28",
                            "2026-03-28",
                            200_000,
                            "0000320193-26-000060",
                            "2026-05-01",
                        ),
                    ]
                }
            }
        }
    }
}


def finra_row(kind: str, week: date, shares: int, trades: int, published: date) -> dict:
    return {
        "issueSymbolIdentifier": "AAPL",
        "issueName": "Apple Inc. Common Stock",
        "tierIdentifier": "T1",
        "summaryTypeCode": kind,
        "weekStartDate": week.isoformat(),
        "totalWeeklyShareQuantity": shares,
        "totalWeeklyTradeCount": trades,
        "initialPublishedDate": published.isoformat(),
        "lastUpdateDate": published.isoformat(),
        "lastReportedDate": published.isoformat(),
    }


def finra_rows() -> list[dict[str, Any]]:
    rows = []
    first = date(2026, 1, 5)  # a Monday
    for i in range(24):
        week = first + timedelta(weeks=i)
        published = week + timedelta(weeks=2, days=7)  # the Monday two weeks after the week
        shares = 2_000_000 if week == date(2026, 5, 18) else 1_000_000
        rows.append(finra_row(ATS, week, shares, 4_000, published))
        rows.append(finra_row(NON_ATS, week, 3_000_000, 9_000, published))
    return rows


class Upstream:
    """Mocked SEC and FINRA endpoints; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("offline", request=request)
        url = str(request.url)
        if url == "https://www.sec.gov/files/company_tickers.json":
            return httpx.Response(
                200, json={"0": {"cik_str": CIK, "ticker": "AAPL", "title": "Apple Inc."}}
            )
        if url == f"https://data.sec.gov/submissions/CIK{CIK:010d}.json":
            return httpx.Response(
                200, json={"cik": str(CIK), "filings": {"recent": columns(FILINGS), "files": []}}
            )
        if url == f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK:010d}.json":
            return httpx.Response(200, json=COMPANY_FACTS)
        if url.startswith("https://ews.fip.finra.org/"):
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": "43170"})
        if url == "https://api.finra.org/data/group/otcMarket/name/weeklySummary":
            assert request.headers["Authorization"] == "Bearer token-1"
            body = json.loads(request.content)
            kind = next(
                f["fieldValue"]
                for f in body["compareFilters"]
                if f["fieldName"] == "summaryTypeCode"
            )
            window = body["dateRangeFilters"][0]
            return httpx.Response(
                200,
                json=[
                    row
                    for row in finra_rows()
                    if row["summaryTypeCode"] == kind
                    and window["startDate"] <= row["weekStartDate"] <= window["endDate"]
                ],
            )
        return httpx.Response(404)

    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def sources_for(
    tmp_path: Path,
    upstream: Upstream,
    clock: Clock,
    user_agent: str | None = USER_AGENT,
    credential: FinraCredential | None = CREDENTIAL,
) -> PublicSources:
    return PublicSources(
        tmp_path / "public_sources.sqlite3",
        clock,
        user_agent=lambda: user_agent,
        finra_credential=lambda: credential,
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
        sec_limiter=RateLimiter(0),
        finra_limiter=RateLimiter(0),
    )


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def facts_of(evidence: Any, code: str) -> list[Any]:
    return [fact for fact in evidence.facts if fact.code == code]


# --- filings ---------------------------------------------------------------------------


def test_filings_known_at_the_cutoff_only(tmp_path: Path, upstream: Upstream, clock: Clock) -> None:
    sources = sources_for(tmp_path, upstream, clock)
    evidence = filings_review(sources.sec(), AAPL, CUTOFF, 30, 0, NOW)
    listed = facts_of(evidence, "filing")
    # The 8-K of 06-10 only: the Form 4 was accepted 30 minutes after the cutoff.
    assert [fact.subject for fact in listed] == ["8-K"]
    assert "2.02 Results of operations" in listed[0].text
    assert listed[0].value == "2.02, 9.01"
    assert not listed[0].hindsight
    assert facts_of(evidence, "filings_in_window")[0].value == "1"
    latest_10q = facts_of(evidence, "latest_10q")[0]
    assert latest_10q.value == "45" and "2026-03-28" in latest_10q.text
    assert facts_of(evidence, "latest_10k")[0].unit == "days"
    # Revenue: the quarter to 2026-03-28, never the value filed after the cutoff.
    assert facts_of(evidence, "revenue_q")[0].value == "104500"
    assert facts_of(evidence, "revenue_q_yoy_pct")[0].value == "10.00"
    assert "eps_diluted_q" in evidence.missing
    assert "0000320193-26-000080" not in evidence.source_refs
    assert "0000320193-26-000070" in evidence.source_refs
    assert any("reconstructed" in note for note in evidence.uncertainty)
    assert evidence.agent_id == "filings_review"
    # A declared User-Agent on every SEC request.
    assert all(request.headers["User-Agent"] == USER_AGENT for request in upstream.requests)
    assert set(upstream.hosts()) == {"www.sec.gov", "data.sec.gov"}


def test_later_filings_only_on_request_and_flagged(
    tmp_path: Path, upstream: Upstream, clock: Clock
) -> None:
    sources = sources_for(tmp_path, upstream, clock)
    evidence = filings_review(sources.sec(), AAPL, CUTOFF, 30, 7, NOW)
    later = [fact for fact in facts_of(evidence, "filing") if fact.hindsight]
    assert sorted(fact.subject for fact in later) == ["4", "8-K"]
    assert any("5.02" in fact.text for fact in later)
    # Hindsight never changes what was known: the XBRL value stays the same.
    assert facts_of(evidence, "revenue_q")[0].value == "104500"


def test_sec_needs_a_user_agent_and_sends_nothing_without_it(
    tmp_path: Path, upstream: Upstream, clock: Clock
) -> None:
    sources = sources_for(tmp_path, upstream, clock, user_agent="  ")
    with pytest.raises(SourceConfigError):
        filings_review(sources.sec(), AAPL, CUTOFF, 30, 0, NOW)
    assert upstream.requests == []


def test_cache_is_reused_and_kept_when_the_source_fails(
    tmp_path: Path, upstream: Upstream, clock: Clock
) -> None:
    sources = sources_for(tmp_path, upstream, clock)
    filings_review(sources.sec(), AAPL, CUTOFF, 30, 0, NOW)
    sent = len(upstream.requests)
    assert sent == 3  # ticker map, submissions, company facts
    filings_review(sources.sec(), AAPL, CUTOFF, 30, 0, NOW)
    assert len(upstream.requests) == sent  # fresh cache: nothing sent
    clock.now = NOW + timedelta(hours=7)
    upstream.fail = True
    evidence = filings_review(sources.sec(), AAPL, CUTOFF, 30, 0, clock.now)
    assert facts_of(evidence, "filing")
    assert any("was used" in note for note in evidence.uncertainty)
    # With no cache at all, a failure is an error, not an empty analysis.
    empty = sources_for(tmp_path / "other", upstream, clock)
    with pytest.raises(SourceError):
        filings_review(empty.sec(), AAPL, CUTOFF, 30, 0, NOW)


def test_korean_listings_are_refused(tmp_path: Path, upstream: Upstream, clock: Clock) -> None:
    sources = sources_for(tmp_path, upstream, clock)
    with pytest.raises(UnsupportedTargetError):
        filings_review(sources.sec(), "kr:005930:xkrx:2026", CUTOFF, 30, 0, NOW)
    assert upstream.requests == []


def test_revisions_are_kept_and_chosen_by_observation_time(tmp_path: Path) -> None:
    clock = Clock()
    cache = PublicSourceCache(tmp_path / "c.sqlite3", clock)
    cache.put("s", "r1", {"v": 1}, "2026-01-01T00:00:00Z", "p-1")
    first_seen = clock.now
    clock.now = NOW + timedelta(days=3)
    cache.put("s", "r1", {"v": 2}, "2026-01-01T00:00:00Z", "p-1")
    cache.put("s", "r1", {"v": 2}, "2026-01-01T00:00:00Z", "p-1")  # unchanged: no new row
    latest = cache.records("s", "r")[0]
    assert latest.document == {"v": 2} and latest.revisions == 2
    assert latest.first_observed_utc == "2026-10-21T12:00:00Z"
    at_first = cache.records("s", "r", as_of=first_seen + timedelta(days=1))[0]
    assert at_first.document == {"v": 1}
    document = latest.contract()
    validate_document(document)
    assert document["published_at_utc"] == "2026-01-01T00:00:00Z"
    assert latest.observed_after_publication()


def test_edgar_acceptance_times_are_corrected_to_true_utc() -> None:
    # Pairs observed on 2026-10-21: Submissions JSON value -> the filing header's Eastern time.
    for shown, eastern in (
        ("2026-01-30T16:01:32.000Z", "2026-01-30T06:01:32-05:00"),  # 10-Q, winter
        ("2025-12-06T02:31:42.000Z", "2025-12-05T16:31:42-05:00"),  # 8-K, winter
        ("2026-07-31T14:01:02.000Z", "2026-07-31T06:01:02-04:00"),  # 10-Q, summer
        ("2026-10-20T02:32:55.000Z", "2026-10-19T18:32:55-04:00"),  # Form 4, summer
    ):
        assert accepted_utc(shown) == datetime.fromisoformat(eastern)


def test_rate_limiter_spaces_requests() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(0.2, monotonic=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == pytest.approx([0.2, 0.2])


# --- dark pool -------------------------------------------------------------------------


def test_dark_pool_uses_only_weeks_published_before_the_cutoff(
    tmp_path: Path, upstream: Upstream, clock: Clock
) -> None:
    sources = sources_for(tmp_path, upstream, clock)
    evidence = dark_pool_review(sources.finra(), synthetic_bars, AAPL, CUTOFF, 12)
    facts = {fact.code: fact for fact in evidence.facts}
    # Weeks are published three Mondays after they start; 06-15 (the cutoff day) is not
    # assumed known, so the latest known week starts 05-18 (published 06-08).
    assert facts["ats_week"].value == "2026-05-18"
    assert facts["ats_shares"].value == "2000000"
    assert facts["ats_share_of_off_exchange_pct"].value == "40.00"
    assert facts["ats_shares_vs_mean"].value == "2.00"
    assert facts["ats_history_weeks"].value == "12"
    assert facts["ats_avg_trade_size"].value == "500.0"
    # Synthetic bars are not a consolidated feed: no share of total volume.
    assert "ats_share_of_total_volume_pct" in evidence.missing
    text = " ".join(evidence.uncertainty)
    assert "not live dark-pool prints" in text and "not yet published" in text
    assert evidence.agent_id == "dark_pool_review"
    assert set(upstream.hosts()) == {"ews.fip.finra.org", "api.finra.org"}


def test_share_of_total_volume_needs_a_consolidated_feed(
    tmp_path: Path, upstream: Upstream, clock: Clock
) -> None:
    sources = sources_for(tmp_path, upstream, clock)
    evidence = dark_pool_review(
        sources.finra(),
        synthetic_bars,
        AAPL,
        CUTOFF,
        12,
        consolidated=frozenset({"synthetic-daily"}),
    )
    share = next(f for f in evidence.facts if f.code == "ats_share_of_total_volume_pct")
    # 2,000,000 ATS shares over five sessions of 1,000 synthetic shares.
    assert share.value == "40000.00"


def test_dark_pool_needs_a_finra_credential(
    tmp_path: Path, upstream: Upstream, clock: Clock
) -> None:
    sources = sources_for(tmp_path, upstream, clock, credential=None)
    with pytest.raises(SourceConfigError):
        dark_pool_review(sources.finra(), synthetic_bars, AAPL, CUTOFF, 12)
    assert upstream.requests == []


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, upstream: Upstream, clock: Clock) -> Iterator[TestClient]:
    app = create_app(
        agent_store=AgentStore(tmp_path / "agents.sqlite3", lambda: NOW),
        public_sources=sources_for(tmp_path, upstream, clock),
        clock=lambda: NOW,
    )
    import services.api.main as main

    original = main.MarketBarsService

    class Synthetic:
        def __init__(self, _provider: object) -> None:
            pass

        def get_bars(self, query: Any) -> Any:
            return synthetic_bars(query)

    main.MarketBarsService = Synthetic  # type: ignore[assignment,misc]
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        main.MarketBarsService = original  # type: ignore[misc]


def test_api_stores_filings_and_dark_pool_analyses(client: TestClient) -> None:
    filings = client.post(
        "/v1/agents/review/filings",
        json={"instrument_id": AAPL, "cutoff_utc": "2026-06-15T18:00:00Z", "after_days": 7},
    )
    assert filings.status_code == 201, filings.text
    document = filings.json()
    validate_document(document)
    assert document["agent_id"] == "filings_review"
    assert document["target"] == {
        "type": "filings",
        "instrument_id": AAPL,
        "window_days": 30,
        "after_days": 7,
    }
    assert document["indicator_versions"] == {"sec-edgar-parser": "sec-1"}
    dark = client.post(
        "/v1/agents/review/dark-pool",
        json={"instrument_id": AAPL, "cutoff_utc": "2026-06-15T18:00:00Z", "weeks": 8},
    )
    assert dark.status_code == 201, dark.text
    validate_document(dark.json())
    assert dark.json()["target"]["type"] == "dark_pool"
    listed = client.get("/v1/agents/analyses").json()
    assert {a["agent_id"] for a in listed["analyses"]} == {"filings_review", "dark_pool_review"}


def test_api_errors(client: TestClient) -> None:
    korean = client.post(
        "/v1/agents/review/filings",
        json={"instrument_id": "kr:005930:xkrx:2026", "cutoff_utc": "2026-06-15T18:00:00Z"},
    )
    assert korean.status_code == 422
    assert korean.json()["error"]["code"] == "agent_target_unsupported"
    naive = client.post(
        "/v1/agents/review/dark-pool",
        json={"instrument_id": AAPL, "cutoff_utc": "2026-06-15T18:00:00"},
    )
    assert naive.status_code == 422


def test_api_reports_missing_configuration(tmp_path: Path, upstream: Upstream) -> None:
    app = create_app(
        agent_store=AgentStore(tmp_path / "agents.sqlite3", lambda: NOW),
        public_sources=sources_for(tmp_path, upstream, Clock(), user_agent=None, credential=None),
        clock=lambda: NOW,
    )
    with TestClient(app) as test_client:
        for path, code in (
            ("/v1/agents/review/filings", "sec_user_agent_missing"),
            ("/v1/agents/review/dark-pool", "finra_credential_missing"),
        ):
            response = test_client.post(
                path, json={"instrument_id": AAPL, "cutoff_utc": "2026-06-15T18:00:00Z"}
            )
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == code
    assert upstream.requests == []
