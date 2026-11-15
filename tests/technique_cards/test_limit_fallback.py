"""A model at its limit goes to another company's model (#LAG-SIW-4, user 2026-11-15)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from services.technique_cards import extract
from services.technique_cards.extract import AttemptFailed, extract_card, http_failure
from services.technique_cards.store import CardStore
from tests.technique_cards.test_card_pipeline import KEYS, LINES, _card

FABLE = "anthropic/claude-fable-5.2"
ARGON = "google/gemini-4-argon"
LUNA = "openai/gpt-6-luna"
OK = {"input_tokens": 1, "output_tokens": 1, "cost_usd": "0"}


class Book:
    def __init__(self) -> None:
        self.held: dict[str, datetime] = {}

    def limited_until(self, model: str) -> datetime | None:
        return self.held.get(model)

    def hold(self, model: str, until: datetime, reason: str) -> None:
        self.held[model] = until


def test_the_chain_puts_another_company_next() -> None:
    assert extract.chain(FABLE, ARGON) == (FABLE, ARGON, LUNA)
    assert extract.chain(ARGON, ARGON) == (ARGON, LUNA)
    assert extract.chain("openai/gpt-6-astra", FABLE) == ("openai/gpt-6-astra", FABLE, LUNA)
    # Sonnet and Fable are both Anthropic: a shared limit, so Argon comes next.
    assert extract.chain("claude-sonnet-5-5", FABLE) == ("claude-sonnet-5-5", ARGON, LUNA)
    assert extract.chain(LUNA, ARGON) == (LUNA, ARGON)


def test_a_limit_is_not_retried_and_the_switch_is_kept() -> None:
    routes: list[str] = []

    def caller(model: str, system: str, prompt: str, keys: Any) -> tuple[dict, dict]:
        routes.append(model)
        if model == FABLE:
            raise AttemptFailed("OpenRouter HTTP 429: limit", limited=True)
        return _card(), OK

    result = extract_card("demo", "demo", LINES, KEYS, call=caller, model=FABLE, default=ARGON)
    assert routes == [FABLE, ARGON]
    assert result.usage["models"] == [ARGON]
    assert result.usage["requested_model"] == FABLE
    assert result.usage["switches"] == [{"part": 1, "from": FABLE, "to": ARGON, "reason": "limit"}]


def test_a_passing_failure_keeps_the_old_ladder() -> None:
    routes: list[str] = []

    def caller(model: str, system: str, prompt: str, keys: Any) -> tuple[dict, dict]:
        routes.append(model)
        if model == FABLE:
            raise AttemptFailed("OpenRouter HTTP 503")
        return _card(), OK

    result = extract_card("demo", "demo", LINES, KEYS, call=caller, model=FABLE, default=ARGON)
    assert routes == [FABLE, FABLE, LUNA]
    assert result.usage["switches"][0]["reason"] == "failed"


def test_a_limit_end_is_remembered_and_the_model_skipped_until_then() -> None:
    book = Book()
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    routes: list[str] = []

    def caller(model: str, system: str, prompt: str, keys: Any) -> tuple[dict, dict]:
        routes.append(model)
        if model == FABLE:
            raise AttemptFailed("limit", limited=True, retry_at=until)
        return _card(), OK

    extract_card("d", "d", LINES, KEYS, call=caller, model=FABLE, default=ARGON, limits=book)
    assert book.held == {FABLE: until}
    routes.clear()
    second = extract_card(
        "d", "d", LINES, KEYS, call=caller, model=FABLE, default=ARGON, limits=book
    )
    assert routes == [ARGON]  # Fable is not called again before its limit ends
    assert second.attempts[0]["skipped"] is True and second.attempts[0]["model"] == FABLE
    # Once the end has passed, Fable is asked again.
    book.held[FABLE] = datetime.now(timezone.utc) - timedelta(seconds=1)
    routes.clear()
    extract_card("d", "d", LINES, KEYS, call=caller, model=FABLE, default=ARGON, limits=book)
    assert routes[0] == FABLE


def test_a_limit_on_every_model_fails_with_the_attempts() -> None:
    def caller(model: str, system: str, prompt: str, keys: Any) -> tuple[dict, dict]:
        raise AttemptFailed("limit", limited=True)

    with pytest.raises(extract.ExtractionError) as caught:
        extract_card("d", "d", LINES, KEYS, call=caller, model=FABLE, default=ARGON)
    assert [a["model"] for a in caught.value.attempts] == [FABLE, ARGON, LUNA]


def _response(status: int, body: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def test_limit_answers_are_told_from_other_errors() -> None:
    limited = http_failure("OpenRouter", _response(429, {}, {"retry-after": "120"}))
    assert limited.limited and limited.retry_at is not None
    assert timedelta(seconds=100) < limited.retry_at - datetime.now(timezone.utc)
    assert http_failure("OpenRouter", _response(402, {})).limited
    raw = {"error": {"message": "Provider returned error",
                     "metadata": {"raw": "Rate limit exceeded for this model"}}}  # fmt: skip
    assert http_failure("OpenRouter", _response(400, raw)).limited
    length = {"error": {"message": "This model's maximum context length limit is 200000"}}
    assert not http_failure("OpenRouter", _response(400, length)).limited
    assert not http_failure("Claude", _response(503, {})).limited
    # A reset too far away is not trusted.
    far = str(int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000))
    held = http_failure("OpenRouter", _response(429, {}, {"x-ratelimit-reset": far}))
    assert held.limited and held.retry_at is None


def test_an_error_inside_a_200_answer_is_a_limit_when_it_says_so() -> None:
    body = {"error": {"code": 429, "message": "Rate limited upstream"}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(AttemptFailed) as caught:
        extract.call_openrouter("s", "p", KEYS, transport, model=FABLE)
    assert caught.value.limited and "limit" in caught.value.reason
    assert json.dumps(body["error"]["message"]) not in caught.value.reason


def test_the_store_keeps_a_limit_end(tmp_path: Path) -> None:
    store = CardStore(
        tmp_path / "cards.sqlite3", lambda: datetime(2026, 11, 15, tzinfo=timezone.utc)
    )
    assert store.limited_until(FABLE) is None
    until = datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)
    store.hold(FABLE, until, "OpenRouter HTTP 429: limit")
    reopened = CardStore(tmp_path / "cards.sqlite3", datetime.now)
    assert reopened.limited_until(FABLE) == until
    assert reopened.limits() == {FABLE: until.isoformat()}
