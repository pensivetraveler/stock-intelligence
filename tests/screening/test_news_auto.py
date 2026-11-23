"""Stage 2 news checks run by themselves (ADR 0057, 2026-11-23 decision 184).

No network: the news-analysis service is the `httpx.MockTransport` fake of the check B
tests, the condition record is written to a temporary folder.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from packages.contracts.validator import validate_document
from services.api.screening_news_auto import NewsAutoRunner, by_runner
from services.api.screening_news_routes import NewsChecks
from services.screening.material import stage2_by_symbol
from services.screening.store import ScreeningStore
from tests.screening.test_news_service import FakeService, news_check
from tests.screening.test_stage4 import CONDITION, NOW, WINDOW, listing_for, news, record

assert WINDOW is not None


class Setup:
    def __init__(self, tmp_path: Path, fake: FakeService) -> None:
        record(tmp_path)
        self.now = NOW
        self.fake = fake
        self.screening = ScreeningStore(tmp_path / "screening.sqlite3", lambda: self.now)
        self.runner = NewsAutoRunner(
            store=lambda: self.screening,
            data_dir=lambda: tmp_path,
            listing_for=listing_for,
            news=NewsChecks(
                clock=lambda: self.now,
                news_service=fake.service,
                wait_seconds=0,
                sleep=lambda _seconds: None,
            ),
            clock=lambda: self.now,
            condition_name=CONDITION.name,
        )

    def checks(self) -> list[dict[str, Any]]:
        return self.screening.documents(WINDOW.session_date, "news")  # type: ignore[union-attr]

    def asked(self) -> list[str]:
        return [json.loads(r.content)["ticker"] for r in self.fake.requests if r.method == "POST"]


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[Setup]:
    made = Setup(tmp_path, FakeService())
    yield made
    made.screening.close()


def test_each_candidate_is_checked_once_in_the_order_it_appeared(setup: Setup) -> None:
    # PASS and EXCL were in the list first, RATE entered later, ZZZZ is unknown to the app.
    reports = [setup.runner.tick() for _ in range(5)]
    assert [r["asked"] for r in reports if r] == ["EXCL", "PASS", "RATE", None, None]
    assert setup.asked() == ["EXCL", "PASS", "RATE"]
    assert "ZZZZ" in reports[3]["unknown_symbol"]  # type: ignore[index]
    for stored in setup.checks():
        validate_document(stored)
        assert by_runner(stored)
        assert stored["result"]["news_auto"]["requested_by"] == "news_auto"
    # Stage 2 reads them like the panel's checks.
    assert set(stage2_by_symbol(setup.screening, WINDOW.session_date)) == {"EXCL", "PASS", "RATE"}


def test_a_check_from_the_panel_is_not_run_again(setup: Setup) -> None:
    setup.screening.add_check(news("EXCL", "present", "yes", None))
    setup.screening.add_check(news("PASS", "absent", None, None))
    report = setup.runner.tick()
    assert report is not None and report["asked"] == "RATE"
    assert setup.asked() == ["RATE"]


def test_a_stopped_service_waits_without_piling_up(tmp_path: Path) -> None:
    fake = FakeService()
    fake.down = True
    made = Setup(tmp_path, fake)
    first = made.runner.tick()
    assert first is not None and first["asked"] == "EXCL"
    second = made.runner.tick()
    assert second is not None and second["asked"] is None
    assert second["blocked"] == "a news check is still waiting"
    assert [c["symbol"] for c in made.checks()] == ["EXCL"]
    fake.down = False
    third = made.runner.tick()
    assert third is not None and third["refreshed"] == ["EXCL"] and third["asked"] == "PASS"
    refreshed = [c for c in made.checks() if c["symbol"] == "EXCL"][-1]
    assert refreshed["status"] == "material" and by_runner(refreshed)
    made.screening.close()


def test_an_error_is_not_retried(setup: Setup) -> None:
    setup.fake.states = ["failed"]
    setup.runner.tick()
    setup.fake.states = ["done"]
    reports = [setup.runner.tick() for _ in range(3)]
    assert setup.asked() == ["EXCL", "PASS", "RATE"]
    assert [r["asked"] for r in reports if r] == ["PASS", "RATE", None]


def test_the_monthly_llm_cap_is_recorded(tmp_path: Path) -> None:
    check = news_check()
    check["nir"] = {"state": "stopped", "reason": "monthly_cap_reached", "rules_only": True}
    made = Setup(tmp_path, FakeService(check=check))
    report = made.runner.tick()
    assert report is not None and report["nir_cap_reached_at_utc"] is not None
    assert made.runner.nir_cap_reached[WINDOW.session_date.isoformat()].startswith(
        NOW.isoformat()[:16]
    )
    made.screening.close()


def test_nothing_runs_outside_the_session(setup: Setup) -> None:
    setup.now = WINDOW.post_close_utc + timedelta(minutes=1)
    assert setup.runner.tick() is None
    assert setup.asked() == []
