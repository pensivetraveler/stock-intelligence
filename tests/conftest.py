from __future__ import annotations

import json
from collections.abc import Callable, Collection
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

import pytest

from services.market_data.calendar import UsEquityCalendar
from services.market_data.fake import DEFAULT_FIXTURE_PATH

SESSION_OPEN = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
SyntheticDocument = Callable[..., dict[str, Any]]


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def synthetic_document() -> SyntheticDocument:
    """Fake-provider document with consecutive valid 1m bars from the 2026-10-09 open."""
    calendar = UsEquityCalendar("XNAS")

    def build(
        minutes: int = 390,
        *,
        start: datetime = SESSION_OPEN,
        skip: Collection[int] = (),
        provisional: Collection[int] = (),
        revision: str | None = None,
    ) -> dict[str, Any]:
        document = cast(dict[str, Any], json.loads(DEFAULT_FIXTURE_PATH.read_text("utf-8")))
        template = document["bars"][0]
        bars = []
        for offset in range(minutes):
            if offset in skip:
                continue
            interval_start = start + timedelta(minutes=offset)
            interval_end = interval_start + timedelta(minutes=1)
            price = Decimal("100") + Decimal(offset) / 100
            bar = deepcopy(template)
            bar.update(
                interval_start_utc=_utc_text(interval_start),
                interval_end_utc=_utc_text(interval_end),
                open=str(price),
                high=str(price + Decimal("0.5")),
                low=str(price - Decimal("0.5")),
                close=str(price + Decimal("0.1")),
                volume=str(100 + offset),
                session=calendar.classify_interval(interval_start, interval_end),
                available_at_utc=_utc_text(interval_end + timedelta(seconds=2)),
            )
            if offset in provisional:
                bar.update(is_final=False, quality="provisional")
            if revision is not None:
                bar["dataset_revision"] = revision
            bars.append(bar)
        document["bars"] = bars
        return document

    return build
