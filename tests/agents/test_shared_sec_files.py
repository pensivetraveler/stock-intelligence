"""SEC responses and the request limit shared with the dilution analyzer (ADR 0046)."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from services.public_sources.cache import PublicSourceCache
from services.public_sources.http import RateLimiter, SourceError
from services.public_sources.sec import TICKERS_URL, SecEdgar
from services.public_sources.shared_sec import INTERVAL_SECONDS, SharedSecFiles

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1/2/index.json"


def edgar(tmp_path: Path, handler: httpx.MockTransport, shared: SharedSecFiles | None) -> SecEdgar:
    return SecEdgar(
        PublicSourceCache(tmp_path / "public.sqlite3", lambda: NOW),
        "SIW test test@example.com",
        httpx.Client(transport=handler),
        RateLimiter(0.0),
        shared,
    )


def test_files_use_the_analyzers_names(tmp_path: Path) -> None:
    digest = hashlib.sha256(FACTS.encode()).hexdigest()[:16]
    assert SharedSecFiles(tmp_path).path(FACTS) == (
        tmp_path / "sec" / "data.sec.gov" / f"{digest}-CIK0000320193.json"
    )


def test_only_sec_addresses_have_a_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SharedSecFiles(tmp_path).path("https://example.com/a.json")


def test_stored_bytes_are_kept_as_they_came(tmp_path: Path) -> None:
    files = SharedSecFiles(tmp_path)
    files.store(FACTS, b'{"a":  1}')
    assert files.path(FACTS).read_bytes() == b'{"a":  1}'
    assert [path.name for path in files.path(FACTS).parent.iterdir()] == [files.path(FACTS).name]


def test_an_immutable_file_is_never_replaced(tmp_path: Path) -> None:
    files = SharedSecFiles(tmp_path)
    files.store(ARCHIVE, b"first")
    files.store(ARCHIVE, b"second")
    assert files.path(ARCHIVE).read_bytes() == b"first"
    files.store(FACTS, b"first")
    files.store(FACTS, b"second")
    assert files.path(FACTS).read_bytes() == b"second"


def test_an_old_file_is_not_fresh(tmp_path: Path) -> None:
    files = SharedSecFiles(tmp_path, wall_clock=lambda: 10_000.0)
    files.store(FACTS, b"body")
    os.utime(files.path(FACTS), (9_000.0, 9_000.0))
    assert files.fresh(FACTS, timedelta(minutes=5)) is None
    os.utime(files.path(FACTS), (9_900.0, 9_900.0))
    assert files.fresh(FACTS, timedelta(minutes=5)) == b"body"


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_requests_are_spaced_through_the_lock_file(tmp_path: Path) -> None:
    clock = _Clock()
    files = SharedSecFiles(tmp_path, clock.monotonic, clock.sleep)
    with files.request_turn():
        assert float((tmp_path / "sec.lock").read_text()) > clock.now  # marked while sending
    assert float((tmp_path / "sec.lock").read_text()) == clock.now
    with files.request_turn():
        pass
    assert clock.slept == [pytest.approx(INTERVAL_SECONDS)]


def test_a_holder_that_died_while_sending_leaves_one_interval(tmp_path: Path) -> None:
    clock = _Clock()
    (tmp_path / "sec.lock").write_text(repr(clock.now + 1e6))
    with SharedSecFiles(tmp_path, clock.monotonic, clock.sleep).request_turn():
        pass
    assert clock.slept == [INTERVAL_SECONDS]


def test_download_is_shared_and_a_recent_one_answers_without_a_request(tmp_path: Path) -> None:
    calls: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"body")

    files = SharedSecFiles(tmp_path / "shared")
    client = edgar(tmp_path, httpx.MockTransport(answer), files)
    assert client._get(TICKERS_URL) == b"body"
    assert client._get(TICKERS_URL) == b"body"
    assert calls == [TICKERS_URL]
    assert client.requests == 1
    assert files.path(TICKERS_URL).read_bytes() == b"body"


def test_an_error_body_is_never_shared(tmp_path: Path) -> None:
    files = SharedSecFiles(tmp_path / "shared")
    transport = httpx.MockTransport(lambda _request: httpx.Response(404, content=b"not found"))
    with pytest.raises(SourceError):
        edgar(tmp_path, transport, files)._get(TICKERS_URL)
    assert not files.path(TICKERS_URL).exists()


def test_without_the_shared_folder_nothing_changes(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"body"))
    assert edgar(tmp_path, transport, None)._get(TICKERS_URL) == b"body"
    assert not (tmp_path / "sec").exists()
