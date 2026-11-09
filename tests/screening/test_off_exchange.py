"""Off-exchange price levels (ADR 0052 §5) against trades shaped like Alpaca's
`GET /v2/stocks/{symbol}/trades` (field names from the docs, codes from the live
meta endpoints read 2026-11-09). No test reaches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest

from packages.contracts.validator import validate_document as validate_contract
from packages.contracts.validator import validation_errors
from services.market_data.alpaca import AlpacaConfig, AlpacaMarketDataProvider
from services.screening.off_exchange import (
    LABEL,
    OffExchangeLimits,
    bin_width,
    finra_context,
    off_exchange_check,
)

SECRET = "test-secret-not-real"
CUTOFF = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)  # 10:00 ET Friday
NOW = CUTOFF + timedelta(minutes=30)


def trade(t: str, p: str, s: int, x: str = "D", c: tuple[str, ...] = ("@",), **extra: Any) -> str:
    fields = {"t": t, "x": x, "s": s, "c": list(c), "i": 1, "z": extra.pop("z", "C"), **extra}
    body = json.dumps(fields)[:-1]
    # Prices stay bare JSON numbers, as Alpaca sends them.
    return f'{body},"p":{p}}}'


def page(*trades: str, token: str | None = None) -> httpx.Response:
    next_token = "null" if token is None else f'"{token}"'
    text = f'{{"symbol":"STAK","trades":[{",".join(trades)}],"next_page_token":{next_token}}}'
    return httpx.Response(200, text=text)


@dataclass
class Tape:
    """Pages by session start date (`start` parameter's date), then by page token."""

    pages: dict[str, list[httpx.Response]]
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        day = request.url.params["start"][:10]
        queue = self.pages.get(day)
        if not queue:
            return page()
        return queue.pop(0)


def adapter(tape: Tape) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        AlpacaConfig(key_id="test-key-id", secret_key=SECRET),
        transport=httpx.MockTransport(tape),
        now=lambda: NOW,
    )


def stored(outcome: dict[str, Any], cutoff: datetime = CUTOFF) -> dict[str, Any]:
    """The `screening_check` document the API stores around an outcome (ADR 0052 S1)."""
    return {
        "kind": "screening_check",
        "schema_version": "0.1",
        "check_id": 1,
        "check": "off_exchange",
        "session_date": "2026-11-06",
        "symbol": "STAK",
        "instrument_id": "us:stak:xnas:2026",
        "cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
        "created_at_utc": "2026-11-06T15:30:00Z",
        "after_the_fact": False,
        **outcome,
    }


def validate_document(outcome: dict[str, Any]) -> None:
    validate_contract(stored(outcome))


def run(tape: Tape, **changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "symbol": "STAK",
        "cutoff": CUTOFF,
        "now": NOW,
        "publication_delay_minutes": 15,
        "limits": OffExchangeLimits(sessions=2),
    }
    values.update(changes)
    return off_exchange_check(adapter(tape).trade_pages, **values)


def friday_and_thursday() -> Tape:
    return Tape(
        {
            "2026-11-06": [
                page(
                    trade("2026-11-06T13:00:00.1Z", "2.0068", 500),
                    trade("2026-11-06T13:00:01Z", "2.0012", 300, x="Q"),
                    # Removed by a later cancel: counts nowhere.
                    trade("2026-11-06T13:00:02Z", "2.0050", 9_000, u="canceled"),
                    token="next",
                ),
                page(
                    # Derivatively priced: real volume, but not a market price.
                    trade("2026-11-06T14:00:00Z", "1.50", 1_000, c=("@", "4")),
                    # Reported out of sequence: the price still counts.
                    trade("2026-11-06T14:30:00Z", "1.9050", 200, c=("@", "Z")),
                    trade("2026-11-06T14:59:59.123456789Z", "2.0101", 100, x="K"),
                ),
            ],
            "2026-11-05": [
                page(
                    trade("2026-11-05T15:00:00Z", "1.9020", 400),
                    trade("2026-11-05T15:00:01Z", "1.9020", 100, c=("@", "I")),
                    trade("2026-11-05T15:30:00Z", "1.95", 50),
                    trade("2026-11-05T20:00:00Z", "1.97", 1_000, x="Q", c=("@", "M")),
                    # "B" is an average price on tape A/B only.
                    trade("2026-11-05T19:00:00Z", "1.80", 70, c=("B",), z="A"),
                    trade("2026-11-05T19:00:01Z", "1.81", 30, c=("@", "B")),
                )
            ],
        }
    )


def test_off_exchange_levels_from_trf_reported_trades() -> None:
    tape = friday_and_thursday()
    document = run(tape)

    validate_document(document)
    assert document["status"] == "ok"
    assert document["result"]["label"] == LABEL == "장외 체결(TRF 보고)"
    assert "다크풀" not in document["result"]["label"]
    assert document["result"]["data_until_utc"] == "2026-11-06T15:00:00Z"
    friday, thursday = document["result"]["sessions"]
    assert friday["session_date"] == "2026-11-06" and friday["partial"] is True
    assert friday["range_utc"] == ["2026-11-06T09:00:00Z", "2026-11-06T15:00:00Z"]
    assert friday["pages"] == 2
    assert friday["total_volume"] == "2100"
    assert friday["off_exchange_volume"] == "1700"
    assert friday["off_market_price_volume"] == "1000"
    assert friday["late_report_volume"] == "200"
    assert friday["removed_trades"] == 1
    assert thursday["partial"] is False
    assert thursday["total_volume"] == "650"  # the official close print is not a trade
    assert thursday["off_exchange_volume"] == "650"
    assert thursday["off_market_price_volume"] == "70"
    # Reference: last market-price trade (any venue) before the cutoff, exact decimals.
    assert document["result"]["reference_price"] == "2.0101"
    assert document["result"]["reference_time_utc"] == "2026-11-06T14:59:59.123456789Z"
    assert document["result"]["bin_width"] == "0.01"
    assert document["result"]["priced_volume"] == "1280"
    assert {b["price_low"]: b["volume"] for b in document["result"]["histogram"]} == {
        "1.81": "30",
        "1.9": "700",
        "1.95": "50",
        "2": "500",
    }
    assert document["result"]["levels"] == [
        {
            "price_low": "1.9",
            "price_high": "1.91",
            "volume": "700",
            "share_pct": "54.69",
            "side": "below",
        },
        {
            "price_low": "2",
            "price_high": "2.01",
            "volume": "500",
            "share_pct": "39.06",
            "side": "below",
        },
        {
            "price_low": "1.95",
            "price_high": "1.96",
            "volume": "50",
            "share_pct": "3.91",
            "side": "below",
        },
        {
            "price_low": "1.81",
            "price_high": "1.82",
            "volume": "30",
            "share_pct": "2.34",
            "side": "below",
        },
    ]
    assert document["result"]["missing"] == ["finra_weekly_ats"]
    assert any("증권사 내부 체결" in text for text in document["result"]["limitations"])
    assert any("방향" in text for text in document["result"]["limitations"])
    assert any("보고 시각" in text for text in document["result"]["limitations"])
    assert document["sources"]["trades"]["exchange_code"] == "D"
    assert document["warnings"][: len(document["result"]["limitations"])] == list(
        document["result"]["limitations"]
    )

    first = tape.requests[0]
    assert first.url.path == "/v2/stocks/STAK/trades"
    assert first.url.params["feed"] == "sip"
    assert first.url.params["start"] == "2026-11-06T09:00:00Z"
    assert first.url.params["end"] == "2026-11-06T15:00:00Z"
    assert tape.requests[1].url.params["page_token"] == "next"
    assert tape.requests[2].url.params["start"] == "2026-11-05T09:00:00Z"
    assert tape.requests[2].url.params["end"] == "2026-11-06T01:00:00Z"
    assert SECRET not in json.dumps(document)


def test_publication_delay_caps_the_data_at_a_live_cutoff() -> None:
    tape = Tape({})
    document = run(tape, cutoff=NOW)
    assert document["result"]["data_until_utc"] == "2026-11-06T15:15:00Z"
    assert tape.requests[0].url.params["end"] == "2026-11-06T15:15:00Z"
    assert document["status"] == "no_data"
    assert "levels" in document["result"]["missing"]
    validate_document(document)


def test_page_budget_drops_the_unfinished_session_whole() -> None:
    tape = Tape(
        {
            "2026-11-06": [page(trade("2026-11-06T13:00:00Z", "2", 100))],
            "2026-11-05": [
                page(trade("2026-11-05T13:00:00Z", "3", 100), token="a"),
                page(trade("2026-11-05T14:00:00Z", "3", 100)),
            ],
        }
    )
    document = run(tape, limits=OffExchangeLimits(sessions=2, max_pages=2))
    validate_document(document)
    assert document["status"] == "incomplete"
    assert [s["session_date"] for s in document["result"]["sessions"]] == ["2026-11-06"]
    assert "session:2026-11-05" in document["result"]["missing"]
    assert document["result"]["limits"]["pages"] == 2
    assert {b["price_low"] for b in document["result"]["histogram"]} == {"2"}
    assert any("상한" in note for note in document["result"]["notes"])


def test_time_budget_stops_between_pages() -> None:
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    tape = Tape({"2026-11-06": [page(trade("2026-11-06T13:00:00Z", "2", 100), token="a")]})
    document = run(tape, monotonic=lambda: next(clock, 100.0))
    assert document["status"] == "incomplete"
    assert document["result"]["sessions"] == []
    assert document["result"]["missing"][:2] == ["session:2026-11-06", "session:2026-11-05"]
    assert len(tape.requests) == 1


def test_provider_error_fails_without_levels() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "too many requests"})

    provider = AlpacaMarketDataProvider(
        AlpacaConfig(key_id="test-key-id", secret_key=SECRET),
        transport=httpx.MockTransport(refuse),
    )
    document = off_exchange_check(
        provider.trade_pages,
        symbol="STAK",
        cutoff=CUTOFF,
        now=NOW,
        publication_delay_minutes=15,
    )
    validate_document(document)
    assert document["status"] == "error"
    assert document["result"]["levels"] == []
    assert document["result"]["reason"].startswith("ProviderRateLimitError")


def test_small_peaks_are_not_levels() -> None:
    tape = Tape(
        {
            "2026-11-06": [
                page(
                    trade("2026-11-06T13:00:00Z", "2", 10_000),
                    trade("2026-11-06T13:00:01Z", "1.5", 1),
                )
            ]
        }
    )
    document = run(tape, limits=OffExchangeLimits(sessions=1))
    assert [level["price_low"] for level in document["result"]["levels"]] == ["2"]
    assert len(document["result"]["histogram"]) == 2


def test_wide_ranges_widen_the_bins() -> None:
    trades = [trade("2026-11-06T13:00:00Z", str(Decimal(n) / 100), 10) for n in range(100, 400)]
    trades.append(trade("2026-11-06T14:59:00Z", "3.99", 10))
    tape = Tape({"2026-11-06": [page(*trades)]})
    document = run(tape, limits=OffExchangeLimits(sessions=1, max_bins=100))
    assert document["result"]["bin_width"] == "0.04"
    assert len(document["result"]["histogram"]) <= 100
    validate_document(document)


def test_past_cutoffs_say_corrections_are_reconstructed() -> None:
    document = run(Tape({}), now=CUTOFF + timedelta(days=3))
    assert any("나중에 받아 재구성" in text for text in document["result"]["unverified"])
    live = run(Tape({}))
    assert not any("나중에 받아 재구성" in text for text in live["result"]["unverified"])


def test_cutoff_after_now_is_refused() -> None:
    with pytest.raises(ValueError):
        run(Tape({}), cutoff=NOW + timedelta(minutes=1))


@pytest.mark.parametrize(
    ("reference", "width"),
    [("342.36", "1"), ("2.0068", "0.01"), ("0.3765", "0.001"), ("50", "0.2"), ("0.01", "0.0001")],
)
def test_bin_width_is_a_1_2_5_step_near_half_a_percent(reference: str, width: str) -> None:
    assert bin_width(Decimal(reference)) == Decimal(width)


def test_finra_weekly_ats_is_context_only() -> None:
    @dataclass
    class FakeFact:
        code: str

        def document(self) -> dict[str, Any]:
            return {"code": self.code, "value": "1", "unit": "count", "text": "t"}

    @dataclass
    class FakeEvidence:
        cutoff_utc: str = "2026-11-06T15:00:00Z"
        facts: list[FakeFact] = field(
            default_factory=lambda: [FakeFact("ats_shares"), FakeFact("ats_trades")]
        )
        missing: list[str] = field(default_factory=lambda: ["ats_share_of_total_volume_pct"])
        uncertainty: list[str] = field(default_factory=lambda: ["delayed weekly volume"])

    context = finra_context(FakeEvidence())
    assert [fact["code"] for fact in context["facts"]] == ["ats_shares"]
    document = run(friday_and_thursday(), finra=context)
    validate_document(document)
    assert document["result"]["context"]["finra_weekly_ats"] == context
    assert "finra_weekly_ats" not in document["result"]["missing"]
    # The levels come from trades alone; FINRA weekly data never changes them.
    assert document["result"]["levels"] == run(friday_and_thursday())["result"]["levels"]


def test_contract_refuses_a_dark_pool_label() -> None:
    document = run(friday_and_thursday())
    assert not validation_errors(stored(document))
    renamed = {**document, "result": {**document["result"], "label": "다크풀"}}
    assert validation_errors(stored(renamed))
    assert validation_errors(stored({**document, "status": "bullish"}))
    # The result shape is only required of off-exchange checks.
    assert not validation_errors({**stored(renamed), "check": "news"})
